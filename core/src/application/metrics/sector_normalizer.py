"""
Sector Normalization for VRP Analysis.

Different sectors have different typical earnings move patterns:
- Tech stocks: Large moves (5-15% typical)
- Utilities: Small moves (1-3% typical)
- Healthcare: Variable (depends on drug trial results)

This module normalizes VRP analysis relative to sector peers,
providing fairer comparison across different industries.

Usage:
    normalizer = SectorNormalizer(db_path)

    # Get sector-adjusted VRP
    adjusted = normalizer.normalize_vrp(
        ticker="AAPL",
        raw_vrp=2.5,
        implied_move_pct=8.0
    )
    # adjusted.sector_percentile = where this VRP ranks within tech sector
    # adjusted.normalized_score = VRP relative to sector average
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Dict, List
import numpy as np

logger = logging.getLogger(__name__)


# Default sector statistics (fallback when no data)
# Based on typical earnings move patterns by sector
DEFAULT_SECTOR_STATS = {
    "Technology": {"mean_move": 6.5, "std_move": 3.0, "typical_vrp": 1.8},
    "Communication Services": {"mean_move": 5.5, "std_move": 2.8, "typical_vrp": 1.7},
    "Consumer Discretionary": {"mean_move": 5.0, "std_move": 2.5, "typical_vrp": 1.6},
    "Healthcare": {"mean_move": 7.0, "std_move": 4.5, "typical_vrp": 1.9},
    "Financials": {"mean_move": 3.5, "std_move": 1.8, "typical_vrp": 1.5},
    "Industrials": {"mean_move": 3.5, "std_move": 1.5, "typical_vrp": 1.4},
    "Consumer Staples": {"mean_move": 2.5, "std_move": 1.2, "typical_vrp": 1.3},
    "Energy": {"mean_move": 4.0, "std_move": 2.5, "typical_vrp": 1.5},
    "Materials": {"mean_move": 3.5, "std_move": 2.0, "typical_vrp": 1.4},
    "Utilities": {"mean_move": 2.0, "std_move": 0.8, "typical_vrp": 1.2},
    "Real Estate": {"mean_move": 3.0, "std_move": 1.5, "typical_vrp": 1.3},
}


@dataclass
class SectorStats:
    """
    Statistics for a sector's earnings behavior.

    Attributes:
        sector: Sector name
        mean_move: Average close-to-close move on earnings (%)
        std_move: Standard deviation of moves
        mean_vrp: Average VRP ratio observed
        median_vrp: Median VRP ratio
        p25_vrp: 25th percentile VRP (lower bound for "good")
        p75_vrp: 75th percentile VRP (threshold for "excellent")
        sample_size: Number of observations
    """
    sector: str
    mean_move: float
    std_move: float
    mean_vrp: float
    median_vrp: float
    p25_vrp: float
    p75_vrp: float
    sample_size: int


@dataclass
class NormalizedVRP:
    """
    VRP normalized relative to sector.

    Attributes:
        ticker: Stock ticker
        sector: Stock's sector
        raw_vrp: Original VRP ratio
        sector_mean_vrp: Average VRP for this sector
        normalized_vrp: VRP relative to sector mean (>1 = above average)
        sector_percentile: Where this VRP ranks within sector (0-100)
        sector_z_score: Standard deviations from sector mean
        is_sector_leader: True if in top 25% of sector
        implied_move_percentile: Where implied move ranks in sector
    """
    ticker: str
    sector: str
    raw_vrp: float
    sector_mean_vrp: float
    normalized_vrp: float
    sector_percentile: float
    sector_z_score: float
    is_sector_leader: bool
    implied_move_percentile: float


class SectorNormalizer:
    """
    Normalizes VRP and implied moves relative to sector peers.

    Approach:
    1. Load sector statistics from historical data
    2. Compare ticker's VRP to sector distribution
    3. Calculate normalized scores and percentiles

    Benefits:
    - Fairer comparison across sectors
    - Identifies relative opportunities
    - Avoids sector bias in selection
    """

    def __init__(self, db_path: Path | str, cache_ttl_hours: int = 24):
        """
        Initialize sector normalizer.

        Args:
            db_path: Path to ivcrush.db
            cache_ttl_hours: How long to cache sector stats (default: 24h)
        """
        self.db_path = Path(db_path)
        self.cache_ttl_hours = cache_ttl_hours
        self._sector_cache: Dict[str, SectorStats] = {}
        self._cache_timestamp: Optional[date] = None
        self._ticker_sector_cache: Dict[str, str] = {}

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get_ticker_sector(self, ticker: str) -> Optional[str]:
        """
        Get sector for a ticker from metadata.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Sector name or None if not found
        """
        # Check cache first
        if ticker in self._ticker_sector_cache:
            return self._ticker_sector_cache[ticker]

        if not self.db_path.exists():
            return None

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sector FROM ticker_metadata WHERE ticker = ?",
                (ticker,)
            )
            row = cursor.fetchone()
            conn.close()

            if row and row['sector']:
                sector = row['sector']
                self._ticker_sector_cache[ticker] = sector
                return sector

            return None

        except Exception as e:
            logger.error(f"Error getting sector for {ticker}: {e}")
            return None

    def get_sector_stats(
        self,
        sector: str,
        window_days: int = 365,
        force_refresh: bool = False
    ) -> SectorStats:
        """
        Get statistics for a sector.

        Args:
            sector: Sector name
            window_days: Historical window for stats
            force_refresh: Force recalculation even if cached

        Returns:
            SectorStats for the sector
        """
        # Check cache
        if not force_refresh and sector in self._sector_cache:
            if self._cache_timestamp == date.today():
                return self._sector_cache[sector]

        # Calculate fresh stats
        stats = self._calculate_sector_stats(sector, window_days)
        self._sector_cache[sector] = stats
        self._cache_timestamp = date.today()

        return stats

    def _calculate_sector_stats(
        self,
        sector: str,
        window_days: int
    ) -> SectorStats:
        """Calculate sector statistics from database."""
        if not self.db_path.exists():
            return self._default_sector_stats(sector)

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            start_date = (date.today() - timedelta(days=window_days)).isoformat()

            # Get VRP ratios for sector
            cursor.execute("""
                SELECT
                    iv.vrp_ratio,
                    iv.implied_move_pct,
                    iv.historical_mean_pct
                FROM iv_log iv
                JOIN ticker_metadata tm ON iv.ticker = tm.ticker
                WHERE tm.sector = ?
                  AND iv.scan_date >= ?
                  AND iv.vrp_ratio IS NOT NULL
                  AND iv.vrp_ratio > 0
            """, (sector, start_date))

            vrp_rows = cursor.fetchall()

            # Get historical moves for sector
            cursor.execute("""
                SELECT hm.close_move_pct
                FROM historical_moves hm
                JOIN ticker_metadata tm ON hm.ticker = tm.ticker
                WHERE tm.sector = ?
                  AND hm.earnings_date >= ?
            """, (sector, start_date))

            move_rows = cursor.fetchall()
            conn.close()

            if not vrp_rows or len(vrp_rows) < 10:
                logger.info(f"Insufficient data for sector {sector}, using defaults")
                return self._default_sector_stats(sector)

            vrp_values = np.array([r['vrp_ratio'] for r in vrp_rows])
            move_values = np.array([abs(r['close_move_pct']) for r in move_rows]) if move_rows else np.array([])

            return SectorStats(
                sector=sector,
                mean_move=float(np.mean(move_values)) if len(move_values) > 0 else 0,
                std_move=float(np.std(move_values)) if len(move_values) > 0 else 0,
                mean_vrp=float(np.mean(vrp_values)),
                median_vrp=float(np.median(vrp_values)),
                p25_vrp=float(np.percentile(vrp_values, 25)),
                p75_vrp=float(np.percentile(vrp_values, 75)),
                sample_size=len(vrp_values),
            )

        except Exception as e:
            logger.error(f"Error calculating sector stats for {sector}: {e}")
            return self._default_sector_stats(sector)

    def _default_sector_stats(self, sector: str) -> SectorStats:
        """Get default sector stats from constants."""
        defaults = DEFAULT_SECTOR_STATS.get(
            sector,
            {"mean_move": 4.0, "std_move": 2.0, "typical_vrp": 1.5}
        )

        return SectorStats(
            sector=sector,
            mean_move=defaults["mean_move"],
            std_move=defaults["std_move"],
            mean_vrp=defaults["typical_vrp"],
            median_vrp=defaults["typical_vrp"],
            p25_vrp=defaults["typical_vrp"] * 0.75,
            p75_vrp=defaults["typical_vrp"] * 1.5,
            sample_size=0,
        )

    def normalize_vrp(
        self,
        ticker: str,
        raw_vrp: float,
        implied_move_pct: float,
        sector: Optional[str] = None
    ) -> NormalizedVRP:
        """
        Normalize VRP relative to sector.

        Args:
            ticker: Stock ticker
            raw_vrp: Raw VRP ratio
            implied_move_pct: Implied move percentage
            sector: Optional sector override (auto-detected if None)

        Returns:
            NormalizedVRP with sector-relative metrics
        """
        # Get sector
        if sector is None:
            sector = self.get_ticker_sector(ticker)

        if sector is None:
            sector = "Unknown"
            logger.debug(f"No sector found for {ticker}, using Unknown")

        # Get sector stats
        stats = self.get_sector_stats(sector)

        # Calculate normalized VRP (ratio to sector mean)
        if stats.mean_vrp > 0:
            normalized_vrp = raw_vrp / stats.mean_vrp
        else:
            normalized_vrp = raw_vrp

        # Calculate Z-score
        if stats.sample_size > 0:
            # Estimate std from percentiles if we have them
            std_estimate = (stats.p75_vrp - stats.p25_vrp) / 1.35  # IQR to std approximation
            if std_estimate > 0:
                z_score = (raw_vrp - stats.mean_vrp) / std_estimate
            else:
                z_score = 0.0
        else:
            z_score = 0.0

        # Calculate percentile within sector
        sector_percentile = self._calculate_percentile(raw_vrp, stats)

        # Calculate implied move percentile
        implied_move_percentile = self._calculate_move_percentile(
            implied_move_pct, stats
        )

        # Is sector leader?
        is_leader = raw_vrp >= stats.p75_vrp if stats.sample_size > 0 else raw_vrp >= 2.0

        return NormalizedVRP(
            ticker=ticker,
            sector=sector,
            raw_vrp=raw_vrp,
            sector_mean_vrp=stats.mean_vrp,
            normalized_vrp=normalized_vrp,
            sector_percentile=sector_percentile,
            sector_z_score=z_score,
            is_sector_leader=is_leader,
            implied_move_percentile=implied_move_percentile,
        )

    def _calculate_percentile(self, vrp: float, stats: SectorStats) -> float:
        """
        Estimate percentile of VRP within sector distribution.

        Uses linear interpolation between known percentile points.
        """
        if stats.sample_size == 0:
            # No data, use heuristic based on typical distribution
            if vrp >= 2.5:
                return 90.0
            elif vrp >= 2.0:
                return 75.0
            elif vrp >= 1.5:
                return 50.0
            elif vrp >= 1.2:
                return 25.0
            else:
                return 10.0

        # Linear interpolation between known points
        if vrp <= stats.p25_vrp:
            # Below 25th percentile, interpolate to 0
            if stats.p25_vrp > 0:
                return 25.0 * (vrp / stats.p25_vrp)
            return 0.0
        elif vrp <= stats.median_vrp:
            # Between 25th and 50th
            range_size = stats.median_vrp - stats.p25_vrp
            if range_size > 0:
                return 25.0 + 25.0 * ((vrp - stats.p25_vrp) / range_size)
            return 37.5
        elif vrp <= stats.p75_vrp:
            # Between 50th and 75th
            range_size = stats.p75_vrp - stats.median_vrp
            if range_size > 0:
                return 50.0 + 25.0 * ((vrp - stats.median_vrp) / range_size)
            return 62.5
        else:
            # Above 75th percentile, cap at 99
            excess = vrp - stats.p75_vrp
            iqr = stats.p75_vrp - stats.p25_vrp
            if iqr > 0:
                additional = min(24.0, 24.0 * (excess / iqr))
                return 75.0 + additional
            return 90.0

    def _calculate_move_percentile(
        self,
        implied_move_pct: float,
        stats: SectorStats
    ) -> float:
        """
        Estimate percentile of implied move within sector.

        Uses normal distribution assumption for move sizes.
        """
        if stats.mean_move == 0 or stats.std_move == 0:
            return 50.0  # Unknown, assume median

        # Calculate z-score
        z = (implied_move_pct - stats.mean_move) / stats.std_move

        # Convert to percentile (approximate using standard normal)
        # Clamp z-score to reasonable range
        z = max(-3, min(3, z))

        # Approximate CDF using logistic function (faster than scipy)
        percentile = 100.0 / (1.0 + np.exp(-1.7 * z))

        return float(percentile)

    def get_all_sector_stats(self, window_days: int = 365) -> Dict[str, SectorStats]:
        """
        Get statistics for all sectors.

        Returns:
            Dict mapping sector name to SectorStats
        """
        sectors = list(DEFAULT_SECTOR_STATS.keys())
        result = {}

        for sector in sectors:
            result[sector] = self.get_sector_stats(sector, window_days)

        return result

    def rank_opportunities_by_sector(
        self,
        opportunities: List[dict]
    ) -> List[dict]:
        """
        Rank opportunities with sector normalization.

        Takes list of dicts with 'ticker', 'vrp_ratio', 'implied_move_pct'
        and adds sector-normalized rankings.

        Args:
            opportunities: List of opportunity dicts

        Returns:
            Opportunities with added sector_percentile and normalized_vrp
        """
        for opp in opportunities:
            ticker = opp.get('ticker', '')
            vrp = opp.get('vrp_ratio', 0)
            implied = opp.get('implied_move_pct', 0)

            if vrp > 0:
                normalized = self.normalize_vrp(ticker, vrp, implied)
                opp['sector'] = normalized.sector
                opp['sector_percentile'] = normalized.sector_percentile
                opp['normalized_vrp'] = normalized.normalized_vrp
                opp['is_sector_leader'] = normalized.is_sector_leader
                opp['sector_z_score'] = normalized.sector_z_score

        # Sort by sector percentile (highest first)
        opportunities.sort(key=lambda x: x.get('sector_percentile', 0), reverse=True)

        return opportunities
