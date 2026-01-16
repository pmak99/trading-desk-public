"""AnalyzeOrchestrator - Single ticker deep dive analysis.

Coordinates parallel specialist analysis for comprehensive ticker evaluation,
providing VRP, liquidity, sentiment, strategies, and explanations.

Target: 60 seconds for single ticker (vs 45 seconds sequential in 5.0).
"""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from .base import BaseOrchestrator
from ..agents.ticker_analysis import TickerAnalysisAgent
from ..agents.sentiment_fetch import SentimentFetchAgent
from ..agents.explanation import ExplanationAgent
from ..agents.anomaly import AnomalyDetectionAgent
from ..agents.health import HealthCheckAgent
from ..integration.cache_4_0 import Cache4_0


class AnalyzeOrchestrator(BaseOrchestrator):
    """
    Orchestrator for /analyze - Single Ticker Deep Dive.

    Workflow:
    1. Run health check (fail fast if APIs down)
    2. Spawn specialist agents in parallel:
       - TickerAnalysisAgent: VRP, liquidity, strategies
       - SentimentFetchAgent: AI sentiment (cached if available)
       - ExplanationAgent: Narrative reasoning
       - AnomalyDetectionAgent: Data quality checks
    3. Wait for all specialists (with timeout)
    4. Synthesize insights into comprehensive report
    5. Generate final trade recommendation
    6. Return markdown-formatted report

    Example:
        orchestrator = AnalyzeOrchestrator()
        result = await orchestrator.orchestrate(
            ticker="NVDA",
            earnings_date="2026-02-05"
        )
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize AnalyzeOrchestrator."""
        super().__init__(config)
        self.timeout = self.config.get('timeout', 60)

    async def orchestrate(
        self,
        ticker: str,
        earnings_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute analyze orchestration workflow.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD), auto-detected if None

        Returns:
            Orchestration result with comprehensive analysis

        Example:
            result = await orchestrator.orchestrate(
                ticker="NVDA",
                earnings_date="2026-02-05"
            )
        """
        # Step 1: Health check
        print("[1/6] Running health check...")
        health_agent = HealthCheckAgent()
        health_result = health_agent.check_health()

        if health_result['status'] == 'unhealthy':
            return {
                'success': False,
                'error': 'System unhealthy - cannot proceed',
                'health': health_result
            }

        # Step 2: Auto-detect earnings date if not provided
        if earnings_date is None:
            print(f"[2/6] Auto-detecting earnings date for {ticker}...")
            earnings_date = self._lookup_earnings_date(ticker)
            if earnings_date is None:
                return {
                    'success': False,
                    'error': f'Could not find earnings date for {ticker}'
                }
            print(f"  Found: {earnings_date}")
        else:
            print(f"[2/6] Using provided earnings date: {earnings_date}")

        # Step 3: Spawn specialist agents in parallel
        print(f"[3/6] Spawning {ticker} specialist agents...")
        specialist_results = await self._parallel_specialist_analysis(
            ticker=ticker,
            earnings_date=earnings_date
        )

        # Step 4: Check for critical failures
        print("[4/6] Checking specialist results...")
        if self._has_critical_failures(specialist_results):
            return {
                'success': False,
                'error': 'Critical analysis failure',
                'specialist_results': specialist_results
            }

        # Step 5: Synthesize into comprehensive report
        print("[5/6] Synthesizing comprehensive report...")
        report = self._synthesize_report(
            ticker=ticker,
            earnings_date=earnings_date,
            specialist_results=specialist_results
        )

        # Step 6: Generate final recommendation
        print("[6/6] Generating final recommendation...")
        recommendation = self._generate_recommendation(
            specialist_results=specialist_results,
            report=report
        )

        return {
            'success': True,
            'ticker': ticker,
            'earnings_date': earnings_date,
            'report': report,
            'recommendation': recommendation,
            'specialist_results': specialist_results,
            'summary': self.get_orchestration_summary()
        }

    def _lookup_earnings_date(self, ticker: str) -> Optional[str]:
        """Look up earnings date from calendar."""
        try:
            # Get upcoming earnings for next 30 days
            result = self.container_2_0.get_upcoming_earnings(days_ahead=30)

            # Handle Result type
            if hasattr(result, 'is_error') and result.is_error():
                return None

            # Extract value
            if hasattr(result, 'value'):
                earnings_tuples = result.value
            else:
                earnings_tuples = result

            # Find ticker
            for t, date_obj in earnings_tuples:
                if t.upper() == ticker.upper():
                    # Convert date to string
                    if hasattr(date_obj, 'strftime'):
                        return date_obj.strftime('%Y-%m-%d')
                    return str(date_obj)

            return None
        except Exception as e:
            print(f"  Error looking up earnings date: {e}")
            return None

    async def _parallel_specialist_analysis(
        self,
        ticker: str,
        earnings_date: str
    ) -> Dict[str, Any]:
        """Run all specialist agents in parallel."""
        # Create agents
        ticker_agent = TickerAnalysisAgent()
        sentiment_agent = SentimentFetchAgent()
        explanation_agent = ExplanationAgent()
        anomaly_agent = AnomalyDetectionAgent()

        # Build tasks
        tasks = {
            'ticker_analysis': asyncio.create_task(
                asyncio.to_thread(
                    ticker_agent.analyze,
                    ticker,
                    earnings_date,
                    generate_strategies=True
                )
            ),
            'sentiment': asyncio.create_task(
                asyncio.to_thread(
                    sentiment_agent.fetch_sentiment,
                    ticker,
                    earnings_date
                )
            )
        }

        # Wait for core analyses with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    tasks['ticker_analysis'],
                    tasks['sentiment'],
                    return_exceptions=True
                ),
                timeout=self.timeout
            )

            ticker_result = results[0]
            sentiment_result = results[1]

            # Check if ticker analysis succeeded
            if isinstance(ticker_result, Exception):
                return {
                    'ticker_analysis': {'error': str(ticker_result)},
                    'sentiment': sentiment_result if not isinstance(sentiment_result, Exception) else {'error': str(sentiment_result)},
                    'explanation': None,
                    'anomaly': None
                }

            if ticker_result.get('error'):
                return {
                    'ticker_analysis': ticker_result,
                    'sentiment': sentiment_result if not isinstance(sentiment_result, Exception) else {'error': str(sentiment_result)},
                    'explanation': None,
                    'anomaly': None
                }

            # Generate explanation based on ticker analysis
            explanation = explanation_agent.explain(
                ticker=ticker,
                vrp_ratio=ticker_result.get('vrp_ratio', 0),
                liquidity_tier=ticker_result.get('liquidity_tier', 'N/A')
            )

            # Get actual cache age from sentiment cache
            cache = Cache4_0()
            cache_age_hours = cache.get_cache_age_hours(ticker, earnings_date)
            if cache_age_hours is None:
                cache_age_hours = 0.0  # Fresh data if not cached

            # Get actual historical quarters count
            historical_moves = ticker_agent.get_historical_moves(ticker, limit=50)
            historical_quarters = len(historical_moves) if historical_moves else 0

            # Run anomaly detection with actual values
            anomaly = anomaly_agent.detect(
                ticker=ticker,
                vrp_ratio=ticker_result.get('vrp_ratio', 0),
                recommendation=ticker_result.get('recommendation', 'SKIP'),
                liquidity_tier=ticker_result.get('liquidity_tier', 'REJECT'),
                earnings_date=earnings_date,
                cache_age_hours=cache_age_hours,
                historical_quarters=historical_quarters
            )

            return {
                'ticker_analysis': ticker_result,
                'sentiment': sentiment_result if not isinstance(sentiment_result, Exception) else {'error': str(sentiment_result)},
                'explanation': explanation,
                'anomaly': anomaly
            }

        except asyncio.TimeoutError:
            return {
                'ticker_analysis': {'error': 'Timeout during analysis'},
                'sentiment': {'error': 'Timeout'},
                'explanation': None,
                'anomaly': None
            }

    def _has_critical_failures(self, specialist_results: Dict[str, Any]) -> bool:
        """Check if any critical specialist failed."""
        # Ticker analysis is critical
        ticker_result = specialist_results.get('ticker_analysis', {})
        if ticker_result.get('error'):
            return True

        # Check anomaly for DO_NOT_TRADE recommendation
        anomaly_result = specialist_results.get('anomaly', {})
        if anomaly_result and anomaly_result.get('recommendation') == 'DO_NOT_TRADE':
            print("  ⚠️  CRITICAL: Anomaly detection recommends DO_NOT_TRADE")
            return True

        return False

    def _synthesize_report(
        self,
        ticker: str,
        earnings_date: str,
        specialist_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Synthesize specialist insights into comprehensive report."""
        ticker_result = specialist_results.get('ticker_analysis', {})
        sentiment_result = specialist_results.get('sentiment', {})
        explanation_result = specialist_results.get('explanation', {})
        anomaly_result = specialist_results.get('anomaly', {})

        # Build report sections
        report = {
            'ticker': ticker,
            'earnings_date': earnings_date,
            'summary': {
                'vrp_ratio': ticker_result.get('vrp_ratio'),
                'recommendation': ticker_result.get('recommendation'),
                'liquidity_tier': ticker_result.get('liquidity_tier'),
                'score': ticker_result.get('score'),
                'sentiment_direction': sentiment_result.get('direction'),
                'sentiment_score': sentiment_result.get('score')
            },
            'vrp_analysis': {
                'ratio': ticker_result.get('vrp_ratio'),
                'recommendation': ticker_result.get('recommendation'),
                'explanation': explanation_result.get('explanation', '')
            },
            'liquidity': {
                'tier': ticker_result.get('liquidity_tier'),
                'tradeable': ticker_result.get('liquidity_tier') in ['EXCELLENT', 'GOOD']
            },
            'sentiment': {
                'direction': sentiment_result.get('direction', 'neutral'),
                'score': sentiment_result.get('score', 0.0),
                'catalysts': sentiment_result.get('catalysts', []),
                'risks': sentiment_result.get('risks', []),
                'cached': sentiment_result.get('cached', False)
            },
            'strategies': ticker_result.get('strategies', []),
            'anomalies': anomaly_result.get('anomalies', []) if anomaly_result else [],
            'key_factors': explanation_result.get('key_factors', []),
            'historical_context': explanation_result.get('historical_context', '')
        }

        return report

    def _generate_recommendation(
        self,
        specialist_results: Dict[str, Any],
        report: Dict[str, Any]
    ) -> Dict[str, str]:
        """Generate final trade recommendation."""
        ticker_result = specialist_results.get('ticker_analysis', {})
        anomaly_result = specialist_results.get('anomaly', {})

        # Check anomaly first
        if anomaly_result and anomaly_result.get('recommendation') == 'DO_NOT_TRADE':
            return {
                'action': 'DO_NOT_TRADE',
                'reason': 'Critical anomalies detected',
                'details': 'See anomalies section for details'
            }

        # Check liquidity
        liquidity_tier = ticker_result.get('liquidity_tier', 'REJECT')
        if liquidity_tier == 'REJECT':
            return {
                'action': 'DO_NOT_TRADE',
                'reason': 'REJECT liquidity tier',
                'details': 'Insufficient liquidity for safe execution'
            }

        # Check VRP
        vrp_ratio = ticker_result.get('vrp_ratio', 0)
        recommendation = ticker_result.get('recommendation', 'SKIP')

        if vrp_ratio >= 4.0 and liquidity_tier in ['EXCELLENT', 'GOOD']:
            return {
                'action': 'TRADE',
                'reason': f'{recommendation} VRP ({vrp_ratio:.2f}x) + {liquidity_tier} liquidity',
                'details': 'Strong opportunity with acceptable risk'
            }
        elif vrp_ratio >= 3.0 and liquidity_tier in ['EXCELLENT', 'GOOD']:
            return {
                'action': 'TRADE_CAUTIOUSLY',
                'reason': f'{recommendation} VRP ({vrp_ratio:.2f}x) + {liquidity_tier} liquidity',
                'details': 'Moderate opportunity - consider reduced position size'
            }
        else:
            return {
                'action': 'SKIP',
                'reason': f'VRP {vrp_ratio:.2f}x below threshold or inadequate liquidity',
                'details': 'Edge not sufficient for trade'
            }

    def format_results(self, result: Dict[str, Any]) -> str:
        """Format orchestration results as markdown report."""
        if not result.get('success'):
            # Extract detailed error information if available
            output = []
            output.append("# Analysis Failed")
            output.append("")
            output.append(f"**Error:** {result.get('error', 'Unknown error')}")
            output.append("")

            # Check for anomaly details in specialist_results
            specialist_results = result.get('specialist_results', {})
            anomaly_result = specialist_results.get('anomaly', {})

            if anomaly_result:
                anomalies = anomaly_result.get('anomalies', [])
                recommendation = anomaly_result.get('recommendation', '')

                if anomalies:
                    output.append("## Anomalies Detected")
                    output.append("")
                    for anomaly in anomalies:
                        severity = anomaly.get('severity', 'warning')
                        msg = anomaly.get('message', '')
                        if severity == 'critical':
                            output.append(f"🚫 **CRITICAL:** {msg}")
                        else:
                            output.append(f"⚠️  **Warning:** {msg}")
                    output.append("")

                if recommendation:
                    output.append(f"**Recommendation:** {recommendation}")
                    output.append("")

            # Show ticker analysis details if available
            ticker_result = specialist_results.get('ticker_analysis', {})
            if ticker_result and not ticker_result.get('error'):
                output.append("## Partial Analysis")
                output.append("")
                output.append(f"- **VRP Ratio:** {ticker_result.get('vrp_ratio', 'N/A')}")
                output.append(f"- **Recommendation:** {ticker_result.get('recommendation', 'N/A')}")
                output.append(f"- **Liquidity Tier:** {ticker_result.get('liquidity_tier', 'N/A')}")
                output.append("")

            return "\n".join(output)

        report = result.get('report', {})
        recommendation = result.get('recommendation', {})

        # Build markdown report
        output = []
        output.append("=" * 60)
        output.append(f"ANALYSIS: {report['ticker']} (Earnings: {report['earnings_date']})")
        output.append("=" * 60)
        output.append("")

        # Recommendation (top)
        action = recommendation.get('action', 'UNKNOWN')
        reason = recommendation.get('reason', '')
        details = recommendation.get('details', '')

        output.append("## RECOMMENDATION")
        output.append("")
        if action == 'TRADE':
            output.append(f"✅ **{action}**")
        elif action == 'TRADE_CAUTIOUSLY':
            output.append(f"⚠️  **{action}**")
        else:
            output.append(f"❌ **{action}**")
        output.append(f"- **Reason:** {reason}")
        output.append(f"- **Details:** {details}")
        output.append("")

        # Summary
        output.append("## Summary")
        output.append("")
        summary = report['summary']
        output.append(f"- **VRP Ratio:** {summary['vrp_ratio']:.2f}x ({summary['recommendation']})")
        output.append(f"- **Liquidity:** {summary['liquidity_tier']}")
        output.append(f"- **Score:** {summary['score']}")

        # Handle None sentiment score/direction
        sent_direction = summary.get('sentiment_direction') or 'N/A'
        sent_score = summary.get('sentiment_score')
        if sent_score is not None:
            output.append(f"- **Sentiment:** {sent_direction} ({sent_score:.2f})")
        else:
            output.append(f"- **Sentiment:** {sent_direction} (unavailable)")
        output.append("")

        # VRP Analysis
        output.append("## VRP Analysis")
        output.append("")
        vrp = report['vrp_analysis']
        output.append(f"**Ratio:** {vrp['ratio']:.2f}x ({vrp['recommendation']})")
        output.append("")
        output.append(f"**Why is VRP elevated?**")
        output.append(vrp['explanation'])
        output.append("")

        # Historical Context
        if report['historical_context']:
            output.append("**Historical Context:**")
            output.append(report['historical_context'])
            output.append("")

        # Key Factors
        if report['key_factors']:
            output.append("**Key Factors:**")
            for factor in report['key_factors']:
                output.append(f"- {factor}")
            output.append("")

        # Liquidity
        output.append("## Liquidity")
        output.append("")
        liquidity = report['liquidity']
        tier = liquidity['tier']
        tradeable = liquidity['tradeable']

        if tradeable:
            output.append(f"✅ **{tier}** - Tradeable")
        else:
            output.append(f"❌ **{tier}** - Not tradeable")
        output.append("")

        # Sentiment
        output.append("## Sentiment")
        output.append("")
        sentiment = report['sentiment']

        # Handle None sentiment score/direction
        sent_direction = (sentiment.get('direction') or 'N/A').title()
        sent_score = sentiment.get('score')
        if sent_score is not None:
            output.append(f"**Direction:** {sent_direction} ({sent_score:.2f})")
        else:
            output.append(f"**Direction:** {sent_direction} (unavailable)")

        if sentiment.get('cached'):
            output.append("*(Cached - pre-fetched)*")
        output.append("")

        if sentiment['catalysts']:
            output.append("**Catalysts:**")
            for catalyst in sentiment['catalysts']:
                output.append(f"- {catalyst}")
            output.append("")

        if sentiment['risks']:
            output.append("**Risks:**")
            for risk in sentiment['risks']:
                output.append(f"- {risk}")
            output.append("")

        # Strategies
        if report['strategies']:
            output.append("## Strategies")
            output.append("")
            output.append(f"**{len(report['strategies'])} strategies generated**")
            output.append("*(Full strategy details available in ticker_analysis result)*")
            output.append("")

        # Anomalies
        if report['anomalies']:
            output.append("## ⚠️  Anomalies Detected")
            output.append("")
            for anomaly in report['anomalies']:
                severity = anomaly.get('severity', 'warning')
                msg = anomaly.get('message', '')
                if severity == 'critical':
                    output.append(f"🚫 **CRITICAL:** {msg}")
                else:
                    output.append(f"⚠️  **Warning:** {msg}")
            output.append("")

        output.append("=" * 60)

        return "\n".join(output)
