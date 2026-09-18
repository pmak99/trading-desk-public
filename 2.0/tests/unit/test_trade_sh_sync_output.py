"""`./trade.sh sync` prints what decides the run, and keeps the rest in a file.

A focused sync logs ~800 lines. The unattended /maintenance run on 2026-09-15 got
that through a tool whose output was cut before the SYNC SUMMARY: it reported one
date conflict of four, called exit 1 harmless, and ran the sync a second time to
look again. The wrapper now writes the full output to
`$TRADING_LOG_DIR/sync-calendar-<ET timestamp>.log` and prints every warning and
error line, the summary, and the file's path. These tests run the wrapper's own
function in bash with `python` stubbed, under trade.sh's `set -euo pipefail`.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TRADE_SH = ROOT / "2.0" / "trade.sh"

PREFIX = "2026-09-15 09:03:47 - [-] - __main__ - "
FINISHED = "\n".join([
    PREFIX + "INFO - Syncing tickers",
    PREFIX + "INFO - CHANGE DETECTED: AMZN",
    "2026-09-15 09:04:38 - [-] - src.application.services.earnings_date_validator - "
    "WARNING - FDX: Date conflict detected! Dates differ by 42 days",
    PREFIX + "WARNING -   ⏭️  NEEDS REVIEW — FDX: sources disagree (...); stored 2026-09-16.",
    PREFIX + "ERROR -   ✗ Failed to update: DBERROR boom",
    PREFIX + "INFO - ",
    PREFIX + "INFO - SYNC SUMMARY",
    PREFIX + "INFO -   ✗ Errors: 2 (1 need review, 1 failed)",
    PREFIX + "INFO - ",
    "NEEDS REVIEW (1) — not written:",
    PREFIX + "INFO -   FDX: stored 2026-09-16 — sources disagree: Yahoo Finance 2026-10-28 (AMC)",
])
CRASHED = "\n".join([PREFIX + f"INFO - line {i}" for i in range(100)] + ["Traceback (most recent call last):"])


def _function() -> str:
    text = TRADE_SH.read_text()
    match = re.search(r"^sync_earnings_calendar\(\) \{\n.*?^\}\n", text, re.S | re.M)
    assert match, "sync_earnings_calendar() not found in trade.sh"
    return match.group(0)


def _run(tmp_path, output: str, rc: int, *args: str):
    fake = tmp_path / "fake_output.txt"
    fake.write_text(output + "\n")
    logs = tmp_path / "logs"
    script = "\n".join([
        "set -euo pipefail",
        "BLUE= BOLD= NC= YELLOW= GREEN= RED=",
        f'python() {{ printf "%s\\n" "$*" > "{tmp_path}/argv"; cat "{fake}"; return {rc}; }}',
        _function(),
        # Called bare, as trade.sh's case branch calls it: set -e applies inside, so a
        # python exit 1 that aborted the function would skip the summary and fail here.
        f'sync_earnings_calendar {" ".join(args)}',
    ])
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                               "TRADING_LOG_DIR": str(logs)})
    return proc, sorted(logs.glob("sync-calendar-*.log"))


def test_the_full_output_goes_to_a_file_and_the_terminal_gets_the_decision(tmp_path):
    proc, logs = _run(tmp_path, FINISHED, 1)

    assert len(logs) == 1 and logs[0].read_text() == FINISHED + "\n"
    out = proc.stdout
    assert "CHANGE DETECTED: AMZN" not in out                     # progress stays in the file
    assert "WARNING - FDX: Date conflict detected!" in out         # every warning line
    assert "ERROR -   ✗ Failed to update: DBERROR boom" in out     # every error line
    summary = out[out.index("SYNC SUMMARY"):]
    assert "✗ Errors: 2 (1 need review, 1 failed)" in summary
    assert "FDX: stored 2026-09-16 — sources disagree" in summary  # the review list survives
    assert f"Full output (11 lines): {logs[0]}" in out
    assert proc.returncode == 1                                    # exit code still reaches the caller


def test_it_passes_execute_unless_told_dry_run(tmp_path):
    _run(tmp_path, FINISHED, 0)
    assert (tmp_path / "argv").read_text().split() == ["scripts/sync_earnings_calendar.py", "--execute"]
    _run(tmp_path, FINISHED, 0, "--dry-run")
    assert (tmp_path / "argv").read_text().split() == ["scripts/sync_earnings_calendar.py"]


def test_a_run_with_no_summary_says_it_did_not_finish(tmp_path):
    proc, logs = _run(tmp_path, CRASHED, 1)

    assert "No SYNC SUMMARY: the sync did not finish" in proc.stdout
    assert "Traceback (most recent call last):" in proc.stdout
    assert "line 30" not in proc.stdout and "line 99" in proc.stdout   # the last 60 lines only
    assert len(logs) == 1 and proc.returncode == 1


@pytest.mark.parametrize("warnings", [False, True])
def test_a_clean_run_exits_zero_and_says_so(tmp_path, warnings):
    output = FINISHED if warnings else "\n".join(
        line for line in FINISHED.splitlines()
        if " - WARNING - " not in line and " - ERROR - " not in line)
    proc, _ = _run(tmp_path, output, 0)
    assert proc.returncode == 0 and "✓ Earnings calendar synced" in proc.stdout
    assert ("(none)" in proc.stdout) is (not warnings)


def test_the_log_it_writes_is_named_for_the_eastern_date():
    # The host-side maintenance job prunes these by name; that job is operator
    # infrastructure and is not part of this repository, so only the name is
    # checked here.
    assert "sync-calendar-$(TZ=America/New_York date" in _function()
