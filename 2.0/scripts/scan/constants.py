"""
Scan module constants - all hardcoded thresholds, scoring weights, and configuration values.

These constants are shared across scan submodules to ensure consistency.
"""

import re

# Finnhub free tier rate limits
FINNHUB_CALLS_PER_MINUTE = 60
RATE_LIMIT_PAUSE_SECONDS = 60

# Cache configuration
CACHE_L1_TTL_SECONDS = 3600      # 1 hour in-memory cache
CACHE_L2_TTL_SECONDS = 518400    # 6 days persistent cache (until next Monday)
CACHE_MAX_L1_SIZE = 100          # Max items in L1 memory cache

# Backfill configuration
BACKFILL_TIMEOUT_SECONDS = 120   # 2 minutes timeout for backfill subprocess
BACKFILL_YEARS = 3               # Years of historical data to backfill

# Trading day adjustment
MAX_TRADING_DAY_ITERATIONS = 10  # Max iterations to find next trading day (handles holiday clusters)

# API rate limiting
API_CALL_DELAY = 0.2             # Delay between API calls to respect rate limits

# Composite quality scoring constants (Dec 2025)
# OPTIMIZED via A/B testing with Monte Carlo simulation (100 iterations)
# Key findings:
#   - Edge score REMOVED: 80-95% correlated with VRP (redundant)
#   - Continuous scoring: Eliminates cliff effects, improves correlation
#   - Higher VRP target (4.0): More selective for quality trades
#   - VRP dominates: Primary edge signal should outweigh secondary factors
#
# Weight Rationale (Dec 6 revision):
#   - VRP is THE edge signal - a 3.87x VRP should beat 2.54x VRP
#   - Move is secondary risk factor, not primary edge
#   - Original 45/35 split let move penalty offset VRP advantage too much
#
# A/B Test Results (vs old config):
#   - Score separation: +38% (17.4 -> 24.0)
#   - Score-PnL correlation: +12% (0.196 -> 0.22)
#   - Win rate delta: +5% (52% -> 57%)

# VRP Factor (55 points) - PRIMARY edge signal
SCORE_VRP_MAX_POINTS = 55                   # Dominant weight - VRP is the core edge metric
SCORE_VRP_TARGET = 4.0                      # Higher bar for full points - more selective
SCORE_VRP_USE_LINEAR = True                 # Continuous scaling, no hard cap at target

# Edge Factor (DISABLED) - Removed due to redundancy with VRP
# edge_score = vrp_ratio / (1 + consistency), so ~85% correlated with VRP
# Having both double-counts the same signal, hurting performance
SCORE_EDGE_MAX_POINTS = 0                   # DISABLED - redundant with VRP
SCORE_EDGE_TARGET = 1.0                     # N/A (disabled)

# Liquidity Factor (20 points) - Moderate penalty for illiquidity
# 4-Tier System: EXCELLENT (>=5x OI, <=8%), GOOD (2-5x, 8-12%), WARNING (1-2x, 12-15%), REJECT (<1x, >15%)
SCORE_LIQUIDITY_MAX_POINTS = 20             # Moderate weight (don't over-penalize)
SCORE_LIQUIDITY_EXCELLENT_POINTS = 20       # Full points for excellent liquidity (>=5x OI, <=8% spread)
SCORE_LIQUIDITY_GOOD_POINTS = 16            # Good liquidity - tradeable at full size (2-5x OI, 8-12% spread)
SCORE_LIQUIDITY_WARNING_POINTS = 12         # Low liquidity - consider reducing size (1-2x OI, 12-15% spread)
SCORE_LIQUIDITY_REJECT_POINTS = 4           # Very low - small penalty, not zero (some REJECT trades win!)

# Implied Move Factor (25 points) - Secondary risk factor
# Lower implied move = easier trade, but VRP edge matters more
SCORE_MOVE_MAX_POINTS = 25                  # Reduced weight - secondary to VRP
SCORE_MOVE_USE_CONTINUOUS = True            # Linear interpolation (no cliff effects)
SCORE_MOVE_BASELINE_PCT = 20.0              # 20% implied move = 0 points

# Market hours indicator
MARKET_CLOSED_INDICATOR = "*"  # Appended to tier when using OI-only scoring

