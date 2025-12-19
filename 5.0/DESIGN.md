# IV Crush 5.0 - Autopilot

## Overview

5.0 "Autopilot" transforms the IV Crush trading system from manual CLI commands into a 24/7 cloud-native service that:

- **Automates** daily trading workflow (pre-market prep, sentiment analysis, digests, refreshes)
- **Notifies** via Telegram for critical opportunities and daily digests
- **Responds** to on-demand queries from Telegram (mobile) and Mac terminal (CLI)
- **Costs** $0/month for infrastructure (within free tiers)
- **Maintains** all core VRP math and trading logic from 2.0

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Google Cloud Platform                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐     ┌─────────────────────────────────────────┐   │
│  │   Cloud      │     │            Cloud Run                     │   │
│  │  Scheduler   │────▶│  ┌─────────────────────────────────┐    │   │
│  │  (1 job)     │     │  │         Dispatcher              │    │   │
│  └──────────────┘     │  │  - Routes to correct job        │    │   │
│                       │  │  - Checks dependencies          │    │   │
│                       │  │  - Records job status           │    │   │
│                       │  └─────────────────────────────────┘    │   │
│  ┌──────────────┐     │                                          │   │
│  │   Telegram   │     │  ┌─────────────────────────────────┐    │   │
│  │   Webhook    │────▶│  │      On-Demand Handlers         │    │   │
│  └──────────────┘     │  │  /analyze, /whisper, /history   │    │   │
│                       │  │  /health, /dashboard, /help     │    │   │
│                       │  └─────────────────────────────────┘    │   │
│                       │                                          │   │
│                       │  ┌─────────────────────────────────┐    │   │
│                       │  │         Core Logic               │    │   │
│                       │  │  - VRP calculation (from 2.0)   │    │   │
│                       │  │  - Liquidity scoring            │    │   │
│                       │  │  - Strategy generation          │    │   │
│                       │  │  - Position sizing              │    │   │
│                       │  └─────────────────────────────────┘    │   │
│                       └─────────────────────────────────────────┘   │
│                                         │                            │
│                                         ▼                            │
│  ┌──────────────┐     ┌──────────────────────────────────────┐      │
│  │   Secret     │     │          Cloud Storage                │      │
│  │   Manager    │     │  ┌──────────────────────────────┐    │      │
│  │  (1 secret)  │     │  │  ivcrush.db (SQLite)         │    │      │
│  └──────────────┘     │  │  sentiment_cache.db          │    │      │
│                       │  └──────────────────────────────┘    │      │
│                       └──────────────────────────────────────┘      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         External Services                            │
├─────────────────────────────────────────────────────────────────────┤
│  Tradier API      │  Options Greeks, IV, chains, current prices     │
│  Perplexity API   │  AI sentiment analysis                          │
│  Alpha Vantage    │  Earnings calendar                              │
│  Yahoo Finance    │  Historical price data only                     │
│  Telegram Bot     │  Notifications, commands                        │
│  Grafana Cloud    │  Metrics, dashboards                            │
└─────────────────────────────────────────────────────────────────────┘
```

## Cost Analysis

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| Cloud Run | ~200 invocations/month, <1 GB-hr | $0 (free tier) |
| Cloud Scheduler | 1 job every 15 min | $0 (3 free jobs) |
| Cloud Storage | <100 MB databases | $0 (free tier) |
| Secret Manager | 1 secret, ~30 reads/day | $0 (free tier) |
| Grafana Cloud | 10k metrics/month | $0 (free tier) |
| Telegram Bot | Unlimited | $0 |
| **Total** | | **$0/month** |

## Scheduled Jobs

### Single Dispatcher Pattern

Instead of 11 separate Cloud Scheduler jobs ($0.80/month), we use ONE job that runs every 15 minutes and dispatches to the correct handler based on current time:

```
Cloud Scheduler: */15 * * * * → POST /dispatch
```

### Slot Assignments (All times ET)

**Weekdays (Mon-Fri):**

| Time | Job | Status | Description |
|------|-----|--------|-------------|
| 5:30 AM | `pre-market-prep` | ✅ | Fetch earnings calendar, calculate VRP for today's tickers, rate-limited API calls |
| 6:30 AM | `sentiment-scan` | ✅ | Pre-cache AI sentiment for high-VRP tickers (≥3x), respects budget limits |
| 7:30 AM | `morning-digest` | ✅ | Send Telegram digest with top 10 ranked opportunities |
| 10:00 AM | `market-open-refresh` | ✅ | Refresh prices, alert on significant pre-market moves (>50% of historical avg) |
| 2:30 PM | `pre-trade-refresh` | ✅ | Final VRP validation, send actionable alert with top 5 AMC earnings |
| 4:30 PM | `after-hours-check` | ✅ | Monitor after-hours prices, alert on moves >1% with historical context |
| 7:00 PM | `outcome-recorder` | ✅ | Record same-day earnings moves to historical_moves table |
| 8:00 PM | `evening-summary` | ✅ | Daily summary notification |

**Saturday:**

| Time | Job | Status | Description |
|------|-----|--------|-------------|
| 4:00 AM | `weekly-backfill` | ✅ | Backfill historical moves for past 7 days earnings, duplicate detection |

**Sunday:**

| Time | Job | Status | Description |
|------|-----|--------|-------------|
| 3:00 AM | `weekly-backup` | ✅ | Integrity check then upload database to GCS with timestamp |
| 3:30 AM | `weekly-cleanup` | ✅ | Clear expired sentiment cache entries |
| 4:00 AM | `calendar-sync` | ✅ | Refresh 3-month earnings calendar from Alpha Vantage |

### Job Dependencies

Jobs check if their dependencies succeeded before running:

```python
JOB_DEPENDENCIES = {
    "sentiment-scan": ["pre-market-prep"],
    "morning-digest": ["pre-market-prep"],
    "market-open-refresh": ["pre-market-prep"],
    "pre-trade-refresh": ["pre-market-prep"],
    "after-hours-check": ["pre-market-prep"],
    "outcome-recorder": ["pre-market-prep"],
    "evening-summary": ["outcome-recorder"],
}
```

If a dependency failed, the job logs an error and skips execution.

### Job Configuration Constants

All jobs use configurable limits defined in `src/jobs/handlers.py`:

```python
# Processing limits
MAX_PRE_MARKET_TICKERS = 30   # Max tickers to evaluate in pre-market prep
MAX_PRIME_CANDIDATES = 40     # Max candidates to consider for priming
MAX_PRIME_CALLS = 15          # Max Perplexity API calls during prime
MAX_DIGEST_CANDIDATES = 40    # Max candidates to consider for digest
MAX_BACKFILL_TICKERS = 60     # Max tickers to backfill in weekly job

