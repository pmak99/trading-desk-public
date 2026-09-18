"""
Configuration management for IV Crush 2.0.

Loads configuration from environment variables with sensible defaults.
All configuration is immutable and validated at startup.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from src.domain.scoring.weights import ScoringWeights
from src.domain.vrp_tier import VRP_EXCELLENT_FROM, VRP_GOOD_FROM, VRP_MARGINAL_FROM

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class APIConfig:
    """API credentials, endpoints and subscriptions.

    `from_env()` is the one reader of these variables in the repo
    (tests/unit/test_config_env_readers.py fails on another). Keys are
    `repr=False`: a logged Config or a traceback's locals never carries one.
    """

    tradier_api_key: str = field(repr=False)
    tradier_base_url: str = "https://api.tradier.com/v1"
    finnhub_key: str = field(default="", repr=False)
    twelve_data_key: str = field(default="", repr=False)
    # ORATS: subscription ended Jun 2026, key dead since 2026-07-02. Off unless
    # ORATS_ENABLED is exactly "true" (any case). Until 2026-09-14 the flag lived
    # in common/constants.py and an unset variable meant ON, which would read the
    # position_limits snapshot frozen at 2026-06-24 into sizing. The branched
    # logic stays for a resubscription.
    orats_enabled: bool = False

    @classmethod
    def from_env(cls) -> "APIConfig":
        """Read the environment as it is now. Does not load 2.0/.env.

        For code that needs a key at call time without a Config in hand (the
        Twelve Data fetch, record_moves' Finnhub gate, TACO's fallbacks).
        `Config.from_env()` loads .env first and then calls this.
        """
        return cls(
            tradier_api_key=os.getenv("TRADIER_API_KEY", ""),
            tradier_base_url=os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1"),
            finnhub_key=os.getenv("FINNHUB_API_KEY", ""),
            twelve_data_key=os.getenv("TWELVE_DATA_KEY", ""),
            orats_enabled=os.getenv("ORATS_ENABLED", "false").lower() == "true",
        )


@dataclass(frozen=True)
class CacheConfig:
    """Cache configuration."""

    l1_ttl: int = 30  # L1 memory cache TTL (seconds)
    l2_ttl: int = 300  # L2 persistent cache TTL (seconds)
    enabled: bool = True


@dataclass(frozen=True)
class ThresholdsConfig:
    """Trading thresholds and parameters."""

    # VRP tiers (CLAUDE.md §1). Not read from the environment, and not adjusted
    # by VIX on any path since 2026-09-14 (scripts/research/vix_adaptive_thresholds.py).
    vrp_excellent: float = VRP_EXCELLENT_FROM
    vrp_good: float = VRP_GOOD_FROM
    vrp_marginal: float = VRP_MARGINAL_FROM

    # Liquidity filters - 4-TIER SYSTEM (RELAXED Feb 2026)
    # Relaxed thresholds to allow more tickers while still penalizing poor liquidity in scoring
    #
    # REJECT tier: Hard minimums (spread > 25%)
    liquidity_reject_min_oi: int = 10
    liquidity_reject_max_spread_pct: float = 25.0  # Spread > 25% = REJECT
    liquidity_reject_min_volume: int = 0

    # WARNING tier: spread 18-25%
    liquidity_warning_min_oi: int = 50
    liquidity_warning_max_spread_pct: float = 18.0  # Spread > 18% = WARNING
    liquidity_warning_min_volume: int = 0

    # GOOD tier: spread 12-18%
    liquidity_good_min_oi: int = 100
    liquidity_good_max_spread_pct: float = 12.0  # Spread > 12% = GOOD

    # EXCELLENT tier: spread <= 12%
    liquidity_excellent_min_oi: int = 200
    liquidity_excellent_max_spread_pct: float = 12.0  # Spread <= 12% = EXCELLENT
    liquidity_excellent_min_volume: int = 10

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

    # Minimum DTE floor (Feb 2026). Evidence: CLAUDE.md §5, printed by
    # scripts/rule_evidence.py -- same win rate at 0-2 and 3-5 DTE, larger losses at 0-2.
    # If calculated expiration gives < min_dte from earnings, bump to next Friday
    min_dte: int = 3

    # Weekly options filter (opt-in)
    # When enabled, filters out tickers without weekly options
    # Weekly options have better liquidity and tighter spreads
    require_weekly_options: bool = False


@dataclass(frozen=True)
class ScanConfig:
    """Scan mode scoring configuration."""

    # Quality score factors (sum = 100)
    score_vrp_max_points: float = 35.0
    score_edge_max_points: float = 30.0
    score_edge_target: float = 4.0
    score_liquidity_max_points: float = 20.0
    score_liquidity_excellent_points: float = 20.0
    score_liquidity_good_points: float = 16.0
    score_liquidity_warning_points: float = 12.0
    score_liquidity_reject_points: float = 4.0  # Relaxed: still penalized but not zero
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

    # Position sizing: a fixed dollar-risk rule, not Kelly (sizing.py). Matches
    # .env.example and CLAUDE.md; the scan's liquidity check sizes from it too.
    risk_budget_per_trade: float = 250000.0
    max_contracts: int = 100

    # Dollar-risk warning (advisory only — never blocks a recommendation).
    # The contract cap above is not a risk cap: it only equals a dollar budget
    # when max loss per contract happens to be ~$500. Set to 0 to disable.
    max_loss_warn_threshold: float = 50000.0

    # Sizing tiers (CLAUDE.md "Sizing rules in order"). These drive the LIVE
    # cap via SizingContext; max_contracts above only applies on the fallback
    # path where no SizingContext is built. Defaults match the values that
    # were hardcoded in SizingContext before Sep 2026.
    sizing_base_cap: int = 100            # rule 5: maximum ever (a ceiling)
    sizing_default_cap: int = 50          # rule 4: cap when no flag fires
    sizing_trr_high_cap: int = 50         # rule 3: HIGH TRR hard cap
    sizing_compound_risk_cap: int = 25    # rules 1-2: compound tail risk
    sizing_reduction_factor: float = 0.5  # rules 4, 8: 50% reduction each

    # Kelly Criterion parameters
    use_kelly_sizing: bool = True
    kelly_fraction: float = 0.25
    kelly_min_contracts: int = 1

    # Commission
    commission_per_contract: float = 0.30

    # Scoring configuration
    scoring_weights: ScoringWeights = field(default_factory=ScoringWeights)


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limiting configuration."""

    # Finnhub: 60 calls/minute (free tier)
    finnhub_per_minute: int = 60

    # Tradier: Generally more permissive
    tradier_per_minute: int = 120

    # Twelve Data's Basic plan: 800 calls a UTC day per key. Every process on a
    # host counts its calls in one file and is refused past the ceiling
    # (src/utils/rate_limiter.py DailyCallCounter); 20 are left for another host.
    twelve_data_daily_ceiling: int = 780
    twelve_data_usage_file: str = "~/.local/state/trading-desk/twelve_data_calls"

    @classmethod
    def from_env(cls) -> "RateLimitConfig":
        """Read the environment as it is now. Does not load 2.0/.env.

        The Twelve Data gate is made on a process's first Twelve Data call, with
        no Config in hand. `Config.from_env()` loads .env first and then calls this.
        """
        return cls(
            finnhub_per_minute=int(os.getenv("FINNHUB_PER_MINUTE", "60")),
            tradier_per_minute=int(os.getenv("TRADIER_PER_MINUTE", "120")),
            twelve_data_daily_ceiling=int(os.getenv("TWELVE_DATA_DAILY_CEILING", "780")),
            twelve_data_usage_file=os.getenv("TWELVE_DATA_USAGE_FILE")
            or cls.twelve_data_usage_file,
        )


