"""TACO skill constants. Parameters marked # CALIBRATION are placeholders
until scripts/taco/calibrate.py output is reviewed against your own
history of long-dated macro-event entries."""

from datetime import date
from enum import Enum


class EventType(str, Enum):
    TARIFF_TACO = "TARIFF_TACO"
    GEOPOLITICAL = "GEOPOLITICAL"
    FED_REGIME_SHIFT = "FED_REGIME_SHIFT"
    ECON_DATA = "ECON_DATA"
    UNKNOWN = "UNKNOWN"


class Tier(str, Enum):
    FULL = "FULL"
    HALF = "HALF"
    PILOT = "PILOT"
    SKIP = "SKIP"


# ── Entry score weights (sum 100) ────────────────────────────────────────────
# Rebalanced to make room for the cross-asset confirmation component.
# Tune the split based on your own factor-importance analysis.
WEIGHT_DIP = 30.0
WEIGHT_VIX = 21.0
WEIGHT_EVENT = 21.0
WEIGHT_TERM = 13.0
WEIGHT_CROSS_ASSET = 15.0
# CALL score without cross-asset data (and every PUT score) renormalizes the
# four legacy components back to a 0-100 scale.
RENORM_NO_CROSS = 100.0 / 85.0

# Event-type points (of WEIGHT_EVENT). Fast mean-reverters score high.
EVENT_SCORES = {
    EventType.TARIFF_TACO: 21.0,
    EventType.GEOPOLITICAL: 18.5,
    EventType.FED_REGIME_SHIFT: 10.1,
    EventType.ECON_DATA: 8.4,
    EventType.UNKNOWN: 4.2,
}

# Component scales (linear lo→hi maps to 0→max points).
# Fitted against a history of long-dated macro-event entries — refit against
# your own trade history before relying on these. All CALIBRATION-marked
# values below are placeholders.
DIP_MIN_PCT = 1.5        # CALIBRATION
DIP_MAX_PCT = 6.0        # CALIBRATION
RUNUP_MIN_Z = 1.0        # CALIBRATION — puts side has no history; unchanged
RUNUP_MAX_Z = 3.0        # CALIBRATION
VIX_LEVEL_MIN = 16.0     # CALIBRATION
VIX_LEVEL_MAX = 30.0     # CALIBRATION
VIX_LEVEL_PTS = 12.6     # CALIBRATION
VIX_SPIKE_MIN = 1.1      # last / 10d mean
VIX_SPIKE_MAX = 1.6
VIX_SPIKE_PTS = 8.4      # CALIBRATION
TERM_RATIO_MIN = 0.90    # CALIBRATION
TERM_RATIO_MAX = 1.10    # CALIBRATION

# ── Cross-asset confirmation ─────────────────────────────────────────────────
CROSS_ASSET_SYMBOLS = ("TLT", "UUP", "USO")
# Noise thresholds in % move since event onset. CALIBRATION — tune based on
# your own cross-asset confirmation analysis.
CROSS_ASSET_THRESHOLDS = {"TLT": 1.0, "UUP": 0.5, "USO": 3.0}

# Tier thresholds on the 0-100 score. CALIBRATION — tune based on your own
# outcome analysis.
TIER_FULL = 65.0         # CALIBRATION
TIER_HALF = 42.0         # CALIBRATION
TIER_PILOT = 27.0        # CALIBRATION

# Put-side SKIP floor. Puts intentionally keep a separate boundary from the
# CALL-side tier thresholds above — puts have less validated history.
PUT_SKIP_FLOOR = 30.0

# ── Sizing (ADVISORY ONLY — never enforced) ─────────────────────────────────
FUND_SIZE = 100_000.0    # advisory capital pool for TACO trades — set to
                          # whatever you're actually earmarking
TIER_SIZING = {Tier.FULL: 0.40, Tier.HALF: 0.20, Tier.PILOT: 0.05,
               Tier.SKIP: 0.0}

# ── Structure playbook ───────────────────────────────────────────────────────
MIN_DTE = 90             # hard gate — part of the strategy definition
STRIKE_OTM_MIN = 0.03
STRIKE_OTM_MAX = 0.07
TARGET_DTE_MIN = 270     # 9-12 months preferred
TARGET_DTE_MAX = 365

# ── Exit engine ──────────────────────────────────────────────────────────────
RETRACE_TARGET = 0.50          # CALIBRATION — fraction of the dip/runup that
                               # must retrace before the exit engine targets it
THESIS_VIX_SESSIONS = 5        # VIX normal this many sessions w/o target => REVIEW
GIVEBACK_ARM_FRACTION = 0.25   # rebound must reach this fraction of dip before
                               # the >50% giveback REVIEW can fire (noise guard)

# ── IPO wind-down guard ──────────────────────────────────────────────────────
IPO_EXPECTED_DATE = date(2026, 10, 1)   # adjust to your own event's expected date
IPO_FUND_RELEASED = False               # flip True post-IPO to release the fund
WINDDOWN_BANNER_DAYS = 60               # countdown banner from T-60 (Aug 2)
WINDDOWN_LOCK_DAYS = 30                 # entries refused from T-30 (Sep 1)
WINDDOWN_LIQUIDATE_DAYS = 14            # liquidation flags from T-14 (Sep 17)
