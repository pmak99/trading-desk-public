"""The trading expiration: which Friday weekly an earnings spread is built on.

One rule for every path (CLAUDE.md §5): /analyze, the scan's sequential loops and
its parallel ConcurrentScanner, the Telegram bot, store_bias_prediction and
trade.sh. Moved from scripts/scan/date_utils.py on 2026-09-14: the parallel scan
reached into the scripts tree for it, which the import contracts now refuse.
The implied move is priced elsewhere, at implied_move_expiration().
"""

import logging
from datetime import date, timedelta
from typing import Optional

from src.domain.enums import EarningsTiming
from src.domain.market_calendar import adjust_to_trading_day, is_market_holiday

logger = logging.getLogger(__name__)


def get_next_friday(from_date: date) -> date:
    """Get the next Friday from the given date."""
    days_until_friday = (4 - from_date.weekday()) % 7
    if days_until_friday == 0:
        # If today is Friday, get next Friday
        days_until_friday = 7
    return from_date + timedelta(days=days_until_friday)


def _holiday_adjusted_friday(friday: date) -> date:
    """Weekly options expire Thursday when their Friday is a market holiday
    (e.g. Good Friday)."""
    if is_market_holiday(friday):
        return friday - timedelta(days=1)
    return friday


def floored_offset_days(offset_days: int, min_dte: int = 3) -> int:
    """The custom offset calculate_expiration_date applies: never below min_dte.

    trade.sh list prints it, so the header shows the offset the scan uses.
    """
    return max(offset_days, min_dte)


def calculate_expiration_date(
    earnings_date: date,
    timing: EarningsTiming,
    offset_days: Optional[int] = None,
    min_dte: int = 3
) -> date:
    """
    Calculate expiration date for TRADING purposes (liquidity, strategy).

    Args:
        earnings_date: Date of earnings announcement
        timing: BMO/AMC/UNKNOWN. Currently unused — the weekday rules below
                already guarantee >= min_dte for either timing; kept in the
                signature so callers don't break if timing-aware logic returns.
        offset_days: Optional custom offset in days from earnings date.
                     The min_dte floor is enforced on this path too.
        min_dte: Minimum days to expiration from earnings date (default 3).
                 At the same win rate, 0-2 DTE trades lose more when they
                 lose than 3-5 DTE ones (CLAUDE.md §5, from
                 scripts/rule_evidence.py). If calculated expiration gives
                 < min_dte, bumps to the following Friday. Minimum 3 DTE —
                 no exceptions.

    Returns:
        Expiration date for options (weekend/holiday adjusted; holiday Fridays
        roll back to Thursday, matching exchange weekly-expiration convention)

    Strategy (aligned with user's trading workflow):
        - Mon/Tue earnings -> Friday of same week (if DTE >= min_dte)
        - Wed earnings -> next Friday (same-week Friday = 2 DTE < min_dte)
        - Thu/Fri earnings -> Friday 1 week out (avoid 0DTE risk)
        - Custom offset: earnings_date + offset_days (floored at min_dte)

    User enters positions at 3-4pm on earnings day (or day before for BMO),
    exits next trading day at 9:30-10:30am, using Friday weekly expirations.

    Note:
        Never price the implied move or VRP here: every path takes that from
        src/application/metrics/implied_move_expiration.py, the first listed
        expiration containing the reaction.
    """
    if offset_days is not None:
        floored = floored_offset_days(offset_days, min_dte)
        if floored != offset_days:
            logger.warning(
                f"DTE floor: custom offset {offset_days} is below min_dte={min_dte} "
                f"(CLAUDE.md §5) — raising to {min_dte}"
            )
        return adjust_to_trading_day(earnings_date + timedelta(days=floored))

    # User strategy: Thursday or Friday earnings -> Use Friday 1 week out
    # This avoids 0DTE risk and provides buffer for exit
    weekday = earnings_date.weekday()

    if weekday == 3:  # Thursday
        return _holiday_adjusted_friday(earnings_date + timedelta(days=8))
    if weekday == 4:  # Friday
        return _holiday_adjusted_friday(earnings_date + timedelta(days=7))

    # Mon/Tue/Wed: Friday of same week, bumped a week at a time while the
    # (holiday-adjusted) candidate lands under the DTE floor.
    # Wed → same-week Friday = 2 DTE; Tue + holiday Friday → Thursday = 2 DTE.
    candidate_friday = get_next_friday(earnings_date)
    expiration = _holiday_adjusted_friday(candidate_friday)
    while (expiration - earnings_date).days < min_dte:
        day_name = earnings_date.strftime('%A')
        bumped = _holiday_adjusted_friday(candidate_friday + timedelta(days=7))
        logger.info(
            f"DTE floor: {earnings_date} ({day_name}) → bumped from "
            f"{expiration} ({(expiration - earnings_date).days} DTE) to "
            f"{bumped} ({(bumped - earnings_date).days} DTE)"
        )
        candidate_friday = candidate_friday + timedelta(days=7)
        expiration = bumped

    return expiration
