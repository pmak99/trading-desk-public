"""Tiered entry score (0-100) for TACO index entries.

Components: dip depth 30 / VIX regime 21 / event type 21 / term structure 13 /
cross-asset confirmation 15. Event type is the one judgment input (Claude
classifies from headlines); its blast radius is bounded — see the
misclassification test. Puts always cap at PILOT until the put side has its
own validated history (spec 2026-07-14). Cross-asset is CALL-only; PUT ignores
it and renormalizes the four legacy components (spec 2026-07-22).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .constants import (
    DIP_MAX_PCT,
    DIP_MIN_PCT,
    EVENT_SCORES,
    FUND_SIZE,
    PUT_SKIP_FLOOR,
    RENORM_NO_CROSS,
    RUNUP_MAX_Z,
    RUNUP_MIN_Z,
    TERM_RATIO_MAX,
    TERM_RATIO_MIN,
    TIER_FULL,
    TIER_HALF,
    TIER_PILOT,
    TIER_SIZING,
    VIX_LEVEL_MAX,
    VIX_LEVEL_MIN,
    VIX_LEVEL_PTS,
    VIX_SPIKE_MAX,
    VIX_SPIKE_MIN,
    VIX_SPIKE_PTS,
    WEIGHT_CROSS_ASSET,
    WEIGHT_DIP,
    WEIGHT_TERM,
    EventType,
    Tier,
)


def _linear(x: float, lo: float, hi: float, max_pts: float) -> float:
    if x <= lo:
        return 0.0
    if x >= hi:
        return max_pts
    return (x - lo) / (hi - lo) * max_pts


def dip_component(drawdown_pct: float) -> float:
    return _linear(drawdown_pct, DIP_MIN_PCT, DIP_MAX_PCT, WEIGHT_DIP)


def runup_component(runup_z: float) -> float:
    return _linear(runup_z, RUNUP_MIN_Z, RUNUP_MAX_Z, WEIGHT_DIP)


def vix_component(vix_level: float, spike_ratio: float) -> float:
    return (_linear(vix_level, VIX_LEVEL_MIN, VIX_LEVEL_MAX, VIX_LEVEL_PTS)
            + _linear(spike_ratio, VIX_SPIKE_MIN, VIX_SPIKE_MAX, VIX_SPIKE_PTS))


def term_component(vix_vix3m_ratio: float) -> float:
    return _linear(vix_vix3m_ratio, TERM_RATIO_MIN, TERM_RATIO_MAX, WEIGHT_TERM)


def event_component(event_type: EventType) -> float:
    return EVENT_SCORES[event_type]


def cross_asset_component(count: int, available: int) -> float:
    """Confirmation count scaled to the 15-pt range. With fewer than 3
    assets available, count scales over what IS available (spec 2026-07-22
    degradation rule)."""
    if available <= 0:
        raise ValueError("cross_asset_component requires available >= 1")
    return count / available * WEIGHT_CROSS_ASSET


@dataclass(frozen=True)
class EntrySignal:
    direction: str
    score: float
    tier: Tier
    sizing_usd: float
    components: Dict[str, float]
    notes: List[str] = field(default_factory=list)


def score_entry(direction: str, *,
                drawdown: Optional[float] = None,
                runup_z: Optional[float] = None,
                vix_level: float,
                vix_spike_ratio: float,
                vix3m_ratio: Optional[float] = None,
                event_type: EventType,
                cross_asset_count: Optional[int] = None,
                cross_asset_available: Optional[int] = None) -> EntrySignal:
    notes: List[str] = []

    if direction == "CALL":
        if drawdown is None:
            raise ValueError("CALL entries require drawdown")
        depth = dip_component(drawdown)
    elif direction == "PUT":
        if runup_z is None:
            raise ValueError("PUT entries require runup_z")
        depth = runup_component(runup_z)
    else:
        raise ValueError(f"direction must be CALL or PUT, got {direction!r}")

    if vix3m_ratio is None:
        term = 0.0
        notes.append("VIX3M unavailable — term component scored 0")
    else:
        term = term_component(vix3m_ratio)

    components = {
        "depth": depth,
        "vix": vix_component(vix_level, vix_spike_ratio),
        "event": event_component(event_type),
        "term": term,
    }

    if direction == "PUT":
        # Cross-asset is display-only on the put side (zero validated put
        # history, spec 2026-07-22): four legacy components renormalized.
        score = sum(components.values()) * RENORM_NO_CROSS
    elif cross_asset_available is None or cross_asset_available == 0:
        score = sum(components.values()) * RENORM_NO_CROSS
        notes.append("cross-asset unavailable — scored on 4 components "
                     "renormalized (x100/85)")
    else:
        if cross_asset_count is None:
            raise ValueError(
                "cross_asset_available given without cross_asset_count")
        components["cross_asset"] = cross_asset_component(
            cross_asset_count, cross_asset_available)
        if cross_asset_available < 3:
            notes.append(f"cross-asset degraded: {cross_asset_available} "
                         f"of 3 assets available")
        score = sum(components.values())

    if score >= TIER_FULL:
        tier = Tier.FULL
    elif score >= TIER_HALF:
        tier = Tier.HALF
    elif score >= TIER_PILOT:
        tier = Tier.PILOT
    else:
        tier = Tier.SKIP

    if direction == "PUT" and tier != Tier.SKIP and score < PUT_SKIP_FLOOR:
        notes.append(f"put score {score:.1f} < legacy floor "
                     f"{PUT_SKIP_FLOOR:.0f} — SKIP (calls-only recalibration "
                     f"does not loosen the put gate)")
        tier = Tier.SKIP

    if direction == "PUT" and tier in (Tier.FULL, Tier.HALF):
        notes.append(
            f"put side unvalidated — tier {tier.value} capped to PILOT")
        tier = Tier.PILOT

    return EntrySignal(direction, score, tier,
                       TIER_SIZING[tier] * FUND_SIZE, components, notes)
