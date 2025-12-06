"""
Put-Call Parity Deviation Analyzer - Directional Signal from Options Mispricing.

Put-Call Parity states: C - P = S - K*e^(-rT)

Deviations from parity can indicate:
- Directional positioning by institutional traders
- Hard-to-borrow situations (high short interest)
- Dividend expectations
- Early exercise premium for American options

Key insight for earnings trades:
- Large P > C deviation → Institutional bearish positioning
- Large C > P deviation → Institutional bullish positioning
- This can inform directional bias for iron condor positioning

Usage:
    analyzer = PCPDeviationAnalyzer(tradier_client)

    # Analyze put-call parity deviations
    result = analyzer.analyze_parity("AAPL", expiration=date(2025, 1, 31))
    # result.atm_deviation = 0.15 (calls 15 cents expensive vs parity)
    # result.deviation_pattern = "bullish" (calls consistently expensive)
    # result.institutional_signal = "mild_bullish"
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, List, Dict, Tuple
import numpy as np

from src.domain.protocols import OptionsDataProvider
from src.domain.types import OptionChain, Strike
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

logger = logging.getLogger(__name__)


@dataclass
class ParityPoint:
    """
    Put-call parity analysis for a single strike.

    Attributes:
        strike: Strike price
        moneyness: Strike relative to stock price (K/S - 1)
        call_mid: Call mid price
        put_mid: Put mid price
        theoretical_diff: Theoretical C-P from parity
        actual_diff: Actual C-P from market
        deviation: Actual - Theoretical (positive = calls expensive)
        deviation_pct: Deviation as % of stock price
    """
    strike: float
    moneyness: float
    call_mid: float
    put_mid: float
    theoretical_diff: float
    actual_diff: float
    deviation: float
    deviation_pct: float


@dataclass
class PCPAnalysis:
    """
    Complete put-call parity deviation analysis.

    Attributes:
        ticker: Stock symbol
        expiration: Options expiration date
        stock_price: Current stock price
        dte: Days to expiration
        risk_free_rate: Risk-free rate used
        parity_points: Analysis at each strike
        atm_deviation: Deviation at ATM strike
        atm_deviation_pct: ATM deviation as % of stock price
        weighted_deviation: IV-weighted average deviation
        deviation_slope: How deviation changes with strike
        deviation_pattern: Classification (bullish/bearish/neutral/complex)
        institutional_signal: Inferred institutional positioning
        confidence: Confidence in analysis (0-1)
        anomalies: List of strikes with unusual deviations
    """
    ticker: str
    expiration: date
    stock_price: float
    dte: int
    risk_free_rate: float
    parity_points: List[ParityPoint]
    atm_deviation: float
    atm_deviation_pct: float
    weighted_deviation: float
    deviation_slope: float
    deviation_pattern: str
    institutional_signal: str
    confidence: float
    anomalies: List[str]


class PCPDeviationAnalyzer:
    """
    Analyzes put-call parity deviations for directional signals.

    Put-Call Parity (for European options):
        C - P = S - K * e^(-rT)

    For American options, dividends and early exercise add complexity,
    but systematic deviations still provide signal.

    Approach:
    1. For each strike, calculate theoretical C-P from parity
    2. Compare to actual market C-P
    3. Positive deviation = calls expensive relative to puts
    4. Negative deviation = puts expensive relative to calls
    5. Pattern across strikes indicates directional positioning
    """

    # Default risk-free rate (approximate)
    DEFAULT_RISK_FREE = 0.05  # 5%

    # Deviation thresholds for signal classification
    SIGNAL_THRESHOLD = 0.10  # 10 cents deviation
    STRONG_THRESHOLD = 0.25  # 25 cents deviation

    def __init__(
        self,
        provider: OptionsDataProvider,
        risk_free_rate: Optional[float] = None
    ):
        """
        Initialize PCP deviation analyzer.

        Args:
            provider: Tradier or other options data API
            risk_free_rate: Risk-free rate (default: 5%)
        """
        self.provider = provider
        self.risk_free_rate = risk_free_rate or self.DEFAULT_RISK_FREE

    def analyze_parity(
        self,
        ticker: str,
        expiration: date,
        stock_price: Optional[float] = None
    ) -> Result[PCPAnalysis, AppError]:
        """
        Analyze put-call parity deviations for an expiration.

        Args:
            ticker: Stock symbol
            expiration: Options expiration date
            stock_price: Current stock price (fetched if not provided)

        Returns:
            Result with PCPAnalysis or AppError
        """
        logger.info(f"Analyzing put-call parity for {ticker} exp {expiration}")

        # Get option chain
        chain_result = self.provider.get_option_chain(ticker, expiration)
        if chain_result.is_err:
            return Err(chain_result.error)

        chain = chain_result.value

        if stock_price is None:
            stock_price = float(chain.stock_price.amount)

        # Calculate days to expiration
        dte = (expiration - date.today()).days
        if dte < 1:
            dte = 1

        # Analyze parity at each strike
        parity_points = self._analyze_strikes(chain, stock_price, dte)

        if not parity_points:
            return Err(AppError(
                ErrorCode.NODATA,
                f"No valid parity points for {ticker}"
            ))

        # Extract key metrics
        try:
            atm_strike = chain.atm_strike()
        except (ValueError, IndexError):
            atm_strike = None
        atm_point = self._find_atm_point(parity_points, stock_price)

        atm_deviation = atm_point.deviation if atm_point else 0.0
        atm_deviation_pct = atm_deviation / stock_price * 100 if stock_price > 0 else 0.0

        # Calculate weighted deviation
        weighted_dev = self._calculate_weighted_deviation(parity_points)

        # Calculate deviation slope
        slope = self._calculate_deviation_slope(parity_points)

        # Classify pattern
        pattern = self._classify_pattern(parity_points, weighted_dev, slope)

        # Infer institutional signal
        signal = self._infer_institutional_signal(pattern, weighted_dev, atm_deviation)

        # Calculate confidence
        confidence = self._calculate_confidence(parity_points, chain)

        # Find anomalies
        anomalies = self._find_anomalies(parity_points)

        analysis = PCPAnalysis(
            ticker=ticker,
            expiration=expiration,
            stock_price=stock_price,
            dte=dte,
            risk_free_rate=self.risk_free_rate,
            parity_points=parity_points,
            atm_deviation=atm_deviation,
            atm_deviation_pct=atm_deviation_pct,
            weighted_deviation=weighted_dev,
            deviation_slope=slope,
            deviation_pattern=pattern,
            institutional_signal=signal,
            confidence=confidence,
            anomalies=anomalies,
        )

        logger.info(
            f"{ticker}: PCP pattern={pattern}, signal={signal}, "
            f"atm_dev={atm_deviation:.2f}, confidence={confidence:.2f}"
        )

        return Ok(analysis)

    def _analyze_strikes(
        self,
        chain: OptionChain,
        stock_price: float,
        dte: int
    ) -> List[ParityPoint]:
        """Analyze parity at each strike."""
        points = []

        # Discount factor
        T = dte / 365.0
        discount = np.exp(-self.risk_free_rate * T)

        for strike in chain.strikes:
            strike_price = float(strike.price)

            # Get call and put
            call = chain.calls.get(strike)
            put = chain.puts.get(strike)

            if not call or not put:
                continue

            # Need both bid and ask for mid price
            if not call.bid or not call.ask or not put.bid or not put.ask:
                continue

            call_mid = (float(call.bid.amount) + float(call.ask.amount)) / 2
            put_mid = (float(put.bid.amount) + float(put.ask.amount)) / 2

            # Skip if no meaningful prices
            if call_mid <= 0 and put_mid <= 0:
                continue

            # Theoretical difference from put-call parity
            # C - P = S - K * e^(-rT)
            theoretical_diff = stock_price - strike_price * discount

            # Actual difference
            actual_diff = call_mid - put_mid

            # Deviation (positive = calls expensive)
            deviation = actual_diff - theoretical_diff

            # Moneyness
            moneyness = (strike_price / stock_price) - 1

            points.append(ParityPoint(
                strike=strike_price,
                moneyness=moneyness,
                call_mid=call_mid,
                put_mid=put_mid,
                theoretical_diff=theoretical_diff,
                actual_diff=actual_diff,
                deviation=deviation,
                deviation_pct=deviation / stock_price * 100,
            ))

        return points

    def _find_atm_point(
        self,
        points: List[ParityPoint],
        stock_price: float
    ) -> Optional[ParityPoint]:
        """Find the ATM parity point."""
        if not points:
            return None

        return min(points, key=lambda p: abs(p.strike - stock_price))

    def _calculate_weighted_deviation(
        self,
        points: List[ParityPoint]
    ) -> float:
        """
        Calculate deviation weighted by proximity to ATM.

        Strikes closer to ATM get more weight.
        """
        if not points:
            return 0.0

        # Weight by inverse of absolute moneyness
        weights = []
        deviations = []

        for p in points:
            # Weight = 1 / (1 + |moneyness|*10)
            weight = 1.0 / (1.0 + abs(p.moneyness) * 10)
            weights.append(weight)
            deviations.append(p.deviation)

        total_weight = sum(weights)
        if total_weight > 0:
            weighted_dev = sum(w * d for w, d in zip(weights, deviations)) / total_weight
        else:
            weighted_dev = np.mean(deviations)

        return float(weighted_dev)

    def _calculate_deviation_slope(
        self,
        points: List[ParityPoint]
    ) -> float:
        """
        Calculate how deviation changes with strike.

        Positive slope = higher strikes (OTM calls) relatively more expensive
        Negative slope = lower strikes (OTM puts) relatively more expensive
        """
        if len(points) < 3:
            return 0.0

        # Linear regression: deviation vs moneyness
        x = np.array([p.moneyness for p in points])
        y = np.array([p.deviation for p in points])

        try:
            slope, _ = np.polyfit(x, y, 1)
            return float(slope)
        except Exception:
            return 0.0

    def _classify_pattern(
        self,
        points: List[ParityPoint],
        weighted_dev: float,
        slope: float
    ) -> str:
        """
        Classify the deviation pattern.

        Returns:
            "bullish" - Calls consistently expensive
            "bearish" - Puts consistently expensive
            "neutral" - No clear pattern
            "call_skew" - OTM calls more expensive (upside demand)
            "put_skew" - OTM puts more expensive (downside protection)
            "complex" - Mixed or unusual pattern
        """
        if not points:
            return "insufficient_data"

        deviations = [p.deviation for p in points]
        positive_count = sum(1 for d in deviations if d > self.SIGNAL_THRESHOLD)
        negative_count = sum(1 for d in deviations if d < -self.SIGNAL_THRESHOLD)
        total = len(deviations)

        # Strong directional pattern
        if positive_count > total * 0.7:
            if slope > 0.5:
                return "call_skew"  # OTM calls especially expensive
            return "bullish"

        if negative_count > total * 0.7:
            if slope < -0.5:
                return "put_skew"  # OTM puts especially expensive
            return "bearish"

        # Check for skew patterns
        if abs(slope) > 1.0:
            if slope > 0:
                return "call_skew"
            else:
                return "put_skew"

        # Check weighted deviation
        if abs(weighted_dev) > self.STRONG_THRESHOLD:
            if weighted_dev > 0:
                return "bullish"
            else:
                return "bearish"

        # Mixed pattern
        if positive_count > 0 and negative_count > 0:
            if positive_count > negative_count * 1.5:
                return "mild_bullish"
            elif negative_count > positive_count * 1.5:
                return "mild_bearish"
            return "complex"

        return "neutral"

    def _infer_institutional_signal(
        self,
        pattern: str,
        weighted_dev: float,
        atm_dev: float
    ) -> str:
        """
        Infer institutional positioning from deviation pattern.

        Institutions often use options for directional bets,
        and their activity shows up in parity deviations.
        """
        # Strong signals
        if pattern in ("bullish", "call_skew"):
            if abs(weighted_dev) > self.STRONG_THRESHOLD:
                return "strong_bullish"
            return "mild_bullish"

        if pattern in ("bearish", "put_skew"):
            if abs(weighted_dev) > self.STRONG_THRESHOLD:
                return "strong_bearish"
            return "mild_bearish"

        if pattern in ("mild_bullish",):
            return "mild_bullish"

        if pattern in ("mild_bearish",):
            return "mild_bearish"

        # ATM deviation can provide weak signal
        if atm_dev > self.SIGNAL_THRESHOLD:
            return "weak_bullish"
        if atm_dev < -self.SIGNAL_THRESHOLD:
            return "weak_bearish"

        return "neutral"

    def _calculate_confidence(
        self,
        points: List[ParityPoint],
        chain: OptionChain
    ) -> float:
        """Calculate confidence in the analysis."""
        if not points:
            return 0.0

        confidence = 1.0

        # Penalize for few data points
        n = len(points)
        if n < 5:
            confidence *= n / 5

        # Penalize for high variance in deviations
        deviations = [p.deviation for p in points]
        if len(deviations) >= 2:
            dev_std = np.std(deviations)
            dev_mean = abs(np.mean(deviations))
            if dev_mean > 0:
                cv = dev_std / dev_mean
                if cv > 2:
                    confidence *= 0.7
                elif cv > 1:
                    confidence *= 0.85

        # Penalize for wide bid-ask spreads (illiquid options)
        liquid_count = sum(1 for p in points
                         if (p.call_mid > 0.10 and p.put_mid > 0.10))
        if n > 0:
            liquidity_factor = liquid_count / n
            confidence *= (0.5 + 0.5 * liquidity_factor)

        return float(min(1.0, confidence))

    def _find_anomalies(self, points: List[ParityPoint]) -> List[str]:
        """Find strikes with unusual parity deviations."""
        anomalies = []

        if len(points) < 3:
            return anomalies

        deviations = [p.deviation for p in points]
        dev_mean = np.mean(deviations)
        dev_std = np.std(deviations)

        for p in points:
            if dev_std > 0:
                z_score = (p.deviation - dev_mean) / dev_std
                if abs(z_score) > 2.5:
                    anomalies.append(
                        f"Strike {p.strike}: deviation {p.deviation:.2f} "
                        f"(z={z_score:.1f})"
                    )

        return anomalies

    def get_directional_bias(
        self,
        ticker: str,
        expiration: date
    ) -> Tuple[str, float]:
        """
        Get simple directional bias from PCP analysis.

        Returns:
            Tuple of (direction, confidence)
            direction: "bullish", "bearish", or "neutral"
            confidence: 0-1 confidence in signal
        """
        result = self.analyze_parity(ticker, expiration)

        if result.is_err:
            return ("neutral", 0.0)

        analysis = result.value
        signal = analysis.institutional_signal

        # Map signal to simple direction
        if "bullish" in signal:
            direction = "bullish"
        elif "bearish" in signal:
            direction = "bearish"
        else:
            direction = "neutral"

        # Adjust confidence based on signal strength
        confidence = analysis.confidence
        if "strong" in signal:
            confidence *= 1.0
        elif "mild" in signal:
            confidence *= 0.7
        elif "weak" in signal:
            confidence *= 0.4

        return (direction, confidence)

    def compare_expirations(
        self,
        ticker: str,
        near_expiration: date,
        far_expiration: date
    ) -> Dict[str, any]:
        """
        Compare PCP deviations across two expirations.

        Useful for term structure analysis of directional positioning.
        """
        near_result = self.analyze_parity(ticker, near_expiration)
        far_result = self.analyze_parity(ticker, far_expiration)

        if near_result.is_err or far_result.is_err:
            return {
                'error': 'Failed to analyze one or both expirations',
                'consistent': False,
            }

        near = near_result.value
        far = far_result.value

        # Check if signals are consistent
        near_dir, _ = self.get_directional_bias(ticker, near_expiration)
        far_dir, _ = self.get_directional_bias(ticker, far_expiration)

        consistent = near_dir == far_dir

        return {
            'near_expiration': near_expiration,
            'far_expiration': far_expiration,
            'near_signal': near.institutional_signal,
            'far_signal': far.institutional_signal,
            'near_atm_deviation': near.atm_deviation,
            'far_atm_deviation': far.atm_deviation,
            'consistent': consistent,
            'term_structure_signal': (
                f"Near-term {near.deviation_pattern}, "
                f"far-term {far.deviation_pattern}"
            ),
        }
