"""
Position sizing service using Kelly Criterion.

Calculates optimal position sizes based on edge, probability,
and risk management constraints.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionSizeInput:
    """
    Input parameters for position sizing calculation.

    Attributes:
        ticker: Stock symbol
        vrp_ratio: Implied move / Historical mean move ratio
        consistency_score: Consistency score (0-1) from consistency analyzer
        historical_win_rate: Historical win rate for this setup (optional)
        num_historical_trades: Number of historical trades (for confidence)
    """

    ticker: str
    vrp_ratio: float
    consistency_score: float
    historical_win_rate: Optional[float] = None
    num_historical_trades: Optional[int] = None


@dataclass(frozen=True)
class PositionSize:
    """
    Position sizing recommendation.

    Attributes:
        ticker: Stock symbol
        kelly_fraction: Full Kelly Criterion fraction (0-1)
        recommended_fraction: Conservative Kelly fraction (typically 0.25x)
        position_size_pct: Recommended position size as % of account
        max_loss_pct: Maximum expected loss as % of account
        risk_adjusted: Whether position was capped by risk limits
        confidence: Confidence score (0-1) based on data quality
    """

    ticker: str
    kelly_fraction: float
    recommended_fraction: float
    position_size_pct: float
    max_loss_pct: float
    risk_adjusted: bool
    confidence: float


class PositionSizer:
    """
    Kelly Criterion-based position sizing with risk limits.

    The Kelly Criterion calculates optimal position size to maximize
    long-term growth given an edge and probability of success.

    Formula: f = (p * b - q) / b
    where:
        f = fraction of capital to bet
        p = probability of win
        b = odds (profit/loss ratio)
        q = probability of loss (1-p)

    For safety, we use Fractional Kelly (typically 0.25x to 0.5x of full Kelly)
    to reduce volatility and account for estimation errors.
    """

    def __init__(
        self,
        fractional_kelly: float = 0.25,
        max_position_pct: float = 0.05,
        max_loss_pct: float = 0.02,
        min_confidence: float = 0.4,
    ):
        """
        Initialize position sizer.

        Args:
            fractional_kelly: Fraction of Kelly to use (default 0.25 = quarter Kelly)
            max_position_pct: Maximum position size as % of account (default 5%)
            max_loss_pct: Maximum acceptable loss per trade (default 2%)
            min_confidence: Minimum confidence score to trade (default 0.4)
        """
        self.fractional_kelly = fractional_kelly
        self.max_position_pct = max_position_pct
        self.max_loss_pct = max_loss_pct
        self.min_confidence = min_confidence

    def calculate_position_size(
        self,
        input_params: PositionSizeInput,
    ) -> PositionSize:
        """
        Calculate optimal position size for a trade.

        Args:
            input_params: PositionSizeInput containing all calculation parameters

        Returns:
            PositionSize with recommendation and risk metrics
        """
        ticker = input_params.ticker
        vrp_ratio = input_params.vrp_ratio
        consistency_score = input_params.consistency_score
        historical_win_rate = input_params.historical_win_rate
        num_historical_trades = input_params.num_historical_trades

        # Estimate win probability from consistency and VRP
        # Higher consistency = higher probability of predictable move
        # Higher VRP ratio = higher probability of profitable trade
        if historical_win_rate is not None:
            # Use actual historical win rate if available
            p = historical_win_rate
        else:
            # Estimate from consistency and VRP ratio
            # Base probability: 50%
            # Consistency boost: +25% max (0.5 + consistency * 0.25)
            # VRP boost: +10% if VRP > 1.5, +15% if VRP > 2.0
            base_p = 0.50
            consistency_boost = consistency_score * 0.25
            vrp_boost = 0.10 if vrp_ratio >= 1.5 else 0.0
            vrp_boost += 0.05 if vrp_ratio >= 2.0 else 0.0

            p = min(0.75, base_p + consistency_boost + vrp_boost)

        # Estimate odds (profit/loss ratio)
        # For IV crush trades, typical profit is ~50% of premium, loss is 100% of premium
        # So odds are approximately 0.5:1
        # Note: Odds are determined by trade structure, NOT by VRP
        # VRP already affects probability above - don't double-count
        b = 0.5  # Fixed based on typical IV crush trade structure

        # Calculate Kelly fraction
        # f = (p * b - q) / b
        q = 1 - p
        kelly_fraction = (p * b - q) / b if b > 0 else 0.0

        # Apply bounds (0 to 1)
        kelly_fraction = max(0.0, min(1.0, kelly_fraction))

        # Apply fractional Kelly for safety
        recommended_fraction = kelly_fraction * self.fractional_kelly

        # Calculate confidence based on data quality
        # Higher consistency = higher confidence
        # More historical trades = higher confidence
        confidence = consistency_score
        if num_historical_trades is not None:
            # Boost confidence if we have many historical examples
            sample_confidence = min(1.0, num_historical_trades / 20)
            confidence = (confidence + sample_confidence) / 2

        # Apply risk limits
        risk_adjusted = False

        # Cap at max position size
        if recommended_fraction > self.max_position_pct:
            recommended_fraction = self.max_position_pct
            risk_adjusted = True

        # Reduce if confidence is low
        if confidence < self.min_confidence:
            recommended_fraction *= (confidence / self.min_confidence)
            risk_adjusted = True

        # Calculate max loss (assume 100% loss of position in worst case)
        max_loss_pct = recommended_fraction

        # Cap at max loss limit
        if max_loss_pct > self.max_loss_pct:
            recommended_fraction *= (self.max_loss_pct / max_loss_pct)
            max_loss_pct = self.max_loss_pct
            risk_adjusted = True

        # Convert to percentage for display
        position_size_pct = recommended_fraction * 100

        result = PositionSize(
            ticker=ticker,
            kelly_fraction=kelly_fraction,
            recommended_fraction=recommended_fraction,
            position_size_pct=position_size_pct,
            max_loss_pct=max_loss_pct * 100,
            risk_adjusted=risk_adjusted,
            confidence=confidence,
        )

        logger.info(
            f"{ticker}: Position size {position_size_pct:.2f}% "
            f"(Kelly: {kelly_fraction:.3f}, p={p:.2f}, b={b:.2f}, conf={confidence:.2f})"
        )

        return result

    def calculate_portfolio_allocation(
        self,
        positions: list[PositionSize],
        max_total_exposure_pct: float = 0.20,
    ) -> list[PositionSize]:
        """
        Adjust individual position sizes to respect portfolio-level limits.

        If sum of individual positions exceeds max total exposure,
        scale down all positions proportionally.

        Args:
            positions: List of PositionSize recommendations
            max_total_exposure_pct: Maximum total portfolio exposure (default 20%)

        Returns:
            Adjusted list of PositionSize objects
        """
        if not positions:
            return []

        # Calculate total exposure
        total_exposure = sum(p.position_size_pct for p in positions)

        # Check if scaling needed
        if total_exposure > max_total_exposure_pct * 100:
            scale_factor = (max_total_exposure_pct * 100) / total_exposure
            logger.info(
                f"Portfolio exposure {total_exposure:.1f}% exceeds limit "
                f"{max_total_exposure_pct * 100:.1f}%. Scaling by {scale_factor:.3f}"
            )

            # Create scaled positions
            adjusted_positions = []
            for pos in positions:
                adjusted = PositionSize(
                    ticker=pos.ticker,
                    kelly_fraction=pos.kelly_fraction,
                    recommended_fraction=pos.recommended_fraction * scale_factor,
                    position_size_pct=pos.position_size_pct * scale_factor,
                    max_loss_pct=pos.max_loss_pct * scale_factor,
                    risk_adjusted=True,  # Mark as adjusted
                    confidence=pos.confidence,
                )
                adjusted_positions.append(adjusted)

            return adjusted_positions
        else:
            logger.info(f"Portfolio exposure {total_exposure:.1f}% within limits")
            return positions
