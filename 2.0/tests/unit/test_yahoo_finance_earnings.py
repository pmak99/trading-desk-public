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

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

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


class TestGetNextEarningsDate:
    """Regression coverage for the 2026-09-10 fix.

    Two independent bugs in get_next_earnings_date(), found live via
    /maintenance sync: (1) calendar['Earnings Date'][0] was taken with no
    check that it's actually upcoming — Yahoo's .calendar cache can lag for
    days/weeks after a report before rolling forward, so AI/LULU/OXM/WDAY
    all served their last-reported (past) date as "next". (2) the timing
    refinement block compared a tz-aware DatetimeIndex against a naive
    datetime.now(), raising on every call — silently swallowed, so every
    ticker fell through to a hardcoded AMC default. Because
    EarningsDateValidator._get_consensus() trusted Yahoo's timing
    unconditionally, this silently overwrote correct Finnhub BMO tags with
    AMC on every sync (DAL/DDOG/DOCN/FISV/MNDY/SHOP/SOFI/SPOT/UBER, one
    run, 2026-09-10).
    """

    @staticmethod
    def _make_stock(calendar_dates, earnings_dates_index=None):
        stock = MagicMock()
        stock.calendar = {"Earnings Date": calendar_dates}
        if earnings_dates_index is not None:
            import pandas as pd
            stock.earnings_dates = pd.DataFrame(index=earnings_dates_index)
        else:
            stock.earnings_dates = None
        return stock

    def test_stale_calendar_date_rejected(self):
        """calendar['Earnings Date'] only has a past date — must not be
        returned as "next"; this is the exact AI/LULU/OXM/WDAY failure mode."""
        past_date = date.today() - timedelta(days=8)
        stock = self._make_stock([past_date])

        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_next_earnings_date("WDAY")

        assert result.is_err

    def test_picks_first_future_date_skipping_stale_ones(self):
        """calendar['Earnings Date'] has a stale past entry ahead of the
        real upcoming one — the past entry must be skipped, not taken as
        earnings_dates[0]."""
        past_date = date.today() - timedelta(days=5)
        future_date = date.today() + timedelta(days=45)
        stock = self._make_stock([past_date, future_date])

        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_next_earnings_date("TEST")

        assert result.is_ok
        found_date, _ = result.value
        assert found_date == future_date

    def test_timing_detected_from_tz_aware_index_without_raising(self):
        """earnings_dates.index is tz-aware (as real yfinance data is) — the
        BMO/AMC detection must not raise comparing it to date.today(), and
        must actually detect the real timing rather than silently falling
        through to a default."""
        import pandas as pd

        future_date = date.today() + timedelta(days=45)
        tz_index = pd.DatetimeIndex(
            [pd.Timestamp(future_date.isoformat() + " 07:00:00", tz="America/New_York")]
        )
        stock = self._make_stock([future_date], earnings_dates_index=tz_index)

        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_next_earnings_date("FISV")

        assert result.is_ok
        _, timing = result.value
        assert timing.value == "BMO"

    def test_timing_unknown_when_earnings_dates_has_no_future_row(self):
        """earnings_dates exists but has no row matching the upcoming date —
        timing must come back UNKNOWN, not a guessed AMC, so a caller with a
        second source can still supply a definite answer."""
        import pandas as pd

        future_date = date.today() + timedelta(days=45)
        stale_index = pd.DatetimeIndex(
            [pd.Timestamp((date.today() - timedelta(days=90)).isoformat() + " 16:00:00", tz="America/New_York")]
        )
        stock = self._make_stock([future_date], earnings_dates_index=stale_index)

        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_next_earnings_date("TEST2")

        assert result.is_ok
        _, timing = result.value
        assert timing.value == "UNKNOWN"


class TestNextEarningsDateCache:
    """The LRU cache in front of get_next_earnings_date. Its hit/miss counters and
    get_cache_stats/clear_cache were read only by the module's __main__ demo,
    and went with it (2026-09-14)."""

    def test_a_hit_is_not_fetched_and_the_least_recently_used_is_evicted(self):
        import src.infrastructure.data_sources.yahoo_finance_earnings as mod

        fetched = []

        def ticker(symbol):
            fetched.append(symbol)
            return TestGetNextEarningsDate._make_stock([date.today() + timedelta(days=30)])

        fetcher = YahooFinanceEarnings(max_cache_size=2)
        with patch.object(mod.yf, "Ticker", side_effect=ticker), \
             patch.object(mod, "yahoo_call", side_effect=lambda call: call()):
            for symbol in ("AAA", "BBB", "AAA", "CCC", "AAA", "BBB"):
                assert fetcher.get_next_earnings_date(symbol).is_ok
        # AAA hits twice; CCC evicts BBB, the least recently used, so BBB is fetched again.
        assert fetched == ["AAA", "BBB", "CCC", "BBB"]

    def test_the_demo_only_api_is_gone(self):
        import src.infrastructure.data_sources.yahoo_finance_earnings as mod

        assert not hasattr(YahooFinanceEarnings, "get_cache_stats")
        assert not hasattr(YahooFinanceEarnings, "clear_cache")
        assert "__main__" not in open(mod.__file__).read()


