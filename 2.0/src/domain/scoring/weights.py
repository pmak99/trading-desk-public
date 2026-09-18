"""Strategy scoring weights: the values domain scoring is handed.

A domain value, so src.domain.scoring never imports src.config. Until 2026-09-14
this dataclass lived in src/config/config.py and factors.py, rationale.py and
strategy_scorer.py imported it from there, a leak the import contracts could not
see while src.config sat outside the layers. StrategyConfig still carries one
(`scoring_weights`); the scorer receives it as an argument.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringWeights:
    """
    Strategy scoring weights configuration.

    Weights determine how much each factor contributes to overall strategy score.
    All weights should sum to 100 for interpretability.

    UPDATED POST-LOSS ANALYSIS (Nov 2025):
    After a liquidity-driven loss, TRUE P&L analysis revealed:
    - Position sizing was fine (credits collected were in range)
    - VRP edge was real (8x, 5x, 4x ratios)
    - PROBLEM: Catastrophic directional moves (3x-8x collected premium)
    - PROBLEM: Poor liquidity made exits expensive (slippage)
    - PROBLEM: No stop losses (held to 45-110% of max loss)

    Revised Rationale:
    - LIQUIDITY (25%): CRITICAL - Can't profit from good setups with bad fills
                       Raised from 0% to 25% after liquidity-driven losses
    - POP (30%): Still primary but reduced to make room for liquidity
    - VRP (20%): Edge quality remains important
    - R/R (15%): Reduced - was overshadowed by directional risk
    - Greeks (10%): Unchanged - secondary edge indicators

    USER PREFERENCE UPDATE (Dec 2025):
    POP increased from 30% to 40% to prioritize high-probability trades.
    Other factors rebalanced proportionally to maintain 100 total.

    CURRENT weights: POP 40%, Liquidity 22%, VRP 17%, Edge 13%, Greeks 8%
    """

    # Scoring weights with Greeks available (sum = 100)
    # USER PREFERENCE (Dec 2025): POP weighted higher for high-probability trades
    pop_weight: float = 40.0          # Probability of profit (increased from 30%)
    liquidity_weight: float = 22.0    # Liquidity quality (reduced from 25%)
    vrp_weight: float = 17.0          # VRP edge strength (reduced from 20%)
    reward_risk_weight: float = 13.0  # Kelly edge (reduced from 15%)
    greeks_weight: float = 8.0        # Theta/vega quality (reduced from 10%)

    # Scoring weights without Greeks (sum = 100)
    pop_weight_no_greeks: float = 45.0        # Increased from 35%
    liquidity_weight_no_greeks: float = 26.0  # Reduced from 30%
    vrp_weight_no_greeks: float = 17.0        # Reduced from 20%
    reward_risk_weight_no_greeks: float = 12.0  # Reduced from 15%

    # Target values for normalization
    target_pop: float = 0.65      # 65% POP is target
    target_rr: float = 0.30        # 30% R/R is target
    target_vrp: float = 1.07       # close-to-close VRP where the VRP score saturates (intraday 2.0 at matched live selectivity); a scale, not a tier
    target_theta: float = 50.0     # $50/day theta is excellent
    target_vega: float = 100.0     # -$100 vega is excellent

    # NEW: Liquidity scoring targets (post-loss analysis addition)
    target_liquidity_oi: float = 5000.0      # Target open interest
    target_liquidity_spread: float = 5.0     # Target bid-ask spread %
    target_liquidity_volume: float = 500.0   # Target daily volume

    # Rationale generation thresholds (VRP is named by its tier, not a threshold here)
    rr_favorable_threshold: float = 0.35   # R/R >= 0.35 is "favorable"
    pop_high_threshold: float = 0.70       # POP >= 70% is "high"
    theta_positive_threshold: float = 30.0  # Theta > $30/day mentioned in rationale
    vega_beneficial_threshold: float = -50.0  # Vega < -$50 benefits from IV crush

    # Liquidity thresholds for rationale
    liquidity_excellent_threshold: str = "EXCELLENT"
    liquidity_acceptable_threshold: str = "WARNING"
