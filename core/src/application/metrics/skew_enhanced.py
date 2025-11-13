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

logger = logging.getLogger(__name__)


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
        directional_bias: Put bias vs call bias
        confidence: Fit quality (R-squared)
        num_points: Number of data points used in fit
    """
    ticker: str
    expiration: date
    stock_price: Money
    skew_atm: Percentage
    curvature: float
    strength: str
    directional_bias: str
    confidence: float
    num_points: int


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
    MIN_POINTS = 5  # Minimum points for reliable fit
    MAX_DISTANCE_PCT = 0.15  # Sample strikes within ±15% of stock price
    MIN_DISTANCE_PCT = 0.02  # Skip strikes within ±2% (ATM)

    # Curvature threshold for smile classification (in IV%/moneyness² units)
    # Positive curvature > 1.0 indicates volatility smile (U-shaped, both OTM expensive)
    # Empirically derived from typical equity skew patterns
    SMILE_THRESHOLD = 1.0

    # Directional bias threshold (in IV%/moneyness units)
    # Slope > 0.5 indicates put bias (puts relatively more expensive)
    # Slope < -0.5 indicates call bias (calls relatively more expensive)
    # Based on typical single-stock skew slopes of 5-15% IV across 10% moneyness
    DIRECTIONAL_BIAS_THRESHOLD = 0.5

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

        # Determine directional bias
        if slope_atm > self.DIRECTIONAL_BIAS_THRESHOLD:
            directional_bias = "put_bias"  # Puts more expensive as we go OTM
        elif slope_atm < -self.DIRECTIONAL_BIAS_THRESHOLD:
            directional_bias = "call_bias"  # Calls more expensive as we go OTM
        else:
            directional_bias = "neutral"

        return SkewAnalysis(
            ticker=ticker,
            expiration=expiration,
            stock_price=stock_price,
            skew_atm=skew_atm,
            curvature=curvature,
            strength=strength,
            directional_bias=directional_bias,
            confidence=r_squared,
            num_points=len(points)
        )
