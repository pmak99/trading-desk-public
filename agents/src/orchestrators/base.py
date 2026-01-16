"""Base orchestrator class for agent coordination.

Provides common patterns for spawning agents, managing timeouts,
and aggregating results.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from ..integration.mcp_client import MCPTaskClient
from ..integration.container_2_0 import Container2_0
from ..integration.cache_4_0 import Cache4_0
from ..utils.timeout import gather_with_timeout, run_with_timeout


class BaseOrchestrator(ABC):
    """
    Base class for all orchestrators.

    Orchestrators coordinate multiple worker agents to accomplish
    complex workflows like /whisper, /analyze, and /maintenance.

    Subclasses must implement:
    - orchestrate(*args, **kwargs): Main workflow logic

    Current Pattern (Direct Agent Calls):
        Uses agent instances directly with asyncio.to_thread() for
        CPU-bound synchronous operations, or await for async methods.

        class WhisperOrchestrator(BaseOrchestrator):
            async def orchestrate(self, date_range):
                agent = TickerAnalysisAgent()
                tasks = [
                    asyncio.create_task(
                        asyncio.to_thread(agent.analyze, ticker, date)
                    )
                    for ticker, date in tickers
                ]
                results = await self.gather_with_timeout(tasks, 90)
                return results

    Future Pattern (MCP Task Tool - Phase 2):
        Will spawn isolated Claude instances via MCP protocol for
        true parallel execution with separate context windows.
        The spawn_agent() method is reserved for this future use.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize orchestrator.

        Args:
            config: Optional configuration dict
        """
        self.config = config or {}
        self.timeout = self.config.get('timeout', 120)

        # Initialize integration components
        self.mcp_client = MCPTaskClient()
        self.container_2_0 = Container2_0()
        self.cache_4_0 = Cache4_0()

        # Track spawned agents
        self.spawned_agents: List[Dict[str, Any]] = []

    @abstractmethod
    async def orchestrate(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Main orchestration logic.

        Subclasses must implement this method with their specific workflow.

        Returns:
            Orchestration result dict
        """
        pass

    async def spawn_agent(
        self,
        agent_type: str,
        prompt: str,
        timeout: Optional[int] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Spawn worker agent via Claude Task tool (Phase 2 - Not Yet Implemented).

        NOTE: This method is reserved for Phase 2 MCP Task tool integration.
        Current orchestrators should use direct agent calls instead:

            agent = TickerAnalysisAgent()
            result = await asyncio.to_thread(agent.analyze, ticker, date)

        Args:
            agent_type: Type of agent (e.g., "TickerAnalysisAgent")
            prompt: Full prompt for agent
            timeout: Max execution time (default: from agent config)
            model: Claude model to use (default: from agent config)

        Returns:
            Parsed JSON response dict (currently returns error response)
        """
        # Get defaults from agent config
        if timeout is None:
            timeout = self.mcp_client.get_timeout(agent_type)
        if model is None:
            model = self.mcp_client.get_model(agent_type)

        try:
            # Spawn agent via MCP Task tool
            raw_response = await self.mcp_client.spawn_agent(
                agent_type=agent_type,
                prompt=prompt,
                timeout=timeout,
                model=model
            )

            # Parse JSON response
            result = self.mcp_client.parse_json_response(raw_response)

            # Track spawned agent
            self.spawned_agents.append({
                'agent_type': agent_type,
                'success': True,
                'error': None
            })

            return result

        except Exception as e:
            # Track failed agent
            self.spawned_agents.append({
                'agent_type': agent_type,
                'success': False,
                'error': str(e)
            })

            # Return error response
            return {
                'error': f"{agent_type} failed: {str(e)}",
                'success': False
            }

    async def gather_with_timeout(
        self,
        tasks: List[asyncio.Task],
        timeout: int
    ) -> List[Dict[str, Any]]:
        """
        Gather all tasks with global timeout.

        Args:
            tasks: List of asyncio tasks
            timeout: Global timeout in seconds

        Returns:
            List of results or exceptions

        Example:
            agent = TickerAnalysisAgent()
            tasks = [
                asyncio.create_task(asyncio.to_thread(agent.analyze, t, d))
                for t, d in tickers
            ]
            results = await self.gather_with_timeout(tasks, 90)
        """
        return await gather_with_timeout(
            tasks=tasks,
            timeout=timeout,
            return_exceptions=True
        )

    async def run_with_timeout(
        self,
        coro,
        timeout: int,
        default: Optional[Any] = None
    ) -> Any:
        """
        Run single coroutine with timeout.

        Args:
            coro: Coroutine to execute
            timeout: Timeout in seconds
            default: Default value on timeout

        Returns:
            Coroutine result or default

        Example:
            result = await self.run_with_timeout(
                asyncio.to_thread(agent.analyze, ticker, date),
                timeout=20,
                default={'status': 'timeout'}
            )
        """
        return await run_with_timeout(
            coro=coro,
            timeout=timeout,
            default=default
        )

    def filter_successful_results(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Filter out failed agent results.

        Args:
            results: List of agent results

        Returns:
            List of successful results only

        Example:
            successful = self.filter_successful_results(results)
        """
        return [
            r for r in results
            if isinstance(r, dict) and r.get('error') is None
        ]

    def get_orchestration_summary(self) -> Dict[str, Any]:
        """
        Get summary of orchestration execution.

        Returns:
            Summary dict with agent counts and success rate

        Example:
            summary = self.get_orchestration_summary()
            print(f"Success rate: {summary['success_rate']}%")
        """
        total = len(self.spawned_agents)
        successful = sum(1 for a in self.spawned_agents if a['success'])
        failed = total - successful

        success_rate = (successful / total * 100) if total > 0 else 0.0

        return {
            'total_agents': total,
            'successful': successful,
            'failed': failed,
            'success_rate': round(success_rate, 1)
        }

    def fetch_earnings_calendar(
        self,
        days_ahead: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Fetch earnings calendar from 2.0.

        Args:
            days_ahead: Number of days to look ahead (default: 5)

        Returns:
            List of earnings dicts with 'ticker' and 'date' keys

        Example:
            earnings = self.fetch_earnings_calendar(days_ahead=7)
            for e in earnings:
                print(f"{e['ticker']} reports on {e['date']}")
        """
        try:
            # Get upcoming earnings using days_ahead parameter
            # Note: 2.0's API returns Result[(ticker, date), error]
            result = self.container_2_0.get_upcoming_earnings(
                days_ahead=days_ahead
            )

            # Check if result is successful
            # Note: 2.0's Result type uses `is_err` property, not `is_error()` method
            if hasattr(result, 'is_err') and result.is_err:
                return []

            # Extract value from Result
            if hasattr(result, 'value'):
                earnings_tuples = result.value
            else:
                earnings_tuples = result

            # Convert (ticker, date) tuples to dicts
            earnings = []
            for ticker, date_obj in earnings_tuples:
                # Convert date to string if it's a date object
                if hasattr(date_obj, 'strftime'):
                    date_str = date_obj.strftime('%Y-%m-%d')
                else:
                    date_str = str(date_obj)

                earnings.append({
                    'ticker': ticker,
                    'date': date_str
                })

            return earnings

        except Exception as e:
            # Log and return empty list on failure - calendar fetch is non-critical
            logger.warning(f"Failed to fetch earnings calendar: {e}")
            return []
