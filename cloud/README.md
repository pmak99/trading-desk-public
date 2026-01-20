# 5.0 Cloud Autopilot

24/7 cloud-native trading system running on GCP Cloud Run. Transforms manual CLI commands into automated pre-market scans, real-time alerts, and Telegram notifications.

**Live Service:** https://your-cloud-run-url.run.app

## Features

- **Automated Daily Workflow** - 12 scheduled jobs (pre-market prep, sentiment scan, digests)
- **Telegram Bot** - Mobile access via `/health`, `/whisper`, `/analyze TICKER`
- **VRP-Based Scoring** - Same calculations as 2.0 core engine
- **AI Sentiment** - Perplexity-powered with budget tracking
- **Performance Optimized** - Parallel analysis, VRP caching, batch queries

## Quick Start

### Local Development

```bash
cd 5.0
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.template .env
# Edit .env with API keys

# Run
export $(cat .env | xargs)
uvicorn src.main:app --reload --port 8080
```

### Docker

```bash
docker build -t ivcrush:local .
docker run -p 8080:8080 --env-file .env -v $(pwd)/data:/app/data ivcrush:local

# Or with compose
docker compose up --build
```

## API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/` | GET | None | Health check (public) |
| `/api/health` | GET | API Key | System health + budget |
| `/api/analyze?ticker=XXX` | GET | API Key | Deep ticker analysis |
| `/api/whisper` | GET | API Key | Find high-VRP opportunities |
| `/api/scan?date=YYYY-MM-DD` | GET | API Key | Scan specific date |
| `/api/budget` | GET | API Key | 7-day spending history |
| `/prime` | POST | API Key | Pre-cache sentiment |
| `/dispatch` | POST | API Key | Job scheduler trigger |
| `/telegram` | POST | Webhook Secret | Bot commands |

### Authentication

```bash
curl -H "X-API-Key: $API_KEY" "https://your-cloud-run-url.run.app/api/health"
```

### Response Formats

Add `?format=cli` for terminal output, `?format=json` for raw data (default).

## Telegram Bot

### Setup

1. Create bot via @BotFather on Telegram
2. Get chat ID from `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Configure in `.env`

### Commands

| Command | Description |
|---------|-------------|
| `/health` | System status and budget |
| `/whisper` | Today's high-VRP opportunities |
| `/analyze TICKER` | Deep analysis |
| `/dashboard` | Grafana metrics link |

**Ticker Aliases:** `NIKE`->`NKE`, `GOOGLE`->`GOOGL`, `FACEBOOK`->`META`, etc.

## Scheduled Jobs

### Weekday Jobs (Mon-Fri ET)

| Time | Job | Description |
|------|-----|-------------|
| 5:30 AM | pre-market-prep | Fetch earnings, calculate VRP |
| 6:30 AM | sentiment-scan | Pre-cache sentiment for high-VRP |
| 7:30 AM | morning-digest | Top 10 opportunities to Telegram |
| 10:00 AM | market-open-refresh | Refresh prices after open |
| 2:30 PM | pre-trade-refresh | Final VRP validation |
| 4:30 PM | after-hours-check | Monitor after-hours moves |
| 7:00 PM | outcome-recorder | Record earnings outcomes |
| 8:00 PM | evening-summary | Daily summary notification |

### Weekend Jobs

| Day | Time | Job | Description |
|-----|------|-----|-------------|
| Sat | 4:00 AM | weekly-backfill | Backfill past 7 days |
| Sun | 3:00 AM | weekly-backup | DB integrity + GCS upload |
| Sun | 3:30 AM | weekly-cleanup | Clear expired cache |
| Sun | 4:00 AM | calendar-sync | Refresh 3-month calendar |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Cloud Run (Trading Desk)                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    FastAPI App                           │    │
│  │  /dispatch  → Job Manager → Job Runner                   │    │
│  │  /api/*     → Analyze, Whisper, Health                   │    │
│  │  /telegram  → Bot Commands                               │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      External Services                           │
├─────────────────────────────────────────────────────────────────┤
│  Tradier       │  Options chains, Greeks, IV                    │
│  Alpha Vantage │  Earnings calendar                             │
│  Yahoo Finance │  Historical prices                             │
│  Perplexity    │  AI sentiment                                  │
│  Telegram      │  Notifications                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
5.0/
├── src/
│   ├── main.py              # FastAPI entry point
│   ├── core/                # Config, logging, job manager
│   ├── domain/              # VRP, liquidity, scoring
│   ├── integrations/        # Tradier, Perplexity, Telegram
│   ├── formatters/          # Telegram HTML, CLI ASCII
│   └── jobs/                # Scheduled job implementations
├── terraform/               # GCP infrastructure
├── data/ivcrush.db          # SQLite database
├── Dockerfile
├── DEPLOYMENT.md            # Full deployment guide
└── DESIGN.md                # Architecture details
```

