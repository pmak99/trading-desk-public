"""PrimeOrchestrator - Pre-cache sentiment for upcoming earnings.

Coordinates parallel sentiment fetching to pre-populate cache before
/whisper runs, enabling instant results and predictable API costs.

Target: 30 tickers in ~10 seconds (vs 90 seconds sequential).
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from .base import BaseOrchestrator
from ..agents.sentiment_fetch import SentimentFetchAgent
from ..agents.health import HealthCheckAgent

logger = logging.getLogger(__name__)


class PrimeOrchestrator(BaseOrchestrator):
    """
    Orchestrator for /prime - Pre-cache sentiment data.

    Workflow:
    1. Run health check (verify Perplexity budget available)
    2. Fetch earnings calendar for date range
    3. Filter tickers already cached (< 3 hours old)
    4. Check budget allows fetching remaining tickers
    5. Spawn N SentimentFetchAgents in parallel (budget-limited)
    6. Cache all results
    7. Return summary (tickers cached, API calls made, budget remaining)

    Example:
        orchestrator = PrimeOrchestrator()
        result = await orchestrator.orchestrate(
            start_date="2026-02-05",
            end_date="2026-02-09"
        )
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize PrimeOrchestrator."""
        super().__init__(config)
        self.timeout = self.config.get('timeout', 60)

    async def orchestrate(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days_ahead: int = 5
    ) -> Dict[str, Any]:
        """
        Execute prime orchestration workflow.

        Args:
            start_date: Start date for earnings (default: today)
            end_date: End date for earnings (default: today + days_ahead)
            days_ahead: Days to look ahead (default: 5)

        Returns:
            Orchestration result with caching summary

        Example:
            result = await orchestrator.orchestrate(
                start_date="2026-02-05",
                end_date="2026-02-09"
            )
        """
        # Step 1: Health check
        logger.info("[1/6] Running health check...")
        health_agent = HealthCheckAgent()
        health_result = health_agent.check_health()

        if health_result['status'] == 'unhealthy':
            logger.error("System unhealthy - cannot proceed")
            return {
                'success': False,
                'error': 'System unhealthy - cannot proceed',
                'health': health_result
            }

        # Check Perplexity budget specifically
        budget_status = health_result.get('budget', {})
        daily_limit = budget_status.get('daily_limit', 40)
        daily_calls = budget_status.get('daily_calls', 0)
        daily_remaining = daily_limit - daily_calls

        if daily_remaining <= 0:
            logger.warning("Perplexity API budget exhausted")
            return {
                'success': False,
                'error': 'Perplexity API budget exhausted (40 calls/day limit)',
                'budget': budget_status
            }

        logger.info(f"Budget available: {daily_remaining} calls remaining (used {daily_calls}/{daily_limit})")

        # Step 2: Fetch earnings calendar
        logger.info("[2/6] Fetching earnings calendar...")
        earnings = self._fetch_earnings_calendar(start_date, end_date, days_ahead)

        if not earnings:
            logger.info("No earnings found for date range")
            return {
                'success': True,
                'tickers_cached': 0,
                'api_calls_made': 0,
                'message': 'No earnings found for date range'
            }

        logger.info(f"Found {len(earnings)} earnings")

        # Step 3: Filter already cached
        logger.info("[3/6] Checking cache status...")
        sentiment_agent = SentimentFetchAgent()
        to_fetch = []
        already_cached = []

        for earning in earnings:
            ticker = earning.get('ticker')
            earnings_date = earning.get('date')

            if ticker and earnings_date:
                cached = sentiment_agent.get_cached_sentiment(ticker, earnings_date)
                if cached:
                    already_cached.append(ticker)
                else:
                    to_fetch.append((ticker, earnings_date))

        logger.info(f"Already cached: {len(already_cached)} tickers")
        logger.info(f"Need to fetch: {len(to_fetch)} tickers")

        if not to_fetch:
            logger.info(f"All {len(already_cached)} tickers already cached")
            return {
                'success': True,
                'tickers_cached': len(already_cached),
                'api_calls_made': 0,
                'already_cached': already_cached,
                'message': f'All {len(already_cached)} tickers already cached'
            }

        # Step 4: Budget check
        logger.info("[4/6] Verifying budget for API calls...")
        if len(to_fetch) > daily_remaining:
            logger.warning(f"Need {len(to_fetch)} calls but only {daily_remaining} remaining")
            logger.warning(f"Limiting to first {daily_remaining} tickers")
            to_fetch = to_fetch[:daily_remaining]

        # Step 5: Parallel sentiment fetching
        logger.info(f"[5/6] Fetching sentiment for {len(to_fetch)} tickers in parallel...")
        results = await self._parallel_sentiment_fetch(to_fetch)

        # Count successes
        successful = self.filter_successful_results(results)
        failed = len(results) - len(successful)

        logger.info(f"Successful: {len(successful)}")
        if failed > 0:
            logger.warning(f"Failed: {failed}")

        # Step 6: Summary
        logger.info("[6/6] Caching complete")

        return {
            'success': True,
            'tickers_cached': len(already_cached) + len(successful),
            'api_calls_made': len(to_fetch),
            'already_cached_count': len(already_cached),
            'newly_cached_count': len(successful),
            'failed_count': failed,
            'budget_remaining': daily_remaining - len(to_fetch),
            'summary': self.get_orchestration_summary()
        }

    def _fetch_earnings_calendar(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        days_ahead: int
    ) -> List[Dict[str, Any]]:
        """Fetch earnings calendar from 2.0."""
        # Default date range: today + days_ahead
        if start_date is None:
            start_date = datetime.now().strftime('%Y-%m-%d')
        if end_date is None:
            end_dt = datetime.now() + timedelta(days=days_ahead)
            end_date = end_dt.strftime('%Y-%m-%d')

        try:
            earnings = self.container_2_0.get_upcoming_earnings(
                start_date=start_date,
                end_date=end_date
            )
            return earnings
        except Exception as e:
            logger.error(f"Error fetching calendar: {e}")
            return []

    async def _parallel_sentiment_fetch(
        self,
        tickers_to_fetch: List[tuple]
    ) -> List[Dict[str, Any]]:
        """Fetch sentiment for all tickers in parallel."""
        # Create sentiment fetch agents
        sentiment_agent = SentimentFetchAgent()

        # Build fetch tasks
        tasks = []
        for ticker, earnings_date in tickers_to_fetch:
            # Wrap synchronous fetch_sentiment() in async task
            task = asyncio.create_task(
                asyncio.to_thread(
                    sentiment_agent.fetch_sentiment,
                    ticker,
                    earnings_date
                )
            )
            tasks.append(task)

        # Wait for all with timeout
        if tasks:
            try:
                results = await self.gather_with_timeout(tasks, timeout=self.timeout)
                return results
            except Exception as e:
                logger.error(f"Error during parallel fetch: {e}")
                # Return empty list on catastrophic failure
                return []

        return []

    def format_results(self, result: Dict[str, Any]) -> str:
        """Format orchestration results as summary."""
        if not result.get('success'):
            return f"Error: {result.get('error', 'Unknown error')}"

        tickers_cached = result.get('tickers_cached', 0)
        api_calls = result.get('api_calls_made', 0)
        budget_remaining = result.get('budget_remaining', 0)
        already_cached = result.get('already_cached_count', 0)
        newly_cached = result.get('newly_cached_count', 0)
        failed = result.get('failed_count', 0)

        lines = [
            "=" * 60,
            "SENTIMENT CACHING COMPLETE",
            "=" * 60,
            f"Total tickers cached: {tickers_cached}",
            f"  - Already cached: {already_cached}",
            f"  - Newly cached: {newly_cached}",
            f"  - Failed: {failed}",
            "",
            f"API calls made: {api_calls}",
            f"Budget remaining: {budget_remaining} calls/day",
            "=" * 60
        ]

        return "\n".join(lines)
