"""
Configuration management for IV Crush 2.0.

Loads configuration from environment variables with sensible defaults.
All configuration is immutable and validated at startup.
"""

import os
from dataclasses import dataclass
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
    fmp_api_key: str = ""  # Financial Modeling Prep
    octagon_api_key: str = ""  # Octagon AI


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

    # VRP ratio thresholds
    vrp_excellent: float = 2.0
    vrp_good: float = 1.5
    vrp_marginal: float = 1.2

    # Liquidity filters
    min_open_interest: int = 50
    max_spread_pct: float = 20.0

    # Data quality
    min_historical_quarters: int = 4
    max_historical_quarters: int = 12

    # Market cap filters
    min_market_cap_millions: float = 1000.0


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
            fmp_api_key=os.getenv("FMP_API_KEY", ""),
            octagon_api_key=os.getenv("OCTAGON_API_KEY", ""),
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

        # Thresholds configuration
        thresholds = ThresholdsConfig(
            vrp_excellent=float(os.getenv("VRP_EXCELLENT", "2.0")),
            vrp_good=float(os.getenv("VRP_GOOD", "1.5")),
            vrp_marginal=float(os.getenv("VRP_MARGINAL", "1.2")),
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

        # Algorithms (Phase 4)
        algorithms = AlgorithmConfig(
            use_interpolated_move=os.getenv("USE_INTERPOLATED_MOVE", "true").lower() == "true",
            use_enhanced_skew=os.getenv("USE_ENHANCED_SKEW", "true").lower() == "true",
            use_enhanced_consistency=os.getenv("USE_ENHANCED_CONSISTENCY", "true").lower() == "true",
            skew_min_points=int(os.getenv("SKEW_MIN_POINTS", "5")),
            consistency_decay_factor=float(os.getenv("CONSISTENCY_DECAY_FACTOR", "0.85")),
            interpolation_tolerance=float(os.getenv("INTERPOLATION_TOLERANCE", "0.01")),
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
