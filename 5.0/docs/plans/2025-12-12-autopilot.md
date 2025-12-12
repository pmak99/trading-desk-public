# IV Crush 5.0 Autopilot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a 24/7 cloud-native trading system that automates VRP analysis, sentiment collection, and Telegram/CLI notifications.

**Architecture:** Google Cloud Run (serverless) with single dispatcher pattern, SQLite synced via Cloud Storage, Telegram for notifications, dual-output formatters for Telegram and CLI.

**Tech Stack:** Python 3.11, FastAPI, httpx (async HTTP), SQLite, Google Cloud (Run, Storage, Scheduler, Secret Manager), Telegram Bot API, Grafana Cloud

---

## Phase 1: Project Setup

### Task 1.1: Initialize Project Structure

**Files:**
- Create: `5.0/pyproject.toml`
- Create: `5.0/requirements.txt`
- Create: `5.0/.gitignore`

**Step 1: Create pyproject.toml**

```toml
[project]
name = "ivcrush"
version = "5.0.0"
description = "IV Crush Autopilot Trading System"
requires-python = ">=3.11"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: Create requirements.txt**

```txt
# Web framework
fastapi==0.109.0
uvicorn[standard]==0.27.0

# HTTP client (async)
httpx==0.26.0

# Retry logic
tenacity==8.2.3

# Google Cloud
google-cloud-storage==2.14.0
google-cloud-secret-manager==2.18.0

# Timezone
pytz==2024.1

# Testing
pytest==7.4.4
pytest-asyncio==0.23.3
pytest-httpx==0.28.0

# Dev
python-dotenv==1.0.0
```

**Step 3: Create .gitignore**

```
__pycache__/
*.pyc
.env
*.db
venv/
.pytest_cache/
.coverage
```

**Step 4: Create directory structure**

```bash
mkdir -p 5.0/{src,tests,scripts}
mkdir -p 5.0/src/{core,domain,integrations,jobs,api,formatters}
touch 5.0/src/__init__.py
touch 5.0/src/{core,domain,integrations,jobs,api,formatters}/__init__.py
```

**Step 5: Commit**

```bash
git add 5.0/
git commit -m "chore: initialize 5.0 project structure"
```

---

### Task 1.2: Core Configuration Module

**Files:**
- Create: `5.0/src/core/config.py`
- Create: `5.0/tests/test_config.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_config.py
import pytest
from datetime import datetime
from src.core.config import now_et, today_et, is_half_day, Settings

def test_now_et_returns_eastern_time():
    """now_et() should return time in Eastern timezone."""
    result = now_et()
    assert result.tzinfo is not None
    assert "America/New_York" in str(result.tzinfo) or "EDT" in str(result) or "EST" in str(result)

def test_today_et_returns_date_string():
    """today_et() should return YYYY-MM-DD format."""
    result = today_et()
    assert len(result) == 10
    assert result[4] == '-' and result[7] == '-'

def test_is_half_day_christmas_eve():
    """Christmas Eve 2025 is a half day."""
    assert is_half_day("2025-12-24") is True

def test_is_half_day_normal_day():
    """Normal trading day is not a half day."""
    assert is_half_day("2025-12-12") is False

def test_settings_vrp_thresholds():
    """Settings should have correct VRP thresholds."""
    s = Settings()
    assert s.VRP_EXCELLENT == 7.0
    assert s.VRP_GOOD == 4.0
    assert s.VRP_MARGINAL == 1.5
    assert s.VRP_DISCOVERY == 3.0
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_config.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'src'"

**Step 3: Write minimal implementation**

```python
# 5.0/src/core/config.py
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


settings = Settings()
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_config.py -v`
Expected: PASS (5 passed)

**Step 5: Commit**

```bash
git add 5.0/src/core/config.py 5.0/tests/test_config.py
git commit -m "feat(core): add config module with timezone handling"
```

---

### Task 1.3: Structured Logging Module

**Files:**
- Create: `5.0/src/core/logging.py`
- Create: `5.0/tests/test_logging.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_logging.py
import json
import pytest
from io import StringIO
from unittest.mock import patch
from src.core.logging import log, set_request_id, get_request_id

def test_set_and_get_request_id():
    """Request ID should be settable and retrievable."""
    set_request_id("abc123")
    assert get_request_id() == "abc123"

def test_log_outputs_json(capsys):
    """log() should output valid JSON to stdout."""
    set_request_id("test123")
    log("info", "Test message", ticker="NVDA", vrp=5.2)

    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())

    assert data["level"] == "INFO"
    assert data["message"] == "Test message"
    assert data["ticker"] == "NVDA"
    assert data["vrp"] == 5.2
    assert data["request_id"] == "test123"

def test_log_excludes_secrets(capsys):
    """log() should filter out secret-like keys."""
    log("info", "Test", api_key="secret123", token="hidden")

    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())

    assert "api_key" not in data
    assert "token" not in data
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_logging.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/core/logging.py
"""
Structured JSON logging for IV Crush 5.0.

All logs are JSON for Cloud Logging and Grafana.
"""

import json
import uuid
from datetime import datetime
from contextvars import ContextVar
from typing import Any

from .config import now_et

_request_id: ContextVar[str] = ContextVar('request_id', default='')


def set_request_id(request_id: str = None) -> str:
    """Set request ID for current context."""
    if request_id is None:
        request_id = str(uuid.uuid4())[:8]
    _request_id.set(request_id)
    return request_id


def get_request_id() -> str:
    """Get request ID for current context."""
    rid = _request_id.get()
    if not rid:
        rid = str(uuid.uuid4())[:8]
        _request_id.set(rid)
    return rid


def log(level: str, message: str, **context: Any):
    """
    Log a structured JSON message.

    Args:
        level: Log level (debug, info, warn, error)
        message: Human-readable message
        **context: Additional key-value pairs
    """
    # Filter out secrets
    safe_context = {
        k: v for k, v in context.items()
        if v is not None
        and 'key' not in k.lower()
        and 'token' not in k.lower()
        and 'secret' not in k.lower()
    }

    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "timestamp_et": now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
        "level": level.upper(),
        "request_id": get_request_id(),
        "message": message,
        **safe_context
    }

    print(json.dumps(entry))
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_logging.py -v`
Expected: PASS (3 passed)

**Step 5: Commit**

```bash
git add 5.0/src/core/logging.py 5.0/tests/test_logging.py
git commit -m "feat(core): add structured JSON logging"
```

---

### Task 1.4: Module Exports (__init__.py files)

**Files:**
- Modify: `5.0/src/core/__init__.py`
- Modify: `5.0/src/domain/__init__.py`
- Modify: `5.0/src/integrations/__init__.py`
- Modify: `5.0/src/formatters/__init__.py`
- Modify: `5.0/src/jobs/__init__.py`

**Purpose:** Clean module exports for easier imports and IDE autocomplete.

**Implementation:**

```python
# 5.0/src/core/__init__.py
"""Core modules for IV Crush 5.0."""

from .config import settings, now_et, today_et, is_half_day, MARKET_TZ
from .logging import log, set_request_id, get_request_id
from .job_manager import JobManager, get_scheduled_job
from .budget import BudgetTracker

__all__ = [
    "settings",
    "now_et",
    "today_et",
    "is_half_day",
    "MARKET_TZ",
    "log",
    "set_request_id",
    "get_request_id",
    "JobManager",
    "get_scheduled_job",
    "BudgetTracker",
]
```

```python
# 5.0/src/domain/__init__.py
"""Domain logic for IV Crush trading system."""

from .vrp import calculate_vrp, get_vrp_tier
from .liquidity import classify_liquidity_tier
from .scoring import calculate_score, apply_sentiment_modifier
from .strategies import generate_strategies, Strategy
from .position_sizing import calculate_position_size, half_kelly
from .implied_move import calculate_implied_move, find_atm_straddle

__all__ = [
    "calculate_vrp",
    "get_vrp_tier",
    "classify_liquidity_tier",
    "calculate_score",
    "apply_sentiment_modifier",
    "generate_strategies",
    "Strategy",
    "calculate_position_size",
    "half_kelly",
    "calculate_implied_move",
    "find_atm_straddle",
]
```

```python
# 5.0/src/integrations/__init__.py
"""External API integrations."""

from .tradier import TradierClient
from .perplexity import PerplexityClient, parse_sentiment_response
from .alphavantage import AlphaVantageClient
from .yahoo import YahooClient
from .telegram import TelegramClient

__all__ = [
    "TradierClient",
    "PerplexityClient",
    "parse_sentiment_response",
    "AlphaVantageClient",
    "YahooClient",
    "TelegramClient",
]
```

```python
# 5.0/src/formatters/__init__.py
"""Output formatters for Telegram and CLI."""

from .telegram import format_ticker_line, format_digest, format_alert
from .cli import format_ticker_line_cli, format_digest_cli, format_analyze_cli

__all__ = [
    "format_ticker_line",
    "format_digest",
    "format_alert",
    "format_ticker_line_cli",
    "format_digest_cli",
    "format_analyze_cli",
]
```

**Step: Commit**

```bash
git add 5.0/src/*/__init__.py
git commit -m "chore: add module exports to __init__.py files"
```

---

## Phase 2: Domain Logic (Port from 2.0)

> **FIXES APPLIED:** Added Task 2.4 (Strategies), Task 2.5 (Position Sizing), Task 2.6 (Implied Move Calculator)

### Task 2.1: VRP Calculator

**Files:**
- Create: `5.0/src/domain/vrp.py`
- Create: `5.0/tests/test_vrp.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_vrp.py
import pytest
from src.domain.vrp import calculate_vrp, get_vrp_tier

def test_calculate_vrp_basic():
    """VRP = implied_move / historical_mean."""
    result = calculate_vrp(
        implied_move_pct=8.0,
        historical_moves=[4.0, 5.0, 3.0, 4.0]  # mean = 4.0
    )
    assert result["vrp_ratio"] == 2.0
    assert result["historical_mean"] == 4.0

def test_calculate_vrp_excellent():
    """VRP >= 7.0 is EXCELLENT tier."""
    result = calculate_vrp(
        implied_move_pct=14.0,
        historical_moves=[2.0, 2.0, 2.0, 2.0]  # mean = 2.0, VRP = 7.0
    )
    assert result["tier"] == "EXCELLENT"

def test_calculate_vrp_good():
    """VRP >= 4.0 is GOOD tier."""
    result = calculate_vrp(
        implied_move_pct=8.0,
        historical_moves=[2.0, 2.0, 2.0, 2.0]  # mean = 2.0, VRP = 4.0
    )
    assert result["tier"] == "GOOD"

def test_calculate_vrp_marginal():
    """VRP >= 1.5 is MARGINAL tier."""
    result = calculate_vrp(
        implied_move_pct=3.0,
        historical_moves=[2.0, 2.0, 2.0, 2.0]  # mean = 2.0, VRP = 1.5
    )
    assert result["tier"] == "MARGINAL"

def test_calculate_vrp_skip():
    """VRP < 1.5 is SKIP tier."""
    result = calculate_vrp(
        implied_move_pct=2.0,
        historical_moves=[2.0, 2.0, 2.0, 2.0]  # mean = 2.0, VRP = 1.0
    )
    assert result["tier"] == "SKIP"

def test_calculate_vrp_insufficient_data():
    """Need at least 4 quarters of data."""
    result = calculate_vrp(
        implied_move_pct=8.0,
        historical_moves=[4.0, 5.0]  # Only 2 quarters
    )
    assert result["error"] == "insufficient_data"

def test_get_vrp_tier():
    """get_vrp_tier returns correct tier for ratio."""
    assert get_vrp_tier(7.5) == "EXCELLENT"
    assert get_vrp_tier(5.0) == "GOOD"
    assert get_vrp_tier(2.0) == "MARGINAL"
    assert get_vrp_tier(1.0) == "SKIP"
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_vrp.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/domain/vrp.py
"""
VRP (Volatility Risk Premium) Calculator.

Ported from 2.0/src/application/metrics/vrp.py with simplified interface.
"""

from typing import List, Dict, Any
import statistics

# Thresholds from CLAUDE.md
VRP_EXCELLENT = 7.0
VRP_GOOD = 4.0
VRP_MARGINAL = 1.5
MIN_QUARTERS = 4


def get_vrp_tier(vrp_ratio: float) -> str:
    """Get VRP tier from ratio."""
    if vrp_ratio >= VRP_EXCELLENT:
        return "EXCELLENT"
    elif vrp_ratio >= VRP_GOOD:
        return "GOOD"
    elif vrp_ratio >= VRP_MARGINAL:
        return "MARGINAL"
    else:
        return "SKIP"


def calculate_vrp(
    implied_move_pct: float,
    historical_moves: List[float],
) -> Dict[str, Any]:
    """
    Calculate VRP ratio and tier.

    Args:
        implied_move_pct: Implied move from ATM straddle (e.g., 8.5 for 8.5%)
        historical_moves: List of historical move percentages (absolute values)

    Returns:
        Dict with vrp_ratio, tier, historical_mean, consistency, or error
    """
    # Validate data
    if len(historical_moves) < MIN_QUARTERS:
        return {
            "error": "insufficient_data",
            "message": f"Need {MIN_QUARTERS}+ quarters, got {len(historical_moves)}"
        }

    # Calculate mean
    historical_mean = statistics.mean(historical_moves)

    if historical_mean <= 0:
        return {
            "error": "invalid_data",
            "message": f"Invalid historical mean: {historical_mean}"
        }

    # Calculate VRP ratio
    vrp_ratio = implied_move_pct / historical_mean

    # Calculate consistency (MAD)
    median = statistics.median(historical_moves)
    mad = statistics.median([abs(x - median) for x in historical_moves])
    consistency = mad / median if median > 0 else 999

    return {
        "vrp_ratio": round(vrp_ratio, 2),
        "tier": get_vrp_tier(vrp_ratio),
        "implied_move_pct": implied_move_pct,
        "historical_mean": round(historical_mean, 2),
        "consistency": round(consistency, 2),
        "sample_size": len(historical_moves),
    }
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_vrp.py -v`
Expected: PASS (7 passed)

