"""
Term Structure Skew Analyzer - Multi-Expiration Volatility Analysis.

Analyzes volatility term structure across multiple expirations to detect:
- Term structure slope (contango vs backwardation)
- Near-term vs far-term skew comparison
- Earnings event pricing efficiency

Key insight: Comparing near-term (weekly) skew to far-term (monthly) skew
provides additional directional conviction signals:
- Steeper near-term skew = stronger directional conviction
- Flat term structure = uncertainty about timing

Usage:
    analyzer = TermStructureAnalyzer(options_provider)
    result = analyzer.analyze_term_structure(
        ticker="AAPL",
        near_expiration=date(2025, 1, 31),  # Weekly
        far_expiration=date(2025, 2, 21),   # Monthly
    )
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, List, Tuple
import numpy as np

from src.domain.protocols import OptionsDataProvider
from src.domain.types import OptionChain, Strike
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

logger = logging.getLogger(__name__)


@dataclass
class TermStructurePoint:
    """IV and skew data for a single expiration."""
    expiration: date
    dte: int  # Days to expiration
    atm_iv: float
    atm_skew: float  # Put IV - Call IV at ATM
    skew_slope: float  # Slope of skew curve
    put_25d_iv: float  # 25-delta put IV
    call_25d_iv: float  # 25-delta call IV
    risk_reversal: float  # Put 25d IV - Call 25d IV


@dataclass
class TermStructureAnalysis:
    """
    Complete term structure analysis.

    Attributes:
        ticker: Stock symbol
        near_point: Near-term expiration data
        far_point: Far-term expiration data
        iv_term_slope: IV term structure slope (positive = contango)
        skew_term_slope: How skew changes across term (positive = steeper near-term)
        term_structure_pattern: Classification (contango/flat/backwardation)
        skew_conviction: Near vs far skew ratio (>1 = stronger near-term signal)
        event_premium: Extra IV in near-term vs far-term
        event_premium_pct: Event premium as percentage
        directional_alignment: Near and far skew agree on direction?
        confidence: Analysis confidence (0-1)
        recommendation: Trading recommendation based on term structure
    """
    ticker: str
    near_point: TermStructurePoint
    far_point: TermStructurePoint
    iv_term_slope: float
    skew_term_slope: float
    term_structure_pattern: str
    skew_conviction: float
    event_premium: float
    event_premium_pct: float
    directional_alignment: bool
    confidence: float
    recommendation: str


class TermStructureAnalyzer:
    """
    Analyzes volatility term structure and skew across expirations.

    Compares near-term (event) expiration to far-term (post-event) expiration
    to understand how the market is pricing the earnings event specifically.
    """

    # Delta thresholds for OTM option selection
    TARGET_DELTA = 0.25  # 25-delta for risk reversal
    DELTA_TOLERANCE = 0.10  # Accept 15-35 delta range

    def __init__(self, provider: OptionsDataProvider):
        """
        Initialize analyzer with options data provider.

        Args:
            provider: Tradier or other options data API
        """
        self.provider = provider

    def analyze_term_structure(
        self,
        ticker: str,
        near_expiration: date,
        far_expiration: date,
        stock_price: Optional[float] = None
    ) -> Result[TermStructureAnalysis, AppError]:
        """
        Analyze term structure between two expirations.

        Args:
            ticker: Stock symbol
            near_expiration: Near-term expiration (typically earnings week)
            far_expiration: Far-term expiration (post-earnings)
            stock_price: Current stock price (fetched if not provided)

        Returns:
            Result with TermStructureAnalysis or AppError
        """
        logger.info(
            f"Analyzing term structure for {ticker}: "
            f"{near_expiration} vs {far_expiration}"
        )

        # Get both option chains
        near_chain_result = self.provider.get_option_chain(ticker, near_expiration)
        if near_chain_result.is_err:
            return Err(near_chain_result.error)

        far_chain_result = self.provider.get_option_chain(ticker, far_expiration)
        if far_chain_result.is_err:
            return Err(far_chain_result.error)

        near_chain = near_chain_result.value
        far_chain = far_chain_result.value

        if stock_price is None:
            stock_price = float(near_chain.stock_price.amount)

        # Extract term structure points
        near_point = self._extract_term_point(near_chain, stock_price, near_expiration)
        far_point = self._extract_term_point(far_chain, stock_price, far_expiration)

        if near_point is None or far_point is None:
            return Err(AppError(
                ErrorCode.NODATA,
                f"Insufficient option data for term structure analysis"
            ))

        # Calculate term structure metrics
        analysis = self._calculate_term_analysis(ticker, near_point, far_point)

        logger.info(
            f"{ticker} term structure: pattern={analysis.term_structure_pattern}, "
            f"event_premium={analysis.event_premium_pct:.1f}%, "
            f"skew_conviction={analysis.skew_conviction:.2f}"
        )

        return Ok(analysis)

    def _extract_term_point(
        self,
        chain: OptionChain,
        stock_price: float,
        expiration: date
    ) -> Optional[TermStructurePoint]:
        """Extract term structure data point from option chain."""
        dte = (expiration - date.today()).days
        if dte < 1:
            dte = 1

        # Get ATM strike
        try:
            atm_strike = chain.atm_strike()
        except (ValueError, IndexError):
            return None
        if atm_strike is None:
            return None

        atm_call = chain.calls.get(atm_strike)
        atm_put = chain.puts.get(atm_strike)

        if not atm_call or not atm_put:
            return None

        if not atm_call.implied_volatility or not atm_put.implied_volatility:
            return None

        atm_call_iv = atm_call.implied_volatility.value
        atm_put_iv = atm_put.implied_volatility.value
        atm_iv = (atm_call_iv + atm_put_iv) / 2
        atm_skew = atm_put_iv - atm_call_iv

        # Find 25-delta options for risk reversal
        put_25d = self._find_delta_option(chain, stock_price, is_put=True)
        call_25d = self._find_delta_option(chain, stock_price, is_put=False)

        put_25d_iv = put_25d.implied_volatility.value if put_25d and put_25d.implied_volatility else atm_put_iv
        call_25d_iv = call_25d.implied_volatility.value if call_25d and call_25d.implied_volatility else atm_call_iv

        risk_reversal = put_25d_iv - call_25d_iv

        # Calculate skew slope
        skew_slope = self._calculate_skew_slope(chain, stock_price)

        return TermStructurePoint(
            expiration=expiration,
            dte=dte,
            atm_iv=atm_iv,
            atm_skew=atm_skew,
            skew_slope=skew_slope,
            put_25d_iv=put_25d_iv,
            call_25d_iv=call_25d_iv,
            risk_reversal=risk_reversal,
        )

    def _find_delta_option(
        self,
        chain: OptionChain,
        stock_price: float,
        is_put: bool
    ) -> Optional[any]:
        """
        Find approximately 25-delta option.

        Uses strike distance from ATM as proxy for delta.
        25-delta is approximately 5-10% OTM depending on IV and DTE.
        """
        try:
            atm_strike = chain.atm_strike()
        except (ValueError, IndexError):
            return None
        if atm_strike is None:
            return None

        atm_price = float(atm_strike.price)
        options = chain.puts if is_put else chain.calls

        # Target 7% OTM for 25-delta approximation
        target_otm = 0.07

        if is_put:
            target_strike = stock_price * (1 - target_otm)
        else:
            target_strike = stock_price * (1 + target_otm)

        # Find closest strike to target
        best_option = None
        best_distance = float('inf')

        for strike, option in options.items():
            strike_price = float(strike.price)
            distance = abs(strike_price - target_strike)

            # Must have IV and be liquid
            if not option.implied_volatility:
                continue
            if not option.is_liquid:
                continue

            if distance < best_distance:
                best_distance = distance
                best_option = option

        return best_option

    def _calculate_skew_slope(
        self,
        chain: OptionChain,
        stock_price: float
    ) -> float:
        """
        Calculate skew slope across strikes.

        Similar to polynomial fit but simpler - just linear slope.
        """
        points = []

        for strike in chain.strikes:
            strike_price = float(strike.price)
            moneyness = (strike_price - stock_price) / stock_price

            # Only use strikes within ±15% of ATM
            if abs(moneyness) > 0.15:
                continue
            # Skip very ATM strikes
            if abs(moneyness) < 0.02:
                continue

            call = chain.calls.get(strike)
            put = chain.puts.get(strike)

            if not call or not put:
                continue
            if not call.implied_volatility or not put.implied_volatility:
                continue
            if not call.is_liquid or not put.is_liquid:
                continue

            skew = put.implied_volatility.value - call.implied_volatility.value
            points.append((moneyness, skew))

        if len(points) < 3:
            return 0.0

        # Linear regression for slope
        x = np.array([p[0] for p in points])
        y = np.array([p[1] for p in points])

        try:
            slope, _ = np.polyfit(x, y, 1)
            return float(slope)
        except (ValueError, np.linalg.LinAlgError) as e:
            # Expected errors from polyfit with invalid data
            logger.debug(f"Skew slope calculation failed: {e}")
            return 0.0
        except Exception as e:
            # Unexpected errors should be logged
            logger.warning(f"Unexpected error in skew slope calculation: {type(e).__name__}: {e}")
            return 0.0

    def _calculate_term_analysis(
        self,
        ticker: str,
        near: TermStructurePoint,
        far: TermStructurePoint
    ) -> TermStructureAnalysis:
        """Calculate complete term structure analysis."""

        # IV term structure slope (annualized)
        dte_diff = far.dte - near.dte
        if dte_diff > 0:
            iv_term_slope = (far.atm_iv - near.atm_iv) / dte_diff * 30  # Per month
        else:
            iv_term_slope = 0.0

        # Skew term slope
        skew_term_slope = near.skew_slope - far.skew_slope

        # Term structure pattern
        if near.atm_iv > far.atm_iv + 5:
            pattern = "backwardation"  # Near-term IV higher (event premium)
        elif far.atm_iv > near.atm_iv + 3:
            pattern = "contango"  # Far-term IV higher (unusual)
        else:
            pattern = "flat"

        # Skew conviction ratio
        # Higher near-term skew slope relative to far-term = stronger conviction
        if abs(far.skew_slope) > 0.1:
            skew_conviction = abs(near.skew_slope) / abs(far.skew_slope)
        else:
            skew_conviction = abs(near.skew_slope) / 0.1 if abs(near.skew_slope) > 0 else 1.0

        # Event premium
        event_premium = near.atm_iv - far.atm_iv
        event_premium_pct = (event_premium / far.atm_iv * 100) if far.atm_iv > 0 else 0

        # Directional alignment
        near_direction = "bearish" if near.risk_reversal > 2 else ("bullish" if near.risk_reversal < -2 else "neutral")
        far_direction = "bearish" if far.risk_reversal > 2 else ("bullish" if far.risk_reversal < -2 else "neutral")
        directional_alignment = (near_direction == far_direction) or (near_direction == "neutral" or far_direction == "neutral")

        # Confidence based on data quality
        confidence = self._calculate_confidence(near, far)

        # Generate recommendation
        recommendation = self._generate_recommendation(
            pattern, skew_conviction, event_premium_pct, directional_alignment
        )

        return TermStructureAnalysis(
            ticker=ticker,
            near_point=near,
            far_point=far,
            iv_term_slope=iv_term_slope,
            skew_term_slope=skew_term_slope,
            term_structure_pattern=pattern,
            skew_conviction=skew_conviction,
            event_premium=event_premium,
            event_premium_pct=event_premium_pct,
            directional_alignment=directional_alignment,
            confidence=confidence,
            recommendation=recommendation,
        )

    def _calculate_confidence(
        self,
        near: TermStructurePoint,
        far: TermStructurePoint
    ) -> float:
        """Calculate analysis confidence."""
        confidence = 1.0

        # Penalize if ATM IV seems unreasonable
        if near.atm_iv < 10 or near.atm_iv > 200:
            confidence *= 0.5
        if far.atm_iv < 10 or far.atm_iv > 200:
            confidence *= 0.5

        # Penalize if skew is extreme
        if abs(near.risk_reversal) > 30:
            confidence *= 0.7
        if abs(far.risk_reversal) > 30:
            confidence *= 0.7

        # Penalize if DTEs are very different
        dte_ratio = near.dte / far.dte if far.dte > 0 else 0
        if dte_ratio > 0.8 or dte_ratio < 0.1:
            confidence *= 0.8

        return confidence

    def _generate_recommendation(
        self,
        pattern: str,
        skew_conviction: float,
        event_premium_pct: float,
        directional_alignment: bool
    ) -> str:
        """Generate trading recommendation from term structure."""
        parts = []

        # Pattern-based insight
        if pattern == "backwardation":
            if event_premium_pct > 20:
                parts.append("High event premium - good IV crush potential")
            else:
                parts.append("Normal event premium")
        elif pattern == "contango":
            parts.append("Unusual term structure - far-term IV elevated")
        else:
            parts.append("Flat term structure - event may be priced in")

        # Conviction-based insight
        if skew_conviction > 1.5:
            parts.append("Strong near-term directional conviction")
        elif skew_conviction > 1.0:
            parts.append("Moderate directional conviction")
        elif skew_conviction < 0.5:
            parts.append("Weak directional signal")

        # Alignment insight
        if not directional_alignment:
            parts.append("CAUTION: Near/far term skew disagree on direction")

        return "; ".join(parts)

    def find_optimal_expirations(
        self,
        ticker: str,
        earnings_date: date
    ) -> Tuple[Optional[date], Optional[date]]:
        """
        Find optimal near and far expirations for analysis.

        Near: First expiration after earnings
        Far: First monthly expiration after near

        Args:
            ticker: Stock symbol
            earnings_date: Earnings announcement date

        Returns:
            Tuple of (near_expiration, far_expiration) or (None, None) if not found
        """
        expirations_result = self.provider.get_expirations(ticker)
        if expirations_result.is_err:
            return None, None

        expirations = sorted(expirations_result.value)

        # Find near expiration (first one after earnings)
        near = None
        for exp in expirations:
            if exp >= earnings_date:
                near = exp
                break

        if near is None:
            return None, None

        # Find far expiration (at least 14 days after near)
        far = None
        target_far = near + timedelta(days=14)
        for exp in expirations:
            if exp >= target_far:
                far = exp
                break

        return near, far
