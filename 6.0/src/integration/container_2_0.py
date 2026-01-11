"""Integration wrapper for 2.0's Container.

Provides access to 2.0's core math engine, database, and API clients
without duplicating code.
"""

import sys
from pathlib import Path
from typing import Optional, Any

# Add 2.0/src to Python path
_2_0_src = Path(__file__).parent.parent.parent.parent / "2.0" / "src"
if str(_2_0_src) not in sys.path:
    sys.path.insert(0, str(_2_0_src))

# Import 2.0 components
from src.container import get_container
from src.config.config import Config


class Container2_0:
    """
    Wrapper for 2.0's dependency injection container.

    Provides access to:
    - analyzer: VRP calculation and strategy generation
    - prices_repository: Historical price data
    - earnings_repository: Earnings calendar
    - tradier_client: Options data API
    - alphavantage_client: Earnings dates API

    Example:
        container = Container2_0()
        result = container.analyze_ticker("NVDA", "2026-02-05", "2026-02-07")
    """

    def __init__(self):
        """Initialize container with 2.0's Config."""
        self.config = Config.from_env()
        self._container = None

    @property
    def container(self):
        """Lazy-load container on first access."""
        if self._container is None:
            self._container = get_container()
        return self._container

    def analyze_ticker(
        self,
        ticker: str,
        earnings_date: str,
        expiration: str,
        generate_strategies: bool = True
    ) -> Any:
        """
        Call 2.0's analyzer for full VRP + strategy analysis.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings announcement date (YYYY-MM-DD)
            expiration: Options expiration date (YYYY-MM-DD)
            generate_strategies: Whether to generate strategy recommendations

        Returns:
            Analysis result from 2.0's analyzer
        """
        return self.container.analyzer.analyze(
            ticker=ticker,
            earnings_date=earnings_date,
            expiration=expiration,
            generate_strategies=generate_strategies
        )

    def get_historical_moves(
        self,
        ticker: str,
        limit: int = 12
    ) -> list:
        """
        Get historical earnings moves for ticker.

        Args:
            ticker: Stock ticker symbol
            limit: Maximum number of historical moves to retrieve

        Returns:
            List of historical move records
        """
        return self.container.prices_repository.get_historical_moves(
            ticker=ticker,
            limit=limit
        )

    def get_upcoming_earnings(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> list:
        """
        Get upcoming earnings from calendar.

        Args:
            start_date: Start date filter (YYYY-MM-DD)
            end_date: End date filter (YYYY-MM-DD)

        Returns:
            List of earnings calendar entries
        """
        return self.container.earnings_repository.get_upcoming_earnings(
            start_date=start_date,
            end_date=end_date
        )

    def check_tradier_health(self) -> dict:
        """
        Check Tradier API connectivity.

        Returns:
            Health status dict with status, latency_ms, error
        """
        try:
            import time
            start = time.time()

            # Try a simple API call (market status)
            # Note: Actual implementation depends on tradier_client interface
            # This is a placeholder - adjust based on actual API
            result = self.container.tradier_client.get_quotes(['SPY'])

            latency_ms = int((time.time() - start) * 1000)

            return {
                'status': 'ok',
                'latency_ms': latency_ms,
                'error': None
            }
        except Exception as e:
            return {
                'status': 'error',
                'latency_ms': None,
                'error': str(e)
            }

    def check_alphavantage_health(self) -> dict:
        """
        Check Alpha Vantage API connectivity.

        Returns:
            Health status dict with status, latency_ms, error
        """
        try:
            import time
            start = time.time()

            # Try a simple API call (earnings calendar)
            # Note: Actual implementation depends on alphavantage_client interface
            result = self.container.alphavantage_client.get_earnings_calendar()

            latency_ms = int((time.time() - start) * 1000)

            return {
                'status': 'ok',
                'latency_ms': latency_ms,
                'error': None
            }
        except Exception as e:
            return {
                'status': 'error',
                'latency_ms': None,
                'error': str(e)
            }

    def check_database_health(self) -> dict:
        """
        Check database connectivity and size.

        Returns:
            Health status dict with status, size_mb, record counts, error
        """
        try:
            import os

            # Get database connection
            db_path = self.config.db_path

            # Check file size
            if os.path.exists(db_path):
                size_bytes = os.path.getsize(db_path)
                size_mb = size_bytes / (1024 * 1024)
            else:
                size_mb = 0.0

            # Get record counts
            historical_moves = len(
                self.container.prices_repository.get_all_historical_moves()
            )
            earnings_calendar = len(
                self.container.earnings_repository.get_all_earnings()
            )

            return {
                'status': 'ok',
                'size_mb': round(size_mb, 2),
                'historical_moves': historical_moves,
                'earnings_calendar': earnings_calendar,
                'error': None
            }
        except Exception as e:
            return {
                'status': 'error',
                'size_mb': None,
                'historical_moves': None,
                'earnings_calendar': None,
                'error': str(e)
            }