**Step 5: Commit**

```bash
git add 5.0/src/domain/vrp.py 5.0/tests/test_vrp.py
git commit -m "feat(domain): add VRP calculator ported from 2.0"
```

---

### Task 2.2: Liquidity Tier Classification

**Files:**
- Create: `5.0/src/domain/liquidity.py`
- Create: `5.0/tests/test_liquidity.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_liquidity.py
import pytest
from src.domain.liquidity import classify_liquidity_tier

def test_liquidity_excellent():
    """EXCELLENT: OI >= 5x, spread <= 8%."""
    tier = classify_liquidity_tier(oi=1000, spread_pct=5.0, position_size=100)
    assert tier == "EXCELLENT"

def test_liquidity_good():
    """GOOD: OI 2-5x, spread 8-12%."""
    tier = classify_liquidity_tier(oi=300, spread_pct=10.0, position_size=100)
    assert tier == "GOOD"

def test_liquidity_warning():
    """WARNING: OI 1-2x, spread 12-15%."""
    tier = classify_liquidity_tier(oi=150, spread_pct=13.0, position_size=100)
    assert tier == "WARNING"

def test_liquidity_reject_low_oi():
    """REJECT: OI < 1x position."""
    tier = classify_liquidity_tier(oi=50, spread_pct=5.0, position_size=100)
    assert tier == "REJECT"

def test_liquidity_reject_wide_spread():
    """REJECT: spread > 15%."""
    tier = classify_liquidity_tier(oi=1000, spread_pct=20.0, position_size=100)
    assert tier == "REJECT"

def test_liquidity_final_tier_is_worse():
    """Final tier = worse of (OI tier, Spread tier)."""
    # Excellent OI but warning spread -> WARNING
    tier = classify_liquidity_tier(oi=1000, spread_pct=13.0, position_size=100)
    assert tier == "WARNING"
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_liquidity.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/domain/liquidity.py
"""
Liquidity tier classification.

Ported from 2.0/src/application/metrics/liquidity_scorer.py with simplified interface.

4-Tier System (from CLAUDE.md):
- EXCELLENT: OI >= 5x position, spread <= 8%
- GOOD: OI 2-5x position, spread 8-12%
- WARNING: OI 1-2x position, spread 12-15%
- REJECT: OI < 1x position, spread > 15%

Final tier = worse of (OI tier, Spread tier)
"""

# Spread thresholds
SPREAD_EXCELLENT = 8.0   # <= 8%
SPREAD_GOOD = 12.0       # <= 12%
SPREAD_WARNING = 15.0    # <= 15%
# > 15% = REJECT

TIER_ORDER = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}


def classify_liquidity_tier(
    oi: int,
    spread_pct: float,
    position_size: int = 100,
) -> str:
    """
    Classify liquidity into 4-tier system.

    Args:
        oi: Open interest
        spread_pct: Bid-ask spread as percentage of mid
        position_size: Expected position size in contracts

    Returns:
        "EXCELLENT", "GOOD", "WARNING", or "REJECT"
    """
    # OI tier (relative to position size)
    oi_ratio = oi / position_size if position_size > 0 else 0

    if oi_ratio >= 5:
        oi_tier = "EXCELLENT"
    elif oi_ratio >= 2:
        oi_tier = "GOOD"
    elif oi_ratio >= 1:
        oi_tier = "WARNING"
    else:
        oi_tier = "REJECT"

    # Spread tier
    if spread_pct <= SPREAD_EXCELLENT:
        spread_tier = "EXCELLENT"
    elif spread_pct <= SPREAD_GOOD:
        spread_tier = "GOOD"
    elif spread_pct <= SPREAD_WARNING:
        spread_tier = "WARNING"
    else:
        spread_tier = "REJECT"

    # Final tier is the worse of the two
    return min([oi_tier, spread_tier], key=lambda t: TIER_ORDER[t])
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_liquidity.py -v`
Expected: PASS (6 passed)

**Step 5: Commit**

```bash
git add 5.0/src/domain/liquidity.py 5.0/tests/test_liquidity.py
git commit -m "feat(domain): add liquidity tier classification"
```

---

### Task 2.3: Composite Scoring

**Files:**
- Create: `5.0/src/domain/scoring.py`
- Create: `5.0/tests/test_scoring.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_scoring.py
import pytest
from src.domain.scoring import calculate_score, apply_sentiment_modifier

def test_calculate_score_weights():
    """Score = VRP(55%) + Move(25%) + Liquidity(20%)."""
    score = calculate_score(
        vrp_ratio=4.0,       # VRP score normalized
        implied_move_pct=5.0,
        liquidity_tier="EXCELLENT"
    )
    assert 0 <= score <= 100

def test_calculate_score_excellent_vrp():
    """EXCELLENT VRP should score high."""
    score = calculate_score(
        vrp_ratio=7.5,
        implied_move_pct=5.0,
        liquidity_tier="EXCELLENT"
    )
    assert score >= 80

def test_calculate_score_reject_liquidity():
    """REJECT liquidity should cap score low."""
    score = calculate_score(
        vrp_ratio=7.5,
        implied_move_pct=5.0,
        liquidity_tier="REJECT"
    )
    assert score < 50

def test_apply_sentiment_modifier_bullish():
    """Strong bullish adds +12%."""
    modified = apply_sentiment_modifier(80, sentiment_score=0.8)
    assert modified == 89.6  # 80 * 1.12

def test_apply_sentiment_modifier_bearish():
    """Strong bearish subtracts -12%."""
    modified = apply_sentiment_modifier(80, sentiment_score=-0.8)
    assert modified == 70.4  # 80 * 0.88

def test_apply_sentiment_modifier_neutral():
    """Neutral sentiment has no effect."""
    modified = apply_sentiment_modifier(80, sentiment_score=0.0)
    assert modified == 80.0
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_scoring.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/domain/scoring.py
"""
Composite scoring system for IV Crush 5.0.

2.0 Score = VRP (55%) + Move Difficulty (25%) + Liquidity (20%)
4.0 Score = 2.0 Score × (1 + Sentiment Modifier)

Sentiment Modifiers:
- Strong Bullish (>= +0.6): +12%
- Bullish (+0.2 to +0.6): +7%
- Neutral (-0.2 to +0.2): 0%
- Bearish (-0.6 to -0.2): -7%
- Strong Bearish (<= -0.6): -12%
"""

from typing import Dict

# Scoring weights
WEIGHT_VRP = 0.55
WEIGHT_MOVE = 0.25
WEIGHT_LIQUIDITY = 0.20

# Liquidity tier scores
LIQUIDITY_SCORES = {
    "EXCELLENT": 100,
    "GOOD": 80,
    "WARNING": 50,
    "REJECT": 20,
}


def calculate_score(
    vrp_ratio: float,
    implied_move_pct: float,
    liquidity_tier: str,
) -> float:
    """
    Calculate composite score (0-100).

    Args:
        vrp_ratio: VRP ratio (higher is better)
        implied_move_pct: Implied move percentage
        liquidity_tier: EXCELLENT/GOOD/WARNING/REJECT

    Returns:
        Composite score 0-100
    """
    # VRP score: normalize to 0-100 (7x = 100, 1x = 14)
    vrp_score = min(100, (vrp_ratio / 7.0) * 100)

    # Move difficulty score: easier moves score higher
    # 5% move = 100, 15% move = 33
    move_score = min(100, (5.0 / max(implied_move_pct, 1.0)) * 100)

    # Liquidity score
    liq_score = LIQUIDITY_SCORES.get(liquidity_tier, 20)

    # Weighted composite
    score = (
        vrp_score * WEIGHT_VRP +
        move_score * WEIGHT_MOVE +
        liq_score * WEIGHT_LIQUIDITY
    )

    return round(score, 1)


def apply_sentiment_modifier(
    base_score: float,
    sentiment_score: float,
) -> float:
    """
    Apply sentiment modifier to base score.

    Args:
        base_score: 2.0 composite score
        sentiment_score: -1.0 to +1.0

    Returns:
        Modified score (4.0 score)
    """
    # Determine modifier based on sentiment strength
    if sentiment_score >= 0.6:
        modifier = 0.12  # Strong bullish
    elif sentiment_score >= 0.2:
        modifier = 0.07  # Bullish
    elif sentiment_score <= -0.6:
        modifier = -0.12  # Strong bearish
    elif sentiment_score <= -0.2:
        modifier = -0.07  # Bearish
    else:
        modifier = 0.0  # Neutral

    return round(base_score * (1 + modifier), 1)
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_scoring.py -v`
Expected: PASS (6 passed)

**Step 5: Commit**

```bash
git add 5.0/src/domain/scoring.py 5.0/tests/test_scoring.py
git commit -m "feat(domain): add composite scoring with sentiment modifier"
```

---

### Task 2.4: Strategy Generator

