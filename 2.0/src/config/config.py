"""
Configuration management for IV Crush 2.0.

Loads configuration from environment variables with sensible defaults.
All configuration is immutable and validated at startup.
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


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

    # VRP ratio thresholds
    # THRESHOLD PROFILES (select via vrp_threshold_mode):
    # - CONSERVATIVE: 2.0x/1.5x/1.2x - Higher selectivity, fewer trades
    # - BALANCED:     1.8x/1.4x/1.2x - Good edge/frequency balance (DEFAULT)
    # - AGGRESSIVE:   1.5x/1.3x/1.1x - More opportunities
    vrp_threshold_mode: str = "BALANCED"
    vrp_excellent: float = 1.8
    vrp_good: float = 1.4
    vrp_marginal: float = 1.2

    # Liquidity filters - 4-TIER SYSTEM
    # OI Tiers: REJECT (<1x), WARNING (1-2x), GOOD (2-5x), EXCELLENT (>=5x) of position size
    # Spread Tiers: REJECT (>15%), WARNING (>12%), GOOD (>8%), EXCELLENT (<=8%)
    #
    # REJECT tier: Hard minimums (spread > 15%)
    liquidity_reject_min_oi: int = 20
    liquidity_reject_max_spread_pct: float = 15.0  # Spread > 15% = REJECT
    liquidity_reject_min_volume: int = 0

    # WARNING tier: 1-2x position size, spread 12-15%
    liquidity_warning_min_oi: int = 200
    liquidity_warning_max_spread_pct: float = 12.0  # Spread > 12% = WARNING
    liquidity_warning_min_volume: int = 0

    # GOOD tier: 2-5x position size, spread 8-12%
    liquidity_good_min_oi: int = 500
    liquidity_good_max_spread_pct: float = 8.0  # Spread > 8% = GOOD

    # EXCELLENT tier: >=5x position size, spread <= 8%
    liquidity_excellent_min_oi: int = 1000
    liquidity_excellent_max_spread_pct: float = 8.0  # Spread <= 8% = EXCELLENT
    liquidity_excellent_min_volume: int = 100

    # Data quality
    min_historical_quarters: int = 4
    max_historical_quarters: int = 12

    # Market cap filters
    min_market_cap_millions: float = 1000.0

    # Strike selection
    implied_move_buffer: float = 0.10

    # Skew analysis
    skew_strong_threshold: float = 5.0
    skew_moderate_threshold: float = 2.0

    # Consistency
    high_consistency_threshold: float = 0.7
    acceptable_consistency_threshold: float = 1.0

    # Greeks thresholds
    min_theta_good: float = 30.0
    min_vega_good: float = -50.0

    # Weekly options filter (opt-in)
    # When enabled, filters out tickers without weekly options
    # Weekly options have better liquidity and tighter spreads
    require_weekly_options: bool = False


@dataclass(frozen=True)
class ScoringWeights:
    """
    Strategy scoring weights configuration.

    Weights determine how much each factor contributes to overall strategy score.
    All weights should sum to 100 for interpretability.

    UPDATED POST-LOSS ANALYSIS (Nov 2025):
    After -$26,930 loss from WDAY/ZS/SYM, TRUE P&L analysis revealed:
    - Position sizing was fine (collected $3k-$10k credits)
    - VRP edge was real (8x, 5x, 4x ratios)
    - PROBLEM: Catastrophic directional moves (3x-8x collected premium)
    - PROBLEM: Poor liquidity made exits expensive (slippage)
    - PROBLEM: No stop losses (held to 45-110% of max loss)

    Revised Rationale:
    - LIQUIDITY (25%): CRITICAL - Can't profit from good setups with bad fills
                       Raised from 0% to 25% after liquidity-driven losses
    - POP (30%): Still primary but reduced to make room for liquidity
    - VRP (20%): Edge quality remains important
    - R/R (15%): Reduced - was overshadowed by directional risk
    - Greeks (10%): Unchanged - secondary edge indicators

    USER PREFERENCE UPDATE (Dec 2025):
    POP increased from 30% to 40% to prioritize high-probability trades.
    Other factors rebalanced proportionally to maintain 100 total.

    CURRENT weights: POP 40%, Liquidity 22%, VRP 17%, Edge 13%, Greeks 8%
    """

    # Scoring weights with Greeks available (sum = 100)
    # USER PREFERENCE (Dec 2025): POP weighted higher for high-probability trades
    pop_weight: float = 40.0          # Probability of profit (increased from 30%)
    liquidity_weight: float = 22.0    # Liquidity quality (reduced from 25%)
    vrp_weight: float = 17.0          # VRP edge strength (reduced from 20%)
    reward_risk_weight: float = 13.0  # Kelly edge (reduced from 15%)
    greeks_weight: float = 8.0        # Theta/vega quality (reduced from 10%)
    size_weight: float = 0.0          # Removed (position sizing is handled separately)

    # Scoring weights without Greeks (sum = 100)
    pop_weight_no_greeks: float = 45.0        # Increased from 35%
    liquidity_weight_no_greeks: float = 26.0  # Reduced from 30%
    vrp_weight_no_greeks: float = 17.0        # Reduced from 20%
    reward_risk_weight_no_greeks: float = 12.0  # Reduced from 15%
    size_weight_no_greeks: float = 0.0        # Removed

    # Target values for normalization
    target_pop: float = 0.65      # 65% POP is target
    target_rr: float = 0.30        # 30% R/R is target
    target_vrp: float = 2.0        # 2.0x VRP is excellent
    target_theta: float = 50.0     # $50/day theta is excellent
    target_vega: float = 100.0     # -$100 vega is excellent

    # NEW: Liquidity scoring targets (post-loss analysis addition)
    target_liquidity_oi: float = 5000.0      # Target open interest
    target_liquidity_spread: float = 5.0     # Target bid-ask spread %
    target_liquidity_volume: float = 500.0   # Target daily volume

    # Rationale generation thresholds
    vrp_excellent_threshold: float = 2.0   # VRP >= 2.0 is "excellent"
    vrp_strong_threshold: float = 1.5      # VRP >= 1.5 is "strong"
    rr_favorable_threshold: float = 0.35   # R/R >= 0.35 is "favorable"
    pop_high_threshold: float = 0.70       # POP >= 70% is "high"
    theta_positive_threshold: float = 30.0  # Theta > $30/day mentioned in rationale
    vega_beneficial_threshold: float = -50.0  # Vega < -$50 benefits from IV crush

    # Liquidity thresholds for rationale
    liquidity_excellent_threshold: str = "EXCELLENT"
    liquidity_acceptable_threshold: str = "WARNING"


@dataclass(frozen=True)
class ScanConfig:
    """Scan mode scoring configuration."""

    # Quality score factors (sum = 100)
    score_vrp_max_points: float = 35.0
    score_vrp_target: float = 3.0
    score_edge_max_points: float = 30.0
    score_edge_target: float = 4.0
    score_liquidity_max_points: float = 20.0
    score_liquidity_excellent_points: float = 20.0
    score_liquidity_warning_points: float = 10.0
    score_liquidity_reject_points: float = 0.0
    score_move_max_points: float = 15.0

    # Implied move difficulty thresholds
    score_move_easy_threshold: float = 8.0
    score_move_moderate_threshold: float = 12.0
    score_move_moderate_points: float = 10.0
    score_move_challenging_threshold: float = 15.0
    score_move_challenging_points: float = 6.0
    score_move_extreme_points: float = 3.0
    score_move_default_points: float = 7.5

    # Cache settings
    cache_max_l1_size: int = 100


@dataclass(frozen=True)
class StrategyConfig:
    """Strategy generation configuration."""

    # Strike selection
    target_delta_short: float = 0.25  # Sell 25-delta (75% POP)
    target_delta_long: float = 0.20   # Buy 20-delta for protection

    # Spread width - Fixed dollar amounts
    spread_width_high_price: float = 5.0  # $5 for stocks >= $20
    spread_width_low_price: float = 3.0   # $3 for stocks < $20
    spread_width_threshold: float = 20.0

    # Quality filters
    min_credit_per_spread: float = 0.20
    min_reward_risk: float = 0.25

    # Position sizing - Kelly Criterion based
    risk_budget_per_trade: float = 20000.0
    max_contracts: int = 100

    # Kelly Criterion parameters
    use_kelly_sizing: bool = True
    kelly_fraction: float = 0.25
    kelly_min_edge: float = 0.05
    kelly_min_contracts: int = 1

    # Commission
    commission_per_contract: float = 0.30

    # Iron butterfly
    iron_butterfly_wing_width_pct: float = 0.015
    iron_butterfly_min_wing_width: float = 3.0
    ib_pop_base: float = 0.40
    ib_pop_reference_range: float = 2.0
    ib_pop_sensitivity: float = 0.10
    ib_pop_min: float = 0.35
    ib_pop_max: float = 0.70

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
    vrp_move_metric: str = "intraday"  # "close", "intraday", or "gap"
    # Rationale:
    # - "close": Earnings close vs prev close (matches ATM straddle expectation)
    # - "intraday": High-low range on earnings day (captures intraday volatility)
    # - "gap": Open vs prev close (gap move only)
    # Changed to "intraday" to capture full volatility range for IV crush strategy


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
    scan: ScanConfig
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

        # Thresholds configuration with profile support
        # VRP threshold profiles (can be overridden via env vars)
        vrp_mode = os.getenv("VRP_THRESHOLD_MODE", "BALANCED").upper()

        # Define threshold profiles
        vrp_profiles = {
            "CONSERVATIVE": {"excellent": 2.0, "good": 1.5, "marginal": 1.2},
            "BALANCED":     {"excellent": 1.8, "good": 1.4, "marginal": 1.2},
            "AGGRESSIVE":   {"excellent": 1.5, "good": 1.3, "marginal": 1.1},
            "LEGACY":       {"excellent": 7.0, "good": 4.0, "marginal": 1.5},
        }

        # Apply profile (with env var override capability)
        if vrp_mode not in vrp_profiles:
            logger.warning(
                f"Invalid VRP_THRESHOLD_MODE '{vrp_mode}', defaulting to BALANCED. "
                f"Valid modes: {', '.join(vrp_profiles.keys())}"
            )
            vrp_mode = "BALANCED"

        profile = vrp_profiles[vrp_mode]
        logger.info(f"Using VRP threshold profile: {vrp_mode}")

        # Check for individual threshold overrides and warn
        overrides = []
        if os.getenv("VRP_EXCELLENT"):
            overrides.append(f"VRP_EXCELLENT={os.getenv('VRP_EXCELLENT')} (profile default: {profile['excellent']})")
        if os.getenv("VRP_GOOD"):
            overrides.append(f"VRP_GOOD={os.getenv('VRP_GOOD')} (profile default: {profile['good']})")
        if os.getenv("VRP_MARGINAL"):
            overrides.append(f"VRP_MARGINAL={os.getenv('VRP_MARGINAL')} (profile default: {profile['marginal']})")

        if overrides:
            logger.warning(
                f"Individual VRP threshold env vars are overriding {vrp_mode} profile: "
                + ", ".join(overrides)
            )

        thresholds = ThresholdsConfig(
            vrp_threshold_mode=vrp_mode,
            vrp_excellent=float(os.getenv("VRP_EXCELLENT", str(profile["excellent"]))),
            vrp_good=float(os.getenv("VRP_GOOD", str(profile["good"]))),
            vrp_marginal=float(os.getenv("VRP_MARGINAL", str(profile["marginal"]))),
            # 4-TIER LIQUIDITY SYSTEM
            # REJECT: spread > 15%
            liquidity_reject_min_oi=int(os.getenv("LIQUIDITY_REJECT_MIN_OI", "20")),
            liquidity_reject_max_spread_pct=float(os.getenv("LIQUIDITY_REJECT_MAX_SPREAD_PCT", "15.0")),
            liquidity_reject_min_volume=int(os.getenv("LIQUIDITY_REJECT_MIN_VOLUME", "0")),
            # WARNING: spread > 12%
            liquidity_warning_min_oi=int(os.getenv("LIQUIDITY_WARNING_MIN_OI", "200")),
            liquidity_warning_max_spread_pct=float(os.getenv("LIQUIDITY_WARNING_MAX_SPREAD_PCT", "12.0")),
            liquidity_warning_min_volume=int(os.getenv("LIQUIDITY_WARNING_MIN_VOLUME", "0")),
            # GOOD: spread > 8%
            liquidity_good_min_oi=int(os.getenv("LIQUIDITY_GOOD_MIN_OI", "500")),
            liquidity_good_max_spread_pct=float(os.getenv("LIQUIDITY_GOOD_MAX_SPREAD_PCT", "8.0")),
            # EXCELLENT: spread <= 8%
            liquidity_excellent_min_oi=int(os.getenv("LIQUIDITY_EXCELLENT_MIN_OI", "1000")),
            liquidity_excellent_max_spread_pct=float(os.getenv("LIQUIDITY_EXCELLENT_MAX_SPREAD_PCT", "8.0")),
            liquidity_excellent_min_volume=int(os.getenv("LIQUIDITY_EXCELLENT_MIN_VOLUME", "100")),
            min_historical_quarters=int(os.getenv("MIN_HISTORICAL_QUARTERS", "4")),
            max_historical_quarters=int(os.getenv("MAX_HISTORICAL_QUARTERS", "12")),
            min_market_cap_millions=float(os.getenv("MIN_MARKET_CAP_MILLIONS", "1000.0")),
            # Weekly options filter (opt-in, default OFF)
            require_weekly_options=os.getenv("REQUIRE_WEEKLY_OPTIONS", "false").lower() == "true",
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
            target_delta_short=float(os.getenv("TARGET_DELTA_SHORT", "0.25")),
            target_delta_long=float(os.getenv("TARGET_DELTA_LONG", "0.20")),
            spread_width_high_price=float(os.getenv("SPREAD_WIDTH_HIGH_PRICE", "5.0")),
            spread_width_low_price=float(os.getenv("SPREAD_WIDTH_LOW_PRICE", "3.0")),
            spread_width_threshold=float(os.getenv("SPREAD_WIDTH_THRESHOLD", "20.0")),
            min_credit_per_spread=float(os.getenv("MIN_CREDIT_PER_SPREAD", "0.20")),
            min_reward_risk=float(os.getenv("MIN_REWARD_RISK", "0.25")),
            risk_budget_per_trade=float(os.getenv("RISK_BUDGET_PER_TRADE", "20000.0")),
            max_contracts=int(os.getenv("MAX_CONTRACTS", "100")),
            use_kelly_sizing=os.getenv("USE_KELLY_SIZING", "true").lower() == "true",
            kelly_fraction=float(os.getenv("KELLY_FRACTION", "0.25")),
            kelly_min_edge=float(os.getenv("KELLY_MIN_EDGE", "0.05")),
            kelly_min_contracts=int(os.getenv("KELLY_MIN_CONTRACTS", "1")),
            commission_per_contract=float(os.getenv("COMMISSION_PER_CONTRACT", "0.30")),
            iron_butterfly_wing_width_pct=float(os.getenv("IB_WING_WIDTH_PCT", "0.015")),
            iron_butterfly_min_wing_width=float(os.getenv("IB_MIN_WING_WIDTH", "3.0")),
            ib_pop_base=float(os.getenv("IB_POP_BASE", "0.40")),
            ib_pop_reference_range=float(os.getenv("IB_POP_REFERENCE_RANGE", "2.0")),
            ib_pop_sensitivity=float(os.getenv("IB_POP_SENSITIVITY", "0.10")),
            ib_pop_min=float(os.getenv("IB_POP_MIN", "0.35")),
            ib_pop_max=float(os.getenv("IB_POP_MAX", "0.70")),
        )

        # Scan scoring configuration
        scan = ScanConfig()

        # Algorithms
        algorithms = AlgorithmConfig(
            use_interpolated_move=os.getenv("USE_INTERPOLATED_MOVE", "true").lower() == "true",
            use_enhanced_skew=os.getenv("USE_ENHANCED_SKEW", "true").lower() == "true",
            use_enhanced_consistency=os.getenv("USE_ENHANCED_CONSISTENCY", "true").lower() == "true",
            skew_min_points=int(os.getenv("SKEW_MIN_POINTS", "5")),
            consistency_decay_factor=float(os.getenv("CONSISTENCY_DECAY_FACTOR", "0.85")),
            interpolation_tolerance=float(os.getenv("INTERPOLATION_TOLERANCE", "0.01")),
            vrp_move_metric=os.getenv("VRP_MOVE_METRIC", "intraday"),
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
            scan=scan,
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
