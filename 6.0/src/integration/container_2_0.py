"""Integration wrapper for 2.0's Container.

Provides access to 2.0's core math engine, database, and API clients
without duplicating code.
"""

import sys
import os
import subprocess
from pathlib import Path
from typing import Optional, Any

# Find main repo root (handles both main repo and worktrees)
def _find_main_repo() -> Path:
    """Find main repository root, handling worktrees correctly."""
    try:
        # Get git common dir (works in both main repo and worktrees)
        result = subprocess.run(
            ['git', 'rev-parse', '--git-common-dir'],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent
        )
        git_common_dir = Path(result.stdout.strip())

        # If commondir path is relative, make it absolute
        if not git_common_dir.is_absolute():
            git_common_dir = (Path(__file__).parent / git_common_dir).resolve()

        # Main repo is parent of .git directory
        main_repo = git_common_dir.parent
        return main_repo
    except:
        # Fallback: assume we're in main repo
        return Path(__file__).parent.parent.parent.parent

# Add 2.0/ to Python path with highest priority
# 2.0's code uses "from src.config..." imports, so it needs 2.0/ in path, not 2.0/src/
_main_repo = _find_main_repo()
_2_0_dir = _main_repo / "2.0"
_2_0_dir_str = str(_2_0_dir)

# Remove if already in path (so we can re-insert at position 0)
if _2_0_dir_str in sys.path:
    sys.path.remove(_2_0_dir_str)

# Insert at position 0 for highest priority (before 6.0/src)
sys.path.insert(0, _2_0_dir_str)

# Note: We don't import at module level to avoid namespace collision with 6.0/src
# Imports happen inside __init__ after sys.path is properly configured


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
        # Import here to avoid namespace collision at module level
        from src.config.config import Config

        # Set DB_PATH to point to main repo's database
        # This is necessary because Config.from_env() uses relative paths
        # and we're running from 6.0/ worktree
        import os
        if 'DB_PATH' not in os.environ:
            db_path = _main_repo / "2.0" / "data" / "ivcrush.db"
            os.environ['DB_PATH'] = str(db_path)

        self.config = Config.from_env()
        self._container = None

    @property
    def container(self):
        """Lazy-load container on first access."""
        if self._container is None:
            # Critical: Remove 6.0/ from sys.path temporarily to avoid namespace collision
            # Both 6.0 and 2.0 use 'src' as top-level package, causing import conflicts
            _6_0_paths = [p for p in sys.path if '6.0' in p]
            for p in _6_0_paths:
                sys.path.remove(p)

            # Ensure 2.0/ is at position 0
            if _2_0_dir_str not in sys.path:
                sys.path.insert(0, _2_0_dir_str)
            elif sys.path.index(_2_0_dir_str) != 0:
                sys.path.remove(_2_0_dir_str)
                sys.path.insert(0, _2_0_dir_str)

            try:
                # Clear cached imports of 'src' package to avoid using 6.0's cached version
                # This is necessary because both 6.0 and 2.0 use 'src' as top-level package
                import importlib
                if 'src' in sys.modules:
                    # Save 6.0's src modules
                    _6_0_src_modules = {
                        k: v for k, v in sys.modules.items()
                        if k.startswith('src.')
                    }
                    # Clear src from sys.modules
                    del sys.modules['src']
                    for k in list(_6_0_src_modules.keys()):
                        if k in sys.modules:
                            del sys.modules[k]

                # Import here to avoid namespace collision at module level
                from src.container import get_container
                self._container = get_container()
            finally:
                # Restore 6.0/ paths after import
                for p in _6_0_paths:
                    if p not in sys.path:
                        sys.path.append(p)

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
        days_ahead: int = 7
    ) -> list:
        """
        Get upcoming earnings from calendar.

        Args:
            days_ahead: Number of days to look ahead (default: 7)

        Returns:
            Result with list of (ticker, date) tuples, or error
        """
        return self.container.earnings_repository.get_upcoming_earnings(
            days_ahead=days_ahead
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

            # Try a simple API call (get stock price)
            result = self.container.tradier.get_stock_price('SPY')

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

            # Try a simple API call (get earnings calendar for today)
            from datetime import datetime
            today = datetime.now().strftime('%Y-%m-%d')
            result = self.container.alphavantage.get_earnings_calendar(
                horizon='1month'
            )

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
            db_path = self.config.database.path

            # Check file size
            if os.path.exists(db_path):
                size_bytes = os.path.getsize(db_path)
                size_mb = size_bytes / (1024 * 1024)
            else:
                size_mb = 0.0

            # Get record counts by querying database directly
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM historical_moves")
            historical_moves = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM earnings_calendar")
            earnings_calendar = cursor.fetchone()[0]

            conn.close()

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
