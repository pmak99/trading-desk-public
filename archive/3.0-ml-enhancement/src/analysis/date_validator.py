"""
Earnings date cross-reference validator.

Ported from 2.0 - compares earnings dates from multiple sources
to ensure accuracy and flag conflicts.
"""

import os
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yfinance as yf
import requests

from src.utils.db import get_db_connection

logger = logging.getLogger(__name__)

__all__ = [
    'EarningsSource',
    'EarningsTiming',
    'EarningsDateInfo',
    'ValidationResult',
    'EarningsDateValidator',
]


class EarningsSource(Enum):
    """Earnings data source."""
    YAHOO_FINANCE = "Yahoo Finance"
    ALPHA_VANTAGE = "Alpha Vantage"
    DATABASE = "Database"


class EarningsTiming(Enum):
    """When earnings are announced."""
    BEFORE_MARKET = "BMO"
    AFTER_MARKET = "AMC"
    DURING_MARKET = "DMH"
    UNKNOWN = "UNK"


@dataclass
class EarningsDateInfo:
    """Earnings date from a specific source."""
    source: EarningsSource
    earnings_date: date
    timing: EarningsTiming
    confidence: float  # 0.0 to 1.0


@dataclass
class ValidationResult:
    """Result of cross-referencing earnings dates."""
    ticker: str
    consensus_date: date
    consensus_timing: EarningsTiming
    sources: List[EarningsDateInfo]
    has_conflict: bool
    conflict_details: Optional[str] = None