**Files:**
- Create: `5.0/src/domain/strategies.py`
- Create: `5.0/tests/test_strategies.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_strategies.py
import pytest
from src.domain.strategies import generate_strategies, Strategy

def test_generate_bull_put_spread():
    """Bullish direction generates bull put spread."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="BULLISH",
        liquidity_tier="EXCELLENT"
    )

    bull_put = next((s for s in strategies if s.name == "Bull Put Spread"), None)
    assert bull_put is not None
    assert bull_put.short_strike < 135.0  # Below current price
    assert bull_put.long_strike < bull_put.short_strike
    assert bull_put.max_profit > 0
    assert bull_put.pop >= 60  # Probability of profit

def test_generate_bear_call_spread():
    """Bearish direction generates bear call spread."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="BEARISH",
        liquidity_tier="EXCELLENT"
    )

    bear_call = next((s for s in strategies if s.name == "Bear Call Spread"), None)
    assert bear_call is not None
    assert bear_call.short_strike > 135.0  # Above current price

def test_generate_iron_condor_neutral():
    """Neutral direction generates iron condor."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="NEUTRAL",
        liquidity_tier="EXCELLENT"
    )

    ic = next((s for s in strategies if s.name == "Iron Condor"), None)
    assert ic is not None

def test_reject_liquidity_no_strategies():
    """REJECT liquidity returns empty list."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="BULLISH",
        liquidity_tier="REJECT"
    )
    assert strategies == []

def test_strategy_has_required_fields():
    """Strategy has all required fields."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="BULLISH",
        liquidity_tier="GOOD"
    )

    assert len(strategies) > 0
    s = strategies[0]
    assert hasattr(s, 'name')
    assert hasattr(s, 'max_profit')
    assert hasattr(s, 'max_risk')
    assert hasattr(s, 'pop')
    assert hasattr(s, 'description')
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_strategies.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/domain/strategies.py
"""
Strategy generator for IV Crush trades.

Generates credit spread strategies based on direction and liquidity.
"""

from dataclasses import dataclass
from typing import List, Optional
import math


@dataclass
class Strategy:
    """Option strategy with P/L characteristics."""
    name: str
    description: str
    short_strike: float
    long_strike: float
    expiration: str
    max_profit: float
    max_risk: float
    pop: int  # Probability of profit (0-100)
    breakeven: float

    @property
    def risk_reward(self) -> float:
        """Risk/reward ratio."""
        return self.max_risk / self.max_profit if self.max_profit > 0 else 999


def _round_strike(price: float, direction: str = "down") -> float:
    """Round to nearest standard strike."""
    if price < 50:
        increment = 2.5
    elif price < 200:
        increment = 5.0
    else:
        increment = 10.0

    if direction == "down":
        return math.floor(price / increment) * increment
    else:
        return math.ceil(price / increment) * increment


def generate_strategies(
    ticker: str,
    price: float,
    implied_move_pct: float,
    direction: str,
    liquidity_tier: str,
    expiration: str = "",
) -> List[Strategy]:
    """
    Generate option strategies for ticker.

    Args:
        ticker: Stock symbol
        price: Current stock price
        implied_move_pct: Expected move percentage
        direction: BULLISH, BEARISH, or NEUTRAL
        liquidity_tier: EXCELLENT, GOOD, WARNING, or REJECT
        expiration: Option expiration date

    Returns:
        List of Strategy objects, sorted by POP descending
    """
    # Never trade REJECT liquidity
    if liquidity_tier == "REJECT":
        return []

    strategies = []
    implied_move = price * (implied_move_pct / 100)

    # Calculate strike distances based on implied move
    # Short strike at 1x implied move, long strike at 1.5x
    short_distance = implied_move
    spread_width = implied_move * 0.5

    if direction == "BULLISH":
        # Bull Put Spread: sell put below price, buy lower put
        short_strike = _round_strike(price - short_distance, "down")
        long_strike = _round_strike(short_strike - spread_width, "down")

        # Estimate credit (simplified)
        credit = spread_width * 0.35  # ~35% of width
        max_risk = (short_strike - long_strike) - credit

        strategies.append(Strategy(
            name="Bull Put Spread",
            description=f"Sell {short_strike}P / Buy {long_strike}P",
            short_strike=short_strike,
            long_strike=long_strike,
            expiration=expiration,
            max_profit=credit * 100,
            max_risk=max_risk * 100,
            pop=68,  # ~1 std dev
            breakeven=short_strike - credit,
        ))

    elif direction == "BEARISH":
        # Bear Call Spread: sell call above price, buy higher call
        short_strike = _round_strike(price + short_distance, "up")
        long_strike = _round_strike(short_strike + spread_width, "up")

        credit = spread_width * 0.35
        max_risk = (long_strike - short_strike) - credit

        strategies.append(Strategy(
            name="Bear Call Spread",
            description=f"Sell {short_strike}C / Buy {long_strike}C",
            short_strike=short_strike,
            long_strike=long_strike,
            expiration=expiration,
            max_profit=credit * 100,
            max_risk=max_risk * 100,
            pop=68,
            breakeven=short_strike + credit,
        ))

    else:  # NEUTRAL
        # Iron Condor: bull put + bear call
        put_short = _round_strike(price - short_distance, "down")
        put_long = _round_strike(put_short - spread_width, "down")
        call_short = _round_strike(price + short_distance, "up")
        call_long = _round_strike(call_short + spread_width, "up")

        credit = spread_width * 0.5  # Both sides
        max_risk = spread_width - credit

        strategies.append(Strategy(
            name="Iron Condor",
            description=f"{put_long}P/{put_short}P - {call_short}C/{call_long}C",
            short_strike=put_short,  # Lower short strike for reference
            long_strike=call_short,  # Upper short strike
            expiration=expiration,
            max_profit=credit * 100,
            max_risk=max_risk * 100,
            pop=60,
            breakeven=put_short - credit,  # Lower breakeven
        ))

    # Sort by POP descending
    strategies.sort(key=lambda s: s.pop, reverse=True)

    return strategies
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_strategies.py -v`
Expected: PASS (5 passed)

**Step 5: Commit**

```bash
git add 5.0/src/domain/strategies.py 5.0/tests/test_strategies.py
git commit -m "feat(domain): add strategy generator for spreads and iron condors"
```

---

### Task 2.5: Position Sizing (Half-Kelly)

**Files:**
- Create: `5.0/src/domain/position_sizing.py`
- Create: `5.0/tests/test_position_sizing.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_position_sizing.py
import pytest
from src.domain.position_sizing import calculate_position_size, half_kelly

def test_half_kelly_formula():
    """Half-Kelly = 0.5 * (bp - q) / b where b=odds, p=win_rate, q=1-p."""
    # Win rate 60%, risk/reward 1:2 (lose $1 to win $2)
    fraction = half_kelly(win_rate=0.60, risk_reward=0.5)
    # Kelly = (0.6 * 2 - 0.4) / 2 = 0.8 / 2 = 0.4
    # Half-Kelly = 0.2
    assert abs(fraction - 0.20) < 0.01

def test_half_kelly_negative_edge():
    """Negative edge returns 0 (don't trade)."""
    fraction = half_kelly(win_rate=0.30, risk_reward=2.0)
    assert fraction == 0.0

def test_calculate_position_size_basic():
    """Calculate contracts based on account and risk."""
    size = calculate_position_size(
        account_value=100000,
        max_risk_per_contract=500,
        win_rate=0.60,
        risk_reward=0.5,
    )
    # Half-Kelly ~0.2, so risk $20k, at $500/contract = 40 contracts
    # But capped at max 5% of account = 10 contracts
    assert size <= 20  # Reasonable cap

def test_calculate_position_size_respects_max():
    """Position size respects maximum percentage."""
    size = calculate_position_size(
        account_value=100000,
        max_risk_per_contract=100,
        win_rate=0.60,
        risk_reward=0.5,
        max_position_pct=0.02,  # 2% max
    )
    # 2% of $100k = $2k risk, at $100/contract = 20 contracts max
    assert size <= 20

def test_calculate_position_size_minimum():
    """Always returns at least 1 if edge exists."""
    size = calculate_position_size(
        account_value=10000,
        max_risk_per_contract=5000,
        win_rate=0.55,
        risk_reward=1.0,
    )
    assert size >= 1
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_position_sizing.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/domain/position_sizing.py
"""
Position sizing using Half-Kelly criterion.

Half-Kelly balances growth vs drawdown risk.
"""

from typing import Optional


def half_kelly(win_rate: float, risk_reward: float) -> float:
    """
    Calculate Half-Kelly fraction.

    Args:
        win_rate: Probability of winning (0-1)
        risk_reward: Risk/Reward ratio (risk/reward, e.g., 0.5 means risk $1 to win $2)

    Returns:
        Fraction of bankroll to risk (0-1)
    """
    if risk_reward <= 0:
        return 0.0

    # b = reward/risk = 1/risk_reward
    b = 1.0 / risk_reward
    p = win_rate
    q = 1 - p

    # Kelly formula: (bp - q) / b
    kelly = (b * p - q) / b

    # Half-Kelly for safety
    half = kelly / 2

    # Never negative
    return max(0.0, half)


def calculate_position_size(
    account_value: float,
    max_risk_per_contract: float,
    win_rate: float,
    risk_reward: float,
    max_position_pct: float = 0.05,
    min_contracts: int = 1,
) -> int:
    """
    Calculate position size in contracts.

    Args:
        account_value: Total account value
        max_risk_per_contract: Maximum loss per contract
        win_rate: Historical win rate (0-1)
        risk_reward: Risk/Reward ratio
        max_position_pct: Maximum position as % of account (default 5%)
        min_contracts: Minimum contracts if edge exists

    Returns:
        Number of contracts to trade
    """
    # Calculate Half-Kelly fraction
    fraction = half_kelly(win_rate, risk_reward)

    if fraction <= 0:
        return 0

    # Calculate risk budget
    kelly_risk = account_value * fraction
    max_risk = account_value * max_position_pct

    # Use smaller of Kelly or max
    risk_budget = min(kelly_risk, max_risk)

    # Calculate contracts
    if max_risk_per_contract <= 0:
        return min_contracts

    contracts = int(risk_budget / max_risk_per_contract)

    # Ensure minimum if we have edge
    return max(min_contracts, contracts)
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_position_sizing.py -v`
Expected: PASS (5 passed)

**Step 5: Commit**

```bash
git add 5.0/src/domain/position_sizing.py 5.0/tests/test_position_sizing.py
git commit -m "feat(domain): add Half-Kelly position sizing"
```

---

### Task 2.6: Implied Move Calculator

**Files:**
- Create: `5.0/src/domain/implied_move.py`
- Create: `5.0/tests/test_implied_move.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_implied_move.py
import pytest
from src.domain.implied_move import calculate_implied_move, find_atm_straddle

def test_calculate_implied_move_basic():
    """Implied move = straddle_price / stock_price * 100."""
    result = calculate_implied_move(
        stock_price=100.0,
        call_price=5.0,
        put_price=4.5,
    )
    # Straddle = 5.0 + 4.5 = 9.5
    # Implied move = 9.5 / 100 * 100 = 9.5%
    assert result["implied_move_pct"] == 9.5
    assert result["straddle_price"] == 9.5

def test_calculate_implied_move_with_iv():
    """Include IV in result if provided."""
    result = calculate_implied_move(
        stock_price=100.0,
        call_price=5.0,
        put_price=4.5,
        call_iv=0.45,
        put_iv=0.42,
    )
    assert result["avg_iv"] == pytest.approx(0.435, rel=0.01)

def test_find_atm_straddle():
    """Find ATM options from chain."""
    chain = [
        {"strike": 95.0, "option_type": "call", "bid": 8.0, "ask": 8.5},
        {"strike": 95.0, "option_type": "put", "bid": 2.5, "ask": 3.0},
        {"strike": 100.0, "option_type": "call", "bid": 5.0, "ask": 5.5},
        {"strike": 100.0, "option_type": "put", "bid": 4.0, "ask": 4.5},
        {"strike": 105.0, "option_type": "call", "bid": 2.5, "ask": 3.0},
        {"strike": 105.0, "option_type": "put", "bid": 7.0, "ask": 7.5},
    ]

    call, put = find_atm_straddle(chain, stock_price=101.0)

    assert call["strike"] == 100.0
    assert put["strike"] == 100.0

def test_find_atm_straddle_empty_chain():
    """Empty chain returns None."""
    call, put = find_atm_straddle([], stock_price=100.0)
    assert call is None
    assert put is None
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_implied_move.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/domain/implied_move.py
"""
Implied move calculator from ATM straddle pricing.

Implied Move = ATM Straddle Price / Stock Price × 100
"""

from typing import Dict, Any, List, Optional, Tuple


def calculate_implied_move(
    stock_price: float,
    call_price: float,
    put_price: float,
    call_iv: Optional[float] = None,
    put_iv: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculate implied move from straddle prices.

    Args:
        stock_price: Current stock price
        call_price: ATM call mid price
        put_price: ATM put mid price
        call_iv: Call implied volatility (optional)
        put_iv: Put implied volatility (optional)

    Returns:
        Dict with implied_move_pct, straddle_price, avg_iv
    """
    straddle_price = call_price + put_price
    implied_move_pct = (straddle_price / stock_price) * 100

    result = {
        "implied_move_pct": round(implied_move_pct, 2),
        "straddle_price": round(straddle_price, 2),
        "call_price": call_price,
        "put_price": put_price,
        "stock_price": stock_price,
    }

    if call_iv is not None and put_iv is not None:
        result["avg_iv"] = (call_iv + put_iv) / 2
        result["call_iv"] = call_iv
        result["put_iv"] = put_iv

    return result


def find_atm_straddle(
    chain: List[Dict[str, Any]],
    stock_price: float,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Find ATM call and put from options chain.

    Args:
        chain: List of option contracts
        stock_price: Current stock price

    Returns:
        Tuple of (atm_call, atm_put) or (None, None) if not found
    """
    if not chain:
        return None, None

    # Get unique strikes
    strikes = sorted(set(opt["strike"] for opt in chain))

    if not strikes:
        return None, None

    # Find closest strike to stock price
    atm_strike = min(strikes, key=lambda s: abs(s - stock_price))

    # Find call and put at ATM strike
    atm_call = None
    atm_put = None

    for opt in chain:
        if opt["strike"] == atm_strike:
            if opt.get("option_type", "").lower() == "call":
                atm_call = opt
            elif opt.get("option_type", "").lower() == "put":
                atm_put = opt

    return atm_call, atm_put


def calculate_implied_move_from_chain(
    chain: List[Dict[str, Any]],
    stock_price: float,
) -> Optional[Dict[str, Any]]:
    """
    Calculate implied move directly from options chain.

    Args:
        chain: Options chain from Tradier
        stock_price: Current stock price

    Returns:
        Implied move data or None if ATM straddle not found
    """
    call, put = find_atm_straddle(chain, stock_price)

    if not call or not put:
        return None

    # Use mid prices
    call_mid = (call.get("bid", 0) + call.get("ask", 0)) / 2
    put_mid = (put.get("bid", 0) + put.get("ask", 0)) / 2

    # Get IVs if available
    call_iv = call.get("greeks", {}).get("mid_iv") or call.get("greeks", {}).get("iv")
    put_iv = put.get("greeks", {}).get("mid_iv") or put.get("greeks", {}).get("iv")

    return calculate_implied_move(
        stock_price=stock_price,
        call_price=call_mid,
        put_price=put_mid,
        call_iv=call_iv,
        put_iv=put_iv,
    )
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_implied_move.py -v`
Expected: PASS (4 passed)

