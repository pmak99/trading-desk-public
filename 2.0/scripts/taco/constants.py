"""TACO skill constants. Parameters marked # CALIBRATION are placeholders
until scripts/taco/calibrate.py output is reviewed (see the plan Task 8 and
docs/superpowers/specs/2026-07-14-taco-index-macro-skill-design.md).

⚠️ Every value marked "calibrated" is IN-SAMPLE. The Jul 14 and Jul 22 2026
passes fitted the scales, tier thresholds and retrace target to the long-dated
index entries through CALIBRATED_THROUGH (the winners' medians; tiers the
winners kept), and the win rates quoted for those entries were measured on the
same entries. They describe the fit, not the rule's edge. Evidence starts with
the entries after CALIBRATED_THROUGH, which calibrate.py prints in their own
section.

Two of the entries both passes fitted as calls were puts: SPY 2025-02-18 and
SPX 2025-05-13 (their trade_journal legs are puts, and the cost basis prices
as puts). calibrate.py now takes the side from the journal; the values below
have not been refitted without those two rows.
"""

from datetime import date
from enum import Enum

# Last day of the entries the calibration passes were fitted on (the Jul 22
# 2026 cross-asset pass). Entries after it are out-of-sample.
CALIBRATED_THROUGH = date(2026, 7, 22)


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
# Rebalanced ×0.85 (rounded) 2026-07-22 to make room for the cross-asset
# confirmation component (spec 2026-07-22). Was 35/25/25/15.
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
# Calibrated 2026-07-14 against the 21 long-dated historical entries
# (scripts/taco/calibrate.py): winners entered at median 2.6% drawdown /
# VIX 21 / mild contango — the original placeholder scales zeroed most of
# the winners. User approved "fit to history" 2026-07-14. IN-SAMPLE: those
# winners are the entries the scales were fitted to, not a validation of them.
DIP_MIN_PCT = 1.5        # calibrated (was 2.0)
DIP_MAX_PCT = 6.0        # calibrated (was 8.0)
RUNUP_MIN_Z = 1.0        # CALIBRATION — puts side has no history; unchanged
RUNUP_MAX_Z = 3.0        # CALIBRATION
VIX_LEVEL_MIN = 16.0     # calibrated (was 18.0)
VIX_LEVEL_MAX = 30.0     # calibrated (was 35.0)
VIX_LEVEL_PTS = 12.6     # was 15 — ×0.84 rebalance 2026-07-22
VIX_SPIKE_MIN = 1.1      # last / 10d mean
VIX_SPIKE_MAX = 1.6
VIX_SPIKE_PTS = 8.4      # was 10 — ×0.84 rebalance 2026-07-22
TERM_RATIO_MIN = 0.90    # calibrated (was 0.95) — winners entered in contango
TERM_RATIO_MAX = 1.10    # calibrated (was 1.15)

# ── Cross-asset confirmation (spec 2026-07-22) ──────────────────────────────
CROSS_ASSET_SYMBOLS = ("TLT", "UUP", "USO")
# Noise thresholds in % move since event onset. calibrated 2026-07-22
# (cross-asset pass, user-approved).
CROSS_ASSET_THRESHOLDS = {"TLT": 1.0, "UUP": 0.5, "USO": 3.0}

# Tier thresholds on the 0-100 score. calibrated 2026-07-22 (cross-asset pass,
# user-approved). IN-SAMPLE: chosen so the entries through CALIBRATED_THROUGH
# kept their intended tiers, so their tier-by-tier win rates cannot test them.
TIER_FULL = 65.0         # calibrated (was 70.0)
TIER_HALF = 42.0         # calibrated (was 45.0)
TIER_PILOT = 27.0        # calibrated (was 30.0)

# Put-side SKIP floor. The 65/42/27 recalibration (2026-07-22) was fitted on
# CALL rows only; the spec invariant is that put behavior stays numerically
# unchanged, so puts keep the legacy PILOT boundary (spec 2026-07-22, I1).
PUT_SKIP_FLOOR = 30.0

# ── Sizing (ADVISORY ONLY — never enforced) ─────────────────────────────────
FUND_SIZE = 400_000.0    # general TACO capital pool (IRA) — set this to your own
                          # pool; sizing off it is advisory only, never enforced
TIER_SIZING = {Tier.FULL: 0.40, Tier.HALF: 0.20, Tier.PILOT: 0.05,
               Tier.SKIP: 0.0}

# ── Structure playbook ───────────────────────────────────────────────────────
MIN_DTE = 90             # hard gate — part of the strategy definition;
                         # enforced by store.require_min_dte at every write
STRIKE_OTM_MIN = 0.03
STRIKE_OTM_MAX = 0.07
TARGET_DTE_MIN = 270     # 9-12 months preferred
TARGET_DTE_MAX = 365

# ── Exit engine ──────────────────────────────────────────────────────────────
RETRACE_TARGET = 0.50          # calibrated (was 0.62) — median winning exit
                               # captured 0.44 of the dip; user sold earlier
                               # than the 62% placeholder assumed. IN-SAMPLE:
                               # a target read off the same exits it now grades
THESIS_VIX_SESSIONS = 5        # VIX normal this many sessions w/o target => REVIEW
GIVEBACK_ARM_FRACTION = 0.25   # rebound must reach this fraction of dip before
                               # the >50% giveback REVIEW can fire (noise guard)

# ── IPO wind-down guard ──────────────────────────────────────────────────────
# Updated 2026-09-10 (was 2026-10-01) — Reuters exclusive (Sep 4-5 2026, most
# recent/specific sourcing) reported the schedule slipped: public prospectus
# now expected late Sep (was "early next week"), marketing begins mid-Oct at
# the earliest, listing completes "days before" the Nov 3 2026 midterms.
# 2026-10-30 is a point estimate inside that window, NOT a confirmed date —
# Anthropic has given no public timing guidance and Reuters explicitly flagged
# the plan as subject to further change. Re-verify before trusting this again.
IPO_EXPECTED_DATE = date(2026, 10, 30)  # adjust when Anthropic timing firms up
IPO_FUND_RELEASED = False               # flip True post-IPO to release the fund
WINDDOWN_BANNER_DAYS = 60               # countdown banner from T-60 (Aug 31)
WINDDOWN_LOCK_DAYS = 30                 # entries refused from T-30 (Sep 30)
WINDDOWN_LIQUIDATE_DAYS = 14            # liquidation flags from T-14 (Oct 16)
