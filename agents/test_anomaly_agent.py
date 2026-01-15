#!/usr/bin/env python3
"""Test script for AnomalyDetectionAgent."""

from datetime import datetime, timedelta

class AnomalyDetectionAgent:
    """Worker agent for detecting anomalies."""
    
    STALE_CACHE_HOURS = 24
    MIN_HISTORICAL_QUARTERS = 4
    EXTREME_VRP_THRESHOLD = 20.0
    
    def detect(self, ticker, vrp_ratio, recommendation, liquidity_tier,
               earnings_date, cache_age_hours=0.0, historical_quarters=0):
        """Detect anomalies in ticker analysis."""
        anomalies = []
        
        # Check 1: Stale earnings data
        try:
            earnings_dt = datetime.strptime(earnings_date, '%Y-%m-%d')
            days_until_earnings = (earnings_dt - datetime.now()).days
            
            if days_until_earnings <= 7 and cache_age_hours > self.STALE_CACHE_HOURS:
                anomalies.append({
                    'type': 'stale_data',
                    'severity': 'warning',
                    'message': f'Earnings within 7 days but cache is {cache_age_hours:.1f}h old (>{self.STALE_CACHE_HOURS}h threshold)'
                })
        except:
            pass
        
        # Check 2: Missing historical data
        if historical_quarters < self.MIN_HISTORICAL_QUARTERS:
            anomalies.append({
                'type': 'missing_data',
                'severity': 'warning',
                'message': f'Only {historical_quarters} quarters of data (minimum: {self.MIN_HISTORICAL_QUARTERS})'
            })
        
        # Check 3: Extreme outliers
        if vrp_ratio > self.EXTREME_VRP_THRESHOLD:
            anomalies.append({
                'type': 'extreme_outlier',
                'severity': 'warning',
                'message': f'VRP ratio {vrp_ratio:.1f}x exceeds extreme threshold ({self.EXTREME_VRP_THRESHOLD}x)'
            })
        
        # Check 4: Conflicting signals (CRITICAL)
        if recommendation in ['EXCELLENT', 'GOOD'] and liquidity_tier == 'REJECT':
            severity = 'critical' if recommendation == 'EXCELLENT' else 'warning'
            anomalies.append({
                'type': 'conflicting_signals',
                'severity': severity,
                'message': f'{recommendation} VRP ({vrp_ratio:.1f}x) but REJECT liquidity - DO NOT TRADE'
            })
        
        # Determine recommendation
        has_critical = any(a['severity'] == 'critical' for a in anomalies)
        has_warnings = any(a['severity'] == 'warning' for a in anomalies)
        
        if has_critical or liquidity_tier == 'REJECT':
            final_recommendation = 'DO_NOT_TRADE'
        elif has_warnings:
            final_recommendation = 'REDUCE_SIZE'
        else:
            final_recommendation = 'TRADE'
        
        return {
            'ticker': ticker,
            'anomalies': anomalies,
            'recommendation': final_recommendation
        }

def test_scenario(name: str, **kwargs):
    """Test a specific anomaly scenario."""
    print(f"\n{'=' * 60}")
    print(f"TEST: {name}")
    print('=' * 60)
    
    agent = AnomalyDetectionAgent()
    result = agent.detect(**kwargs)
    
    print(f"Ticker: {result['ticker']}")
    print(f"Recommendation: {result['recommendation']}")
    print(f"Anomalies found: {len(result['anomalies'])}")
    
    for i, anomaly in enumerate(result['anomalies'], 1):
        severity_emoji = '🔴' if anomaly['severity'] == 'critical' else '⚠️ '
        print(f"\n{severity_emoji} Anomaly {i}:")
        print(f"   Type: {anomaly['type']}")
        print(f"   Severity: {anomaly['severity']}")
        print(f"   Message: {anomaly['message']}")
    
    return result

# Test 1: EXCELLENT VRP + REJECT liquidity (WDAY scenario - learned from significant loss)
test_scenario(
    "Critical Conflict - EXCELLENT VRP + REJECT Liquidity",
    ticker="WDAY",
    vrp_ratio=7.2,
    recommendation="EXCELLENT",
    liquidity_tier="REJECT",
    earnings_date="2026-02-05",
    cache_age_hours=2.0,
    historical_quarters=8
)

# Test 2: GOOD VRP + REJECT liquidity (warning)
test_scenario(
    "Warning - GOOD VRP + REJECT Liquidity",
    ticker="EXAMPLE",
    vrp_ratio=5.0,
    recommendation="GOOD",
    liquidity_tier="REJECT",
    earnings_date="2026-02-05",
    cache_age_hours=2.0,
    historical_quarters=8
)

# Test 3: Extreme VRP outlier
test_scenario(
    "Extreme Outlier - VRP > 20x",
    ticker="OUTLIER",
    vrp_ratio=25.0,
    recommendation="EXCELLENT",
    liquidity_tier="GOOD",
    earnings_date="2026-02-05",
    cache_age_hours=2.0,
    historical_quarters=8
)

# Test 4: Stale cache data
test_scenario(
    "Stale Data - Cache > 24h old",
    ticker="STALE",
    vrp_ratio=6.0,
    recommendation="EXCELLENT",
    liquidity_tier="GOOD",
    earnings_date="2026-01-20",  # Within 7 days
    cache_age_hours=36.0,  # > 24 hours
    historical_quarters=8
)

# Test 5: Missing historical data
test_scenario(
    "Missing Data - < 4 Quarters",
    ticker="NEWIPO",
    vrp_ratio=6.0,
    recommendation="EXCELLENT",
    liquidity_tier="GOOD",
    earnings_date="2026-02-05",
    cache_age_hours=2.0,
    historical_quarters=2  # < 4
)

# Test 6: Clean ticker (no anomalies)
test_scenario(
    "Clean Ticker - No Anomalies",
    ticker="NVDA",
    vrp_ratio=6.0,
    recommendation="EXCELLENT",
    liquidity_tier="GOOD",
    earnings_date="2026-02-05",
    cache_age_hours=2.0,
    historical_quarters=12
)

print("\n" + "=" * 60)
print("✅ ALL ANOMALY DETECTION TESTS COMPLETE")
print("=" * 60)