**Step 5: Commit**

```bash
git add 5.0/src/domain/implied_move.py 5.0/tests/test_implied_move.py
git commit -m "feat(domain): add implied move calculator from ATM straddle"
```

---

## Phase 3: API Integrations

> **FIXES APPLIED:** Added Task 3.3 (Alpha Vantage), Task 3.4 (Yahoo Finance), Task 3.5 (Telegram Sender)

### Task 3.1: Tradier Client (Options Data)

**Files:**
- Create: `5.0/src/integrations/tradier.py`
- Create: `5.0/tests/test_tradier.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_tradier.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch
from src.integrations.tradier import TradierClient

@pytest.fixture
def tradier():
    return TradierClient(api_key="test-key")

@pytest.mark.asyncio
async def test_get_quote(tradier):
    """get_quote returns price data."""
    mock_response = {
        "quotes": {
            "quote": {
                "symbol": "NVDA",
                "last": 135.50,
                "bid": 135.45,
                "ask": 135.55,
            }
        }
    }

    with patch.object(tradier, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await tradier.get_quote("NVDA")

        assert result["symbol"] == "NVDA"
        assert result["last"] == 135.50

@pytest.mark.asyncio
async def test_get_options_chain(tradier):
    """get_options_chain returns options data."""
    mock_response = {
        "options": {
            "option": [
                {
                    "symbol": "NVDA250117C00140000",
                    "strike": 140.0,
                    "option_type": "call",
                    "bid": 5.20,
                    "ask": 5.40,
                    "open_interest": 1500,
                    "greeks": {"delta": 0.45, "theta": -0.08, "vega": 0.25}
                }
            ]
        }
    }

    with patch.object(tradier, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await tradier.get_options_chain("NVDA", "2025-01-17")

        assert len(result) == 1
        assert result[0]["strike"] == 140.0
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_tradier.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/integrations/tradier.py
"""
Tradier API client for options data.

Replaces MCP tradier integration with direct REST calls.
"""

import httpx
from typing import Dict, List, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.logging import log

BASE_URL = "https://api.tradier.com/v1"


class TradierClient:
    """Async Tradier API client."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _request(
        self,
        endpoint: str,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Tradier API."""
        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{BASE_URL}/{endpoint}"
            response = await client.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Get stock quote."""
        log("debug", "Fetching quote", symbol=symbol)
        data = await self._request("markets/quotes", {"symbols": symbol})

        quote = data.get("quotes", {}).get("quote", {})
        if isinstance(quote, list):
            quote = quote[0] if quote else {}

        return quote

    async def get_options_chain(
        self,
        symbol: str,
        expiration: str,
        greeks: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get options chain for symbol and expiration.

        Args:
            symbol: Stock symbol
            expiration: Expiration date (YYYY-MM-DD)
            greeks: Include Greeks in response

        Returns:
            List of option contracts
        """
        log("debug", "Fetching options chain", symbol=symbol, expiration=expiration)

        params = {
            "symbol": symbol,
            "expiration": expiration,
            "greeks": str(greeks).lower(),
        }

        data = await self._request("markets/options/chains", params)

        options = data.get("options", {}).get("option", [])
        if not isinstance(options, list):
            options = [options] if options else []

        return options

    async def get_expirations(self, symbol: str) -> List[str]:
        """Get available expiration dates."""
        log("debug", "Fetching expirations", symbol=symbol)
        data = await self._request("markets/options/expirations", {"symbol": symbol})

        expirations = data.get("expirations", {}).get("date", [])
        if not isinstance(expirations, list):
            expirations = [expirations] if expirations else []

        return expirations
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_tradier.py -v`
Expected: PASS (2 passed)

**Step 5: Commit**

```bash
git add 5.0/src/integrations/tradier.py 5.0/tests/test_tradier.py
git commit -m "feat(integrations): add Tradier client for options data"
```

---

### Task 3.2: Perplexity Client (Sentiment)

**Files:**
- Create: `5.0/src/integrations/perplexity.py`
- Create: `5.0/tests/test_perplexity.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_perplexity.py
import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.perplexity import PerplexityClient, parse_sentiment_response

def test_parse_sentiment_response_bullish():
    """Parse bullish sentiment response."""
    text = """Direction: bullish
Score: 0.7
Catalysts: AI demand surge, Data center growth
Risks: China exposure"""

    result = parse_sentiment_response(text)
    assert result["direction"] == "bullish"
    assert result["score"] == 0.7
    assert "AI demand" in result["tailwinds"]
    assert "China" in result["headwinds"]

def test_parse_sentiment_response_bearish():
    """Parse bearish sentiment response."""
    text = """Direction: bearish
Score: -0.5
Catalysts: Market expansion
Risks: Inventory concerns, Competition"""

    result = parse_sentiment_response(text)
    assert result["direction"] == "bearish"
    assert result["score"] == -0.5

@pytest.mark.asyncio
async def test_get_sentiment():
    """get_sentiment calls API and parses response."""
    client = PerplexityClient(api_key="test-key")

    mock_response = {
        "choices": [{
            "message": {
                "content": "Direction: bullish\nScore: 0.6\nCatalysts: Growth\nRisks: None"
            }
        }]
    }

    with patch.object(client, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await client.get_sentiment("NVDA", "2025-01-15")

        assert result["direction"] == "bullish"
        assert result["score"] == 0.6
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_perplexity.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/integrations/perplexity.py
"""
Perplexity API client for AI sentiment analysis.

Replaces MCP perplexity integration with direct REST calls.
"""

import os
import re
import httpx
from typing import Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.logging import log

BASE_URL = "https://api.perplexity.ai"


def parse_sentiment_response(text: str) -> Dict[str, Any]:
    """
    Parse structured sentiment response.

    Expected format:
        Direction: [bullish/bearish/neutral]
        Score: [number -1 to +1]
        Catalysts: [tailwinds]
        Risks: [headwinds]
    """
    result = {
        "direction": "neutral",
        "score": 0.0,
        "tailwinds": "",
        "headwinds": "",
        "raw": text,
    }

    # Parse direction
    dir_match = re.search(r'Direction:\s*(bullish|bearish|neutral)', text, re.I)
    if dir_match:
        result["direction"] = dir_match.group(1).lower()

    # Parse score
    score_match = re.search(r'Score:\s*([+-]?\d*\.?\d+)', text)
    if score_match:
        result["score"] = float(score_match.group(1))

    # Parse catalysts/tailwinds
    cat_match = re.search(r'Catalysts?:\s*(.+?)(?=\n|Risks?:|$)', text, re.I | re.S)
    if cat_match:
        result["tailwinds"] = cat_match.group(1).strip()

    # Parse risks/headwinds
    risk_match = re.search(r'Risks?:\s*(.+?)(?=\n|$)', text, re.I | re.S)
    if risk_match:
        result["headwinds"] = risk_match.group(1).strip()

    return result


class PerplexityClient:
    """Async Perplexity API client."""

    # Default model - can be overridden via environment or constructor
    DEFAULT_MODEL = "llama-3.1-sonar-small-128k-online"

    def __init__(self, api_key: str, model: str = None):
        self.api_key = api_key
        self.model = model or os.environ.get("PERPLEXITY_MODEL", self.DEFAULT_MODEL)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _request(self, messages: list) -> Dict[str, Any]:
        """Make request to Perplexity API."""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                }
            )
            response.raise_for_status()
            return response.json()

    async def get_sentiment(
        self,
        ticker: str,
        earnings_date: str
    ) -> Dict[str, Any]:
        """
        Get AI sentiment for ticker earnings.

        Args:
            ticker: Stock symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Parsed sentiment with direction, score, tailwinds, headwinds
        """
        log("info", "Fetching sentiment", ticker=ticker, date=earnings_date)

        prompt = f"""For {ticker} earnings on {earnings_date}, respond ONLY in this format:
Direction: [bullish/bearish/neutral]
Score: [number -1 to +1]
Catalysts: [2 bullets, max 10 words each]
Risks: [1 bullet, max 10 words]"""

        messages = [{"role": "user", "content": prompt}]

        data = await self._request(messages)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        result = parse_sentiment_response(content)
        result["ticker"] = ticker
        result["earnings_date"] = earnings_date

        return result
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_perplexity.py -v`
Expected: PASS (3 passed)

**Step 5: Commit**

```bash
git add 5.0/src/integrations/perplexity.py 5.0/tests/test_perplexity.py
git commit -m "feat(integrations): add Perplexity client for sentiment"
```

---

### Task 3.3: Alpha Vantage Client (Earnings Calendar)

**Files:**
- Create: `5.0/src/integrations/alphavantage.py`
- Create: `5.0/tests/test_alphavantage.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_alphavantage.py
import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.alphavantage import AlphaVantageClient

@pytest.fixture
def client():
    return AlphaVantageClient(api_key="test-key")

@pytest.mark.asyncio
async def test_get_earnings_calendar(client):
    """get_earnings_calendar returns earnings dates."""
    mock_csv = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
NVDA,NVIDIA Corporation,2025-02-26,2025-01-31,0.80,USD
AVGO,Broadcom Inc,2025-03-06,2025-01-31,1.39,USD"""

    with patch.object(client, '_request_csv', new_callable=AsyncMock) as mock:
        mock.return_value = mock_csv
        result = await client.get_earnings_calendar(horizon="3month")

        assert len(result) == 2
        assert result[0]["symbol"] == "NVDA"
        assert result[0]["reportDate"] == "2025-02-26"

@pytest.mark.asyncio
async def test_get_earnings_for_date(client):
    """get_earnings_for_date filters by date."""
    mock_csv = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
NVDA,NVIDIA,2025-02-26,2025-01-31,0.80,USD
AVGO,Broadcom,2025-03-06,2025-01-31,1.39,USD
LULU,Lululemon,2025-02-26,2025-01-31,5.85,USD"""

    with patch.object(client, '_request_csv', new_callable=AsyncMock) as mock:
        mock.return_value = mock_csv
        result = await client.get_earnings_for_date("2025-02-26")

        assert len(result) == 2
        symbols = [r["symbol"] for r in result]
        assert "NVDA" in symbols
        assert "LULU" in symbols
        assert "AVGO" not in symbols
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_alphavantage.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/integrations/alphavantage.py
"""
Alpha Vantage API client for earnings calendar.

Primary source for earnings dates.
"""

import csv
import io
import httpx
from typing import Dict, List, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.logging import log

BASE_URL = "https://www.alphavantage.co/query"


class AlphaVantageClient:
    """Async Alpha Vantage API client."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _request_csv(self, params: Dict[str, str]) -> str:
        """Make request and return CSV response."""
        params["apikey"] = self.api_key

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(BASE_URL, params=params)
            response.raise_for_status()
            return response.text

    async def get_earnings_calendar(
        self,
        symbol: Optional[str] = None,
        horizon: str = "3month"
    ) -> List[Dict[str, Any]]:
        """
        Get earnings calendar.

        Args:
            symbol: Optional symbol filter
            horizon: 3month, 6month, or 12month

        Returns:
            List of earnings events
        """
        log("debug", "Fetching earnings calendar", horizon=horizon)

        params = {"function": "EARNINGS_CALENDAR", "horizon": horizon}
        if symbol:
            params["symbol"] = symbol

        csv_text = await self._request_csv(params)

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_text))
        return list(reader)

    async def get_earnings_for_date(self, date: str) -> List[Dict[str, Any]]:
        """
        Get earnings for specific date.

        Args:
            date: Date string YYYY-MM-DD

        Returns:
            List of earnings on that date
        """
        log("debug", "Fetching earnings for date", date=date)

        calendar = await self.get_earnings_calendar()

        return [e for e in calendar if e.get("reportDate") == date]

    async def get_company_overview(self, symbol: str) -> Dict[str, Any]:
        """Get company fundamentals."""
        log("debug", "Fetching company overview", symbol=symbol)

        params = {"function": "OVERVIEW", "symbol": symbol}

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                BASE_URL,
                params={**params, "apikey": self.api_key}
            )
            response.raise_for_status()
            return response.json()
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_alphavantage.py -v`
Expected: PASS (2 passed)

