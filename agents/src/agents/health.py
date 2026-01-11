"""HealthCheckAgent - System health monitoring.

This agent verifies system health before batch operations:
- API connectivity (Tradier, Alpha Vantage, Perplexity)
- Database health
- Budget status
- Data freshness
"""

from typing import Dict, Any

from ..integration.container_2_0 import Container2_0
from ..integration.cache_4_0 import Cache4_0
from ..utils.schemas import HealthCheckResponse, APIHealthStatus, DatabaseHealthStatus, BudgetStatus
from .base import BaseAgent


class HealthCheckAgent:
    """
    Worker agent for system health checks.

    Verifies:
    1. API connectivity (Tradier, Alpha Vantage, Perplexity)
    2. Database health (connection, size, record counts)
    3. Budget status (daily/monthly limits)
    4. Data freshness (cache statistics)

    Example:
        agent = HealthCheckAgent()
        result = agent.check_health()
    """

    def __init__(self):
        """Initialize agent with containers."""
        self.container = Container2_0()
        self.cache = Cache4_0()

    def check_health(self) -> Dict[str, Any]:
        """
        Execute full health check.

        Returns:
            Health check result dict conforming to HealthCheckResponse schema

        Example:
            result = agent.check_health()
            # Returns:
            # {
            #     "status": "healthy",
            #     "apis": {...},
            #     "database": {...},
            #     "budget": {...}
            # }
        """
        try:
            # Check APIs
            apis = self._check_apis()

            # Check database
            database = self._check_database()

            # Check budget
            budget = self._check_budget()

            # Determine overall status
            overall_status = self._determine_overall_status(apis, database)

            # Build response
            response_data = {
                'status': overall_status,
                'apis': apis,
                'database': database,
                'budget': budget
            }

            # Validate with schema
            validated = HealthCheckResponse(**response_data)
            return validated.dict()

        except Exception as e:
            # Return unhealthy status on error
            return {
                'status': 'unhealthy',
                'apis': {},
                'database': {'status': 'error', 'error': str(e)},
                'budget': {'daily_calls': 0, 'daily_limit': 40, 'monthly_cost': 0.0, 'monthly_budget': 5.0}
            }

    def _check_apis(self) -> Dict[str, Dict[str, Any]]:
        """Check all API connectivity."""
        apis = {}

        # Check Tradier
        apis['tradier'] = self._check_tradier()

        # Check Alpha Vantage
        apis['alphavantage'] = self._check_alphavantage()

        # Check Perplexity (via budget status)
        apis['perplexity'] = self._check_perplexity()

        return apis

    def _check_tradier(self) -> Dict[str, Any]:
        """Check Tradier API connectivity."""
        try:
            result = self.container.check_tradier_health()
            return {
                'status': result['status'],
                'latency_ms': result.get('latency_ms'),
                'error': result.get('error')
            }
        except Exception as e:
            return {
                'status': 'error',
                'latency_ms': None,
                'error': str(e)
            }

    def _check_alphavantage(self) -> Dict[str, Any]:
        """Check Alpha Vantage API connectivity."""
        try:
            result = self.container.check_alphavantage_health()
            return {
                'status': result['status'],
                'latency_ms': result.get('latency_ms'),
                'error': result.get('error')
            }
        except Exception as e:
            return {
                'status': 'error',
                'latency_ms': None,
                'error': str(e)
            }

    def _check_perplexity(self) -> Dict[str, Any]:
        """Check Perplexity API budget status."""
        try:
            result = self.cache.check_perplexity_health()
            return {
                'status': result['status'],
                'remaining_calls': result.get('remaining_calls'),
                'error': result.get('error')
            }
        except Exception as e:
            return {
                'status': 'error',
                'remaining_calls': None,
                'error': str(e)
            }

    def _check_database(self) -> Dict[str, Any]:
        """Check database health."""
        try:
            result = self.container.check_database_health()
            return {
                'status': result['status'],
                'size_mb': result.get('size_mb'),
                'historical_moves': result.get('historical_moves'),
                'earnings_calendar': result.get('earnings_calendar'),
                'error': result.get('error')
            }
        except Exception as e:
            return {
                'status': 'error',
                'size_mb': None,
                'historical_moves': None,
                'earnings_calendar': None,
                'error': str(e)
            }

    def _check_budget(self) -> Dict[str, Any]:
        """Check budget status."""
        try:
            return self.cache.get_budget_status()
        except Exception as e:
            return {
                'daily_calls': 0,
                'daily_limit': 40,
                'monthly_cost': 0.0,
                'monthly_budget': 5.0
            }

    def _determine_overall_status(
        self,
        apis: Dict[str, Dict[str, Any]],
        database: Dict[str, Any]
    ) -> str:
        """Determine overall system health status."""
        # Check for critical failures
        if database.get('status') == 'error':
            return 'unhealthy'

        # Check API statuses
        api_errors = sum(
            1 for api_status in apis.values()
            if api_status.get('status') == 'error'
        )

        if api_errors >= 2:  # Multiple API failures
            return 'unhealthy'
        elif api_errors == 1:  # Single API failure
            return 'degraded'

        # All systems operational
        return 'healthy'

    def get_cache_statistics(self) -> Dict[str, Any]:
        """Get cache statistics."""
        try:
            return self.cache.get_cache_statistics()
        except Exception as e:
            return {
                'error': str(e)
            }
