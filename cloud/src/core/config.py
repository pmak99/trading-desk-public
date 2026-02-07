"""
Configuration and timezone handling for IV Crush 5.0.

CRITICAL: All times are Eastern Time (ET). Cloud Run defaults to UTC.
Timezone utilities imported from common/timezone.py.
VRP thresholds imported from common/constants.py.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

# Ensure common/ is importable
_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.timezone import MARKET_TZ, now_et, today_et, HALF_DAYS, is_half_day  # noqa: E402
from common.constants import (  # noqa: E402
    VRP_EXCELLENT as _VRP_EXCELLENT,
    VRP_GOOD as _VRP_GOOD,
    VRP_MARGINAL as _VRP_MARGINAL,
    PERPLEXITY_DAILY_LIMIT as _PERPLEXITY_DAILY_LIMIT,
    PERPLEXITY_MONTHLY_BUDGET as _PERPLEXITY_MONTHLY_BUDGET,
)


class Settings:
    """Application settings."""

    # VRP thresholds - BALANCED mode (from common/constants.py)
    VRP_EXCELLENT = _VRP_EXCELLENT
    VRP_GOOD = _VRP_GOOD
    VRP_MARGINAL = _VRP_MARGINAL
    VRP_DISCOVERY = _VRP_EXCELLENT  # Aligned with EXCELLENT tier

    # API budget limits (from common/constants.py)
    PERPLEXITY_DAILY_LIMIT = _PERPLEXITY_DAILY_LIMIT
    PERPLEXITY_MONTHLY_BUDGET = _PERPLEXITY_MONTHLY_BUDGET

    # Cache TTLs (hours)
    CACHE_TTL_PRE_MARKET = 8
    CACHE_TTL_INTRADAY = 3

    # Position sizing defaults
    DEFAULT_POSITION_SIZE = 10  # Contracts for liquidity tier calculation

    # Weekly options filter (opt-in, default OFF)
    # When enabled, filters out tickers without weekly options
    # Weekly options have better liquidity and tighter spreads
    REQUIRE_WEEKLY_OPTIONS = False

    def __init__(self):
        self._secrets: Optional[dict] = None

    def _load_secrets(self):
        """Load secrets from env vars, SECRETS JSON blob, or Secret Manager.

        Priority:
        1. Individual env vars (for local development)
        2. SECRETS JSON blob (for Docker/Cloud Run)
        3. GCP Secret Manager (for production fallback)
        """
        if self._secrets:
            return

        # Priority 1: Check for individual env vars (local development)
        individual_keys = {
            'TRADIER_API_KEY': os.environ.get('TRADIER_API_KEY'),
            'ALPHA_VANTAGE_KEY': os.environ.get('ALPHA_VANTAGE_KEY'),
            'PERPLEXITY_API_KEY': os.environ.get('PERPLEXITY_API_KEY'),
            'TELEGRAM_BOT_TOKEN': os.environ.get('TELEGRAM_BOT_TOKEN'),
            'TELEGRAM_CHAT_ID': os.environ.get('TELEGRAM_CHAT_ID'),
            'API_KEY': os.environ.get('API_KEY'),
            'TELEGRAM_WEBHOOK_SECRET': os.environ.get('TELEGRAM_WEBHOOK_SECRET'),
            'TWELVE_DATA_KEY': os.environ.get('TWELVE_DATA_KEY'),
        }

        # If any individual key is set, use individual env vars
        if any(individual_keys.values()):
            self._secrets = {k: v or '' for k, v in individual_keys.items()}
            return

        # Priority 2: SECRETS JSON blob
        secrets_json = os.environ.get('SECRETS')
        if secrets_json:
            try:
                self._secrets = json.loads(secrets_json)
                return
            except json.JSONDecodeError as e:
                # Log error but continue to Secret Manager fallback
                # Import log here to avoid circular import
                try:
                    from .logging import log
                    log("error", "Invalid SECRETS JSON, falling back to Secret Manager",
                        error=str(e))
                except ImportError:
                    print(f"WARNING: Invalid SECRETS JSON: {e}")
                # Fall through to Secret Manager

        # Priority 3: Fallback to Secret Manager (production)
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            project = os.environ.get('GOOGLE_CLOUD_PROJECT', 'your-gcp-project')
            name = f"projects/{project}/secrets/trading-desk-secrets/versions/latest"
            response = client.access_secret_version(request={"name": name})
            self._secrets = json.loads(response.payload.data.decode("UTF-8"))
        except Exception as e:
            # Log the exception type for debugging (don't log full message which may contain secrets)
            print(f"WARNING: Secret Manager unavailable ({type(e).__name__}), using empty secrets")
            self._secrets = {}

    @property
    def tradier_api_key(self) -> str:
        """Tradier API key. Empty string is treated as unconfigured."""
        self._load_secrets()
        key = self._secrets.get('TRADIER_API_KEY', '')
        # Empty string is as bad as None for API authentication
        if not key or not key.strip():
            import logging
            logging.getLogger(__name__).warning("TRADIER_API_KEY not configured or empty")
            return ''
        return key.strip()

    @property
    def alpha_vantage_key(self) -> str:
        """Alpha Vantage API key. Empty string is treated as unconfigured."""
        self._load_secrets()
        key = self._secrets.get('ALPHA_VANTAGE_KEY', '')
        if not key or not key.strip():
            import logging
            logging.getLogger(__name__).warning("ALPHA_VANTAGE_KEY not configured or empty")
            return ''
        return key.strip()

    @property
    def perplexity_api_key(self) -> str:
        """Perplexity API key. Empty string is treated as unconfigured."""
        self._load_secrets()
        key = self._secrets.get('PERPLEXITY_API_KEY', '')
        if not key or not key.strip():
            import logging
            logging.getLogger(__name__).warning("PERPLEXITY_API_KEY not configured or empty")
            return ''
        return key.strip()

    @property
    def telegram_bot_token(self) -> str:
        self._load_secrets()
        return self._secrets.get('TELEGRAM_BOT_TOKEN', '')

    @property
    def telegram_chat_id(self) -> str:
        self._load_secrets()
        return self._secrets.get('TELEGRAM_CHAT_ID', '')

    @property
    def api_key(self) -> str:
        """API key for authenticating requests. Empty string treated as unconfigured."""
        self._load_secrets()
        key = self._secrets.get('API_KEY', '')
        # Empty string is as bad as None for API authentication
        return key.strip() if key else ''

    @property
    def telegram_webhook_secret(self) -> str:
        """Telegram webhook secret. Empty string treated as unconfigured."""
        self._load_secrets()
        key = self._secrets.get('TELEGRAM_WEBHOOK_SECRET', '')
        return key.strip() if key else ''

    @property
    def twelve_data_key(self) -> str:
        """Twelve Data API key. Empty string is treated as unconfigured."""
        self._load_secrets()
        key = self._secrets.get('TWELVE_DATA_KEY', '')
        if not key or not key.strip():
            import logging
            logging.getLogger(__name__).warning("TWELVE_DATA_KEY not configured or empty")
            return ''
        return key.strip()

    # Account size bounds (reasonable range for options trading)
    ACCOUNT_SIZE_MIN = 1_000          # $1,000 minimum
    ACCOUNT_SIZE_MAX = 100_000_000    # $100,000,000 maximum
    ACCOUNT_SIZE_DEFAULT = 100_000    # $100,000 default

    @property
    def account_size(self) -> int:
        """Get account size from secrets or environment (default 100k).

        Must be between $1,000 and $100,000,000. Values outside this range
        are likely misconfiguration (e.g., cents instead of dollars, or
        a typo adding extra zeros). Falls back to $100k default.
        Values above $10M log a warning but are accepted.
        """
        self._load_secrets()
        size_str = self._secrets.get('ACCOUNT_SIZE', str(self.ACCOUNT_SIZE_DEFAULT)) if self._secrets else str(self.ACCOUNT_SIZE_DEFAULT)
        try:
            size = int(size_str)
            if size < self.ACCOUNT_SIZE_MIN or size > self.ACCOUNT_SIZE_MAX:
                from .logging import log
                log("error", "ACCOUNT_SIZE out of bounds, using default",
                    size=size, min=self.ACCOUNT_SIZE_MIN, max=self.ACCOUNT_SIZE_MAX,
                    default=self.ACCOUNT_SIZE_DEFAULT)
                return self.ACCOUNT_SIZE_DEFAULT
            if size > 10_000_000:
                from .logging import log
                log("warn", "ACCOUNT_SIZE above $10M - verify this is intentional",
                    size=size)
            return size
        except (ValueError, TypeError):
            return self.ACCOUNT_SIZE_DEFAULT

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        env = os.environ.get('ENV', os.environ.get('ENVIRONMENT', 'development'))
        return env.lower() in ('production', 'prod')

    @property
    def gcs_bucket(self) -> str:
        return os.environ.get('GCS_BUCKET', 'your-gcs-bucket')

    @property
    def grafana_graphite_url(self) -> str:
        """Grafana Cloud Graphite metrics endpoint."""
        # Check env var first (for local dev), then secrets
        env_val = os.environ.get('GRAFANA_GRAPHITE_URL')
        if env_val:
            return env_val
        self._load_secrets()
        return self._secrets.get('GRAFANA_GRAPHITE_URL', '') if self._secrets else ''

    @property
    def grafana_user(self) -> str:
        """Grafana Cloud instance ID."""
        env_val = os.environ.get('GRAFANA_USER')
        if env_val:
            return env_val
        self._load_secrets()
        return self._secrets.get('GRAFANA_USER', '') if self._secrets else ''

    @property
    def grafana_api_key(self) -> str:
        """Grafana Cloud API key."""
        env_val = os.environ.get('GRAFANA_API_KEY')
        if env_val:
            return env_val
        self._load_secrets()
        return self._secrets.get('GRAFANA_API_KEY', '') if self._secrets else ''

    @property
    def grafana_dashboard_url(self) -> str:
        """Grafana dashboard URL for notifications."""
        env_val = os.environ.get('GRAFANA_DASHBOARD_URL')
        if env_val:
            return env_val
        self._load_secrets()
        return self._secrets.get('GRAFANA_DASHBOARD_URL', '') if self._secrets else ''

    @property
    def require_weekly_options(self) -> bool:
        """Check if weekly options filter is enabled (opt-in)."""
        return os.environ.get('REQUIRE_WEEKLY_OPTIONS', 'false').lower() == 'true'

    @property
    def DB_PATH(self) -> str:
        """Database path - uses temp file in tests, data dir in production."""
        env_path = os.environ.get('DB_PATH')
        if env_path:
            return env_path

        # Default to data/ivcrush.db, but ensure directory exists
        default_path = 'data/ivcrush.db'
        data_dir = os.path.dirname(default_path)
        if data_dir and not os.path.exists(data_dir):
            import tempfile
            # Return temp path for test environments
            return os.path.join(tempfile.gettempdir(), 'ivcrush_test.db')
        return default_path

    def validate_required_config(self) -> list[str]:
        """Validate that required configuration is present.

        Returns:
            List of missing/invalid configuration errors.
            Empty list means all required config is valid.

        This should be called at startup to fail fast on misconfiguration.
        """
        errors = []

        # Required API keys
        if not self.tradier_api_key:
            errors.append("TRADIER_API_KEY is required but not configured")
        if not self.alpha_vantage_key:
            errors.append("ALPHA_VANTAGE_KEY is required but not configured")

        # Required for authentication
        if not self.api_key:
            errors.append("API_KEY is required for API authentication")

        # Required for Telegram notifications
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN is required for notifications")
        if not self.telegram_chat_id:
            errors.append("TELEGRAM_CHAT_ID is required for notifications")
        if not self.telegram_webhook_secret:
            errors.append("TELEGRAM_WEBHOOK_SECRET is required for webhook security")

        return errors

    def validate_or_warn(self):
        """Validate config and log warnings for missing optional config.

        This is a gentler validation that logs warnings but doesn't fail.
        Use validate_required_config() for strict startup validation.
        """
        import logging
        logger = logging.getLogger(__name__)

        # Check optional but recommended config
        if not self.perplexity_api_key:
            logger.warning("PERPLEXITY_API_KEY not configured - sentiment analysis disabled")
        if not self.twelve_data_key:
            logger.warning("TWELVE_DATA_KEY not configured - historical backfill disabled")
        if not self.grafana_graphite_url:
            logger.info("Grafana metrics not configured (optional)")


settings = Settings()
