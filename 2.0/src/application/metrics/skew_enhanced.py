"""
Skew classifier: the 25-delta risk reversal read off a fitted smile.

What is measured
----------------
Skew is the price of downside protection relative to upside: out-of-the-money
puts against out-of-the-money calls, each at its own delta. The standard
reading is the 25-delta risk reversal,

    RR25 = (IV of the 25Δ put − IV of the 25Δ call) / ATM IV

normalised by ATM IV so it compares across tickers and survives pre-earnings
IV inflation, which lifts every strike together. Positive RR25 = puts richer =
hedge demand = the bearish side of DirectionalBias.

Until Sep 2026 skew was put IV − call IV at the SAME strike. Put-call parity
makes those two IVs equal up to quote noise (rates, dividends, early exercise,
stale marks), so that difference measured noise, not the smile: a chain with a
steep, perfectly parity-consistent put skew read NEUTRAL.

How it is read
--------------
1. Take every liquid OTM quote with an IV and a delta: puts below spot, calls
   above, MIN_ABS_DELTA <= |Δ| < MAX_ABS_DELTA. ITM quotes never enter.
2. Place each on one axis, v = signed distance from 50Δ: v = |Δ| − 0.5 for a
   put (−0.40 … 0), v = 0.5 − Δ for a call (0 … +0.40). The 25Δ put sits at
   v = −0.25, the 25Δ call at +0.25, ATM at 0.
3. Fit IV(v) = a·v² + b·v + c by least squares and read IV at −0.25, 0, +0.25.
4. Refuse to read what the chain does not contain: at least MIN_POINTS quotes,
   MIN_POINTS_PER_WING on each side, and a quote at or beyond 25Δ on each side,
   so both 25Δ vols are interpolated, never extrapolated.

A 3-parameter fit through 3 points is exact interpolation (R² = 1 whatever the
quotes say), and with few spare degrees of freedom raw R² stays inflated — its
expectation under pure noise is 2/(n−1), 0.5 at n = 5. Fit quality is therefore
ADJUSTED R², which is 0 in expectation under noise at every n.

Thresholds are provisional
--------------------------
THRESHOLD_* were set from option structure, not fitted to outcomes (no stored
chains existed to fit them on). A symmetric event jump adds roughly the same
variance at every strike near the money, so pre-earnings RR25 is compressed
relative to a normal month: by that variance-additive estimate (not a
measurement), an ordinary name whose 30-day RR25 is ~12% of ATM reads ~3-7% the
week of earnings, depending on how far the event lifts ATM IV. NEUTRAL <= 5% keeps that ordinary tilt out of
the directional levels, and BEARISH (> 10%, the compound-risk leg) requires put
demand around twice the ordinary level. Recalibrate on logged skew_atm values
against outcomes before trusting the level boundaries.
"""

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

import numpy as np

from common.constants import RSLP30_THRESHOLDS  # noqa: E402
from src.domain.enums import DirectionalBias
from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.protocols import OptionsDataProvider
from src.domain.types import (
    MAX_PERCENTAGE,
    MIN_PERCENTAGE,
    Money,
    OptionChain,
    Percentage,
)

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

# A tie that floating point lands a hair short of .5 is still a tie.
_ROUNDING_TOLERANCE = 1e-9


def bias_to_numeric(bias: DirectionalBias) -> int:
    return _BIAS_NUMERIC[bias]


def numeric_to_bias(n: int) -> DirectionalBias:
    clamped = max(-3, min(3, n))
    return _NUMERIC_BIAS[clamped]