## Performance Optimizations

### Parallel Ticker Analysis

```python
MAX_CONCURRENT_ANALYSIS = 5  # Respects Tradier rate limits
```

**Impact:** ~60s -> ~15s for whisper scan (4x faster)

### VRP Caching

| Earnings Distance | TTL | Rationale |
|-------------------|-----|-----------|
| > 3 days | 6 hours | Stable IV |
| <= 3 days | 1 hour | Rapid IV changes |

**Impact:** 89% reduction in Tradier API calls

### Batch Database Queries

Uses window functions for per-ticker limiting:

```sql
SELECT * FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY earnings_date DESC) as rn
  FROM historical_moves WHERE ticker IN (...)
) WHERE rn <= 12
```

**Impact:** 97% reduction in queries

### Ticker Whitelist Filtering

Alert jobs only process tickers from `historical_moves` table (429 tracked tickers). This filters out OTC/foreign stocks that:
- Have unreliable price data
- Don't have options (can't trade VRP strategy)

| Job Type | Filter | Purpose |
|----------|--------|---------|
| Alert jobs | Whitelist | Clean alerts, only tracked tickers |
| Recording jobs | `is_valid_ticker()` only | New tickers build history |

**Alert jobs (use whitelist):** pre-market-prep, sentiment-scan, morning-digest, market-open-refresh, pre-trade-refresh, after-hours-check, evening-summary

**Recording jobs (open to new tickers):** outcome-recorder, weekly-backfill

New tickers get recorded by `outcome-recorder`, then appear in alerts after building 4+ quarters of history.

## Configuration

```bash
# API Keys
TRADIER_API_KEY=xxx
ALPHA_VANTAGE_KEY=xxx
PERPLEXITY_API_KEY=xxx
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# Security
API_KEY=xxx
TELEGRAM_WEBHOOK_SECRET=xxx

# Database
DB_PATH=data/ivcrush.db

# GCP (production)
GOOGLE_CLOUD_PROJECT=your-gcp-project
GCS_BUCKET=your-gcs-bucket
```

### Secret Priority

1. Individual env vars (local dev)
2. SECRETS JSON blob (Docker/Cloud Run)
3. GCP Secret Manager (production fallback)

## Testing

```bash
cd 5.0
../2.0/venv/bin/python -m pytest tests/ -v    # 228 tests
```

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete instructions.

### Quick Deploy

```bash
# Build and push to Artifact Registry (Container Registry is deprecated)
gcloud builds submit --tag us-docker.pkg.dev/your-gcp-project/gcr.io/trading-desk:latest \
  --project=your-gcp-project

# Deploy to Cloud Run
gcloud run deploy trading-desk \
  --image us-docker.pkg.dev/your-gcp-project/gcr.io/trading-desk:latest \
  --region us-east1 \
  --project your-gcp-project

# IMPORTANT: Run calendar-sync after deployment (database is ephemeral)
curl -X POST "https://your-cloud-run-url.run.app/dispatch?force=calendar-sync" \
  -H "X-API-Key: $API_KEY"
```

**Note:** Cloud Run instances use ephemeral SQLite. After each deployment, run `calendar-sync` to populate the earnings calendar from Alpha Vantage.

## Budget

| Item | Cost |
|------|------|
| Perplexity API | ~$3-5/month |
| GCP services | ~$1/month |
| **Total** | **~$6/month** |

## Related Systems

- **2.0/** - Core VRP math
- **4.0/** - AI sentiment layer
- **6.0/** - Agent orchestration (local alternative)

---

**Disclaimer:** For research purposes only. Not financial advice.
