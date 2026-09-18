"""Contract sizing: Kelly fraction, bounds, and contract count calculations."""
import logging
from typing import Optional, Tuple

from src.config.config import StrategyConfig
from src.domain.types import (
    Money,
    SizingContext,
    dollar_risk_contracts,
    max_loss_capped_contracts,
)

logger = logging.getLogger(__name__)


def max_loss_warning(
    total_max_loss: float,
    threshold: float,
    contracts: int = 0,
) -> Optional[str]:
    """
    Warn when a position's total dollar risk exceeds the budget. Never blocks.

    The engine sizes within this budget (max_loss_capped_contracts, since
    2026-09-16), so on its own output this fires only for a single spread whose
    max loss is over the budget by itself. Pass the worst-case (natural-price)
    max loss: that is the figure the cap holds under the budget.

    The contract cap is not a risk cap. 2.0/.env documents MAX_CONTRACTS=50 as
    "50 contracts x $500 max loss = $25K", but $500/contract only holds for a
    $5-wide spread with no credit. On an expensive underlying, or on a single
    (where max loss is the premium paid), the same contract count can carry many
    times that risk and still read as compliant: CRWV Apr 2026 was 110 contracts
    at ~$3,090 each, ~$340k of risk, against a nominal 50-contract cap.

    Sizing stays a human decision, so this returns a message, not a veto.

    Args:
        total_max_loss: Position max loss in dollars, already scaled by contracts.
        threshold: Dollar budget. Zero or negative disables the warning.
        contracts: Contract count, used only to show per-contract risk.

    Returns:
        A warning string, or None when the position is within budget.
    """
    if threshold <= 0 or total_max_loss <= 0:
        return None
    if total_max_loss <= threshold:
        return None

    multiple = total_max_loss / threshold
    msg = (
        f"MAX LOSS ${total_max_loss:,.0f} exceeds the ${threshold:,.0f} "
        f"risk budget ({multiple:.1f}x)"
    )
    if contracts > 0:
        msg += f" — {contracts} contracts x ${total_max_loss / contracts:,.0f} per contract"
    return msg


def effective_cap(config: StrategyConfig, sizing_context: Optional[SizingContext]) -> int:
    if sizing_context is not None:
        return sizing_context.effective_max_contracts
    return config.max_contracts


def max_loss_threshold(config: StrategyConfig, sizing_context: Optional[SizingContext]) -> float:
    """MAX_LOSS_WARN_THRESHOLD as the live path carries it, like effective_cap."""
    if sizing_context is not None:
        return sizing_context.max_loss_warn_threshold
    return config.max_loss_warn_threshold


def calculate_contracts(
    config: StrategyConfig,
    max_loss_per_spread: Money,
    sizing_context: Optional[SizingContext] = None,
    worst_case_max_loss: Optional[Money] = None,
) -> int:
    """
    DEPRECATED: Use calculate_contracts_kelly instead.
    Kept for backward compatibility only. Held to MAX_LOSS_WARN_THRESHOLD of the
    worst-case max loss per spread, as calculate_contracts_kelly is.
    """
    if max_loss_per_spread.amount <= 0:
        return 0

    contracts = int(config.risk_budget_per_trade / float(max_loss_per_spread.amount))
    risk_per_spread = max(
        float(max_loss_per_spread.amount),
        float(worst_case_max_loss.amount) if worst_case_max_loss is not None else 0.0,
    )
    return max_loss_capped_contracts(
        max(1, min(contracts, effective_cap(config, sizing_context))),
        risk_per_spread,
        max_loss_threshold(config, sizing_context),
    )


# A spread whose EV, computed from its delta-implied POP as a win-everything /
# lose-everything bet, is below -2% of max loss gets KELLY_MIN_CONTRACTS. The
# .env comment on the removed KELLY_MIN_EDGE ("2% minimum edge required") had
# the opposite sign and was never read (audit 4.1, 2026-09-13).
MIN_EV_PCT = -0.02


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
    min_ev_pct = MIN_EV_PCT
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
    worst_case_max_loss: Optional[Money] = None,
) -> int:
    """
    Calculate position size for credit spreads using expected value sizing.

    ⚠️ Not Kelly, despite the names. It is a fixed dollar-risk rule:
    contracts = RISK_BUDGET_PER_TRADE / max_loss x KELLY_FRACTION (a fixed
    fraction, 0.25) x POP scale x EV scale, then the SizingContext caps. No
    p - q/b term appears anywhere, and a Sep 2026 fit of the reconciled book
    found no measurable edge per dollar at risk to fit one to (audit 4.1).
    The names are kept until the POP rework merges; the behaviour is unchanged.

    Delegates pure math to calculate_kelly_fraction() and bounds to
    apply_kelly_caps(); assembles the result here. Last, the count is held to
    MAX_LOSS_WARN_THRESHOLD of `worst_case_max_loss` per spread (the natural
    price's max loss; `max_loss` when not given), since 2026-09-16.
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

    min_ev_pct = MIN_EV_PCT

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

    quality_scale = pop_scale * ev_scale
    position_fraction = config.kelly_fraction * quality_scale
    # Shared with the scan's liquidity check (full_size_contracts)
    raw_contracts = dollar_risk_contracts(
        config.risk_budget_per_trade, float(max_loss.amount), position_fraction
    )

    contracts = apply_kelly_caps(config, raw_contracts, sizing_context)
    risk_per_spread = max(
        float(max_loss.amount),
        float(worst_case_max_loss.amount) if worst_case_max_loss is not None else 0.0,
    )
    contracts = max_loss_capped_contracts(
        contracts, risk_per_spread, max_loss_threshold(config, sizing_context)
    )

    # Recompute EV for debug logging (same formula as in calculate_kelly_fraction)
    p = probability_of_profit
    ev = p * float(max_profit.amount) - (1.0 - p) * float(max_loss.amount)

    logger.debug(
        f"Position sizing: POP={p:.1%}, EV=${ev:.2f} ({ev_pct:.1%} of risk), "
        f"pop_scale={pop_scale:.2f}, ev_scale={ev_scale:.2f}, "
        f"quality={quality_scale:.2f}, fraction={position_fraction:.3f}, contracts={contracts}"
    )

    return contracts
