"""
Position sizing using Half-Kelly criterion.

Half-Kelly balances growth vs drawdown risk.
"""


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

    # Ensure minimum if we have edge
    return max(min_contracts, contracts)


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
