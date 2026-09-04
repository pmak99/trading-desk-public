"""
Enhanced Skew Analyzer - Phase 4 Algorithmic Optimization

Polynomial-fitted volatility skew analysis using multiple OTM points.
Provides superior edge detection compared to single-point skew.
"""

import logging
from datetime import date
from typing import List, Tuple, Optional
from dataclasses import dataclass

import numpy as np

from src.domain.types import Money, Percentage, Strike, OptionChain
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.protocols import OptionsDataProvider
from src.domain.enums import DirectionalBias

import sys as _sys
from pathlib import Path as _Path

_root = str(_Path(__file__).resolve().parent.parent.parent.parent.parent)
if _root not in _sys.path:
    _sys.path.insert(0, _root)

from common.constants import RSLP30_THRESHOLDS  # noqa: E402

logger = logging.getLogger(__name__)


_BIAS_NUMERIC = {
    DirectionalBias.STRONG_BEARISH: -3,
    DirectionalBias.BEARISH:        -2,
    DirectionalBias.WEAK_BEARISH:   -1,
    DirectionalBias.NEUTRAL:         0,
    DirectionalBias.WEAK_BULLISH:   +1,
    DirectionalBias.BULLISH:        +2,
    DirectionalBias.STRONG_BULLISH: +3,
}
_NUMERIC_BIAS = {v: k for k, v in _BIAS_NUMERIC.items()}


def bias_to_numeric(bias: DirectionalBias) -> int:
    return _BIAS_NUMERIC[bias]


def numeric_to_bias(n: int) -> DirectionalBias:
    clamped = max(-3, min(3, n))
    return _NUMERIC_BIAS[clamped]


def r_slp_30_to_numeric(r_slp_30: float) -> int:
    for upper_bound, level in RSLP30_THRESHOLDS:
        if r_slp_30 < upper_bound:
            return level
    return RSLP30_THRESHOLDS[-1][1]


def fuse_skew_bias(
    tradier_bias: DirectionalBias,
    tradier_conf: float,
    slope_atm,
    r_slp_30,
):
    """
    Confidence-weighted fusion of Tradier polynomial bias with a slope signal.

    Single source of truth for the fusion formula — used by both
    analyzer._fuse_skew_signals (live /analyze display) and
    store_bias_prediction (persisted predictions) so the two cannot drift.

    Slope signal priority: ORATS r_slp_30 at ORATS_SKEW_CONFIDENCE when
    available; otherwise the Tradier slope_atm proxy at
    TRADIER_PROXY_SKEW_CONFIDENCE (post-ORATS continuity path).

    Returns None when neither slope signal is available (fusion impossible).
    """
    from common.constants import (  # noqa: PLC0415
        ORATS_SKEW_CONFIDENCE, TRADIER_PROXY_SKEW_CONFIDENCE,
    )
    if r_slp_30 is not None:
        slope_numeric = r_slp_30_to_numeric(r_slp_30)
        slope_conf = ORATS_SKEW_CONFIDENCE
    elif slope_atm is not None:
        proxy = compute_tradier_r_slp30_proxy(slope_atm)
        slope_numeric = r_slp_30_to_numeric(proxy)
        slope_conf = TRADIER_PROXY_SKEW_CONFIDENCE
    else:
        return None

    tradier_numeric = bias_to_numeric(tradier_bias)
    fused = (tradier_numeric * tradier_conf + slope_numeric * slope_conf) \
            / (tradier_conf + slope_conf)
    return numeric_to_bias(round(fused))


def compute_tradier_r_slp30_proxy(slope_atm: float) -> float:
    """
    Approximate ORATS r_slp_30 from the Tradier polynomial skew slope_atm.

    Sign convention matches the polynomial classifier (and 5.0 skew.py):
    negative slope_atm = put skew = bearish → LOW r_slp_30; positive =
    call skew = bullish → HIGH r_slp_30. Confirmed empirically Jul 2026:
    corr(slope_atm, r_slp_30) = +0.45 across 42 ORATS-era tickers in
    bias_predictions (+0.29 excluding the financials cluster).

    Scale from the same OLS fit: Δr_slp_30 ≈ 0.0027 per slope_atm unit,
    so ±150 slope ≈ ±0.3σ (WEAK_* bucket), NOT ±2σ — a Tradier slope
    alone never reaches a STRONG_* bucket. Clamped at MEAN ± 2σ.

    (Pre-Jul-2026 version had the sign inverted and a 6x-hot ±150 → ±2σ
    scale — every proxy-path fusion pulled the wrong direction.)

    Accuracy caveat: noisy single-expiry snapshot vs ORATS' 30-day
    smoothing. Use at TRADIER_PROXY_SKEW_CONFIDENCE (0.3) in fusion, not 0.5.
    """
    from common.constants import RSLP30_MEAN, RSLP30_STD  # noqa: PLC0415
    _RSLP30_PER_SLOPE_UNIT = 0.0027  # OLS slope, Jul 2026 calibration
    raw = RSLP30_MEAN + slope_atm * _RSLP30_PER_SLOPE_UNIT
    lo, hi = RSLP30_MEAN - 2.0 * RSLP30_STD, RSLP30_MEAN + 2.0 * RSLP30_STD
    return max(lo, min(hi, raw))


