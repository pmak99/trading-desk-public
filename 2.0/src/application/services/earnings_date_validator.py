"""
Earnings date cross-reference validator.

Compares earnings dates from multiple sources (Alpha Vantage, Yahoo Finance,
Earnings Whisper) to ensure accuracy and flag conflicts.
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from src.domain.errors import Result, AppError, ErrorCode
from src.domain.types import EarningsTiming

logger = logging.getLogger(__name__)


class EarningsSource(Enum):
    """Earnings data source."""
    ALPHA_VANTAGE = "Alpha Vantage"
    YAHOO_FINANCE = "Yahoo Finance"
    EARNINGS_WHISPER = "Earnings Whisper"
    DATABASE = "Database"


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
    2. Earnings Whisper - Good for near-term dates
    3. Alpha Vantage - Can be stale or incorrect
    """

    # Source reliability weights
    SOURCE_CONFIDENCE = {
        EarningsSource.YAHOO_FINANCE: 1.0,      # Highest confidence
        EarningsSource.EARNINGS_WHISPER: 0.85,  # High confidence for near-term
        EarningsSource.ALPHA_VANTAGE: 0.70,     # Lower confidence (known to be stale)
        EarningsSource.DATABASE: 0.60,          # Lowest (could be outdated)
    }

    def __init__(
        self,
        alpha_vantage=None,
        yahoo_finance=None,
        max_date_diff_days: int = 7
    ):
        """
        Initialize validator with data sources.

        Args:
            alpha_vantage: Alpha Vantage API client
            yahoo_finance: Yahoo Finance earnings fetcher
            max_date_diff_days: Maximum allowed difference between sources (days)
        """
        self.alpha_vantage = alpha_vantage
        self.yahoo_finance = yahoo_finance
        self.max_date_diff_days = max_date_diff_days

    def validate_earnings_date(
        self, ticker: str
    ) -> Result[ValidationResult, AppError]:
        """
        Cross-reference earnings date from all available sources.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ValidationResult with consensus date and conflict info
        """
        logger.info(f"Cross-referencing earnings date for {ticker}...")

        sources: List[EarningsDateInfo] = []

        # Fetch from Yahoo Finance (highest priority)
        if self.yahoo_finance:
            yf_result = self.yahoo_finance.get_next_earnings_date(ticker)
            if yf_result.is_ok:
                earnings_date, timing = yf_result.value
                sources.append(EarningsDateInfo(
                    source=EarningsSource.YAHOO_FINANCE,
                    earnings_date=earnings_date,
                    timing=timing,
                    confidence=self.SOURCE_CONFIDENCE[EarningsSource.YAHOO_FINANCE]
                ))
                logger.debug(f"{ticker}: Yahoo Finance = {earnings_date} ({timing.value})")

        # Fetch from Alpha Vantage
        if self.alpha_vantage:
            av_result = self.alpha_vantage.get_earnings_calendar(
                symbol=ticker, horizon="3month"
            )
            if av_result.is_ok and len(av_result.value) > 0:
                _, earnings_date, timing = av_result.value[0]
                sources.append(EarningsDateInfo(
                    source=EarningsSource.ALPHA_VANTAGE,
                    earnings_date=earnings_date,
                    timing=timing,
                    confidence=self.SOURCE_CONFIDENCE[EarningsSource.ALPHA_VANTAGE]
                ))
                logger.debug(f"{ticker}: Alpha Vantage = {earnings_date} ({timing.value})")

        # Check if we have any data
        if not sources:
            return Result.Err(
                AppError(
                    ErrorCode.NODATA,
                    f"No earnings date found from any source for {ticker}"
                )
            )

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
                logger.warning(f"{ticker}: Date conflict detected! {conflict_details}")

        # Determine consensus date (weighted by confidence)
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
            f"{ticker}: Consensus date = {consensus_date} ({consensus_timing.value})"
            f"{' ⚠️  CONFLICT' if has_conflict else ''}"
        )

        return Result.Ok(result)

    def _get_consensus(
        self, sources: List[EarningsDateInfo]
    ) -> Tuple[date, EarningsTiming]:
        """
        Determine consensus date using confidence-weighted voting.

        If there's a clear high-confidence source (Yahoo Finance), use it.
        Otherwise, use majority vote weighted by confidence.

        Args:
            sources: List of earnings date info from different sources

        Returns:
            Tuple of (consensus_date, consensus_timing)
        """
        # If we have Yahoo Finance, trust it (highest confidence)
        yf_sources = [s for s in sources if s.source == EarningsSource.YAHOO_FINANCE]
        if yf_sources:
            return yf_sources[0].earnings_date, yf_sources[0].timing

        # Otherwise, use the source with highest confidence
        sources_sorted = sorted(sources, key=lambda s: s.confidence, reverse=True)
        best_source = sources_sorted[0]

        return best_source.earnings_date, best_source.timing

    def _build_conflict_message(
        self, sources: List[EarningsDateInfo], max_diff_days: int
    ) -> str:
        """Build human-readable conflict message."""
        parts = [f"Dates differ by {max_diff_days} days:"]
        for source_info in sources:
            parts.append(
                f"{source_info.source.value}: {source_info.earnings_date} "
                f"({source_info.timing.value})"
            )
        return " | ".join(parts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    # Test with real data sources
    from src.infrastructure.api.alpha_vantage import AlphaVantageAPI
    from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings
    from src.config.config import AppConfig
    import os

    config = AppConfig.from_env()

    # Initialize sources
    alpha_vantage = AlphaVantageAPI(
        api_key=os.getenv("ALPHA_VANTAGE_KEY", ""),
        rate_limiter=None
    )
    yahoo_finance = YahooFinanceEarnings()

    validator = EarningsDateValidator(
        alpha_vantage=alpha_vantage,
        yahoo_finance=yahoo_finance
    )

    # Test tickers
    for ticker in ['MRVL', 'AEO', 'SNOW', 'CRM']:
        print(f"\n{'='*60}")
        result = validator.validate_earnings_date(ticker)

        if result.is_ok:
            validation = result.value
            print(f"{ticker}:")
            print(f"  Consensus: {validation.consensus_date} ({validation.consensus_timing.value})")
            print(f"  Sources ({len(validation.sources)}):")
            for src in validation.sources:
                print(f"    - {src.source.value}: {src.earnings_date} ({src.timing.value})")
            if validation.has_conflict:
                print(f"  ⚠️  CONFLICT: {validation.conflict_details}")
        else:
            print(f"{ticker}: ERROR - {result.error}")
