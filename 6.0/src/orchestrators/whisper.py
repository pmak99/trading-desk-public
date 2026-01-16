"""WhisperOrchestrator - Most Anticipated Earnings discovery.

Coordinates parallel ticker analysis for upcoming earnings, providing
ranked opportunities with VRP, liquidity, sentiment, and explanations.

Target: 90 seconds for 30 tickers (vs 180 seconds sequential).
"""

import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from .base import BaseOrchestrator
from ..agents.ticker_analysis import TickerAnalysisAgent
from ..agents.explanation import ExplanationAgent
from ..agents.anomaly import AnomalyDetectionAgent
from ..agents.health import HealthCheckAgent
from ..integration.cache_4_0 import Cache4_0
from ..utils.formatter import format_whisper_results, format_cross_ticker_warnings


class WhisperOrchestrator(BaseOrchestrator):
    """
    Orchestrator for /whisper - Most Anticipated Earnings.

    Workflow:
    1. Run health check (fail fast if APIs down)
    2. Fetch earnings calendar for date range
    3. Spawn N TickerAnalysisAgents in parallel
    4. Filter by VRP >= 3.0 (discovery threshold)
    5. Spawn ExplanationAgents for top candidates
    6. Spawn AnomalyDetectionAgents for top candidates
    7. Apply cross-ticker intelligence (sector correlation, portfolio risk)
    8. Rank by composite score
    9. Return top 10 with explanations

    Example:
        orchestrator = WhisperOrchestrator()
        result = await orchestrator.orchestrate(
            start_date="2026-02-05",
            end_date="2026-02-09"
        )
    """

    # Discovery threshold (lower than position sizing threshold)
    VRP_DISCOVERY_THRESHOLD = 3.0

    # Limit for performance
    MAX_TICKERS_TO_ANALYZE = 30

    # Portfolio risk thresholds (from TRR position limits in CLAUDE.md)
    # Normal TRR tickers: $50K max notional per position
    # HIGH TRR tickers: $25K max notional (enforced by TRR limits, not here)
    DEFAULT_POSITION_NOTIONAL = 50000

    # Maximum recommended portfolio exposure for /whisper candidates
    # Conservative limit to avoid overconcentration in single week
    MAX_PORTFOLIO_NOTIONAL = 150000

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize WhisperOrchestrator."""
        super().__init__(config)
        self.timeout = self.config.get('timeout', 90)

    async def orchestrate(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Execute whisper orchestration workflow.

        Args:
            start_date: Start date for earnings (default: today)
            end_date: End date for earnings (default: today + 4 days)
            limit: Number of results to return (default: 10)

        Returns:
            Orchestration result with ranked opportunities

        Example:
            result = await orchestrator.orchestrate(
                start_date="2026-02-05",
                end_date="2026-02-09",
                limit=10
            )
        """
        # Step 1: Health check
        print("[1/7] Running health check...")
        health_agent = HealthCheckAgent()
        health_result = health_agent.check_health()

        if health_result['status'] == 'unhealthy':
            return {
                'success': False,
                'error': 'System unhealthy - cannot proceed',
                'health': health_result
            }

        # Step 2: Fetch earnings calendar
        print("[2/7] Fetching earnings calendar...")
        earnings = self.fetch_earnings_calendar(days_ahead=5)

        if not earnings:
            return {
                'success': True,
                'results': [],
                'cross_ticker_warnings': [],
                'message': 'No earnings found for date range'
            }

        print(f"  Found {len(earnings)} earnings")

        # Limit for performance
        if len(earnings) > self.MAX_TICKERS_TO_ANALYZE:
            print(f"  Limiting to first {self.MAX_TICKERS_TO_ANALYZE} tickers")
            earnings = earnings[:self.MAX_TICKERS_TO_ANALYZE]

        # Step 3: Parallel ticker analysis
        print(f"[3/7] Analyzing {len(earnings)} tickers in parallel...")
        analysis_results = await self._parallel_ticker_analysis(earnings)

        # Filter successful results
        successful = self.filter_successful_results(analysis_results)
        print(f"  {len(successful)} successful analyses")

        # Step 4: Filter by VRP threshold
        print(f"[4/7] Filtering by VRP >= {self.VRP_DISCOVERY_THRESHOLD}x...")
        filtered = self._filter_by_vrp(successful, self.VRP_DISCOVERY_THRESHOLD)
        print(f"  {len(filtered)} tickers meet threshold")

        if not filtered:
            return {
                'success': True,
                'results': [],
                'cross_ticker_warnings': [],
                'message': f'No tickers with VRP >= {self.VRP_DISCOVERY_THRESHOLD}x'
            }

        # Step 5: Sort and take top candidates
        print("[5/7] Ranking by composite score...")
        filtered.sort(key=lambda x: x.get('score', 0), reverse=True)
        top_candidates = filtered[:limit * 2]  # Get 2x limit for explanation phase

        # Step 6: Add explanations to top candidates
        print(f"[6/7] Adding explanations to top {len(top_candidates)} candidates...")
        enriched = await self._add_explanations(top_candidates)

        # Step 7: Add anomaly detection to top candidates
        print(f"[7/7] Running anomaly detection on top {len(enriched)} candidates...")
        final_results = await self._add_anomaly_detection(enriched)

        # Apply cross-ticker intelligence
        warnings = self._detect_cross_ticker_risks(final_results)

        # Final sort and limit
        final_results.sort(key=lambda x: x.get('score', 0), reverse=True)
        final_results = final_results[:limit]

        return {
            'success': True,
            'results': final_results,
            'cross_ticker_warnings': warnings,
            'summary': self.get_orchestration_summary()
        }

    async def _parallel_ticker_analysis(
        self,
        earnings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Analyze all tickers in parallel."""
        # Create ticker analysis agents
        analysis_agent = TickerAnalysisAgent()

        # Build analysis tasks
        tasks = []
        for earning in earnings:
            ticker = earning.get('ticker')
            earnings_date = earning.get('date')

            if ticker and earnings_date:
                # Wrap synchronous analyze() in async task
                task = asyncio.create_task(
                    asyncio.to_thread(
                        analysis_agent.analyze,
                        ticker,
                        earnings_date,
                        generate_strategies=False  # Skip strategies in whisper
                    )
                )
                tasks.append(task)

        # Wait for all with timeout
        if tasks:
            results = await self.gather_with_timeout(tasks, timeout=self.timeout)
            return results

        return []

    def _filter_by_vrp(
        self,
        results: List[Dict[str, Any]],
        threshold: float
    ) -> List[Dict[str, Any]]:
        """Filter results by VRP threshold."""
        return [
            r for r in results
            if r.get('vrp_ratio', 0) >= threshold
        ]

    async def _add_explanations(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Add explanations to top results."""
        explanation_agent = ExplanationAgent()

        enriched = []
        for result in results:
            # Add explanation
            explanation = explanation_agent.explain(
                ticker=result['ticker'],
                vrp_ratio=result.get('vrp_ratio', 0),
                liquidity_tier=result.get('liquidity_tier', 'N/A')
            )

            # Merge into result
            result['explanation'] = explanation.get('explanation', '')
            result['key_factors'] = explanation.get('key_factors', [])
            result['historical_context'] = explanation.get('historical_context', '')

            enriched.append(result)

        return enriched

    async def _add_anomaly_detection(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Add anomaly detection to results."""
        anomaly_agent = AnomalyDetectionAgent()
        ticker_agent = TickerAnalysisAgent()
        cache = Cache4_0()

        enriched = []
        for result in results:
            ticker = result['ticker']
            earnings_date = result.get('earnings_date', '')

            # Get actual cache age from sentiment cache
            cache_age_hours = cache.get_cache_age_hours(ticker, earnings_date)
            if cache_age_hours is None:
                cache_age_hours = 0.0  # Fresh data if not cached

            # Get actual historical quarters count
            historical_moves = ticker_agent.get_historical_moves(ticker, limit=50)
            historical_quarters = len(historical_moves) if historical_moves else 0

            # Run anomaly detection with actual values
            anomaly_result = anomaly_agent.detect(
                ticker=ticker,
                vrp_ratio=result.get('vrp_ratio', 0),
                recommendation=result.get('recommendation', 'SKIP'),
                liquidity_tier=result.get('liquidity_tier', 'REJECT'),
                earnings_date=earnings_date,
                cache_age_hours=cache_age_hours,
                historical_quarters=historical_quarters
            )

            # Merge into result
            result['anomalies'] = anomaly_result.get('anomalies', [])
            result['anomaly_recommendation'] = anomaly_result.get('recommendation', 'TRADE')

            enriched.append(result)

        return enriched

    def _detect_cross_ticker_risks(
        self,
        results: List[Dict[str, Any]]
    ) -> List[str]:
        """Detect cross-ticker correlation and portfolio risks."""
        warnings = []

        # Check 1: Sector correlation (simplified - group by first letter for demo)
        # TODO: Use proper sector data from 2.0 company profiles
        sector_groups = {}
        for result in results:
            ticker = result.get('ticker', '')
            # Simplified sector grouping (first letter as proxy)
            sector = ticker[0] if ticker else 'UNKNOWN'
            if sector not in sector_groups:
                sector_groups[sector] = []
            sector_groups[sector].append(ticker)

        # Warn if 3+ tickers in same sector
        for sector, tickers in sector_groups.items():
            if len(tickers) >= 3:
                warnings.append(
                    f"⚠️  Sector concentration: {len(tickers)} tickers starting with '{sector}' "
                    f"({', '.join(tickers[:3])}...)"
                )

        # Check 2: Portfolio risk using configured thresholds
        # Note: Individual TRR limits are enforced by TickerAnalysisAgent
        total_notional = len(results) * self.DEFAULT_POSITION_NOTIONAL
        max_recommended = self.MAX_PORTFOLIO_NOTIONAL

        if total_notional > max_recommended:
            warnings.append(
                f"⚠️  Portfolio risk: {len(results)} positions = ${total_notional:,} notional "
                f"(exceeds ${max_recommended:,} recommended max)"
            )

        return warnings

    def format_results(self, result: Dict[str, Any]) -> str:
        """Format orchestration results as ASCII table."""
        if not result.get('success'):
            return f"Error: {result.get('error', 'Unknown error')}"

        results = result.get('results', [])
        warnings = result.get('cross_ticker_warnings', [])

        output = format_whisper_results(results)

        if warnings:
            output += "\n" + format_cross_ticker_warnings(warnings)

        return output
