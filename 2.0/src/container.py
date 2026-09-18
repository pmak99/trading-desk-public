"""
Dependency Injection Container for IV Crush 2.0.

Provides lazy-loaded singletons for all services, calculators, and infrastructure.
Makes the entire system testable by allowing easy mocking.

⚠️ **Constructing a Container touches no database** (since 2026-09-14). It used
to run `MigrationManager.migrate()` from `__init__`, so every Container -- in a
test, the Finnhub MCP server, `trade.sh health` -- applied pending migrations to
whatever PGDATABASE named. The entry points that write call
`apply_migrations()` themselves, once (migrations/migration_manager.py lists
them). There is no process-global Container either: an entry point builds one
and passes it down.

The data providers are handed out as the domain's protocols
(src/domain/protocols.py) since 2026-09-14: `tradier` and
`cached_options_provider` as options data providers, `cache` as a CacheProvider,
`prices_repository` as a PriceRepository, and `implied_move_calculator` as an
ImpliedMoveCalculator (it is either calculator, per USE_INTERPOLATED_MOVE, and
neither subclasses the other). The rest stay concrete;
tests/unit/test_container_protocols.py lists them and why.
"""

import logging
from typing import Optional

from src.application.metrics.implied_move import (
    ImpliedMoveCalculator as StandardImpliedMoveCalculator,
)
from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.application.metrics.market_conditions import MarketConditionsAnalyzer
from src.application.metrics.vrp import VRPCalculator
from src.application.services.analyzer import TickerAnalyzer
from src.application.services.health import HealthCheckService
from src.application.services.strategy import StrategyGenerator
from src.config.config import Config
from src.config.validation import validate_configuration
from src.domain.protocols import (
    CachedOptionsProvider,
    CacheProvider,
    ImpliedMoveCalculator,
    OptionsDataProvider,
    PriceRepository,
)
from src.infrastructure.api.finnhub import FinnhubAPI
from src.infrastructure.api.tradier import TradierAPI
from src.infrastructure.cache.memory_cache import CachedOptionsDataProvider, MemoryCache
from src.infrastructure.database.pg_pool import get_pool
from src.infrastructure.database.repositories.analysis_repository import (
    AnalysisRepository,
)
from src.infrastructure.database.repositories.earnings_repository import (
    EarningsRepository,
)
from src.infrastructure.database.repositories.prices_repository import (
    PricesRepository,
)
from src.utils.concurrent_scanner import ConcurrentScanner
from src.utils.rate_limiter import (
    create_finnhub_limiter,
    create_tradier_limiter,
)

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

    def __init__(
        self,
        config: Config,
        skip_validation: bool = False,
        *,
        tradier: Optional[OptionsDataProvider] = None,
        cache: Optional[CacheProvider] = None,
    ):
        """
        Initialize container with configuration. Opens no connection.

        Args:
            config: Configuration instance
            skip_validation: If True, skip configuration validation (useful for testing)
            tradier: A Tradier client to use instead of building one (a test's fake)
            cache: A cache to use instead of building one
        """
        if not skip_validation:
            validate_configuration(config)

        self.config = config
        self._tradier: Optional[OptionsDataProvider] = tradier
        self._finnhub: Optional[FinnhubAPI] = None
        self._cache: Optional[CacheProvider] = cache
        self._cached_options_provider: Optional[CachedOptionsProvider] = None
        self._earnings_repo: Optional[EarningsRepository] = None
        self._prices_repo: Optional[PriceRepository] = None
        self._implied_move_calc: Optional[ImpliedMoveCalculator] = None
        self._vrp_calc: Optional[VRPCalculator] = None
        self._skew_analyzer = "uninitialized"  # SkewAnalyzerEnhanced or None (Phase 4)
        self._consistency_analyzer = "uninitialized"  # ConsistencyAnalyzerEnhanced or None (Phase 4)
        self._liquidity_scorer: Optional[LiquidityScorer] = None
        self._market_conditions: Optional[MarketConditionsAnalyzer] = None
        self._strategy_generator: Optional[StrategyGenerator] = None
        self._analyzer: Optional[TickerAnalyzer] = None
        self._analysis_repo: Optional[AnalysisRepository] = None
        self._health_check_service: Optional[HealthCheckService] = None
        self._concurrent_scanner: Optional[ConcurrentScanner] = None

    # ========================================================================
    # Infrastructure Layer
    # ========================================================================

    @property
    def tradier(self) -> OptionsDataProvider:
        """Get Tradier API client."""
        if self._tradier is None:
            rate_limiter = create_tradier_limiter(
                per_minute=self.config.rate_limits.tradier_per_minute
            )
            self._tradier = TradierAPI(
                api_key=self.config.api.tradier_api_key,
                base_url=self.config.api.tradier_base_url,
                rate_limiter=rate_limiter,
            )
            logger.debug("Created TradierAPI client with rate limiter")
        return self._tradier

    @property
    def cache(self) -> CacheProvider:
        """Get memory cache."""
        if self._cache is None:
            self._cache = MemoryCache(
                ttl_seconds=self.config.cache.l1_ttl, max_size=1000
            )
            logger.debug("Created MemoryCache")
        return self._cache

    # NOTE: `Container.hybrid_cache` was deleted at the Postgres port. It
    # handed out a HybridCache over `data/cache.db` — a file that has never
    # existed on any machine — and had zero consumers. The live persistent
    # cache is `get_shared_cache()` in scripts/scan/market_data.py, over
    # `scan_cache.db`, which is untouched. HybridCache the class stays.

    @property
    def cached_options_provider(self) -> CachedOptionsProvider:
        """Get cached options data provider (wraps Tradier with cache)."""
        if self._cached_options_provider is None:
            self._cached_options_provider = CachedOptionsDataProvider(
                provider=self.tradier, cache=self.cache
            )
            logger.debug("Created CachedOptionsDataProvider")
        return self._cached_options_provider

    @property
    def finnhub(self) -> FinnhubAPI:
        """Get Finnhub API client."""
        if self._finnhub is None:
            rate_limiter = create_finnhub_limiter(
                per_minute=self.config.rate_limits.finnhub_per_minute
            )
            self._finnhub = FinnhubAPI(
                api_key=self.config.api.finnhub_key,
                rate_limiter=rate_limiter,
            )
            logger.debug("Created FinnhubAPI client")
        return self._finnhub

    @property
    def db_pool(self):
        """The process-wide Postgres pool, opened on first use.

        No per-container instance: the pool is a process singleton, shared by
        every Container and by `apply_migrations()`.
        """
        return get_pool()

    @property
    def earnings_repository(self) -> EarningsRepository:
        """Get earnings repository with connection pooling."""
        if self._earnings_repo is None:
            self._earnings_repo = EarningsRepository(pool=self.db_pool)
            logger.debug("Created EarningsRepository with connection pool")
        return self._earnings_repo

    @property
    def prices_repository(self) -> PriceRepository:
        """Get prices repository with connection pooling."""
        if self._prices_repo is None:
            self._prices_repo = PricesRepository(pool=self.db_pool)
            logger.debug("Created PricesRepository with connection pool")
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
                from src.application.metrics.implied_move_interpolated import (
                    ImpliedMoveCalculatorInterpolated,
                )
                self._implied_move_calc = ImpliedMoveCalculatorInterpolated(
                    provider=self.cached_options_provider
                )
                logger.info("Created ImpliedMoveCalculatorInterpolated (Phase 4)")
            else:
                self._implied_move_calc = StandardImpliedMoveCalculator(
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
                min_quarters=self.config.thresholds.min_historical_quarters,
                move_metric=self.config.algorithms.vrp_move_metric,
            )
            logger.debug(f"Created VRPCalculator (metric={self.config.algorithms.vrp_move_metric})")
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
                from src.application.metrics.consistency_enhanced import (
                    ConsistencyAnalyzerEnhanced,
                )
                # FIX: Pass the same move_metric as VRP to ensure apples-to-apples comparison
                self._consistency_analyzer = ConsistencyAnalyzerEnhanced(
                    move_metric=self.config.algorithms.vrp_move_metric
                )
                logger.info(
                    f"Created ConsistencyAnalyzerEnhanced (Phase 4, metric={self.config.algorithms.vrp_move_metric})"
                )
            else:
                # Set to None permanently if disabled
                self._consistency_analyzer = None
                logger.debug("Consistency analysis disabled (use_enhanced_consistency=false)")
        return self._consistency_analyzer

    @property
    def liquidity_scorer(self) -> LiquidityScorer:
        """
        Get liquidity scorer with thresholds from config.

        Four tiers, from ThresholdsConfig (LIQUIDITY_* in .env). The static
        open-interest bars below grade strategy legs. The scan's hybrid check
        replaces them with multiples of the position the engine would size
        (liquidity/hybrid.py, full_size_contracts) and keeps only the spread bars.
        """
        if self._liquidity_scorer is None:
            # Use config thresholds to ensure consistency across all modes
            thresholds = self.config.thresholds

            self._liquidity_scorer = LiquidityScorer(
                # REJECT: OI < warning_oi (min_oi, default 10, is a floor below it), spread > 25%
                min_oi=thresholds.liquidity_reject_min_oi,
                min_volume=thresholds.liquidity_reject_min_volume,
                max_spread_pct=thresholds.liquidity_reject_max_spread_pct,

                # WARNING: OI from liquidity_warning_min_oi (default 50), spread 18-25%
                warning_oi=thresholds.liquidity_warning_min_oi,
                warning_spread_pct=thresholds.liquidity_warning_max_spread_pct,

                # GOOD: OI from liquidity_good_min_oi (default 100), spread 12-18%
                good_oi=thresholds.liquidity_good_min_oi,
                good_volume=thresholds.liquidity_warning_min_volume,
                good_spread_pct=thresholds.liquidity_good_max_spread_pct,

                # EXCELLENT: OI from liquidity_excellent_min_oi (default 200), spread < 12%
                excellent_oi=thresholds.liquidity_excellent_min_oi,
                excellent_volume=thresholds.liquidity_excellent_min_volume,
                excellent_spread_pct=thresholds.liquidity_excellent_max_spread_pct,
            )
            logger.debug(f"Created LiquidityScorer with 4-tier config: "
                        f"EXCELLENT OI>={thresholds.liquidity_excellent_min_oi}, "
                        f"GOOD OI>={thresholds.liquidity_good_min_oi}, "
                        f"WARNING OI>={thresholds.liquidity_warning_min_oi}, else REJECT; "
                        f"spread REJECT>{thresholds.liquidity_reject_max_spread_pct}% "
                        f"WARNING>={thresholds.liquidity_warning_max_spread_pct}% "
                        f"GOOD>={thresholds.liquidity_good_max_spread_pct}%")
        return self._liquidity_scorer

    @property
    def market_conditions_analyzer(self) -> MarketConditionsAnalyzer:
        """VIX regime, for display and analysis_log only — it moves no tier or size."""
        if self._market_conditions is None:
            self._market_conditions = MarketConditionsAnalyzer(
                provider=self.tradier,
                vix_symbol="VIX",
            )
            logger.debug("Created MarketConditionsAnalyzer")
        return self._market_conditions

    # ========================================================================
    # Application Layer - Services
    # ========================================================================

    @property
    def strategy_generator(self) -> StrategyGenerator:
        """Get strategy generator service with liquidity scoring."""
        if self._strategy_generator is None:
            self._strategy_generator = StrategyGenerator(
                config=self.config.strategy,
                liquidity_scorer=self.liquidity_scorer
            )
            logger.debug("Created StrategyGenerator with StrategyConfig and LiquidityScorer")
        return self._strategy_generator

    @property
    def analyzer(self) -> TickerAnalyzer:
        """Get ticker analyzer service."""
        if self._analyzer is None:
            self._analyzer = TickerAnalyzer(self)
            logger.debug("Created TickerAnalyzer")
        return self._analyzer

    @property
    def health_check_service(self) -> HealthCheckService:
        """Get health check service."""
        if self._health_check_service is None:
            self._health_check_service = HealthCheckService(self)
            logger.debug("Created HealthCheckService")
        return self._health_check_service

    @property
    def analysis_repository(self) -> AnalysisRepository:
        """Get analysis repository for logging analysis results with connection pooling."""
        if self._analysis_repo is None:
            self._analysis_repo = AnalysisRepository(pool=self.db_pool)
            logger.debug("Created AnalysisRepository with connection pool")
        return self._analysis_repo

    @property
    def concurrent_scanner(self) -> ConcurrentScanner:
        """
        Get concurrent scanner for parallel ticker analysis.

        Uses ThreadPoolExecutor for 5x+ speedup on multi-ticker scans.
        Default: 5 workers. API pacing happens in the shared TradierAPI
        client's TokenBucketRateLimiter, not here.
        """
        if self._concurrent_scanner is None:
            self._concurrent_scanner = ConcurrentScanner(
                container=self,
                max_workers=5,
            )
            logger.debug("Created ConcurrentScanner (5 workers)")
        return self._concurrent_scanner

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def initialize_database(self) -> None:
        """
        Initialize database schema.
        Should be called once at startup or during setup.
        """
        from src.infrastructure.database.init_schema import init_database

        with self.db_pool.connection() as conn:
            init_database(conn)
        logger.info("Database initialized")

    # `create_from_env`, `with_mock_tradier`, `with_mock_cache` and the global
    # `get_container()` / `reset_container()` were removed 2026-09-14 (audit
    # 5.5). None had a caller: the entry points build `Container(config)` and
    # pass it down, and a test injects fakes through the constructor
    # (`Container(config, tradier=fake, cache=...)`). `verify_configuration`,
    # `clear_cache` and `get_cache_stats` followed the same day, also uncalled:
    # the constructor already validates unless `skip_validation`.
