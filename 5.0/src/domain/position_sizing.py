"""
Position sizing using Half-Kelly criterion.

Half-Kelly balances growth vs drawdown risk.
"""

from typing import Optional


def half_kelly(win_rate: float, risk_reward: float) -> float:
    """
    Calculate Half-Kelly fraction.

    Args:
        win_rate: Probability of winning (0-1)
        risk_reward: Risk/Reward ratio (risk/reward, e.g., 0.5 means risk $1 to win $2)

    Returns:
        Fraction of bankroll to risk (0-1)
    """
    if risk_reward <= 0:
        return 0.0

    # b = reward/risk = 1/risk_reward
    b = 1.0 / risk_reward
    p = win_rate
    q = 1 - p

    # Kelly formula: (bp - q) / b
    kelly = (b * p - q) / b

    # Half-Kelly for safety
    half = kelly / 2

    # Never negative
    return max(0.0, half)


def calculate_position_size(
    account_value: float,
    max_risk_per_contract: float,
    win_rate: float,
    risk_reward: float,
    max_position_pct: float = 0.05,
    min_contracts: int = 1,
    max_contracts_cap: Optional[int] = None,
) -> int:
    """
    Calculate position size in contracts.

    Args:
        account_value: Total account value
        max_risk_per_contract: Maximum loss per contract
        win_rate: Historical win rate (0-1)
        risk_reward: Risk/Reward ratio
        max_position_pct: Maximum position as % of account (default 5%)
        min_contracts: Minimum contracts if edge exists
        max_contracts_cap: Hard ceiling from position_limits (TRR/compound-risk/
            VRP-liquidity caps -- see apply_compound_risk_cap and
            apply_vrp_liquidity_reduction). None means no cap.
            Fixed 2026-07-27: callers computed this cap correctly but never
            passed it here -- the Kelly-sized result was previously bounded
            ONLY by max_position_pct (5% of account), so a TRR-HIGH (50) or
            compound-risk (25) cap could be silently exceeded in the
            position_size actually shown to the user, even though the
            correct, lower number was computed and displayed right next to
            it in position_limits.

    Returns:
        Number of contracts to trade
    """
    # Calculate Half-Kelly fraction
    fraction = half_kelly(win_rate, risk_reward)

    if fraction <= 0:
        return 0

    # Calculate risk budget
    kelly_risk = account_value * fraction
    max_risk = account_value * max_position_pct

    # Use smaller of Kelly or max
    risk_budget = min(kelly_risk, max_risk)

    # Calculate contracts
    if max_risk_per_contract <= 0:
        return min_contracts

    contracts = int(risk_budget / max_risk_per_contract)
    contracts = max(min_contracts, contracts)

    if max_contracts_cap is not None:
        contracts = min(contracts, max_contracts_cap)

    return contracts


def apply_compound_risk_cap(position_limits, tail_risk_level, skew_bias):
    """
    Apply the compound tail risk contract cap (parity with 2.0 SizingContext).

    Compound risk = TRR HIGH + BEARISH/STRONG_BEARISH skew firing together
    (the ORATS sizing alarm cannot fire post-Jun-2026 sunset, so this is the
    only reachable live combination). Calibrated May 2026: 44% crush rate in
    FULL compound zones vs ~67% baseline — cap at 25 contracts.

    Args:
        position_limits: Dict with max_contracts (from DB row or fallback),
                         or None when no limits are known.
        tail_risk_level: 'LOW' | 'NORMAL' | 'HIGH' | 'UNKNOWN' — the
                         live-computed level, not the frozen DB snapshot.
        skew_bias: DirectionalBias value string (e.g. 'bearish') or None.

    Returns:
        position_limits dict with compound_risk_active set, max_contracts
        capped at 25 when compound risk fires (never raises an existing
        tighter limit). None passes through unchanged.
    """
    COMPOUND_RISK_MAX_CONTRACTS = 25

    if position_limits is None:
        return None

    compound = (
        tail_risk_level == "HIGH"
        and skew_bias in ("bearish", "strong_bearish")
    )
    position_limits["compound_risk_active"] = compound
    if compound:
        existing = position_limits.get("max_contracts") or COMPOUND_RISK_MAX_CONTRACTS
        position_limits["max_contracts"] = min(existing, COMPOUND_RISK_MAX_CONTRACTS)
    return position_limits


def apply_vrp_liquidity_reduction(position_limits, vrp_tier, liquidity_tier):
    """
    CLAUDE.md sizing Rule 4 (parity with 2.0 SizingContext.vrp_marginal /
    liquidity_warning): MARGINAL VRP or WARNING liquidity -> single,
    non-stacking 50% reduction of max_contracts. Call AFTER
    apply_compound_risk_cap -- if compound risk already fired (25-contract
    cap), rules 1-2 are first-match-wins per CLAUDE.md and this does not
    reduce further, matching 2.0's effective_max_contracts early return.

    Added 2026-07-27 alongside the 2.0 fix for the same gap: this rule
    existed only in documentation, position_limits carried no VRP/liquidity
    signal at all before this.

    Args:
        position_limits: Dict with max_contracts and compound_risk_active
                         (from apply_compound_risk_cap), or None.
        vrp_tier: 'EXCELLENT' | 'GOOD' | 'MARGINAL' | 'SKIP'
        liquidity_tier: 'EXCELLENT' | 'GOOD' | 'WARNING' | 'REJECT'

    Returns:
        position_limits dict with vrp_liquidity_reduction_active set and
        max_contracts halved (// 2, floor at 1) when it fires and compound
        risk hasn't already capped tighter. None passes through unchanged.
    """
    if position_limits is None:
        return None

    reduction_fires = vrp_tier == "MARGINAL" or liquidity_tier == "WARNING"
    position_limits["vrp_liquidity_reduction_active"] = reduction_fires

    if reduction_fires and not position_limits.get("compound_risk_active"):
        existing = position_limits.get("max_contracts")
        if existing:
            position_limits["max_contracts"] = max(1, existing // 2)

    return position_limits


def is_tradeable_tier(vrp_tier) -> bool:
    """
    Whether a VRP tier meets the minimum tradeable threshold.

    Parity with 2.0's VRPResult.is_tradeable (fixed 2026-07-27 there to
    include MARGINAL -- see 2.0/src/domain/types.py docstring for the
    incident this closed). MARGINAL is tradeable at 50% reduced size per
    CLAUDE.md's VRP tier table; only SKIP (<1.2x) means don't trade.
    """
    return vrp_tier in ("EXCELLENT", "GOOD", "MARGINAL")
