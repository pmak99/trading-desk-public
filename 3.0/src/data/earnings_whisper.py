"""
Earnings Whisper scraper for most anticipated earnings.

Ported from 2.0 with simplifications for 3.0.

Primary: X/Twitter @eWhispers account (playwright browser automation)
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
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Optional dependencies (lazy import)
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not available. Install: pip install playwright && playwright install chromium")

try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

import requests

__all__ = [
    'EarningsWhisperResult',
    'EarningsWhisperScraper',
    'get_week_monday',
]


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class SimpleCircuitBreaker:
    """Simple circuit breaker for external service calls."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: int = 60,
        success_threshold: int = 2
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None

    def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        if self.state == CircuitState.OPEN:
            if self.last_failure_time:
                time_since_failure = time.time() - self.last_failure_time
                if time_since_failure >= self.recovery_timeout:
                    logger.debug(f"Circuit {self.name}: OPEN -> HALF_OPEN")
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                else:
                    raise RuntimeError(f"Circuit breaker {self.name} is OPEN")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                logger.info(f"Circuit {self.name}: HALF_OPEN -> CLOSED")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def _on_failure(self):
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            logger.warning(f"Circuit {self.name}: HALF_OPEN -> OPEN")
            self.state = CircuitState.OPEN
        elif self.state == CircuitState.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                logger.warning(f"Circuit {self.name}: CLOSED -> OPEN")
                self.state = CircuitState.OPEN

    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN


def get_week_monday(target_date: Optional[datetime] = None) -> datetime:
    """Get Monday of the week for given date."""
    if target_date is None:
        target_date = datetime.now()
    days_since_monday = target_date.weekday()
    monday = target_date - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class EarningsWhisperResult:
    """Result from Earnings Whisper scraper."""
    tickers: List[str]
    week_monday: str
    source: str  # "twitter" or "ocr"
    cached: bool = False


