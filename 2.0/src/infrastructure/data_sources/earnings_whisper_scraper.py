"""
Earnings Whisper scraper for most anticipated earnings.

Primary: earningswhispers.com /api/calweekview endpoint
Fallback: database (historical_moves-joined earnings_calendar)

There used to be a screenshot-OCR path between those two. It was removed in
Aug 2026: the URL-downloading half had no callers at all, and the local-file
half was reachable only via a `scripts/scan.py --fallback-image` flag that
trade.sh never passed and no skill referenced, so /whisper could not reach it.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from enum import Enum

import requests
from dotenv import load_dotenv

from src.domain.errors import Result, AppError, ErrorCode

load_dotenv()
logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """Simple circuit breaker for external service calls."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: int = 60,
        success_threshold: int = 2
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Circuit breaker name for logging
            failure_threshold: Consecutive failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            success_threshold: Consecutive successes in half-open to close circuit
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None

    def call(self, func, *args, **kwargs):
        """
        Execute function with circuit breaker protection.

        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function

        Returns:
            Function result

        Raises:
            Exception: If circuit is open or function fails
        """
        # Check if circuit should move to half-open
        if self.state == CircuitState.OPEN:
            if self.last_failure_time:
                time_since_failure = datetime.now().timestamp() - self.last_failure_time
                if time_since_failure >= self.recovery_timeout:
                    logger.debug(f"Circuit {self.name}: OPEN -> HALF_OPEN (recovery attempt)")
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                else:
                    raise Exception(f"Circuit breaker {self.name} is OPEN (failing fast)")

        # Execute function
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                logger.info(f"Circuit {self.name}: HALF_OPEN -> CLOSED (recovered)")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0

    def _on_failure(self):
        """Handle failed call."""
        self.last_failure_time = datetime.now().timestamp()

        if self.state == CircuitState.HALF_OPEN:
            logger.warning(f"Circuit {self.name}: HALF_OPEN -> OPEN (recovery failed)")
            self.state = CircuitState.OPEN
            self.failure_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                logger.warning(f"Circuit {self.name}: CLOSED -> OPEN (threshold reached: {self.failure_count})")
                self.state = CircuitState.OPEN

    def is_open(self) -> bool:
        """Check if circuit is currently open."""
        return self.state == CircuitState.OPEN


def get_week_monday(target_date: Optional[datetime] = None) -> datetime:
    """Get Monday of the week for given date."""
    if target_date is None:
        target_date = datetime.now()

    days_since_monday = target_date.weekday()
    monday = target_date - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


