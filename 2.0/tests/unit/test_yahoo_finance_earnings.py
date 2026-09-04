"""
Unit tests for YahooFinanceEarnings.get_earnings_date_near().

Regression coverage for the 2026-08-06 fix: cleanup_duplicate_earnings()'s
confirmed-vs-confirmed corroboration used to call get_next_earnings_date(),
which only ever answers "what's the NEXT upcoming earnings date" (via
stock.calendar, forward-looking from today). For a ticker whose disputed
duplicate dates had already occurred (AXON, SKYT, ABTC — both candidates
<= today by the time dedup ran), "next" had already rolled forward past
the cluster to the FOLLOWING quarter and could never match either
candidate — not just that run, on every future retry too.

get_earnings_date_near() fixes this by searching stock.earnings_dates,
which spans past AND future quarters, for whichever entry is nearest the
disputed cluster — not just "next".
"""

import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings


class _FakeEarningsDatesFrame:
    """Minimal stand-in for the pandas DataFrame yfinance returns from
    stock.earnings_dates — only .index and len() are used by the code
    under test, and plain datetime objects satisfy .date()/.hour/.minute
    the same way pandas Timestamps do."""

    def __init__(self, timestamps):
        self.index = timestamps

    def __len__(self):
        return len(self.index)


def _make_stock(timestamps):
    stock = MagicMock()
    stock.earnings_dates = _FakeEarningsDatesFrame(timestamps)
    return stock


class TestGetEarningsDateNear:

    def test_finds_already_past_date_over_the_next_quarter(self):
        """The disputed cluster is entirely in the past; the ticker's actual
        NEXT quarter (further out) also exists in history. Must return the
        entry nearest the disputed cluster, not the next upcoming one —
        this is the exact AXON/SKYT/ABTC failure mode."""
        past_date = date.today() - timedelta(days=3)
        next_quarter_date = date.today() + timedelta(days=90)

        stock = _make_stock([
            datetime(past_date.year, past_date.month, past_date.day, 16, 5),  # AMC
            datetime(
                next_quarter_date.year, next_quarter_date.month,
                next_quarter_date.day, 7, 0,
            ),  # BMO — unrelated, later quarter
        ])

        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_earnings_date_near(
                "AXON", [past_date, past_date + timedelta(days=2)]
            )

        assert result.is_ok
        found_date, timing = result.value
        assert found_date == past_date
        assert timing.value == "AMC"

    def test_picks_closest_of_two_history_entries(self):
        """Two history entries flank the reference dates — picks whichever
        is numerically closer, not just the first in the list."""
        near_date = date.today() - timedelta(days=1)
        far_date = date.today() - timedelta(days=40)

        stock = _make_stock([
            datetime(far_date.year, far_date.month, far_date.day, 16, 0),
            datetime(near_date.year, near_date.month, near_date.day, 7, 15),  # BMO
        ])

        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_earnings_date_near("SKYT", [near_date])

        assert result.is_ok
        found_date, timing = result.value
        assert found_date == near_date
        assert timing.value == "BMO"

    def test_no_history_returns_err(self):
        """Empty earnings_dates history — Err, not a false 'no match'."""
        stock = _make_stock([])

        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_earnings_date_near("ABTC", [date.today()])

        assert result.is_err

    def test_none_earnings_dates_returns_err(self):
        """stock.earnings_dates is None (ticker not covered) — Err."""
        stock = MagicMock()
        stock.earnings_dates = None

        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_earnings_date_near("ZZZZ", [date.today()])

        assert result.is_err

    def test_exception_returns_err(self):
        """Underlying yfinance call raises — wrapped as Err, not propagated."""
        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            side_effect=RuntimeError("network error"),
        ):
            result = fetcher.get_earnings_date_near("AAPL", [date.today()])

        assert result.is_err