class EarningsWhisperScraper:
    """Scraper for most anticipated earnings from @eWhispers."""

    TWITTER_URL = "https://twitter.com/eWhispers"
    PAGE_LOAD_TIMEOUT_MS = 30000
    TWEET_WAIT_TIMEOUT_MS = 10000
    MAX_TWEETS_TO_CHECK = 50

    TWEET_SELECTORS = [
        'article[data-testid="tweet"]',
        'article[role="article"]',
    ]
    TWEET_TEXT_SELECTORS = [
        '[data-testid="tweetText"]',
        '[lang]',
    ]

    # Pre-compiled patterns
    _TICKER_PATTERN = re.compile(r'\b([A-Z]{1,5})\b')
    _DATE_PATTERN = re.compile(r'(\d{1,2})/(\d{1,2})')
    _WRITTEN_DATE_PATTERN = re.compile(
        r'(january|february|march|april|may|june|july|august|'
        r'september|october|november|december)\s+(\d{1,2})',
        re.IGNORECASE
    )

    _MONTH_NAMES = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12
    }

    # Words to exclude from ticker extraction
    EXCLUDED_WORDS = {
        'THE', 'AND', 'FOR', 'ARE', 'NOT', 'YOU', 'ALL', 'CAN',
        'BUT', 'HAD', 'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'DAY',
        'GET', 'HAS', 'HIM', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW',
        'NOW', 'OLD', 'SEE', 'TWO', 'WAY', 'WHO', 'BOY', 'DID',
        'LET', 'PUT', 'SAY', 'SHE', 'TOO', 'USE', 'BMO', 'AMC',
        'EST', 'PST', 'EDT', 'PDT', 'MOST', 'ANTICIPATED', 'WEEK',
        'THAT', 'WITH', 'FROM', 'HAVE', 'THIS', 'WILL', 'YOUR',
        'MORE', 'BEEN', 'THAN', 'SOME', 'TIME', 'VERY', 'WHEN',
        'COME', 'HERE', 'JUST', 'LIKE', 'LONG', 'MAKE', 'MANY',
        'OVER', 'SUCH', 'TAKE', 'THEM', 'WELL', 'ONLY', 'BACK',
        'GOOD', 'HIGH', 'LIFE', 'MUCH', 'DOWN', 'BOTH', 'EACH',
        'FIND', 'FOUR', 'GIVE', 'HAND', 'KEEP', 'LAST', 'LATE',
        'LOOK', 'MOVE', 'NEXT', 'OPEN', 'PART', 'PLAY', 'REAL',
        'SAME', 'SEEM', 'SHOW', 'SIDE', 'TELL', 'TURN', 'WHAT',
        'WORK', 'YEAR', 'MONDAY', 'TUESDAY', 'WEDNESDAY',
        'THURSDAY', 'FRIDAY', 'EARNINGS', 'REPORT', 'AFTER',
        'BEFORE', 'CLOSE', 'MARKET', 'BELL',
    }

    MIN_TICKER_LENGTH = 2

    def __init__(self, cache_ttl_hours: int = 24):
        """
        Initialize scraper.

        Args:
            cache_ttl_hours: How long to cache results (hours)
        """
        self._cache: Dict[str, Tuple[EarningsWhisperResult, float]] = {}
        self._cache_ttl = cache_ttl_hours * 3600

        self._twitter_breaker = SimpleCircuitBreaker(
            name="Twitter",
            failure_threshold=3,
            recovery_timeout=120,
        )

        self._last_request_time: Optional[float] = None
        self._min_request_interval = 5.0

    def get_most_anticipated(
        self,
        week_monday: Optional[str] = None,
        fallback_image: Optional[str] = None
    ) -> Optional[EarningsWhisperResult]:
        """
        Get most anticipated earnings tickers for a week.

        Args:
            week_monday: Monday in YYYY-MM-DD format (defaults to upcoming week)
            fallback_image: Path to screenshot image for OCR fallback

        Returns:
            EarningsWhisperResult or None if all methods fail
        """
        if week_monday:
            try:
                target_date = datetime.strptime(week_monday, "%Y-%m-%d")
            except ValueError:
                logger.error(f"Invalid date format: {week_monday}. Use YYYY-MM-DD")
                return None
            monday = get_week_monday(target_date)
            weeks_to_try = [monday]
        else:
            current_monday = get_week_monday(datetime.now())
            next_monday = current_monday + timedelta(days=7)
            weeks_to_try = [next_monday, current_monday]

        for monday in weeks_to_try:
            result = self._try_fetch_week(monday, fallback_image)
            if result:
                return result

        return None

    def _try_fetch_week(
        self,
        monday: datetime,
        fallback_image: Optional[str] = None
    ) -> Optional[EarningsWhisperResult]:
        """Try to fetch earnings for a specific week."""
        monday_str = monday.strftime('%Y-%m-%d')

        # Check cache
        if monday_str in self._cache:
            cached_result, cached_time = self._cache[monday_str]
            if time.time() - cached_time < self._cache_ttl:
                logger.info(f"Cache HIT for week {monday_str}")
                return EarningsWhisperResult(
                    tickers=cached_result.tickers,
                    week_monday=monday_str,
                    source=cached_result.source,
                    cached=True
                )

        # Try Twitter
        if PLAYWRIGHT_AVAILABLE and not self._twitter_breaker.is_open():
            try:
                tickers = self._twitter_breaker.call(self._fetch_from_twitter, monday)
                if tickers:
                    result = EarningsWhisperResult(
                        tickers=tickers,
                        week_monday=monday_str,
                        source="twitter"
                    )
                    self._cache[monday_str] = (result, time.time())
                    return result
            except Exception as e:
                logger.warning(f"Twitter fetch failed: {e}")

        # Fallback to image OCR
        if fallback_image and OCR_AVAILABLE:
            tickers = self._parse_image(fallback_image)
            if tickers:
                result = EarningsWhisperResult(
                    tickers=tickers,
                    week_monday=monday_str,
                    source="ocr"
                )
                self._cache[monday_str] = (result, time.time())
                return result

        return None

    def _fetch_from_twitter(self, week_monday: datetime) -> Optional[List[str]]:
        """Fetch from @eWhispers Twitter using playwright."""
        if not PLAYWRIGHT_AVAILABLE:
            return None

        # Rate limiting
        if self._last_request_time:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)

        monday_str = f"{week_monday.month}/{week_monday.day}"
        logger.info(f"Scraping @eWhispers for week {monday_str}")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.set_extra_http_headers({
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                                      'Chrome/131.0.0.0 Safari/537.36'
                    })

                    page.goto(self.TWITTER_URL, wait_until='load',
                             timeout=self.PAGE_LOAD_TIMEOUT_MS)

                    # Wait for tweets
                    tweet_selector = None
                    for selector in self.TWEET_SELECTORS:
                        try:
                            page.wait_for_selector(selector, timeout=self.TWEET_WAIT_TIMEOUT_MS)
                            tweet_selector = selector
                            break
                        except PlaywrightTimeout:
                            continue

                    if not tweet_selector:
                        logger.warning("No tweets found - page may require auth")
                        return None

                    tweet_elements = page.query_selector_all(tweet_selector)

                    for i, tweet_elem in enumerate(tweet_elements):
                        if i >= self.MAX_TWEETS_TO_CHECK:
                            break

                        text_elem = None
                        for text_selector in self.TWEET_TEXT_SELECTORS:
                            text_elem = tweet_elem.query_selector(text_selector)
                            if text_elem:
                                break

                        if not text_elem:
                            continue

                        tweet_text = text_elem.inner_text()

                        if self._matches_week(tweet_text, week_monday):
                            tickers = self._parse_twitter_text(tweet_text)
                            if tickers:
                                logger.info(f"Found {len(tickers)} tickers from Twitter")
                                return tickers

                finally:
                    browser.close()

        except Exception as e:
            logger.error(f"Twitter scraper error: {e}")

        finally:
            self._last_request_time = time.time()

        return None

    def _matches_week(self, tweet_text: str, week_monday: datetime) -> bool:
        """Check if tweet matches the requested week."""
        # Try written format first
        written_matches = self._WRITTEN_DATE_PATTERN.findall(tweet_text.lower())
        if written_matches:
            month_name, day_str = written_matches[0]
            try:
                tweet_month = self._MONTH_NAMES[month_name.lower()]
                tweet_day = int(day_str)
                if tweet_month == week_monday.month and tweet_day == week_monday.day:
                    return True
            except (ValueError, KeyError):
                pass

        # Try numeric format
        matches = self._DATE_PATTERN.findall(tweet_text)
        if matches:
            month_str, day_str = matches[0]
            try:
                tweet_month = int(month_str)
                tweet_day = int(day_str)
                if (1 <= tweet_month <= 12 and 1 <= tweet_day <= 31 and
                    tweet_month == week_monday.month and tweet_day == week_monday.day):
                    return True
            except (ValueError, OverflowError):
                pass

        return False

    def _parse_twitter_text(self, tweet_text: str) -> List[str]:
        """Extract ticker symbols from tweet text."""
        tickers = []

        for line in tweet_text.split('\n'):
            # Skip header lines
            if any(skip in line.lower() for skip in
                   ['most anticipated', 'monday', 'tuesday', 'wednesday',
                    'thursday', 'friday', 'week beginning']):
                continue

            # Extract $TICKER format
            dollar_tickers = re.findall(r'\$([A-Z]{1,5})\b', line)
            for ticker in dollar_tickers:
                if ticker not in self.EXCLUDED_WORDS and ticker not in tickers:
                    tickers.append(ticker)

            # Extract plain uppercase tickers
            plain_tickers = self._TICKER_PATTERN.findall(line)
            for ticker in plain_tickers:
                if (ticker not in self.EXCLUDED_WORDS and
                    len(ticker) >= self.MIN_TICKER_LENGTH and
                    ticker not in tickers):
                    tickers.append(ticker)

        return list(dict.fromkeys(tickers))

    def _parse_image(self, image_path: str) -> Optional[List[str]]:
        """Extract tickers from image using OCR."""
        if not OCR_AVAILABLE:
            logger.warning("OCR not available")
            return None

        try:
            path = Path(image_path)
            if not path.exists():
                logger.error(f"Image file not found: {image_path}")
                return None

            image = Image.open(path)
            text = pytesseract.image_to_string(image)

            tickers = []
            for line in text.split('\n'):
                matches = self._TICKER_PATTERN.findall(line)
                for ticker in matches:
                    if (ticker not in self.EXCLUDED_WORDS and
                        len(ticker) >= self.MIN_TICKER_LENGTH and
                        ticker not in tickers):
                        tickers.append(ticker)

            logger.info(f"Extracted {len(tickers)} tickers from OCR")
            return tickers if tickers else None

        except Exception as e:
            logger.error(f"OCR error: {e}")
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    scraper = EarningsWhisperScraper()
    result = scraper.get_most_anticipated()

    if result:
        print(f"\nFound {len(result.tickers)} tickers for week of {result.week_monday}")
        print(f"Source: {result.source}")
        print(f"Tickers: {', '.join(result.tickers[:20])}...")
    else:
        print("\nNo earnings data found")
