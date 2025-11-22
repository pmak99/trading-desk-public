"""
Configuration management for IV Crush 2.0.

Loads configuration from environment variables with sensible defaults.
All configuration is immutable and validated at startup.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv


@dataclass(frozen=True)
class APIConfig:
    """API credentials and endpoints."""

    tradier_api_key: str
    tradier_base_url: str = "https://api.tradier.com/v1"
    alpha_vantage_key: str = ""
    alpha_vantage_base_url: str = "https://www.alphavantage.co/query"


@dataclass(frozen=True)
class DatabaseConfig:
    """Database configuration."""

    path: Path
    timeout: int = 30
    max_retries: int = 3


@dataclass(frozen=True)
class CacheConfig:
    """Cache configuration."""

    l1_ttl: int = 30  # L1 memory cache TTL (seconds)
    l2_ttl: int = 300  # L2 persistent cache TTL (seconds)
    enabled: bool = True


@dataclass(frozen=True)
class ThresholdsConfig:
    """Trading thresholds and parameters."""

    # VRP ratio thresholds (data-driven from 289-ticker grid scan)
    vrp_excellent: float = 7.0  # Top 33% - exceptional edge
    vrp_good: float = 4.0       # Top 67% - strong edge
    vrp_marginal: float = 1.5   # Baseline edge

    # Liquidity filters
    min_open_interest: int = 50
    max_spread_pct: float = 20.0

    # Data quality
    min_historical_quarters: int = 4
    max_historical_quarters: int = 12

    # Market cap filters
    min_market_cap_millions: float = 1000.0

    # Strike selection
    implied_move_buffer: float = 0.10  # 10% beyond implied move for safety

    # Skew analysis
    skew_strong_threshold: float = 5.0  # 5% IV difference = strong skew
    skew_moderate_threshold: float = 2.0  # 2% = moderate skew

    # Consistency
    high_consistency_threshold: float = 0.7  # CV < 0.7 = consistent
    acceptable_consistency_threshold: float = 1.0

    # Greeks thresholds
    min_theta_good: float = 30.0  # $30/day = good theta
    min_vega_good: float = -50.0  # -$50 = good vega exposure


@dataclass(frozen=True)
class ScoringWeights:
    """
    Strategy scoring weights configuration.

    Weights determine how much each factor contributes to overall strategy score.
    All weights should sum to 100 for interpretability.

    Rationale:
    - POP (45%): Primary driver of win rate. Higher weight because
                 winning more often reduces drawdowns.
    - R/R (20%): Important but secondary. A 0.30 R/R means risking $300
                 to make $100, which is acceptable with 65%+ POP.
    - VRP (20%): Measures edge quality. Higher VRP = more overpriced IV.
    - Greeks (10%): Theta/vega provide additional edge but are secondary
                    to core probability and reward/risk metrics.
    - Size (5%): Minor factor. Larger positions are better but capped
                 by risk limits anyway.

    Note: These weights should be validated through backtesting and
          potentially optimized using grid search on historical trades.
    """

    # Scoring weights with Greeks available (sum = 100)
    pop_weight: float = 45.0          # Probability of profit
    reward_risk_weight: float = 20.0  # Reward/risk ratio
    vrp_weight: float = 20.0           # VRP edge strength
    greeks_weight: float = 10.0        # Theta/vega quality
    size_weight: float = 5.0           # Position sizing

    # Scoring weights without Greeks (sum = 100)
    pop_weight_no_greeks: float = 50.0
    reward_risk_weight_no_greeks: float = 25.0
    vrp_weight_no_greeks: float = 20.0
    size_weight_no_greeks: float = 5.0

    # Target values for normalization
    target_pop: float = 0.65      # 65% POP is target
    target_rr: float = 0.30        # 30% R/R is target
    target_vrp: float = 2.0        # 2.0x VRP is excellent
    target_theta: float = 50.0     # $50/day theta is excellent
    target_vega: float = 100.0     # -$100 vega is excellent


@dataclass(frozen=True)
class StrategyConfig:
    """
    Strategy generation configuration.

    Controls how strategies are selected and constructed.
    """

    # Strike selection - Delta-based (probability-based)
    target_delta_short: float = 0.30  # Sell 30-delta (70% POP)
    target_delta_long: float = 0.20   # Buy 20-delta (80% protection)

    # Strike selection - Distance-based (fallback)
    spread_width_percent: float = 0.03  # 3% of stock price

    # Quality filters
    min_credit_per_spread: float = 0.25  # $0.25 minimum credit
    min_reward_risk: float = 0.25  # Minimum 1:4 ratio (25% reward/risk)

    # Position sizing
    risk_budget_per_trade: float = 20000.0  # $20K max loss per position
    max_contracts: int = 100  # Safety cap on contract size

    # Commission and fees
    commission_per_contract: float = 0.30  # $0.30 per contract

    # Iron butterfly specific
    iron_butterfly_wing_width_pct: float = 0.015  # 1.5% for tighter profit zone
    iron_butterfly_min_wing_width: float = 3.0    # Minimum $3 wing width

    # Iron butterfly POP estimation parameters
    # Formula: POP = base_pop + (range_pct - reference_range) * sensitivity
    # Example: 2% range → 40% POP, 4% range → 60% POP
    ib_pop_base: float = 0.40          # Base POP at reference range
    ib_pop_reference_range: float = 2.0  # Reference profit range (%)
    ib_pop_sensitivity: float = 0.10   # POP change per 1% range
    ib_pop_min: float = 0.35           # Minimum POP cap
    ib_pop_max: float = 0.70           # Maximum POP cap

    # Scoring configuration
    scoring_weights: ScoringWeights = field(default_factory=ScoringWeights)


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limiting configuration."""

    # Alpha Vantage: 5 calls/minute, 500 calls/day
    alpha_vantage_per_minute: int = 5
    alpha_vantage_per_day: int = 500

    # Tradier: Generally more permissive
    tradier_per_second: int = 10
    tradier_per_minute: int = 120


