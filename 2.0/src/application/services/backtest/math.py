"""Pure simulation math: consistency, P&L, Kelly sizing, position sizing."""
import statistics
from typing import List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from src.application.services.backtest.types import BacktestTrade


def calculate_consistency(moves: List[float]) -> float:
    """
    Calculate consistency score from historical moves.

    Uses coefficient of variation (lower is more consistent).

    Args:
        moves: List of historical move percentages

    Returns:
        Consistency score (0-1, higher is more consistent)
    """
    if len(moves) == 0:
        return 0.5  # Neutral if no data

    if len(moves) == 1:
        # With only 1 move, assume moderate consistency
        return 0.6  # Slightly above neutral (benefit of the doubt)

    mean = statistics.mean(moves)
    std = statistics.stdev(moves)

    if mean == 0:
        return 0.5

    # Coefficient of variation
    cv = std / abs(mean)

    # Convert to 0-1 score (lower CV = higher consistency)
    # CV of 0.5 = score 0.5, CV of 0 = score 1.0, CV of 1.0+ = score ~0
    consistency = 1.0 / (1.0 + cv)

    return max(0.0, min(1.0, consistency))


def simulate_pnl(
    actual_move: float,
    avg_historical_move: float,
    stock_price: float = 100.0,
    bid_ask_spread_pct: float = 0.10,
    commission_per_contract: float = 0.65,
    use_realistic_model: bool = True,
) -> float:
    """
    Simulate P&L for a straddle trade.

    Enhanced realistic model:
    - Sell ATM straddle with bid-ask spread costs
    - Premium collected at bid (worse price)
    - Exit at ask next day (worse price)
    - Commission costs on entry and exit
    - IV crush decay modeling

    Args:
        actual_move: Actual move percentage
        avg_historical_move: Average historical move percentage
        stock_price: Stock price for commission calculation
        bid_ask_spread_pct: Bid-ask spread as % of mid (default 10%)
        commission_per_contract: Commission per contract (default $0.65)
        use_realistic_model: If True, use enhanced model; if False, use simple model

    Returns:
        Simulated P&L as percentage of stock price
    """
    # Assume implied move is historical * 1.3 (typical IV inflation for earnings)
    implied_move = avg_historical_move * 1.3

    if not use_realistic_model:
        # Simple model (original)
        premium = implied_move * 0.5
        loss = max(0, actual_move - implied_move)
        return premium - loss

    # Enhanced realistic model
    # Entry: Sell straddle at bid (50% of implied move, less half spread)
    straddle_mid = implied_move * 0.5
    entry_slippage = straddle_mid * (bid_ask_spread_pct / 2)
    premium_collected = straddle_mid - entry_slippage

    # Exit: Buy back straddle next day after IV crush
    # Residual value = intrinsic value if actual > implied
    residual_intrinsic = max(0, actual_move - implied_move)

    # Add some residual time value (IV doesn't go to zero)
    # Assume 20% of original implied move remains as residual IV
    residual_extrinsic = implied_move * 0.10

    # Total exit cost at ask (worse price)
    exit_mid = residual_intrinsic + residual_extrinsic
    exit_slippage = exit_mid * (bid_ask_spread_pct / 2)
    exit_cost = exit_mid + exit_slippage

    # Commission costs (2 contracts: call + put, entry + exit)
    # Commission is per contract, need to express as % of stock price
    # Total commission: 4 * $0.65 = $2.60
    # Per share: $2.60 / 100 shares = $0.026
    # As % of stock price: ($0.026 / stock_price) * 100
    total_commission = 4 * commission_per_contract
    commission_per_share = total_commission / 100  # Divide by 100 shares
    commission_pct = (commission_per_share / stock_price) * 100

    # Net P&L as percentage
    pnl = premium_collected - exit_cost - commission_pct

    return pnl


def calculate_kelly_fraction(trades: List) -> float:
    """
    Calculate Kelly Criterion fraction from historical trades.

    Args:
        trades: List of trades (must have .simulated_pnl attribute)

    Returns:
        Kelly fraction (capped at 0.25 for quarter-Kelly)
    """
    if not trades:
        return 0.10  # Default conservative sizing

    winners = [t.simulated_pnl for t in trades if t.simulated_pnl > 0]
    losers = [t.simulated_pnl for t in trades if t.simulated_pnl <= 0]

    if not winners or not losers:
        return 0.10  # Default if all wins or all losses

    win_rate = len(winners) / len(trades)
    avg_win = statistics.mean([abs(w) for w in winners])
    avg_loss = statistics.mean([abs(l) for l in losers])

    if avg_loss == 0:
        return 0.25  # Max quarter-Kelly

    # Kelly formula: f = (p * b - q) / b
    # where p = win rate, q = loss rate, b = win/loss ratio
    p = win_rate
    q = 1 - win_rate
    b = avg_win / avg_loss

    kelly = (p * b - q) / b

    # Cap at quarter-Kelly for safety
    return min(max(kelly, 0.05), 0.25)


def apply_position_sizing(
    trades: List,
    total_capital: float = 40000.0,
    use_hybrid: bool = True,
) -> Tuple[float, float, float]:
    """
    Apply position sizing to trades using Kelly + VRP weighting.

    Modifies trades in place, converting P&L from percentage to dollars.

    Args:
        trades: List of backtest trades (modified in place!)
        total_capital: Total capital available
        use_hybrid: If True, use Kelly + VRP hybrid; if False, use equal weight

    Returns:
        Tuple of (kelly_fraction, total P&L in dollars, max drawdown in %)
    """
    if not trades:
        return 0.0, 0.0, 0.0

    # Store original P&L percentages for Kelly calculation
    original_pnls = [t.simulated_pnl for t in trades]

    # Calculate Kelly fraction from percentage returns
    kelly_frac = calculate_kelly_fraction(trades)

    if not use_hybrid:
        # Equal weight baseline
        position_size = total_capital / len(trades)
        for i, trade in enumerate(trades):
            # Convert percentage P&L to dollar P&L
            trade.simulated_pnl = original_pnls[i] / 100.0 * position_size

    else:
        # Hybrid: Kelly base * VRP multiplier
        avg_score = statistics.mean(t.composite_score for t in trades)

        for i, trade in enumerate(trades):
            # VRP multiplier (relative to average)
            vrp_multiplier = trade.composite_score / avg_score

            # Position size = capital * kelly_frac * VRP_multiplier
            position_size = total_capital * kelly_frac * vrp_multiplier

            # Convert P&L from percentage to dollars
            trade.simulated_pnl = original_pnls[i] / 100.0 * position_size

    # Calculate total P&L and max drawdown in dollars
    total_pnl = sum(t.simulated_pnl for t in trades)

    # Max drawdown as percentage of peak capital
    capital = total_capital
    peak_capital = total_capital
    max_dd_pct = 0.0

    for trade in trades:
        capital += trade.simulated_pnl
        peak_capital = max(peak_capital, capital)

        # Drawdown as % from peak
        if peak_capital > 0:
            dd_pct = (peak_capital - capital) / peak_capital * 100.0
            max_dd_pct = max(max_dd_pct, dd_pct)

    return kelly_frac, total_pnl, max_dd_pct