# Rate limiting
RATE_LIMIT_DELAY = 0.5        # Seconds between API call batches
RATE_LIMIT_BATCH_SIZE = 5     # API calls before adding delay
TRADIER_CALLS_PER_TICKER = 3  # Quote + expirations + chain per ticker
```

### Implied Move Calculation

All job handlers use **real options data** from Tradier for VRP calculation:

```python
# src/domain/implied_move.py - shared helpers
async def fetch_real_implied_move(tradier, ticker, earnings_date, price=None):
    """Fetch ATM straddle from Tradier for accurate implied move."""
    # 1. Get stock price (if not provided)
    # 2. Get expirations, find nearest after earnings
    # 3. Fetch options chain
    # 4. Calculate implied move from ATM straddle

def get_implied_move_with_fallback(real_result, historical_avg):
    """Extract real data or fall back to 1.5x estimate."""
    if real_result["used_real_data"]:
        return real_result["implied_move_pct"], True
    return historical_avg * 1.5, False  # Fallback only if Tradier unavailable
```

**Rate Limiting Note**: Each ticker requires 3 Tradier API calls (quote, expirations, chain). Rate limiting accounts for this with `TRADIER_CALLS_PER_TICKER = 3`.

### Job Error Handling

All jobs follow consistent error handling patterns:

1. **Rate limiting**: `asyncio.sleep()` between API call batches
2. **Failed ticker tracking**: Jobs track `failed_tickers` list for debugging
3. **Empty API validation**: Check for empty responses before processing
4. **Telegram error handling**: Capture and log Telegram send failures separately
5. **Metrics recording**: Duration and counts recorded for all jobs
6. **Duplicate detection**: Backfill jobs skip already-recorded earnings

## On-Demand Commands

### Available Commands

| Command | Purpose | Example |
|---------|---------|---------|
| `/analyze TICKER` | Deep dive on single ticker | `/analyze NVDA` |
| `/whisper` | Most anticipated earnings this week | `/whisper` |
| `/history TICKER` | Historical earnings visualization | `/history AMD` |
| `/health` | System status + API budget | `/health` |
| `/dashboard` | Quick link to Grafana | `/dashboard` |
| `/help` | List available commands | `/help` |

### Dual-Client Support

Both Telegram and Mac terminal are supported via format parameter:

```
GET /api/analyze?ticker=NVDA&format=telegram  → HTML with emoji
GET /api/analyze?ticker=NVDA&format=cli       → ASCII tables
GET /api/analyze?ticker=NVDA&format=json      → Raw JSON
```

**Mac Terminal Setup:**
```bash
# Add to ~/.zshrc
export IVCRUSH_URL="https://ivcrush-xxx.run.app"
alias iv='curl -s "$IVCRUSH_URL/api"'

