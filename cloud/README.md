# cloud Cloud Autopilot

24/7 cloud-native trading system on GCP Cloud Run. Transforms manual CLI commands into automated pre-market scans, real-time alerts, and Telegram notifications.

**Live:** https://your-cloud-run-url.run.app

## Features

- **12 Scheduled Jobs** - Pre-market prep, sentiment scan, morning digest, outcome recording
- **Telegram Bot** - Mobile access via `/health`, `/whisper`, `/analyze TICKER`, `/council TICKER`
- **Full VRP Stack** - VRP, liquidity, skew, and 3-rule direction system (ported from core/sentiment)
- **AI Sentiment** - Perplexity + Finnhub powered with budget tracking
- **Council** - 6-source AI sentiment consensus for pre-earnings analysis
- **Performance Optimized** - Parallel analysis (5 concurrent), VRP caching (1-6hr TTL)

## Quick Start

### Local Development

```bash
cd cloud
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.template .env   # Edit with API keys

export $(cat .env | xargs)
uvicorn src.main:app --reload --port 8080
```

### Docker

```bash
docker build -t ivcrush:local .
docker run -p 8080:8080 --env-file .env -v $(pwd)/data:/app/data ivcrush:local

# Or with compose
docker compose -f docker-compose.yml up --build
```

## API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/` | GET | None | Health check (public) |
| `/api/health` | GET | API Key | System health + budget |
| `/api/analyze?ticker=XXX` | GET | API Key | Deep ticker analysis |
| `/api/whisper` | GET | API Key | High-VRP opportunities |
| `/api/scan?date=YYYY-MM-DD` | GET | API Key | Scan specific date |
| `/api/council?ticker=XXX` | GET | API Key | 6-source AI sentiment council |
| `/api/budget` | GET | API Key | 7-day spending history |
| `/prime` | POST | API Key | Pre-cache sentiment |
| `/dispatch` | POST | API Key | Job scheduler trigger |
| `/telegram` | POST | Webhook | Bot commands |

**Auth:** `curl -H "X-API-Key: $KEY" https://your-cloud-run-url.run.app/api/health`

**Rate Limit:** 60 requests/minute per IP (in-memory sliding window)

## Telegram Bot

| Command | Description |
|---------|-------------|
| `/health` | System status and budget |
| `/whisper` | Today's high-VRP opportunities |
| `/analyze TICKER` | Deep analysis |
| `/council TICKER` | 6-source AI sentiment consensus |
| `/dashboard` | Grafana metrics link |

**Aliases:** `NIKE`->`NKE`, `GOOGLE`->`GOOGL`, `FACEBOOK`->`META`

## Scheduled Jobs

### Weekday (Mon-Fri ET)

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

### Weekend

| Day | Time | Job |
|-----|------|-----|
| Sat | 4:00 AM | weekly-backfill (past 7 days) |
| Sun | 3:00 AM | weekly-backup (DB integrity + GCS) |
| Sun | 3:30 AM | weekly-cleanup (expired cache) |
| Sun | 4:00 AM | calendar-sync (3-month refresh) |

## Architecture

```
cloud/
├── src/
│   ├── main.py              # FastAPI entry point
│   ├── api/                 # API layer
│   │   ├── routers/         # analysis, health, jobs, operations, webhooks
│   │   ├── middleware.py    # Rate limiting, API key auth
│   │   ├── dependencies.py  # FastAPI dependency injection
│   │   └── state.py         # Application state management
│   ├── core/                # Config, logging, metrics, job manager, database
│   ├── domain/              # VRP, liquidity, scoring, skew, direction, strategies
│   ├── integrations/        # Tradier, Perplexity, Finnhub, Telegram, Yahoo, AlphaVantage
│   ├── formatters/          # Telegram HTML, CLI ASCII formatters
│   ├── application/         # Business logic (filters)
│   └── jobs/                # Scheduled job implementations
├── terraform/               # GCP infrastructure (Cloud Run, monitoring)
├── data/ivcrush.db          # SQLite database (gitignored)
├── deploy.sh                # Deploy script (syncs DB + deploys)
├── Dockerfile
├── docker-compose.yml
├── DEPLOYMENT.md            # Full deployment guide
└── DESIGN.md                # Architecture details
```

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete instructions.

```bash
cd cloud
./deploy.sh              # Full deploy with DB sync
./deploy.sh --quick      # Code-only deploy (faster)
```

## Configuration

```bash
# API Keys
TRADIER_API_KEY=xxx
ALPHA_VANTAGE_KEY=xxx
PERPLEXITY_API_KEY=xxx
FINNHUB_API_KEY=xxx
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# Security
API_KEY=xxx
TELEGRAM_WEBHOOK_SECRET=xxx

# Database
DB_PATH=data/ivcrush.db

# GCP
GOOGLE_CLOUD_PROJECT=your-gcp-project
GCS_BUCKET=your-gcs-bucket
```

## Monthly Cost

| Item | Cost |
|------|------|
| Perplexity API | ~$3-5 |
| GCP services | ~$1 |
| **Total** | **~$6/month** |

## Testing

```bash
cd cloud
../core/venv/bin/python -m pytest tests/ -v    # 507 tests
```

---

**Disclaimer:** For research purposes only. Not financial advice.
