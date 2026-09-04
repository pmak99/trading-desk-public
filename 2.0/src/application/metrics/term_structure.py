"""
IV Term Structure Analyzer — front-vs-back ATM IV around earnings.

The slope of the IV term structure measures how much of the front-expiry IV
is EVENT-SPECIFIC (will crush after the announcement) versus persistent
(will not). Steep backwardation (front ATM IV >> back ATM IV) is the
favorable IV-crush setup; a flat structure despite a high VRP means the
market is pricing persistent risk, not event risk — caution.

Academic basis: Xie (Columbia) shows the term-structure SLOPE factor adds
predictive power for earnings-announcement outcomes beyond the
implied-vs-historical move ratio. Practitioner guidance (ORATS, MenthorQ
ACES) measures it 2-3 days pre-earnings against a back expiry ~1 month out.

Convention (matches TermStructureResult):
    slope       = back_iv - front_iv   (positive = contango)
    slope_ratio = front_iv / back_iv   (>1 = backwardation; the event multiple)
"""

import logging
from datetime import date, timedelta
from typing import List, Optional

from src.domain.types import OptionChain, Percentage, TermStructureResult
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

logger = logging.getLogger(__name__)

# Back expiration target: ~30 calendar days after the front expiry, and at
# least 7 days out so the back leg is not itself dominated by the event.
BACK_TARGET_DAYS = 30
BACK_MIN_GAP_DAYS = 7

# slope_ratio classification thresholds (front/back ATM IV)
RATIO_STEEP = 1.30      # strong event-vol concentration — favorable crush
RATIO_MODERATE = 1.10   # moderate backwardation
RATIO_FLAT = 1.00       # at/below: no event premium in the front — caution


def select_back_expiration(
    expirations: List[date],
    front_expiration: date,
    target_days: int = BACK_TARGET_DAYS,
    min_gap_days: int = BACK_MIN_GAP_DAYS,
) -> Optional[date]:
    """Pick the listed expiration closest to front + target_days (front excluded)."""
    target = front_expiration + timedelta(days=target_days)
    candidates = [
        e for e in expirations
        if e >= front_expiration + timedelta(days=min_gap_days)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda e: abs((e - target).days))


def atm_iv(chain: OptionChain) -> Optional[float]:
    """
    ATM implied volatility for a chain: mean of call and put IV at the ATM
    strike (either alone if the other is missing). Returns None if neither
    side has an IV at the ATM strike.
    """
    try:
        strike = chain.atm_strike()
    except ValueError:
        return None
    ivs = []
    for side in (chain.calls, chain.puts):
        quote = side.get(strike)
        if quote is not None and quote.implied_volatility is not None:
            iv = float(quote.implied_volatility.value)
            if iv > 0:
                ivs.append(iv)
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def analyze_term_structure(
    front_chain: OptionChain,
    back_chain: OptionChain,
) -> Result[TermStructureResult, AppError]:
    """Compute the term-structure slope from two already-fetched chains."""
    front_iv = atm_iv(front_chain)
    back_iv = atm_iv(back_chain)

    if front_iv is None or back_iv is None or back_iv <= 0:
        return Err(AppError(
            ErrorCode.NODATA,
            f"{front_chain.ticker}: missing ATM IV for term structure "
            f"(front={front_iv}, back={back_iv})",
        ))

    slope = back_iv - front_iv
    slope_ratio = front_iv / back_iv

    return Ok(TermStructureResult(
        ticker=front_chain.ticker,
        expirations=[front_chain.expiration, back_chain.expiration],
        ivs=[Percentage(front_iv), Percentage(back_iv)],
        slope=slope,
        is_backwardation=slope < 0,
        slope_ratio=slope_ratio,
    ))


def classify_slope_ratio(slope_ratio: Optional[float]) -> str:
    """
    Human-readable classification of the event-vol multiple.

    STEEP_BACKWARDATION: front IV >= 1.30x back IV — IV is event-concentrated,
        the crush has the most room to work.
    BACKWARDATION: 1.10-1.30x — normal pre-earnings structure.
    MILD: 1.00-1.10x — weak event premium.
    FLAT_OR_CONTANGO: front <= back — the elevated IV is NOT event-specific;
        a high VRP here means persistent risk the crush will not remove.
    """
    if slope_ratio is None:
        return "UNKNOWN"
    if slope_ratio >= RATIO_STEEP:
        return "STEEP_BACKWARDATION"
    if slope_ratio >= RATIO_MODERATE:
        return "BACKWARDATION"
    if slope_ratio > RATIO_FLAT:
        return "MILD"
    return "FLAT_OR_CONTANGO"