**Step 5: Commit**

```bash
git add 5.0/src/integrations/alphavantage.py 5.0/tests/test_alphavantage.py
git commit -m "feat(integrations): add Alpha Vantage client for earnings calendar"
```

---

### Task 3.4: Yahoo Finance Client (Fallback Prices)

**Files:**
- Create: `5.0/src/integrations/yahoo.py`
- Create: `5.0/tests/test_yahoo.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_yahoo.py
import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.yahoo import YahooClient

@pytest.fixture
def client():
    return YahooClient()

@pytest.mark.asyncio
async def test_get_quote(client):
    """get_quote returns price data."""
    mock_response = {
        "chart": {
            "result": [{
                "meta": {
                    "regularMarketPrice": 135.50,
                    "previousClose": 133.25,
                    "symbol": "NVDA"
                }
            }]
        }
    }

    with patch.object(client, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await client.get_quote("NVDA")

        assert result["price"] == 135.50
        assert result["previous_close"] == 133.25
        assert result["symbol"] == "NVDA"

@pytest.mark.asyncio
async def test_get_historical_prices(client):
    """get_historical_prices returns OHLCV data."""
    mock_response = {
        "chart": {
            "result": [{
                "timestamp": [1702339200, 1702425600],
                "indicators": {
                    "quote": [{
                        "open": [130.0, 132.0],
                        "high": [135.0, 137.0],
                        "low": [129.0, 131.0],
                        "close": [134.0, 136.0],
                        "volume": [1000000, 1200000]
                    }]
                }
            }]
        }
    }

    with patch.object(client, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await client.get_historical_prices("NVDA", period="1mo")

        assert len(result) == 2
        assert result[0]["close"] == 134.0
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_yahoo.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/integrations/yahoo.py
"""
Yahoo Finance client for price data.

Free fallback when Tradier is unavailable.
"""

import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.logging import log

BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"


class YahooClient:
    """Async Yahoo Finance client."""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _request(
        self,
        symbol: str,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make request to Yahoo Finance."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BASE_URL}/{symbol}",
                headers=self.headers,
                params=params or {}
            )
            response.raise_for_status()
            return response.json()

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get current quote for symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with price, previous_close, symbol
        """
        log("debug", "Fetching Yahoo quote", symbol=symbol)

        data = await self._request(symbol, {"interval": "1d", "range": "1d"})

        result = data.get("chart", {}).get("result", [{}])[0]
        meta = result.get("meta", {})

        return {
            "symbol": meta.get("symbol", symbol),
            "price": meta.get("regularMarketPrice"),
            "previous_close": meta.get("previousClose"),
            "market_state": meta.get("marketState"),
        }

    async def get_historical_prices(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d"
    ) -> List[Dict[str, Any]]:
        """
        Get historical OHLCV data.

        Args:
            symbol: Stock symbol
            period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
            interval: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo

        Returns:
            List of OHLCV dicts
        """
        log("debug", "Fetching Yahoo historical", symbol=symbol, period=period)

        data = await self._request(symbol, {"interval": interval, "range": period})

        result = data.get("chart", {}).get("result", [{}])[0]
        timestamps = result.get("timestamp", [])
        quotes = result.get("indicators", {}).get("quote", [{}])[0]

        prices = []
        for i, ts in enumerate(timestamps):
            prices.append({
                "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                "timestamp": ts,
                "open": quotes.get("open", [None])[i],
                "high": quotes.get("high", [None])[i],
                "low": quotes.get("low", [None])[i],
                "close": quotes.get("close", [None])[i],
                "volume": quotes.get("volume", [None])[i],
            })

        return prices
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_yahoo.py -v`
Expected: PASS (2 passed)

**Step 5: Commit**

```bash
git add 5.0/src/integrations/yahoo.py 5.0/tests/test_yahoo.py
git commit -m "feat(integrations): add Yahoo Finance client for price data"
```

---

### Task 3.5: Telegram Sender

**Files:**
- Create: `5.0/src/integrations/telegram.py`
- Create: `5.0/tests/test_telegram_client.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_telegram_client.py
import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.telegram import TelegramClient

@pytest.fixture
def client():
    return TelegramClient(bot_token="test-token", chat_id="123456")

@pytest.mark.asyncio
async def test_send_message(client):
    """send_message sends HTML message."""
    mock_response = {"ok": True, "result": {"message_id": 100}}

    with patch.object(client, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await client.send_message("Test <b>message</b>")

        assert result["ok"] is True
        mock.assert_called_once()
        call_args = mock.call_args
        assert call_args[0][0] == "sendMessage"
        assert call_args[1]["data"]["parse_mode"] == "HTML"

@pytest.mark.asyncio
async def test_send_message_silent(client):
    """send_message with silent=True disables notification."""
    mock_response = {"ok": True, "result": {"message_id": 100}}

    with patch.object(client, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        await client.send_message("Test", silent=True)

        call_args = mock.call_args
        assert call_args[1]["data"]["disable_notification"] is True

@pytest.mark.asyncio
async def test_send_alert(client):
    """send_alert sends with notification enabled."""
    mock_response = {"ok": True, "result": {"message_id": 100}}

    with patch.object(client, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        await client.send_alert("🚨 Alert!")

        call_args = mock.call_args
        assert call_args[1]["data"]["disable_notification"] is False
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_telegram_client.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/integrations/telegram.py
"""
Telegram Bot API client for sending notifications.

Sends HTML-formatted messages to configured chat.
"""

import httpx
from typing import Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.logging import log

BASE_URL = "https://api.telegram.org"


class TelegramClient:
    """Async Telegram Bot API client."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"{BASE_URL}/bot{bot_token}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _request(
        self,
        method: str,
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make request to Telegram Bot API."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/{method}",
                json=data or {}
            )
            response.raise_for_status()
            return response.json()

    async def send_message(
        self,
        text: str,
        silent: bool = False,
        reply_to: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send HTML-formatted message.

        Args:
            text: Message text (HTML supported)
            silent: Disable notification sound
            reply_to: Message ID to reply to

        Returns:
            Telegram API response
        """
        log("info", "Sending Telegram message", length=len(text), silent=silent)

        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }

        if reply_to:
            data["reply_to_message_id"] = reply_to

        return await self._request("sendMessage", data=data)

    async def send_alert(self, text: str) -> Dict[str, Any]:
        """
        Send alert message (always with notification).

        Args:
            text: Alert message

        Returns:
            Telegram API response
        """
        log("info", "Sending Telegram alert")
        return await self.send_message(text, silent=False)

    async def send_digest(self, text: str) -> Dict[str, Any]:
        """
        Send digest message (silent notification).

        Args:
            text: Digest message

        Returns:
            Telegram API response
        """
        log("info", "Sending Telegram digest")
        return await self.send_message(text, silent=True)

    async def get_webhook_info(self) -> Dict[str, Any]:
        """Get current webhook configuration."""
        return await self._request("getWebhookInfo", data={})

    async def set_webhook(self, url: str, secret_token: str = "") -> Dict[str, Any]:
        """
        Set webhook URL.

        Args:
            url: Webhook URL (must be HTTPS)
            secret_token: Secret for X-Telegram-Bot-Api-Secret-Token header

        Returns:
            Telegram API response
        """
        log("info", "Setting Telegram webhook", url=url)
        data = {"url": url}
        if secret_token:
            data["secret_token"] = secret_token
        return await self._request("setWebhook", data=data)
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_telegram_client.py -v`
Expected: PASS (3 passed)

**Step 5: Commit**

```bash
git add 5.0/src/integrations/telegram.py 5.0/tests/test_telegram_client.py
git commit -m "feat(integrations): add Telegram client for sending notifications"
```

---

## Phase 4: Output Formatters

### Task 4.1: Telegram Formatter

**Files:**
- Create: `5.0/src/formatters/telegram.py`
- Create: `5.0/tests/test_formatters.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_formatters.py
import pytest
from src.formatters.telegram import format_ticker_line, format_digest

def test_format_ticker_line():
    """Format single ticker for digest."""
    ticker_data = {
        "ticker": "AVGO",
        "vrp_ratio": 7.2,
        "score": 82,
        "direction": "BULLISH",
        "tailwinds": "AI demand",
        "headwinds": "China risk",
        "strategy": "Bull Put 165/160",
        "credit": 2.10,
    }

    line = format_ticker_line(ticker_data, rank=1)

    assert "AVGO" in line
    assert "7.2x" in line
    assert "82" in line
    assert "BULLISH" in line
    assert "AI demand" in line or "✅" in line

def test_format_digest():
    """Format full morning digest."""
    tickers = [
        {
            "ticker": "AVGO", "vrp_ratio": 7.2, "score": 82,
            "direction": "BULLISH", "tailwinds": "AI demand", "headwinds": "China risk",
            "strategy": "Bull Put 165/160", "credit": 2.10
        },
        {
            "ticker": "LULU", "vrp_ratio": 4.8, "score": 71,
            "direction": "NEUTRAL", "tailwinds": "Holiday", "headwinds": "Inventory",
            "strategy": "IC 380/420", "credit": 3.50
        },
    ]

    digest = format_digest("2025-12-12", tickers, budget_calls=12, budget_remaining=4.85)

    assert "Dec 12" in digest or "2025-12-12" in digest
    assert "AVGO" in digest
    assert "LULU" in digest
    assert "12/40" in digest or "12" in digest
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_formatters.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/formatters/telegram.py
"""
Telegram message formatter.

Creates HTML-formatted messages with emoji for Telegram notifications.
"""

from typing import List, Dict, Any
from datetime import datetime


def format_ticker_line(ticker_data: Dict[str, Any], rank: int) -> str:
    """
    Format single ticker for digest.

    Output format:
    1. AVGO | 7.2x | 82 | BULLISH
       ✅ AI tailwinds  ⚠️ China risk
       → Bull Put 165/160 @ $2.10
    """
    ticker = ticker_data.get("ticker", "???")
    vrp = ticker_data.get("vrp_ratio", 0)
    score = ticker_data.get("score", 0)
    direction = ticker_data.get("direction", "NEUTRAL")
    tailwinds = ticker_data.get("tailwinds", "")
    headwinds = ticker_data.get("headwinds", "")
    strategy = ticker_data.get("strategy", "")
    credit = ticker_data.get("credit", 0)

    # Truncate tailwinds/headwinds
    if len(tailwinds) > 20:
        tailwinds = tailwinds[:17] + "..."
    if len(headwinds) > 20:
        headwinds = headwinds[:17] + "..."

    lines = [
        f"{rank}. <b>{ticker}</b> | {vrp}x | {score} | {direction}",
        f"   ✅ {tailwinds}  ⚠️ {headwinds}",
        f"   → {strategy} @ ${credit:.2f}" if credit else f"   → {strategy}",
    ]

    return "\n".join(lines)


def format_digest(
    date: str,
    tickers: List[Dict[str, Any]],
    budget_calls: int = 0,
    budget_remaining: float = 5.00,
) -> str:
    """
    Format morning digest message.

    Args:
        date: Date string (YYYY-MM-DD)
        tickers: List of qualified ticker data
        budget_calls: API calls used today
        budget_remaining: Budget remaining

    Returns:
        HTML-formatted Telegram message
    """
    # Parse date for display
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        date_display = dt.strftime("%b %d")
    except ValueError:
        date_display = date

    lines = [
        f"☀️ <b>{date_display} EARNINGS</b> ({len(tickers)} qualified)",
        "",
    ]

    for i, ticker_data in enumerate(tickers, 1):
        lines.append(format_ticker_line(ticker_data, i))
        lines.append("")

    lines.append(f"Budget: {budget_calls}/40 calls | ${budget_remaining:.2f} left")

    return "\n".join(lines)


def format_alert(ticker_data: Dict[str, Any]) -> str:
    """
    Format critical alert for high-VRP opportunity.

    Args:
        ticker_data: Ticker analysis data

    Returns:
        HTML-formatted alert message
    """
    ticker = ticker_data.get("ticker", "???")
    vrp = ticker_data.get("vrp_ratio", 0)
    score = ticker_data.get("score", 0)
    direction = ticker_data.get("direction", "NEUTRAL")
    sentiment_score = ticker_data.get("sentiment_score", 0)
    tailwinds = ticker_data.get("tailwinds", "")
    headwinds = ticker_data.get("headwinds", "")
    strategy = ticker_data.get("strategy", "")
    credit = ticker_data.get("credit", 0)
    risk = ticker_data.get("max_risk", 0)
    pop = ticker_data.get("pop", 0)

    return f"""🚨 <b>{ticker}</b> | VRP {vrp}x | Score {score}

📊 {direction} | Sentiment {sentiment_score:+.1f}
✅ {tailwinds}
⚠️ {headwinds}

💰 <b>{strategy}</b>
   Credit ${credit:.2f} | Risk ${risk:.2f} | POP {pop}%"""
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_formatters.py -v`
Expected: PASS (2 passed)

