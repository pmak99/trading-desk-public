"""
Dependency Injection Container for IV Crush 2.0.

Provides lazy-loaded singletons for all services, calculators, and infrastructure.
Makes the entire system testable by allowing easy mocking.
"""

import logging
from pathlib import Path
from typing import Optional
from src.config.config import Config
from src.config.validation import validate_configuration
from src.infrastructure.api.tradier import TradierAPI
from src.infrastructure.api.alpha_vantage import AlphaVantageAPI
from src.infrastructure.cache.memory_cache import MemoryCache, CachedOptionsDataProvider
from src.infrastructure.cache.hybrid_cache import HybridCache
from src.infrastructure.database.repositories.earnings_repository import (
    EarningsRepository,
)
from src.infrastructure.database.repositories.prices_repository import (
    PricesRepository,
)
from src.application.metrics.implied_move import ImpliedMoveCalculator
from src.application.metrics.vrp import VRPCalculator
from src.application.services.analyzer import TickerAnalyzer
from src.application.services.health import HealthCheckService
from src.application.async_metrics.vrp_analyzer_async import AsyncTickerAnalyzer
from src.utils.rate_limiter import (
    create_alpha_vantage_limiter,
    create_tradier_limiter,
)
from src.utils.circuit_breaker import CircuitBreaker

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

    def __init__(self, config: Config, skip_validation: bool = False):
        """
        Initialize container with configuration.

        Args:
            config: Configuration instance
            skip_validation: If True, skip configuration validation (useful for testing)
        """
        if not skip_validation:
            validate_configuration(config)

        self.config = config
        self._tradier: Optional[TradierAPI] = None
        self._alphavantage: Optional[AlphaVantageAPI] = None
        self._cache: Optional[MemoryCache] = None
        self._hybrid_cache: Optional[HybridCache] = None
        self._cached_options_provider: Optional[CachedOptionsDataProvider] = None
        self._earnings_repo: Optional[EarningsRepository] = None
        self._prices_repo: Optional[PricesRepository] = None
        self._implied_move_calc: Optional[ImpliedMoveCalculator] = None
        self._vrp_calc: Optional[VRPCalculator] = None
        self._skew_analyzer = "uninitialized"  # SkewAnalyzerEnhanced or None (Phase 4)
        self._consistency_analyzer = "uninitialized"  # ConsistencyAnalyzerEnhanced or None (Phase 4)
        self._analyzer: Optional[TickerAnalyzer] = None
        self._async_analyzer: Optional[AsyncTickerAnalyzer] = None
        self._health_check_service: Optional[HealthCheckService] = None
        self._tradier_breaker: Optional[CircuitBreaker] = None
        self._alpha_vantage_breaker: Optional[CircuitBreaker] = None

    # ========================================================================
    # Infrastructure Layer
    # ========================================================================

    @property
    def tradier(self) -> TradierAPI:
        """Get Tradier API client."""
        if self._tradier is None:
            rate_limiter = create_tradier_limiter()
            self._tradier = TradierAPI(
                api_key=self.config.api.tradier_api_key,
                base_url=self.config.api.tradier_base_url,
                rate_limiter=rate_limiter,
            )
            logger.debug("Created TradierAPI client with rate limiter")
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
    def hybrid_cache(self) -> HybridCache:
        """
        Get hybrid cache (L1 memory + L2 SQLite persistence).

        Phase 2 feature: Persistent cache that survives restarts.
        """
        if self._hybrid_cache is None:
            # Use separate database file for L2 cache
            cache_db_path = self.config.database.path.parent / "cache.db"
            self._hybrid_cache = HybridCache(
                db_path=cache_db_path,
                l1_ttl_seconds=self.config.cache.l1_ttl,
                l2_ttl_seconds=self.config.cache.l2_ttl,
                max_l1_size=1000
            )
            logger.debug(f"Created HybridCache (db={cache_db_path})")
        return self._hybrid_cache

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
    def alphavantage(self) -> AlphaVantageAPI:
        """Get Alpha Vantage API client."""
        if self._alphavantage is None:
            rate_limiter = create_alpha_vantage_limiter()
            self._alphavantage = AlphaVantageAPI(
                api_key=self.config.api.alpha_vantage_key,
                base_url=self.config.api.alpha_vantage_base_url,
                rate_limiter=rate_limiter.limiters[0],  # Use per-minute limiter
            )
            logger.debug("Created AlphaVantageAPI client")
        return self._alphavantage

    @property
    def earnings_repository(self) -> EarningsRepository:
        """Get earnings repository."""
        if self._earnings_repo is None:
            self._earnings_repo = EarningsRepository(
                db_path=str(self.config.database.path)
            )
            logger.debug("Created EarningsRepository")
        return self._earnings_repo

    @property
    def prices_repository(self) -> PricesRepository:
        """Get prices repository."""
        if self._prices_repo is None:
            self._prices_repo = PricesRepository(
                db_path=str(self.config.database.path)
            )
            logger.debug("Created PricesRepository")
        return self._prices_repo

    # ========================================================================
    # Application Layer - Calculators
    # ========================================================================

    @property
    def implied_move_calculator(self) -> ImpliedMoveCalculator:
        """Get implied move calculator (standard or interpolated based on config)."""
        if self._implied_move_calc is None:
            # Use enhanced interpolated calculator if enabled in config
            if self.config.algorithms.use_interpolated_move:
                from src.application.metrics.implied_move_interpolated import ImpliedMoveCalculatorInterpolated
                self._implied_move_calc = ImpliedMoveCalculatorInterpolated(
                    provider=self.cached_options_provider
                )
                logger.info("Created ImpliedMoveCalculatorInterpolated (Phase 4)")
            else:
                self._implied_move_calc = ImpliedMoveCalculator(
                    provider=self.cached_options_provider
                )
                logger.debug("Created ImpliedMoveCalculator (standard)")
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

    @property
    def skew_analyzer(self):
        """Get skew analyzer (enhanced if enabled in config, None if disabled)."""
        if self._skew_analyzer == "uninitialized":
            if self.config.algorithms.use_enhanced_skew:
                from src.application.metrics.skew_enhanced import SkewAnalyzerEnhanced
                self._skew_analyzer = SkewAnalyzerEnhanced(
                    provider=self.cached_options_provider
                )
                logger.info("Created SkewAnalyzerEnhanced (Phase 4)")
            else:
                # Set to None permanently if disabled
                self._skew_analyzer = None
                logger.debug("Skew analysis disabled (use_enhanced_skew=false)")
        return self._skew_analyzer

    @property
    def consistency_analyzer(self):
        """Get consistency analyzer (enhanced if enabled in config, None if disabled)."""
        if self._consistency_analyzer == "uninitialized":
            if self.config.algorithms.use_enhanced_consistency:
                from src.application.metrics.consistency_enhanced import ConsistencyAnalyzerEnhanced
                self._consistency_analyzer = ConsistencyAnalyzerEnhanced(
                    prices_repo=self.prices_repository
                )
                logger.info("Created ConsistencyAnalyzerEnhanced (Phase 4)")
            else:
                # Set to None permanently if disabled
                self._consistency_analyzer = None
                logger.debug("Consistency analysis disabled (use_enhanced_consistency=false)")
        return self._consistency_analyzer

    # ========================================================================
    # Application Layer - Services
    # ========================================================================

    @property
    def analyzer(self) -> TickerAnalyzer:
        """Get ticker analyzer service."""
        if self._analyzer is None:
            self._analyzer = TickerAnalyzer(self)
            logger.debug("Created TickerAnalyzer")
        return self._analyzer

    @property
    def async_analyzer(self) -> AsyncTickerAnalyzer:
        """Get async ticker analyzer service."""
        if self._async_analyzer is None:
            self._async_analyzer = AsyncTickerAnalyzer(self.analyzer)
            logger.debug("Created AsyncTickerAnalyzer")
        return self._async_analyzer

    @property
    def health_check_service(self) -> HealthCheckService:
        """Get health check service."""
        if self._health_check_service is None:
            self._health_check_service = HealthCheckService(self)
            logger.debug("Created HealthCheckService")
        return self._health_check_service

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

    def setup_api_resilience(self):
        """Setup circuit breaker protection for all external APIs.

        Wraps all Tradier and Alpha Vantage API methods with circuit breakers
        to prevent cascading failures when APIs are down.

        Protected methods:
        - Tradier: get_option_chain, get_stock_price, get_expirations
        - Alpha Vantage: get_earnings_calendar, get_daily_prices
        """
        # Setup Tradier circuit breaker
        if self._tradier_breaker is None:
            self._tradier_breaker = CircuitBreaker(
                name="tradier", failure_threshold=5, recovery_timeout=60
            )
            logger.info("Circuit breaker installed for Tradier API")

        # Wrap all Tradier API methods
        if self._tradier is not None:
            tradier_methods = ['get_option_chain', 'get_stock_price', 'get_expirations']

            for method_name in tradier_methods:
                if hasattr(self._tradier, method_name):
                    original_method = getattr(self._tradier, method_name)

                    # Create closure to capture original_method
                    def create_wrapper(orig):
                        def wrapper(*args, **kwargs):
                            return self._tradier_breaker.call(orig, *args, **kwargs)
                        return wrapper

                    setattr(self._tradier, method_name, create_wrapper(original_method))
                    logger.debug(f"Tradier API method '{method_name}' wrapped with circuit breaker")

        # Setup Alpha Vantage circuit breaker
        if self._alpha_vantage_breaker is None:
            self._alpha_vantage_breaker = CircuitBreaker(
                name="alpha_vantage", failure_threshold=3, recovery_timeout=120
            )
            logger.info("Circuit breaker installed for Alpha Vantage API")

        # Wrap all Alpha Vantage API methods
        if self._alpha_vantage is not None:
            av_methods = ['get_earnings_calendar', 'get_daily_prices']

            for method_name in av_methods:
                if hasattr(self._alpha_vantage, method_name):
                    original_method = getattr(self._alpha_vantage, method_name)

                    # Create closure to capture original_method
                    def create_wrapper(orig):
                        def wrapper(*args, **kwargs):
                            return self._alpha_vantage_breaker.call(orig, *args, **kwargs)
                        return wrapper

                    setattr(self._alpha_vantage, method_name, create_wrapper(original_method))
                    logger.debug(f"Alpha Vantage API method '{method_name}' wrapped with circuit breaker")

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
