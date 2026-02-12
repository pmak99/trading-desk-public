"""
Skew analyzer for directional bias detection.

Simplified port from core/src/application/metrics/skew_enhanced.py.
Analyzes put/call IV skew to determine market directional bias.
DirectionalBias enum imported from common/enums.py.
"""

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

# Ensure common/ is importable
_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.enums import DirectionalBias  # noqa: E402
from src.core.logging import log


@dataclass
class SkewAnalysis:
    """Skew analysis result."""
    ticker: str
    directional_bias: DirectionalBias
    slope: float          # First derivative at ATM
    confidence: float     # R-squared of polynomial fit
    num_points: int       # Data points used


# Thresholds (from 2.0)
THRESHOLD_NEUTRAL = 30.0   # |slope| <= 30 → NEUTRAL
THRESHOLD_WEAK = 80.0      # 30 < |slope| <= 80 → WEAK
THRESHOLD_STRONG = 150.0   # |slope| > 150 → STRONG
MIN_CONFIDENCE = 0.15      # Minimum R² × slope_strength to trust signal
MIN_POINTS = 5             # Minimum data points for reliable fit
MAX_DISTANCE_PCT = 0.15    # Sample strikes within ±15%
MIN_DISTANCE_PCT = 0.02    # Skip strikes within ±2% (ATM)


def analyze_skew(
    ticker: str,
    stock_price: float,
    options_chain: List[Dict[str, Any]],
) -> Optional[SkewAnalysis]:
    """
    Analyze volatility skew from options chain data.

    Uses polynomial fitting on OTM put/call IV differences to detect
    directional bias in the options market.

    Args:
        ticker: Stock symbol
        stock_price: Current stock price
        options_chain: Tradier options chain with greeks

    Returns:
        SkewAnalysis or None if insufficient data
    """
    if not options_chain or stock_price <= 0:
        return None

    # Group options by strike
    strikes: Dict[float, Dict[str, Dict]] = {}
    for opt in options_chain:
        strike = opt.get("strike")
        opt_type = opt.get("option_type", "").lower()
        greeks = opt.get("greeks", {})
        iv = greeks.get("mid_iv") if greeks else None

        if strike is None or iv is None or iv <= 0:
            continue

        if strike not in strikes:
            strikes[strike] = {}
        strikes[strike][opt_type] = {"iv": iv, "option": opt}

    # Collect skew points (moneyness, put_iv - call_iv)
    points: List[Tuple[float, float]] = []

    for strike_price, opts in strikes.items():
        if "put" not in opts or "call" not in opts:
            continue

        # Calculate moneyness
        moneyness = (strike_price - stock_price) / stock_price

        # Skip ATM and far OTM strikes
        if abs(moneyness) < MIN_DISTANCE_PCT:
            continue
        if abs(moneyness) > MAX_DISTANCE_PCT:
            continue

        put_iv = opts["put"]["iv"]
        call_iv = opts["call"]["iv"]
        skew = put_iv - call_iv

        points.append((moneyness, skew))

    if len(points) < MIN_POINTS:
        log("debug", "Insufficient skew points", ticker=ticker, points=len(points))
        return None

    # Fit polynomial and extract characteristics
    try:
        analysis = _fit_polynomial(ticker, points)
        log("debug", "Skew analysis complete",
            ticker=ticker,
            bias=analysis.directional_bias.value,
            slope=round(analysis.slope, 2),
            confidence=round(analysis.confidence, 3),
            points=analysis.num_points)
        return analysis
    except Exception as e:
        log("warn", "Skew fit failed", ticker=ticker, error=str(e))
        return None


def _fit_polynomial(
    ticker: str,
    points: List[Tuple[float, float]],
) -> SkewAnalysis:
    """
    Fit 2nd-degree polynomial to skew data.

    skew(x) = a*x² + b*x + c
    where x = moneyness

    ATM slope = b (first derivative at x=0)
    """
    moneyness_vals, skew_vals = zip(*points)
    x = np.array(moneyness_vals)
    y = np.array(skew_vals)

    # Fit polynomial: y = ax² + bx + c
    coeffs = np.polyfit(x, y, deg=2)
    a, b, c = coeffs

    # Calculate R-squared
    y_pred = np.polyval(coeffs, x)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    slope_atm = b  # First derivative at x=0

    # Determine directional bias
    abs_slope = abs(slope_atm)

    if abs_slope <= THRESHOLD_NEUTRAL:
        bias = DirectionalBias.NEUTRAL
    elif slope_atm < 0:  # Negative slope = put skew (bearish protection)
        if abs_slope > THRESHOLD_STRONG:
            bias = DirectionalBias.STRONG_BEARISH
        elif abs_slope > THRESHOLD_WEAK:
            bias = DirectionalBias.BEARISH
        else:
            bias = DirectionalBias.WEAK_BEARISH
    else:  # Positive slope = call skew (bullish speculation)
        if abs_slope > THRESHOLD_STRONG:
            bias = DirectionalBias.STRONG_BULLISH
        elif abs_slope > THRESHOLD_WEAK:
            bias = DirectionalBias.BULLISH
        else:
            bias = DirectionalBias.WEAK_BULLISH

    # Check confidence threshold
    slope_strength = min(1.0, abs_slope / THRESHOLD_STRONG)
    bias_confidence = r_squared * slope_strength

    if bias_confidence < MIN_CONFIDENCE and bias != DirectionalBias.NEUTRAL:
        log("debug", "Low skew confidence, forcing NEUTRAL",
            ticker=ticker, confidence=bias_confidence, was=bias.value)
        bias = DirectionalBias.NEUTRAL

    return SkewAnalysis(
        ticker=ticker,
        directional_bias=bias,
        slope=slope_atm,
        confidence=r_squared,
        num_points=len(points),
    )