class TestPlaceholderHourDMH:
    """A DMH read off an UNCONFIRMED future row is usually a Yahoo placeholder.

    Yahoo frequently reports an unconfirmed future date at exactly one hour
    before the ticker's real report time — 15:00 standing in for a confirmed
    16:00 AMC — which lands inside the DMH band (09:30–16:00). Verified live
    2026-09-12: ADBE, ORCL and CRM each show their last four CONFIRMED reports
    at hour 16 while the next unconfirmed date reads 15. Not universal (COST
    reads 16), which is why the correction only fires on DMH.

    Why it is worth correcting rather than tolerating: a wrong timing tag makes
    historical_moves measure the WRONG reaction-day window — how AXON's data
    was corrupted (CLAUDE.md, Aug 6 2026) — and it feeds
    calculate_expiration_date, which is what enforces the min-3-DTE rule.

    Found by the weekly maintenance run on 2026-09-12, which reported 63 timing
    corrections.
    """

    @staticmethod
    def _stock(future_hour, confirmed_hours, with_eps_column=True):
        """A frame with confirmed history plus one unconfirmed future row.

        yfinance marks a row confirmed by having a non-NaN 'Reported EPS'.
        """
        import pandas as pd

        future_date = date.today() + timedelta(days=45)
        rows, eps = [], []
        for i, h in enumerate(confirmed_hours):
            d = date.today() - timedelta(days=90 * (i + 1))
            rows.append(pd.Timestamp(f"{d.isoformat()} {h:02d}:00:00", tz="America/New_York"))
            eps.append(1.23)                       # reported -> confirmed
        rows.insert(0, pd.Timestamp(
            f"{future_date.isoformat()} {future_hour:02d}:00:00", tz="America/New_York"))
        eps.insert(0, float("nan"))                # unreported -> unconfirmed

        data = {"Reported EPS": eps} if with_eps_column else {}
        df = pd.DataFrame(data, index=pd.DatetimeIndex(rows))

        stock = MagicMock()
        stock.calendar = {"Earnings Date": [future_date]}
        stock.earnings_dates = df
        return stock

    def _timing(self, stock, ticker="TEST"):
        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            result = fetcher.get_next_earnings_date(ticker)
        assert result.is_ok, result
        return result.value[1].value

    def test_placeholder_hour_is_corrected_to_amc(self):
        """15:00 future row, every confirmed report at 16:00 — the ADBE/ORCL/CRM
        shape. Must read AMC, not DMH."""
        assert self._timing(self._stock(15, [16, 16, 16, 16]), "ADBE") == "AMC"

    def test_genuine_dmh_reporter_is_left_alone(self):
        """The correction must not manufacture AMC for a ticker that really does
        report during market hours — its own confirmed history shows DMH too.
        Without this the test above would pass for the wrong reason."""
        assert self._timing(self._stock(12, [12, 12, 12, 12]), "MIDDAY") == "DMH"

    def test_mixed_confirmed_history_is_left_alone(self):
        """`all()`, not `any()`: one non-AMC confirmed report is enough to make
        the placeholder inference unsafe."""
        assert self._timing(self._stock(15, [16, 16, 12, 16]), "MIXED") == "DMH"

    def test_amc_and_bmo_are_untouched(self):
        """The correction fires only on DMH. A real 16:00 AMC and a real 07:00
        BMO must pass through unchanged — a BMO placeholder shifts the same way
        but stays under the 09:30 threshold, so it can never be produced
        spuriously."""
        assert self._timing(self._stock(16, [16, 16, 16, 16]), "AMCTKR") == "AMC"
        assert self._timing(self._stock(7, [7, 7, 7, 7]), "BMOTKR") == "BMO"

    def test_missing_reported_eps_column_does_not_lose_timing(self):
        """The frame is whatever yfinance returns. A KeyError here would be
        swallowed by the enclosing `except` and silently degrade timing to
        UNKNOWN — worse than the spurious DMH being corrected."""
        assert self._timing(self._stock(15, [16, 16], with_eps_column=False)) == "DMH"

    def test_no_confirmed_history_is_left_alone(self):
        """A newly listed ticker has no confirmed reports to infer from."""
        assert self._timing(self._stock(15, [])) == "DMH"