# Usage
iv/analyze?ticker=NVDA&format=cli
iv/whisper?format=cli
iv/health?format=cli
```

**CLI Output Example (`iv/whisper?format=cli`):**
```
═══════════════════════════════════════════════════════
 Dec 12 EARNINGS (3 qualified)
═══════════════════════════════════════════════════════
 #  TICKER   VRP    SCORE  DIR      STRATEGY
───────────────────────────────────────────────────────
 1  AVGO     7.2x   82     BULLISH  Bull Put 165/160
    + AI tailwinds          - China risk
 2  LULU     4.8x   71     NEUTRAL  IC 380/400/420/440
    + Holiday sales         - Inventory
 3  ORCL     3.9x   65     BEARISH  Bear Call 145/150
    + Cloud growth          - Competition
───────────────────────────────────────────────────────
 Budget: 12/40 calls | $4.85 remaining
═══════════════════════════════════════════════════════
```

**CLI Output Example (`iv/analyze?ticker=AVGO&format=cli`):**
```
═══════════════════════════════════════════════════════
 AVGO Analysis - Dec 12 (AMC)
═══════════════════════════════════════════════════════
 VRP: 7.2x (EXCELLENT)    Score: 82
 Implied: 6.8%            Historical: 4.2%
 Liquidity: GOOD          OI: 3.2x | Spread: 9%
───────────────────────────────────────────────────────
 SENTIMENT: BULLISH (+0.7)
 + AI demand surge, data center growth
 - China exposure, margin pressure concerns
───────────────────────────────────────────────────────
 TOP STRATEGY: Bull Put Spread 165/160
 Credit: $2.10 | Max Risk: $2.90 | POP: 68%
 Size: 3 contracts (Half-Kelly)
═══════════════════════════════════════════════════════
```

## Database Strategy

### SQLite + Cloud Storage Sync

Databases remain SQLite (no migration to Firestore), synced via Cloud Storage:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Cloud Storage  │────▶│   Cloud Run     │────▶│  Cloud Storage  │
│  (source of     │     │  1. Download DB │     │  (updated DB)   │
│   truth)        │     │  2. Read/Write  │     │                 │
└─────────────────┘     │  3. Upload DB   │     └─────────────────┘
                        └─────────────────┘
```

### File Locking

Prevents concurrent write conflicts:

```python
import fcntl
from contextlib import contextmanager

@contextmanager
def db_write_lock():
    lock_file = "/tmp/ivcrush.lock"
    with open(lock_file, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
```

### Read-Only Pattern for Telegram

