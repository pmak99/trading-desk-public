"""scripts/with-env, scripts/db, scripts/backfill_new_ticker_events.sh and
2.0/trade.sh, run as bash.

with-env holds the rule scripts/db was written for -- a caller's PG* beats
2.0/.env, or `PGDATABASE=ivcrush_scratch` silently becomes production -- and
since 2026-09-14 /maintenance runs its Python steps through it too, so the rule
now guards more than psql. The backfill script is Step 7b's loop, moved out of
maintenance.md so the unattended run can allow it with one rule; these tests pin
that the move kept its logic. `trade.sh list` hands the scan the earnings date it
was given, passes an offset only when one was given, and prints the expiration the
scan will use: the weekday rule, or an offset the minimum DTE can raise.

Each test copies the scripts into a temporary tree with a fake 2.0/.env and
stub executables, so nothing reaches a database or an API.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "2.0").mkdir()
    (tmp_path / "bin").mkdir()
    for name in ("with-env", "db", "backfill_new_ticker_events.sh"):
        shutil.copy2(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    (tmp_path / "2.0" / ".env").write_text(
        "PGDATABASE=ivcrush\nPGUSER=trading\nTWELVE_DATA_KEY=from-dotenv  # inline comment\n")
    return tmp_path


def _stub(tree, name, body):
    path = tree / "bin" / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def _run(tree, *args, env=None):
    base = {"PATH": f"{tree / 'bin'}:/usr/bin:/bin"}
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          env={**base, **(env or {})}, cwd=tree)


# --- with-env ------------------------------------------------------------------

def test_with_env_exports_dotenv_to_the_command(tree):
    _stub(tree, "show", 'echo "$PGDATABASE $PGUSER $TWELVE_DATA_KEY"\n')
    r = _run(tree, tree / "scripts" / "with-env", "show")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["ivcrush", "trading", "from-dotenv"]


def test_a_callers_pgdatabase_is_not_turned_into_production(tree):
    _stub(tree, "show", 'echo "$PGDATABASE $PGUSER"\n')
    r = _run(tree, tree / "scripts" / "with-env", "show", env={"PGDATABASE": "ivcrush_scratch"})
    assert r.stdout.split() == ["ivcrush_scratch", "trading"]


def test_with_env_passes_arguments_and_exit_status_through(tree):
    _stub(tree, "show", 'printf "<%s>" "$@"; exit 7\n')
    r = _run(tree, tree / "scripts" / "with-env", "show", "a b", "--flag")
    assert r.stdout == "<a b><--flag>"
    assert r.returncode == 7


def test_with_env_without_a_command_is_a_usage_error(tree):
    r = _run(tree, tree / "scripts" / "with-env")
    assert r.returncode == 2 and "usage" in r.stderr


def test_scripts_db_runs_psql_through_with_env(tree):
    _stub(tree, "psql", 'echo "psql $PGDATABASE $*"\n')
    r = _run(tree, tree / "scripts" / "db", "-tAc", "SELECT 1",
             env={"PGDATABASE": "ivcrush_scratch"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "psql ivcrush_scratch -tAc SELECT 1"


# --- backfill_new_ticker_events.sh (maintenance Step 7b) -----------------------

@pytest.fixture
def backfill(tree):
    """A stub backfill_historical.py: the ticker decides what report-only prints.
    ADD -> `Would add` (discovery); NONE -> nothing computable; else `Would save`.
    Every call is appended to calls.log."""
    calls = tree / "calls.log"
    python = _stub(tree, "python", (
        f'echo "$*" >> "{calls}"\n'
        'case "$*" in\n'
        '  *--execute*) echo "Saved 1 moves" ;;\n'
        '  *ADD*) echo "Would add 2 earnings dates" ;;\n'
        '  *NONE*) echo "no bars" ;;\n'
        '  *) echo "[DRY RUN] Would save: 1.0% gap" ;;\n'
        'esac\n'))
    _stub(tree, "sleep", f'echo "sleep $*" >> "{calls}"\n')

    def run(lines, *extra):
        events = tree / "events.txt"
        events.write_text("".join(f"{ln}\n" for ln in lines))
        r = _run(tree, tree / "scripts" / "backfill_new_ticker_events.sh", events, *extra,
                 env={"PYTHON": str(python)})
        log = calls.read_text().splitlines() if calls.exists() else []
        calls.unlink(missing_ok=True)
        return r, log
    return run


def test_an_event_that_would_save_is_executed_after_its_report_only_pass(backfill):
    r, calls = backfill(["AAA 2026-04-02"])
    assert r.returncode == 0, r.stderr
    backfills = [c for c in calls if not c.startswith("sleep")]
    assert backfills[0].endswith("backfill_historical.py AAA --start-date 2026-04-02 --end-date 2026-04-02")
    assert backfills[1].endswith(
        "backfill_historical.py --execute AAA --start-date 2026-04-02 --end-date 2026-04-02")
    assert "Saved 1 moves" in r.stdout
    assert calls.count("sleep 8") == 2        # before --execute, and after the event


def test_discovery_is_reported_and_never_executed(backfill):
    r, calls = backfill(["ADD 2026-04-02"])
    assert "REVIEW ADD 2026-04-02: discovery would insert calendar rows" in r.stdout
    assert "Would add 2 earnings dates" in r.stdout
    assert not [c for c in calls if "--execute" in c]


def test_nothing_to_record_is_reported_and_never_executed(backfill):
    r, calls = backfill(["NONE 2026-04-02"])
    assert "REVIEW NONE 2026-04-02: no move computed" in r.stdout
    assert not [c for c in calls if "--execute" in c]


def test_the_event_cap_holds_and_the_remainder_is_counted(backfill):
    r, calls = backfill(["AAA 2026-01-02", "BBB 2026-01-03", "", "CCC 2026-01-04"], "2")
    tickers = {c.split()[-5] for c in calls if "--execute" in c}
    assert tickers == {"AAA", "BBB"}
    assert r.stdout.strip().endswith("processed 2 of 3 events; 1 remain")


def test_the_default_cap_is_the_one_maintenance_md_passes(tree):
    import re
    md = (ROOT / ".claude" / "commands" / "maintenance.md").read_text()
    (cap,) = re.findall(r"scripts/backfill_new_ticker_events\.sh \S+ (\d+)", md)
    script = (tree / "scripts" / "backfill_new_ticker_events.sh").read_text()
    assert f"MAX=${{2:-{cap}}}" in script


def test_an_unreadable_events_file_is_an_error(tree):
    r = _run(tree, tree / "scripts" / "backfill_new_ticker_events.sh", tree / "missing.txt")
    assert r.returncode == 2 and "cannot read" in r.stderr


# --- 2.0/trade.sh ----------------------------------------------------------------

# What the stub answers when a test does not say: enough for each wrapper to go on.
TRADE_SH_ANSWERS = {
    "health_check.py": "System Health: UP\n",
    "scan.py": "TICKER MODE\n",
    "analyze.py": "ANALYSIS RESULTS\nVRP Ratio: 2.26x\n",
}


@pytest.fixture
def trade(tmp_path):
    """trade.sh in a temporary 2.0/ whose venv `python` is a stub. Every script it
    runs is logged to calls.log and answered from `answers` (script name -> its
    stdout, a (stdout, exit status) pair, or a list of either answered in call
    order), while `python -c` runs the real interpreter on this checkout, so the
    expiration and offset the wrapper computes come from the engine's own rules.

    `run(*args)` returns the finished process, with colour codes stripped from
    stdout, and the logged calls. `run.engine` is the temporary 2.0/.
    """
    engine = tmp_path / "2.0"
    (engine / "venv" / "bin").mkdir(parents=True)
    shutil.copy2(ROOT / "2.0" / "trade.sh", engine / "trade.sh")
    calls = tmp_path / "calls.log"
    answers = tmp_path / "answers"
    stub = engine / "venv" / "bin" / "python"
    stub.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "-c" ]; then\n'
        f'  PYTHONPATH="{ROOT}:{ROOT / "2.0"}" exec "{sys.executable}" "$@"\n'
        "fi\n"
        f'echo "$*" >> "{calls}"\n'
        'script=$1; [ "$script" = "-u" ] && script=$2\n'
        'name=$(basename "$script")\n'
        f'n=$(grep -c "$name" "{calls}")\n'
        f'answer="{answers}/$name.$n"; [ -f "$answer" ] || answer="{answers}/$name"\n'
        '[ -f "$answer" ] || exit 0\n'
        'cat "$answer"\n'
        'exit "$(cat "$answer.rc" 2>/dev/null || echo 0)"\n')
    stub.chmod(0o755)
    (engine / "venv" / "bin" / "activate").write_text(f'PATH="{stub.parent}:$PATH"\n')

    def _write(path, answer):
        out, rc = answer if isinstance(answer, tuple) else (answer, 0)
        path.write_text(out)
        (path.parent / f"{path.name}.rc").write_text(str(rc))

    def run(*args, dotenv="", **answer_for):
        shutil.rmtree(answers, ignore_errors=True)
        answers.mkdir()
        for name, answer in {**TRADE_SH_ANSWERS, **answer_for}.items():
            for i, one in enumerate(answer if isinstance(answer, list) else [answer], 1):
                _write(answers / (f"{name}.{i}" if isinstance(answer, list) else name), one)
        (engine / ".env").write_text(dotenv)
        r = subprocess.run(["bash", str(engine / "trade.sh"), *args],
                           capture_output=True, text=True, cwd=tmp_path,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
        r.stdout = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout)
        log = calls.read_text().splitlines() if calls.exists() else []
        calls.unlink(missing_ok=True)
        return r, log
    run.engine = engine
    return run


def _ran(calls, script):
    return [c for c in calls if f"scripts/{script}" in c]


# --- trade.sh list ---------------------------------------------------------------

@pytest.fixture
def trade_list(trade):
    def run(*args, dotenv=""):
        return trade("list", *args, dotenv=dotenv)
    return run


def test_ticker_mode_stores_the_bias_prediction_for_the_date_it_analyses(trade):
    """It passed the ticker alone, and store_bias_prediction.py predicted the ticker's
    next calendar date, whatever date analyze.py was then run for (until 2026-09-14)."""
    _, calls = trade("AAPL", "2026-09-15")
    assert "scripts/store_bias_prediction.py AAPL --earnings-date 2026-09-15" in calls
    assert any(c.startswith("scripts/analyze.py AAPL --earnings-date 2026-09-15 ") for c in calls)


@pytest.mark.parametrize("min_dte, expiration", [("3", "2026-10-16"), ("5", "2026-10-23")])
def test_ticker_mode_floors_the_expiration_at_min_dte(trade, min_dte, expiration):
    """A Monday report's same-week Friday is 4 DTE. `trade.sh list` read MIN_DTE, but
    ticker mode's calculate_expiration() did not, so MIN_DTE=5 still analysed the
    4-DTE chain (until 2026-09-14)."""
    _, calls = trade("AAPL", "2026-10-12", dotenv=f"MIN_DTE={min_dte}\n")
    (analyze,) = _ran(calls, "analyze.py")
    assert f"--expiration {expiration} " in analyze + " "


def _offset_lines(stdout):
    return [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in stdout.splitlines()
            if "Expiration Offset" in ln]


def _expiration_lines(stdout):
    return [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in stdout.splitlines()
            if re.sub(r"\x1b\[[0-9;]*m", "", ln).startswith("Expiration")]


def _scan_calls(calls):
    return [c for c in calls if "scan.py" in c]


def test_list_scans_the_date_it_was_given(trade_list):
    """list printed `Earnings Date: <date>` and then ran scan.py --tickers without it,
    so each ticker was analysed for its next calendar date (until 2026-09-14)."""
    r, calls = trade_list("AAPL,MSFT", "2026-09-15", dotenv="MIN_DTE=3\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _scan_calls(calls) == ["-u scripts/scan.py --tickers AAPL,MSFT --earnings-date 2026-09-15"]


def test_list_without_an_offset_takes_the_weekday_rule(trade_list):
    """Its default offset of 1, raised to the 3-day floor, replaced the weekday rule
    on every run: a Wednesday report expired the next Monday, not the next Friday."""
    r, calls = trade_list("AAPL", "2026-09-16", dotenv="MIN_DTE=3\n")    # a Wednesday
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--expiration-offset" not in _scan_calls(calls)[0]
    assert _expiration_lines(r.stdout) == ["Expiration: 2026-09-25 (weekday rule, at least 3 DTE)"]


def test_list_prints_the_offset_the_floor_raises_it_to(trade_list):
    """calculate_expiration_date raises an offset of 1 to the 3-day minimum; until
    2026-09-14 the header said "+1 days"."""
    # MIN_DTE spelled out: Config.from_env() also loads the checkout's own 2.0/.env.
    r, calls = trade_list("AAPL,MSFT", "2026-09-15", "1", dotenv="MIN_DTE=3\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _expiration_lines(r.stdout) == ["Expiration Offset: +3 days (+1 raised to the 3-day minimum)"]
    assert _scan_calls(calls) == [
        "-u scripts/scan.py --tickers AAPL,MSFT --earnings-date 2026-09-15 --expiration-offset 1"]


def test_list_prints_an_offset_at_or_above_the_floor_as_given(trade_list):
    r, _ = trade_list("AAPL", "2026-09-15", "7")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _offset_lines(r.stdout) == ["Expiration Offset: +7 days"]


def test_list_reads_the_floor_from_min_dte(trade_list):
    r, _ = trade_list("AAPL", "2026-09-15", "4", dotenv="MIN_DTE=5\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _offset_lines(r.stdout) == ["Expiration Offset: +5 days (+4 raised to the 5-day minimum)"]


def test_the_weekday_rule_reads_the_floor_from_min_dte(trade_list):
    """A Tuesday report's same-week Friday is 3 DTE, under a 5-day floor."""
    r, _ = trade_list("AAPL", "2026-09-15", dotenv="MIN_DTE=5\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _expiration_lines(r.stdout) == ["Expiration: 2026-09-25 (weekday rule, at least 5 DTE)"]


def test_list_refuses_an_offset_that_is_not_a_whole_number(trade_list):
    r, calls = trade_list("AAPL", "2026-09-15", "one")
    assert r.returncode != 0
    assert "offset" in r.stdout.lower()
    assert not [c for c in calls if "scan.py" in c]


def test_list_refuses_a_malformed_ticker_before_the_scan(trade_list):
    r, calls = trade_list("AAPL,aapl,TSLA", "2026-09-15")
    assert r.returncode != 0
    assert "Invalid ticker format 'aapl'" in r.stdout
    assert not _ran(calls, "scan.py")


# --- trade.sh, every other subcommand ----------------------------------------------
# Ported 2026-09-14 from 2.0/tests/shell/test_trade.sh and test_trade_integration.sh,
# which CI never ran and which had stopped passing: one expected the expiration to
# be the earnings date + 1 day (the bug calculate_expiration_date replaced), the
# other a COMMANDS heading the help no longer has, and both exited at their first
# failure. What they checked by grepping trade.sh for its own text (set -euo
# pipefail, "Usage:", DATE_CMD=, a single sed) is not here: a test that reads the
# script instead of running it can only confirm the text is still there.

@pytest.mark.parametrize("ticker", ["aapl", "GOOGLE", "AA PL", "AAPL123", "AA-PL"])
def test_a_ticker_must_be_one_to_five_capital_letters(trade, ticker):
    r, calls = trade(ticker, "2026-09-15")
    assert r.returncode != 0
    assert f"Invalid ticker format '{ticker}'" in r.stdout
    assert not _ran(calls, "analyze.py")


@pytest.mark.parametrize("day", ["2026/09/15", "15-09-2026", "2026-09-5", "2026-9-15",
                                 "15-Sep-2026", "invalid"])
def test_a_date_must_be_yyyy_mm_dd(trade, day):
    r, calls = trade("scan", day)
    assert r.returncode != 0
    assert f"Invalid date format '{day}'" in r.stdout
    assert not _ran(calls, "scan.py")


@pytest.mark.parametrize("args, error", [
    (["AAPL"], "Error: Earnings date required"),
    (["scan"], "Error: Scan date required"),
    (["list"], "Error: Tickers and date required"),
    (["list", "AAPL"], "Error: Tickers and date required"),
    (["trr"], "Error: At least one ticker required"),
])
def test_a_missing_argument_is_an_error_before_any_work(trade, args, error):
    r, calls = trade(*args)
    assert r.returncode == 1
    assert error in r.stdout
    assert not [c for c in calls if "validate_bias_predictions.py" not in c]


@pytest.mark.parametrize("day, expiration", [
    ("2026-09-15", "2026-09-18"),    # Tuesday: the same week's Friday
    ("2026-09-17", "2026-09-25"),    # Thursday: next week's Friday, not 09-18 at 1 DTE
])
def test_ticker_mode_analyses_at_the_engines_trading_expiration(trade, day, expiration):
    r, calls = trade("AAPL", day)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"Analyzing AAPL for {day}" in r.stdout
    assert _ran(calls, "analyze.py") == [
        f"scripts/analyze.py AAPL --earnings-date {day} --expiration {expiration} --strategies"]
    assert "ANALYSIS RESULTS" in r.stdout and "VRP Ratio: 2.26x" in r.stdout


def test_ticker_mode_passes_a_given_expiration_through(trade):
    r, calls = trade("AAPL", "2026-09-17", "2026-10-16")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ran(calls, "analyze.py")[0].endswith("--expiration 2026-10-16 --strategies")


def test_ticker_mode_backfills_a_ticker_with_no_history_and_analyses_again(trade):
    r, calls = trade("NEWCO", "2026-09-17", **{
        "analyze.py": ["No historical data for NEWCO\n", "ANALYSIS RESULTS\n"],
        "backfill_historical.py": "Backfilled 24 earnings moves\n",
    })
    assert r.returncode == 0, r.stdout + r.stderr
    (backfill,) = _ran(calls, "backfill_historical.py")
    assert re.fullmatch(r"scripts/backfill_historical\.py NEWCO --start-date \d{4}-\d\d-\d\d "
                        r"--end-date \d{4}-\d\d-\d\d --execute", backfill)
    assert len(_ran(calls, "analyze.py")) == 2
    assert "Backfilled 24 earnings moves" in r.stdout
    assert "ANALYSIS RESULTS" in r.stdout


def test_ticker_mode_reports_a_failed_analysis_and_exits_non_zero(trade):
    log_line = "2026-09-17 10:00:00 - [MainThread] - analyze - ERROR - No options for AAPL\n"
    r, _ = trade("AAPL", "2026-09-17", **{"analyze.py": log_line})
    assert r.returncode == 1
    assert "Analysis failed" in r.stdout
    assert "⚠️  No options for AAPL" in r.stdout


def test_scan_runs_the_scan_for_its_date_and_strips_log_prefixes(trade):
    r, calls = trade("scan", "2026-09-15", **{"scan.py": (
        "2026-09-15 10:00:00 - [MainThread] - scan - INFO - Found 3 earnings events\n"
        "2026-09-15 10:00:01 - [MainThread] - scan - WARNING - ZZZZ has no chain\n")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Scanning Earnings for 2026-09-15" in r.stdout
    assert _ran(calls, "scan.py") == ["-u scripts/scan.py --scan-date 2026-09-15"]
    lines = r.stdout.splitlines()
    assert "Found 3 earnings events" in lines
    assert "⚠️  ZZZZ has no chain" in lines


@pytest.mark.parametrize("args, invocation", [
    (["whisper"], "-u scripts/scan.py --whisper-week"),
    (["whisper", "2026-09-14"], "-u scripts/scan.py --whisper-week 2026-09-14"),
])
def test_whisper_runs_the_current_or_a_given_week(trade, args, invocation):
    r, calls = trade(*args)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Most Anticipated Earnings" in r.stdout
    assert _ran(calls, "scan.py") == [invocation]


def test_whisper_refuses_a_malformed_week(trade):
    r, calls = trade("whisper", "09-14-2026")
    assert r.returncode != 0
    assert "Invalid date format '09-14-2026'" in r.stdout
    assert not _ran(calls, "scan.py")


def test_health_prints_the_check_and_runs_nothing_else(trade):
    r, calls = trade("health", **{"health_check.py": "System Health\nDatabase: UP\nTradier: HEALTHY\n"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Database: UP" in r.stdout and "Tradier: HEALTHY" in r.stdout
    assert calls == ["scripts/health_check.py"]


def test_a_failed_health_check_is_reported_and_exits_non_zero(trade):
    r, _ = trade("health", **{"health_check.py": ("System Health\nStatus: UNHEALTHY\n", 1)})
    assert r.returncode == 1
    assert "Health check failed" in r.stdout


@pytest.mark.parametrize("args", [["help"], ["--help"], ["-h"], []])
def test_help_lists_every_subcommand(trade, args):
    r, calls = trade(*args)
    assert r.returncode == 0, r.stdout + r.stderr
    usage = r.stdout[r.stdout.index("USAGE"):r.stdout.index("EXAMPLES")]
    for subcommand in ("TICKER YYYY-MM-DD", "list TICKERS", "scan YYYY-MM-DD", "whisper",
                       "trr TICKER", "harvest", "taco", "sync", "health"):
        assert subcommand in usage, subcommand
    # Usage runs nothing: until 2026-09-14 a bare ./trade.sh ran the bias validator,
    # a writer, with --execute before printing it.
    assert calls == []


def test_without_a_venv_nothing_runs(trade):
    shutil.rmtree(trade.engine / "venv")
    r, calls = trade("scan", "2026-09-15")
    assert r.returncode == 1
    assert "venv not found" in r.stdout
    assert calls == []
