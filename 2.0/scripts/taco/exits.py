"""Exit engine — evaluates open TACO positions against FROZEN entry refs.

References are never recomputed here (audit finding 1). Priority order:
STOPPED > TAKE_PROFIT(retrace) > REVIEW(thesis stalled) > TAKE_PROFIT(VIX) >
REVIEW(giveback) > HOLD. Puts have no VIX profit rule: puts profit when vol
rises, so VIX normalization is not symmetric.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .constants import GIVEBACK_ARM_FRACTION, RETRACE_TARGET, THESIS_VIX_SESSIONS
from .market_data import EntryRefs, Series


class ExitStatus(str, Enum):
    HOLD = "HOLD"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOPPED = "STOPPED"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class ExitEval:
    status: ExitStatus
    reason: str
    retrace: Optional[float] = None


def _trailing_vix_normal_sessions(vix: Series, pre_event_vix: float) -> int:
    n = 0
    for _, c in reversed(vix):
        if c < pre_event_vix:
            n += 1
        else:
            break
    return n


def evaluate_exit(refs: EntryRefs, spx_since_entry: Series,
                  vix_since_entry: Series,
                  retrace_target: float = RETRACE_TARGET) -> ExitEval:
    if not spx_since_entry:
        return ExitEval(ExitStatus.REVIEW, "no SPX closes since entry")
    last = spx_since_entry[-1][1]

    if refs.direction == "CALL":
        return _evaluate_call(refs, last, spx_since_entry,
                              vix_since_entry, retrace_target)
    return _evaluate_put(refs, last, spx_since_entry, retrace_target)


def _evaluate_call(refs: EntryRefs, last: float, spx: Series,
                   vix: Series, target: float) -> ExitEval:
    dip = refs.pre_event_high - refs.panic_low
    if dip <= 0:
        return ExitEval(ExitStatus.REVIEW,
                        "invalid frozen refs: dip <= 0 — re-record position")

    if last < refs.panic_low:
        return ExitEval(
            ExitStatus.STOPPED,
            f"price {last:.0f} below frozen panic low {refs.panic_low:.0f} — "
            f"thesis broken, that was not the bottom")

    retrace = (last - refs.panic_low) / dip
    if retrace >= target:
        return ExitEval(ExitStatus.TAKE_PROFIT,
                        f"retraced {retrace:.0%} of the dip (target "
                        f"{target:.0%})", retrace)

    normal = _trailing_vix_normal_sessions(vix, refs.pre_event_vix)
    if normal >= THESIS_VIX_SESSIONS:
        return ExitEval(
            ExitStatus.REVIEW,
            f"thesis stalled: VIX normal {normal} sessions but retrace only "
            f"{retrace:.0%} — vol crushed without price recovery", retrace)
    if normal >= 1:
        return ExitEval(
            ExitStatus.TAKE_PROFIT,
            f"VIX closed below pre-event {refs.pre_event_vix:.1f}", retrace)

    high_since = max(c for _, c in spx)
    rebound = high_since - refs.panic_low
    if rebound >= GIVEBACK_ARM_FRACTION * dip and rebound > 0:
        giveback = (high_since - last) / rebound
        if giveback > 0.5:
            return ExitEval(
                ExitStatus.REVIEW,
                f"giveback: {giveback:.0%} of the rebound surrendered "
                f"(high {high_since:.0f} -> {last:.0f})", retrace)

    return ExitEval(ExitStatus.HOLD,
                    f"retrace {retrace:.0%} of target {target:.0%}", retrace)


def _evaluate_put(refs: EntryRefs, last: float, spx: Series,
                  target: float) -> ExitEval:
    rip = refs.euphoria_high - refs.rip_base
    if rip <= 0:
        return ExitEval(ExitStatus.REVIEW,
                        "invalid frozen refs: rip <= 0 — re-record position")

    if last > refs.euphoria_high:
        return ExitEval(
            ExitStatus.STOPPED,
            f"price {last:.0f} above frozen euphoria high "
            f"{refs.euphoria_high:.0f} — thesis broken")

    retrace = (refs.euphoria_high - last) / rip
    if retrace >= target:
        return ExitEval(ExitStatus.TAKE_PROFIT,
                        f"gave back {retrace:.0%} of the rip (target "
                        f"{target:.0%})", retrace)

    low_since = min(c for _, c in spx)
    captured = refs.euphoria_high - low_since
    if captured >= GIVEBACK_ARM_FRACTION * rip and captured > 0:
        giveback = (last - low_since) / captured
        if giveback > 0.5:
            return ExitEval(
                ExitStatus.REVIEW,
                f"giveback: market recovered {giveback:.0%} of the drop "
                f"(low {low_since:.0f} -> {last:.0f})", retrace)

    return ExitEval(ExitStatus.HOLD,
                    f"retrace {retrace:.0%} of target {target:.0%}", retrace)
