"""Contract sizing: Kelly fraction, bounds, and contract count calculations."""
import logging
from typing import Optional, Tuple

from src.domain.types import Money, SizingContext
from src.config.config import StrategyConfig

logger = logging.getLogger(__name__)


def effective_cap(config: StrategyConfig, sizing_context: Optional[SizingContext]) -> int:
    if sizing_context is not None:
        return sizing_context.effective_max_contracts
    return config.max_contracts


def calculate_contracts(
    config: StrategyConfig,
    max_loss_per_spread: Money,
    sizing_context: Optional[SizingContext] = None,
) -> int:
    """
    DEPRECATED: Use calculate_contracts_kelly instead.
    Kept for backward compatibility only.
    """
    if max_loss_per_spread.amount <= 0:
        return 0

    contracts = int(config.risk_budget_per_trade / float(max_loss_per_spread.amount))
    return max(1, min(contracts, effective_cap(config, sizing_context)))


def calculate_kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> Tuple[float, float, float]:
    """
    Pure EV/Kelly math for credit spread sizing. No side effects.

    Credit spreads have asymmetric payoffs (small win, larger loss), so
    traditional Kelly (designed for binary bets) doesn't apply directly.
    We compute EV-based scaling factors instead.

    Args:
        win_rate: Probability of profit (0.0–1.0)
        avg_win: Max profit per spread (dollars)
        avg_loss: Max loss per spread (dollars, positive number)

    Returns:
        Tuple of (ev_pct, pop_scale, ev_scale) — all pure floats, no config
        ev_pct: EV as fraction of loss (can be negative; caller checks floor)
        pop_scale: POP quality scaling factor (0.3–1.0)
        ev_scale: EV quality scaling factor (0.3–1.0)
    """
    p = win_rate
    q = 1.0 - p
    ev = p * avg_win - q * avg_loss
    ev_pct = ev / avg_loss if avg_loss > 0 else 0.0

    # POP scale: 0.3 below 70%, linear 0.5→1.0 from 70%→90%, 1.0 above 90%
    if p >= 0.90:
        pop_scale = 1.0
    elif p >= 0.70:
        pop_scale = 0.5 + (p - 0.70) * 2.5
    else:
        pop_scale = 0.3

    # EV scale: 1.0 at ≥5%, linear 0.5→1.0 from 0%→5%,
    #           linear 0.3→0.5 from -2%→0%, 0.3 below -2%
    min_ev_pct = -0.02
    if ev_pct >= 0.05:
        ev_scale = 1.0
    elif ev_pct >= 0.0:
        ev_scale = 0.5 + ev_pct / 0.05 * 0.5
    elif ev_pct >= min_ev_pct:
        ev_scale = 0.3 + (ev_pct - min_ev_pct) / (0.0 - min_ev_pct) * 0.2
    else:
        ev_scale = 0.3

    return ev_pct, pop_scale, ev_scale


def apply_kelly_caps(
    config: StrategyConfig,
    raw_contracts: int,
    sizing_context: Optional[SizingContext],
) -> int:
    """Apply min/max bounds to a raw Kelly contract count."""
    contracts = max(config.kelly_min_contracts, raw_contracts)
    contracts = min(contracts, effective_cap(config, sizing_context))
    return contracts


def calculate_contracts_kelly(
    config: StrategyConfig,
    max_profit: Money,
    max_loss: Money,
    probability_of_profit: float,
    sizing_context: Optional[SizingContext] = None,
) -> int:
    """
    Calculate position size for credit spreads using expected value sizing.

    Delegates pure math to calculate_kelly_fraction() and bounds to
    apply_kelly_caps(); assembles the result here.
    """
    if max_loss.amount <= 0:
        logger.warning("Invalid max_loss <= 0, returning minimum contracts")
        return config.kelly_min_contracts

    if max_profit.amount <= 0:
        logger.warning("Invalid max_profit <= 0, returning minimum contracts")
        return config.kelly_min_contracts

    if not (0.0 <= probability_of_profit <= 1.0):
        logger.warning(
            f"Invalid probability_of_profit={probability_of_profit:.3f} "
            f"(must be 0.0-1.0), returning minimum contracts"
        )
        return config.kelly_min_contracts

    min_ev_pct = -0.02  # Allow up to -2% EV (VRP provides additional edge)

    ev_pct, pop_scale, ev_scale = calculate_kelly_fraction(
        win_rate=probability_of_profit,
        avg_win=float(max_profit.amount),
        avg_loss=float(max_loss.amount),
    )

    if ev_pct < min_ev_pct:
        logger.debug(
            f"Position sizing: EV {ev_pct:.2%} below minimum {min_ev_pct:.2%}, "
            f"POP={probability_of_profit:.1%}, max_profit=${max_profit.amount:.2f}, "
            f"max_loss=${max_loss.amount:.2f}, "
            f"using min_contracts={config.kelly_min_contracts}"
        )
        return config.kelly_min_contracts

    base_contracts = config.risk_budget_per_trade / float(max_loss.amount)
    quality_scale = pop_scale * ev_scale
    position_fraction = config.kelly_fraction * quality_scale
    raw_contracts = int(base_contracts * position_fraction)

    contracts = apply_kelly_caps(config, raw_contracts, sizing_context)

    # Recompute EV for debug logging (same formula as in calculate_kelly_fraction)
    p = probability_of_profit
    ev = p * float(max_profit.amount) - (1.0 - p) * float(max_loss.amount)

    logger.debug(
        f"Position sizing: POP={p:.1%}, EV=${ev:.2f} ({ev_pct:.1%} of risk), "
        f"pop_scale={pop_scale:.2f}, ev_scale={ev_scale:.2f}, "
        f"quality={quality_scale:.2f}, fraction={position_fraction:.3f}, contracts={contracts}"
    )

    return contracts