On-demand commands from Telegram use read-only mode (no DB upload) to avoid conflicts with scheduled jobs that may be writing.

## Secrets Management

### JSON Blob Approach

Single secret contains all API keys as JSON:

```json
{
  "TRADIER_API_KEY": "xxx",
  "PERPLEXITY_API_KEY": "xxx",
  "ALPHA_VANTAGE_KEY": "xxx",
  "TELEGRAM_BOT_TOKEN": "xxx",
  "GRAFANA_API_KEY": "xxx"
}
```

### Setup Flow

```bash
# Create secrets JSON file locally
cat > /tmp/secrets.json << 'EOF'
{
  "TRADIER_API_KEY": "your-key-here",
  ...
}
EOF

# Upload to Secret Manager
gcloud secrets create ivcrush-secrets --data-file=/tmp/secrets.json

# Delete local file immediately
rm /tmp/secrets.json

# Grant Cloud Run access
gcloud secrets add-iam-policy-binding ivcrush-secrets \
  --member="serviceAccount:ivcrush@PROJECT.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Loading in Cloud Run

```python
from google.cloud import secretmanager
import json

def load_secrets():
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT}/secrets/ivcrush-secrets/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return json.loads(response.payload.data.decode("UTF-8"))

# Usage
secrets = load_secrets()
tradier_key = secrets["TRADIER_API_KEY"]
```

## Notifications (Telegram)

### Bot Setup

1. Create bot via @BotFather
2. Get bot token
3. Set webhook: `https://api.telegram.org/bot{TOKEN}/setWebhook?url={CLOUD_RUN_URL}/telegram`

### Message Types

**Critical Alert (immediate push):**
```
🚨 AVGO | VRP 7.2x | Score 82

📊 BULLISH | Sentiment +0.7
✅ AI demand, data center growth
⚠️ China exposure, margin pressure

💰 Bull Put Spread 165/160
   Credit $2.10 | Risk $2.90 | POP 68%
```

**Morning Digest (7:30 AM):**
```
☀️ Dec 12 EARNINGS (3 qualified)

1. AVGO | 7.2x | 82 | BULLISH
   ✅ AI tailwinds  ⚠️ China risk
   → Bull Put 165/160 @ $2.10

2. LULU | 4.8x | 71 | NEUTRAL
   ✅ Holiday sales  ⚠️ Inventory
   → Iron Condor 380/400/420/440

3. ORCL | 3.9x | 65 | BEARISH
   ✅ Cloud growth  ⚠️ Competition
   → Bear Call 145/150 @ $1.85

Budget: 12/40 calls | $4.85 left
```

**Format Key:**
- `TICKER | VRP | Score | DIRECTION`
- `✅ Tailwinds  ⚠️ Headwinds`
- `→ Strategy @ Credit`

## Telemetry (Grafana Cloud)

Uses Graphite protocol for simple HTTP POST metrics push to Grafana Cloud.

### Implementation

- **Module**: `src/core/metrics.py`
- **Protocol**: Graphite (simple HTTP POST)
- **Push**: Synchronous, fire-and-forget after each request

### Metrics Exported

| Metric | Type | Description |
|--------|------|-------------|
| `ivcrush.request.duration` | timing | Endpoint latency (ms) |
| `ivcrush.request.status` | count | Success/error by endpoint |
| `ivcrush.vrp.ratio` | gauge | VRP ratio per ticker |
| `ivcrush.vrp.tier` | count | Count by VRP tier |
| `ivcrush.liquidity.tier` | count | Count by liquidity tier |
| `ivcrush.sentiment.score` | gauge | Sentiment score per ticker |
| `ivcrush.api.calls` | count | External API calls by provider |
| `ivcrush.api.latency` | timing | External API latency |
| `ivcrush.budget.calls_remaining` | gauge | Perplexity calls remaining |
| `ivcrush.budget.dollars_remaining` | gauge | Budget remaining |
| `ivcrush.tickers.qualified` | gauge | Qualified tickers from scan |

### Dashboards

