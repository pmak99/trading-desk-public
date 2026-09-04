"""Health check service for monitoring system components."""

import asyncio
import time
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ServiceHealth:
    """Health status for a single service.

    Attributes:
        name: Service identifier
        healthy: Whether service is operational
        latency_ms: Response time in milliseconds
        error: Error message if unhealthy
        checked_at: Timestamp of health check
    """

    name: str
    healthy: bool
    latency_ms: float
    error: Optional[str] = None
    checked_at: Optional[datetime] = None

    def __post_init__(self):
        if self.checked_at is None:
            self.checked_at = datetime.now()

    def __str__(self):
        status = "✅ UP" if self.healthy else "❌ DOWN"
        error_msg = f" - {self.error}" if self.error else ""
        return f"{self.name:20} {status:8} {self.latency_ms:.1f}ms{error_msg}"


class HealthCheckService:
    """Service for checking health of all system components.

    Performs asynchronous health checks on:
    - Tradier API (external market data)
    - Database (SQLite)
    - Cache (in-memory)

    Args:
        container: Dependency injection container
    """

    def __init__(self, container):
        self.container = container
        self.timeout_seconds = 5

    async def check_all(self) -> Dict[str, ServiceHealth]:
        """Check all services in parallel.

        Returns:
            Dictionary mapping service name to health status
        """
        checks = {
            "tradier": self._check_tradier(),
            "database": self._check_database(),
            "cache": self._check_cache(),
        }

        results = await asyncio.gather(*checks.values(), return_exceptions=False)
        return dict(zip(checks.keys(), results))

    async def _check_tradier(self) -> ServiceHealth:
        """Check Tradier API health by fetching stock price.

        Returns:
            ServiceHealth for Tradier API
        """
        start = time.perf_counter()
        try:
            # Run sync API call in executor to avoid blocking
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None, self.container.tradier.get_stock_price, "AAPL"
                ),
                timeout=self.timeout_seconds
            )
            if result.is_err:
                raise result.error
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(name="tradier", healthy=True, latency_ms=latency)
        except asyncio.TimeoutError:
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(
                name="tradier", healthy=False, latency_ms=latency,
                error=f"Timeout after {self.timeout_seconds}s"
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(
                name="tradier", healthy=False, latency_ms=latency, error=str(e)
            )

    async def _check_database(self) -> ServiceHealth:
        """Check database health by opening connection.

        Returns:
            ServiceHealth for database
        """
        start = time.perf_counter()
        try:
            import sqlite3

            dbpath = str(self.container.config.database.path)
            loop = asyncio.get_event_loop()

            def check_db():
                with sqlite3.connect(dbpath, timeout=self.timeout_seconds):
                    pass

            await asyncio.wait_for(
                loop.run_in_executor(None, check_db),
                timeout=self.timeout_seconds
            )
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(name="database", healthy=True, latency_ms=latency)
        except asyncio.TimeoutError:
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(
                name="database", healthy=False, latency_ms=latency,
                error=f"Timeout after {self.timeout_seconds}s"
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(
                name="database", healthy=False, latency_ms=latency, error=str(e)
            )

    async def _check_cache(self) -> ServiceHealth:
        """Check cache health by set/get operations.

        Returns:
            ServiceHealth for cache
        """
        start = time.perf_counter()
        try:
            loop = asyncio.get_event_loop()

            def check_cache():
                self.container.cache.set("_health", {"test": True})
                result = self.container.cache.get("_health")
                if result is None:
                    raise ValueError("Cache returned None")

            await asyncio.wait_for(
                loop.run_in_executor(None, check_cache),
                timeout=self.timeout_seconds
            )
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(name="cache", healthy=True, latency_ms=latency)
        except asyncio.TimeoutError:
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(
                name="cache", healthy=False, latency_ms=latency,
                error=f"Timeout after {self.timeout_seconds}s"
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return ServiceHealth(
                name="cache", healthy=False, latency_ms=latency, error=str(e)
            )