@dataclass(frozen=True)
class ResilienceConfig:
    """Resilience patterns configuration (Phase 1)."""

    # Retry settings
    retry_max_attempts: int = 3
    retry_backoff_base: float = 2.0
    retry_max_backoff: float = 60.0

    # Circuit breaker settings
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: int = 60

    # Async settings
    max_concurrent_requests: int = 10
    request_timeout: int = 30


@dataclass(frozen=True)
class AlgorithmConfig:
    """Phase 4 Enhanced Algorithm Configuration."""

    # Enable Phase 4 enhanced algorithms
    use_interpolated_move: bool = True  # Straddle interpolation between strikes
    use_enhanced_skew: bool = True      # Polynomial-fitted volatility skew
    use_enhanced_consistency: bool = True  # Exponential-weighted consistency

    # Algorithm parameters (optional overrides)
    skew_min_points: int = 5            # Minimum points for polynomial fit
    consistency_decay_factor: float = 0.85  # Exponential weight decay
    interpolation_tolerance: float = 0.01   # Strike match tolerance (dollars)

    # VRP calculation metric selection
    vrp_move_metric: str = "close"  # "close", "intraday", or "gap"
    # Rationale:
    # - "close": Earnings close vs prev close (matches ATM straddle expectation)
    # - "intraday": High-low range on earnings day (captures intraday volatility)
    # - "gap": Open vs prev close (gap move only)
    # Recommended: "close" for apples-to-apples with implied move


@dataclass(frozen=True)
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    format: str = "[%(correlation_id)s] %(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: Optional[Path] = None
    console_output: bool = True


