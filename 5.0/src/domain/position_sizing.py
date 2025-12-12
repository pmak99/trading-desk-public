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
