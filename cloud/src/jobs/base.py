"""
Base class for job handlers with shared infrastructure.

Extracts common patterns from individual job handlers:
- Earnings fetch with empty-response handling
- Date filtering (upcoming days or today-only)
- Tracked ticker filtering via historical_moves whitelist
- Historical move percentage extraction
- Full VRP evaluation pipeline (historical + implied move + VRP calc)
- Rate limiting between API calls
- Timing/metrics recording
- Result building with optional error fields
"""

import asyncio
from typing import Dict, Any, List, Optional, Tuple

from datetime import timedelta

from src.core.config import settings, now_et, today_et
from src.core.logging import log
from src.core import metrics
from src.domain import (
    calculate_vrp,
    HistoricalMovesRepository,
)
from src.domain.implied_move import (
    fetch_real_implied_move,
    get_implied_move_with_fallback,
)


# Configuration constants for job limits
# These can be overridden via environment variables if needed
MAX_PRE_MARKET_TICKERS = 30  # Max tickers to evaluate in pre-market prep
MAX_PRIME_CANDIDATES = 40  # Max candidates to consider for priming
MAX_PRIME_CALLS = 15  # Max Perplexity API calls during prime
MAX_DIGEST_CANDIDATES = 40  # Max candidates to consider for digest
MAX_BACKFILL_TICKERS = 60  # Max tickers to backfill in weekly job
MAX_OUTCOME_TICKERS = 30  # Max tickers to record outcomes for same-day earnings
RATE_LIMIT_DELAY = 0.5  # Seconds between API calls
RATE_LIMIT_BATCH_SIZE = 5  # API calls before adding delay

# Alert thresholds
PRE_MARKET_ALERT_THRESHOLD = 0.5  # Alert if pre-market move > 50% of historical avg
AFTER_HOURS_ALERT_THRESHOLD = 1.0  # Only track after-hours moves > 1%

# API calls per ticker when fetching real implied move (quote + expirations + chain)
TRADIER_CALLS_PER_TICKER = 3


def filter_to_tracked_tickers(
    earnings: List[Dict[str, Any]],
    tracked_tickers: set
) -> List[Dict[str, Any]]:
    """
    Filter earnings list to only include tickers with historical moves data.

    This is the most reliable way to filter out OTC/foreign stocks because:
    1. If a ticker is in historical_moves, we have VRP data for it
    2. These are tickers we've explicitly tracked and can analyze
    3. No false positives (legitimate tickers won't be filtered)
    4. No false negatives (untraceable tickers won't slip through)

    Args:
        earnings: List of earnings dicts from Alpha Vantage (with 'symbol' key)
        tracked_tickers: Set of ticker symbols from historical_moves table

    Returns:
        Filtered list containing only tracked tickers
    """
    return [e for e in earnings if e["symbol"] in tracked_tickers]


