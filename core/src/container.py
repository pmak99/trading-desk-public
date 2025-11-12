"""
Dependency Injection Container for IV Crush 2.0.

Provides lazy-loaded singletons for all services, calculators, and infrastructure.
Makes the entire system testable by allowing easy mocking.
"""

import logging
from pathlib import Path
from typing import Optional
from src.config.config import Config
from src.infrastructure.api.tradier import TradierAPI
from src.infrastructure.cache.memory_cache import MemoryCache, CachedOptionsDataProvider
from src.infrastructure.database.repositories.earnings_repository import (
    EarningsRepository,
)
from src.application.metrics.implied_move import ImpliedMoveCalculator
from src.application.metrics.vrp import VRPCalculator

logger = logging.getLogger(__name__)


class Container:
    """
    Dependency injection container.

    All dependencies are lazy-loaded (created on first access).
    This pattern ensures:
    - Single responsibility
    - Easy testing (mock individual components)
    - Clear dependency graph
    - No circular dependencies
    """

    def __init__(self, config: Config):
        self.config = config
        self._tradier: Optional[TradierAPI] = None
        self._cache: Optional[MemoryCache] = None
        self._cached_options_provider: Optional[CachedOptionsDataProvider] = None
        self._earnings_repo: Optional[EarningsRepository] = None
        self._implied_move_calc: Optional[ImpliedMoveCalculator] = None
        self._vrp_calc: Optional[VRPCalculator] = None

    # ========================================================================
    # Infrastructure Layer
    # ========================================================================

    @property
    def tradier(self) -> TradierAPI:
        """Get Tradier API client."""
        if self._tradier is None:
            self._tradier = TradierAPI(
                api_key=self.config.api.tradier_api_key,
                base_url=self.config.api.tradier_base_url,
            )
            logger.debug("Created TradierAPI client")
        return self._tradier

    @property
    def cache(self) -> MemoryCache:
        """Get memory cache."""
        if self._cache is None:
            self._cache = MemoryCache(
                ttl_seconds=self.config.cache.l1_ttl, max_size=1000
            )
            logger.debug("Created MemoryCache")
        return self._cache

    @property
    def cached_options_provider(self) -> CachedOptionsDataProvider:
        """Get cached options data provider (wraps Tradier with cache)."""
        if self._cached_options_provider is None:
            self._cached_options_provider = CachedOptionsDataProvider(
                provider=self.tradier, cache=self.cache
            )
            logger.debug("Created CachedOptionsDataProvider")
        return self._cached_options_provider

    @property
    def earnings_repository(self) -> EarningsRepository:
        """Get earnings repository."""
        if self._earnings_repo is None:
            self._earnings_repo = EarningsRepository(
                db_path=str(self.config.database.path)
            )
            logger.debug("Created EarningsRepository")
        return self._earnings_repo

    # ========================================================================
    # Application Layer - Calculators
    # ========================================================================

    @property
    def implied_move_calculator(self) -> ImpliedMoveCalculator:
        """Get implied move calculator."""
        if self._implied_move_calc is None:
            # Use cached provider for efficiency
            self._implied_move_calc = ImpliedMoveCalculator(
                provider=self.cached_options_provider
            )
            logger.debug("Created ImpliedMoveCalculator")
        return self._implied_move_calc

    @property
    def vrp_calculator(self) -> VRPCalculator:
        """Get VRP calculator."""
        if self._vrp_calc is None:
            self._vrp_calc = VRPCalculator(
                threshold_excellent=self.config.thresholds.vrp_excellent,
                threshold_good=self.config.thresholds.vrp_good,
                threshold_marginal=self.config.thresholds.vrp_marginal,
            )
            logger.debug("Created VRPCalculator")
        return self._vrp_calc

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def initialize_database(self) -> None:
        """
        Initialize database schema.
        Should be called once at startup or during setup.
        """
        from src.infrastructure.database.init_schema import init_database

        init_database(self.config.database.path)
        logger.info("Database initialized")

    def verify_configuration(self) -> None:
        """
        Verify configuration is valid.
        Raises ConfigurationError if invalid.
        """
        from src.config.validation import validate_configuration

        validate_configuration(self.config)
        logger.info("Configuration verified")

    def clear_cache(self) -> None:
        """Clear all caches."""
        if self._cache:
            self._cache.clear()
            logger.info("Cache cleared")

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        if self._cache:
            return self._cache.get_stats()
        return {}

    # ========================================================================
    # Factory Methods (for testing)
    # ========================================================================

    @classmethod
    def create_from_env(cls, env_file: Optional[str] = None) -> 'Container':
        """
        Create container from environment variables.

        Args:
            env_file: Optional path to .env file

        Returns:
            Configured container instance
        """
        config = Config.from_env(env_file)
        return cls(config)

    def with_mock_tradier(self, mock_tradier) -> 'Container':
        """
        Replace Tradier client with mock (for testing).

        Args:
            mock_tradier: Mock TradierAPI instance

        Returns:
            Self for chaining
        """
        self._tradier = mock_tradier
        self._cached_options_provider = None  # Force recreation
        return self

    def with_mock_cache(self, mock_cache) -> 'Container':
        """
        Replace cache with mock (for testing).

        Args:
            mock_cache: Mock cache instance

        Returns:
            Self for chaining
        """
        self._cache = mock_cache
        self._cached_options_provider = None  # Force recreation
        return self


# ============================================================================
# Global Container (Singleton Pattern)
# ============================================================================

_container: Optional[Container] = None


def get_container() -> Container:
    """
    Get global container instance.

    Loads from environment on first call.
    Use this in CLI scripts for convenience.

    For testing, create Container instances directly.
    """
    global _container
    if _container is None:
        _container = Container.create_from_env()
    return _container


def reset_container() -> None:
    """
    Reset global container (useful for testing).
    """
    global _container
    _container = None