Create in Grafana Cloud UI:
1. **Operations** - Request rate, error rate, P95 latency
2. **Trading** - VRP distribution, qualified tickers, tier breakdown
3. **API** - Calls by provider, latency, budget gauge
4. **Whisper** - Daily scan results, top VRP tickers

## Code Reuse Strategy

### PORT (Copy with minimal changes)

From `2.0/src/`:
- `application/metrics/vrp.py` - VRP calculation
- `application/metrics/liquidity_scorer.py` - Liquidity tiers
- `domain/scoring/strategy_scorer.py` - Composite scoring
- `domain/strategies/` - Strategy generation
- `domain/position_sizing.py` - Half-Kelly sizing

### REWRITE (New implementations)

All API integrations rewritten as direct REST calls (not MCP):
- `integrations/tradier.py` - Options chains, Greeks, current prices
- `integrations/perplexity.py` - Sentiment analysis
- `integrations/alphavantage.py` - Earnings calendar
- `integrations/yahoo.py` - Historical price data only
- `integrations/telegram.py` - Bot API

### NEW (From scratch)

- `jobs/` - All scheduled job handlers
- `api/` - FastAPI endpoints
- `formatters/` - Telegram HTML, CLI ASCII, JSON output
- `core/dispatcher.py` - Job routing and dependencies
- `core/db.py` - SQLite + Cloud Storage sync
- `core/config.py` - Timezone handling, settings

## Key Fixes Implemented

### 1. Timezone Handling (HIGH)

```python
# core/config.py
import pytz
from datetime import datetime

MARKET_TZ = pytz.timezone('America/New_York')

def now_et() -> datetime:
    """Current time in Eastern."""
    return datetime.now(MARKET_TZ)

def today_et() -> str:
    """Today's date in Eastern as YYYY-MM-DD."""
    return now_et().strftime('%Y-%m-%d')
```

### 2. Job Dependencies (HIGH)

```python
# core/job_manager.py
def check_dependencies(job_name: str) -> tuple[bool, str]:
    """Check if job's dependencies succeeded today."""
    deps = JOB_DEPENDENCIES.get(job_name, [])
    if not deps:
        return True, ""

    today = today_et()
    for dep in deps:
        status = get_job_status(dep, today)
        if status != "success":
            return False, f"Dependency '{dep}' not successful (status: {status})"

    return True, ""
```

### 3. Sentiment Cache TTL (MEDIUM)

```python
# Extended from 3 hours to 8 hours for pre-market caches
CACHE_TTL_HOURS = {
    "pre_market": 8,   # 5:30 AM cache valid until 1:30 PM
    "intraday": 3,     # Standard 3-hour TTL
    "after_hours": 12  # Evening cache valid until morning
}
```

### 4. API Rate Limiting (MEDIUM)

```python
# integrations/base.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

def is_retryable(exc):
    """Retry on 429 (rate limit) and 5xx errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout))

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception(is_retryable)
)
async def api_request(client, method, url, **kwargs):
    response = await client.request(method, url, **kwargs)
    response.raise_for_status()
    return response
```

### 5. Parallel API Calls (MEDIUM)

```python
# api/analyze.py
async def analyze_ticker(ticker: str, earnings_date: str):
    """Fetch all data in parallel for speed."""
    async with httpx.AsyncClient(timeout=30) as client:
        options_task = fetch_options_data(client, ticker, earnings_date)
        price_task = fetch_price_data(client, ticker)
        historical_task = asyncio.to_thread(get_historical_moves, ticker)

        options, price, historical = await asyncio.gather(
            options_task, price_task, historical_task,
            return_exceptions=True
        )

        # Handle any failures gracefully
        if isinstance(options, Exception):
            log("error", "Options fetch failed", ticker=ticker, error=str(options))
            options = None
        # ... process results
```

### 6. Structured Logging (MEDIUM)

```python
# core/logging.py
import json
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar('request_id', default='')

def get_request_id() -> str:
    return request_id_var.get() or str(uuid.uuid4())[:8]

def log(level: str, message: str, **context):
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "timestamp_et": now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
        "level": level,
        "request_id": get_request_id(),
        "message": message,
        **context
    }
    print(json.dumps(entry))
```

