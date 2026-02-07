"""
Scan module constants - all hardcoded thresholds, scoring weights, and configuration values.

These constants are shared across scan submodules to ensure consistency.
"""

import re

# Alpha Vantage free tier rate limits
ALPHA_VANTAGE_CALLS_PER_MINUTE = 5
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
