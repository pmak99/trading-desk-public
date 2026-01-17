"""
Configuration and timezone handling for IV Crush 5.0.

CRITICAL: All times are Eastern Time (ET). Cloud Run defaults to UTC.
"""

import os
import json
from datetime import datetime
from typing import Optional
import pytz

MARKET_TZ = pytz.timezone('America/New_York')

def now_et() -> datetime:
    """Get current time in Eastern timezone."""
    return datetime.now(MARKET_TZ)

def today_et() -> str:
    """Get today's date in Eastern as YYYY-MM-DD."""
    return now_et().strftime('%Y-%m-%d')

# Market half-days (close at 1 PM ET)
HALF_DAYS = {
    "2025-07-03",   # Day before July 4th
    "2025-11-28",   # Day after Thanksgiving
    "2025-12-24",   # Christmas Eve
    "2026-07-02",   # Day before July 4th (2026)
    "2026-11-27",   # Day after Thanksgiving (2026)
    "2026-12-24",   # Christmas Eve (2026)
}

def is_half_day(date_str: str = None) -> bool:
    """Check if a date is a market half-day."""
    if date_str is None:
        date_str = today_et()
    return date_str in HALF_DAYS


class Settings:
    """Application settings."""

    # VRP thresholds - BALANCED mode (matching 2.0 default)
    VRP_EXCELLENT = 1.8
    VRP_GOOD = 1.4
    VRP_MARGINAL = 1.2
    VRP_DISCOVERY = 1.4  # Aligned with GOOD tier - triggers alerts on tradeable opportunities

    # API budget limits
    PERPLEXITY_DAILY_LIMIT = 40
    PERPLEXITY_MONTHLY_BUDGET = 5.00

    # Cache TTLs (hours)
    CACHE_TTL_PRE_MARKET = 8
    CACHE_TTL_INTRADAY = 3

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
        self._load_secrets()
        return self._secrets.get('TRADIER_API_KEY', '')

    @property
    def alpha_vantage_key(self) -> str:
        self._load_secrets()
        return self._secrets.get('ALPHA_VANTAGE_KEY', '')

    @property
    def perplexity_api_key(self) -> str:
        self._load_secrets()
        return self._secrets.get('PERPLEXITY_API_KEY', '')

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
        self._load_secrets()
        return self._secrets.get('API_KEY', '')

    @property
    def telegram_webhook_secret(self) -> str:
        self._load_secrets()
        return self._secrets.get('TELEGRAM_WEBHOOK_SECRET', '')

    @property
    def twelve_data_key(self) -> str:
        self._load_secrets()
        return self._secrets.get('TWELVE_DATA_KEY', '')

    @property
    def account_size(self) -> int:
        """Get account size from secrets or environment (default 100k)."""
        self._load_secrets()
        size_str = self._secrets.get('ACCOUNT_SIZE', '100000') if self._secrets else '100000'
        try:
            return int(size_str)
        except (ValueError, TypeError):
            return 100000

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


settings = Settings()
