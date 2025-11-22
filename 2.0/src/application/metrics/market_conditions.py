"""
Market Conditions Analyzer - VIX Regime Classification.

Classifies current market volatility regime using VIX (CBOE Volatility Index).
Helps adjust strategy selection and position sizing based on market environment.

VIX Regimes:
- Low volatility (VIX < 15): Complacent market, potential for vol expansion
- Normal volatility (VIX 15-25): Balanced market conditions
- Elevated volatility (VIX 25-35): Increased uncertainty
- High volatility (VIX > 35): Fear/panic, extreme moves possible
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from src.domain.types import Percentage
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.protocols import OptionsDataProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketConditions:
    """
    Current market conditions and volatility regime.

    Attributes:
        vix_level: Current VIX level (%)
        regime: Volatility regime classification
        regime_score: Normalized score (0-100, higher = more volatile)
        trend: Recent VIX trend (rising/falling/stable)
        analysis_time: When conditions were assessed

        # Regime-specific guidance
        strategy_adjustment: Recommended strategy adjustment
        position_size_multiplier: Suggested position size adjustment (0.5 - 1.5)
    """
    vix_level: Percentage
    regime: str
    regime_score: float
    trend: str
    analysis_time: datetime

    strategy_adjustment: str
    position_size_multiplier: float


class MarketConditionsAnalyzer:
    """
    Analyze market conditions using VIX levels.

    VIX represents implied volatility of S&P 500 index options.
    It's often called the "fear gauge" as it spikes during market stress.

    Historical context:
    - Average VIX: ~17-20
    - 2017-2019: Often below 15 (complacent market)
    - COVID crash (Mar 2020): Peaked at 82
    - 2022-2023: Mostly 15-30 range
    """

    def __init__(
        self,
        provider: OptionsDataProvider,
        vix_symbol: str = "VIX",
    ):
        """
        Initialize market conditions analyzer.

        Args:
            provider: Options data provider (must support VIX queries)
            vix_symbol: Symbol for VIX (default "VIX")
        """
        self.provider = provider
        self.vix_symbol = vix_symbol

    def get_current_conditions(self) -> Result[MarketConditions, AppError]:
        """
        Get current market conditions and VIX regime.

        Returns:
            Result with MarketConditions or AppError
        """
        logger.info("Analyzing market conditions (VIX regime)")

        # Get VIX data
        vix_result = self._get_vix_level()
        if vix_result.is_err:
            return Err(vix_result.error)

        vix_level = vix_result.value

        # Classify regime
        regime = self._classify_regime(vix_level.value)

        # Calculate regime score (0-100)
        regime_score = self._calculate_regime_score(vix_level.value)

        # Determine trend (would need historical data - simplified for now)
        trend = self._estimate_trend(vix_level.value)

        # Get strategy adjustment guidance
        strategy_adjustment = self._get_strategy_adjustment(regime)
        position_multiplier = self._get_position_size_multiplier(regime)

        conditions = MarketConditions(
            vix_level=vix_level,
            regime=regime,
            regime_score=regime_score,
            trend=trend,
            analysis_time=datetime.now(),
            strategy_adjustment=strategy_adjustment,
            position_size_multiplier=position_multiplier,
        )

        logger.info(
            f"Market conditions: VIX={vix_level.value:.1f}% ({regime}), "
            f"trend={trend}, size_adj={position_multiplier:.2f}x"
        )

        return Ok(conditions)

    def _get_vix_level(self) -> Result[Percentage, AppError]:
        """
        Fetch current VIX level.

        Returns:
            Result with VIX as Percentage
        """
        # Try to get VIX from provider
        # Most providers support VIX as a regular ticker
        try:
            price_result = self.provider.get_stock_price(self.vix_symbol)
            if price_result.is_err:
                return Err(price_result.error)

            price = price_result.value
            vix_level = Percentage(float(price.amount))

            # Validate VIX level is reasonable (5-100 range)
            if vix_level.value < 5 or vix_level.value > 100:
                return Err(
                    AppError(
                        ErrorCode.INVALID,
                        f"VIX level {vix_level.value:.1f} outside valid range (5-100)"
                    )
                )

            return Ok(vix_level)

        except Exception as e:
            return Err(
                AppError(
                    ErrorCode.EXTERNAL,
                    f"Failed to fetch VIX: {str(e)}"
                )
            )

    def _classify_regime(self, vix_level: float) -> str:
        """
        Classify volatility regime based on VIX level.

        Args:
            vix_level: Current VIX level

        Returns:
            Regime name (low/normal/elevated/high)
        """
        if vix_level < 12:
            return "very_low"
        elif vix_level < 15:
            return "low"
        elif vix_level < 20:
            return "normal"
        elif vix_level < 25:
            return "normal_high"
        elif vix_level < 30:
            return "elevated"
        elif vix_level < 35:
            return "elevated_high"
        elif vix_level < 40:
            return "high"
        else:
            return "extreme"

    def _calculate_regime_score(self, vix_level: float) -> float:
        """
        Calculate normalized regime score (0-100).

        Maps VIX levels to 0-100 scale:
        - VIX 10 = score 0 (very calm)
        - VIX 20 = score 50 (normal)
        - VIX 40+ = score 100 (extreme)

        Args:
            vix_level: Current VIX level

        Returns:
            Score between 0 and 100
        """
        # Linear mapping with saturation at 40
        score = min(100, max(0, (vix_level - 10) / 30 * 100))
        return score

    def _estimate_trend(self, current_vix: float) -> str:
        """
        Estimate VIX trend direction.

        Note: This is simplified - proper implementation would compare
        current VIX to recent averages (5-day, 20-day moving averages).

        For now, uses heuristics based on current level.

        Args:
            current_vix: Current VIX level

        Returns:
            Trend direction (rising/falling/stable)
        """
        # Simplified: In low vol environments, VIX tends to mean-revert up
        # In high vol environments, VIX tends to decline back to normal
        if current_vix < 13:
            return "mean_reverting_up"
        elif current_vix > 30:
            return "mean_reverting_down"
        else:
            return "stable"

    def _get_strategy_adjustment(self, regime: str) -> str:
        """
        Get recommended strategy adjustment for regime.

        Args:
            regime: Volatility regime

        Returns:
            Strategy adjustment guidance
        """
        adjustments = {
            "very_low": "Favor neutral strategies (iron condors), watch for vol expansion",
            "low": "Standard approach, slight preference for selling premium",
            "normal": "Standard approach, all strategies viable",
            "normal_high": "Standard approach, monitor vol changes",
            "elevated": "Consider tighter spreads, reduce size",
            "elevated_high": "Use conservative strikes, reduce position size",
            "high": "Wait for better conditions or use minimal size",
            "extreme": "Avoid new positions, wait for vol to normalize",
        }
        return adjustments.get(regime, "Monitor conditions closely")

    def _get_position_size_multiplier(self, regime: str) -> float:
        """
        Get position size adjustment multiplier for regime.

        Args:
            regime: Volatility regime

        Returns:
            Multiplier for position size (0.5 - 1.5)
        """
        multipliers = {
            "very_low": 1.0,      # Normal size, but watch for vol spike
            "low": 1.1,           # Slightly larger (good selling environment)
            "normal": 1.0,        # Standard size
            "normal_high": 0.9,   # Slightly smaller
            "elevated": 0.75,     # Reduce size 25%
            "elevated_high": 0.6, # Reduce size 40%
            "high": 0.5,          # Half size
            "extreme": 0.25,      # Minimal size or avoid
        }
        return multipliers.get(regime, 1.0)

    def should_trade_in_regime(self, regime: str) -> bool:
        """
        Determine if we should trade in current regime.

        Args:
            regime: Volatility regime

        Returns:
            True if favorable for trading, False if should avoid
        """
        # Avoid trading in extreme volatility
        return regime not in ["extreme"]

    def get_regime_risk_premium(self, regime: str) -> float:
        """
        Get additional VRP threshold required for regime.

        In elevated vol regimes, require higher VRP ratios to compensate
        for increased risk.

        Args:
            regime: Volatility regime

        Returns:
            Additional VRP ratio required (0.0 - 0.5)
        """
        risk_premiums = {
            "very_low": 0.0,
            "low": 0.0,
            "normal": 0.0,
            "normal_high": 0.1,     # Require VRP 0.1 higher
            "elevated": 0.2,        # Require VRP 0.2 higher
            "elevated_high": 0.3,   # Require VRP 0.3 higher
            "high": 0.5,            # Require VRP 0.5 higher
            "extreme": 1.0,         # Effectively avoid
        }
        return risk_premiums.get(regime, 0.0)
