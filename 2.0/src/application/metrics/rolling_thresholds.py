"""
Rolling Percentile VRP Thresholds - Dynamic Threshold Calculation.

Instead of static VRP thresholds (e.g., 2.0x for excellent), this module
calculates thresholds dynamically based on rolling historical distributions.

Benefits:
- Auto-adapts to market conditions (high VIX = higher baseline VRP everywhere)
- Identifies relative opportunities (top 25% of recent VRPs)
- Avoids "threshold inflation" during volatile periods
- More robust across different market regimes

Usage:
    calculator = RollingThresholdCalculator(db_path)
    thresholds = calculator.get_adaptive_thresholds(window_days=90)
    # thresholds.excellent = rolling_p75
    # thresholds.good = rolling_p50
    # thresholds.marginal = rolling_p25
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List, Tuple
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RollingThresholds:
    """
    VRP thresholds calculated from rolling historical data.

    Attributes:
        excellent: 75th percentile VRP (top quartile)
        good: 50th percentile VRP (median)
        marginal: 25th percentile VRP (bottom quartile cutoff)
        sample_size: Number of observations in window
        window_days: Rolling window size in days
        as_of_date: Date thresholds were calculated
        mean_vrp: Mean VRP in window (for reference)
        std_vrp: Standard deviation of VRP (volatility of edge)
    """
    excellent: float
    good: float
    marginal: float
    sample_size: int
    window_days: int
    as_of_date: date
    mean_vrp: float
    std_vrp: float

    def is_sufficient_data(self, min_samples: int = 30) -> bool:
        """Check if we have enough data for reliable thresholds."""
        return self.sample_size >= min_samples


@dataclass
class SectorThresholds:
    """
    Sector-specific VRP thresholds.

    Different sectors have different typical move patterns.
    Tech stocks move more than utilities, so sector normalization
    provides fairer comparison.
    """
    sector: str
    excellent: float
    good: float
    marginal: float
    sample_size: int
    mean_move: float  # Sector's average historical move


class RollingThresholdCalculator:
    """
    Calculates VRP thresholds from rolling historical distributions.

    Instead of static thresholds, this identifies opportunities
    relative to recent market behavior.

    Approach:
    1. Query historical VRP ratios from iv_log
    2. Calculate rolling percentiles (p25, p50, p75)
    3. Use these as dynamic thresholds

    Fallback:
    - If insufficient data, uses static defaults
    - Logs warnings when data is stale or sparse
    """

    # Static fallbacks when insufficient data
    DEFAULT_EXCELLENT = 2.0
    DEFAULT_GOOD = 1.5
    DEFAULT_MARGINAL = 1.2

    # Minimum samples for reliable percentile estimation
    MIN_SAMPLES = 30

    # Percentile levels
    EXCELLENT_PERCENTILE = 75
    GOOD_PERCENTILE = 50
    MARGINAL_PERCENTILE = 25

    def __init__(self, db_path: Path | str):
        """
        Initialize calculator with database path.

        Args:
            db_path: Path to ivcrush.db database
        """
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            logger.warning(f"Database not found: {db_path}, will use static thresholds")

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get_rolling_thresholds(
        self,
        window_days: int = 90,
        as_of_date: Optional[date] = None
    ) -> RollingThresholds:
        """
        Calculate VRP thresholds from rolling window.

        Args:
            window_days: Number of days to look back (default: 90)
            as_of_date: Calculate as of this date (default: today)

        Returns:
            RollingThresholds with percentile-based thresholds
        """
        if as_of_date is None:
            as_of_date = date.today()

        start_date = as_of_date - timedelta(days=window_days)

        if not self.db_path.exists():
            logger.warning("No database, returning static thresholds")
            return self._static_fallback(window_days, as_of_date)

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Query historical VRP ratios
            cursor.execute("""
                SELECT vrp_ratio
                FROM iv_log
                WHERE scan_date >= ?
                  AND scan_date <= ?
                  AND vrp_ratio IS NOT NULL
                  AND vrp_ratio > 0
                ORDER BY scan_date
            """, (start_date.isoformat(), as_of_date.isoformat()))

            rows = cursor.fetchall()
            conn.close()

            if not rows:
                logger.warning(f"No VRP data in last {window_days} days, using static thresholds")
                return self._static_fallback(window_days, as_of_date)

            vrp_values = [row['vrp_ratio'] for row in rows]

            return self._calculate_thresholds(vrp_values, window_days, as_of_date)

        except Exception as e:
            logger.error(f"Error calculating rolling thresholds: {e}")
            return self._static_fallback(window_days, as_of_date)

    def _calculate_thresholds(
        self,
        vrp_values: List[float],
        window_days: int,
        as_of_date: date
    ) -> RollingThresholds:
        """
        Calculate percentile-based thresholds from VRP values.

        Uses numpy percentile calculation for efficiency and accuracy.
        """
        arr = np.array(vrp_values)
        n = len(arr)

        if n < self.MIN_SAMPLES:
            logger.warning(
                f"Only {n} samples (need {self.MIN_SAMPLES}), "
                f"blending with static thresholds"
            )
            # Blend with static thresholds based on sample size
            blend_weight = n / self.MIN_SAMPLES
            return self._blended_thresholds(arr, blend_weight, window_days, as_of_date)

        # Calculate percentiles
        p75 = np.percentile(arr, self.EXCELLENT_PERCENTILE)
        p50 = np.percentile(arr, self.GOOD_PERCENTILE)
        p25 = np.percentile(arr, self.MARGINAL_PERCENTILE)

        # Ensure ordering (excellent > good > marginal)
        # This should always be true for percentiles, but be defensive
        excellent = max(p75, self.DEFAULT_EXCELLENT * 0.8)  # Floor at 80% of default
        good = max(p50, self.DEFAULT_GOOD * 0.8)
        marginal = max(p25, self.DEFAULT_MARGINAL * 0.8)

        thresholds = RollingThresholds(
            excellent=excellent,
            good=good,
            marginal=marginal,
            sample_size=n,
            window_days=window_days,
            as_of_date=as_of_date,
            mean_vrp=float(np.mean(arr)),
            std_vrp=float(np.std(arr)),
        )

        logger.info(
            f"Rolling thresholds ({window_days}d, n={n}): "
            f"excellent={excellent:.2f}x, good={good:.2f}x, marginal={marginal:.2f}x"
        )

        return thresholds

    def _blended_thresholds(
        self,
        arr: np.ndarray,
        blend_weight: float,
        window_days: int,
        as_of_date: date
    ) -> RollingThresholds:
        """
        Blend rolling thresholds with static defaults when data is sparse.

        blend_weight: 0 = all static, 1 = all rolling
        """
        n = len(arr)

        # Calculate rolling percentiles
        if n > 0:
            p75 = np.percentile(arr, self.EXCELLENT_PERCENTILE)
            p50 = np.percentile(arr, self.GOOD_PERCENTILE)
            p25 = np.percentile(arr, self.MARGINAL_PERCENTILE)
            mean_vrp = float(np.mean(arr))
            std_vrp = float(np.std(arr))
        else:
            p75 = p50 = p25 = 0
            mean_vrp = std_vrp = 0

        # Blend with static defaults
        excellent = blend_weight * p75 + (1 - blend_weight) * self.DEFAULT_EXCELLENT
        good = blend_weight * p50 + (1 - blend_weight) * self.DEFAULT_GOOD
        marginal = blend_weight * p25 + (1 - blend_weight) * self.DEFAULT_MARGINAL

        return RollingThresholds(
            excellent=excellent,
            good=good,
            marginal=marginal,
            sample_size=n,
            window_days=window_days,
            as_of_date=as_of_date,
            mean_vrp=mean_vrp,
            std_vrp=std_vrp,
        )

    def _static_fallback(
        self,
        window_days: int,
        as_of_date: date
    ) -> RollingThresholds:
        """Return static thresholds when no data available."""
        return RollingThresholds(
            excellent=self.DEFAULT_EXCELLENT,
            good=self.DEFAULT_GOOD,
            marginal=self.DEFAULT_MARGINAL,
            sample_size=0,
            window_days=window_days,
            as_of_date=as_of_date,
            mean_vrp=0.0,
            std_vrp=0.0,
        )

    def get_sector_thresholds(
        self,
        sector: str,
        window_days: int = 90,
        as_of_date: Optional[date] = None
    ) -> SectorThresholds:
        """
        Calculate sector-specific VRP thresholds.

        Different sectors have different typical move patterns.
        This normalizes thresholds relative to sector peers.

        Args:
            sector: Sector name (e.g., "Technology", "Healthcare")
            window_days: Rolling window in days
            as_of_date: As of date (default: today)

        Returns:
            SectorThresholds for the given sector
        """
        if as_of_date is None:
            as_of_date = date.today()

        start_date = as_of_date - timedelta(days=window_days)

        if not self.db_path.exists():
            return self._default_sector_thresholds(sector)

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Query VRP ratios for tickers in this sector
            cursor.execute("""
                SELECT iv.vrp_ratio, hm.close_move_pct
                FROM iv_log iv
                LEFT JOIN ticker_metadata tm ON iv.ticker = tm.ticker
                LEFT JOIN historical_moves hm ON iv.ticker = hm.ticker
                WHERE iv.scan_date >= ?
                  AND iv.scan_date <= ?
                  AND iv.vrp_ratio IS NOT NULL
                  AND iv.vrp_ratio > 0
                  AND tm.sector = ?
            """, (start_date.isoformat(), as_of_date.isoformat(), sector))

            rows = cursor.fetchall()
            conn.close()

            if not rows or len(rows) < 10:
                logger.warning(f"Insufficient data for sector {sector}, using defaults")
                return self._default_sector_thresholds(sector)

            vrp_values = [row['vrp_ratio'] for row in rows]
            move_values = [row['close_move_pct'] for row in rows if row['close_move_pct']]

            arr = np.array(vrp_values)

            return SectorThresholds(
                sector=sector,
                excellent=float(np.percentile(arr, 75)),
                good=float(np.percentile(arr, 50)),
                marginal=float(np.percentile(arr, 25)),
                sample_size=len(arr),
                mean_move=float(np.mean(move_values)) if move_values else 0.0,
            )

        except Exception as e:
            logger.error(f"Error calculating sector thresholds for {sector}: {e}")
            return self._default_sector_thresholds(sector)

    def _default_sector_thresholds(self, sector: str) -> SectorThresholds:
        """Return default sector thresholds."""
        return SectorThresholds(
            sector=sector,
            excellent=self.DEFAULT_EXCELLENT,
            good=self.DEFAULT_GOOD,
            marginal=self.DEFAULT_MARGINAL,
            sample_size=0,
            mean_move=0.0,
        )

    def get_threshold_stats(self, window_days: int = 90) -> dict:
        """
        Get statistics about VRP distribution for analysis.

        Returns:
            Dict with distribution statistics
        """
        thresholds = self.get_rolling_thresholds(window_days)

        return {
            'window_days': window_days,
            'sample_size': thresholds.sample_size,
            'thresholds': {
                'excellent': thresholds.excellent,
                'good': thresholds.good,
                'marginal': thresholds.marginal,
            },
            'distribution': {
                'mean': thresholds.mean_vrp,
                'std': thresholds.std_vrp,
            },
            'is_reliable': thresholds.is_sufficient_data(self.MIN_SAMPLES),
        }
