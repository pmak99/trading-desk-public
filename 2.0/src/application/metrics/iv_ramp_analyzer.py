"""
IV Ramp Analyzer - Pre-Earnings Implied Volatility Build-up Tracking.

Analyzes how implied volatility builds up before earnings announcements.
This provides additional signal quality information:

- Fast IV ramp: Market recently priced in uncertainty → more crush expected
- Slow IV ramp: Already priced in → may be efficiently priced
- IV already elevated: Less edge potential

Usage:
    analyzer = IVRampAnalyzer(db_path, tradier_client)

    # Analyze IV build-up pattern
    ramp = analyzer.analyze_iv_ramp(
        ticker="AAPL",
        earnings_date=date(2025, 1, 30),
        expiration=date(2025, 1, 31)
    )
    # ramp.ramp_velocity = IV change per day
    # ramp.ramp_pattern = "accelerating" | "steady" | "decelerating" | "flat"
    # ramp.days_of_ramp = how long IV has been building
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
class IVSnapshot:
    """Single IV observation."""
    date: date
    atm_iv: float
    days_to_earnings: int
    straddle_cost: float
    stock_price: float


@dataclass
class IVRampAnalysis:
    """
    Analysis of pre-earnings IV build-up pattern.

    Attributes:
        ticker: Stock symbol
        earnings_date: Earnings announcement date
        current_iv: Current ATM IV
        iv_5d_ago: IV 5 trading days ago (if available)
        iv_10d_ago: IV 10 trading days ago (if available)
        ramp_velocity: Average IV increase per day (%/day)
        ramp_pattern: Classification of ramp behavior
        ramp_acceleration: Is IV build-up accelerating?
        days_of_ramp: Days of continuous IV increase
        ramp_percentile: How this ramp compares historically (0-100)
        ramp_strength: Strength classification (weak/moderate/strong)
        crush_expectation: Expected crush based on ramp pattern
        confidence: Confidence in the analysis (based on data quality)
    """
    ticker: str
    earnings_date: date
    current_iv: float
    iv_5d_ago: Optional[float]
    iv_10d_ago: Optional[float]
    ramp_velocity: float
    ramp_pattern: str
    ramp_acceleration: float
    days_of_ramp: int
    ramp_percentile: float
    ramp_strength: str
    crush_expectation: str
    confidence: float


class IVRampAnalyzer:
    """
    Analyzes IV build-up patterns before earnings.

    Key insight: The pattern of IV increase before earnings
    provides signal about expected IV crush magnitude.

    - Fast late ramp → More crush expected (recently priced in)
    - Slow steady ramp → Market has time to adjust, less surprise
    - Already elevated → May be efficiently priced
    """

    # Ramp velocity thresholds (% IV per day)
    WEAK_RAMP = 0.5  # < 0.5% per day
    MODERATE_RAMP = 1.5  # 0.5-1.5% per day
    STRONG_RAMP = 3.0  # > 1.5% per day

    # Lookback periods
    SHORT_LOOKBACK = 5  # 5 trading days
    LONG_LOOKBACK = 10  # 10 trading days

    def __init__(self, db_path: Path | str, options_provider=None):
        """
        Initialize IV ramp analyzer.

        Args:
            db_path: Path to database for historical IV data
            options_provider: Optional Tradier/options API for live IV
        """
        self.db_path = Path(db_path)
        self.options_provider = options_provider

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def analyze_iv_ramp(
        self,
        ticker: str,
        earnings_date: date,
        expiration: date,
        current_iv: Optional[float] = None
    ) -> IVRampAnalysis:
        """
        Analyze IV ramp pattern for upcoming earnings.

        Args:
            ticker: Stock symbol
            earnings_date: Earnings date
            expiration: Options expiration date
            current_iv: Current IV (if already known)

        Returns:
            IVRampAnalysis with ramp characteristics
        """
        logger.info(f"Analyzing IV ramp for {ticker} (earnings: {earnings_date})")

        # Get historical IV snapshots
        snapshots = self._get_iv_history(ticker, earnings_date, expiration)

        if not snapshots:
            logger.warning(f"No IV history for {ticker}, limited analysis")
            return self._minimal_analysis(ticker, earnings_date, current_iv)

        # Sort by date (oldest first)
        snapshots.sort(key=lambda x: x.date)

        # Get current IV (most recent snapshot or provided value)
        if current_iv is None and snapshots:
            current_iv = snapshots[-1].atm_iv

        # Calculate ramp metrics
        ramp_velocity = self._calculate_ramp_velocity(snapshots)
        ramp_pattern = self._classify_ramp_pattern(snapshots)
        acceleration = self._calculate_acceleration(snapshots)
        days_of_ramp = self._calculate_continuous_ramp_days(snapshots)

        # Get IV at specific lookback points
        iv_5d = self._get_iv_at_lookback(snapshots, self.SHORT_LOOKBACK)
        iv_10d = self._get_iv_at_lookback(snapshots, self.LONG_LOOKBACK)

        # Calculate ramp percentile (vs historical ramps)
        ramp_percentile = self._calculate_ramp_percentile(ticker, ramp_velocity)

        # Classify strength
        ramp_strength = self._classify_ramp_strength(ramp_velocity)

        # Determine crush expectation
        crush_expectation = self._estimate_crush_expectation(
            ramp_velocity, ramp_pattern, acceleration
        )

        # Calculate confidence
        confidence = self._calculate_confidence(snapshots)

        return IVRampAnalysis(
            ticker=ticker,
            earnings_date=earnings_date,
            current_iv=current_iv or 0.0,
            iv_5d_ago=iv_5d,
            iv_10d_ago=iv_10d,
            ramp_velocity=ramp_velocity,
            ramp_pattern=ramp_pattern,
            ramp_acceleration=acceleration,
            days_of_ramp=days_of_ramp,
            ramp_percentile=ramp_percentile,
            ramp_strength=ramp_strength,
            crush_expectation=crush_expectation,
            confidence=confidence,
        )

    def _get_iv_history(
        self,
        ticker: str,
        earnings_date: date,
        expiration: date
    ) -> List[IVSnapshot]:
        """Get historical IV snapshots from database."""
        if not self.db_path.exists():
            return []

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Look for IV logs in the 2 weeks before earnings
            start_date = earnings_date - timedelta(days=14)

            cursor.execute("""
                SELECT
                    scan_date,
                    implied_move_pct,
                    straddle_cost,
                    stock_price
                FROM iv_log
                WHERE ticker = ?
                  AND scan_date >= ?
                  AND scan_date <= ?
                ORDER BY scan_date
            """, (ticker, start_date.isoformat(), earnings_date.isoformat()))

            rows = cursor.fetchall()
            conn.close()

            snapshots = []
            for row in rows:
                scan_date = date.fromisoformat(row['scan_date'])
                days_to_earnings = (earnings_date - scan_date).days

                # Convert implied move to IV estimate
                # Implied move ≈ straddle / stock_price
                # ATM IV ≈ implied_move * sqrt(252 / DTE)
                implied_move = row['implied_move_pct']
                dte = max(1, (expiration - scan_date).days)
                atm_iv = implied_move * np.sqrt(252 / dte)

                snapshots.append(IVSnapshot(
                    date=scan_date,
                    atm_iv=atm_iv,
                    days_to_earnings=days_to_earnings,
                    straddle_cost=row['straddle_cost'],
                    stock_price=row['stock_price'],
                ))

            return snapshots

        except Exception as e:
            logger.error(f"Error getting IV history for {ticker}: {e}")
            return []

    def _calculate_ramp_velocity(self, snapshots: List[IVSnapshot]) -> float:
        """
        Calculate average IV increase per day.

        Returns:
            IV change per day (% points)
        """
        if len(snapshots) < 2:
            return 0.0

        # Use linear regression for velocity
        days = np.array([s.days_to_earnings for s in snapshots])
        ivs = np.array([s.atm_iv for s in snapshots])

        if len(days) < 2:
            return 0.0

        # Fit line: IV = slope * days_to_earnings + intercept
        # Note: days_to_earnings decreases as we approach earnings
        # So negative slope means IV is increasing
        try:
            slope, _ = np.polyfit(days, ivs, 1)
            # Negate because days_to_earnings counts down
            velocity = -slope
            return float(velocity)
        except Exception:
            return 0.0

    def _classify_ramp_pattern(self, snapshots: List[IVSnapshot]) -> str:
        """
        Classify the shape of the IV ramp.

        Returns:
            "accelerating" - IV build-up speeding up
            "steady" - Consistent IV increase
            "decelerating" - IV build-up slowing
            "flat" - Little to no IV build-up
            "volatile" - Erratic IV changes
        """
        if len(snapshots) < 3:
            return "insufficient_data"

        ivs = [s.atm_iv for s in snapshots]

        # Calculate differences
        diffs = np.diff(ivs)

        if len(diffs) < 2:
            if np.mean(diffs) > 0.5:
                return "steady"
            return "flat"

        # Calculate acceleration (change in differences)
        accel = np.diff(diffs)

        mean_diff = np.mean(diffs)
        mean_accel = np.mean(accel)
        diff_std = np.std(diffs)

        # High volatility in changes
        if diff_std > abs(mean_diff) * 2:
            return "volatile"

        # Nearly flat
        if abs(mean_diff) < 0.3:
            return "flat"

        # Accelerating or decelerating
        if mean_accel > 0.1:
            return "accelerating"
        elif mean_accel < -0.1:
            return "decelerating"
        else:
            return "steady"

    def _calculate_acceleration(self, snapshots: List[IVSnapshot]) -> float:
        """Calculate IV ramp acceleration."""
        if len(snapshots) < 3:
            return 0.0

        ivs = [s.atm_iv for s in snapshots]
        diffs = np.diff(ivs)

        if len(diffs) < 2:
            return 0.0

        # Average second derivative
        accel = np.diff(diffs)
        return float(np.mean(accel))

    def _calculate_continuous_ramp_days(self, snapshots: List[IVSnapshot]) -> int:
        """Count consecutive days of IV increase."""
        if len(snapshots) < 2:
            return 0

        # Count from most recent backward
        count = 0
        ivs = [s.atm_iv for s in reversed(snapshots)]

        for i in range(1, len(ivs)):
            if ivs[i-1] > ivs[i]:  # IV was increasing
                count += 1
            else:
                break

        return count

    def _get_iv_at_lookback(
        self,
        snapshots: List[IVSnapshot],
        days_ago: int
    ) -> Optional[float]:
        """Get IV from approximately N days ago."""
        if not snapshots:
            return None

        target_date = snapshots[-1].date - timedelta(days=days_ago)

        # Find closest snapshot to target date
        closest = None
        min_diff = float('inf')

        for s in snapshots:
            diff = abs((s.date - target_date).days)
            if diff < min_diff:
                min_diff = diff
                closest = s

        # Only use if within 2 days of target
        if closest and min_diff <= 2:
            return closest.atm_iv

        return None

    def _calculate_ramp_percentile(
        self,
        ticker: str,
        velocity: float
    ) -> float:
        """
        Calculate where this ramp ranks historically.

        Compares current ramp velocity to historical ramps for similar tickers.
        """
        # For now, use heuristic percentiles
        # TODO: Could query historical ramp velocities from database

        if velocity <= 0:
            return 10.0
        elif velocity < self.WEAK_RAMP:
            return 25.0 + 25.0 * (velocity / self.WEAK_RAMP)
        elif velocity < self.MODERATE_RAMP:
            return 50.0 + 25.0 * ((velocity - self.WEAK_RAMP) / (self.MODERATE_RAMP - self.WEAK_RAMP))
        elif velocity < self.STRONG_RAMP:
            return 75.0 + 20.0 * ((velocity - self.MODERATE_RAMP) / (self.STRONG_RAMP - self.MODERATE_RAMP))
        else:
            return min(99.0, 95.0 + 4.0 * ((velocity - self.STRONG_RAMP) / self.STRONG_RAMP))

    def _classify_ramp_strength(self, velocity: float) -> str:
        """Classify ramp velocity strength."""
        if velocity < self.WEAK_RAMP:
            return "weak"
        elif velocity < self.MODERATE_RAMP:
            return "moderate"
        else:
            return "strong"

    def _estimate_crush_expectation(
        self,
        velocity: float,
        pattern: str,
        acceleration: float
    ) -> str:
        """
        Estimate expected IV crush based on ramp characteristics.

        Higher velocity + accelerating pattern → More crush expected
        Low velocity + flat pattern → Less crush expected
        """
        score = 0

        # Velocity contribution
        if velocity >= self.STRONG_RAMP:
            score += 3
        elif velocity >= self.MODERATE_RAMP:
            score += 2
        elif velocity >= self.WEAK_RAMP:
            score += 1

        # Pattern contribution
        if pattern == "accelerating":
            score += 2
        elif pattern == "steady":
            score += 1
        elif pattern == "decelerating":
            score -= 1

        # Acceleration contribution
        if acceleration > 0.2:
            score += 1
        elif acceleration < -0.2:
            score -= 1

        # Map score to expectation
        if score >= 5:
            return "high_crush"
        elif score >= 3:
            return "moderate_crush"
        elif score >= 1:
            return "low_crush"
        else:
            return "minimal_crush"

    def _calculate_confidence(self, snapshots: List[IVSnapshot]) -> float:
        """
        Calculate confidence in the analysis.

        More data points and less volatility = higher confidence.
        """
        if not snapshots:
            return 0.0

        n = len(snapshots)

        # Data quantity factor (asymptotic to 1.0)
        quantity_factor = n / (n + 3)

        # Data quality factor (lower volatility = higher confidence)
        if n >= 2:
            ivs = [s.atm_iv for s in snapshots]
            cv = np.std(ivs) / np.mean(ivs) if np.mean(ivs) > 0 else 1.0
            quality_factor = max(0.3, 1.0 - cv)
        else:
            quality_factor = 0.5

        # Time coverage factor
        if n >= 2:
            days_covered = (snapshots[-1].date - snapshots[0].date).days
            coverage_factor = min(1.0, days_covered / 10)  # 10 days = full coverage
        else:
            coverage_factor = 0.3

        confidence = quantity_factor * quality_factor * coverage_factor
        return float(min(1.0, confidence))

    def _minimal_analysis(
        self,
        ticker: str,
        earnings_date: date,
        current_iv: Optional[float]
    ) -> IVRampAnalysis:
        """Return minimal analysis when no history available."""
        return IVRampAnalysis(
            ticker=ticker,
            earnings_date=earnings_date,
            current_iv=current_iv or 0.0,
            iv_5d_ago=None,
            iv_10d_ago=None,
            ramp_velocity=0.0,
            ramp_pattern="unknown",
            ramp_acceleration=0.0,
            days_of_ramp=0,
            ramp_percentile=50.0,
            ramp_strength="unknown",
            crush_expectation="unknown",
            confidence=0.0,
        )

    def log_iv_snapshot(
        self,
        ticker: str,
        earnings_date: date,
        atm_iv: float,
        straddle_cost: float,
        stock_price: float
    ) -> bool:
        """
        Log an IV snapshot to the database.

        Should be called during each scan to build IV history.
        """
        # This is handled by the existing iv_log table
        # This method is here for documentation purposes
        # The analyzer reads from iv_log which is already populated by scan.py
        return True
