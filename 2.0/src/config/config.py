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

    # VRP ratio thresholds - REVISED (Nov 2025)
    # Previous values (7.0x/4.0x) were overfitted to small sample (8 trades)
    # Academic literature: VRP of 1.2-1.5x is typically tradeable
    # Market research: Consistent edge appears at 1.5x+, excellent at 2.0x+
    #
    # THRESHOLD PROFILES (select via vrp_threshold_mode):
    # - CONSERVATIVE: 2.0x/1.5x/1.2x - Higher selectivity, fewer trades, stronger edge
    # - BALANCED:     1.8x/1.4x/1.2x - Moderate selectivity, good edge/frequency balance (DEFAULT)
    # - AGGRESSIVE:   1.5x/1.3x/1.1x - More opportunities, acceptable edge
    # - LEGACY:       7.0x/4.0x/1.5x - Original overfitted values (NOT RECOMMENDED)

    vrp_threshold_mode: str = "BALANCED"  # CONSERVATIVE, BALANCED, AGGRESSIVE, or LEGACY

    # Active thresholds (set based on mode, can be overridden)
    vrp_excellent: float = 1.8  # BALANCED default
    vrp_good: float = 1.4       # BALANCED default
    vrp_marginal: float = 1.2   # BALANCED default

    # Liquidity filters - 3-TIER SYSTEM (USER CALIBRATED FOR 50-200 CONTRACT TRADES)
    # Classification logic: EXCELLENT if all excellent thresholds met,
    #                       REJECT if any reject threshold fails,
    #                       WARNING for everything in between (catch-all tier)

    # REJECT tier: Hard minimums - any metric below these = REJECT
    liquidity_reject_min_oi: int = 20         # Absolute minimum OI
    liquidity_reject_max_spread_pct: float = 100.0  # Maximum acceptable spread %
    liquidity_reject_min_volume: int = 0      # Allow zero volume (future options may not have traded today)

    # WARNING tier: Used for scoring only (not classification boundaries)
    # These thresholds help score options 0-100 but don't filter
    liquidity_warning_min_oi: int = 200       # "Good" OI for scoring
    liquidity_warning_max_spread_pct: float = 15.0  # "Good" spread for scoring
    liquidity_warning_min_volume: int = 0     # Allow zero volume

    # EXCELLENT tier: All metrics must meet these thresholds
    liquidity_excellent_min_oi: int = 1000    # High OI threshold
    liquidity_excellent_max_spread_pct: float = 8.0  # Tight spread threshold
    liquidity_excellent_min_volume: int = 100  # High volume threshold

    # Legacy thresholds (DEPRECATED - use tier system instead)
    min_open_interest: int = 100  # Now matches REJECT tier
    max_spread_pct: float = 50.0  # Now matches REJECT tier

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

    OLD weights: POP 45%, R/R 20%, VRP 20%, Greeks 10%, Size 5%
    NEW weights: POP 30%, LIQUIDITY 25%, VRP 20%, R/R 15%, Greeks 10%
    """

    # Scoring weights with Greeks available (sum = 100)
    pop_weight: float = 30.0          # Probability of profit (reduced from 45%)
    liquidity_weight: float = 25.0    # NEW: Liquidity quality (critical addition)
    vrp_weight: float = 20.0          # VRP edge strength (unchanged)
    reward_risk_weight: float = 15.0  # Reward/risk ratio (reduced from 20%)
    greeks_weight: float = 10.0       # Theta/vega quality (unchanged)
    size_weight: float = 0.0          # Removed (position sizing is handled separately)

    # Scoring weights without Greeks (sum = 100)
    pop_weight_no_greeks: float = 35.0        # Reduced from 50%
    liquidity_weight_no_greeks: float = 30.0  # NEW: Even more critical without Greeks
    vrp_weight_no_greeks: float = 20.0        # Unchanged
    reward_risk_weight_no_greeks: float = 15.0  # Reduced from 25%
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

    # NEW: Liquidity thresholds for rationale
    liquidity_excellent_threshold: str = "EXCELLENT"  # Liquidity tier = EXCELLENT
    liquidity_acceptable_threshold: str = "WARNING"   # Minimum acceptable = WARNING


@dataclass(frozen=True)
class StrategyConfig:
    """
    Strategy generation configuration.

    Controls how strategies are selected and constructed.
    """

    # Strike selection - Delta-based (probability-based)
    target_delta_short: float = 0.25  # Sell 25-delta (75% POP, balanced approach)

    # DEPRECATED: Long strike selection now uses fixed dollar width
    # TODO(v3.0): Remove this parameter - kept for backward compatibility only
    target_delta_long: float = 0.25

    # DEPRECATED: Spread width now uses fixed dollar amounts
    # TODO(v3.0): Remove this parameter - kept for backward compatibility only
    spread_width_percent: float = 0.03

    # Spread width - Fixed dollar amounts (user strategy)
    spread_width_high_price: float = 5.0  # $5 for stocks >= $20
    spread_width_low_price: float = 3.0   # $3 for stocks < $20
    spread_width_threshold: float = 20.0  # Price threshold

    # Quality filters
    min_credit_per_spread: float = 0.20  # $0.20 minimum credit (balanced for 25-delta)
    min_reward_risk: float = 0.25  # Minimum 1:4 ratio (25% reward/risk)

    # Position sizing - Kelly Criterion based
    risk_budget_per_trade: float = 20000.0  # $20K max loss per position (used as account equity proxy)
    max_contracts: int = 100  # Safety cap on contract size

    # Kelly Criterion parameters
    use_kelly_sizing: bool = True           # Use Kelly Criterion (True) or fixed risk budget (False)
    kelly_fraction: float = 0.25            # Fractional Kelly (0.25 = use 25% of full Kelly, conservative)
    kelly_min_edge: float = 0.05            # Minimum edge required for Kelly (5%)
    kelly_min_contracts: int = 1            # Minimum contracts even if Kelly suggests 0

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

        thresholds = ThresholdsConfig(
            vrp_threshold_mode=vrp_mode,
            vrp_excellent=float(os.getenv("VRP_EXCELLENT", str(profile["excellent"]))),
            vrp_good=float(os.getenv("VRP_GOOD", str(profile["good"]))),
            vrp_marginal=float(os.getenv("VRP_MARGINAL", str(profile["marginal"]))),
            # 3-tier liquidity system (calibrated for 50-200 contract trades)
            liquidity_reject_min_oi=int(os.getenv("LIQUIDITY_REJECT_MIN_OI", "20")),
            liquidity_reject_max_spread_pct=float(os.getenv("LIQUIDITY_REJECT_MAX_SPREAD_PCT", "100.0")),
            liquidity_reject_min_volume=int(os.getenv("LIQUIDITY_REJECT_MIN_VOLUME", "0")),
            liquidity_warning_min_oi=int(os.getenv("LIQUIDITY_WARNING_MIN_OI", "200")),
            liquidity_warning_max_spread_pct=float(os.getenv("LIQUIDITY_WARNING_MAX_SPREAD_PCT", "15.0")),
            liquidity_warning_min_volume=int(os.getenv("LIQUIDITY_WARNING_MIN_VOLUME", "0")),
            liquidity_excellent_min_oi=int(os.getenv("LIQUIDITY_EXCELLENT_MIN_OI", "1000")),
            liquidity_excellent_max_spread_pct=float(os.getenv("LIQUIDITY_EXCELLENT_MAX_SPREAD_PCT", "8.0")),
            liquidity_excellent_min_volume=int(os.getenv("LIQUIDITY_EXCELLENT_MIN_VOLUME", "100")),
            # Legacy (deprecated)
            min_open_interest=int(os.getenv("MIN_OPEN_INTEREST", "100")),
            max_spread_pct=float(os.getenv("MAX_SPREAD_PCT", "50.0")),
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

        # Check for deprecated env vars and warn users
        if os.getenv("TARGET_DELTA_LONG") and os.getenv("TARGET_DELTA_LONG") != "0.25":
            logger.warning(
                "TARGET_DELTA_LONG is deprecated and ignored. "
                "Long strike selection now uses fixed dollar spread widths. "
                "Use SPREAD_WIDTH_HIGH_PRICE and SPREAD_WIDTH_LOW_PRICE instead."
            )

        if os.getenv("SPREAD_WIDTH_PERCENT") and os.getenv("SPREAD_WIDTH_PERCENT") != "0.03":
            logger.warning(
                "SPREAD_WIDTH_PERCENT is deprecated and ignored. "
                "Use SPREAD_WIDTH_HIGH_PRICE (default $5) and SPREAD_WIDTH_LOW_PRICE (default $3) instead."
            )

        # Strategy configuration
        strategy = StrategyConfig(
            target_delta_short=float(os.getenv("TARGET_DELTA_SHORT", "0.25")),
            target_delta_long=float(os.getenv("TARGET_DELTA_LONG", "0.25")),
            spread_width_percent=float(os.getenv("SPREAD_WIDTH_PERCENT", "0.03")),
            spread_width_high_price=float(os.getenv("SPREAD_WIDTH_HIGH_PRICE", "5.0")),
            spread_width_low_price=float(os.getenv("SPREAD_WIDTH_LOW_PRICE", "3.0")),
            spread_width_threshold=float(os.getenv("SPREAD_WIDTH_THRESHOLD", "20.0")),
            min_credit_per_spread=float(os.getenv("MIN_CREDIT_PER_SPREAD", "0.20")),
            min_reward_risk=float(os.getenv("MIN_REWARD_RISK", "0.25")),
            risk_budget_per_trade=float(os.getenv("RISK_BUDGET_PER_TRADE", "20000.0")),
            max_contracts=int(os.getenv("MAX_CONTRACTS", "100")),
            # Kelly Criterion position sizing
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
