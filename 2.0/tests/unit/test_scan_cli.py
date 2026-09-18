"""scripts/scan/cli.py against the invocations that actually reach it.

trade.sh is scan.py's only caller, and it pipes the output through sed and
grep: a flag argparse no longer accepts prints a usage line into that pipe and
the wrapper's `if !` sees grep's exit status, not Python's. So a renamed or
dropped flag would not fail loudly; it would turn `./trade.sh scan` into a
usage message. Every invocation in trade.sh is parsed here.
"""

import inspect
import re
from pathlib import Path

import pytest

from scripts.scan.cli import parse_args

TRADE_SH = Path(__file__).resolve().parents[2] / "trade.sh"

# Stand-ins for the shell variables trade.sh passes.
SHELL_VALUES = {"tickers": "AAPL,MSFT", "offset_days": "1", "scan_date": "2026-09-15",
                "earnings_date": "2026-09-15"}


def _trade_sh_invocations() -> list[list[str]]:
    text = TRADE_SH.read_text()
    calls = []
    for match in re.finditer(r"scripts/scan\.py((?:[ \t]+(?:--[\w-]+|\"\$\w+\"|\$\w+))*)", text):
        argv = []
        for token in match.group(1).split():
            var = re.fullmatch(r"\"?\$(\w+)\"?", token)
            argv.append(SHELL_VALUES[var.group(1)] if var else token)
        calls.append(argv)
    return calls


def test_trade_sh_still_calls_every_mode():
    modes = {argv[0] for argv in _trade_sh_invocations()}
    assert modes == {"--tickers", "--scan-date", "--whisper-week", "--harvest"}


@pytest.mark.parametrize("argv", _trade_sh_invocations(), ids=lambda a: " ".join(a))
def test_every_trade_sh_invocation_parses(argv):
    args = parse_args(argv)
    assert args.log_level == "INFO"
    assert args.sequential is False          # parallel unless asked


def test_ticker_mode_carries_the_date_and_offset_trade_sh_passes():
    args = parse_args(["--tickers", "AAPL,MSFT", "--earnings-date", "2026-09-15",
                       "--expiration-offset", "1"])
    assert (args.tickers, str(args.earnings_date), args.expiration_offset) == (
        "AAPL,MSFT", "2026-09-15", 1)
    assert args.scan_date is None and args.whisper_week is None and not args.harvest


def test_trade_sh_list_passes_its_date_and_the_offset_only_when_given():
    text = TRADE_SH.read_text()
    (line,) = [ln for ln in text.splitlines() if "scripts/scan.py --tickers" in ln]
    assert '--earnings-date "$earnings_date"' in line
    assert '${offset_days:+--expiration-offset "$offset_days"}' in line


def test_whisper_week_without_a_date_is_the_current_week_sentinel():
    # trade.sh appends the Monday only when one was given. main() reads
    # `whisper_week is not None` as whisper mode and '' as "let the scraper pick".
    assert parse_args(["--whisper-week"]).whisper_week == ""
    assert parse_args(["--whisper-week", "2026-09-14"]).whisper_week == "2026-09-14"
    assert parse_args(["--scan-date", "2026-09-15"]).whisper_week is None


@pytest.mark.parametrize("argv", [
    [],                                                   # a mode is required
    ["--scan-date", "2026-09-15", "--tickers", "AAPL"],   # and only one
    ["--harvest", "--whisper-week"],
    ["--tickers", "AAPL", "--log-level", "TRACE"],
    ["--tickers", "AAPL", "--expiration-offset", "one"],
])
def test_invalid_invocations_exit_before_any_work(argv):
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2


# --- the help text ------------------------------------------------------------------

ENGINE = TRADE_SH.parent
# A command the engine prints for someone to run: `python scripts/x.py`.
SCRIPT_COMMAND = re.compile(r"python3? (?:-u )?(scripts/[\w/]+\.py)")
# A function the help names with the file that defines it: `name() in path.py`.
FUNCTION_IN_FILE = re.compile(r"(\w+)\(\) in ([\w/]+\.py)")


def _help(capsys) -> str:
    """`scan.py --help` as printed, whitespace collapsed so argparse's line
    wrapping cannot split a phrase."""
    with pytest.raises(SystemExit) as exc:
        parse_args(["--help"])
    assert exc.value.code == 0
    return " ".join(capsys.readouterr().out.split())


def test_the_help_promises_no_expiration_the_code_never_picks(capsys):
    """The epilog said a Friday BMO report expired the same day (0 DTE) and a
    Thursday AMC report the next day (1 DTE). calculate_expiration_date picks
    neither: both take the following week's Friday under the 3 DTE floor."""
    text = _help(capsys).lower()
    for claim in ("same day", "next day", "next friday (conservative)"):
        assert claim not in text


def test_the_help_names_the_functions_that_pick_both_expirations(capsys):
    named = dict(FUNCTION_IN_FILE.findall(_help(capsys)))
    assert {"calculate_expiration_date", "implied_move_expiration"} <= set(named)
    for function, rel in named.items():
        assert f"def {function}(" in (ENGINE / rel).read_text(), f"{function} is not in {rel}"


def test_the_help_says_a_custom_offset_is_floored(capsys):
    text = _help(capsys)
    option = text[text.rindex("--expiration-offset EXPIRATION_OFFSET"):]   # not the usage line
    assert "minimum DTE" in option[:option.index("--sequential")]


def test_the_help_examples_use_offsets_the_floor_leaves_alone(capsys):
    from src.domain.trading_expiration import calculate_expiration_date

    floor = inspect.signature(calculate_expiration_date).parameters["min_dte"].default
    offsets = [int(n) for n in re.findall(r"--expiration-offset (\d+)", _help(capsys))]
    assert offsets and min(offsets) >= floor


def test_the_help_says_which_runs_backfill(capsys):
    """Only analyze_ticker's one-ticker-at-a-time callers pass auto_backfill=True.
    The help said whisper mode backfilled, but whisper runs in parallel unless
    --sequential is given, and the concurrent path never backfills."""
    text = _help(capsys)
    assert "Whisper mode auto-backfills" not in text
    assert "--sequential" in text[text.index("backfilled automatically"):]


def test_every_script_the_help_names_exists(capsys):
    """The help pointed at scripts/backfill.py, which does not exist."""
    named = SCRIPT_COMMAND.findall(_help(capsys))
    assert "scripts/backfill_historical.py" in named
    assert [rel for rel in named if not (ENGINE / rel).is_file()] == []


def test_every_script_the_engine_tells_someone_to_run_exists():
    """analyze.py printed the same dead scripts/backfill.py for a ticker with no
    history. Every `python scripts/...` in the engine's scripts and src."""
    missing = {}
    for root in ("scripts", "src"):
        for path in sorted((ENGINE / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for rel in SCRIPT_COMMAND.findall(path.read_text()):
                if not (ENGINE / rel).is_file():
                    missing.setdefault(rel, []).append(str(path.relative_to(ENGINE)))
    assert missing == {}