**Step 5: Commit**

```bash
git add 5.0/src/formatters/telegram.py 5.0/tests/test_formatters.py
git commit -m "feat(formatters): add Telegram message formatter"
```

---

### Task 4.2: CLI Formatter

**Files:**
- Create: `5.0/src/formatters/cli.py`
- Modify: `5.0/tests/test_formatters.py`

**Step 1: Write the failing test**

```python
# Add to 5.0/tests/test_formatters.py

from src.formatters.cli import format_ticker_line_cli, format_digest_cli

def test_format_ticker_line_cli():
    """Format single ticker for CLI."""
    ticker_data = {
        "ticker": "AVGO",
        "vrp_ratio": 7.2,
        "score": 82,
        "direction": "BULLISH",
        "tailwinds": "AI demand",
        "headwinds": "China risk",
        "strategy": "Bull Put 165/160",
    }

    line = format_ticker_line_cli(ticker_data, rank=1)

    assert "AVGO" in line
    assert "7.2x" in line
    # Should NOT have HTML tags
    assert "<b>" not in line

def test_format_digest_cli():
    """Format full digest for CLI with ASCII borders."""
    tickers = [
        {
            "ticker": "AVGO", "vrp_ratio": 7.2, "score": 82,
            "direction": "BULLISH", "tailwinds": "AI demand", "headwinds": "China risk",
            "strategy": "Bull Put 165/160"
        },
    ]

    digest = format_digest_cli("2025-12-12", tickers, 12, 4.85)

    # Should have ASCII borders
    assert "═" in digest or "─" in digest
    assert "AVGO" in digest
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_formatters.py::test_format_ticker_line_cli -v`
Expected: FAIL with "cannot import name 'format_ticker_line_cli'"

**Step 3: Write minimal implementation**

```python
# 5.0/src/formatters/cli.py
"""
CLI formatter for terminal output.

Creates ASCII-formatted tables for Mac terminal display.
"""

from typing import List, Dict, Any
from datetime import datetime


def format_ticker_line_cli(ticker_data: Dict[str, Any], rank: int) -> str:
    """
    Format single ticker for CLI.

    Output format:
     1  AVGO     7.2x   82     BULLISH  Bull Put 165/160
        + AI demand          - China risk
    """
    ticker = ticker_data.get("ticker", "???")
    vrp = ticker_data.get("vrp_ratio", 0)
    score = ticker_data.get("score", 0)
    direction = ticker_data.get("direction", "NEUTRAL")[:7]  # Truncate
    tailwinds = ticker_data.get("tailwinds", "")[:18]
    headwinds = ticker_data.get("headwinds", "")[:18]
    strategy = ticker_data.get("strategy", "")

    line1 = f" {rank}  {ticker:<8} {vrp}x   {score:<5} {direction:<8} {strategy}"
    line2 = f"    + {tailwinds:<18} - {headwinds}"

    return f"{line1}\n{line2}"


def format_digest_cli(
    date: str,
    tickers: List[Dict[str, Any]],
    budget_calls: int = 0,
    budget_remaining: float = 5.00,
) -> str:
    """
    Format morning digest for CLI with ASCII borders.
    """
    # Parse date
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        date_display = dt.strftime("%b %d")
    except ValueError:
        date_display = date

    width = 55
    border = "═" * width
    thin = "─" * width

    lines = [
        border,
        f" {date_display} EARNINGS ({len(tickers)} qualified)",
        border,
        " #  TICKER   VRP    SCORE  DIR      STRATEGY",
        thin,
    ]

    for i, ticker_data in enumerate(tickers, 1):
        lines.append(format_ticker_line_cli(ticker_data, i))

    lines.extend([
        thin,
        f" Budget: {budget_calls}/40 calls | ${budget_remaining:.2f} remaining",
        border,
    ])

    return "\n".join(lines)


def format_analyze_cli(data: Dict[str, Any]) -> str:
    """
    Format /analyze output for CLI.
    """
    ticker = data.get("ticker", "???")
    date = data.get("earnings_date", "")
    timing = data.get("timing", "")
    vrp = data.get("vrp_ratio", 0)
    vrp_tier = data.get("vrp_tier", "")
    score = data.get("score", 0)
    implied = data.get("implied_move_pct", 0)
    historical = data.get("historical_mean", 0)
    liquidity = data.get("liquidity_tier", "")
    direction = data.get("direction", "NEUTRAL")
    sentiment = data.get("sentiment_score", 0)
    tailwinds = data.get("tailwinds", "")
    headwinds = data.get("headwinds", "")
    strategy = data.get("strategy", "")
    credit = data.get("credit", 0)
    risk = data.get("max_risk", 0)
    pop = data.get("pop", 0)
    size = data.get("position_size", 0)

    width = 55
    border = "═" * width
    thin = "─" * width

    return f"""{border}
 {ticker} Analysis - {date} ({timing})
{border}
 VRP: {vrp}x ({vrp_tier})    Score: {score}
 Implied: {implied}%            Historical: {historical}%
 Liquidity: {liquidity}
{thin}
 SENTIMENT: {direction} ({sentiment:+.1f})
 + {tailwinds}
 - {headwinds}
{thin}
 TOP STRATEGY: {strategy}
 Credit: ${credit:.2f} | Max Risk: ${risk:.2f} | POP: {pop}%
 Size: {size} contracts (Half-Kelly)
{border}"""
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_formatters.py -v`
Expected: PASS (4 passed)

**Step 5: Commit**

```bash
git add 5.0/src/formatters/cli.py 5.0/tests/test_formatters.py
git commit -m "feat(formatters): add CLI ASCII formatter"
```

---

## Phase 5: Job Dispatcher

> **FIXES APPLIED:**
> - Job status persists to SQLite (not in-memory)
> - morning-digest depends on sentiment-scan (not just pre-market-prep)
> - Added half-day schedule handling (1 PM close)
> - Added /dispatch authentication with Cloud Scheduler token

### Task 5.1: Job Manager with DB Persistence