class TestPlaceholderCorrectionAppliesToBothPaths:
    """The correction must live at BOTH classification sites, not one.

    It was first added only to get_next_earnings_date. get_earnings_date_near
    has the identical hour->timing logic and needs it MORE, not less: that
    method searches past AND future by design, so its nearest match can be an
    unconfirmed future row carrying a placeholder hour — and
    cleanup_duplicate_earnings() calls it to decide whether a stale duplicate
    row may be deleted (CLAUDE.md, Aug 6 2026).
    """

    @staticmethod
    def _frame(rows):
        """rows: list of (days_offset, hour, reported) -> DataFrame."""
        import pandas as pd

        idx, eps = [], []
        for offset, hour, reported in rows:
            d = date.today() + timedelta(days=offset)
            idx.append(pd.Timestamp(f"{d.isoformat()} {hour:02d}:00:00",
                                    tz="America/New_York"))
            eps.append(1.23 if reported else float("nan"))
        return pd.DataFrame({"Reported EPS": eps}, index=pd.DatetimeIndex(idx))

    def _near(self, df, ticker="TEST"):
        stock = MagicMock()
        stock.earnings_dates = df
        fetcher = YahooFinanceEarnings()
        with patch(
            "src.infrastructure.data_sources.yahoo_finance_earnings.yf.Ticker",
            return_value=stock,
        ):
            ref = date.today() + timedelta(days=45)
            result = fetcher.get_earnings_date_near(ticker, [ref])
        assert result.is_ok, result
        return result.value[1].value

    def test_get_earnings_date_near_corrects_the_placeholder(self):
        """The gap this closes: this path had no correction at all."""
        df = self._frame([
            (45, 15, False),     # unconfirmed future — the placeholder
            (-45, 16, True), (-135, 16, True), (-225, 16, True), (-315, 16, True),
        ])
        assert self._near(df, "ADBE") == "AMC"

    def test_get_earnings_date_near_leaves_a_genuine_dmh_alone(self):
        df = self._frame([
            (45, 12, False),
            (-45, 12, True), (-135, 12, True), (-225, 12, True), (-315, 12, True),
        ])
        assert self._near(df, "MIDDAY") == "DMH"


class TestPlaceholderCorrectionSortOrder:
    """The correction reads the ticker's four MOST RECENT confirmed reports.

    yfinance happens to return earnings_dates newest-first (verified
    is_monotonic_decreasing on live ADBE/ORCL data), but that is undocumented.
    An unsorted head would read a ticker's OLDEST quarters instead — ADBE's go
    back to a 2020 BMO at 08:00 — and answer a different question entirely.
    """

    @staticmethod
    def _oldest_first_frame():
        """Same rows as the ADBE shape, but ASCENDING: a company that used to
        report BMO years ago and has reported AMC every quarter since. Read
        oldest-first, the head is all-BMO and the correction would not fire."""
        import pandas as pd

        rows = [
            (-720, 8, True), (-630, 8, True),      # ancient BMO era
            (-315, 16, True), (-225, 16, True),
            (-135, 16, True), (-45, 16, True),     # recent, all AMC
            (45, 15, False),                        # the placeholder
        ]
        idx, eps = [], []
        for offset, hour, reported in rows:
            d = date.today() + timedelta(days=offset)
            idx.append(pd.Timestamp(f"{d.isoformat()} {hour:02d}:00:00",
                                    tz="America/New_York"))
            eps.append(1.23 if reported else float("nan"))
        return pd.DataFrame({"Reported EPS": eps}, index=pd.DatetimeIndex(idx))

    def test_correction_does_not_depend_on_yfinance_row_order(self):
        """Ascending input must give the same answer as descending. Without the
        explicit sort this reads the 2020 BMO rows and returns DMH."""
        from src.domain.types import EarningsTiming
        from src.infrastructure.data_sources.yahoo_finance_earnings import (
            correct_placeholder_dmh,
        )

        ascending = self._oldest_first_frame()
        descending = ascending.sort_index(ascending=False)

        for name, df in (("ascending", ascending), ("descending", descending)):
            got = correct_placeholder_dmh(df, EarningsTiming.DMH, "SORTED")
            assert got == EarningsTiming.AMC, f"{name} order gave {got}"
