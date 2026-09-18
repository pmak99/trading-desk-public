"""The calendar sync writes a date only when Finnhub and Yahoo both have it and agree.

`validate_earnings_date` flagged a conflict only between two sources that answered.
When Yahoo had no date, Finnhub's bulk date stood alone, nothing conflicted with
it, and every write path took it as consensus: on 2026-09-15 the unattended
/maintenance sync inserted SKYT 2026-11-03 as `confirmed` -- SkyWater was acquired
by IonQ and delisted 2026-07-31 -- and the same hole let a lone Finnhub date
replace a stored one. `ValidationResult.corroborated` is the gate now.

Marking such a row `confirmed=false` instead would not be safe: the post-sync
dedup deletes an unconfirmed row within 90 days of a confirmed one, which is next
quarter's row for any ticker that reported in the last two weeks.

The same run's summary said "Updated dates: 67" -- each a stored date Yahoo
re-confirmed while Finnhub was a day off -- and ended before the unattended run
could read it, so the report named one of four conflicts. Re-confirmations count
as unchanged, and every date not written is listed at the end of the summary.
"""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from common.timezone import today_et
from src.application.services.earnings_date_validator import (
    EarningsDateInfo,
    EarningsDateValidator,
    EarningsSource,
    ValidationResult,
)
from src.domain.errors import AppError, Err, ErrorCode, Ok
from src.domain.types import EarningsTiming
from src.infrastructure.api.finnhub import FinnhubAPI
from src.infrastructure.database.repositories.earnings_repository import (
    EarningsRepository,
)

AMC, UNKNOWN = EarningsTiming.AMC, EarningsTiming.UNKNOWN
D = today_et() + timedelta(days=30)


class _Yahoo:
    """get_next_earnings_date answers from a dict; a missing ticker has no date."""

    def __init__(self, answers):
        self.answers = answers

    def get_next_earnings_date(self, ticker):
        if ticker in self.answers:
            return Ok(self.answers[ticker])
        return Err(AppError(ErrorCode.NODATA, f"No earnings date for {ticker}"))


def _run(finnhub_rows, stored, yahoo, dry_run=False):
    """One focused sync over `finnhub_rows` with `stored` as the calendar."""
    import scripts.sync_earnings_calendar as sync

    finnhub = MagicMock(spec=FinnhubAPI)
    finnhub.get_earnings_calendar.return_value = Ok(finnhub_rows)
    repo = MagicMock(spec=EarningsRepository)
    repo.save_earnings_event.return_value = Ok(None)
    validator = EarningsDateValidator(finnhub=finnhub, yahoo_finance=_Yahoo(yahoo))
    with patch.object(sync, "get_focused_tickers", return_value={r[0] for r in finnhub_rows}), \
         patch.object(sync, "get_database_dates", return_value=stored), \
         patch.object(sync, "cleanup_duplicate_earnings", return_value=[]):
        stats = sync.sync_earnings_calendar(
            validator=validator, earnings_repo=repo, finnhub=finnhub, dry_run=dry_run)
    return stats, repo


def _stored(d, timing=AMC, age_days=0):
    updated = datetime.now(timezone.utc) - timedelta(days=age_days)
    return (d, timing, updated, None)


def _info(source, d):
    return EarningsDateInfo(source=source, earnings_date=d, timing=AMC, confidence=1.0)


# --- the gate ---------------------------------------------------------------

@pytest.mark.parametrize("sources, conflict, expected", [
    ([EarningsSource.FINNHUB], False, False),
    ([EarningsSource.FINNHUB, EarningsSource.FINNHUB], False, False),
    ([EarningsSource.YAHOO_FINANCE, EarningsSource.FINNHUB], True, False),
    ([EarningsSource.YAHOO_FINANCE, EarningsSource.FINNHUB], False, True),
])
def test_corroborated_needs_two_different_sources_that_agree(sources, conflict, expected):
    result = ValidationResult(ticker="X", consensus_date=D, consensus_timing=AMC,
                              sources=[_info(s, D) for s in sources], has_conflict=conflict)
    assert result.corroborated is expected


# --- every write path refuses a lone source -----------------------------------

def test_a_new_event_only_finnhub_lists_is_not_inserted():
    stats, repo = _run([("SKYT", D, UNKNOWN)], stored={}, yahoo={})

    repo.save_earnings_event.assert_not_called()
    assert (stats.new_dates, stats.uncorroborated, stats.errors) == (0, 1, 1)
    assert stats.needs_review == [{
        "ticker": "SKYT", "stored": None, "reason": "only one source has a date",
        "sources": f"Finnhub {D} (UNKNOWN)"}]


def test_a_changed_date_only_finnhub_lists_does_not_replace_the_stored_one():
    stats, repo = _run([("AAPL", D + timedelta(days=3), AMC)],
                       stored={"AAPL": _stored(D)}, yahoo={})

    repo.save_earnings_event.assert_not_called()
    assert stats.updated_dates == 0 and stats.uncorroborated == 1
    assert stats.needs_review[0]["stored"] == D