class EarningsDateValidator:
    """
    Cross-reference earnings dates from multiple sources.

    Priority (highest to lowest):
    1. Yahoo Finance - Most reliable, real-time
    2. Alpha Vantage - Good coverage
    3. Database - May be outdated
    """

    SOURCE_CONFIDENCE = {
        EarningsSource.YAHOO_FINANCE: 1.0,
        EarningsSource.ALPHA_VANTAGE: 0.70,
        EarningsSource.DATABASE: 0.60,
    }

    def __init__(
        self,
        alpha_vantage_key: Optional[str] = None,
        db_path: Optional[Path] = None,
        max_date_diff_days: int = 7
    ):
        """
        Initialize validator.

        Args:
            alpha_vantage_key: Alpha Vantage API key
            db_path: Path to earnings database
            max_date_diff_days: Maximum allowed difference between sources
        """
        self.alpha_vantage_key = alpha_vantage_key or os.getenv('ALPHA_VANTAGE_KEY')
        default_db = Path(__file__).parent.parent.parent.parent / "2.0" / "data" / "ivcrush.db"
        self.db_path = db_path or Path(os.getenv('DB_PATH', str(default_db)))
        self.max_date_diff_days = max_date_diff_days

    def validate(self, ticker: str) -> Optional[ValidationResult]:
        """
        Cross-reference earnings date from all available sources.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ValidationResult or None if no data found
        """
        logger.info(f"Cross-referencing earnings date for {ticker}...")

        sources: List[EarningsDateInfo] = []

        # Fetch from Yahoo Finance (highest priority)
        yf_result = self._fetch_yahoo(ticker)
        if yf_result:
            sources.append(yf_result)

        # Fetch from Alpha Vantage
        if self.alpha_vantage_key:
            av_result = self._fetch_alpha_vantage(ticker)
            if av_result:
                sources.append(av_result)

        # Fetch from database
        db_result = self._fetch_database(ticker)
        if db_result:
            sources.append(db_result)

        if not sources:
            logger.warning(f"{ticker}: No earnings date found from any source")
            return None

        # Detect conflicts
        has_conflict = False
        conflict_details = None

        if len(sources) > 1:
            dates = [s.earnings_date for s in sources]
            min_date = min(dates)
            max_date = max(dates)
            date_diff = (max_date - min_date).days

            if date_diff > self.max_date_diff_days:
                has_conflict = True
                conflict_details = self._build_conflict_message(sources, date_diff)
                logger.warning(f"{ticker}: Date conflict! {conflict_details}")

        # Determine consensus
        consensus_date, consensus_timing = self._get_consensus(sources)

        result = ValidationResult(
            ticker=ticker,
            consensus_date=consensus_date,
            consensus_timing=consensus_timing,
            sources=sources,
            has_conflict=has_conflict,
            conflict_details=conflict_details
        )

        logger.info(
            f"{ticker}: Consensus = {consensus_date} ({consensus_timing.value})"
            f"{' CONFLICT!' if has_conflict else ''}"
        )

        return result

    def _fetch_yahoo(self, ticker: str) -> Optional[EarningsDateInfo]:
        """Fetch earnings date from Yahoo Finance."""
        try:
            stock = yf.Ticker(ticker)
            calendar = stock.calendar

            if calendar is None or calendar.empty:
                logger.debug(f"{ticker}: No Yahoo calendar data")
                return None

            # Handle different calendar formats
            if isinstance(calendar, dict):
                earnings_date_val = calendar.get('Earnings Date')
                if isinstance(earnings_date_val, list) and earnings_date_val:
                    earnings_date = earnings_date_val[0]
                else:
                    earnings_date = earnings_date_val
            else:
                # DataFrame format
                if 'Earnings Date' in calendar.columns:
                    earnings_date = calendar['Earnings Date'].iloc[0]
                elif 0 in calendar.columns:
                    earnings_date = calendar[0].iloc[0]
                else:
                    return None

            if earnings_date is None:
                return None

            # Convert to date
            if hasattr(earnings_date, 'date'):
                earnings_date = earnings_date.date()
            elif isinstance(earnings_date, str):
                from datetime import datetime
                earnings_date = datetime.strptime(earnings_date[:10], '%Y-%m-%d').date()

            logger.debug(f"{ticker}: Yahoo = {earnings_date}")

            return EarningsDateInfo(
                source=EarningsSource.YAHOO_FINANCE,
                earnings_date=earnings_date,
                timing=EarningsTiming.UNKNOWN,
                confidence=self.SOURCE_CONFIDENCE[EarningsSource.YAHOO_FINANCE]
            )

        except Exception as e:
            logger.debug(f"{ticker}: Yahoo fetch failed - {e}")
            return None

    def _fetch_alpha_vantage(self, ticker: str) -> Optional[EarningsDateInfo]:
        """Fetch earnings date from Alpha Vantage."""
        if not self.alpha_vantage_key:
            return None

        try:
            url = "https://www.alphavantage.co/query"
            params = {
                'function': 'EARNINGS_CALENDAR',
                'symbol': ticker,
                'horizon': '3month',
                'apikey': self.alpha_vantage_key
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            # Parse CSV response
            lines = response.text.strip().split('\n')
            if len(lines) < 2:
                return None

            # Skip header, get first data row
            parts = lines[1].split(',')
            if len(parts) < 2:
                return None

            earnings_date_str = parts[2]  # reportDate column
            from datetime import datetime
            earnings_date = datetime.strptime(earnings_date_str, '%Y-%m-%d').date()

            logger.debug(f"{ticker}: Alpha Vantage = {earnings_date}")

            return EarningsDateInfo(
                source=EarningsSource.ALPHA_VANTAGE,
                earnings_date=earnings_date,
                timing=EarningsTiming.UNKNOWN,
                confidence=self.SOURCE_CONFIDENCE[EarningsSource.ALPHA_VANTAGE]
            )

        except Exception as e:
            logger.debug(f"{ticker}: Alpha Vantage fetch failed - {e}")
            return None

    def _fetch_database(self, ticker: str) -> Optional[EarningsDateInfo]:
        """Fetch earnings date from local database."""
        try:
            with get_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT earnings_date, timing
                    FROM earnings_calendar
                    WHERE ticker = ? AND earnings_date >= date('now')
                    ORDER BY earnings_date
                    LIMIT 1
                """, (ticker,))

                row = cursor.fetchone()
                if not row:
                    return None

                earnings_date_str = row[0]
                timing_str = row[1] if len(row) > 1 else 'UNK'

                from datetime import datetime
                if isinstance(earnings_date_str, str):
                    earnings_date = datetime.strptime(earnings_date_str, '%Y-%m-%d').date()
                else:
                    earnings_date = earnings_date_str

                timing_map = {
                    'BMO': EarningsTiming.BEFORE_MARKET,
                    'AMC': EarningsTiming.AFTER_MARKET,
                    'DMH': EarningsTiming.DURING_MARKET,
                }
                timing = timing_map.get(timing_str, EarningsTiming.UNKNOWN)

                logger.debug(f"{ticker}: Database = {earnings_date} ({timing.value})")

                return EarningsDateInfo(
                    source=EarningsSource.DATABASE,
                    earnings_date=earnings_date,
                    timing=timing,
                    confidence=self.SOURCE_CONFIDENCE[EarningsSource.DATABASE]
                )

        except Exception as e:
            logger.debug(f"{ticker}: Database fetch failed - {e}")
            return None

    def _get_consensus(
        self, sources: List[EarningsDateInfo]
    ) -> Tuple[date, EarningsTiming]:
        """Determine consensus using confidence-weighted selection."""
        # Trust Yahoo Finance if available
        yf_sources = [s for s in sources if s.source == EarningsSource.YAHOO_FINANCE]
        if yf_sources:
            return yf_sources[0].earnings_date, yf_sources[0].timing

        # Otherwise use highest confidence source
        sources_sorted = sorted(sources, key=lambda s: s.confidence, reverse=True)
        best = sources_sorted[0]
        return best.earnings_date, best.timing

    def _build_conflict_message(
        self, sources: List[EarningsDateInfo], max_diff_days: int
    ) -> str:
        """Build human-readable conflict message."""
        parts = [f"Dates differ by {max_diff_days} days:"]
        for s in sources:
            parts.append(f"{s.source.value}: {s.earnings_date}")
        return " | ".join(parts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    validator = EarningsDateValidator()

    for ticker in ['AAPL', 'MSFT', 'NVDA', 'ORCL']:
        print(f"\n{'='*60}")
        result = validator.validate(ticker)

        if result:
            print(f"{ticker}:")
            print(f"  Consensus: {result.consensus_date} ({result.consensus_timing.value})")
            print(f"  Sources ({len(result.sources)}):")
            for s in result.sources:
                print(f"    - {s.source.value}: {s.earnings_date}")
            if result.has_conflict:
                print(f"  WARNING: {result.conflict_details}")
        else:
            print(f"{ticker}: No earnings data found")