@dataclass(frozen=True)
class Config:
    """Main configuration container."""

    api: APIConfig
    database: DatabaseConfig
    cache: CacheConfig
    thresholds: ThresholdsConfig
    strategy: StrategyConfig
    rate_limits: RateLimitConfig
    resilience: ResilienceConfig
    algorithms: AlgorithmConfig
    logging: LoggingConfig

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> 'Config':
        """
        Load configuration from environment variables.

        Args:
            env_file: Optional path to .env file. Defaults to .env in project root.

        Returns:
            Config instance with all settings loaded.

        Raises:
            ValueError: If required environment variables are missing.
        """
        # Load .env file if specified or default
        if env_file:
            load_dotenv(env_file)
        else:
            # Try to load from project root (src/config/config.py -> go up 3 levels to project root)
            project_root = Path(__file__).parent.parent.parent
            env_path = project_root / ".env"
            if env_path.exists():
                load_dotenv(env_path)

        # API configuration
        api = APIConfig(
            tradier_api_key=os.getenv("TRADIER_API_KEY", ""),
            tradier_base_url=os.getenv(
                "TRADIER_BASE_URL", "https://api.tradier.com/v1"
            ),
            alpha_vantage_key=os.getenv("ALPHA_VANTAGE_KEY", ""),
            alpha_vantage_base_url=os.getenv(
                "ALPHA_VANTAGE_BASE_URL", "https://www.alphavantage.co/query"
            ),
        )

        # Database configuration
        db_path = os.getenv("DB_PATH", "2.0/data/iv_crush_v2.db")
        database = DatabaseConfig(
            path=Path(db_path),
            timeout=int(os.getenv("DB_TIMEOUT", "30")),
            max_retries=int(os.getenv("DB_MAX_RETRIES", "3")),
        )

        # Cache configuration
        cache = CacheConfig(
            l1_ttl=int(os.getenv("CACHE_L1_TTL", "30")),
            l2_ttl=int(os.getenv("CACHE_L2_TTL", "300")),
            enabled=os.getenv("CACHE_ENABLED", "true").lower() == "true",
        )

        # Thresholds configuration (data-driven defaults)
        thresholds = ThresholdsConfig(
            vrp_excellent=float(os.getenv("VRP_EXCELLENT", "7.0")),
            vrp_good=float(os.getenv("VRP_GOOD", "4.0")),
            vrp_marginal=float(os.getenv("VRP_MARGINAL", "1.5")),
            min_open_interest=int(os.getenv("MIN_OPEN_INTEREST", "50")),
            max_spread_pct=float(os.getenv("MAX_SPREAD_PCT", "20.0")),
            min_historical_quarters=int(os.getenv("MIN_HISTORICAL_QUARTERS", "4")),
            max_historical_quarters=int(os.getenv("MAX_HISTORICAL_QUARTERS", "12")),
            min_market_cap_millions=float(
                os.getenv("MIN_MARKET_CAP_MILLIONS", "1000.0")
            ),
        )

        # Rate limits
        rate_limits = RateLimitConfig(
            alpha_vantage_per_minute=int(
                os.getenv("ALPHA_VANTAGE_PER_MINUTE", "5")
            ),
            alpha_vantage_per_day=int(os.getenv("ALPHA_VANTAGE_PER_DAY", "500")),
            tradier_per_second=int(os.getenv("TRADIER_PER_SECOND", "10")),
            tradier_per_minute=int(os.getenv("TRADIER_PER_MINUTE", "120")),
        )

        # Resilience (Phase 1)
        resilience = ResilienceConfig(
            retry_max_attempts=int(os.getenv("RETRY_MAX_ATTEMPTS", "3")),
            retry_backoff_base=float(os.getenv("RETRY_BACKOFF_BASE", "2.0")),
            retry_max_backoff=float(os.getenv("RETRY_MAX_BACKOFF", "60.0")),
            circuit_breaker_failure_threshold=int(
                os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")
            ),
            circuit_breaker_recovery_timeout=int(
                os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "60")
            ),
            max_concurrent_requests=int(
                os.getenv("MAX_CONCURRENT_REQUESTS", "10")
            ),
            request_timeout=int(os.getenv("REQUEST_TIMEOUT", "30")),
        )

        # Strategy configuration
        strategy = StrategyConfig(
            target_delta_short=float(os.getenv("TARGET_DELTA_SHORT", "0.30")),
            target_delta_long=float(os.getenv("TARGET_DELTA_LONG", "0.20")),
            spread_width_percent=float(os.getenv("SPREAD_WIDTH_PERCENT", "0.03")),
            min_credit_per_spread=float(os.getenv("MIN_CREDIT_PER_SPREAD", "0.25")),
            min_reward_risk=float(os.getenv("MIN_REWARD_RISK", "0.25")),
            risk_budget_per_trade=float(os.getenv("RISK_BUDGET_PER_TRADE", "20000.0")),
            max_contracts=int(os.getenv("MAX_CONTRACTS", "100")),
            commission_per_contract=float(os.getenv("COMMISSION_PER_CONTRACT", "0.30")),
            iron_butterfly_wing_width_pct=float(os.getenv("IB_WING_WIDTH_PCT", "0.015")),
            iron_butterfly_min_wing_width=float(os.getenv("IB_MIN_WING_WIDTH", "3.0")),
            ib_pop_base=float(os.getenv("IB_POP_BASE", "0.40")),
            ib_pop_reference_range=float(os.getenv("IB_POP_REFERENCE_RANGE", "2.0")),
            ib_pop_sensitivity=float(os.getenv("IB_POP_SENSITIVITY", "0.10")),
            ib_pop_min=float(os.getenv("IB_POP_MIN", "0.35")),
            ib_pop_max=float(os.getenv("IB_POP_MAX", "0.70")),
        )

        # Algorithms (Phase 4)
        algorithms = AlgorithmConfig(
            use_interpolated_move=os.getenv("USE_INTERPOLATED_MOVE", "true").lower() == "true",
            use_enhanced_skew=os.getenv("USE_ENHANCED_SKEW", "true").lower() == "true",
            use_enhanced_consistency=os.getenv("USE_ENHANCED_CONSISTENCY", "true").lower() == "true",
            skew_min_points=int(os.getenv("SKEW_MIN_POINTS", "5")),
            consistency_decay_factor=float(os.getenv("CONSISTENCY_DECAY_FACTOR", "0.85")),
            interpolation_tolerance=float(os.getenv("INTERPOLATION_TOLERANCE", "0.01")),
            vrp_move_metric=os.getenv("VRP_MOVE_METRIC", "close"),
        )

        # Logging
        log_file_path = os.getenv("LOG_FILE")
        logging = LoggingConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format=os.getenv(
                "LOG_FORMAT",
                "[%(correlation_id)s] %(asctime)s - %(name)s - %(levelname)s - %(message)s",
            ),
            log_file=Path(log_file_path) if log_file_path else None,
            console_output=os.getenv("LOG_CONSOLE", "true").lower() == "true",
        )

        return cls(
            api=api,
            database=database,
            cache=cache,
            thresholds=thresholds,
            strategy=strategy,
            rate_limits=rate_limits,
            resilience=resilience,
            algorithms=algorithms,
            logging=logging,
        )

    def validate(self) -> List[str]:
        """
        Validate configuration and return list of errors.

        Returns:
            List of error messages. Empty list if valid.
        """
        errors = []

        # API keys
        if not self.api.tradier_api_key:
            errors.append("TRADIER_API_KEY is required")

        # Database path parent must exist
        if not self.database.path.parent.exists():
            errors.append(
                f"Database directory does not exist: {self.database.path.parent}"
            )

        # Threshold validation
        if self.thresholds.vrp_excellent <= self.thresholds.vrp_good:
            errors.append(
                f"vrp_excellent ({self.thresholds.vrp_excellent}) must be > vrp_good ({self.thresholds.vrp_good})"
            )

        if self.thresholds.vrp_good <= self.thresholds.vrp_marginal:
            errors.append(
                f"vrp_good ({self.thresholds.vrp_good}) must be > vrp_marginal ({self.thresholds.vrp_marginal})"
            )

        # Rate limits
        if self.rate_limits.alpha_vantage_per_minute <= 0:
            errors.append("alpha_vantage_per_minute must be > 0")

        # Resilience
        if self.resilience.retry_max_attempts < 1:
            errors.append("retry_max_attempts must be >= 1")

        if self.resilience.max_concurrent_requests < 1:
            errors.append("max_concurrent_requests must be >= 1")

        return errors


# Singleton config instance (lazy loaded)
_config: Optional[Config] = None


def get_config() -> Config:
    """
    Get global config instance.
    Loads from environment on first call.
    """
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def reset_config():
    """Reset config singleton (useful for testing)."""
    global _config
    _config = None