@dataclass(frozen=True)
class AlgorithmConfig:
    """Phase 4 Enhanced Algorithm Configuration."""

    # Enable Phase 4 enhanced algorithms
    use_interpolated_move: bool = True  # Straddle interpolation between strikes
    use_enhanced_skew: bool = True      # Polynomial-fitted volatility skew
    use_enhanced_consistency: bool = True  # Consistency, trend and trust of the VRP moves

    # Algorithm parameters (optional overrides)
    skew_min_points: int = 5            # Minimum points for polynomial fit
    interpolation_tolerance: float = 0.01   # Strike match tolerance (dollars)

    # The move VRP divides by. Fixed, not an env override: the tier cutoffs,
    # SCORE_VRP_TARGET and target_vrp are calibrated to it
    # (scripts/research/vrp_close_calibration.py). "close" = mean
    # |prev close -> reaction close|, the move a held position eats. It was
    # "intraday" (open -> close, gap excluded) until the 2026-09 migration.
    vrp_move_metric: str = "close"


@dataclass(frozen=True)
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    format: str = "[%(correlation_id)s] %(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: Optional[Path] = None
    console_output: bool = True


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot credentials for digests, alerts and job failure reports.

    Optional by design: absence is the normal state on a machine that only runs
    the CLI, and must never fail Config.from_env(). Callers gate on
    is_configured rather than assuming the values are present.
    """

    bot_token: Optional[str] = field(default=None, repr=False)
    chat_id: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class Config:
    """Main configuration container."""

    api: APIConfig
    cache: CacheConfig
    thresholds: ThresholdsConfig
    strategy: StrategyConfig
    scan: ScanConfig
    rate_limits: RateLimitConfig
    algorithms: AlgorithmConfig
    logging: LoggingConfig
    telegram: TelegramConfig = field(default_factory=TelegramConfig)

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
        # src/config/config.py -> up 3 levels is the 2.0/ project root.
        # Anchored to __file__ rather than cwd so the defaults below resolve
        # identically however the process was launched.
        project_root = Path(__file__).parent.parent.parent

        # Load .env file if specified or default
        if env_file:
            load_dotenv(env_file)
        else:
            env_path = project_root / ".env"
            if env_path.exists():
                load_dotenv(env_path)

        # API configuration: keys, endpoints, the ORATS switch
        api = APIConfig.from_env()

        # NOTE: `DatabaseConfig` was removed at the tail of the Postgres
        # migration. It carried a `path` (plus an unread timeout and
        # max_retries), and the connection target now comes from PG* in the
        # environment via common/db.py and pg_pool -- no path is passed
        # anywhere. Its last real consumer was anchoring the SQLite
        # `scan_cache.db`, and that cache moved into the Postgres
        # `whisper_cache` table on 2026-09-12.
        #
        # This also retires the CI hazard the old comment here described: a
        # cwd-relative DB_PATH resolving to 2.0/2.0/data/. A wrong DSN fails to
        # connect; there is no path left to resolve wrongly.

        # NOTE: `job_status_path` was removed at the Postgres port. It existed
        # only because a whole-file `gsutil cp` kept wiping the server's
        # job_status rows -- that table was never in sync_databases.py's
        # row-level merge, so it had no protection against a wholesale
        # overwrite (CLAUDE.md, Sep 11 2026). Splitting it into its own SQLite
        # file was the interim fix. There is one database now and no file-copy
        # sync, so the failure mode cannot occur and the separate file has no
        # remaining purpose.

        # Cache configuration
        cache = CacheConfig(
            l1_ttl=int(os.getenv("CACHE_L1_TTL", "30")),
            l2_ttl=int(os.getenv("CACHE_L2_TTL", "300")),
            enabled=os.getenv("CACHE_ENABLED", "true").lower() == "true",
        )

        # VRP tiers are ThresholdsConfig's defaults (src/domain/vrp_tier.py). The
        # VRP_THRESHOLD_MODE profiles and VRP_EXCELLENT/GOOD/MARGINAL overrides
        # were removed 2026-09-14: they let .env grade on a ladder CLAUDE.md does not have.
        thresholds = ThresholdsConfig(
            # 4-TIER LIQUIDITY SYSTEM (RELAXED Feb 2026)
            # REJECT: spread > 25%
            liquidity_reject_min_oi=int(os.getenv("LIQUIDITY_REJECT_MIN_OI", "10")),
            liquidity_reject_max_spread_pct=float(os.getenv("LIQUIDITY_REJECT_MAX_SPREAD_PCT", "25.0")),
            liquidity_reject_min_volume=int(os.getenv("LIQUIDITY_REJECT_MIN_VOLUME", "0")),
            # WARNING: spread > 18%
            liquidity_warning_min_oi=int(os.getenv("LIQUIDITY_WARNING_MIN_OI", "50")),
            liquidity_warning_max_spread_pct=float(os.getenv("LIQUIDITY_WARNING_MAX_SPREAD_PCT", "18.0")),
            liquidity_warning_min_volume=int(os.getenv("LIQUIDITY_WARNING_MIN_VOLUME", "0")),
            # GOOD: spread > 12%
            liquidity_good_min_oi=int(os.getenv("LIQUIDITY_GOOD_MIN_OI", "100")),
            liquidity_good_max_spread_pct=float(os.getenv("LIQUIDITY_GOOD_MAX_SPREAD_PCT", "12.0")),
            # EXCELLENT: spread <= 12%
            liquidity_excellent_min_oi=int(os.getenv("LIQUIDITY_EXCELLENT_MIN_OI", "200")),
            liquidity_excellent_max_spread_pct=float(os.getenv("LIQUIDITY_EXCELLENT_MAX_SPREAD_PCT", "12.0")),
            liquidity_excellent_min_volume=int(os.getenv("LIQUIDITY_EXCELLENT_MIN_VOLUME", "10")),
            min_historical_quarters=int(os.getenv("MIN_HISTORICAL_QUARTERS", "4")),
            max_historical_quarters=int(os.getenv("MAX_HISTORICAL_QUARTERS", "12")),
            min_market_cap_millions=float(os.getenv("MIN_MARKET_CAP_MILLIONS", "1000.0")),
            # Minimum DTE floor (Feb 2026)
            min_dte=int(os.getenv("MIN_DTE", "3")),
            # Weekly options filter (opt-in, default OFF)
            require_weekly_options=os.getenv("REQUIRE_WEEKLY_OPTIONS", "false").lower() == "true",
        )

        # Rate limits
        rate_limits = RateLimitConfig.from_env()

        # Strategy configuration
        strategy = StrategyConfig(
            target_delta_short=float(os.getenv("TARGET_DELTA_SHORT", "0.25")),
            target_delta_long=float(os.getenv("TARGET_DELTA_LONG", "0.20")),
            spread_width_high_price=float(os.getenv("SPREAD_WIDTH_HIGH_PRICE", "5.0")),
            spread_width_low_price=float(os.getenv("SPREAD_WIDTH_LOW_PRICE", "3.0")),
            spread_width_threshold=float(os.getenv("SPREAD_WIDTH_THRESHOLD", "20.0")),
            min_credit_per_spread=float(os.getenv("MIN_CREDIT_PER_SPREAD", "0.20")),
            min_reward_risk=float(os.getenv("MIN_REWARD_RISK", "0.25")),
            risk_budget_per_trade=float(os.getenv("RISK_BUDGET_PER_TRADE", "250000.0")),
            max_contracts=int(os.getenv("MAX_CONTRACTS", "100")),
            max_loss_warn_threshold=float(os.getenv("MAX_LOSS_WARN_THRESHOLD", "50000.0")),
            sizing_base_cap=int(os.getenv("SIZING_BASE_CAP", "100")),
            sizing_default_cap=int(os.getenv("SIZING_DEFAULT_CAP", "50")),
            sizing_trr_high_cap=int(os.getenv("SIZING_TRR_HIGH_CAP", "50")),
            sizing_compound_risk_cap=int(os.getenv("SIZING_COMPOUND_RISK_CAP", "25")),
            sizing_reduction_factor=float(os.getenv("SIZING_REDUCTION_FACTOR", "0.5")),
            use_kelly_sizing=os.getenv("USE_KELLY_SIZING", "true").lower() == "true",
            kelly_fraction=float(os.getenv("KELLY_FRACTION", "0.25")),
            kelly_min_contracts=int(os.getenv("KELLY_MIN_CONTRACTS", "1")),
            commission_per_contract=float(os.getenv("COMMISSION_PER_CONTRACT", "0.30")),
        )

        # Scan scoring configuration
        scan = ScanConfig()

        # Algorithms
        algorithms = AlgorithmConfig(
            use_interpolated_move=os.getenv("USE_INTERPOLATED_MOVE", "true").lower() == "true",
            use_enhanced_skew=os.getenv("USE_ENHANCED_SKEW", "true").lower() == "true",
            use_enhanced_consistency=os.getenv("USE_ENHANCED_CONSISTENCY", "true").lower() == "true",
            skew_min_points=int(os.getenv("SKEW_MIN_POINTS", "5")),
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

        # Telegram (optional). `or None` rather than a "" default so a key that
        # is present-but-blank in .env reads as absent, not as configured.
        telegram = TelegramConfig(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        )

        return cls(
            api=api,
            cache=cache,
            thresholds=thresholds,
            strategy=strategy,
            scan=scan,
            rate_limits=rate_limits,
            algorithms=algorithms,
            logging=logging,
            telegram=telegram,
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

        # Threshold validation - range checks
        for name, value in [
            ("vrp_excellent", self.thresholds.vrp_excellent),
            ("vrp_good", self.thresholds.vrp_good),
            ("vrp_marginal", self.thresholds.vrp_marginal),
        ]:
            if value < 0:
                errors.append(f"{name} ({value}) must not be negative")
            elif value < 0.1 or value > 50:
                errors.append(
                    f"{name} ({value}) outside reasonable range (0.1 to 50)"
                )

        # Threshold ordering validation
        if self.thresholds.vrp_excellent <= self.thresholds.vrp_good:
            errors.append(
                f"vrp_excellent ({self.thresholds.vrp_excellent}) must be > vrp_good ({self.thresholds.vrp_good})"
            )

        if self.thresholds.vrp_good <= self.thresholds.vrp_marginal:
            errors.append(
                f"vrp_good ({self.thresholds.vrp_good}) must be > vrp_marginal ({self.thresholds.vrp_marginal})"
            )

        # Rate limits
        if self.rate_limits.finnhub_per_minute <= 0:
            errors.append("finnhub_per_minute must be > 0")
        if self.rate_limits.twelve_data_daily_ceiling <= 0:
            errors.append("twelve_data_daily_ceiling must be > 0")

        return errors


# The process-global `get_config()` / `reset_config()` singleton was removed
# 2026-09-14: nothing called either. Entry points build `Config.from_env()` and
# hand it to `Container`, which validates it (src/config/validation.py).
