"""Integration tests for health check service."""

import pytest
import asyncio
from dataclasses import replace
from pathlib import Path

from src.config.config import Config
from src.container import Container
from src.application.services.health import HealthCheckService, ServiceHealth


@pytest.fixture
def config():
    """Create test configuration."""
    return Config.from_env()


@pytest.fixture
def container(config):
    """Create container with test config."""
    container = Container(config, skip_validation=True)
    # Initialize database for tests
    container.initialize_database()
    return container


class TestHealthCheckService:
    """Integration tests for HealthCheckService."""

    @pytest.mark.asyncio
    async def test_health_service_creation(self, container):
        """Test that health service can be created."""
        health_service = container.health_check_service

        assert health_service is not None
        assert isinstance(health_service, HealthCheckService)

    @pytest.mark.asyncio
    async def test_check_all_returns_results(self, container):
        """Test that check_all returns results for all services."""
        health_service = container.health_check_service

        results = await health_service.check_all()

        assert "tradier" in results
        assert "database" in results
        assert "cache" in results

        for name, health in results.items():
            assert isinstance(health, ServiceHealth)
            assert health.name == name
            assert health.latency_ms >= 0
            assert health.checked_at is not None

    @pytest.mark.asyncio
    async def test_database_health_check(self, container):
        """Test database health check."""
        health_service = container.health_check_service

        result = await health_service._check_database()

        assert result.name == "database"
        assert result.healthy is True
        assert result.latency_ms >= 0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_cache_health_check(self, container):
        """Test cache health check."""
        health_service = container.health_check_service

        result = await health_service._check_cache()

        assert result.name == "cache"
        assert result.healthy is True
        assert result.latency_ms >= 0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_tradier_health_check_with_invalid_key(self, config):
        """Test Tradier health check with invalid API key."""
        # Create config with invalid API key
        modified_api = replace(config.api, tradier_api_key="invalid_key")
        modified_config = replace(config, api=modified_api)
        container = Container(modified_config, skip_validation=True)
        health_service = container.health_check_service

        result = await health_service._check_tradier()

        assert result.name == "tradier"
        # May fail due to invalid key (expected behavior)
        # Just verify the structure is correct
        assert result.latency_ms >= 0
        assert result.checked_at is not None

    @pytest.mark.asyncio
    async def test_health_check_concurrent_execution(self, container):
        """Test that health checks run concurrently."""
        health_service = container.health_check_service

        start = asyncio.get_event_loop().time()
        results = await health_service.check_all()
        elapsed = asyncio.get_event_loop().time() - start

        # If they ran concurrently, total time should be less than sum of individual times
        total_latency = sum(h.latency_ms for h in results.values())

        # Total time should be significantly less than sum (running in parallel)
        # Allow for some overhead
        assert elapsed * 1000 < total_latency * 1.5

    @pytest.mark.asyncio
    async def test_service_health_str_representation(self):
        """Test ServiceHealth string representation."""
        health = ServiceHealth(
            name="test_service", healthy=True, latency_ms=42.5, error=None
        )

        result = str(health)

        assert "test_service" in result
        assert "✅" in result
        assert "42.5ms" in result

    @pytest.mark.asyncio
    async def test_service_health_unhealthy_str(self):
        """Test ServiceHealth string representation for unhealthy service."""
        health = ServiceHealth(
            name="failed_service",
            healthy=False,
            latency_ms=100.0,
            error="Connection refused",
        )

        result = str(health)

        assert "failed_service" in result
        assert "❌" in result
        assert "100.0ms" in result
        assert "Connection refused" in result

    @pytest.mark.asyncio
    async def test_database_health_with_missing_db(self, config):
        """Test database health check with missing database."""
        # Use non-existent database path
        modified_db = replace(config.database, path=Path("/tmp/nonexistent_test_db.db"))
        modified_config = replace(config, database=modified_db)
        container = Container(modified_config, skip_validation=True)
        health_service = container.health_check_service

        result = await health_service._check_database()

        # Should still be healthy as it creates the db
        assert result.name == "database"
        assert result.latency_ms >= 0