class BaseJobHandler:
    """
    Shared infrastructure for scheduled job handlers.

    Provides reusable methods for the common patterns found across
    all 12 job handlers: earnings fetching, date filtering, VRP
    evaluation, rate limiting, timing, and result building.

    JobRunner inherits from this class to gain access to these
    helpers while keeping its existing lazy client properties and
    dispatch mechanism intact.
    """

    # ------------------------------------------------------------------ #
    #  Timing / Metrics
    # ------------------------------------------------------------------ #

    @staticmethod
    def _start_timer() -> float:
        """Start a high-resolution timer for job duration measurement."""
        return asyncio.get_event_loop().time()

    @staticmethod
    def _record_duration(start_time: float, job_name: str) -> float:
        """
        Record job duration metric and return duration_ms.

        Args:
            start_time: Value from _start_timer()
            job_name: Metric tag (e.g. "pre_market_prep")

        Returns:
            Duration in milliseconds (rounded)
        """
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": job_name})
        return round(duration_ms)

    # ------------------------------------------------------------------ #
    #  Earnings Pipeline
    # ------------------------------------------------------------------ #

    async def _fetch_earnings(
        self, job_name: str, horizon: Optional[str] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch earnings calendar from Alpha Vantage.

        Logs a warning and records a metric if the API returns empty.
        Returns None on empty so callers can return an early-exit result.

        Args:
            job_name: For logging and metrics tags
            horizon: Optional horizon parameter (e.g. "3month")

        Returns:
            List of earnings dicts, or None if empty/unavailable
        """
        if horizon:
            earnings = await self.alphavantage.get_earnings_calendar(horizon=horizon)
        else:
            earnings = await self.alphavantage.get_earnings_calendar()

        if not earnings:
            log("warn", "Empty earnings calendar from Alpha Vantage", job=job_name)
            metrics.count(
                "ivcrush.job.api_empty", {"job": job_name, "api": "alphavantage"}
            )
            return None
        return earnings

    @staticmethod
    def _upcoming_earnings(
        earnings: List[Dict[str, Any]], days: int = 4
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Filter earnings to the next N days (including today).

        Args:
            earnings: Full earnings list from Alpha Vantage
            days: Number of days to include (default 4: today + 3)

        Returns:
            (filtered_earnings, target_dates)
        """
        today = today_et()
        target_dates = [today]
        for i in range(1, days):
            future = (now_et() + timedelta(days=i)).strftime("%Y-%m-%d")
            target_dates.append(future)

        upcoming = [e for e in earnings if e["report_date"] in target_dates]
        return upcoming, target_dates

    @staticmethod
    def _todays_earnings(
        earnings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Filter earnings to today only."""
        today = today_et()
        return [e for e in earnings if e["report_date"] == today]

    @staticmethod
    def _filter_tracked(
        earnings: List[Dict[str, Any]], repo: Optional[HistoricalMovesRepository] = None
    ) -> Tuple[List[Dict[str, Any]], HistoricalMovesRepository]:
        """
        Filter earnings to tickers present in historical_moves.

        Creates a new HistoricalMovesRepository if one is not provided.

        Args:
            earnings: Earnings list to filter
            repo: Optional pre-existing repository instance

        Returns:
            (filtered_earnings, repo) - repo is returned so callers can reuse it
        """
        if repo is None:
            repo = HistoricalMovesRepository(settings.DB_PATH)
        tracked = repo.get_tracked_tickers()
        return filter_to_tracked_tickers(earnings, tracked), repo

    # ------------------------------------------------------------------ #
    #  Historical Data
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_historical_pcts(
        repo: HistoricalMovesRepository,
        ticker: str,
        min_moves: int = 4,
    ) -> Tuple[Optional[List[float]], Optional[float]]:
        """
        Extract historical move percentages for a ticker.

        Args:
            repo: HistoricalMovesRepository instance
            ticker: Stock symbol
            min_moves: Minimum number of historical moves required

        Returns:
            (historical_pcts, historical_avg) or (None, None) if insufficient data
        """
        moves = repo.get_moves(ticker)
        if len(moves) < min_moves:
            return None, None

        pcts = [
            abs(m["intraday_move_pct"])
            for m in moves
            if m.get("intraday_move_pct")
        ]
        if not pcts:
            return None, None

        return pcts, sum(pcts) / len(pcts)

    # ------------------------------------------------------------------ #
    #  VRP Evaluation Pipeline
    # ------------------------------------------------------------------ #

    async def _evaluate_vrp(
        self,
        repo: HistoricalMovesRepository,
        ticker: str,
        earnings_date: str,
        api_calls: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Full VRP evaluation for a single ticker.

        Combines historical move lookup, real implied move fetch from Tradier,
        fallback logic, and VRP calculation into one call.

        Args:
            repo: HistoricalMovesRepository instance
            ticker: Stock symbol
            earnings_date: Earnings date string (YYYY-MM-DD)
            api_calls: Current API call counter (for rate limiting)

        Returns:
            Dict with keys: vrp_data, im_result, implied_move_pct, used_real,
            historical_pcts, historical_avg, api_calls (updated counter).
            Returns None if ticker should be skipped (insufficient data).
        """
        pcts, avg = self._get_historical_pcts(repo, ticker)
        if pcts is None:
            return None

        # Rate limiting for Tradier API calls (3 calls per ticker)
        api_calls += TRADIER_CALLS_PER_TICKER
        if api_calls % (RATE_LIMIT_BATCH_SIZE * TRADIER_CALLS_PER_TICKER) == 0:
            await asyncio.sleep(RATE_LIMIT_DELAY)

        # Fetch real implied move from Tradier options chain
        im_result = await fetch_real_implied_move(
            self.tradier, ticker, earnings_date
        )
        implied_move_pct, used_real = get_implied_move_with_fallback(im_result, avg)

        vrp_data = calculate_vrp(
            implied_move_pct=implied_move_pct,
            historical_moves=pcts,
        )

        return {
            "vrp_data": vrp_data,
            "im_result": im_result,
            "implied_move_pct": implied_move_pct,
            "used_real": used_real,
            "historical_pcts": pcts,
            "historical_avg": avg,
            "api_calls": api_calls,
        }

    # ------------------------------------------------------------------ #
    #  Rate Limiting
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _rate_limit_tick(
        api_calls: int,
        batch_size: int = RATE_LIMIT_BATCH_SIZE,
        delay: float = RATE_LIMIT_DELAY,
    ) -> None:
        """
        Apply rate-limiting delay if the batch threshold is reached.

        Args:
            api_calls: Current count of API calls made
            batch_size: Number of calls per batch before sleeping
            delay: Seconds to sleep between batches
        """
        if api_calls > 0 and api_calls % batch_size == 0:
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------ #
    #  Result Building
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_result(
        failed_tickers: Optional[List[str]] = None,
        telegram_error: Optional[str] = None,
        job_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Build a result dict with status="success" and optional error fields.

        Automatically records failed_tickers gauge metric if job_name is provided.

        Args:
            failed_tickers: List of ticker symbols that failed processing
            telegram_error: Error string from Telegram send attempt
            job_name: For metrics recording (optional)
            **kwargs: Additional key-value pairs for the result dict

        Returns:
            Result dict with status and all provided fields
        """
        result: Dict[str, Any] = {"status": "success", **kwargs}

        if telegram_error:
            result["telegram_error"] = telegram_error

        if failed_tickers:
            result["failed_tickers"] = failed_tickers
            if job_name:
                metrics.gauge(
                    "ivcrush.job.tickers_failed",
                    len(failed_tickers),
                    {"job": job_name},
                )

        return result
