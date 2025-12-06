"""
Adaptive VRP Threshold Calculator.

Adjusts VRP thresholds based on current VIX regime.
In elevated volatility environments, we require higher VRP ratios
to compensate for increased risk and potential for larger moves.

VIX Regime Adjustments:
- VIX < 15 (low): No adjustment (standard thresholds)
- VIX 15-20 (normal): No adjustment
- VIX 20-25 (normal_high): 10% higher thresholds
- VIX 25-30 (elevated): 20% higher thresholds
- VIX 30-35 (elevated_high): 30% higher thresholds
- VIX 35-40 (high): 50% higher thresholds
- VIX 40+ (extreme): Not recommended to trade

Rationale:
High VIX environments mean the market is already pricing in volatility.
To maintain edge, we need even higher VRP ratios to justify the trade.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from src.config.config import ThresholdsConfig
from src.application.metrics.market_conditions import MarketConditions
from src.domain.vix_regime import classify_vix_regime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdaptedThresholds:
    """
    VRP thresholds adjusted for current market regime.

    Attributes:
        vrp_excellent: Adjusted excellent threshold
        vrp_good: Adjusted good threshold
        vrp_marginal: Adjusted marginal threshold
        regime: Current VIX regime
        vix_level: Current VIX level
        adjustment_factor: Multiplier applied to base thresholds
        trade_recommended: Whether trading is recommended in current regime
    """
    vrp_excellent: float
    vrp_good: float
    vrp_marginal: float
    regime: str
    vix_level: float
    adjustment_factor: float
    trade_recommended: bool

    @property
    def is_adjusted(self) -> bool:
        """Return True if thresholds were adjusted from baseline."""
        return self.adjustment_factor > 1.0


class AdaptiveThresholdCalculator:
    """
    Calculate adaptive VRP thresholds based on VIX regime.

    In high volatility environments, we require higher VRP ratios
    because:
    1. Implied volatility is already elevated
    2. Actual moves can be larger than historical averages
    3. Risk of catastrophic loss increases
    4. Liquidity often degrades

    The adjustment is multiplicative - we multiply base thresholds
    by a regime-dependent factor.
    """

    # Regime adjustment factors
    # These multiply the base VRP thresholds
    REGIME_ADJUSTMENTS = {
        "very_low": 1.0,      # VIX < 12: No adjustment
        "low": 1.0,           # VIX 12-15: No adjustment
        "normal": 1.0,        # VIX 15-20: No adjustment
        "normal_high": 1.1,   # VIX 20-25: 10% higher thresholds
        "elevated": 1.2,      # VIX 25-30: 20% higher thresholds
        "elevated_high": 1.3, # VIX 30-35: 30% higher thresholds
        "high": 1.5,          # VIX 35-40: 50% higher thresholds
        "extreme": 2.0,       # VIX 40+: 100% higher (effectively avoid)
    }

    # Regimes where we recommend avoiding trades
    NO_TRADE_REGIMES = {"extreme"}

    def __init__(self, base_thresholds: ThresholdsConfig):
        """
        Initialize with base thresholds from configuration.

        Args:
            base_thresholds: ThresholdsConfig with baseline VRP values
        """
        self.base_thresholds = base_thresholds
        self._base_excellent = base_thresholds.vrp_excellent
        self._base_good = base_thresholds.vrp_good
        self._base_marginal = base_thresholds.vrp_marginal

        logger.debug(
            f"AdaptiveThresholdCalculator initialized with base thresholds: "
            f"excellent={self._base_excellent:.2f}, "
            f"good={self._base_good:.2f}, "
            f"marginal={self._base_marginal:.2f}"
        )

    def calculate(self, market_conditions: MarketConditions) -> AdaptedThresholds:
        """
        Calculate adapted thresholds based on current market conditions.

        Args:
            market_conditions: Current MarketConditions with VIX and regime

        Returns:
            AdaptedThresholds with adjusted values
        """
        regime = market_conditions.regime
        vix_level = market_conditions.vix_level.value

        # Get adjustment factor for this regime
        adjustment = self.REGIME_ADJUSTMENTS.get(regime, 1.0)

        # Calculate adjusted thresholds
        adjusted_excellent = self._base_excellent * adjustment
        adjusted_good = self._base_good * adjustment
        adjusted_marginal = self._base_marginal * adjustment

        # Determine if trading is recommended
        trade_recommended = regime not in self.NO_TRADE_REGIMES

        adapted = AdaptedThresholds(
            vrp_excellent=adjusted_excellent,
            vrp_good=adjusted_good,
            vrp_marginal=adjusted_marginal,
            regime=regime,
            vix_level=vix_level,
            adjustment_factor=adjustment,
            trade_recommended=trade_recommended,
        )

        if adapted.is_adjusted:
            logger.info(
                f"VRP thresholds adjusted for {regime} regime (VIX={vix_level:.1f}): "
                f"excellent={adjusted_excellent:.2f} (was {self._base_excellent:.2f}), "
                f"good={adjusted_good:.2f} (was {self._base_good:.2f}), "
                f"marginal={adjusted_marginal:.2f} (was {self._base_marginal:.2f})"
            )
        else:
            logger.debug(
                f"Using base thresholds for {regime} regime (VIX={vix_level:.1f})"
            )

        if not trade_recommended:
            logger.warning(
                f"Trading not recommended in {regime} regime (VIX={vix_level:.1f}). "
                "Consider waiting for volatility to normalize."
            )

        return adapted

    def calculate_from_vix(self, vix_level: float) -> AdaptedThresholds:
        """
        Calculate adapted thresholds directly from VIX level.

        Convenience method when MarketConditions object is not available.

        Args:
            vix_level: Current VIX level

        Returns:
            AdaptedThresholds with adjusted values
        """
        regime = self._classify_regime(vix_level)
        adjustment = self.REGIME_ADJUSTMENTS.get(regime, 1.0)
        trade_recommended = regime not in self.NO_TRADE_REGIMES

        return AdaptedThresholds(
            vrp_excellent=self._base_excellent * adjustment,
            vrp_good=self._base_good * adjustment,
            vrp_marginal=self._base_marginal * adjustment,
            regime=regime,
            vix_level=vix_level,
            adjustment_factor=adjustment,
            trade_recommended=trade_recommended,
        )

    def _classify_regime(self, vix_level: float) -> str:
        """
        Classify VIX level into regime category.

        Delegates to shared utility for consistency across application.

        Args:
            vix_level: Current VIX level

        Returns:
            Regime name string

        Raises:
            ValueError: If vix_level is negative
        """
        return classify_vix_regime(vix_level)

    def get_recommendation_with_adjustment(
        self,
        vrp_ratio: float,
        market_conditions: Optional[MarketConditions] = None,
        vix_level: Optional[float] = None,
    ) -> str:
        """
        Get VRP recommendation using adapted thresholds.

        Args:
            vrp_ratio: Calculated VRP ratio
            market_conditions: Optional MarketConditions (preferred)
            vix_level: Optional VIX level (fallback)

        Returns:
            Recommendation string: "EXCELLENT", "GOOD", "MARGINAL", or "SKIP"
        """
        if market_conditions is not None:
            adapted = self.calculate(market_conditions)
        elif vix_level is not None:
            adapted = self.calculate_from_vix(vix_level)
        else:
            # No market data - use base thresholds
            if vrp_ratio >= self._base_excellent:
                return "EXCELLENT"
            elif vrp_ratio >= self._base_good:
                return "GOOD"
            elif vrp_ratio >= self._base_marginal:
                return "MARGINAL"
            else:
                return "SKIP"

        # Check if trading is recommended
        if not adapted.trade_recommended:
            return "SKIP"

        # Apply adapted thresholds
        if vrp_ratio >= adapted.vrp_excellent:
            return "EXCELLENT"
        elif vrp_ratio >= adapted.vrp_good:
            return "GOOD"
        elif vrp_ratio >= adapted.vrp_marginal:
            return "MARGINAL"
        else:
            return "SKIP"
