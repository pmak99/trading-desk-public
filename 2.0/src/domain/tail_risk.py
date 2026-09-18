"""Tail risk ratio (TRR): the largest earnings gap move over the mean one.

CLAUDE.md §3 caps position size on it. Every caller imports this module --
the engine, the live TRR command, bias predictions, harvest, and the rule
evidence -- so the thresholds a trade is sized on and the thresholds its
evidence is tiered on cannot drift apart. Until 2026-09-13 they were written
out in five places and had already diverged on all-zero history.
"""

from datetime import date
from typing import Iterable, Optional, Sequence, Tuple

TRR_HIGH_ABOVE = 2.5
TRR_NORMAL_FROM = 1.5
# max/mean can never exceed n: HIGH (>2.5) is impossible at 2 quarters and appears in ~4% of
# 3-quarter windows, against ~57% at 12. Below 4 the tier is arithmetic, not evidence
# (audit 4.3, 2026-09-13; was 2). Config's MIN_HISTORICAL_QUARTERS is held equal by a test.
TRR_MIN_QUARTERS = 4
TRR_LOOKBACK_QUARTERS = 12   # prices_repository.get_historical_moves' default


def tail_risk_ratio(gap_moves: Iterable[Optional[float]]) -> Optional[float]:
    """max |gap| / mean |gap|, or None below TRR_MIN_QUARTERS moves.

    All-zero history gives 0.0 (LOW) rather than a division error.
    """
    moves = [abs(m) for m in gap_moves if m is not None]
    if len(moves) < TRR_MIN_QUARTERS:
        return None
    mean = sum(moves) / len(moves)
    return max(moves) / mean if mean > 0 else 0.0


def trr_level(trr: Optional[float]) -> Optional[str]:
    """HIGH above 2.5, NORMAL from 1.5, LOW below; None when TRR is unknown."""
    if trr is None:
        return None
    if trr > TRR_HIGH_ABOVE:
        return "HIGH"
    if trr >= TRR_NORMAL_FROM:
        return "NORMAL"
    return "LOW"


def tail_risk_level(gap_moves: Iterable[Optional[float]]) -> Optional[str]:
    return trr_level(tail_risk_ratio(gap_moves))


def trr_as_of(history: Sequence[Tuple[date, float]], before: date) -> Optional[float]:
    """TRR from the last TRR_LOOKBACK_QUARTERS events strictly before `before`.

    `history` is (earnings_date, gap_move_pct) pairs in any order. The event on
    `before` itself is excluded: it is the outcome being traded, not an input.
    """
    prior = sorted(((d, m) for d, m in history if d < before), key=lambda pair: pair[0])
    return tail_risk_ratio(m for _, m in prior[-TRR_LOOKBACK_QUARTERS:])