**Files:**
- Create: `5.0/src/core/job_manager.py`
- Create: `5.0/tests/test_job_manager.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_job_manager.py
import pytest
import sqlite3
import tempfile
import os
from src.core.job_manager import JobManager, get_scheduled_job, get_half_day_schedule

def test_get_scheduled_job_weekday_morning():
    """5:30 AM weekday should dispatch pre-market-prep."""
    job = get_scheduled_job("05:30", is_weekend=False)
    assert job == "pre-market-prep"

def test_get_scheduled_job_weekday_digest():
    """7:30 AM weekday should dispatch morning-digest."""
    job = get_scheduled_job("07:30", is_weekend=False)
    assert job == "morning-digest"

def test_get_scheduled_job_saturday():
    """4:00 AM Saturday should dispatch weekly-backfill."""
    job = get_scheduled_job("04:00", is_weekend=True, day_of_week=5)  # Saturday
    assert job == "weekly-backfill"

def test_get_scheduled_job_no_match():
    """Random time with no scheduled job returns None."""
    job = get_scheduled_job("03:45", is_weekend=False)
    assert job is None

def test_job_dependencies_sentiment_scan():
    """sentiment-scan depends on pre-market-prep."""
    manager = JobManager(db_path=":memory:")
    deps = manager.get_dependencies("sentiment-scan")
    assert "pre-market-prep" in deps

def test_job_dependencies_morning_digest():
    """morning-digest depends on sentiment-scan (FIX: was pre-market-prep)."""
    manager = JobManager(db_path=":memory:")
    deps = manager.get_dependencies("morning-digest")
    assert "sentiment-scan" in deps

def test_job_no_dependencies():
    """pre-market-prep has no dependencies."""
    manager = JobManager(db_path=":memory:")
    deps = manager.get_dependencies("pre-market-prep")
    assert deps == []

def test_job_status_persists_to_db():
    """Job status should persist to SQLite, not in-memory."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        # Record status with first manager instance
        manager1 = JobManager(db_path=db_path)
        manager1.record_status("pre-market-prep", "success", "2025-12-12")

        # Create new manager instance - should still see the status
        manager2 = JobManager(db_path=db_path)
        can_run, _ = manager2.check_dependencies("sentiment-scan", "2025-12-12")
        assert can_run is True
    finally:
        os.unlink(db_path)

def test_half_day_schedule():
    """Half-day should skip jobs after 1 PM."""
    schedule = get_half_day_schedule()
    # 14:30 (2:30 PM) should not be in half-day schedule
    assert "14:30" not in schedule
    # Morning jobs should still be there
    assert "05:30" in schedule
    assert "07:30" in schedule
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_job_manager.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/core/job_manager.py
"""
Job dispatcher and dependency manager.

Single dispatcher pattern: Cloud Scheduler calls /dispatch every 15 min,
and this module routes to the correct job based on current time.

CRITICAL FIX: Job status persists to SQLite, not in-memory dict.
"""

import sqlite3
from typing import Optional, List, Dict
from datetime import datetime

from .config import now_et, today_et, is_half_day
from .logging import log

# Weekday schedule (Mon-Fri) - all times ET
WEEKDAY_SCHEDULE = {
    "05:30": "pre-market-prep",
    "06:30": "sentiment-scan",
    "07:30": "morning-digest",
    "10:00": "market-open-refresh",
    "14:30": "pre-trade-refresh",
    "16:30": "after-hours-check",
    "19:00": "outcome-recorder",
    "20:00": "evening-summary",
}

# Half-day schedule (market closes 1 PM ET)
HALF_DAY_SCHEDULE = {
    "05:30": "pre-market-prep",
    "06:30": "sentiment-scan",
    "07:30": "morning-digest",
    "10:00": "market-open-refresh",
    "13:30": "after-hours-check",  # Moved earlier
    "17:00": "outcome-recorder",   # Moved earlier
    "18:00": "evening-summary",    # Moved earlier
}

# Saturday schedule
SATURDAY_SCHEDULE = {
    "04:00": "weekly-backfill",
}

# Sunday schedule
SUNDAY_SCHEDULE = {
    "03:00": "weekly-backup",
    "03:30": "weekly-cleanup",
    "04:00": "calendar-sync",
}

# Job dependencies (job -> list of jobs that must succeed first)
# FIX: morning-digest now depends on sentiment-scan (not pre-market-prep)
JOB_DEPENDENCIES: Dict[str, List[str]] = {
    "sentiment-scan": ["pre-market-prep"],
    "morning-digest": ["sentiment-scan"],  # FIX: was pre-market-prep
    "market-open-refresh": ["pre-market-prep"],
    "pre-trade-refresh": ["pre-market-prep"],
    "after-hours-check": ["pre-market-prep"],
    "outcome-recorder": ["pre-market-prep"],
    "evening-summary": ["outcome-recorder"],
}


def get_half_day_schedule() -> Dict[str, str]:
    """Get schedule for market half-days."""
    return HALF_DAY_SCHEDULE


def get_scheduled_job(
    time_str: str,
    is_weekend: bool,
    day_of_week: int = 0,
    half_day: bool = False,
) -> Optional[str]:
    """
    Get job scheduled for given time.

    Args:
        time_str: Time in HH:MM format
        is_weekend: True if Saturday or Sunday
        day_of_week: 0=Mon, 5=Sat, 6=Sun
        half_day: True if market half-day

    Returns:
        Job name or None if no job scheduled
    """
    if is_weekend:
        if day_of_week == 5:  # Saturday
            return SATURDAY_SCHEDULE.get(time_str)
        elif day_of_week == 6:  # Sunday
            return SUNDAY_SCHEDULE.get(time_str)
        return None
    else:
        if half_day:
            return HALF_DAY_SCHEDULE.get(time_str)
        return WEEKDAY_SCHEDULE.get(time_str)


class JobManager:
    """Manages job dispatch and dependency checking with DB persistence."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize job_status table if not exists."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_status (
                    date TEXT NOT NULL,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    PRIMARY KEY (date, job_name)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def get_dependencies(self, job_name: str) -> List[str]:
        """Get list of jobs that must succeed before this job."""
        return JOB_DEPENDENCIES.get(job_name, [])

    def check_dependencies(self, job_name: str, date: str = None) -> tuple[bool, str]:
        """
        Check if all dependencies succeeded for given date.

        Args:
            job_name: Job to check dependencies for
            date: Date to check (default: today)

        Returns:
            (can_run, reason) - True if can run, else reason why not
        """
        deps = self.get_dependencies(job_name)
        if not deps:
            return True, ""

        if date is None:
            date = today_et()

        conn = sqlite3.connect(self.db_path)
        try:
            for dep in deps:
                cursor = conn.execute(
                    "SELECT status FROM job_status WHERE date = ? AND job_name = ?",
                    (date, dep)
                )
                row = cursor.fetchone()
                status = row[0] if row else "not_run"

                if status != "success":
                    return False, f"Dependency '{dep}' status: {status}"

            return True, ""
        finally:
            conn.close()

    def record_status(self, job_name: str, status: str, date: str = None):
        """
        Record job completion status to database.

        Args:
            job_name: Job that ran
            status: success, failed, or skipped
            date: Date to record for (default: today)
        """
        if date is None:
            date = today_et()

        timestamp = now_et().isoformat()

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO job_status (date, job_name, status, timestamp)
                VALUES (?, ?, ?, ?)
            """, (date, job_name, status, timestamp))
            conn.commit()
            log("info", "Job status recorded", job=job_name, status=status, date=date)
        finally:
            conn.close()

    def get_current_job(self) -> Optional[str]:
        """
        Get job to run based on current time.

        Uses ±7.5 minute window around scheduled time.
        Handles half-day schedules automatically.
        """
        now = now_et()
        current_time = now.strftime("%H:%M")
        is_weekend = now.weekday() >= 5
        day_of_week = now.weekday()
        half_day = is_half_day()

        # Check exact match first
        job = get_scheduled_job(current_time, is_weekend, day_of_week, half_day)
        if job:
            return job

        # Check within ±7 minute window for 15-min dispatcher
        minute = now.minute
        for offset in [-7, -6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7]:
            check_minute = (minute + offset) % 60
            check_hour = now.hour + ((minute + offset) // 60)
            check_time = f"{check_hour:02d}:{check_minute:02d}"
            job = get_scheduled_job(check_time, is_weekend, day_of_week, half_day)
            if job:
                return job

        return None

    def get_status(self, job_name: str, date: str = None) -> str:
        """Get status of a job for given date."""
        if date is None:
            date = today_et()

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT status FROM job_status WHERE date = ? AND job_name = ?",
                (date, job_name)
            )
            row = cursor.fetchone()
            return row[0] if row else "not_run"
        finally:
            conn.close()
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_job_manager.py -v`
Expected: PASS (9 passed)

**Step 5: Commit**

```bash
git add 5.0/src/core/job_manager.py 5.0/tests/test_job_manager.py
git commit -m "feat(core): add job dispatcher with dependency checking"
```

---

## Phase 6: FastAPI Application

> **FIXES APPLIED:**
> - /dispatch requires Cloud Scheduler OIDC token authentication
> - /telegram webhook verifies X-Telegram-Bot-Api-Secret-Token header

### Task 6.1: Main Application Entry Point with Security

**Files:**
- Create: `5.0/src/main.py`
- Create: `5.0/tests/test_api.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_api.py
import pytest
import os
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from src.main import app, verify_scheduler_token, verify_telegram_webhook

@pytest.fixture
def client():
    return TestClient(app)

def test_root_health(client):
    """Root endpoint returns health status."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "ivcrush"
    assert data["status"] == "healthy"

def test_health_endpoint(client):
    """Health endpoint returns system status."""
    response = client.get("/api/health?format=json")
    assert response.status_code == 200

def test_dispatch_requires_auth(client):
    """Dispatch endpoint requires Cloud Scheduler authentication."""
    # No auth header should fail
    response = client.post("/dispatch")
    assert response.status_code == 401

def test_dispatch_with_valid_token(client):
    """Dispatch with valid OIDC token should succeed."""
    with patch('src.main.verify_scheduler_token', return_value=True):
        response = client.post(
            "/dispatch",
            headers={"Authorization": "Bearer valid-oidc-token"}
        )
        # Should return 200 (no job or success)
        assert response.status_code == 200

def test_telegram_webhook_requires_secret(client):
    """Telegram webhook requires secret token header."""
    response = client.post("/telegram", json={"update_id": 123})
    assert response.status_code == 401

def test_telegram_webhook_with_valid_secret(client):
    """Telegram webhook with valid secret should succeed."""
    with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "test-secret"}):
        response = client.post(
            "/telegram",
            json={"update_id": 123},
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
        )
        assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_api.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/main.py
"""
IV Crush 5.0 - Autopilot
FastAPI application entry point.

SECURITY FIXES:
- /dispatch requires Cloud Scheduler OIDC token
- /telegram requires X-Telegram-Bot-Api-Secret-Token header
"""

import os
import uuid
import httpx
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from typing import Optional

from src.core.config import now_et, settings
from src.core.logging import log, set_request_id
from src.core.job_manager import JobManager

app = FastAPI(
    title="IV Crush 5.0",
    description="Autopilot trading system",
    version="5.0.0"
)

job_manager = JobManager()


# ============== SECURITY ==============

async def verify_scheduler_token(authorization: Optional[str] = Header(None)) -> bool:
    """
    Verify Cloud Scheduler OIDC token.

    Cloud Scheduler sends: Authorization: Bearer <OIDC_TOKEN>
    We verify it against Google's token endpoint.
    """
    if not authorization:
        return False

    if not authorization.startswith("Bearer "):
        return False

    token = authorization[7:]

    # In production, verify against Google's tokeninfo endpoint
    # For now, check if token exists (Cloud Run handles verification)
    if os.environ.get("SKIP_AUTH_VERIFICATION"):
        return True

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
            )
            if response.status_code == 200:
                data = response.json()
                # Verify audience matches our service URL
                expected_audience = os.environ.get("CLOUD_RUN_SERVICE_URL", "")
                if expected_audience and data.get("aud") != expected_audience:
                    log("warn", "Token audience mismatch")
                    return False
                return True
    except Exception as e:
        log("error", "Token verification failed", error=str(e))

    return False


def verify_telegram_webhook(
    secret_token: Optional[str] = Header(None, alias="X-Telegram-Bot-Api-Secret-Token")
) -> bool:
    """
    Verify Telegram webhook secret.

    Telegram sends: X-Telegram-Bot-Api-Secret-Token: <SECRET>
    """
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected:
        # No secret configured - allow all (dev mode)
        return True
    return secret_token == expected


# ============== MIDDLEWARE ==============

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request ID to all requests."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    set_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ============== PUBLIC ENDPOINTS ==============

@app.get("/")
async def root():
    """Health check endpoint (public)."""
    return {
        "service": "ivcrush",
        "version": "5.0.0",
        "timestamp_et": now_et().isoformat(),
        "status": "healthy"
    }


@app.get("/api/health")
async def health(format: str = "json"):
    """System health check (public)."""
    data = {
        "status": "healthy",
        "timestamp_et": now_et().isoformat(),
        "budget": {
            "calls_today": 0,  # TODO: Get from DB
            "remaining": settings.PERPLEXITY_MONTHLY_BUDGET,
        }
    }
    return data


# ============== PROTECTED ENDPOINTS ==============

@app.post("/dispatch")
async def dispatch(authorization: Optional[str] = Header(None)):
    """
    Dispatcher endpoint called by Cloud Scheduler every 15 min.
    Routes to correct job based on current time.

    SECURITY: Requires Cloud Scheduler OIDC token.
    """
    # Verify Cloud Scheduler token
    if not await verify_scheduler_token(authorization):
        log("warn", "Unauthorized dispatch attempt")
        raise HTTPException(401, "Unauthorized - valid OIDC token required")

    job = job_manager.get_current_job()

    if not job:
        log("info", "No job scheduled for current time")
        return {"status": "no_job", "message": "No job scheduled"}

    # Check dependencies
    can_run, reason = job_manager.check_dependencies(job)
    if not can_run:
        log("warn", "Job dependencies not met", job=job, reason=reason)
        return {"status": "skipped", "job": job, "reason": reason}

    log("info", "Dispatching job", job=job)

    # TODO: Actually run the job
    # For now, just record success
    job_manager.record_status(job, "success")

    return {"status": "success", "job": job}


@app.post("/telegram")
async def telegram_webhook(
    request: Request,
    secret_token: Optional[str] = Header(None, alias="X-Telegram-Bot-Api-Secret-Token")
):
    """
    Telegram bot webhook handler.

    SECURITY: Requires X-Telegram-Bot-Api-Secret-Token header.
    """
    # Verify webhook secret
    if not verify_telegram_webhook(secret_token):
        log("warn", "Unauthorized Telegram webhook attempt")
        raise HTTPException(401, "Unauthorized - valid secret token required")

    try:
        body = await request.json()
        log("info", "Telegram update", update_id=body.get("update_id"))
        # TODO: Handle telegram commands
        return {"ok": True}
    except Exception as e:
        log("error", "Telegram handler failed", error=str(e))
        return {"ok": True}


# ============== API ENDPOINTS ==============

@app.get("/api/analyze")
async def analyze(ticker: str, date: str = None, format: str = "json"):
    """Deep analysis of single ticker."""
    ticker = ticker.upper().strip()
    if not ticker.isalnum():
        raise HTTPException(400, "Invalid ticker")

    log("info", "Analyze request", ticker=ticker)

    # TODO: Implement full analysis
    return {
        "ticker": ticker,
        "status": "not_implemented",
    }


@app.get("/api/whisper")
async def whisper(date: str = None, format: str = "json"):
    """Most anticipated earnings this week."""
    log("info", "Whisper request")

    # TODO: Implement whisper
    return {
        "status": "not_implemented",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_api.py -v`
Expected: PASS (3 passed)

**Step 5: Commit**

```bash
git add 5.0/src/main.py 5.0/tests/test_api.py
git commit -m "feat(api): add FastAPI application with core endpoints"
```

---

## Remaining Tasks (Summary)

The following tasks complete the implementation. Each follows the same TDD pattern:

### Phase 7: Database Layer

> **FIXES APPLIED:** Task 7.1 uses GCS generation-based locking (not file locks) to prevent race conditions

- **Task 7.1:** SQLite connection with GCS generation-based locking
- **Task 7.2:** Historical moves repository
- **Task 7.3:** Sentiment cache repository
- **Task 7.4:** API budget tracker (NEW - tracks Perplexity calls against daily/monthly limits)

