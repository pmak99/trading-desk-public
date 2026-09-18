"""The implied move, and so VRP, is priced at one expiration on every path.

/analyze (TickerAnalyzer) used to price it at the TRADING expiration, the 3-10 DTE
Friday calculate_expiration_date picks for the spread, while scan, whisper and the
Telegram bot (ticker_analysis.analyze_ticker) priced it at the first expiration
after the report. A later expiration adds ordinary diffusion: 8% event move, 50%
vol, a Wednesday report is 8.81% at that Friday and 11.21% at the next, +27% VRP.
Logged side by side, a Wednesday or Thursday AMC /analyze read 10-30% above scan
on the report day.

The parity tests drive both real entry points with one fake chain listing and
assert they hand the implied-move calculator the same expiration.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, PropertyMock

import pytest

from src.application.metrics.implied_move_expiration import (  # noqa: E402
    first_reaction_date,
    implied_move_expiration,
)
from src.domain.enums import EarningsTiming  # noqa: E402
from src.domain.errors import AppError, Err, ErrorCode, Ok  # noqa: E402
from src.infrastructure.cache.memory_cache import (  # noqa: E402
    CachedOptionsDataProvider,
    MemoryCache,
)

BMO, AMC, DMH, UNKNOWN = (EarningsTiming.BMO, EarningsTiming.AMC,
                          EarningsTiming.DMH, EarningsTiming.UNKNOWN)


def _monday(weeks_ahead=2):
    """A Monday comfortably in the future, so no path rejects a past date."""
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7) + timedelta(weeks=weeks_ahead)


class _Listing:
    """Stands in for Tradier behind the real CachedOptionsDataProvider."""

    def __init__(self, expirations):
        self.expirations = sorted(expirations)

    def get_expirations(self, ticker):
        return Ok(list(self.expirations))


def _fridays(monday, weeks=6):
    return [monday + timedelta(days=4, weeks=w) for w in range(weeks)]


def _weekdays(monday, weeks=6):
    return [monday + timedelta(days=d, weeks=w) for w in range(weeks) for d in range(5)]


def _provider(expirations):
    return CachedOptionsDataProvider(provider=_Listing(expirations), cache=MemoryCache())


# --- the rule ---------------------------------------------------------------

def test_bmo_reacts_on_the_report_date():
    d = date(2026, 10, 16)
    assert first_reaction_date(d, BMO) == d


@pytest.mark.parametrize("timing", [AMC, DMH, UNKNOWN])
def test_everything_else_reacts_the_next_day(timing):
    """UNKNOWN must not be read as BMO: an expiration on the report date closes
    before an AMC report and would price no event at all."""
    d = date(2026, 10, 16)
    assert first_reaction_date(d, timing) == d + timedelta(days=1)


def test_friday_bmo_on_weekly_listings_is_that_friday():
    """The expiration the old next-trading-day rule skipped a whole week past."""
    m = _monday()
    fri = m + timedelta(days=4)
    result = implied_move_expiration(_provider(_fridays(m)), "JPM", fri, BMO)
    assert result.value == fri


def test_friday_amc_on_weekly_listings_is_the_next_friday():
    m = _monday()
    fri = m + timedelta(days=4)
    result = implied_move_expiration(_provider(_fridays(m)), "X", fri, AMC)
    assert result.value == fri + timedelta(days=7)


def test_daily_listings_use_the_reaction_day_itself():
    m = _monday()
    thu = m + timedelta(days=3)
    provider = _provider(_weekdays(m))
    assert implied_move_expiration(provider, "NVDA", thu, BMO).value == thu
    assert implied_move_expiration(provider, "NVDA", thu, AMC).value == thu + timedelta(days=1)


def test_a_listing_error_propagates():
    class _Down:
        def get_expirations(self, ticker):
            return Err(AppError(ErrorCode.EXTERNAL, "tradier down"))

    provider = CachedOptionsDataProvider(provider=_Down(), cache=MemoryCache())
    result = implied_move_expiration(provider, "X", date(2026, 10, 16), AMC)
    assert result.is_err


# --- parity through both entry points ---------------------------------------

def _container(expirations):
    """A container whose only live piece is the cached provider over a listing.

    The implied-move calculator records the expiration it is asked for and then
    fails, which ends both paths right after the choice under test. Touching
    raw container.tradier fails the test: chain lookups go through the cache.
    """
    container = MagicMock()
    type(container).tradier = PropertyMock(
        side_effect=AssertionError("raw container.tradier used; fetch via cached_options_provider")
    )
    container.cached_options_provider = _provider(expirations)
    container.config.thresholds.require_weekly_options = False
    seen = []

    def calculate(ticker, expiration):
        seen.append(expiration)
        return Err(AppError(ErrorCode.NODATA, "stop here"))

    container.implied_move_calculator.calculate.side_effect = calculate
    return container, seen


def _via_analyzer(expirations, earnings_date, timing):
    from src.application.services.analyzer import TickerAnalyzer
    from src.domain.trading_expiration import calculate_expiration_date

    container, seen = _container(expirations)
    result = TickerAnalyzer(container).analyze(
        ticker="TEST",
        earnings_date=earnings_date,
        expiration=calculate_expiration_date(earnings_date, timing),
        earnings_timing=timing,
    )
    assert result.is_err
    return seen


def _via_scan(expirations, earnings_date, timing, monkeypatch):
    from scripts.scan.workflows import ticker_analysis as ta
    from src.domain.trading_expiration import calculate_expiration_date

    monkeypatch.setattr(ta, "get_ticker_name", lambda ticker: "Test Co")
    container, seen = _container(expirations)
    result = ta.analyze_ticker(
        container, "TEST", earnings_date,
        calculate_expiration_date(earnings_date, timing),
        earnings_timing=timing,
    )
    assert result is None
    return seen


# (weekday offset from Monday, timing, listing, expected expiration offset from Monday)
PARITY_CASES = [
    ("wed_amc_weekly", 2, AMC, _fridays, 4),
    ("wed_bmo_weekly", 2, BMO, _fridays, 4),
    ("thu_amc_weekly", 3, AMC, _fridays, 4),
    ("thu_bmo_weekly", 3, BMO, _fridays, 4),
    ("fri_bmo_weekly", 4, BMO, _fridays, 4),
    ("fri_amc_weekly", 4, AMC, _fridays, 11),
    ("fri_unknown_weekly", 4, UNKNOWN, _fridays, 11),
    ("thu_bmo_daily", 3, BMO, _weekdays, 3),
    ("thu_amc_daily", 3, AMC, _weekdays, 4),
    ("wed_unknown_daily", 2, UNKNOWN, _weekdays, 3),
]


@pytest.mark.parametrize(
    "offset,timing,listing,expected",
    [c[1:] for c in PARITY_CASES],
    ids=[c[0] for c in PARITY_CASES],
)
def test_analyze_and_scan_price_the_implied_move_at_the_same_expiration(
    offset, timing, listing, expected, monkeypatch
):
    m = _monday()
    earnings_date = m + timedelta(days=offset)
    expirations = listing(m)

    analyzer_seen = _via_analyzer(expirations, earnings_date, timing)
    scan_seen = _via_scan(expirations, earnings_date, timing, monkeypatch)

    assert analyzer_seen == scan_seen == [m + timedelta(days=expected)]


def test_analyzer_no_longer_prices_at_the_trading_expiration():
    """The pre-fix bug, pinned: a Thursday AMC on weekly listings trades the next
    week's Friday but must price the implied move at the Friday after the report."""
    from src.domain.trading_expiration import calculate_expiration_date

    m = _monday()
    thu = m + timedelta(days=3)
    trading = calculate_expiration_date(thu, AMC)
    seen = _via_analyzer(_fridays(m), thu, AMC)
    assert trading == thu + timedelta(days=8)
    assert seen == [thu + timedelta(days=1)]
