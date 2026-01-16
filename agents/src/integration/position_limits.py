"""Position limits integration - query TRR data from database.

Provides access to the position_limits table which contains Tail Risk Ratio
calculations for each ticker.
"""

import sqlite3
from typing import Dict, Any, Optional

from .container_2_0 import Container2_0


class PositionLimitsRepository:
    """Repository for querying position limits from database."""

    def __init__(self):
        """Initialize with database path from 2.0 container."""
        container = Container2_0()
        self.db_path = container.get_db_path()

    def get_limits(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get position limits for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dict with position limits or None if not found

        Example:
            repo = PositionLimitsRepository()
            limits = repo.get_limits("MU")
            # Returns:
            # {
            #     'ticker': 'MU',
            #     'tail_risk_ratio': 3.05,
            #     'tail_risk_level': 'HIGH',
            #     'max_contracts': 50,
            #     'max_notional': 25000.0,
            #     'avg_move': 3.68,
            #     'max_move': 11.21
            # }
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, tail_risk_ratio, tail_risk_level,
                       max_contracts, max_notional, avg_move, max_move
                FROM position_limits
                WHERE ticker = ?
            """, (ticker.upper(),))

            row = cursor.fetchone()
            conn.close()

            if row is None:
                return None

            return dict(row)

        except Exception as e:
            return None

    def get_all_high_risk(self) -> list:
        """Get all tickers with HIGH tail risk."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, tail_risk_ratio, tail_risk_level,
                       max_contracts, max_notional, avg_move, max_move
                FROM position_limits
                WHERE tail_risk_level = 'HIGH'
                ORDER BY tail_risk_ratio DESC
            """)

            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]

        except Exception:
            return []
