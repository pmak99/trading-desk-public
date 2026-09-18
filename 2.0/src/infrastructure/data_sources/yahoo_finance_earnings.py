"""
Yahoo Finance earnings date fetcher.

Fetches upcoming earnings dates from Yahoo Finance as a cross-reference
to Finnhub's calendar, which can sometimes be incorrect.
"""

import logging
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import List, Tuple

from common.timezone import now_et
from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.types import EarningsTiming
from src.infrastructure.api.yahoo import yahoo_call

try:
    import yfinance as yf
except ImportError:
    yf = None

logger = logging.getLogger(__name__)

# Yahoo's own AMC boundary, shared by both classification sites below so they
# cannot drift apart.
_AMC_HOUR = 16


def timing_from_hour(ts) -> EarningsTiming:
    """Classify a timestamp's wall clock into BMO / DMH / AMC."""
    if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30):
        return EarningsTiming.BMO
    if ts.hour >= _AMC_HOUR:
        return EarningsTiming.AMC
    return EarningsTiming.DMH


def correct_placeholder_dmh(earnings_df, timing: EarningsTiming, ticker: str = "") -> EarningsTiming:
    """Upgrade a spurious DMH to AMC when the ticker's own history says so.

    ⚠️ **A DMH read off an UNCONFIRMED row is usually a Yahoo placeholder.**
    Yahoo reports an unconfirmed future date at exactly one hour before the
    ticker's real report time — 15:00 standing in for a confirmed 16:00 AMC —
    which lands inside the DMH band (09:30–16:00). Verified 2026-09-12: ADBE,
    ORCL and CRM each show all four of their last CONFIRMED reports at hour 16
    while the next unconfirmed date reads 15. Not universal (COST reads 16),
    which is why this only ever fires on DMH.

    Why correct it rather than tolerate it: a wrong timing tag makes
    historical_moves measure the WRONG reaction-day window — how AXON's data
    was corrupted (CLAUDE.md, Aug 6 2026) — and it feeds
    calculate_expiration_date, which enforces the min-3-DTE rule.

    Conservative by construction:
      * fires only on DMH. A BMO placeholder shifts the same way but stays
        under the 09:30 threshold, so BMO is never spuriously produced;
      * requires EVERY one of the last four confirmed reports to be AMC, so a
        genuine during-market-hours reporter — whose own history shows DMH —
        is untouched;
      * needs a 'Reported EPS' column to tell confirmed from unconfirmed. The
        frame is whatever yfinance returns, and a KeyError would be swallowed
        by the callers' enclosing `except` and silently degrade timing to
        UNKNOWN, which is worse than the DMH being corrected.

    The explicit sort is not redundant. yfinance happens to return
    earnings_dates newest-first (verified `is_monotonic_decreasing`), but that
    is undocumented; if it ever flipped, an unsorted head would read a
    ticker's OLDEST quarters — ADBE's go back to a 2020 BMO at 08:00 — and
    silently answer a different question.
    """
    if timing != EarningsTiming.DMH:
        return timing
    if earnings_df is None or "Reported EPS" not in earnings_df:
        return timing

    confirmed = earnings_df[earnings_df["Reported EPS"].notna()]
    if len(confirmed) == 0:
        return timing

    recent = confirmed.sort_index(ascending=False).index[:4]
    hours = list(recent.hour)
    if hours and all(h >= _AMC_HOUR for h in hours):
        logger.debug(
            f"{ticker}: hour looks like a Yahoo placeholder — last "
            f"{len(hours)} confirmed reports all AMC; DMH -> AMC"
        )
        return EarningsTiming.AMC
    return timing


