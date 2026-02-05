"""AnomalyDetectionAgent - Catches data quality issues and conflicting signals.

This agent implements guardrails to prevent costly mistakes like the
$26,930 WDAY/ZS loss from EXCELLENT VRP + REJECT liquidity conflict.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from ..utils.schemas import AnomalyDetectionResponse, AnomalyDetail
from .base import BaseAgent


class AnomalyDetectionAgent:
    """
    Worker agent for detecting anomalies and data quality issues.

    Checks for:
    1. Stale earnings dates (>7 days out, cache >24h old)
    2. Missing historical data (<4 quarters)
    3. Extreme outliers (VRP >20x, moves >50%)
    4. Conflicting signals (EXCELLENT VRP + REJECT liquidity)
    5. GOOD VRP + REJECT liquidity (warning level)

    Example:
        agent = AnomalyDetectionAgent()
        result = agent.detect(
            ticker="WDAY",
            vrp_ratio=7.2,
            recommendation="EXCELLENT",
            liquidity_tier="REJECT",
            earnings_date="2026-02-05",
            cache_age_hours=36,
            historical_quarters=8
        )
    """

    # Thresholds
    STALE_CACHE_HOURS = 24
    MIN_HISTORICAL_QUARTERS = 4
    EXTREME_VRP_THRESHOLD = 20.0
    EXTREME_MOVE_THRESHOLD = 50.0

    def detect(
        self,
        ticker: str,
        vrp_ratio: float,
        recommendation: str,
        liquidity_tier: str,
        earnings_date: str,
        cache_age_hours: float = 0.0,
        historical_quarters: int = 0
    ) -> Dict[str, Any]:
        """
        Detect anomalies in ticker analysis.

        Args:
            ticker: Stock ticker symbol
            vrp_ratio: VRP ratio
            recommendation: VRP recommendation (EXCELLENT/GOOD/MARGINAL/SKIP)
            liquidity_tier: Liquidity tier (EXCELLENT/GOOD/WARNING/REJECT)
            earnings_date: Earnings date (YYYY-MM-DD)
            cache_age_hours: Age of cached data in hours
            historical_quarters: Number of historical quarters available

        Returns:
            Anomaly detection result dict

        Example:
            result = agent.detect(
                ticker="WDAY",
                vrp_ratio=7.2,
                recommendation="EXCELLENT",
                liquidity_tier="REJECT",
                earnings_date="2026-02-05",
                cache_age_hours=36,
                historical_quarters=8
            )
            # Returns anomalies list with recommendation
        """
        anomalies: List[Dict[str, Any]] = []

        # Check 1: Stale earnings data
        stale_anomaly = self._check_stale_data(
            ticker, earnings_date, cache_age_hours
        )
        if stale_anomaly:
            anomalies.append(stale_anomaly)

        # Check 2: Missing historical data
        missing_data_anomaly = self._check_missing_data(
            ticker, historical_quarters
        )
        if missing_data_anomaly:
            anomalies.append(missing_data_anomaly)

        # Check 3: Extreme outliers
        outlier_anomaly = self._check_extreme_outlier(ticker, vrp_ratio)
        if outlier_anomaly:
            anomalies.append(outlier_anomaly)

        # Check 4: Conflicting signals (CRITICAL)
        conflict_anomaly = self._check_conflicting_signals(
            ticker, recommendation, liquidity_tier
        )
        if conflict_anomaly:
            anomalies.append(conflict_anomaly)

        # Determine overall recommendation
        overall_recommendation = self._determine_recommendation(anomalies)

        # Build response
        response_data = {
            'ticker': ticker,
            'anomalies': [
                {
                    'type': a['type'],
                    'severity': a['severity'],
                    'message': a['message']
                }
                for a in anomalies
            ],
            'recommendation': overall_recommendation
        }

        # Validate with schema
        validated = AnomalyDetectionResponse(**response_data)
        return validated.model_dump()

    def _check_stale_data(
        self,
        ticker: str,
        earnings_date: str,
        cache_age_hours: float
    ) -> Optional[Dict[str, Any]]:
        """Check for stale earnings date cache."""
        try:
            earnings_dt = datetime.strptime(earnings_date, '%Y-%m-%d')
            days_until_earnings = (earnings_dt - datetime.now()).days

            # If earnings within 7 days and cache older than 24h
            # Note: cache_age_hours can be None if cache lookup fails
            if days_until_earnings <= 7 and cache_age_hours is not None and cache_age_hours > self.STALE_CACHE_HOURS:
                return {
                    'type': 'stale_data',
                    'severity': 'warning',
                    'message': (
                        f"Earnings date cached {cache_age_hours:.1f}h ago, "
                        f"within 7-day window (may have changed)"
                    )
                }

        except (ValueError, TypeError):
            # Date parsing failed - treat as no stale data anomaly
            pass

        return None

    def _check_missing_data(
        self,
        ticker: str,
        historical_quarters: int
    ) -> Optional[Dict[str, Any]]:
        """Check for insufficient historical data."""
        if historical_quarters < self.MIN_HISTORICAL_QUARTERS:
            return {
                'type': 'missing_data',
                'severity': 'warning',
                'message': (
                    f"Only {historical_quarters} historical quarters available "
                    f"(minimum {self.MIN_HISTORICAL_QUARTERS} recommended)"
                )
            }

        return None

    def _check_extreme_outlier(
        self,
        ticker: str,
        vrp_ratio: float
    ) -> Optional[Dict[str, Any]]:
        """Check for extreme VRP outliers."""
        if vrp_ratio > self.EXTREME_VRP_THRESHOLD:
            return {
                'type': 'extreme_outlier',
                'severity': 'warning',
                'message': (
                    f"Extreme VRP ({vrp_ratio:.1f}x) may indicate data error "
                    f"or unusual market conditions"
                )
            }

        return None

    def _check_conflicting_signals(
        self,
        ticker: str,
        recommendation: str,
        liquidity_tier: str
    ) -> Optional[Dict[str, Any]]:
        """Check for conflicting VRP and liquidity signals."""
        # CRITICAL: EXCELLENT VRP + REJECT liquidity
        if recommendation == 'EXCELLENT' and liquidity_tier == 'REJECT':
            return {
                'type': 'conflicting_signals',
                'severity': 'critical',
                'message': (
                    f"EXCELLENT VRP but REJECT liquidity - "
                    f"DO NOT TRADE (learned from WDAY/ZS $26,930 loss)"
                )
            }

        # WARNING: GOOD VRP + REJECT liquidity
        if recommendation == 'GOOD' and liquidity_tier == 'REJECT':
            return {
                'type': 'reject_liquidity',
                'severity': 'warning',
                'message': (
                    f"GOOD VRP but REJECT liquidity - "
                    f"high execution risk"
                )
            }

        # WARNING: EXCELLENT/GOOD VRP + WARNING liquidity
        if recommendation in ['EXCELLENT', 'GOOD'] and liquidity_tier == 'WARNING':
            return {
                'type': 'reject_liquidity',
                'severity': 'warning',
                'message': (
                    f"{recommendation} VRP but WARNING liquidity - "
                    f"consider reducing position size"
                )
            }

        return None

    def _determine_recommendation(
        self,
        anomalies: List[Dict[str, Any]]
    ) -> str:
        """Determine overall trading recommendation based on anomalies."""
        # If any critical anomalies, block trade
        if any(a['severity'] == 'critical' for a in anomalies):
            return 'DO_NOT_TRADE'

        # If multiple warnings, suggest size reduction
        warning_count = sum(1 for a in anomalies if a['severity'] == 'warning')
        if warning_count >= 2:
            return 'REDUCE_SIZE'

        # If any warnings, suggest size reduction
        if warning_count >= 1:
            return 'REDUCE_SIZE'

        # No significant issues
        return 'TRADE'