### 7. Market Half-Days (LOW)

```python
# core/market.py
HALF_DAYS_2025 = [
    "2025-07-03",   # Day before July 4th
    "2025-11-28",   # Day after Thanksgiving
    "2025-12-24",   # Christmas Eve
]

def get_market_close_time(date: str) -> str:
    """Returns market close time for given date."""
    if date in HALF_DAYS_2025:
        return "13:00"  # 1 PM ET
    return "16:00"  # 4 PM ET

def should_skip_afternoon_jobs(date: str) -> bool:
    """Skip 2:30 PM refresh on half-days (market closed at 1 PM)."""
    return date in HALF_DAYS_2025
```

### 8. Earnings Conflicts (LOW)

```python
# core/scoring.py
def break_ties(tickers: list[dict]) -> list[dict]:
    """
    When VRP scores are equal, break ties by:
    1. Higher implied move (more premium)
    2. Better liquidity tier
    3. Alphabetical (deterministic)
    """
    return sorted(tickers, key=lambda t: (
        -t['vrp_score'],           # Primary: higher VRP
        -t['implied_move_pct'],    # Tiebreaker 1: higher premium
        LIQUIDITY_RANK[t['liquidity_tier']],  # Tiebreaker 2: better liquidity
        t['ticker']                # Tiebreaker 3: alphabetical
    ))

LIQUIDITY_RANK = {"EXCELLENT": 0, "GOOD": 1, "WARNING": 2, "REJECT": 3}
```

## Directory Structure

```
5.0/
├── DESIGN.md                 # This document
├── Dockerfile
├── requirements.txt
├── main.py                   # FastAPI app entry point
│
├── api/                      # HTTP endpoints
│   ├── __init__.py
│   ├── dispatch.py           # Scheduled job dispatcher
│   ├── analyze.py            # /analyze endpoint
│   ├── whisper.py            # /whisper endpoint
│   ├── history.py            # /history endpoint
│   ├── health.py             # /health endpoint
│   └── telegram.py           # Webhook handler
│
├── jobs/                     # Scheduled job handlers
│   ├── __init__.py
│   ├── pre_market_prep.py
│   ├── sentiment_scan.py
│   ├── morning_digest.py
│   ├── market_refresh.py
│   ├── after_hours.py
│   ├── outcome_recorder.py
│   ├── evening_summary.py
│   ├── weekly_backfill.py
│   ├── weekly_backup.py
│   ├── weekly_cleanup.py
│   └── calendar_sync.py
│
├── core/                     # Core utilities
│   ├── __init__.py
│   ├── config.py             # Settings, timezone
│   ├── db.py                 # SQLite + Cloud Storage
│   ├── logging.py            # Structured JSON logging
│   ├── market.py             # Market hours, half-days
│   ├── job_manager.py        # Dependencies, status tracking
│   └── scoring.py            # Tie-breaking, ranking
│
├── domain/                   # Business logic (ported from 2.0)
│   ├── __init__.py
│   ├── vrp.py                # VRP calculation
│   ├── liquidity.py          # Liquidity scoring
│   ├── implied_move.py       # Implied move from ATM straddle (shared helpers)
│   ├── strategies.py         # Strategy generation
│   ├── position_sizing.py    # Half-Kelly
│   └── scoring.py            # Composite score
│
├── integrations/             # External API clients
│   ├── __init__.py
│   ├── base.py               # Retry logic, rate limiting
│   ├── tradier.py
│   ├── perplexity.py
│   ├── alphavantage.py
│   ├── yahoo.py
│   └── telegram.py
│
├── formatters/               # Output formatting
│   ├── __init__.py
│   ├── telegram.py           # HTML with emoji
│   ├── cli.py                # ASCII tables
│   └── json.py               # Raw JSON
│
├── tests/                    # Test suite
│   ├── __init__.py
│   ├── test_vrp.py
│   ├── test_dispatcher.py
│   └── ...
│
└── scripts/                  # Deployment scripts
    ├── deploy.sh
    ├── setup_secrets.sh
    └── local_dev.sh
```

