"""
Scoring configuration for ticker selection and ranking.

Defines weight configurations for A/B testing to find optimal
ticker selection criteria based on multiple factors.
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class ScoringWeights:
    """
    Weights for composite scoring algorithm.

    All weights should sum to 1.0 for interpretability.
    Scores are on 0-100 scale, then weighted and summed.
    """

    # Core metrics
    vrp_weight: float  # VRP ratio importance (implied vs historical move)
    consistency_weight: float  # Historical consistency importance
    skew_weight: float  # Skew favorability importance
    liquidity_weight: float  # Liquidity (OI, volume, spreads) importance

    # Validation
    def __post_init__(self):
        total = (
            self.vrp_weight
            + self.consistency_weight
            + self.skew_weight
            + self.liquidity_weight
        )
        if not 0.99 <= total <= 1.01:  # Allow small floating point error
            raise ValueError(f"Weights must sum to 1.0, got {total}")

        # Individual weight validation
        for name, weight in [
            ("vrp_weight", self.vrp_weight),
            ("consistency_weight", self.consistency_weight),
            ("skew_weight", self.skew_weight),
            ("liquidity_weight", self.liquidity_weight),
        ]:
            if not 0.0 <= weight <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1, got {weight}")


@dataclass(frozen=True)
class ScoringThresholds:
    """
    Thresholds for converting raw metrics to scores.

    These define the breakpoints for excellent/good/marginal ratings.
    """

    # VRP ratio thresholds (updated based on comprehensive backtesting)
    # Original values (7.0/4.0) were overfitted - updated to research-backed thresholds
    vrp_excellent: float = 2.0  # Excellent edge (conservative baseline)
    vrp_good: float = 1.5  # Strong edge
    vrp_marginal: float = 1.2  # Baseline edge (VRP exists)

    # Consistency thresholds (higher is better, 0-1 scale)
    consistency_excellent: float = 0.8  # Very predictable moves
    consistency_good: float = 0.6  # Reasonably predictable
    consistency_marginal: float = 0.4  # Less predictable

    # Skew thresholds (neutral is best for straddles)
    skew_neutral_range: float = 0.15  # Within ±15% is neutral
    skew_moderate_range: float = 0.35  # ±15-35% is moderate
    # >35% is extreme skew

    # Liquidity thresholds
    min_open_interest: int = 100  # Minimum OI for acceptable
    good_open_interest: int = 500  # Good liquidity
    excellent_open_interest: int = 1000  # Excellent liquidity

    max_spread_excellent: float = 5.0  # Spread ≤5% = excellent
    max_spread_good: float = 10.0  # Spread ≤10% = good
    max_spread_marginal: float = 15.0  # Spread ≤15% = marginal

    min_volume: int = 50  # Minimum daily volume
    good_volume: int = 100  # Good volume
    excellent_volume: int = 500  # Excellent volume

    # Overall selection threshold
    min_composite_score: float = 60.0  # Minimum score to consider (0-100)


@dataclass(frozen=True)
class ScoringConfig:
    """
    Complete scoring configuration.

    Combines weights and thresholds for a specific strategy.
    """

    name: str  # Config name (e.g., "VRP-Dominant", "Balanced")
    description: str  # Brief description
    weights: ScoringWeights
    thresholds: ScoringThresholds

    # Selection criteria
    max_positions: int = 10  # Max number of positions per batch
    min_score: float = 60.0  # Minimum composite score to trade


# =============================================================================
# PREDEFINED CONFIGURATIONS FOR A/B TESTING
# =============================================================================

def get_all_configs() -> Dict[str, ScoringConfig]:
    """
    Get all predefined scoring configurations for A/B testing.

    Returns:
        Dictionary mapping config name to ScoringConfig
    """
    # Shared thresholds (can be customized per config if needed)
    default_thresholds = ScoringThresholds()

    configs = {
        # Config 1: VRP-Dominant (Current Baseline)
        # Focuses primarily on VRP edge, minimal other factors
        "vrp_dominant": ScoringConfig(
            name="VRP-Dominant",
            description="Prioritizes raw VRP edge over other factors. Baseline strategy.",
            weights=ScoringWeights(
                vrp_weight=0.70,
                consistency_weight=0.20,
                skew_weight=0.05,
                liquidity_weight=0.05,
            ),
            thresholds=ScoringThresholds(
                vrp_excellent=2.2,  # Focus on high VRP
                vrp_good=1.6,
                vrp_marginal=1.3,
                min_composite_score=62.0,  # Moderate bar
            ),
            max_positions=10,
            min_score=62.0,
        ),

        # Config 2: Balanced (Recommended for User)
        # User profile: Balanced risk, Liquidity First, Skew-Aware, 5-15 trades/week
        "balanced": ScoringConfig(
            name="Balanced",
            description="Well-rounded approach balancing VRP, consistency, skew, and liquidity.",
            weights=ScoringWeights(
                vrp_weight=0.40,
                consistency_weight=0.25,
                skew_weight=0.15,
                liquidity_weight=0.20,
            ),
            thresholds=default_thresholds,
            max_positions=12,
            min_score=60.0,
        ),

        # Config 3: Liquidity-First (User's Priority)
        # Emphasizes tight spreads and high liquidity
        "liquidity_first": ScoringConfig(
            name="Liquidity-First",
            description="Prioritizes liquidity and low slippage. Best for larger position sizes.",
            weights=ScoringWeights(
                vrp_weight=0.30,
                consistency_weight=0.20,
                skew_weight=0.15,
                liquidity_weight=0.35,
            ),
            thresholds=default_thresholds,
            max_positions=10,
            min_score=60.0,
        ),

        # Config 4: Consistency-Heavy
        # Focuses on predictable, reliable moves
        "consistency_heavy": ScoringConfig(
            name="Consistency-Heavy",
            description="Favors stocks with predictable earnings moves. Lower variance.",
            weights=ScoringWeights(
                vrp_weight=0.35,
                consistency_weight=0.45,
                skew_weight=0.10,
                liquidity_weight=0.10,
            ),
            thresholds=default_thresholds,
            max_positions=8,
            min_score=65.0,
        ),

        # Config 5: Skew-Aware (User Preference)
        # Uses skew to avoid bad setups or find directional opportunities
        "skew_aware": ScoringConfig(
            name="Skew-Aware",
            description="Emphasizes skew analysis. Avoids extreme skew or uses it directionally.",
            weights=ScoringWeights(
                vrp_weight=0.35,
                consistency_weight=0.20,
                skew_weight=0.30,
                liquidity_weight=0.15,
            ),
            thresholds=default_thresholds,
            max_positions=10,
            min_score=60.0,
        ),

        # Config 6: Aggressive
        # Takes more trades, lower thresholds
        "aggressive": ScoringConfig(
            name="Aggressive",
            description="Higher volume approach. Lower thresholds, more trades.",
            weights=ScoringWeights(
                vrp_weight=0.55,
                consistency_weight=0.20,
                skew_weight=0.10,
                liquidity_weight=0.15,
            ),
            thresholds=ScoringThresholds(
                vrp_excellent=1.5,  # Lower than baseline (aggressive)
                vrp_good=1.3,
                vrp_marginal=1.1,
                min_composite_score=50.0,  # Lower bar
            ),
            max_positions=15,
            min_score=50.0,
        ),

        # Config 7: Conservative
        # Fewer trades, higher quality
        "conservative": ScoringConfig(
            name="Conservative",
            description="High-confidence only. Fewer trades, higher win rate expected.",
            weights=ScoringWeights(
                vrp_weight=0.40,
                consistency_weight=0.35,
                skew_weight=0.15,
                liquidity_weight=0.10,
            ),
            thresholds=ScoringThresholds(
                vrp_excellent=2.5,  # Higher than baseline (conservative)
                vrp_good=1.8,
                vrp_marginal=1.4,
                min_composite_score=65.0,  # Higher bar
            ),
            max_positions=6,
            min_score=65.0,
        ),

        # Config 8: Hybrid (Adaptive)
        # Mix of VRP and liquidity with moderate thresholds
        "hybrid": ScoringConfig(
            name="Hybrid",
            description="Adaptive approach balancing edge and execution quality.",
            weights=ScoringWeights(
                vrp_weight=0.45,
                consistency_weight=0.20,
                skew_weight=0.15,
                liquidity_weight=0.20,
            ),
            thresholds=default_thresholds,
            max_positions=10,
            min_score=62.0,
        ),
    }

    return configs


def get_config(name: str) -> ScoringConfig:
    """
    Get a specific scoring configuration by name.

    Args:
        name: Config name (case-insensitive)

    Returns:
        ScoringConfig instance

    Raises:
        KeyError: If config name not found
    """
    configs = get_all_configs()
    key = name.lower().replace("-", "_").replace(" ", "_")

    if key not in configs:
        available = ", ".join(configs.keys())
        raise KeyError(f"Unknown config: {name}. Available: {available}")

    return configs[key]


def list_configs() -> List[str]:
    """
    List all available configuration names.

    Returns:
        List of config names
    """
    return list(get_all_configs().keys())