def round_half_away_from_zero(x: float) -> int:
    """
    Round to the nearest integer; an exact half goes away from zero.

    0.5 → 1, 1.5 → 2, 2.5 → 3, and the negatives mirror them. Python's round()
    is banker's rounding (0.5 → 0, 2.5 → 2), so a tie's outcome depended on
    whether the neighbouring level was even — not on the signal. This rule is
    symmetric under a sign flip (a bearish reading and its mirror-image bullish
    reading land on the same strength) and monotone in magnitude.

    On the one tie the compound-risk leg can see, ±1.5, it agrees with banker's
    rounding (→ ±2): the change is confined to the ±0.5 and ±2.5 ties.
    """
    magnitude = math.floor(abs(x) + 0.5 + _ROUNDING_TOLERANCE)
    return int(math.copysign(magnitude, x)) if magnitude else 0


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
    Combine the Tradier skew bias with an INDEPENDENT second skew source.

    Single source of truth for the formula — used by both
    analyzer._fuse_skew_signals (live /analyze and sizing) and
    store_bias_prediction (persisted predictions) so the two cannot drift.

    The only independent source is ORATS r_slp_30, read from ORATS' own smoothed
    surface. With it, the two readings are confidence-weighted,

        fused = (tradier × tradier_conf + orats × ORATS_SKEW_CONFIDENCE)
                / (tradier_conf + ORATS_SKEW_CONFIDENCE)

    rounded half away from zero (round_half_away_from_zero) and clamped to ±3.

    Without it there is nothing to fuse and the Tradier bias is returned as is.
    Until Sep 2026 this branch fused the bias with
    compute_tradier_r_slp30_proxy(slope_atm): a linear map of the slope of the
    same fit that produced tradier_bias. That is one measurement counted twice.
    It could not add information, only move the reading — it turned a NEUTRAL
    into WEAK_BEARISH, and a WEAK_BEARISH at the noise gate into BEARISH, arming
    the compound-risk leg on a single source. The proxy was deleted with it; its
    calibration (Δr_slp_30 per slope unit) belonged to the old same-strike slope
    and means nothing for the risk-reversal fit.

    slope_atm is kept so neither caller changes: it now only says whether a
    Tradier fit exists.

    Returns None when there is neither an ORATS reading nor a Tradier fit.
    """
    if r_slp_30 is not None:
        from common.constants import ORATS_SKEW_CONFIDENCE  # noqa: PLC0415
        orats_numeric = r_slp_30_to_numeric(r_slp_30)
        tradier_numeric = bias_to_numeric(tradier_bias)
        fused = (tradier_numeric * tradier_conf + orats_numeric * ORATS_SKEW_CONFIDENCE) \
            / (tradier_conf + ORATS_SKEW_CONFIDENCE)
        return numeric_to_bias(round_half_away_from_zero(fused))
    if slope_atm is None:
        return None
    return tradier_bias


@dataclass
class SkewAnalysis:
    """
    Skew classification for one ticker and expiration.

    Field names predate the risk-reversal measure and are kept for callers.
    Every shape measure is scale-free (normalised by the fitted ATM IV).

    Attributes:
        ticker: Stock symbol
        expiration: Option expiration date
        stock_price: Current stock price
        skew_atm: 25Δ risk reversal as a percent of ATM IV,
            100 × (IV_25Δput − IV_25Δcall) / IV_ATM. Positive = puts richer.
        curvature: 25Δ butterfly as a fraction of ATM IV,
            ((IV_25Δput + IV_25Δcall) / 2 − IV_ATM) / IV_ATM.
        strength: "smile" (curvature > SMILE_THRESHOLD), "inverse_smile"
            (< −SMILE_THRESHOLD), otherwise "smirk"
        directional_bias: 7-level bias from skew_atm; NEUTRAL when the fit
            fails the noise gate
        confidence: Fit quality — adjusted R², clipped to [0, 1]
        num_points: OTM quotes in the fit
        slope_atm: Fitted dIV/dv at ATM over ATM IV (v in delta units, puts
            negative). Negative = put skew. Equals −skew_atm / 50.
        bias_confidence: Weight of this reading in fusion; equals confidence
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
    Classify an expiration's skew from its 25-delta risk reversal.

    See the module docstring for the measure, the fit, and why each
    requirement exists.
    """

    # --- What the chain must contain --------------------------------------
    # A degree-2 fit has 3 parameters. Through 3 points it is exact (R² = 1
    # whatever the quotes are); 5 leave 2 residual degrees of freedom, so fit
    # quality rests on more than a single leftover point.
    MIN_POINTS = 5
    MIN_POINTS_PER_WING = 2
    # 10Δ is the conventional far pillar of a vol surface. Further out, quotes sit
    # near the minimum tick and their IVs scatter; a least-squares fit is pulled
    # by them — on a test chain whose local 25Δ vols were 52.8 / 53.5, fitting
    # from 5Δ read RR +29.5% (STRONG_BEARISH), from 10Δ −4.5% (NEUTRAL).
    MIN_ABS_DELTA = 0.10
    MAX_ABS_DELTA = 0.50  # OTM side of 50Δ only
    RR_DELTA = 0.25       # the risk reversal is read at 25Δ

    # --- Fit quality gate --------------------------------------------------
    # Adjusted R² below this → NEUTRAL, whatever the tilt.
    MIN_FIT_QUALITY = 0.30

    # --- Shape ------------------------------------------------------------
    # |25Δ butterfly| / ATM IV above this is a smile (or inverse smile).
    SMILE_THRESHOLD = 0.02

    # --- Directional levels, on RR25 / ATM IV (provisional; module doc) ---
    THRESHOLD_NEUTRAL = 0.05   # |RR| <= 5%            → NEUTRAL
    THRESHOLD_WEAK = 0.10      # 5% < |RR| <= 10%      → WEAK_*
    THRESHOLD_STRONG = 0.20    # 10% < |RR| <= 20%     → BEARISH / BULLISH; above → STRONG_*

    def __init__(self, provider: OptionsDataProvider):
        self.provider = provider

    def analyze_skew_curve(
        self,
        ticker: str,
        expiration: date
    ) -> Result[SkewAnalysis, AppError]:
        """
        Classify skew for one expiration.

        Returns:
            Ok(SkewAnalysis), Err(NODATA) when the chain cannot support a
            25Δ reading, Err(CALCULATION) when the fit itself fails.
        """
        logger.info(f"Analyzing skew curve: {ticker} exp {expiration}")

        chain_result = self.provider.get_option_chain(ticker, expiration)
        if chain_result.is_err:
            return Err(chain_result.error)

        chain = chain_result.value
        stock_price = float(chain.stock_price.amount)

        points = self._collect_skew_points(chain, stock_price)

        problem = self._coverage_problem(points)
        if problem is not None:
            return Err(
                AppError(
                    ErrorCode.NODATA,
                    f"Insufficient data points for skew fit: {problem}"
                )
            )

        try:
            analysis = self._fit_and_analyze(
                ticker,
                expiration,
                chain.stock_price,
                points
            )
        except Exception as e:
            logger.error(f"Skew fit failed: {e}")
            return Err(
                AppError(
                    ErrorCode.CALCULATION,
                    f"Polynomial fit failed: {str(e)}"
                )
            )

        logger.info(
            f"{ticker}: RR25={analysis.skew_atm.value:.2f}% of ATM IV, "
            f"Strength={analysis.strength}, "
            f"Bias={analysis.directional_bias}, "
            f"Fit={analysis.confidence:.2f}, "
            f"Points={analysis.num_points}"
        )
        return Ok(analysis)

    def _collect_skew_points(
        self,
        chain: OptionChain,
        stock_price: float
    ) -> List[Tuple[float, float]]:
        """
        Collect (v, IV%) for every usable OTM quote.

        v is the signed distance from 50Δ in delta units: |Δ| − 0.5 for a put
        (negative), 0.5 − Δ for a call (positive). Each wing contributes its own
        OTM options; the put and the call at one strike are never differenced.
        """
        points = []
        ticker = chain.ticker

        for strike in chain.strikes:
            strike_price = float(strike.price)
            if strike_price < stock_price:
                quote, is_call = chain.puts.get(strike), False
            elif strike_price > stock_price:
                quote, is_call = chain.calls.get(strike), True
            else:
                continue  # exactly at spot: neither wing

            if quote is None or not quote.implied_volatility or quote.delta is None:
                continue
            if not quote.is_liquid:
                continue

            delta = quote.delta
            if (delta > 0) != is_call:
                logger.debug(
                    f"{ticker}: {'call' if is_call else 'put'} {strike_price:.2f} "
                    f"has delta {delta:+.3f} of the wrong sign - skipping"
                )
                continue

            abs_delta = abs(delta)
            if not (self.MIN_ABS_DELTA <= abs_delta < self.MAX_ABS_DELTA):
                continue

            v = (0.5 - abs_delta) if is_call else (abs_delta - 0.5)
            points.append((v, quote.implied_volatility.value))

        return points

    def _coverage_problem(self, points: List[Tuple[float, float]]) -> Optional[str]:
        """Why these points cannot support a 25Δ reading, or None if they can."""
        if len(points) < self.MIN_POINTS:
            return f"{len(points)} OTM points < {self.MIN_POINTS}"

        rr_v = 0.5 - self.RR_DELTA
        puts = [v for v, _ in points if v < 0]
        calls = [v for v, _ in points if v > 0]
        if len(puts) < self.MIN_POINTS_PER_WING or len(calls) < self.MIN_POINTS_PER_WING:
            return (
                f"{len(puts)} OTM puts and {len(calls)} OTM calls "
                f"(each wing needs {self.MIN_POINTS_PER_WING})"
            )
        if min(puts) > -rr_v:
            return f"no OTM put at or beyond {self.RR_DELTA * 100:.0f}Δ (the 25Δ put would be extrapolated)"
        if max(calls) < rr_v:
            return f"no OTM call at or beyond {self.RR_DELTA * 100:.0f}Δ (the 25Δ call would be extrapolated)"
        return None

    def _fit_and_analyze(
        self,
        ticker: str,
        expiration: date,
        stock_price: Money,
        points: List[Tuple[float, float]]
    ) -> SkewAnalysis:
        """
        Fit IV(v) = a·v² + b·v + c and read the risk reversal and butterfly.

        IV_25Δput = IV(−0.25), IV_25Δcall = IV(+0.25), IV_ATM = c, so
        RR25 / ATM = −b / (2c) and BF25 / ATM = a / (16c).
        """
        vs, ivs = zip(*points, strict=True)
        x = np.array(vs, dtype=float)
        y = np.array(ivs, dtype=float)
        n = len(points)

        coeffs = np.polyfit(x, y, deg=2)
        fitted = np.polyval(coeffs, x)
        ss_res = float(np.sum((y - fitted) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        # Adjusted for the 2 fitted terms beyond the intercept.
        adjusted = 1.0 - (1.0 - r_squared) * (n - 1) / (n - 3)
        confidence = min(1.0, max(0.0, adjusted))

        rr_v = 0.5 - self.RR_DELTA
        iv_put = float(np.polyval(coeffs, -rr_v))
        iv_call = float(np.polyval(coeffs, rr_v))
        iv_atm = float(np.polyval(coeffs, 0.0))
        if min(iv_put, iv_call, iv_atm) <= 0:
            raise ValueError(
                f"fitted smile is non-positive (25Δ put {iv_put:.2f}, "
                f"ATM {iv_atm:.2f}, 25Δ call {iv_call:.2f})"
            )

        risk_reversal = (iv_put - iv_call) / iv_atm
        butterfly = ((iv_put + iv_call) / 2.0 - iv_atm) / iv_atm
        slope_atm = float(coeffs[1]) / iv_atm

        if butterfly > self.SMILE_THRESHOLD:
            strength = "smile"
        elif butterfly < -self.SMILE_THRESHOLD:
            strength = "inverse_smile"
        else:
            strength = "smirk"

        directional_bias = self._classify(risk_reversal)
        if confidence < self.MIN_FIT_QUALITY and directional_bias != DirectionalBias.NEUTRAL:
            logger.debug(
                f"{ticker}: fit quality {confidence:.3f} (adjusted R², n={n}) "
                f"< {self.MIN_FIT_QUALITY}, forcing NEUTRAL "
                f"(was {directional_bias.value})"
            )
            directional_bias = DirectionalBias.NEUTRAL

        rr_pct = 100.0 * risk_reversal
        if not (MIN_PERCENTAGE <= rr_pct <= MAX_PERCENTAGE):
            logger.warning(
                f"{ticker}: RR25 {rr_pct:.1f}% of ATM IV is outside the storable "
                f"range; skew_atm is clamped, the bias is not"
            )
            rr_pct = max(MIN_PERCENTAGE, min(MAX_PERCENTAGE, rr_pct))

        return SkewAnalysis(
            ticker=ticker,
            expiration=expiration,
            stock_price=stock_price,
            skew_atm=Percentage(rr_pct),
            curvature=butterfly,
            strength=strength,
            directional_bias=directional_bias,
            confidence=confidence,
            num_points=n,
            slope_atm=slope_atm,
            bias_confidence=confidence,
        )

    def _classify(self, risk_reversal: float) -> DirectionalBias:
        """7-level bias from RR25 / ATM IV. Positive (puts richer) is bearish."""
        magnitude = abs(risk_reversal)
        if magnitude <= self.THRESHOLD_NEUTRAL:
            return DirectionalBias.NEUTRAL
        if risk_reversal > 0:
            if magnitude > self.THRESHOLD_STRONG:
                return DirectionalBias.STRONG_BEARISH
            if magnitude > self.THRESHOLD_WEAK:
                return DirectionalBias.BEARISH
            return DirectionalBias.WEAK_BEARISH
        if magnitude > self.THRESHOLD_STRONG:
            return DirectionalBias.STRONG_BULLISH
        if magnitude > self.THRESHOLD_WEAK:
            return DirectionalBias.BULLISH
        return DirectionalBias.WEAK_BULLISH