## Deployment

### Initial Setup

```bash
# 1. Create GCP project
gcloud projects create ivcrush-prod

# 2. Enable APIs
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com

# 3. Create service account
gcloud iam service-accounts create ivcrush

# 4. Upload secrets
./scripts/setup_secrets.sh

# 5. Create storage bucket
gsutil mb gs://ivcrush-data

# 6. Upload initial databases
gsutil cp 2.0/data/ivcrush.db gs://ivcrush-data/
gsutil cp 4.0/data/sentiment_cache.db gs://ivcrush-data/

# 7. Deploy Cloud Run
gcloud run deploy ivcrush \
  --source . \
  --region us-east4 \
  --service-account ivcrush@PROJECT.iam.gserviceaccount.com \
  --set-secrets=SECRETS=ivcrush-secrets:latest \
  --min-instances=0 \
  --max-instances=1

# 8. Create scheduler job
gcloud scheduler jobs create http ivcrush-dispatcher \
  --schedule="*/15 * * * *" \
  --uri="https://ivcrush-xxx.run.app/dispatch" \
  --http-method=POST \
  --oidc-service-account-email=ivcrush@PROJECT.iam.gserviceaccount.com

# 9. Set Telegram webhook
curl "https://api.telegram.org/bot{TOKEN}/setWebhook?url=https://ivcrush-xxx.run.app/telegram"
```

### Local Development

```bash
# Run locally with hot reload
cd 5.0
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set environment variables
export SECRETS='{"TRADIER_API_KEY":"xxx",...}'
export GCS_BUCKET="ivcrush-data-dev"

# Run
uvicorn main:app --reload --port 8080
```

## Migration Path

### Phase 1: Core Infrastructure
1. Create GCP project and enable services
2. Set up secrets and storage
3. Deploy minimal Cloud Run service
4. Verify dispatcher works

### Phase 2: Port Business Logic
1. Port VRP calculation from 2.0
2. Port liquidity scoring
3. Port strategy generation
4. Add unit tests

### Phase 3: Integrations
1. Implement Tradier client
2. Implement Perplexity client
3. Implement Yahoo Finance client
4. Add integration tests

### Phase 4: Jobs ✅
1. ✅ Implement pre-market-prep
2. ✅ Implement sentiment-scan
3. ✅ Implement morning-digest
4. ✅ Implement market-open-refresh
5. ✅ Implement pre-trade-refresh
6. ✅ Implement after-hours-check
7. ✅ Implement outcome-recorder
8. ✅ Implement evening-summary
9. ✅ Implement weekly-backfill
10. ✅ Implement weekly-backup
11. ✅ Implement weekly-cleanup
12. ✅ Implement calendar-sync

### Phase 5: Commands
1. Implement /analyze
2. Implement /whisper
3. Implement remaining commands
4. Add Telegram bot

### Phase 6: Telemetry ✅
1. ✅ Implement metrics module (`src/core/metrics.py`)
2. ✅ Instrument endpoints with metrics
3. ✅ Add `/dashboard` bot command
4. Create dashboards in Grafana Cloud UI (manual step)

## Success Metrics

| Metric | Target |
|--------|--------|
| Morning digest delivery | Before 6:30 AM ET |
| API call budget | <40 calls/day |
| Job success rate | >99% |
| On-demand response time | <5 seconds |
| Monthly infrastructure cost | $0 |

## Appendix: Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SECRETS` | JSON blob from Secret Manager | Yes |
| `GCS_BUCKET` | Cloud Storage bucket name | Yes |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID | Yes |
| `GRAFANA_GRAPHITE_URL` | Grafana Cloud Graphite endpoint | No |
| `GRAFANA_USER` | Grafana Cloud instance ID | No |
| `GRAFANA_API_KEY` | Grafana Cloud API key | No |
| `GRAFANA_DASHBOARD_URL` | Link to main dashboard | No |
| `LOG_LEVEL` | DEBUG, INFO, WARN, ERROR | No (default: INFO) |