class EarningsWhisperScraper:
    """Scraper for most anticipated earnings from earningswhispers.com."""

    # earningswhispers.com configuration
    # The public /calendar page renders its ticker list client-side via JS (jQuery
    # $.getJSON against /api/calweekview) — a plain HTTP GET of the page never
    # contains ticker data. We call the same internal API the page's own JS uses.
    # This is an undocumented endpoint (no public API contract) — keep call volume
    # low (cached ~1x/week via get_shared_cache) to stay well under any rate limiting.
    EARNINGSWHISPERS_URL = "https://www.earningswhispers.com/calendar"
    EARNINGSWHISPERS_WEEKVIEW_URL = "https://www.earningswhispers.com/api/calweekview/{date}/all"
    EARNINGSWHISPERS_TIMEOUT = 15  # seconds

    def __init__(self, cache=None):
        """
        Initialize earningswhispers.com scraper and circuit breaker.

        Args:
            cache: Optional cache instance for caching whisper results by week
        """
        self.ew_available = True
        logger.debug("EarningsWhispers scraper ready")

        # Week-specific whisper results cache (injected dependency)
        self._cache = cache

        # Circuit breaker for the external dependency
        self._ew_breaker = CircuitBreaker(
            name="EarningsWhispers",
            failure_threshold=3,
            recovery_timeout=120,  # 2 minutes
            success_threshold=2
        )

    def get_most_anticipated_earnings(
        self,
        week_monday: Optional[str] = None
    ) -> Result[Tuple[List[str], datetime], AppError]:
        """
        Get most anticipated earnings tickers.

        Args:
            week_monday: Monday in YYYY-MM-DD format (defaults to upcoming week)

        Returns:
            Result with tuple of (ticker list, week Monday datetime) or error

        Note:
            @eWhispers typically posts about the UPCOMING week's earnings,
            so when no date is specified, we try next week first, then current week.
            Returns the actual week that tickers were found for.
        """
        if week_monday:
            try:
                target_date = datetime.strptime(week_monday, "%Y-%m-%d")
            except ValueError:
                return Result.Err(AppError(
                    ErrorCode.INVALID,
                    f"Invalid date format: {week_monday}. Use YYYY-MM-DD"
                ))
            monday = get_week_monday(target_date)
            weeks_to_try = [monday]
        else:
            # No date specified - try next week first, then current week
            # @eWhispers posts about upcoming weeks, not past ones
            current_monday = get_week_monday(datetime.now())
            next_monday = current_monday + timedelta(days=7)
            weeks_to_try = [next_monday, current_monday]
            logger.info(f"No date specified, trying next week ({next_monday.strftime('%Y-%m-%d')}) then current week")

        # Try each week in order
        last_error = None
        for monday in weeks_to_try:
            result = self._try_fetch_week(monday)
            if result.is_ok:
                # Return tickers AND the week they're for
                return Result.Ok((result.value, monday))
            last_error = result.error
            logger.debug(f"Week {monday.strftime('%Y-%m-%d')} not found, trying next...")

        return Result.Err(last_error or AppError(
            ErrorCode.EXTERNAL,
            "All weeks and methods failed"
        ))

    def _try_fetch_week(
        self,
        monday: datetime
    ) -> Result[List[str], AppError]:
        """Try to fetch earnings for a specific week."""
        monday_str = monday.strftime('%Y-%m-%d')
        week_end = monday + timedelta(days=6)

        # Check cache first (week-specific key)
        if self._cache:
            cache_key = f"whisper_tickers:{monday_str}"
            cached_tickers = self._cache.get(cache_key)
            if cached_tickers is not None:
                logger.info(f"✓ Cache HIT for week {monday_str} ({len(cached_tickers)} tickers)")
                return Result.Ok(cached_tickers)
            logger.debug(f"Cache MISS for week {monday_str}")

        logger.info(f"Fetching earnings for week of {monday_str}")

        # Try earningswhispers.com first
        if self.ew_available and not self._ew_breaker.is_open():
            result = self._fetch_from_earningswhispers(monday)
            if result.is_ok:
                tickers = result.value
                logger.info(f"✓ Retrieved {len(tickers)} tickers from EarningsWhispers")
                self._ew_breaker._on_success()

                # Cache the result (week-specific)
                if self._cache:
                    cache_key = f"whisper_tickers:{monday_str}"
                    self._cache.set(cache_key, tickers)
                    logger.debug(f"Cached whisper tickers for week {monday_str}")

                return result

            logger.warning(f"EarningsWhispers fetch failed: {result.error}")
            # Network errors trip the breaker; NODATA (page loaded but no tickers) does not
            if result.error.code == ErrorCode.EXTERNAL:
                self._ew_breaker._on_failure()

        # Final fallback: Use database earnings calendar
        logger.info(f"Using database fallback for week {monday_str}")
        result = self._fetch_from_database(monday, week_end)
        if result.is_ok:
            tickers = result.value
            logger.info(f"✓ Retrieved {len(tickers)} tickers from database")

            # Cache the result (week-specific, shorter TTL for DB fallback)
            if self._cache:
                cache_key = f"whisper_tickers:{monday_str}"
                self._cache.set(cache_key, tickers)
                logger.debug(f"Cached database tickers for week {monday_str}")

            return result

        return Result.Err(AppError(
            ErrorCode.EXTERNAL,
            "All methods failed (earningswhispers.com, database)",
            context={"week_monday": monday.strftime("%Y-%m-%d")}
        ))

    def _fetch_from_earningswhispers(self, week_monday: datetime) -> Result[List[str], AppError]:
        """Fetch most anticipated earnings from earningswhispers.com.

        Calls the site's internal calweekview API directly (the same endpoint its
        own front-end JS calls via $.getJSON) since the static page HTML never
        contains ticker data — it's injected client-side after load. The API
        requires a session cookie (ASP.NET antiforgery token) that's only set by
        first loading the /calendar page — without it the API silently returns an
        empty 200. We reproduce the same two-request flow a real browser makes:
        load the page once to establish the session, then call the API with it.
        """
        date_str = week_monday.strftime("%Y%m%d")
        monday_str = week_monday.strftime("%Y-%m-%d")
        logger.info(f"Fetching most anticipated tickers from earningswhispers.com for {monday_str}")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        try:
            with requests.Session() as session:
                session.headers.update(headers)

                page_url = f"{self.EARNINGSWHISPERS_URL}?date={monday_str}"
                page_response = session.get(page_url, timeout=self.EARNINGSWHISPERS_TIMEOUT)
                page_response.raise_for_status()

                api_url = self.EARNINGSWHISPERS_WEEKVIEW_URL.format(date=date_str)
                response = session.get(
                    api_url,
                    headers={
                        "Accept": "text/plain, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": page_url,
                    },
                    timeout=self.EARNINGSWHISPERS_TIMEOUT,
                )
                response.raise_for_status()

            if not response.text.strip():
                logger.warning("Empty response from earningswhispers.com calweekview API")
                return Result.Err(AppError(
                    ErrorCode.NODATA,
                    "Empty response from earningswhispers.com — possibly rate limited or week not yet published"
                ))

            tickers = self._parse_earningswhispers_weekview(response.text)
            if not tickers:
                logger.warning("No tickers found in earningswhispers.com calweekview response")
                return Result.Err(AppError(
                    ErrorCode.NODATA,
                    "No tickers found on earningswhispers.com calweekview response"
                ))

            logger.info(f"EarningsWhispers: extracted {len(tickers)} tickers")
            return Result.Ok(tickers)

        except requests.RequestException as e:
            logger.warning(f"EarningsWhispers request failed: {e}")
            return Result.Err(AppError(ErrorCode.EXTERNAL, f"EarningsWhispers fetch error: {e}"))

    def _parse_earningswhispers_weekview(self, html_fragment: str) -> List[str]:
        """Extract ticker symbols from the calweekview HTML fragment.

        The fragment marks each day's "most anticipated" companies with a
        background-image URL like /api/verticallogos/AVNT,SOTK,HELE, — tickers
        without a logo (i.e. not anticipated) appear only in the fuller
        /api/caldata/{date} feed, which this deliberately does not use.

        No word-list filtering is applied. Every value in a
        /api/verticallogos/ path segment is already a real ticker symbol, so
        there is no word-shaped noise to screen. An EXCLUDED_WORDS list used
        to be applied here, inherited from a since-removed OCR path; because
        plenty of real symbols are ordinary words it was silently dropping
        genuine reporters — NOW (ServiceNow), ALL (Allstate), ARE (Alexandria
        Real Estate), FOUR (Shift4), HAS (Hasbro), WELL (Welltower), JACK,
        TEN, SHOE. 33 of its 142 entries appear as real tickers in this
        project's own database.
        """
        tickers = []
        for match in re.finditer(r'/api/verticallogos/([A-Z,]+)', html_fragment):
            for ticker in match.group(1).split(','):
                if ticker and ticker not in tickers:
                    tickers.append(ticker)
        return tickers

    def _fetch_from_database(
        self,
        monday: datetime,
        week_end: datetime
    ) -> Result[List[str], AppError]:
        """Fallback: Fetch earnings from local database for the week.

        This provides tickers with earnings during the target week, though without
        the "most anticipated" filtering that Twitter provides.
        """
        try:
            import sqlite3
            from pathlib import Path

            # Find database path
            db_path = Path(__file__).parent.parent.parent.parent / "data" / "ivcrush.db"
            if not db_path.exists():
                return Result.Err(AppError(
                    ErrorCode.NODATA,
                    f"Database not found: {db_path}"
                ))

            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Get tickers with earnings in the target week
            # Order by historical move count (proxy for popularity/importance)
            # INNER JOIN (not LEFT) is deliberate: a ticker only has historical_moves
            # rows if it has previously been tracked as a real, optionable US-listed
            # stock. This excludes OTC/pink-sheet/foreign tickers that happen to have
            # an earnings_calendar row but no options market (e.g. FRCOF, RHUHF,
            # SVNDY) — those passed through with a LEFT JOIN and produced a "most
            # anticipated" list full of untradeable names (Jul 2026).
            query = """
                SELECT ec.ticker, COUNT(*) as move_count
                FROM earnings_calendar ec
                INNER JOIN historical_moves hm ON ec.ticker = hm.ticker
                WHERE ec.earnings_date BETWEEN ? AND ?
                GROUP BY ec.ticker
                ORDER BY move_count DESC
                LIMIT 30
            """

            cursor.execute(query, (
                monday.strftime('%Y-%m-%d'),
                week_end.strftime('%Y-%m-%d')
            ))

            tickers = [row[0] for row in cursor.fetchall()]
            conn.close()

            if not tickers:
                return Result.Err(AppError(
                    ErrorCode.NODATA,
                    f"No earnings found in database for week {monday.strftime('%Y-%m-%d')}"
                ))

            logger.info(f"Database fallback: Found {len(tickers)} tickers with earnings")
            return Result.Ok(tickers)

        except Exception as e:
            logger.error(f"Database fallback failed: {e}")
            return Result.Err(AppError(ErrorCode.EXTERNAL, f"Database error: {e}"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    scraper = EarningsWhisperScraper()
    result = scraper.get_most_anticipated_earnings()

    if result.is_ok:
        print(f"\n✅ Found {len(result.value)} tickers: {', '.join(result.value)}")
    else:
        print(f"\n❌ Error: {result.error}")
