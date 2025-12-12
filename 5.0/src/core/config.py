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

    # VRP thresholds (from CLAUDE.md)
    VRP_EXCELLENT = 7.0
    VRP_GOOD = 4.0
    VRP_MARGINAL = 1.5
    VRP_DISCOVERY = 3.0  # For priming/whisper

    # API budget limits
    PERPLEXITY_DAILY_LIMIT = 40
    PERPLEXITY_MONTHLY_BUDGET = 5.00

    # Cache TTLs (hours)
    CACHE_TTL_PRE_MARKET = 8
    CACHE_TTL_INTRADAY = 3

    def __init__(self):
        self._secrets: Optional[dict] = None

    def _load_secrets(self):
        """Load secrets from env or Secret Manager."""
        if self._secrets:
            return

        secrets_json = os.environ.get('SECRETS')
        if secrets_json:
            self._secrets = json.loads(secrets_json)
            return

        # Fallback to Secret Manager
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            project = os.environ.get('GOOGLE_CLOUD_PROJECT', 'ivcrush-prod')
            name = f"projects/{project}/secrets/ivcrush-secrets/versions/latest"
            response = client.access_secret_version(request={"name": name})
            self._secrets = json.loads(response.payload.data.decode("UTF-8"))
        except Exception:
            self._secrets = {}

    @property
    def tradier_api_key(self) -> str:
        self._load_secrets()
        return self._secrets.get('TRADIER_API_KEY', '')

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
    def gcs_bucket(self) -> str:
        return os.environ.get('GCS_BUCKET', 'ivcrush-data')

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