# Discrete thresholds (fallback if continuous disabled)
SCORE_MOVE_EASY_THRESHOLD = 8.0             # Implied move % considered "easy" (full points)
SCORE_MOVE_MODERATE_THRESHOLD = 12.0        # Implied move % considered "moderate"
SCORE_MOVE_MODERATE_POINTS = 18             # Points for moderate difficulty (scaled to 25 max)
SCORE_MOVE_CHALLENGING_THRESHOLD = 15.0     # Implied move % considered "challenging"
SCORE_MOVE_CHALLENGING_POINTS = 11          # Points for challenging difficulty
SCORE_MOVE_EXTREME_POINTS = 4               # Points for extreme difficulty (>15%)
SCORE_DEFAULT_MOVE_POINTS = 12.5            # Default when implied move is missing (middle)

# Liquidity tier priority for sorting (lower number = higher priority)
# 4-Tier System: EXCELLENT > GOOD > WARNING > REJECT
LIQUIDITY_PRIORITY_ORDER = {
    'EXCELLENT': 0,
    'GOOD': 1,
    'WARNING': 2,
    'REJECT': 3,
    'UNKNOWN': 4
}

# Harvest scoring constants (30-45 DTE non-earnings premium scan)
# Scoring: IV Rank (40) + IV/HV ratio (25) + Skew (15) + Liquidity proxy (20) = 100 max
HARVEST_IV_RANK_MIN = 60.0          # Hard gate — top 40% of 52-week IV range required
HARVEST_IV_RANK_MAX_POINTS = 40.0   # Linear: rank=60→0pts, rank=100→40pts
HARVEST_IV_HV_MAX_POINTS = 25.0     # Linear: ratio=1.0→0pts, ratio=2.0+→25pts (capped)
HARVEST_IV_HV_CAP = 2.0             # No extra credit above 2.0x IV/HV ratio
HARVEST_SKEW_MAX_POINTS = 15.0
HARVEST_LIQUIDITY_MAX_POINTS = 20.0
HARVEST_EARNINGS_EXCLUSION_DAYS = 30  # Exclude tickers with earnings within this window
HARVEST_TOP_N = 20                    # Default maximum candidates to display

# Harvest universe (45 DTE non-earnings sleeve) — fixed per sleeve rules.
# Index names get a real 52-week IVR from their CBOE vol index (yfinance
# symbols below); single names have NO live IVR source until iv_history
# matures (~Jun 2027) and must be IVR-verified at the broker before entry.
HARVEST_UNIVERSE_INDEX = ("SPY", "QQQ", "IWM")
HARVEST_UNIVERSE_STOCK = ("AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN")
HARVEST_VOL_INDEX_FOR = {"SPY": "^VIX", "QQQ": "^VXN"}  # ^RVX (IWM) discontinued on Yahoo — IWM degrades to the ungated broker-check path

# Skew label → score mapping (r_slp_30 → RSLP30 bucket → points)
# Mirrors RSLP30_THRESHOLDS from common/constants.py (strong bull = best, bearish = penalised)
HARVEST_SKEW_SCORES: dict = {
    'STRONG_BULLISH': 15.0,
    'BULLISH':        14.0,
    'WEAK_BULLISH':   12.0,
    'NEUTRAL':        10.0,
    'WEAK_BEARISH':    6.0,
    'BEARISH':         3.0,
}
HARVEST_SKEW_NULL_SCORE = 8.0        # Middle-ground default when r_slp_30 is NULL

# Megacap correlation cluster — highly correlated in stress scenarios.
# Running 2+ of these simultaneously creates concentrated directional risk.
MEGACAP_CLUSTER: frozenset = frozenset({'AAPL', 'MSFT', 'NVDA', 'GOOG', 'GOOGL', 'META', 'AMZN'})

# Pre-compiled regex patterns for company name cleaning (performance optimization)
_COMPANY_SUFFIX_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in [
        r',?\s+Inc\.?$',
        r',?\s+Incorporated$',
        r',?\s+Corp\.?$',
        r',?\s+Corporation$',
        r',?\s+Ltd\.?$',
        r',?\s+Limited$',
        r',?\s+LLC$',
        r',?\s+L\.L\.C\.?$',
        r',?\s+Co\.?$',
        r',?\s+Company$',
        r',?\s+PLC$',
        r',?\s+P\.L\.C\.?$',
        r',?\s+Plc$',
        r',?\s+LP$',
        r',?\s+L\.P\.?$',
    ]
]
_TRAILING_AMPERSAND_PATTERN = re.compile(r'\s*&\s*$')
