"""
Earnings Whisper scraper for most anticipated earnings.

Primary: Reddit r/wallstreetbets weekly earnings threads (PRAW)
Fallback: Image file with OCR parsing
"""

import logging
import re
import os
from datetime import datetime, timedelta
from typing import List, Optional
from pathlib import Path

import praw
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


def get_week_monday(target_date: Optional[datetime] = None) -> datetime:
    """Get Monday of the week for given date."""
    if target_date is None:
        target_date = datetime.now()

    days_since_monday = target_date.weekday()
    monday = target_date - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


class EarningsWhisperScraper:
    """Scraper for most anticipated earnings using PRAW and OCR."""

    # Regex patterns for extracting tickers from Reddit markdown
    TICKER_PATTERNS = [
        r'\[([A-Z]{1,5})\]\(http',           # [TICKER](url)
        r'\*\*([A-Z]{1,5})\*\*',             # **TICKER**
        r'^[-*]\s+([A-Z]{1,5})\s+[\(\[]'    # - TICKER (date)
    ]

    # Words to exclude from OCR ticker extraction (common false positives)
    EXCLUDED_WORDS = {
        'THE', 'AND', 'FOR', 'ARE', 'NOT', 'YOU', 'ALL', 'CAN',
        'BUT', 'HAD', 'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'DAY',
        'GET', 'HAS', 'HIM', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW',
        'NOW', 'OLD', 'SEE', 'TWO', 'WAY', 'WHO', 'BOY', 'DID',
        'ITS', 'LET', 'PUT', 'SAY', 'SHE', 'TOO', 'USE', 'BMO',
        'AMC', 'DMH', 'EST', 'PST', 'EDT', 'PDT', 'MOST', 'ANTICIPATED'
    }

    def __init__(self):
        """Initialize Reddit client."""
        try:
            self.reddit = praw.Reddit(
                client_id=os.getenv('REDDIT_CLIENT_ID'),
                client_secret=os.getenv('REDDIT_CLIENT_SECRET'),
                user_agent='iv-crush-trading-bot/2.0'
            )
            self.reddit_available = True
            logger.debug("PRAW initialized")
        except Exception as e:
            logger.warning(f"Reddit unavailable: {e}")
            self.reddit_available = False

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

        # Try Reddit first
        if self.reddit_available:
            result = self._fetch_from_reddit(monday)
            if result.is_ok:
                logger.info(f"✓ Retrieved {len(result.value)} tickers from Reddit")
                return result
            logger.warning("Reddit fetch failed")

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

    def _fetch_from_reddit(self, week_monday: datetime) -> Result[List[str], AppError]:
        """Fetch from r/wallstreetbets weekly earnings thread."""
        try:
            subreddit = self.reddit.subreddit('wallstreetbets')
            friday = week_monday + timedelta(days=4)
            week_str = f"{week_monday.month}/{week_monday.day} {friday.month}/{friday.day}"

            # Search with date range
            posts = list(subreddit.search(
                f"weekly earnings thread {week_str}",
                sort='new',
                time_filter='week',
                limit=10
            ))

            # Fallback to broader search
            if not posts:
                posts = list(subreddit.search(
                    "weekly earnings thread",
                    sort='new',
                    time_filter='week',
                    limit=5
                ))

            for post in posts:
                if 'weekly earnings thread' in post.title.lower():
                    tickers = self._parse_reddit_post(post.selftext)
                    if tickers:
                        logger.info(f"Parsed from: {post.title}")
                        return Result.Ok(tickers)

            return Result.Err(AppError(
                ErrorCode.NODATA,
                f"No thread found for {week_monday.strftime('%Y-%m-%d')}"
            ))

        except Exception as e:
            return Result.Err(AppError(ErrorCode.EXTERNAL, f"Reddit error: {e}"))

    def _parse_reddit_post(self, text: str) -> List[str]:
        """Extract tickers from Reddit post text."""
        tickers = []
        in_section = False

        for line in text.split('\n'):
            if 'most anticipated' in line.lower() and 'earnings' in line.lower():
                in_section = True
                continue

            if in_section:
                if line.strip().startswith('#') or 'upcoming earnings' in line.lower():
                    break

                # Extract ticker patterns using class constants
                for pattern in self.TICKER_PATTERNS:
                    match = re.search(pattern, line)
                    if match:
                        tickers.append(match.group(1))
                        break

        return list(dict.fromkeys(tickers))  # Remove duplicates, preserve order

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
        """Extract valid ticker symbols from OCR text."""
        tickers = []

        # Find all potential tickers (1-5 uppercase letters)
        potential_tickers = re.findall(r'\b[A-Z]{1,5}\b', text)

        # Filter out common false positives using class constants
        for ticker in potential_tickers:
            if ticker not in self.EXCLUDED_WORDS and len(ticker) >= 2:
                tickers.append(ticker)

        return list(dict.fromkeys(tickers))  # Remove duplicates, preserve order


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    scraper = EarningsWhisperScraper()
    result = scraper.get_most_anticipated_earnings()

    if result.is_ok:
        print(f"\n✅ Found {len(result.value)} tickers: {', '.join(result.value)}")
    else:
        print(f"\n❌ Error: {result.error}")
