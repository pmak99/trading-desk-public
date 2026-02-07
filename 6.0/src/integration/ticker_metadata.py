"""Ticker metadata integration - sector and industry data.

Provides access to the ticker_metadata table for cross-ticker
sector correlation analysis.
"""

import logging
import sqlite3
from typing import Dict, Any, Optional, List
from datetime import datetime

from .container_2_0 import Container2_0

logger = logging.getLogger(__name__)


class TickerMetadataRepository:
    """Repository for ticker metadata (sector, industry, market cap)."""

    # Finnhub industry to sector mapping
    INDUSTRY_TO_SECTOR = {
        'Semiconductors': 'Technology',
        'Software': 'Technology',
        'Technology': 'Technology',
        'Hardware': 'Technology',
        'Internet': 'Technology',
        'Banks': 'Financial Services',
        'Insurance': 'Financial Services',
        'Investment Banking': 'Financial Services',
        'Asset Management': 'Financial Services',
        'Pharmaceuticals': 'Healthcare',
        'Biotechnology': 'Healthcare',
        'Medical Devices': 'Healthcare',
        'Healthcare': 'Healthcare',
        'Retail': 'Consumer Cyclical',
        'Auto': 'Consumer Cyclical',
        'Restaurants': 'Consumer Cyclical',
        'Consumer Electronics': 'Consumer Cyclical',
        'Oil & Gas': 'Energy',
        'Utilities': 'Utilities',
        'Telecom': 'Communication Services',
        'Media': 'Communication Services',
        'Aerospace': 'Industrials',
        'Defense': 'Industrials',
        'Industrial': 'Industrials',
    }

    def __init__(self):
        """Initialize with database path."""
        container = Container2_0()
        self.db_path = container.get_db_path()

    def get_metadata(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a ticker."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT ticker, company_name, sector, industry,
                           market_cap, updated_at
                    FROM ticker_metadata
                    WHERE ticker = ?
                """, (ticker.upper(),))

                row = cursor.fetchone()

            if row is None:
                logger.debug(f"No metadata found for ticker {ticker}")
                return None

            metadata = dict(row)

            # Log missing expected fields at debug level
            for field in ('sector', 'industry', 'company_name'):
                if not metadata.get(field):
                    logger.debug(f"Ticker {ticker} missing metadata field: {field}")

            return metadata

        except Exception as e:
            logger.debug(f"Error fetching metadata for {ticker}: {e}")
            return None

    def save_metadata(
        self,
        ticker: str,
        company_name: str,
        sector: str,
        industry: str,
        market_cap: Optional[float] = None
    ) -> bool:
        """Save or update ticker metadata."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR REPLACE INTO ticker_metadata
                    (ticker, company_name, sector, industry, market_cap, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    ticker.upper(),
                    company_name,
                    sector,
                    industry,
                    market_cap,
                    datetime.now().isoformat()
                ))

                conn.commit()
            return True

        except Exception as e:
            logger.debug(f"Error saving metadata for {ticker}: {e}")
            return False

    def delete_metadata(self, ticker: str) -> bool:
        """Delete ticker metadata (for testing)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("DELETE FROM ticker_metadata WHERE ticker = ?", (ticker.upper(),))
                conn.commit()
            return True
        except Exception as e:
            logger.debug(f"Error deleting metadata for {ticker}: {e}")
            return False

    def get_by_sector(self, sector: str) -> List[Dict[str, Any]]:
        """Get all tickers in a sector."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT ticker, company_name, sector, industry, market_cap
                    FROM ticker_metadata
                    WHERE sector = ?
                """, (sector,))

                rows = cursor.fetchall()

            return [dict(row) for row in rows]

        except Exception as e:
            logger.debug(f"Error fetching tickers for sector {sector}: {e}")
            return []

    @classmethod
    def map_industry_to_sector(cls, industry: str) -> str:
        """Map Finnhub industry to sector."""
        return cls.INDUSTRY_TO_SECTOR.get(industry, 'Other')
