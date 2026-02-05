# 5.0 Cloud Autopilot

24/7 cloud-native trading system running on GCP Cloud Run. Transforms manual CLI commands into automated pre-market scans, real-time alerts, and Telegram notifications.

**Live Service:** https://your-cloud-run-url.run.app

## Strategy Performance (2025 Verified Data)

| Strategy | Trades | Win Rate | Total P&L | Recommendation |
|----------|-------:|:--------:|----------:|----------------|
| **SINGLE** | 108 | **~60-65%** | **strongest performer** | Preferred |
| SPREAD | 86 | ~50-55% | positive | Good |
| STRANGLE | 6 | ~33% | negative | Avoid |
| IRON_CONDOR | 3 | ~67% | significant loss (sizing) | Caution |

## Features

- **Automated Daily Workflow** - 12 scheduled jobs (pre-market prep, sentiment scan, digests)
- **Telegram Bot** - Mobile access via `/health`, `/whisper`, `/analyze TICKER`
- **VRP-Based Scoring** - Same calculations as 2.0 core engine
- **Skew Analysis** - Polynomial-fitted IV skew for directional bias (ported from 2.0)
- **3-Rule Direction System** - Combines skew + sentiment for direction (ported from 4.0)
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

## Critical Rules

1. **Prefer SINGLE options** - 64% vs 52% win rate vs spreads
2. **Respect TRR limits** - LOW TRR: strong profit, HIGH TRR: significant loss
3. **Never roll** - 0% success rate, always makes losses worse
4. **Reduce size for REJECT liquidity** - allowed but penalized in scoring

## TRR Performance

| Level | Win Rate | P&L | Recommendation |
|-------|:--------:|----:|----------------|
| **LOW** (<1.5x) | **70.6%** | **strong profit** | Preferred |
| NORMAL (1.5-2.5x) | 56.5% | moderate loss | Standard |
| HIGH (>2.5x) | 54.8% | significant loss | **Avoid** |

## Telegram Bot

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
│   ├── domain/              # VRP, liquidity, scoring, skew, direction
│   ├── integrations/        # Tradier, Perplexity, Telegram
│   ├── formatters/          # Telegram HTML, CLI ASCII
│   └── jobs/                # Scheduled job implementations
├── terraform/               # GCP infrastructure
├── data/ivcrush.db          # SQLite database (gitignored)
├── deploy.sh                # Deploy script (syncs DB + deploys)
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

## Testing

```bash
cd 5.0
../2.0/venv/bin/python -m pytest tests/ -v    # 308 tests
```

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete instructions.

### Quick Deploy

```bash
cd 5.0

# Recommended: Use deploy script (syncs database automatically)
./deploy.sh              # Full deploy with DB sync
./deploy.sh --quick      # Code-only deploy (faster)
./deploy.sh --help       # Show usage

# Manual deploy (without DB sync)
gcloud run deploy trading-desk \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --memory 512Mi \
  --set-secrets 'SECRETS=trading-desk-secrets:latest' \
  --set-env-vars 'GOOGLE_CLOUD_PROJECT=your-gcp-project,GCS_BUCKET=your-gcs-bucket'
```

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
