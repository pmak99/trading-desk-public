"""Shared constants for Trading Desk subsystems.

Liquidity tiers, scoring weights, sentiment direction bands and ORATS constants.
VRP tiers live in 2.0/src/domain/vrp_tier.py and TRR in 2.0/src/domain/tail_risk.py.
The ORATS switch is `config.api.orats_enabled` (2.0/src/config/config.py).
"""

# Two stored earnings dates this close are the same event, not two quarters.
# Real consecutive quarters are ~90 days apart; spurious duplicates found in the
# Sep 2026 sweep were mostly 3-8 days apart, which a <=2 audit called clean.
# Used by every Yahoo-sourced calendar insert and by the duplicate-move audit.
EARNINGS_SAME_EVENT_DAYS = 15

# Longest gap between two stored earnings dates (calendar or historical_moves,
# whichever is more recent) that is still adjacent quarters. Traded tickers on
# 2026-09-13: 130 gaps of 100-114 days (late fiscal-year reports), then a valley
# -- 5 of 119-139 -- then 22 of 175-280, which are lost quarters. One lost quarter
# spans two report intervals, so it cannot read much under ~175.
#
# 150, not 115 (until 2026-09-16): a 16-week fiscal fourth quarter reads 119 days with
# nothing missing (COST 2026-05-28 -> 09-24, AZO 05-26 -> 09-22), and at 115 every
# analysis of those names warned STALE HISTORY each September. 150 sits in the valley
# with 25 days to spare below the shortest lost quarter.
#
# Shared by scripts/integrity_report.py's weekly sweep (scoped to traded-or-analyzed
# tickers) and src/application/services/stale_history.py's live per-ticker guard,
# added 2026-09-16 after LEN's 2026-03-12 and 2026-06-11 quarters sat missing from
# both tables for six months -- invisible to the weekly check, which at the time
# only scanned traded tickers, and LEN had never been traded. Found only because
# someone ran /analyze on it and noticed the last-4-quarters table was all 2025
# dates a day after LEN reported.
QUARTER_GAP_DAYS = 150


# =============================================================================
# Liquidity Thresholds - RELAXED Feb 2026
# =============================================================================
# Spread thresholds (bid-ask spread as % of mid price)
SPREAD_EXCELLENT = 12.0   # <= 12%
SPREAD_GOOD = 18.0        # <= 18%
SPREAD_WARNING = 25.0     # <= 25%
# > 25% = REJECT

# Tier ordering (for min/max comparisons)
TIER_ORDER = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}

# Liquidity tier scores for composite scoring
LIQUIDITY_SCORES = {
    "EXCELLENT": 100,
    "GOOD": 80,
    "WARNING": 60,
    "REJECT": 20,  # Still penalized but strategies allowed
}


# =============================================================================
# Scoring Weights - Composite score calculation
# =============================================================================
WEIGHT_VRP = 0.55
WEIGHT_MOVE = 0.25
WEIGHT_LIQUIDITY = 0.20


# =============================================================================
# Sentiment direction bands (score -1..+1)
# Labels only, quoted by /collect and /council: sentiment changes no score,
# cutoff, size or displayed direction (CLAUDE.md §7). The +1% score overlay,
# the contrarian sizing and sentiment's adjustment of the displayed direction
# (with its zeroed bearish directions) were removed 2026-09-14.
# =============================================================================
SENTIMENT_STRONG_BULLISH_THRESHOLD = 0.6
SENTIMENT_BULLISH_THRESHOLD = 0.2
SENTIMENT_BEARISH_THRESHOLD = -0.2
SENTIMENT_STRONG_BEARISH_THRESHOLD = -0.6


# =============================================================================
# ORATS snapshot
# =============================================================================
# The ORATS_ENABLED switch is APIConfig.orats_enabled (2.0/src/config/config.py)
# since 2026-09-14, off unless set. It was read here at import and defaulted on.

# Staleness guard for the position_limits ORATS snapshot (Jul 2026).
# When ORATS_ENABLED=true, snapshot rows older than this are treated as
# absent - protects against flipping the flag back on (frozen 2026-06-24 data
# would otherwise silently feed sizing Rules A/B and compound-risk detection).
# The snapshot is permanently frozen since the Postgres port deleted the
# refresh script, so this guard now always fires on that data.
# 14 days = two missed weekly refreshes; display warnings fire at 10.
ORATS_SNAPSHOT_MAX_AGE_DAYS: int = 14


# =============================================================================
# ORATS Signal Fusion (2026-05-26-orats-signal-fusion-design.md)
# =============================================================================

# rSlp30 distribution (measured May 2026, 423 position_limits tickers)
# Z-score normalization required: raw thresholds at 0 would classify 60% as STRONG_BULLISH
RSLP30_MEAN = 1.036
RSLP30_STD  = 1.294

# Sorted breakpoints: (upper_bound, numeric_level) → first threshold exceeded wins
# Produces balanced distribution: 6.6% | 15.6% | 17.7% | 21.3% | 18.0% | 14.9% | 5.9%
RSLP30_THRESHOLDS = [
    (RSLP30_MEAN - 1.50 * RSLP30_STD, -3),  # < -0.905 → STRONG_BEARISH
    (RSLP30_MEAN - 0.75 * RSLP30_STD, -2),  # < +0.065 → BEARISH
    (RSLP30_MEAN - 0.25 * RSLP30_STD, -1),  # < +0.712 → WEAK_BEARISH
    (RSLP30_MEAN + 0.25 * RSLP30_STD,  0),  # < +1.359 → NEUTRAL
    (RSLP30_MEAN + 0.75 * RSLP30_STD, +1),  # < +2.006 → WEAK_BULLISH
    (RSLP30_MEAN + 1.50 * RSLP30_STD, +2),  # < +2.976 → BULLISH
    (float('inf'),                     +3),  # else      → STRONG_BULLISH
]

# At median Tradier R²=0.226, ORATS drives 69% of fused signal (0.5 / (0.226 + 0.5))
ORATS_SKEW_CONFIDENCE = 0.5

# Proxy backtest: iee/fcst 1.5-2.0x bucket → 16.7% win, negative mean per trade (n=6; provisional)
IEE_DIVERGENCE_RATIO  = 1.5

# Validated May 2026: 58.7% win below 2.0x (net positive) vs 48.3% win above (net negative)
FCST_ERN_IV_THRESHOLD = 2.0