def test_a_stale_date_only_finnhub_confirms_is_not_touched():
    stats, repo = _run([("AAPL", D, AMC)], stored={"AAPL": _stored(D, age_days=30)}, yahoo={})

    repo.save_earnings_event.assert_not_called()
    assert stats.unchanged_dates == 0 and stats.uncorroborated == 1


def test_sources_that_disagree_are_a_conflict_and_not_written():
    stats, repo = _run([("FDX", D, UNKNOWN)], stored={"FDX": _stored(D, UNKNOWN, age_days=30)},
                       yahoo={"FDX": (D + timedelta(days=42), AMC)})

    repo.save_earnings_event.assert_not_called()
    assert (stats.conflicts_detected, stats.uncorroborated, stats.errors) == (1, 0, 1)
    assert stats.needs_review[0]["reason"] == "sources disagree"


# --- what both sources agree on is still written --------------------------------

def test_a_new_event_both_sources_list_is_inserted():
    stats, repo = _run([("NFLX", D, AMC)], stored={}, yahoo={"NFLX": (D, AMC)})

    repo.save_earnings_event.assert_called_once_with(ticker="NFLX", earnings_date=D, timing=AMC)
    assert stats.new_dates == 1 and stats.errors == 0 and stats.needs_review == []


def test_a_date_both_sources_move_is_updated_and_listed():
    new = D + timedelta(days=2)
    stats, repo = _run([("AMD", new, AMC)], stored={"AMD": _stored(D)}, yahoo={"AMD": (new, AMC)})

    repo.save_earnings_event.assert_called_once_with(ticker="AMD", earnings_date=new, timing=AMC)
    assert stats.updated_dates == 1
    assert [c["reason"] for c in stats.changes] == ["Date changed"]


def test_a_stored_date_yahoo_reconfirms_is_unchanged_not_an_update():
    """Finnhub a day early, Yahoo on the stored date: 50 of 67 such rows on 2026-09-15."""
    stats, repo = _run([("GOOGL", D - timedelta(days=1), AMC)],
                       stored={"GOOGL": _stored(D)}, yahoo={"GOOGL": (D, AMC)})

    repo.save_earnings_event.assert_called_once_with(ticker="GOOGL", earnings_date=D, timing=AMC)
    assert (stats.updated_dates, stats.unchanged_dates) == (0, 1)
    assert stats.changes == []


def test_a_failed_save_of_a_reconfirmed_date_is_an_error():
    import scripts.sync_earnings_calendar as sync

    finnhub = MagicMock(spec=FinnhubAPI)
    finnhub.get_earnings_calendar.return_value = Ok([("GOOGL", D, AMC)])
    repo = MagicMock(spec=EarningsRepository)
    repo.save_earnings_event.return_value = Err(AppError(ErrorCode.DBERROR, "boom"))
    validator = EarningsDateValidator(finnhub=finnhub, yahoo_finance=_Yahoo({"GOOGL": (D, AMC)}))
    with patch.object(sync, "get_focused_tickers", return_value={"GOOGL"}), \
         patch.object(sync, "get_database_dates", return_value={"GOOGL": _stored(D, age_days=30)}), \
         patch.object(sync, "cleanup_duplicate_earnings", return_value=[]):
        stats = sync.sync_earnings_calendar(validator=validator, earnings_repo=repo, finnhub=finnhub)

    assert stats.errors == 1 and stats.needs_review == []


# --- the summary carries the review list ---------------------------------------

def test_the_summary_ends_with_every_date_not_written(caplog):
    stats, _ = _run(
        [("CAVA", D, UNKNOWN), ("FDX", D, UNKNOWN), ("SKYT", D, UNKNOWN), ("NFLX", D, AMC)],
        stored={"CAVA": _stored(D, UNKNOWN, age_days=30), "FDX": _stored(D, UNKNOWN, age_days=30)},
        yahoo={"CAVA": (D + timedelta(days=8), AMC), "FDX": (D + timedelta(days=42), AMC),
               "NFLX": (D, AMC)},
        dry_run=True,
    )
    caplog.set_level(logging.INFO, logger="scripts.sync_earnings_calendar")
    stats.log_summary()
    lines = [r.getMessage() for r in caplog.records]

    assert "  ✗ Errors: 3 (3 need review, 0 failed)" in lines
    tail = lines[lines.index(next(m for m in lines if m.startswith("\nNEEDS REVIEW"))):]
    assert tail[0] == "\nNEEDS REVIEW (3) — not written:"
    assert [t.split(":")[0].strip() for t in tail[1:]] == ["CAVA", "FDX", "SKYT"]
    assert f"SKYT: stored nothing — only one source has a date: Finnhub {D} (UNKNOWN)" in tail[3]