class YahooFinanceEarnings:
    """Fetch earnings dates from Yahoo Finance with LRU caching."""

    def __init__(
        self,
        timeout: int = 10,
        cache_ttl_hours: int = 24,
        max_cache_size: int = 1000
    ):
        """
        Initialize Yahoo Finance earnings fetcher.

        Args:
            timeout: Request timeout in seconds
            cache_ttl_hours: Cache time-to-live in hours (default: 24)
            max_cache_size: Maximum number of entries in cache (default: 1000)
        """
        if yf is None:
            raise ImportError("yfinance not installed. Run: pip install yfinance")

        self.timeout = timeout
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.max_cache_size = max_cache_size

        # LRU Cache: OrderedDict maintains insertion order
        # {ticker: (earnings_date, timing, cached_at)}
        self._cache: OrderedDict[str, Tuple[date, EarningsTiming, datetime]] = OrderedDict()

    def get_next_earnings_date(
        self, ticker: str
    ) -> Result[Tuple[date, EarningsTiming], AppError]:
        """
        Get next earnings date for a ticker from Yahoo Finance.

        Uses an in-memory cache to avoid redundant API calls within the TTL window.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Result with (earnings_date, timing) tuple or error
        """
        # Check cache first
        if ticker in self._cache:
            cached_date, cached_timing, cached_at = self._cache[ticker]
            age = now_et() - cached_at
            if age < self.cache_ttl:
                # Cache hit - move to end (most recently used)
                self._cache.move_to_end(ticker)
                logger.debug(
                    f"{ticker}: Using cached Yahoo Finance data (age: {age.seconds//60}min)"
                )
                return Ok((cached_date, cached_timing))
            else:
                # Cache expired - remove stale entry
                del self._cache[ticker]
                logger.debug(f"{ticker}: Cache expired (age: {age.seconds//3600}hrs)")

        try:
            logger.debug(f"Fetching earnings date from Yahoo Finance: {ticker}")

            # Fetch ticker data. Each property below is one Yahoo request, made
            # under the process's Yahoo limit (yahoo_call); both keep nothing on
            # the Ticker from a refused attempt, so a retry can reuse it.
            stock = yf.Ticker(ticker)

            # Get calendar data (includes next earnings date)
            calendar = yahoo_call(lambda: stock.calendar)

            if not calendar or 'Earnings Date' not in calendar:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No earnings date found for {ticker} in Yahoo Finance calendar"
                    )
                )

            # Yahoo Finance returns a list of possible dates
            earnings_dates = calendar['Earnings Date']
            if not earnings_dates or len(earnings_dates) == 0:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"Empty earnings date list for {ticker}"
                    )
                )

            # calendar['Earnings Date'] can lag for days/weeks after a report
            # before Yahoo rolls it forward to the next quarter's estimate —
            # found live 2026-09-10 (AI/LULU/OXM/WDAY all still pointing at
            # their last-reported date, days to weeks in the past). A "next
            # earnings date" call must never hand back a date that's already
            # happened, so only a date >= today is eligible; if none qualify,
            # this is genuinely stale data, not an answer.
            today = now_et().date()
            candidate_dates = [
                d.date() if isinstance(d, datetime) else d for d in earnings_dates
            ]
            future_candidates = [d for d in candidate_dates if d >= today]
            if not future_candidates:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"Yahoo Finance calendar for {ticker} only has stale/past "
                        f"dates: {candidate_dates}"
                    )
                )
            earnings_date = future_candidates[0]

            # Try to get timing from earnings_dates DataFrame (has timestamps).
            # Default is UNKNOWN, not a guessed AMC — callers with a second,
            # definite-timing source (e.g. Finnhub) should prefer that over a
            # blind default rather than have it silently discarded.
            timing = EarningsTiming.UNKNOWN
            try:
                earnings_df = yahoo_call(lambda: stock.earnings_dates)
                if earnings_df is not None and len(earnings_df) > 0:
                    # earnings_df.index is tz-aware (America/New_York); comparing
                    # it directly against a naive datetime.now() raises
                    # TypeError on every call — silently swallowed below, which
                    # meant this block never actually ran and every ticker fell
                    # through to the hardcoded default (found live 2026-09-10:
                    # 9 BMO-reporting tickers — DAL/DDOG/DOCN/FISV/MNDY/SHOP/
                    # SOFI/SPOT/UBER — got silently overwritten to AMC in a
                    # single sync run). Comparing .date() against date.today()
                    # sidesteps the tz mismatch entirely.
                    future_dates = earnings_df[earnings_df.index.date >= today]
                    if len(future_dates) > 0:
                        next_earnings = future_dates.index[0]
                        hour = next_earnings.hour

                        timing = timing_from_hour(next_earnings)
                        timing = correct_placeholder_dmh(
                            earnings_df, timing, ticker
                        )

                        logger.debug(f"{ticker}: Detected timing {timing.value} from hour {hour}")
            except Exception as e:
                logger.debug(f"{ticker}: Could not determine timing from DataFrame: {e}")

            logger.info(
                f"{ticker}: Yahoo Finance earnings date = {earnings_date} ({timing.value})"
            )

            # Update cache with LRU eviction
            self._update_cache(ticker, earnings_date, timing)

            return Ok((earnings_date, timing))

        except Exception as e:
            logger.warning(f"Failed to fetch earnings date from Yahoo Finance for {ticker}: {e}")
            return Err(
                AppError(
                    ErrorCode.EXTERNAL,
                    f"Yahoo Finance error for {ticker}: {str(e)}"
                )
            )

    def get_earnings_date_near(
        self, ticker: str, reference_dates: List[date]
    ) -> Result[Tuple[date, EarningsTiming], AppError]:
        """
        Find this ticker's actual earnings date nearest to any of reference_dates.

        Unlike get_next_earnings_date(), which only ever returns the next
        upcoming date (via stock.calendar, forward-looking from today), this
        searches stock.earnings_dates — a DataFrame that already spans past
        AND future quarters — so it can corroborate a candidate date that has
        already occurred. Without this, a ticker whose disputed dates are all
        at or before today can never be corroborated: "next earnings date"
        has already rolled forward to the following quarter and will never
        match either candidate, no matter how many times the check is retried.

        Args:
            ticker: Stock ticker symbol
            reference_dates: Candidate dates to search near (e.g. the disputed
                entries in an earnings_calendar duplicate cluster)

        Returns:
            Result with (nearest_date, timing) — the actual date/timing found,
            which the caller compares against its own candidates. Not filtered
            to only-exact-matches, so a genuine third-date disagreement is still
            visible to the caller rather than silently collapsed to "no match".
        """
        try:
            stock = yf.Ticker(ticker)
            earnings_df = yahoo_call(lambda: stock.earnings_dates)

            if earnings_df is None or len(earnings_df) == 0:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No earnings_dates history for {ticker}"
                    )
                )

            best_ts = min(
                earnings_df.index,
                key=lambda ts: min(abs((ts.date() - ref).days) for ref in reference_dates),
            )
            best_date = best_ts.date()

            # Same classification and the same placeholder correction as
            # get_next_earnings_date. It matters MORE here, not less: this
            # method searches past AND future by design, so `best_ts` can land
            # on an unconfirmed future row and read its placeholder hour — and
            # cleanup_duplicate_earnings() calls this to decide whether a stale
            # duplicate row may be deleted.
            timing = timing_from_hour(best_ts)
            timing = correct_placeholder_dmh(earnings_df, timing, ticker)

            logger.info(
                f"{ticker}: Yahoo Finance earnings history nearest to "
                f"{reference_dates} = {best_date} ({timing.value})"
            )

            return Ok((best_date, timing))

        except Exception as e:
            logger.warning(f"Failed to search earnings_dates history for {ticker}: {e}")
            return Err(
                AppError(
                    ErrorCode.EXTERNAL,
                    f"Yahoo Finance error for {ticker}: {str(e)}"
                )
            )

    def _update_cache(
        self,
        ticker: str,
        earnings_date: date,
        timing: EarningsTiming
    ) -> None:
        """
        Update cache with LRU eviction policy.

        If cache is full, evicts the least recently used entry.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date to cache
            timing: Earnings timing (BMO/AMC/DMH)
        """
        # Check if cache is full
        if len(self._cache) >= self.max_cache_size:
            # Evict least recently used (first item in OrderedDict)
            evicted_ticker, _ = self._cache.popitem(last=False)
            logger.debug(
                f"Cache full ({self.max_cache_size}): Evicted {evicted_ticker} for {ticker}"
            )

        # Add/update entry (always added to end as most recently used)
        self._cache[ticker] = (earnings_date, timing, now_et())
        logger.debug(f"{ticker}: Cached Yahoo Finance data (cache size: {len(self._cache)})")
