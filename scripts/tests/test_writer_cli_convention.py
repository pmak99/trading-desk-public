"""Every script that writes to the database is report-only unless told --execute.

Until 2026-09-13 only two scripts worked that way. Five made a dry run opt-in
(`--dry-run`), so forgetting the flag wrote -- regroup_strategies issues
DELETEs -- and two had no dry run at all. A misremembered flag should cost a
re-run, not a table. `--dry-run` is gone rather than kept as an alias: a stale
invocation now fails at argument parsing and writes nothing.

Scheduled and automatic callers pass --execute explicitly; see
test_callers_pass_execute.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

WRITERS = [
    "scripts/regroup_strategies.py",
    "scripts/import_journal_to_db.py",
    "scripts/reenrich_journal_earnings.py",
    "scripts/reconcile_journal.py",
    "2.0/scripts/sync_earnings_calendar.py",
    "2.0/scripts/track_iv_weekly.py",
    "2.0/scripts/validate_bias_predictions.py",
    "2.0/scripts/backfill_historical.py",
    "2.0/scripts/cleanup_duplicate_moves.py",
    "2.0/scripts/resolve_ambiguous_moves.py",
    "2.0/scripts/backfill_trr_at_entry.py",
    "2.0/scripts/jobs/record_moves.py",
]


def _help(script: str) -> str:
    cwd = REPO / "2.0" if script.startswith("2.0/") else REPO
    path = REPO / script
    proc = subprocess.run([sys.executable, str(path), "--help"], cwd=cwd,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.mark.parametrize("script", WRITERS)
def test_writes_need_execute(script):
    out = _help(script)
    assert "--execute" in out
    assert "--dry-run" not in out


def test_regroup_has_no_no_op_flags():
    # --fix-mistagged was `if args.fix_mistagged or True`, and --split-mixed was
    # documented as "always runs": both checks run unconditionally.
    out = _help("scripts/regroup_strategies.py")
    assert "--fix-mistagged" not in out and "--split-mixed" not in out


# Every automatic or documented invocation that is meant to write. A caller
# left behind would silently turn a scheduled write into a report.
CALLERS = {
    "2.0/trade.sh": [
        r"scripts/backfill_historical\.py[^\n]*\\\n[^\n]*\\\n[^\n]*--execute",
        r"scripts/validate_bias_predictions\.py --execute",
        r"scripts/sync_earnings_calendar\.py \$mode_flag",
    ],
    "2.0/scripts/scan/workflows/ticker_analysis.py": [
        r'"scripts/backfill_historical\.py",\s*ticker,\s*"--execute"',
    ],
    ".claude/commands/journal.md": [
        r'"scripts/import_journal_to_db\.py" \\\n\s*--execute',
        r'"scripts/regroup_strategies\.py" \\\n\s*--execute',
        r'"2\.0/scripts/backfill_trr_at_entry\.py" --execute',
    ],
    ".claude/commands/maintenance.md": [
        r"scripts/track_iv_weekly\.py --execute",
        # Step 8 runs record_moves report-only (no caller here).
        r"scripts/backfill_new_ticker_events\.sh /tmp/maintenance_step7_events\.txt",
    ],
    # maintenance Step 7b's loop: one event per run, and only after a report-only
    # pass printed no "Would add" line.
    "scripts/backfill_new_ticker_events.sh": [
        r'BACKFILL="\$ROOT/2\.0/scripts/backfill_historical\.py"',
        r'"\$BACKFILL" --execute "\$TICKER" --start-date "\$DAY" --end-date "\$DAY"',
    ],
}


@pytest.mark.parametrize("path,pattern",
                         [(p, pat) for p, pats in CALLERS.items() for pat in pats])
def test_callers_pass_execute(path, pattern):
    assert re.search(pattern, (REPO / path).read_text()), f"{path}: {pattern}"


def test_trade_sh_sync_writes_unless_asked_not_to():
    text = (REPO / "2.0" / "trade.sh").read_text()
    body = text[text.index("sync_earnings_calendar() {"):]
    body = body[:body.index("\n}\n")]
    assert 'local mode_flag="--execute"' in body
    assert 'mode_flag=""' in body          # --dry-run clears it
