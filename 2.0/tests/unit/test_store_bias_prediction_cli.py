"""store_bias_prediction.py's command line: the event it predicts is the one it is given.

`./trade.sh TICKER DATE` runs this script before analyze.py. Until 2026-09-14 the wrapper
passed the ticker alone and the CLI took the ticker's next calendar date on or after
today, so a run for any other date stored a prediction for a different event, or none.
`--start`/`--end` were parsed and never read. `--earnings-date` is now required with
tickers; `--all` still covers the calendar's next `--days-ahead` days, read through the
earnings repository.
"""

import sys
from datetime import date
from unittest.mock import Mock, patch

import pytest

import scripts.store_bias_prediction as sbp
from src.domain.errors import Ok

EVENT = date(2026, 10, 14)


def _run(monkeypatch, *argv, upcoming=()):
    """main() with the migration, container and writer stubbed; returns (exit code,
    the events handed to analyze_and_store, the container)."""
    monkeypatch.setattr(sys, "argv", ["store_bias_prediction.py", *argv])
    analysed = []
    container = Mock()
    container.earnings_repository.get_upcoming_earnings.return_value = Ok(list(upcoming))

    def no_calendar_query(*args, **kwargs):
        raise AssertionError("the CLI reads the calendar through the repository only")

    with patch.object(sbp, "setup_logging"), \
         patch.object(sbp, "apply_migrations") as migrate, \
         patch.object(sbp, "Container", return_value=container), \
         patch.object(sbp, "connect", side_effect=no_calendar_query), \
         patch.object(sbp, "analyze_and_store",
                      side_effect=lambda c, t, d: analysed.append((t, d)) or sbp.STORED):
        code = sbp.main()
    return code, analysed, container, migrate


def test_tickers_are_predicted_for_the_date_given(monkeypatch):
    code, analysed, _, _ = _run(monkeypatch, "MU", "NVDA", "--earnings-date", "2026-10-14")
    assert code == 0
    assert analysed == [("MU", EVENT), ("NVDA", EVENT)]


@pytest.mark.parametrize("argv", [
    ["MU"],                                               # a ticker needs its date
    ["MU", "--earnings-date", "10/14/2026"],
    ["--start", "2026-10-01", "--end", "2026-10-07"],     # parsed and never read; gone
    ["--all", "--earnings-date", "2026-10-14"],           # --all takes the calendar's dates
])
def test_an_invocation_it_cannot_honour_exits_before_migrating(monkeypatch, argv):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, *argv)
    assert exc.value.code == 2


def test_all_reads_the_upcoming_events_from_the_repository(monkeypatch):
    code, analysed, container, _ = _run(
        monkeypatch, "--all", "--days-ahead", "7", upcoming=[("MU", EVENT)])
    assert code == 0
    assert analysed == [("MU", EVENT)]
    container.earnings_repository.get_upcoming_earnings.assert_called_once_with(7)
