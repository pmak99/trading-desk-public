"""AnalyzeOrchestrator - Single ticker deep dive analysis.

Coordinates parallel specialist analysis for comprehensive ticker evaluation,
providing VRP, liquidity, sentiment, news, strategies, and explanations.

Pipeline:
  Step 1: [PreFlight + Health] parallel
  Step 2: Fail-fast (invalid ticker? unhealthy?)
  Step 3: Auto-detect earnings date
  Step 4: [TickerAnalysis + Sentiment + News + Pattern] all parallel (with retry)
  Step 5: [Explanation + Anomaly] parallel (depend on Step 4 TickerAnalysis)
  Step 6: Synthesize + Recommend

Target: 20-30 seconds (down from 45-60 seconds sequential).
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from .base import BaseOrchestrator
from ..agents.ticker_analysis import TickerAnalysisAgent
from ..agents.sentiment_fetch import SentimentFetchAgent
from ..agents.explanation import ExplanationAgent
from ..agents.anomaly import AnomalyDetectionAgent
from ..agents.health import HealthCheckAgent
from ..agents.pattern_recognition import PatternRecognitionAgent
from ..agents.preflight import PreFlightAgent
from ..agents.news_fetch import NewsFetchAgent
from ..integration.cache_4_0 import Cache4_0
from ..utils.retry import with_retry

logger = logging.getLogger(__name__)


class AnalyzeOrchestrator(BaseOrchestrator):
    """
    Orchestrator for /analyze - Single Ticker Deep Dive.

    Workflow:
    1. Run PreFlight + Health in parallel (fail fast)
    2. Auto-detect earnings date
    3. Run TickerAnalysis + Sentiment + News + Pattern in parallel (with retry)
    4. Run Explanation + Anomaly in parallel (depend on TickerAnalysis)
    5. Synthesize insights into comprehensive report
    6. Generate final trade recommendation

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
            ticker: Stock ticker symbol (or company name alias)
            earnings_date: Earnings date (YYYY-MM-DD), auto-detected if None

        Returns:
            Orchestration result with comprehensive analysis
        """
        # Step 1: PreFlight + Health in parallel
        print("[1/7] Running pre-flight validation + health check...")
        preflight_agent = PreFlightAgent()
        health_agent = HealthCheckAgent()

        preflight_result, health_result = await asyncio.gather(
            asyncio.to_thread(preflight_agent.validate, ticker, earnings_date),
            asyncio.to_thread(health_agent.check_health),
        )

        # Step 2: Fail-fast checks
        if not preflight_result.get('is_valid'):
            return {
                'success': False,
                'error': f"Pre-flight failed: {preflight_result.get('error', 'Invalid ticker')}",
                'preflight': preflight_result
            }

        if health_result['status'] == 'unhealthy':
            return {
                'success': False,
                'error': 'System unhealthy - cannot proceed',
                'health': health_result
            }

        # Use normalized ticker from pre-flight
        normalized_ticker = preflight_result['normalized_ticker']
        if normalized_ticker != ticker.upper().strip():
            print(f"  Resolved: {ticker} -> {normalized_ticker}")

        if preflight_result.get('warnings'):
            for w in preflight_result['warnings']:
                print(f"  Warning: {w}")

        # Step 3: Auto-detect earnings date if not provided
        if earnings_date is None:
            print(f"[2/7] Auto-detecting earnings date for {normalized_ticker}...")
            earnings_date = self._lookup_earnings_date(normalized_ticker)
            if earnings_date is None:
                return {
                    'success': False,
                    'error': f'Could not find earnings date for {normalized_ticker}',
                    'preflight': preflight_result
                }
            print(f"  Found: {earnings_date}")
        else:
            print(f"[2/7] Using provided earnings date: {earnings_date}")

        # Step 4: Spawn specialist agents in parallel
        print(f"[3/7] Spawning {normalized_ticker} specialist agents (parallel)...")
        specialist_results = await self._parallel_specialist_analysis(
            ticker=normalized_ticker,
            earnings_date=earnings_date
        )

        # Include preflight and news in specialist results
        specialist_results['preflight'] = preflight_result

        # Step 5: Check for critical failures
        print("[4/7] Checking specialist results...")
        if self._has_critical_failures(specialist_results):
            return {
                'success': False,
                'error': 'Critical analysis failure',
                'specialist_results': specialist_results
            }

        # Step 6: Synthesize into comprehensive report
        print("[5/7] Synthesizing comprehensive report...")
        report = self._synthesize_report(
            ticker=normalized_ticker,
            earnings_date=earnings_date,
            specialist_results=specialist_results
        )

        # Step 7: Generate final recommendation
        print("[6/7] Generating final recommendation...")
        recommendation = self._generate_recommendation(
            specialist_results=specialist_results,
            report=report
        )

        print("[7/7] Done.")
        return {
            'success': True,
            'ticker': normalized_ticker,
            'earnings_date': earnings_date,
            'report': report,
            'recommendation': recommendation,
            'specialist_results': specialist_results,
            'summary': self.get_orchestration_summary()
        }

    def _lookup_earnings_date(self, ticker: str) -> Optional[str]:
        """Look up earnings date from calendar."""
        try:
            result = self.container_2_0.get_upcoming_earnings(days_ahead=30)

            if hasattr(result, 'is_err') and result.is_err:
                return None

            if hasattr(result, 'value'):
                earnings_tuples = result.value
            else:
                earnings_tuples = result

            for t, date_obj in earnings_tuples:
                if t.upper() == ticker.upper():
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
        """Run all specialist agents in parallel with retry.

        Phase 1: TickerAnalysis + Sentiment + News + Pattern (all independent)
        Phase 2: Explanation + Anomaly (depend on TickerAnalysis from Phase 1)
        """
        # Create agents
        ticker_agent = TickerAnalysisAgent()
        sentiment_agent = SentimentFetchAgent()
        news_agent = NewsFetchAgent()
        pattern_agent = PatternRecognitionAgent()

        # Phase 1: All independent agents in parallel with retry
        print("  Phase 1: TickerAnalysis + Sentiment + News + Pattern (parallel)...")
        try:
            phase1_results = await asyncio.wait_for(
                asyncio.gather(
                    with_retry(
                        lambda: asyncio.to_thread(
                            ticker_agent.analyze,
                            ticker,
                            earnings_date,
                            generate_strategies=True
                        ),
                        max_retries=3,
                        base_delay=2.0,
                        label=f"TickerAnalysis({ticker})"
                    ),
                    with_retry(
                        lambda: sentiment_agent.fetch_sentiment(ticker, earnings_date),
                        max_retries=2,
                        base_delay=2.0,
                        label=f"Sentiment({ticker})"
                    ),
                    with_retry(
                        lambda: news_agent.fetch_news(ticker),
                        max_retries=1,
                        base_delay=2.0,
                        label=f"News({ticker})"
                    ),
                    with_retry(
                        lambda: asyncio.to_thread(pattern_agent.analyze, ticker),
                        max_retries=1,
                        base_delay=2.0,
                        label=f"Pattern({ticker})"
                    ),
                    return_exceptions=True
                ),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            return {
                'ticker_analysis': {'error': 'Timeout during Phase 1 analysis'},
                'sentiment': {'error': 'Timeout'},
                'news': {'error': 'Timeout'},
                'explanation': None,
                'anomaly': None,
                'patterns': None
            }

        # Unwrap Phase 1 results
        ticker_result = self._unwrap_result(phase1_results[0], 'ticker_analysis')
        sentiment_result = self._unwrap_result(phase1_results[1], 'sentiment')
        news_result = self._unwrap_result(phase1_results[2], 'news')
        patterns_result = self._unwrap_result(phase1_results[3], 'patterns')

        # Check if ticker analysis succeeded (critical dependency for Phase 2)
        if ticker_result.get('error'):
            print(f"  TickerAnalysis failed: {ticker_result['error']}")
            return {
                'ticker_analysis': ticker_result,
                'sentiment': sentiment_result,
                'news': news_result,
                'explanation': None,
                'anomaly': None,
                'patterns': patterns_result
            }

        # Phase 2: Explanation + Anomaly in parallel (depend on TickerAnalysis)
        print("  Phase 2: Explanation + Anomaly (parallel, depend on TickerAnalysis)...")
        explanation_agent = ExplanationAgent()
        anomaly_agent = AnomalyDetectionAgent()

        # Prepare anomaly detection inputs
        cache = Cache4_0()
        cache_age_hours = cache.get_cache_age_hours(ticker, earnings_date)
        if cache_age_hours is None:
            cache_age_hours = 0.0

        historical_moves = ticker_agent.get_historical_moves(ticker, limit=50)
        historical_quarters = len(historical_moves) if historical_moves else 0

        try:
            phase2_results = await asyncio.wait_for(
                asyncio.gather(
                    asyncio.to_thread(
                        explanation_agent.explain,
                        ticker=ticker,
                        vrp_ratio=ticker_result.get('vrp_ratio', 0),
                        liquidity_tier=ticker_result.get('liquidity_tier', 'N/A')
                    ),
                    asyncio.to_thread(
                        anomaly_agent.detect,
                        ticker=ticker,
                        vrp_ratio=ticker_result.get('vrp_ratio', 0),
                        recommendation=ticker_result.get('recommendation', 'SKIP'),
                        liquidity_tier=ticker_result.get('liquidity_tier', 'REJECT'),
                        earnings_date=earnings_date,
                        cache_age_hours=cache_age_hours,
                        historical_quarters=historical_quarters
                    ),
                    return_exceptions=True
                ),
                timeout=15
            )
        except asyncio.TimeoutError:
            phase2_results = [None, None]

        explanation = self._unwrap_result(phase2_results[0], 'explanation') if phase2_results[0] is not None else None
        anomaly = self._unwrap_result(phase2_results[1], 'anomaly') if phase2_results[1] is not None else None

        return {
            'ticker_analysis': ticker_result,
            'sentiment': sentiment_result,
            'news': news_result,
            'explanation': explanation,
            'anomaly': anomaly,
            'patterns': patterns_result
        }

    def _unwrap_result(self, result: Any, label: str) -> Dict[str, Any]:
        """Unwrap a gather result, converting exceptions to error dicts."""
        if isinstance(result, Exception):
            logger.warning(f"{label} raised {type(result).__name__}: {result}")
            return {'error': f'{type(result).__name__}: {result}'}
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        return {'error': f'Unexpected result type: {type(result).__name__}'}

    def _has_critical_failures(self, specialist_results: Dict[str, Any]) -> bool:
        """Check if any critical specialist failed."""
        ticker_result = specialist_results.get('ticker_analysis', {})
        if ticker_result.get('error'):
            return True

        anomaly_result = specialist_results.get('anomaly', {})
        if anomaly_result and anomaly_result.get('recommendation') == 'DO_NOT_TRADE':
            print("  CRITICAL: Anomaly detection recommends DO_NOT_TRADE")
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
        news_result = specialist_results.get('news', {})
        preflight_result = specialist_results.get('preflight', {})

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
                'tradeable': ticker_result.get('liquidity_tier') in ['EXCELLENT', 'GOOD', 'WARNING']
            },
            'sentiment': {
                'direction': sentiment_result.get('direction', 'neutral'),
                'score': sentiment_result.get('score', 0.0),
                'catalysts': sentiment_result.get('catalysts', []),
                'risks': sentiment_result.get('risks', []),
                'cached': sentiment_result.get('cached', False)
            },
            'news': {
                'headlines': news_result.get('headlines', []),
                'count': news_result.get('count', 0),
                'error': news_result.get('error')
            },
            'strategies': ticker_result.get('strategies', []),
            'anomalies': anomaly_result.get('anomalies', []) if anomaly_result else [],
            'key_factors': explanation_result.get('key_factors', []),
            'historical_context': explanation_result.get('historical_context', ''),
            'position_limits': ticker_result.get('position_limits'),
            'patterns': specialist_results.get('patterns'),
            'preflight_warnings': preflight_result.get('warnings', [])
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

        # Check VRP and liquidity (RELAXED Feb 2026 - REJECT allowed but penalized)
        liquidity_tier = ticker_result.get('liquidity_tier', 'REJECT')
        vrp_ratio = ticker_result.get('vrp_ratio', 0)
        recommendation = ticker_result.get('recommendation', 'SKIP')

        if vrp_ratio >= 1.8 and liquidity_tier in ['EXCELLENT', 'GOOD']:
            return {
                'action': 'TRADE',
                'reason': f'{recommendation} VRP ({vrp_ratio:.2f}x) + {liquidity_tier} liquidity',
                'details': 'Strong opportunity with acceptable risk'
            }
        elif vrp_ratio >= 1.8 and liquidity_tier in ['WARNING', 'REJECT']:
            return {
                'action': 'TRADE_CAUTIOUSLY',
                'reason': f'{recommendation} VRP ({vrp_ratio:.2f}x) + {liquidity_tier} liquidity',
                'details': 'Strong VRP but reduce position size due to liquidity'
            }
        elif vrp_ratio >= 1.4 and liquidity_tier in ['EXCELLENT', 'GOOD']:
            return {
                'action': 'TRADE_CAUTIOUSLY',
                'reason': f'{recommendation} VRP ({vrp_ratio:.2f}x) + {liquidity_tier} liquidity',
                'details': 'Moderate opportunity - consider reduced position size'
            }
        elif vrp_ratio >= 1.4 and liquidity_tier in ['WARNING', 'REJECT']:
            return {
                'action': 'TRADE_CAUTIOUSLY',
                'reason': f'{recommendation} VRP ({vrp_ratio:.2f}x) + {liquidity_tier} liquidity',
                'details': 'Reduce position size due to liquidity constraints'
            }
        elif vrp_ratio < 1.4:
            return {
                'action': 'SKIP',
                'reason': f'VRP {vrp_ratio:.2f}x below GOOD threshold (1.4x)',
                'details': 'Insufficient volatility risk premium'
            }
        else:
            return {
                'action': 'SKIP',
                'reason': f'VRP {vrp_ratio:.2f}x insufficient',
                'details': 'No sufficient edge detected'
            }

    def format_results(self, result: Dict[str, Any]) -> str:
        """Format orchestration results as markdown report."""
        if not result.get('success'):
            output = []
            output.append("# Analysis Failed")
            output.append("")
            output.append(f"**Error:** {result.get('error', 'Unknown error')}")
            output.append("")

            # Pre-flight details
            preflight = result.get('preflight', {})
            if preflight and preflight.get('warnings'):
                output.append("## Pre-flight Warnings")
                output.append("")
                for w in preflight['warnings']:
                    output.append(f"- {w}")
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
                            output.append(f"CRITICAL: {msg}")
                        else:
                            output.append(f"Warning: {msg}")
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

        # Pre-flight warnings (if any)
        preflight_warnings = report.get('preflight_warnings', [])
        if preflight_warnings:
            output.append("## Pre-flight Warnings")
            output.append("")
            for w in preflight_warnings:
                output.append(f"- {w}")
            output.append("")

        # Recommendation (top)
        action = recommendation.get('action', 'UNKNOWN')
        reason = recommendation.get('reason', '')
        details = recommendation.get('details', '')

        output.append("## RECOMMENDATION")
        output.append("")
        if action == 'TRADE':
            output.append(f"**{action}**")
        elif action == 'TRADE_CAUTIOUSLY':
            output.append(f"**{action}**")
        else:
            output.append(f"**{action}**")
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

        if tier in ['EXCELLENT', 'GOOD']:
            output.append(f"**{tier}** - Tradeable at full size")
        elif tier == 'WARNING':
            output.append(f"**{tier}** - Tradeable at reduced size")
        else:
            output.append(f"**{tier}** - Not tradeable")
        output.append("")

        # Position Limits (if HIGH TRR)
        position_limits = report.get('position_limits')
        if position_limits and position_limits.get('tail_risk_level') == 'HIGH':
            output.append("## Position Limits")
            output.append("")
            output.append(f"**Tail Risk Ratio:** {position_limits['tail_risk_ratio']:.2f}x (HIGH)")
            output.append(f"**Max Contracts:** {position_limits['max_contracts']}")
            output.append(f"**Max Notional:** ${position_limits['max_notional']:,.0f}")
            output.append(f"**Reason:** Historical max move {position_limits['max_move']:.1f}% vs avg {position_limits['avg_move']:.1f}%")
            output.append("")

        # Sentiment
        output.append("## Sentiment")
        output.append("")
        sentiment = report['sentiment']

        sent_direction = (sentiment.get('direction') or 'N/A').title()
        sent_score = sentiment.get('score')
        if sent_score is not None:
            output.append(f"**Direction:** {sent_direction} ({sent_score:.2f})")
        else:
            output.append(f"**Direction:** {sent_direction} (unavailable)")

        if sentiment.get('cached'):
            output.append("*(Cached - pre-fetched)*")
        output.append("")

        # Filter malformed catalysts
        valid_catalysts = [c for c in sentiment['catalysts'] if c and c.strip('*').strip()]
        if valid_catalysts:
            output.append("**Catalysts:**")
            for catalyst in valid_catalysts:
                output.append(f"- {catalyst}")
            output.append("")

        valid_risks = [r for r in sentiment['risks'] if r and r.strip('*').strip()]
        if valid_risks:
            output.append("**Risks:**")
            for risk in valid_risks:
                output.append(f"- {risk}")
            output.append("")

        # Recent News
        news = report.get('news', {})
        headlines = news.get('headlines', [])
        if headlines:
            output.append("## Recent News")
            output.append("")
            for headline in headlines:
                title = headline.get('title', '') if isinstance(headline, dict) else str(headline)
                source = headline.get('source', '') if isinstance(headline, dict) else ''
                dt = headline.get('datetime', '') if isinstance(headline, dict) else ''
                line = f"- {title}"
                if source:
                    line += f" ({source})"
                if dt:
                    line += f" [{dt}]"
                output.append(line)
            output.append("")
        elif news.get('error'):
            output.append("## Recent News")
            output.append("")
            output.append(f"*{news['error']}*")
            output.append("")

        # Strategies
        if report['strategies']:
            output.append("## Strategies")
            output.append("")
            for strategy in report['strategies']:
                strategy_type = strategy.get('type', 'Unknown')
                max_profit = strategy.get('max_profit', 0)
                max_loss = strategy.get('max_loss', 0)
                pop = strategy.get('probability_of_profit', 0)
                contracts = strategy.get('contracts', 0)

                output.append(f"**{strategy_type}**")
                strikes = strategy.get('strikes')
                if strikes:
                    output.append(f"  - Strikes: {strikes}")
                output.append(f"  - Max Profit: ${max_profit:,.2f} | Max Loss: ${max_loss:,.2f}")
                output.append(f"  - POP: {pop:.0%} | Contracts: {contracts}")
                output.append("")

        # Anomalies
        if report['anomalies']:
            output.append("## Anomalies Detected")
            output.append("")
            for anomaly in report['anomalies']:
                severity = anomaly.get('severity', 'warning')
                msg = anomaly.get('message', '')
                if severity == 'critical':
                    output.append(f"CRITICAL: {msg}")
                else:
                    output.append(f"Warning: {msg}")
            output.append("")

        # Historical Patterns
        patterns = report.get('patterns')
        if patterns and patterns.get('quarters_analyzed', 0) >= 8:
            output.append("## Historical Patterns")
            output.append("")
            output.append(f"**Quarters Analyzed:** {patterns['quarters_analyzed']}")
            output.append("")

            bias = patterns.get('directional_bias', 'NEUTRAL')
            bullish_pct = patterns.get('bullish_pct', 0.5)
            bias_indicator = {'BULLISH': '(UP)', 'BEARISH': '(DOWN)', 'NEUTRAL': '(--)'}.get(bias, '(--)')
            output.append(f"{bias_indicator} **Directional Bias:** {bias} ({bullish_pct:.0%} UP moves)")

            streak = patterns.get('current_streak', 0)
            streak_dir = patterns.get('streak_direction', 'UP')
            if streak >= 3:
                output.append(f"**Current Streak:** {streak} consecutive {streak_dir}")

            trend = patterns.get('magnitude_trend')
            if trend and trend != 'STABLE':
                recent = patterns.get('avg_move_recent', 0)
                overall = patterns.get('avg_move_overall', 0)
                trend_indicator = '(EXPANDING)' if trend == 'EXPANDING' else '(CONTRACTING)'
                output.append(f"{trend_indicator} **Magnitude:** {trend} ({recent:.1f}% recent vs {overall:.1f}% avg)")

            recent_moves = patterns.get('recent_moves', [])
            if recent_moves:
                output.append("")
                output.append("**Recent Earnings:**")
                for move in recent_moves:
                    arrow = '(UP)' if move['direction'] == 'UP' else '(DOWN)'
                    output.append(f"  {move['date']}: {move['move']:+.1f}% {arrow}")

            output.append("")

        output.append("=" * 60)

        return "\n".join(output)
