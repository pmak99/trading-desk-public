"""IPO wind-down guard — the only capital protection chosen for the
capital pool funding TACO trades (time-based, no dollar caps)."""

from datetime import date
from enum import Enum
from typing import Tuple

from .constants import (
    IPO_EXPECTED_DATE,
    IPO_FUND_RELEASED,
    WINDDOWN_BANNER_DAYS,
    WINDDOWN_LIQUIDATE_DAYS,
    WINDDOWN_LOCK_DAYS,
)


class WinddownPhase(str, Enum):
    CLEAR = "CLEAR"            # > 60 days out
    BANNER = "BANNER"          # <= 60 days: countdown on every run
    LOCKED = "LOCKED"          # <= 30 days: no new entries
    LIQUIDATE = "LIQUIDATE"    # <= 14 days (and past): flag all open positions
    RELEASED = "RELEASED"      # post-IPO, fund released back to TACO duty


def winddown_phase(today: date, ipo_date: date = IPO_EXPECTED_DATE,
                   released: bool = IPO_FUND_RELEASED) -> WinddownPhase:
    if released:
        return WinddownPhase.RELEASED
    days = (ipo_date - today).days
    if days <= WINDDOWN_LIQUIDATE_DAYS:
        return WinddownPhase.LIQUIDATE
    if days <= WINDDOWN_LOCK_DAYS:
        return WinddownPhase.LOCKED
    if days <= WINDDOWN_BANNER_DAYS:
        return WinddownPhase.BANNER
    return WinddownPhase.CLEAR


def entries_allowed(today: date, ipo_date: date = IPO_EXPECTED_DATE,
                    released: bool = IPO_FUND_RELEASED) -> Tuple[bool, str]:
    phase = winddown_phase(today, ipo_date, released)
    if phase in (WinddownPhase.LOCKED, WinddownPhase.LIQUIDATE):
        return False, (
            f"entries locked: {(ipo_date - today).days} days to expected "
            f"Anthropic IPO ({ipo_date}) — fund must be liquid at pricing. "
            f"Flip IPO_FUND_RELEASED in scripts/taco/constants.py post-IPO.")
    if phase == WinddownPhase.BANNER:
        return True, (f"WIND-DOWN: {(ipo_date - today).days} days to expected "
                      f"IPO {ipo_date} — entries lock at T-30")
    return True, ""
