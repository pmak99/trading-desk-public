"""
Earnings Whisper scraper for most anticipated earnings.

Primary: earningswhispers.com direct HTTP scraping
Fallback: Image file with OCR parsing, then database
"""

import logging
import re
import hashlib
import tempfile
import threading
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from pathlib import Path
from enum import Enum

import requests
from dotenv import load_dotenv

from src.domain.errors import Result, AppError, ErrorCode

# Optional OCR dependencies (lazy import for fallback mode only)
try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

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

    # Image download configuration
    MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
    IMAGE_DOWNLOAD_TIMEOUT = 30  # seconds
    IMAGE_CHUNK_SIZE = 8192  # bytes

    # OCR configuration
    MIN_TICKER_LENGTH = 2  # Minimum characters for valid ticker
    OCR_CACHE_TTL_SECONDS = 604800  # 7 days (weekly earnings posts)

    # Pre-compiled regex patterns for better performance
    _TICKER_PATTERN = re.compile(r'\b([A-Z]{1,5})\b')  # 1-5 uppercase letters (standard ticker format)

    # Words to exclude from OCR ticker extraction (common false positives)
    EXCLUDED_WORDS = {
        'THE', 'AND', 'FOR', 'ARE', 'NOT', 'YOU', 'ALL', 'CAN',
        'BUT', 'HAD', 'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'DAY',
        'GET', 'HAS', 'HIM', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW',
        'NOW', 'OLD', 'SEE', 'TWO', 'WAY', 'WHO', 'BOY', 'DID',
        'ITS', 'LET', 'PUT', 'SAY', 'SHE', 'TOO', 'USE', 'BMO',
        'AMC', 'DMH', 'EST', 'PST', 'EDT', 'PDT', 'MOST', 'ANTICIPATED',
        # Additional OCR false positives from company names
        'LOWES', 'HERES', 'DRESS', 'LESS', 'MINI', 'MOOG', 'SHOE',
        'JACK', 'TEN', 'THAT', 'WITH', 'FROM', 'HAVE', 'THIS',
        'WILL', 'YOUR', 'MORE', 'BEEN', 'THAN', 'SOME', 'TIME',
        'VERY', 'WHEN', 'COME', 'HERE', 'JUST', 'LIKE', 'LONG',
        'MAKE', 'MANY', 'OVER', 'SUCH', 'TAKE', 'THEM', 'WELL',
        'ONLY', 'BACK', 'GOOD', 'HIGH', 'LIFE', 'MUCH', 'DOWN',
        'BOTH', 'EACH', 'FIND', 'FOUR', 'GIVE', 'HAND', 'KEEP',
        'LAST', 'LATE', 'LOOK', 'MOST', 'MOVE', 'NEXT', 'OPEN',
        'PART', 'PLAY', 'REAL', 'SAME', 'SEEM', 'SHOW', 'SIDE',
        'STILL', 'TELL', 'THING', 'TURN', 'WEEK', 'WHAT', 'WORK',
        'YEAR', 'AREA', 'BEST', 'CASE', 'EVEN', 'FACT', 'FEEL',
        'FORM', 'HAND', 'IDEA', 'KIND', 'KNOW', 'LATE', 'LESS',
        'MEAN', 'NAME', 'NEED', 'ONCE', 'POINT', 'RIGHT', 'ROOM',
        'SEEM', 'TELL', 'THESE', 'THOSE', 'UNDER', 'UNTIL', 'WHILE',
        'WORLD', 'WOULD', 'WRITE'
    }

    def __init__(self, cache=None):
        """
        Initialize earningswhispers.com scraper, OCR cache, and circuit breakers.

        Args:
            cache: Optional cache instance for caching whisper results by week
        """
        self.ew_available = True
        logger.debug("EarningsWhispers scraper ready")

        # OCR result cache: {url_hash: (tickers, timestamp)}
        # Bounded to prevent memory leaks; protected by lock for thread safety
        self._ocr_cache: Dict[str, Tuple[List[str], float]] = {}
        self._ocr_cache_lock = threading.Lock()
        self._ocr_cache_max_size = 50

        # Week-specific whisper results cache (injected dependency)
        self._cache = cache

        # Circuit breakers for external dependencies
        self._ew_breaker = CircuitBreaker(
            name="EarningsWhispers",
            failure_threshold=3,
            recovery_timeout=120,  # 2 minutes
            success_threshold=2
        )
        self._image_breaker = CircuitBreaker(
            name="ImageDownload",
            failure_threshold=5,
            recovery_timeout=60,  # 1 minute
            success_threshold=2
        )

    def get_most_anticipated_earnings(
        self,
        week_monday: Optional[str] = None,
        fallback_image: Optional[str] = None
    ) -> Result[Tuple[List[str], datetime], AppError]:
        """
        Get most anticipated earnings tickers.

        Args:
            week_monday: Monday in YYYY-MM-DD format (defaults to upcoming week)
            fallback_image: Path to screenshot image (PNG/JPG)

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
            result = self._try_fetch_week(monday, fallback_image)
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
        monday: datetime,
        fallback_image: Optional[str] = None
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

        # Fallback to image if provided
        if fallback_image:
            logger.info(f"Using image fallback: {fallback_image}")
            result = self._parse_image(fallback_image)
            if result.is_ok:
                tickers = result.value
                logger.info(f"✓ Retrieved {len(tickers)} tickers from image")

                # Cache the result (week-specific)
                if self._cache:
                    cache_key = f"whisper_tickers:{monday_str}"
                    self._cache.set(cache_key, tickers)
                    logger.debug(f"Cached whisper tickers for week {monday_str}")

                return result

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
            "All methods failed (earningswhispers.com, image, database)",
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
        """
        tickers = []
        for match in re.finditer(r'/api/verticallogos/([A-Z,]+)', html_fragment):
            for ticker in match.group(1).split(','):
                if ticker and ticker not in self.EXCLUDED_WORDS and ticker not in tickers:
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

    def _download_and_parse_image(self, image_url: str) -> Result[List[str], AppError]:
        """Download image from URL and parse with OCR.

        Args:
            image_url: URL to the image to download and parse

        Returns:
            Result with list of ticker symbols or error
        """
        try:
            # Check if OCR dependencies are available
            if not OCR_AVAILABLE:
                return Result.Err(AppError(
                    ErrorCode.EXTERNAL,
                    "OCR not available. Install: pip install pillow pytesseract && brew install tesseract"
                ))

            # Check cache first (thread-safe)
            url_hash = hashlib.md5(image_url.encode()).hexdigest()
            with self._ocr_cache_lock:
                if url_hash in self._ocr_cache:
                    cached_tickers, cached_time = self._ocr_cache[url_hash]
                    age_seconds = datetime.now().timestamp() - cached_time
                    if age_seconds < self.OCR_CACHE_TTL_SECONDS:
                        logger.debug(f"Cache hit for {image_url[:50]}... (age: {age_seconds:.0f}s)")
                        return Result.Ok(cached_tickers)
                    else:
                        logger.debug(f"Cache expired for {image_url[:50]}... (age: {age_seconds:.0f}s)")
                        del self._ocr_cache[url_hash]

            # Download image with circuit breaker protection
            try:
                content = self._image_breaker.call(
                    self._download_image_impl,
                    image_url
                )
            except Exception as e:
                return Result.Err(AppError(
                    ErrorCode.EXTERNAL,
                    f"Image download failed (circuit breaker): {e}"
                ))

            logger.debug(f"Downloaded {len(content):,} bytes from {image_url}")

            # Create temp file with proper extension
            suffix = '.jpg' if image_url.lower().endswith('.jpg') or image_url.lower().endswith('.jpeg') else '.png'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                tmp_file.write(content)
                tmp_path = tmp_file.name

            try:
                # Parse the downloaded image
                image = Image.open(tmp_path)
                text = pytesseract.image_to_string(image)

                logger.debug(f"OCR extracted {len(text)} characters")
                logger.debug(f"OCR text preview: {text[:200]}...")

                # Extract ticker symbols
                tickers = self._extract_tickers_from_ocr(text)

                if not tickers:
                    return Result.Err(AppError(ErrorCode.NODATA, "No tickers found in image"))

                logger.debug(f"Extracted {len(tickers)} tickers after filtering")

                # Cache the result (thread-safe, bounded)
                with self._ocr_cache_lock:
                    # Evict oldest entries if at capacity
                    while len(self._ocr_cache) >= self._ocr_cache_max_size:
                        oldest_key = min(self._ocr_cache, key=lambda k: self._ocr_cache[k][1])
                        del self._ocr_cache[oldest_key]
                    self._ocr_cache[url_hash] = (tickers, datetime.now().timestamp())
                logger.debug(f"Cached OCR result for {image_url[:50]}...")

                return Result.Ok(tickers)

            finally:
                # Clean up temp file
                Path(tmp_path).unlink(missing_ok=True)

        except requests.RequestException as e:
            return Result.Err(AppError(ErrorCode.EXTERNAL, f"Image download error: {e}"))
        except Exception as e:
            return Result.Err(AppError(ErrorCode.EXTERNAL, f"Image processing error: {e}"))

    def _download_image_impl(self, image_url: str) -> bytes:
        """
        Download image from URL with size validation.

        Args:
            image_url: URL to download

        Returns:
            Image content as bytes

        Raises:
            Exception: If download fails or size exceeds limit
        """
        headers = {
            'User-Agent': 'iv-crush-trading-bot/2.0 (Python/requests)'
        }
        response = requests.get(
            image_url,
            timeout=self.IMAGE_DOWNLOAD_TIMEOUT,
            headers=headers,
            stream=True
        )
        response.raise_for_status()

        # Check content length before downloading
        content_length = response.headers.get('content-length')
        if content_length and int(content_length) > self.MAX_IMAGE_SIZE_BYTES:
            raise Exception(
                f"Image too large: {int(content_length):,} bytes (max: {self.MAX_IMAGE_SIZE_BYTES:,})"
            )

        # Download with size limit enforcement
        content = b''
        for chunk in response.iter_content(chunk_size=self.IMAGE_CHUNK_SIZE):
            content += chunk
            if len(content) > self.MAX_IMAGE_SIZE_BYTES:
                raise Exception(
                    f"Image exceeds size limit during download: {len(content):,} bytes"
                )

        return content

    def _parse_image(self, image_path: str) -> Result[List[str], AppError]:
        """Extract tickers from earnings table image using OCR."""
        try:
            # Check if OCR dependencies are available
            if not OCR_AVAILABLE:
                return Result.Err(AppError(
                    ErrorCode.EXTERNAL,
                    "OCR not available. Install: pip install pillow pytesseract && brew install tesseract"
                ))

            path = Path(image_path)
            if not path.exists():
                return Result.Err(AppError(ErrorCode.NODATA, f"File not found: {image_path}"))

            # Validate image format
            if path.suffix.lower() not in ['.png', '.jpg', '.jpeg']:
                return Result.Err(AppError(
                    ErrorCode.INVALID,
                    f"Unsupported format: {path.suffix}. Use PNG or JPG"
                ))

            # Load and OCR image
            image = Image.open(path)
            text = pytesseract.image_to_string(image)

            # Extract ticker symbols
            tickers = self._extract_tickers_from_ocr(text)

            if not tickers:
                return Result.Err(AppError(ErrorCode.NODATA, "No tickers found in image"))

            return Result.Ok(tickers)

        except Exception as e:
            return Result.Err(AppError(ErrorCode.EXTERNAL, f"Image parsing error: {e}"))

    def _extract_tickers_from_ocr(self, text: str) -> List[str]:
        """Extract valid ticker symbols from OCR text.

        Uses context-aware extraction to find tickers in the "Most Anticipated"
        section and filters out common false positives.

        Args:
            text: OCR-extracted text from earnings image

        Returns:
            List of ticker symbols, deduplicated and order-preserved
        """
        tickers = []

        # Try context-aware extraction first
        # Look for "Most Anticipated" or "WHISPERS" section markers
        lines = text.split('\n')
        in_earnings_section = False
        section_found = False

        for line in lines:
            line_lower = line.lower()

            # Detect earnings section start (various formats)
            if any(marker in line_lower for marker in ['most', 'anticipated', 'whispers', 'earnings']):
                if not in_earnings_section:  # First occurrence
                    in_earnings_section = True
                    section_found = True
                    logger.debug(f"Found earnings section marker in line: {line[:60]}")
                continue

            # Detect section end (other headers like "Upcoming", headers with ##)
            if in_earnings_section and any(header in line_lower for header in ['upcoming', 'other', 'next week']) and len(line.strip()) < 30:
                logger.debug(f"End of earnings section at: {line[:50]}")
                break

            # Extract tickers from earnings section
            if in_earnings_section:
                # Find ALL uppercase words in line (tickers can appear anywhere)
                line_tickers = self._TICKER_PATTERN.findall(line)
                for potential_ticker in line_tickers:
                    if potential_ticker in self.EXCLUDED_WORDS:
                        continue

                    if len(potential_ticker) >= self.MIN_TICKER_LENGTH and len(potential_ticker) <= 5:
                        # Valid ticker length - add directly
                        if potential_ticker not in tickers:
                            tickers.append(potential_ticker)
                            logger.debug(f"Extracted ticker: {potential_ticker} <- {line[:60]}")
                    # Note: Words >5 chars (company names) are skipped - use Twitter for better extraction

        # Fallback: If no "Most Anticipated" section found, use simple extraction
        if not tickers:
            logger.debug("No 'Most Anticipated' section found, using fallback extraction")

            # Extract ticker patterns
            potential_tickers = self._TICKER_PATTERN.findall(text)
            for potential_ticker in potential_tickers:
                if potential_ticker in self.EXCLUDED_WORDS:
                    continue

                if len(potential_ticker) >= self.MIN_TICKER_LENGTH and len(potential_ticker) <= 5:
                    tickers.append(potential_ticker)
                # Note: Words >5 chars (company names) are skipped

        return list(dict.fromkeys(tickers))  # Remove duplicates, preserve order


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    scraper = EarningsWhisperScraper()
    result = scraper.get_most_anticipated_earnings()

    if result.is_ok:
        print(f"\n✅ Found {len(result.value)} tickers: {', '.join(result.value)}")
    else:
        print(f"\n❌ Error: {result.error}")
