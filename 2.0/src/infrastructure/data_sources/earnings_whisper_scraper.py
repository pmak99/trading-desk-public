"""
Earnings Whisper scraper for most anticipated earnings.

Primary: X/Twitter @eWhispers account (playwright browser automation - no auth required)
Fallback: Image file with OCR parsing
"""

import logging
import re
import os
import hashlib
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from pathlib import Path
from enum import Enum

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import requests
import tempfile
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
    """Scraper for most anticipated earnings using PRAW and OCR."""

    # Search configuration
    FLAIR_SEARCH_LIMIT = 20  # Last ~5 weeks of earnings threads
    FLAIR_SEARCH_TIME_FILTER = 'month'
    TEXT_SEARCH_LIMIT = 10
    TEXT_SEARCH_TIME_FILTER = 'month'

    # Image download configuration
    MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
    IMAGE_DOWNLOAD_TIMEOUT = 30  # seconds
    IMAGE_CHUNK_SIZE = 8192  # bytes

    # OCR configuration
    MIN_TICKER_LENGTH = 2  # Minimum characters for valid ticker
    OCR_CACHE_TTL_SECONDS = 604800  # 7 days (weekly earnings posts)

    # Pre-compiled regex patterns for better performance
    _TICKER_PATTERN = re.compile(r'\b([A-Z]{1,5})\b')  # 1-5 uppercase letters (standard ticker format)
    _DATE_PATTERN = re.compile(r'(\d{1,2})/(\d{1,2})')
    _WRITTEN_DATE_PATTERN = re.compile(
        r'(january|february|march|april|may|june|july|august|'
        r'september|october|november|december)\s+(\d{1,2})',
        re.IGNORECASE
    )

    # Month name to number mapping
    _MONTH_NAMES = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12
    }

    # Twitter scraping configuration
    TWITTER_URL = "https://twitter.com/eWhispers"
    PAGE_LOAD_TIMEOUT_MS = 30000  # 30 seconds
    TWEET_WAIT_TIMEOUT_MS = 10000  # 10 seconds
    MAX_TWEETS_TO_CHECK = 50

    # CSS selectors with fallbacks for robustness
    TWEET_SELECTORS = [
        'article[data-testid="tweet"]',
        'article[role="article"]',  # fallback if data-testid changes
    ]
    TWEET_TEXT_SELECTORS = [
        '[data-testid="tweetText"]',
        '[lang]',  # fallback - tweets usually have lang attribute
    ]

    # Regex patterns for extracting tickers from Reddit markdown
    TICKER_PATTERNS = [
        re.compile(r'\[([A-Z]{1,5})\]\(http'),           # [TICKER](url)
        re.compile(r'\*\*([A-Z]{1,5})\*\*'),             # **TICKER**
        re.compile(r'^[-*]\s+([A-Z]{1,5})\s+[\(\[]')    # - TICKER (date)
    ]

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

    def __init__(self):
        """Initialize Twitter scraper (playwright), OCR cache, and circuit breakers."""
        # Playwright requires no initialization - works directly
        self.twitter_available = True
        logger.debug("Twitter scraper ready (playwright browser automation - no auth required)")

        # Rate limiting for Twitter scraping (avoid triggering anti-bot measures)
        self._last_twitter_request: Optional[float] = None
        self._min_request_interval = 5.0  # minimum seconds between requests

        # OCR result cache: {url_hash: (tickers, timestamp)}
        self._ocr_cache: Dict[str, Tuple[List[str], float]] = {}

        # Circuit breakers for external dependencies
        self._twitter_breaker = CircuitBreaker(
            name="Twitter",
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
    ) -> Result[List[str], AppError]:
        """
        Get most anticipated earnings tickers.

        Args:
            week_monday: Monday in YYYY-MM-DD format (defaults to current week)
            fallback_image: Path to screenshot image (PNG/JPG)

        Returns:
            Result with ticker list or error
        """
        if week_monday:
            try:
                target_date = datetime.strptime(week_monday, "%Y-%m-%d")
            except ValueError:
                return Result.Err(AppError(
                    ErrorCode.INVALID,
                    f"Invalid date format: {week_monday}. Use YYYY-MM-DD"
                ))
        else:
            target_date = datetime.now()

        monday = get_week_monday(target_date)
        logger.info(f"Fetching earnings for week of {monday.strftime('%Y-%m-%d')}")

        # Try Twitter first (with circuit breaker)
        if self.twitter_available and not self._twitter_breaker.is_open():
            try:
                result = self._twitter_breaker.call(self._fetch_from_twitter, monday)
                if result.is_ok:
                    logger.info(f"✓ Retrieved {len(result.value)} tickers from Twitter")
                    return result
                logger.warning("Twitter fetch failed")
            except Exception as e:
                logger.warning(f"Twitter circuit breaker: {e}")

        # Fallback to image
        if fallback_image:
            logger.info(f"Using image fallback: {fallback_image}")
            result = self._parse_image(fallback_image)
            if result.is_ok:
                logger.info(f"✓ Retrieved {len(result.value)} tickers from image")
                return result

        return Result.Err(AppError(
            ErrorCode.EXTERNAL,
            "All methods failed",
            context={"week_monday": monday.strftime("%Y-%m-%d")}
        ))

    def _fetch_from_twitter(self, week_monday: datetime) -> Result[List[str], AppError]:
        """Fetch from @eWhispers Twitter account using playwright browser automation (circuit breaker protected)."""
        # Rate limiting - wait if needed
        if self._last_twitter_request is not None:
            elapsed = time.time() - self._last_twitter_request
            if elapsed < self._min_request_interval:
                wait_time = self._min_request_interval - elapsed
                logger.debug(f"Rate limiting: waiting {wait_time:.1f}s before Twitter request")
                time.sleep(wait_time)

        start_time = time.time()
        monday_str = f"{week_monday.month}/{week_monday.day}"
        logger.info(f"Scraping @eWhispers tweets for week {monday_str}")

        browser = None
        try:
            with sync_playwright() as p:
                try:
                    # Launch headless browser
                    logger.debug("Launching headless Chromium browser")
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page()

                    # Set user agent to avoid bot detection
                    page.set_extra_http_headers({
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
                    })

                    # Navigate to @eWhispers Twitter profile
                    logger.debug(f"Navigating to {self.TWITTER_URL}")
                    try:
                        # Use 'load' instead of 'networkidle' - Twitter keeps making requests
                        response = page.goto(self.TWITTER_URL, wait_until='load', timeout=self.PAGE_LOAD_TIMEOUT_MS)
                        logger.debug(f"Page loaded with status: {response.status}")
                    except PlaywrightTimeout as e:
                        logger.error(f"Timeout navigating to Twitter: {e}")
                        # Debug screenshot only if debug logging enabled
                        if logger.isEnabledFor(logging.DEBUG):
                            screenshot_path = f"/tmp/twitter_timeout_{int(time.time())}.png"
                            page.screenshot(path=screenshot_path)
                            logger.debug(f"Debug screenshot saved to {screenshot_path}")
                        return Result.Err(AppError(ErrorCode.EXTERNAL, f"Navigation timeout: {e}"))

                    # Debug logging only
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"Page title: {page.title()}")
                        logger.debug(f"Current URL: {page.url}")

                    # Wait for tweets to load - try primary selector, fallback to alternative
                    tweet_selector = None
                    for selector in self.TWEET_SELECTORS:
                        try:
                            logger.debug(f"Waiting for tweets with selector: {selector}")
                            page.wait_for_selector(selector, timeout=self.TWEET_WAIT_TIMEOUT_MS)
                            tweet_selector = selector
                            logger.debug(f"Found tweets using selector: {selector}")
                            break
                        except PlaywrightTimeout:
                            logger.debug(f"Selector {selector} timed out, trying next...")
                            continue

                    if not tweet_selector:
                        logger.warning("All tweet selectors failed - page may require authentication")
                        return Result.Err(AppError(
                            ErrorCode.EXTERNAL,
                            "Could not find tweets - page may require authentication"
                        ))

                    # Extract tweet elements
                    tweet_elements = page.query_selector_all(tweet_selector)
                    logger.info(f"Found {len(tweet_elements)} tweets")

                    # Check tweets for matching week
                    for i, tweet_elem in enumerate(tweet_elements):
                        if i >= self.MAX_TWEETS_TO_CHECK:
                            logger.debug(f"Reached max tweet check limit ({self.MAX_TWEETS_TO_CHECK})")
                            break

                        # Extract text - try primary selector, fallback to alternative
                        text_elem = None
                        for text_selector in self.TWEET_TEXT_SELECTORS:
                            text_elem = tweet_elem.query_selector(text_selector)
                            if text_elem:
                                break

                        if not text_elem:
                            logger.debug(f"Tweet {i+1}: No text element found")
                            continue

                        tweet_text = text_elem.inner_text()
                        logger.debug(f"Tweet {i+1}: {tweet_text[:100]}...")

                        # Check if this tweet matches the requested week
                        if self._matches_week_in_tweet(tweet_text, week_monday):
                            logger.info(f"Found matching tweet for week {monday_str}")

                            # Parse tickers from tweet text
                            tickers = self._parse_twitter_text(tweet_text)
                            if tickers:
                                duration = time.time() - start_time
                                logger.info(f"Extracted {len(tickers)} tickers in {duration:.1f}s")
                                return Result.Ok(tickers)

                    # No matching tweet found
                    return Result.Err(AppError(
                        ErrorCode.NODATA,
                        f"No matching tweet found for {week_monday.strftime('%Y-%m-%d')}"
                    ))

                finally:
                    # Ensure browser is always closed
                    if browser:
                        try:
                            browser.close()
                        except Exception as e:
                            logger.debug(f"Error closing browser: {e}")

        except Exception as e:
            logger.error(f"Twitter scraper exception: {type(e).__name__}: {e}", exc_info=True)
            return Result.Err(AppError(ErrorCode.EXTERNAL, f"Twitter scraper error: {e}"))
        finally:
            # Update rate limiting timestamp
            self._last_twitter_request = time.time()

    def _matches_week_in_tweet(self, tweet_text: str, week_monday: datetime) -> bool:
        """Check if tweet text matches the requested week.

        Args:
            tweet_text: Tweet text content
            week_monday: Target Monday datetime to match

        Returns:
            True if tweet matches the week, False otherwise
        """
        # Try written month format first (e.g., "November 17, 2025")
        written_matches = self._WRITTEN_DATE_PATTERN.findall(tweet_text.lower())

        if written_matches:
            month_name, day_str = written_matches[0]
            try:
                tweet_month = self._MONTH_NAMES[month_name.lower()]
                tweet_day = int(day_str)

                matches_date = tweet_month == week_monday.month and tweet_day == week_monday.day
                if matches_date:
                    logger.debug(f"Tweet date {month_name} {tweet_day} matches {week_monday.strftime('%Y-%m-%d')}")
                    return True
            except (ValueError, KeyError) as e:
                logger.debug(f"Failed to parse written date: {e}")

        # Fall back to numeric date pattern (e.g., "11/17")
        matches = self._DATE_PATTERN.findall(tweet_text)

        if not matches:
            return False

        # Get the first date from tweet (should be Monday)
        month_str, day_str = matches[0]

        try:
            tweet_month = int(month_str)
            tweet_day = int(day_str)

            # Validate reasonable date ranges
            if not (1 <= tweet_month <= 12):
                logger.debug(f"Invalid month {tweet_month} in tweet")
                return False
            if not (1 <= tweet_day <= 31):
                logger.debug(f"Invalid day {tweet_day} in tweet")
                return False

            # Match if same month and day
            matches_date = tweet_month == week_monday.month and tweet_day == week_monday.day

            if matches_date:
                logger.debug(f"Tweet date {tweet_month}/{tweet_day} matches {week_monday.strftime('%Y-%m-%d')}")

            return matches_date

        except (ValueError, OverflowError) as e:
            logger.debug(f"Failed to parse date from tweet: {e}")
            return False

    def _matches_week(self, title: str, week_monday: datetime) -> bool:
        """Check if Reddit post title matches the requested week (legacy).

        Args:
            title: Reddit post title (e.g., "Weekly Earnings Thread 11/10 - 11/14")
            week_monday: Target Monday datetime to match

        Returns:
            True if title matches the week, False otherwise
        """
        # Extract date pattern from title: "11/10 - 11/14" or "11/10"
        matches = self._DATE_PATTERN.findall(title)

        if not matches:
            return False

        # Get the first date from title (should be Monday)
        month_str, day_str = matches[0]

        try:
            title_month = int(month_str)
            title_day = int(day_str)

            # Validate reasonable date ranges
            if not (1 <= title_month <= 12):
                logger.debug(f"Invalid month {title_month} in title: {title}")
                return False
            if not (1 <= title_day <= 31):
                logger.debug(f"Invalid day {title_day} in title: {title}")
                return False

            # Match if same month and day
            # Note: Year check not possible from title alone, but acceptable
            # for recent posts (within current year or last 12 months)
            matches_date = title_month == week_monday.month and title_day == week_monday.day

            if matches_date:
                logger.debug(f"Matched: {title} -> {week_monday.strftime('%Y-%m-%d')}")

            return matches_date

        except (ValueError, OverflowError) as e:
            logger.debug(f"Failed to parse date from title '{title}': {e}")
            return False

    def _parse_twitter_text(self, tweet_text: str) -> List[str]:
        """Extract ticker symbols from @eWhispers tweet text.

        Twitter format is cleaner than OCR - tickers are listed with $ prefix or
        in a structured format. This method extracts all valid ticker symbols.

        Args:
            tweet_text: Full text of the tweet

        Returns:
            List of ticker symbols, deduplicated and order-preserved
        """
        tickers = []

        # Extract tickers - Twitter often uses $TICKER format
        # Also extract any 1-5 uppercase letter words
        for line in tweet_text.split('\n'):
            # Skip header lines
            if any(skip in line.lower() for skip in ['most anticipated', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'week beginning']):
                continue

            # Extract $TICKER format (e.g., $NVDA, $WMT)
            dollar_tickers = re.findall(r'\$([A-Z]{1,5})\b', line)
            for ticker in dollar_tickers:
                if ticker not in self.EXCLUDED_WORDS and ticker not in tickers:
                    tickers.append(ticker)
                    logger.debug(f"Extracted ticker ($ format): {ticker}")

            # Extract plain uppercase tickers (1-5 letters)
            plain_tickers = self._TICKER_PATTERN.findall(line)
            for ticker in plain_tickers:
                if ticker not in self.EXCLUDED_WORDS and len(ticker) >= self.MIN_TICKER_LENGTH and ticker not in tickers:
                    tickers.append(ticker)
                    logger.debug(f"Extracted ticker: {ticker}")

        return list(dict.fromkeys(tickers))  # Remove duplicates, preserve order

    def _parse_reddit_post(self, text: str) -> List[str]:
        """Extract tickers from Reddit post text (legacy)."""
        tickers = []
        in_section = False

        for line in text.split('\n'):
            if 'most anticipated' in line.lower() and 'earnings' in line.lower():
                in_section = True
                continue

            if in_section:
                if line.strip().startswith('#') or 'upcoming earnings' in line.lower():
                    break

                # Extract ticker patterns using pre-compiled patterns
                for pattern in self.TICKER_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        tickers.append(match.group(1))
                        break

        return list(dict.fromkeys(tickers))  # Remove duplicates, preserve order

    def _download_and_parse_image(self, image_url: str) -> Result[List[str], AppError]:
        """Download Reddit image and parse with OCR.

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

            # Check cache first
            url_hash = hashlib.md5(image_url.encode()).hexdigest()
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

                # Cache the result
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