@dataclass
class SkewAnalysis:
    """
    Polynomial skew analysis results.

    Attributes:
        ticker: Stock symbol
        expiration: Option expiration date
        stock_price: Current stock price
        skew_atm: Skew at ATM (positive = puts expensive)
        curvature: Second derivative (smile vs smirk)
        strength: Classification (smile, smirk, flat)
        directional_bias: Directional bias enum (7-level scale)
        confidence: Fit quality (R-squared)
        num_points: Number of data points used in fit
        slope_atm: First derivative at ATM (for bias strength)
        bias_confidence: Bias prediction confidence (0-1, R² adjusted by slope strength)
    """
    ticker: str
    expiration: date
    stock_price: Money
    skew_atm: Percentage
    curvature: float
    strength: str
    directional_bias: DirectionalBias
    confidence: float
    num_points: int
    slope_atm: float
    bias_confidence: float


class SkewAnalyzerEnhanced:
    """
    Enhanced volatility skew analyzer using polynomial fitting.

    Traditional skew analysis uses a single ATM put/call IV comparison.
    This enhanced version:
    1. Samples 5+ OTM points across the strike range
    2. Fits a 2nd-degree polynomial (parabola)
    3. Extracts ATM skew, curvature, and directional bias
    4. Classifies skew shape (smile vs smirk)

    Benefits:
    - More robust to bad data points
    - Detects directional bias
    - Quantifies smile/smirk strength
    - Better edge detection for rare setups
    """

    # Configuration
    MIN_POINTS = 3  # Minimum points for reliable fit (5 was over-rejecting sparse chains)
    MAX_DISTANCE_PCT = 0.15  # Sample strikes within ±15% of stock price
    MIN_DISTANCE_PCT = 0.02  # Skip strikes within ±2% (ATM)

    # Curvature threshold for smile classification (in IV%/moneyness² units)
    # Positive curvature > 1.0 indicates volatility smile (U-shaped, both OTM expensive)
    # Empirically derived from typical equity skew patterns
    SMILE_THRESHOLD = 1.0

    # Directional bias thresholds (7-level scale, in IV%/moneyness units)
    # Based on typical single-stock skew slopes of 5-15% IV across 10% moneyness
    # NOTE: Slope units are "percentage points per decimal moneyness"
    # Example: slope=100 means 1% OTM strike has 1% higher IV (100 * 0.01 = 1%)
    THRESHOLD_NEUTRAL = 30.0   # |slope| <= 30 → NEUTRAL (< 0.3% IV change per 1% moneyness)
    THRESHOLD_WEAK = 80.0      # 30 < |slope| <= 80 → WEAK bias (0.3-0.8% IV change per 1% moneyness)
    THRESHOLD_STRONG = 150.0   # |slope| > 150 → STRONG bias (> 1.5% IV change per 1% moneyness)

    # Bias confidence thresholds
    MIN_CONFIDENCE = 0.15     # Minimum confidence to trust bias signal (R² × slope_strength)
    MAX_TYPICAL_SLOPE = 150.0 # Maximum typical slope for normalization (matches THRESHOLD_STRONG)

    def __init__(self, provider: OptionsDataProvider):
        self.provider = provider

    def analyze_skew_curve(
        self,
        ticker: str,
        expiration: date
    ) -> Result[SkewAnalysis, AppError]:
        """
        Analyze volatility skew using polynomial fitting.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date

        Returns:
            Result with SkewAnalysis or AppError
        """
        logger.info(f"Analyzing skew curve: {ticker} exp {expiration}")

        # Get option chain
        chain_result = self.provider.get_option_chain(ticker, expiration)
        if chain_result.is_err:
            return Err(chain_result.error)

        chain = chain_result.value
        stock_price = float(chain.stock_price.amount)

        # Collect skew points (distance from ATM, skew value)
        skew_points = self._collect_skew_points(chain, stock_price)

        if len(skew_points) < self.MIN_POINTS:
            return Err(
                AppError(
                    ErrorCode.NODATA,
                    f"Insufficient data points for skew fit: "
                    f"{len(skew_points)} < {self.MIN_POINTS}"
                )
            )

        # Fit polynomial and analyze
        try:
            analysis = self._fit_and_analyze(
                ticker,
                expiration,
                chain.stock_price,
                skew_points
            )

            logger.info(
                f"{ticker}: Skew ATM={analysis.skew_atm.value:.2f}%, "
                f"Strength={analysis.strength}, "
                f"Bias={analysis.directional_bias}, "
                f"Points={analysis.num_points}"
            )

            return Ok(analysis)

        except Exception as e:
            logger.error(f"Skew fit failed: {e}")
            return Err(
                AppError(
                    ErrorCode.CALCULATION,
                    f"Polynomial fit failed: {str(e)}"
                )
            )

    def _collect_skew_points(
        self,
        chain: OptionChain,
        stock_price: float
    ) -> List[Tuple[float, float]]:
        """
        Collect (moneyness, skew) points for fitting.

        Moneyness = (strike - stock) / stock
        Skew = put_iv - call_iv

        Returns:
            List of (moneyness, skew) tuples
        """
        points = []
        ticker = chain.ticker  # Extract ticker for logging

        for strike in chain.strikes:
            strike_price = float(strike.price)

            # Calculate moneyness (distance from ATM as %)
            moneyness = (strike_price - stock_price) / stock_price

            # Skip if too close to ATM (avoid ATM strike)
            if abs(moneyness) < self.MIN_DISTANCE_PCT:
                continue

            # Skip if too far OTM
            if abs(moneyness) > self.MAX_DISTANCE_PCT:
                continue

            # Get IVs
            call = chain.calls.get(strike)
            put = chain.puts.get(strike)

            if not call or not put:
                logger.debug(
                    f"{ticker}: Strike {strike_price:.2f} missing "
                    f"{'call' if not call else 'put'} - skipping from skew fit"
                )
                continue

            if not call.implied_volatility or not put.implied_volatility:
                continue

            # Skip illiquid options
            if not call.is_liquid or not put.is_liquid:
                continue

            put_iv = put.implied_volatility.value
            call_iv = call.implied_volatility.value
            skew = put_iv - call_iv

            points.append((moneyness, skew))

        return points

    def _fit_and_analyze(
        self,
        ticker: str,
        expiration: date,
        stock_price: Money,
        points: List[Tuple[float, float]]
    ) -> SkewAnalysis:
        """
        Fit polynomial and extract skew characteristics.

        Polynomial: skew(x) = a*x^2 + b*x + c
        where x = moneyness = (strike - stock) / stock

        ATM skew = c (value at x=0)
        Curvature = 2*a (second derivative)
        Directional bias = sign(b) (first derivative at ATM)
        """
        # Separate x and y values
        moneyness_vals, skew_vals = zip(*points)
        x = np.array(moneyness_vals)
        y = np.array(skew_vals)

        # Fit 2nd degree polynomial: y = ax^2 + bx + c
        coeffs = np.polyfit(x, y, deg=2)
        a, b, c = coeffs

        # Calculate R-squared for fit quality
        y_pred = np.polyval(coeffs, x)
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        # Extract characteristics
        skew_atm = Percentage(c)  # Value at x=0
        curvature = 2 * a  # Second derivative
        slope_atm = b  # First derivative at x=0

        # Classify skew shape
        if curvature > self.SMILE_THRESHOLD:
            strength = "smile"  # Both puts and calls expensive
        elif curvature < -self.SMILE_THRESHOLD:
            strength = "inverse_smile"  # Both puts and calls cheap
        else:
            strength = "smirk"  # Normal asymmetric skew

        # Determine directional bias with 7-level strength scale
        # NOTE: In typical equity skew, OTM puts have higher IV than OTM calls
        # - Negative slope (skew decreases as strikes increase) = bearish protection demand
        # - Positive slope (skew increases as strikes increase) = bullish call demand
        abs_slope = abs(slope_atm)

        if abs_slope <= self.THRESHOLD_NEUTRAL:
            directional_bias = DirectionalBias.NEUTRAL
        elif slope_atm < 0:  # Negative slope = put skew (bearish protection)
            if abs_slope > self.THRESHOLD_STRONG:
                directional_bias = DirectionalBias.STRONG_BEARISH
            elif abs_slope > self.THRESHOLD_WEAK:
                directional_bias = DirectionalBias.BEARISH
            else:  # THRESHOLD_NEUTRAL < abs_slope <= THRESHOLD_WEAK
                directional_bias = DirectionalBias.WEAK_BEARISH
        else:  # Positive slope = call skew (bullish speculation)
            if abs_slope > self.THRESHOLD_STRONG:
                directional_bias = DirectionalBias.STRONG_BULLISH
            elif abs_slope > self.THRESHOLD_WEAK:
                directional_bias = DirectionalBias.BULLISH
            else:  # THRESHOLD_NEUTRAL < abs_slope <= THRESHOLD_WEAK
                directional_bias = DirectionalBias.WEAK_BULLISH

        # bias_confidence = R² only (slope strength belongs in level, not confidence)
        # Previous formula (R² × slope_strength) returned ~0.0002 for all tickers
        # because slopes are tiny in absolute IV units — permanently forcing NEUTRAL.
        bias_confidence = r_squared

        # Reject noisy fits: R²<0.30 → NEUTRAL regardless of slope
        # Calibrated for earnings-time analysis (chains more pronounced at earnings)
        if r_squared < 0.30 and directional_bias != DirectionalBias.NEUTRAL:
            logger.debug(
                f"{ticker}: Low R² {r_squared:.3f} < 0.30, "
                f"forcing NEUTRAL (was {directional_bias.value})"
            )
            directional_bias = DirectionalBias.NEUTRAL

        return SkewAnalysis(
            ticker=ticker,
            expiration=expiration,
            stock_price=stock_price,
            skew_atm=skew_atm,
            curvature=curvature,
            strength=strength,
            directional_bias=directional_bias,
            confidence=r_squared,
            num_points=len(points),
            slope_atm=slope_atm,
            bias_confidence=bias_confidence,
        )
