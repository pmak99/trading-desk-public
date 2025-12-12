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

## Phase 2: Domain Logic (Port from 2.0)

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

## Phase 3: API Integrations

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

    def __init__(self, api_key: str):
        self.api_key = api_key

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
                    "model": "llama-3.1-sonar-small-128k-online",
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

### Task 5.1: Job Manager with Dependencies

**Files:**
- Create: `5.0/src/core/job_manager.py`
- Create: `5.0/tests/test_job_manager.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_job_manager.py
import pytest
from src.core.job_manager import JobManager, get_scheduled_job

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

def test_job_dependencies():
    """sentiment-scan depends on pre-market-prep."""
    manager = JobManager()
    deps = manager.get_dependencies("sentiment-scan")
    assert "pre-market-prep" in deps

def test_job_no_dependencies():
    """pre-market-prep has no dependencies."""
    manager = JobManager()
    deps = manager.get_dependencies("pre-market-prep")
    assert deps == []
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
"""

from typing import Optional, List, Dict
from datetime import datetime

from .config import now_et, today_et
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
JOB_DEPENDENCIES: Dict[str, List[str]] = {
    "sentiment-scan": ["pre-market-prep"],
    "morning-digest": ["pre-market-prep"],
    "market-open-refresh": ["pre-market-prep"],
    "pre-trade-refresh": ["pre-market-prep"],
    "after-hours-check": ["pre-market-prep"],
    "outcome-recorder": ["pre-market-prep"],
    "evening-summary": ["outcome-recorder"],
}


def get_scheduled_job(
    time_str: str,
    is_weekend: bool,
    day_of_week: int = 0,
) -> Optional[str]:
    """
    Get job scheduled for given time.

    Args:
        time_str: Time in HH:MM format
        is_weekend: True if Saturday or Sunday
        day_of_week: 0=Mon, 5=Sat, 6=Sun

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
        return WEEKDAY_SCHEDULE.get(time_str)


class JobManager:
    """Manages job dispatch and dependency checking."""

    def __init__(self):
        self._job_status: Dict[str, Dict[str, str]] = {}  # {date: {job: status}}

    def get_dependencies(self, job_name: str) -> List[str]:
        """Get list of jobs that must succeed before this job."""
        return JOB_DEPENDENCIES.get(job_name, [])

    def check_dependencies(self, job_name: str) -> tuple[bool, str]:
        """
        Check if all dependencies succeeded today.

        Returns:
            (can_run, reason) - True if can run, else reason why not
        """
        deps = self.get_dependencies(job_name)
        if not deps:
            return True, ""

        today = today_et()
        day_status = self._job_status.get(today, {})

        for dep in deps:
            status = day_status.get(dep, "not_run")
            if status != "success":
                return False, f"Dependency '{dep}' status: {status}"

        return True, ""

    def record_status(self, job_name: str, status: str):
        """Record job completion status."""
        today = today_et()
        if today not in self._job_status:
            self._job_status[today] = {}
        self._job_status[today][job_name] = status
        log("info", "Job status recorded", job=job_name, status=status)

    def get_current_job(self) -> Optional[str]:
        """
        Get job to run based on current time.

        Uses ±7.5 minute window around scheduled time.
        """
        now = now_et()
        current_time = now.strftime("%H:%M")
        is_weekend = now.weekday() >= 5
        day_of_week = now.weekday()

        # Check exact match first
        job = get_scheduled_job(current_time, is_weekend, day_of_week)
        if job:
            return job

        # Check within ±7 minute window for 15-min dispatcher
        minute = now.minute
        for offset in [-7, -6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7]:
            check_minute = (minute + offset) % 60
            check_hour = now.hour + ((minute + offset) // 60)
            check_time = f"{check_hour:02d}:{check_minute:02d}"
            job = get_scheduled_job(check_time, is_weekend, day_of_week)
            if job:
                return job

        return None
```

**Step 4: Run test to verify it passes**

Run: `cd 5.0 && python -m pytest tests/test_job_manager.py -v`
Expected: PASS (6 passed)

**Step 5: Commit**

```bash
git add 5.0/src/core/job_manager.py 5.0/tests/test_job_manager.py
git commit -m "feat(core): add job dispatcher with dependency checking"
```

---

## Phase 6: FastAPI Application

### Task 6.1: Main Application Entry Point

**Files:**
- Create: `5.0/src/main.py`
- Create: `5.0/tests/test_api.py`

**Step 1: Write the failing test**

```python
# 5.0/tests/test_api.py
import pytest
from fastapi.testclient import TestClient
from src.main import app

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

def test_dispatch_endpoint(client):
    """Dispatch endpoint accepts POST."""
    response = client.post("/dispatch")
    # Should return 200 even if no job scheduled
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
"""

import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from src.core.config import now_et, settings
from src.core.logging import log, set_request_id
from src.core.job_manager import JobManager

app = FastAPI(
    title="IV Crush 5.0",
    description="Autopilot trading system",
    version="5.0.0"
)

job_manager = JobManager()


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request ID to all requests."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    set_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "service": "ivcrush",
        "version": "5.0.0",
        "timestamp_et": now_et().isoformat(),
        "status": "healthy"
    }


@app.post("/dispatch")
async def dispatch():
    """
    Dispatcher endpoint called by Cloud Scheduler every 15 min.
    Routes to correct job based on current time.
    """
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


@app.get("/api/health")
async def health(format: str = "json"):
    """System health check."""
    data = {
        "status": "healthy",
        "timestamp_et": now_et().isoformat(),
        "budget": {
            "calls_today": 0,  # TODO: Get from DB
            "remaining": settings.PERPLEXITY_MONTHLY_BUDGET,
        }
    }
    return data


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


@app.post("/telegram")
async def telegram_webhook(request: Request):
    """Telegram bot webhook handler."""
    try:
        body = await request.json()
        log("info", "Telegram update", update_id=body.get("update_id"))
        # TODO: Handle telegram commands
        return {"ok": True}
    except Exception as e:
        log("error", "Telegram handler failed", error=str(e))
        return {"ok": True}


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
- **Task 7.1:** SQLite connection with Cloud Storage sync
- **Task 7.2:** Historical moves repository
- **Task 7.3:** Sentiment cache repository

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
