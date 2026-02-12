"""PreFlightAgent - Ticker validation and data freshness checks.

Performs fast pre-flight validation before running the full analysis pipeline:
1. Ticker format validation and alias resolution (PFIZER -> PFE)
2. Historical data existence check (OTC/delisted detection)
3. Data freshness check (stale data warning)
4. Earnings date sanity check

All checks are DB-only (no API calls), targeting <50ms execution.
"""

import sqlite3
import logging
import importlib.util
from typing import Dict, Any, Optional
from datetime import datetime, date
from pathlib import Path

# Import 5.0's ticker module directly by file path to avoid namespace collision
# (both agents and cloud use 'src' as top-level package)
_ticker_module_path = Path(__file__).parent.parent.parent.parent / "cloud" / "src" / "domain" / "ticker.py"
_spec = importlib.util.spec_from_file_location("_ticker_5_0", str(_ticker_module_path))
_ticker_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ticker_mod)

normalize_ticker = _ticker_mod.normalize_ticker
validate_ticker = _ticker_mod.validate_ticker
TICKER_ALIASES = _ticker_mod.TICKER_ALIASES
InvalidTickerError = _ticker_mod.InvalidTickerError

from ..integration.container_2_0 import Container2_0
from ..utils.schemas import PreFlightResponse

logger = logging.getLogger(__name__)


class PreFlightAgent:
    """Pre-flight ticker validation agent.

    Performs fast validation checks before the main analysis pipeline
    to fail fast on invalid tickers, missing data, or stale data.

    Example:
        agent = PreFlightAgent()
        result = agent.validate("PFIZER")
        # Returns: {'ticker': 'PFIZER', 'normalized_ticker': 'PFE', 'is_valid': True, ...}
    """

    # Thresholds
    STALE_DATA_DAYS = 14
    EARNINGS_TOO_FAR_DAYS = 7

    def __init__(self):
        """Initialize with container for DB access."""
        self._container = None

    @property
    def container(self) -> Container2_0:
        """Lazy-load container."""
        if self._container is None:
            self._container = Container2_0()
        return self._container

    def validate(
        self,
        ticker: str,
        earnings_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Validate ticker and check data readiness.

        Args:
            ticker: Raw ticker input (may be company name alias)
            earnings_date: Optional earnings date for sanity checks (YYYY-MM-DD)

        Returns:
            Dict conforming to PreFlightResponse schema
        """
        warnings = []

        # Step 1: Normalize ticker (alias resolution + format validation)
        try:
            normalized = normalize_ticker(ticker)
        except InvalidTickerError as e:
            return PreFlightResponse(
                ticker=ticker,
                normalized_ticker=ticker.upper().strip() if ticker else "",
                is_valid=False,
                error=str(e),
            ).model_dump()

        # Track if alias was resolved
        upper_ticker = ticker.upper().strip()
        if upper_ticker != normalized and upper_ticker in TICKER_ALIASES:
            warnings.append(f"Resolved alias: {upper_ticker} -> {normalized}")

        # Step 2: Check historical data in DB
        historical_quarters = 0
        has_historical_data = False
        data_freshness_days = None

        try:
            db_path = self.container.get_db_path()
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.cursor()

                # Count historical moves
                cursor.execute(
                    "SELECT COUNT(*) FROM historical_moves WHERE ticker = ?",
                    (normalized,)
                )
                historical_quarters = cursor.fetchone()[0]
                has_historical_data = historical_quarters > 0

                # Check data freshness (most recent earnings date in DB)
                if has_historical_data:
                    cursor.execute(
                        "SELECT MAX(earnings_date) FROM historical_moves WHERE ticker = ?",
                        (normalized,)
                    )
                    latest_date_str = cursor.fetchone()[0]
                    if latest_date_str:
                        try:
                            latest_date = datetime.strptime(latest_date_str, '%Y-%m-%d').date()
                            data_freshness_days = (date.today() - latest_date).days
                            if data_freshness_days > self.STALE_DATA_DAYS:
                                warnings.append(
                                    f"Stale data: last earnings record is {data_freshness_days} days old"
                                )
                        except ValueError:
                            pass

                # If no historical data, check earnings_calendar for existence
                if not has_historical_data:
                    cursor.execute(
                        "SELECT COUNT(*) FROM earnings_calendar WHERE ticker = ?",
                        (normalized,)
                    )
                    in_calendar = cursor.fetchone()[0] > 0
                    if not in_calendar:
                        warnings.append(
                            f"No historical data or earnings calendar entries for {normalized} "
                            f"- may be OTC, delisted, or new IPO"
                        )

        except Exception as e:
            logger.warning(f"DB check failed for {normalized}: {e}")
            warnings.append(f"Could not verify data: {e}")

        # Step 3: Earnings date sanity checks
        if earnings_date:
            try:
                ed = datetime.strptime(earnings_date, '%Y-%m-%d').date()
                days_away = (ed - date.today()).days
                if days_away < 0:
                    warnings.append(f"Earnings date {earnings_date} is in the past")
                elif days_away > self.EARNINGS_TOO_FAR_DAYS:
                    warnings.append(
                        f"Earnings date {earnings_date} is {days_away} days away "
                        f"(IV crush opportunity may not be actionable yet)"
                    )
            except ValueError:
                warnings.append(f"Invalid earnings date format: {earnings_date}")

        result = PreFlightResponse(
            ticker=ticker,
            normalized_ticker=normalized,
            is_valid=True,
            has_historical_data=has_historical_data,
            historical_quarters=historical_quarters,
            data_freshness_days=data_freshness_days,
            warnings=warnings,
        )
        return result.model_dump()