#### Task 7.1 Critical Implementation: GCS Generation Locking

```python
# 5.0/src/core/database.py
"""
Database sync with GCS using generation-based locking.

CRITICAL: Uses GCS object generation numbers for optimistic locking.
This prevents race conditions when multiple Cloud Run instances try to write.
"""

from google.cloud import storage
from google.api_core.exceptions import PreconditionFailed
import sqlite3
import tempfile
import shutil
from typing import Optional
from pathlib import Path

from .logging import log


class DatabaseSync:
    """Sync SQLite to/from GCS with generation-based locking."""

    # Timeout for GCS operations (seconds)
    DOWNLOAD_TIMEOUT = 30
    UPLOAD_TIMEOUT = 60

    def __init__(self, bucket_name: str, blob_name: str = "ivcrush.db"):
        self.bucket_name = bucket_name
        self.blob_name = blob_name
        self.local_path = Path(tempfile.gettempdir()) / "ivcrush.db"
        self._generation: Optional[int] = None
        self._client = storage.Client()

    def download(self) -> str:
        """
        Download database from GCS.

        Returns:
            Local path to downloaded database
        """
        bucket = self._client.bucket(self.bucket_name)
        blob = bucket.blob(self.blob_name)

        try:
            blob.reload()  # Get current metadata
            self._generation = blob.generation
            blob.download_to_filename(
                str(self.local_path),
                timeout=self.DOWNLOAD_TIMEOUT
            )
            log("info", "Database downloaded", generation=self._generation)
        except Exception as e:
            log("warn", "No existing database, starting fresh", error=str(e))
            self._generation = None

        return str(self.local_path)

    def upload(self) -> bool:
        """
        Upload database to GCS with generation-based locking.

        Uses if_generation_match to ensure no concurrent writes.

        Returns:
            True if upload succeeded, False if conflict
        """
        bucket = self._client.bucket(self.bucket_name)
        blob = bucket.blob(self.blob_name)

        try:
            if self._generation is not None:
                # Optimistic lock: only succeed if generation matches
                blob.upload_from_filename(
                    str(self.local_path),
                    if_generation_match=self._generation,
                    timeout=self.UPLOAD_TIMEOUT
                )
            else:
                # First upload - no generation to match
                blob.upload_from_filename(
                    str(self.local_path),
                    if_generation_match=0,  # Only succeed if doesn't exist
                    timeout=self.UPLOAD_TIMEOUT
                )

            blob.reload()
            self._generation = blob.generation
            log("info", "Database uploaded", generation=self._generation)
            return True

        except PreconditionFailed:
            log("error", "Database upload conflict - another instance wrote first")
            return False

    def get_connection(self) -> sqlite3.Connection:
        """Get SQLite connection to local database."""
        return sqlite3.connect(str(self.local_path))


# Context manager for safe database operations
class DatabaseContext:
    """Context manager for database operations with auto-sync."""

    def __init__(self, sync: DatabaseSync, max_retries: int = 3):
        self.sync = sync
        self.conn: Optional[sqlite3.Connection] = None
        self.max_retries = max_retries
        self._changes_sql: List[str] = []  # Track changes for retry

    def __enter__(self) -> sqlite3.Connection:
        self.sync.download()
        self.conn = self.sync.get_connection()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

        if exc_type is None:
            # Try upload with retries on conflict
            for attempt in range(self.max_retries):
                if self.sync.upload():
                    return  # Success

                # Conflict - download latest and notify caller
                log("warn", "GCS conflict, attempt retry", attempt=attempt + 1)
                self.sync.download()

            # All retries failed
            raise RuntimeError(
                f"Database sync conflict after {self.max_retries} retries - "
                "another instance is writing frequently"
            )
```

#### Task 7.4: API Budget Tracker

**Files:**
- Create: `5.0/src/core/budget.py`
- Create: `5.0/tests/test_budget.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_budget.py
import pytest
import sqlite3
import tempfile
import os
from datetime import date
from src.core.budget import BudgetTracker

@pytest.fixture
def tracker():
    """Create tracker with temp database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield BudgetTracker(db_path=db_path)
    os.unlink(db_path)

def test_record_api_call(tracker):
    """record_call increments daily count."""
    tracker.record_call("perplexity", cost=0.005)
    stats = tracker.get_daily_stats("perplexity")
    assert stats["calls"] == 1
    assert stats["cost"] == 0.005

def test_daily_limit_check(tracker):
    """can_call returns False when daily limit exceeded."""
    # Record 40 calls (daily limit)
    for _ in range(40):
        tracker.record_call("perplexity", cost=0.005)

    assert tracker.can_call("perplexity") is False

def test_daily_limit_resets(tracker):
    """Daily limit resets on new day."""
    # Record calls for yesterday
    yesterday = "2025-12-11"
    for _ in range(40):
        tracker.record_call("perplexity", cost=0.005, date_str=yesterday)

    # Today should be allowed
    assert tracker.can_call("perplexity") is True

def test_monthly_budget_check(tracker):
    """can_call returns False when monthly budget exceeded."""
    # Record $5 worth of calls (monthly budget)
    for _ in range(100):
        tracker.record_call("perplexity", cost=0.05)  # $5 total

    assert tracker.can_call("perplexity") is False

def test_get_budget_summary(tracker):
    """get_summary returns calls and budget remaining."""
    tracker.record_call("perplexity", cost=0.10)
    tracker.record_call("perplexity", cost=0.15)

    summary = tracker.get_summary()
    assert summary["today_calls"] == 2
    assert summary["today_cost"] == 0.25
    assert summary["month_cost"] == 0.25
    assert summary["budget_remaining"] == 4.75  # $5 - $0.25
```

**Step 2: Run test to verify it fails**

Run: `cd 5.0 && python -m pytest tests/test_budget.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

```python
# 5.0/src/core/budget.py
"""
API budget tracker for rate-limited services.

Tracks daily calls and monthly spend against configured limits.
Prevents exceeding Perplexity's 40 calls/day, $5/month budget.
"""

import sqlite3
from datetime import datetime, date
from typing import Dict, Any, Optional

from .config import today_et, now_et, settings
from .logging import log


class BudgetTracker:
    """Track API usage against daily/monthly limits."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize api_budget table."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_budget (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    service TEXT NOT NULL,
                    calls INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_api_budget_date_service
                ON api_budget(date, service)
            """)
            conn.commit()
        finally:
            conn.close()

    def record_call(
        self,
        service: str,
        cost: float = 0.0,
        date_str: Optional[str] = None
    ):
        """
        Record an API call.

        Args:
            service: Service name (e.g., "perplexity")
            cost: Cost of the call in dollars
            date_str: Date to record for (default: today)
        """
        if date_str is None:
            date_str = today_et()

        timestamp = now_et().isoformat()

        conn = sqlite3.connect(self.db_path)
        try:
            # Check if row exists for today
            cursor = conn.execute(
                "SELECT id, calls, cost FROM api_budget WHERE date = ? AND service = ?",
                (date_str, service)
            )
            row = cursor.fetchone()

            if row:
                # Update existing
                conn.execute("""
                    UPDATE api_budget
                    SET calls = calls + 1, cost = cost + ?, updated_at = ?
                    WHERE id = ?
                """, (cost, timestamp, row[0]))
            else:
                # Insert new
                conn.execute("""
                    INSERT INTO api_budget (date, service, calls, cost, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                """, (date_str, service, cost, timestamp))

            conn.commit()
            log("debug", "API call recorded", service=service, cost=cost)
        finally:
            conn.close()

    def get_daily_stats(self, service: str, date_str: Optional[str] = None) -> Dict[str, Any]:
        """Get daily usage stats for a service."""
        if date_str is None:
            date_str = today_et()

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT calls, cost FROM api_budget WHERE date = ? AND service = ?",
                (date_str, service)
            )
            row = cursor.fetchone()
            return {
                "calls": row[0] if row else 0,
                "cost": row[1] if row else 0.0,
                "date": date_str,
            }
        finally:
            conn.close()

    def get_monthly_cost(self, service: str) -> float:
        """Get total cost for current month."""
        month_prefix = today_et()[:7]  # YYYY-MM

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT SUM(cost) FROM api_budget WHERE date LIKE ? AND service = ?",
                (f"{month_prefix}%", service)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0.0
        finally:
            conn.close()

    def can_call(self, service: str = "perplexity") -> bool:
        """
        Check if we can make another API call.

        Returns False if:
        - Daily limit exceeded (40 calls)
        - Monthly budget exceeded ($5)
        """
        daily = self.get_daily_stats(service)
        monthly_cost = self.get_monthly_cost(service)

        # Check daily limit
        if daily["calls"] >= settings.PERPLEXITY_DAILY_LIMIT:
            log("warn", "Daily API limit reached", service=service, calls=daily["calls"])
            return False

        # Check monthly budget
        if monthly_cost >= settings.PERPLEXITY_MONTHLY_BUDGET:
            log("warn", "Monthly API budget exceeded", service=service, cost=monthly_cost)
            return False

        return True

    def get_summary(self, service: str = "perplexity") -> Dict[str, Any]:
        """Get budget summary for display."""
        daily = self.get_daily_stats(service)
        monthly_cost = self.get_monthly_cost(service)

        return {
            "today_calls": daily["calls"],
            "today_cost": round(daily["cost"], 2),
            "daily_limit": settings.PERPLEXITY_DAILY_LIMIT,
            "month_cost": round(monthly_cost, 2),
            "monthly_budget": settings.PERPLEXITY_MONTHLY_BUDGET,
            "budget_remaining": round(settings.PERPLEXITY_MONTHLY_BUDGET - monthly_cost, 2),
            "can_call": self.can_call(service),
        }
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_budget.py -v`
Expected: PASS (5 passed)

**Step 5: Commit**

```bash
git add 5.0/src/core/budget.py 5.0/tests/test_budget.py
git commit -m "feat(core): add API budget tracker for rate limiting"
```

---

#### Update Perplexity Client to Use Budget Tracker

**Modify:** `5.0/src/integrations/perplexity.py`

Add budget check before API calls:

```python
# Add to PerplexityClient.__init__
from src.core.budget import BudgetTracker

class PerplexityClient:
    def __init__(self, api_key: str, budget_tracker: Optional[BudgetTracker] = None):
        self.api_key = api_key
        self.budget = budget_tracker or BudgetTracker()

    async def get_sentiment(self, ticker: str, earnings_date: str) -> Dict[str, Any]:
        # Check budget before calling
        if not self.budget.can_call("perplexity"):
            log("warn", "Perplexity budget exceeded, returning cached or default")
            return {
                "direction": "neutral",
                "score": 0.0,
                "tailwinds": "",
                "headwinds": "",
                "error": "budget_exceeded",
                "ticker": ticker,
                "earnings_date": earnings_date,
            }

        # ... existing API call code ...

        # Record the call after success
        self.budget.record_call("perplexity", cost=0.005)  # ~$0.005 per call

        return result
```

### Phase 8: Job Implementations
- **Task 8.1:** pre-market-prep job
- **Task 8.2:** sentiment-scan job
- **Task 8.3:** morning-digest job
- **Task 8.4:** market-open-refresh job
- **Task 8.5:** pre-trade-refresh job
- **Task 8.6:** after-hours-check job
- **Task 8.7:** outcome-recorder job
- **Task 8.8:** evening-summary job
- **Task 8.9:** weekly-backfill job
- **Task 8.10:** weekly-backup job
- **Task 8.11:** weekly-cleanup job
- **Task 8.12:** calendar-sync job

### Phase 9: Telegram Bot
- **Task 9.1:** Command parser
- **Task 9.2:** Message sender
- **Task 9.3:** Webhook handler

### Phase 10: Deployment
- **Task 10.1:** Dockerfile
- **Task 10.2:** Cloud Run deployment script
- **Task 10.3:** Cloud Scheduler setup
- **Task 10.4:** Secret Manager setup

### Phase 11: Grafana Dashboards
- **Task 11.1:** Operations dashboard
- **Task 11.2:** Trading dashboard
- **Task 11.3:** API dashboard
- **Task 11.4:** Whisper dashboard

---

## Execution

Plan complete and saved to `5.0/docs/plans/2025-12-12-autopilot.md`.

**Two execution options:**

1. **Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

2. **Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
