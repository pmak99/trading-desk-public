# Trading Desk 5.0 - Autopilot

24/7 cloud-native trading system for IV Crush strategies. Transforms manual CLI commands into automated pre-market scans, real-time alerts, and Telegram notifications.

## Features

- **Automated Daily Workflow** - Pre-market prep, sentiment analysis, digests, refreshes
- **Telegram Bot** - Mobile access via `/health`, `/whisper`, `/analyze TICKER`
- **VRP-Based Scoring** - Volatility Risk Premium calculations from proven 2.0 system
- **AI Sentiment** - Perplexity-powered sentiment with budget tracking
- **Cloud-Native** - Runs on GCP Cloud Run with ~$6/month cost
- **Trading Desk** - Part of the Trading Desk ecosystem (2.0, 3.0, 4.0, 5.0)

## Quick Start (Local Development)

### 1. Clone and Setup

```bash
cd "/Users/prashant/PycharmProjects/Trading Desk/5.0"

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy template
cp .env.template .env

# Edit with your API keys
open .env
```

**Required API Keys:**
| Key | Source | Purpose |
|-----|--------|---------|
| `TRADIER_API_KEY` | [developer.tradier.com](https://developer.tradier.com) | Options chains, Greeks |
| `ALPHA_VANTAGE_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) | Earnings calendar |
| `PERPLEXITY_API_KEY` | [perplexity.ai](https://www.perplexity.ai/settings/api) | AI sentiment |
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram | Bot notifications |
| `TELEGRAM_CHAT_ID` | See setup guide below | Your chat ID |

### 3. Run Locally

```bash
# Load environment
source venv/bin/activate
export $(cat .env | xargs)

# Start server
uvicorn src.main:app --reload --port 8080
```

### 4. Test Endpoints

```bash
# Health check
curl http://localhost:8080/

# API health with budget
curl http://localhost:8080/api/health

# Analyze a ticker
curl "http://localhost:8080/api/analyze?ticker=AAPL"

# Find opportunities
curl http://localhost:8080/api/whisper
```

## Docker Setup

### Build and Run

```bash
# Build image
docker build -t ivcrush:local .

# Run with environment file
docker run -p 8080:8080 --env-file .env -v $(pwd)/data:/app/data ivcrush:local
```

### Using Docker Compose

```bash
# Start
docker compose up --build

# Stop
docker compose down

# View logs
docker compose logs -f
```

## Telegram Bot Setup

### 1. Create Bot

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`
3. Enter name: `Trading Desk Alerts`
4. Enter username: `trading_desk_yourname_bot`
5. **Save the bot token**

### 2. Get Your Chat ID

1. Start a chat with your bot
2. Send `/start`
3. Open: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Find `"chat":{"id":123456789}` - that's your chat ID

### 3. Test Bot

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<CHAT_ID>" \
  -d "text=🎉 Trading Desk bot is alive!"
```

### Bot Commands

| Command | Description |
|---------|-------------|
| `/health` | System status and API budget |
| `/whisper` | Today's high-VRP opportunities |
| `/analyze TICKER` | Deep analysis of specific ticker |
| `/dashboard` | Link to Grafana metrics dashboard |

**Ticker Aliases**: Common company names are automatically converted:
- `NIKE` → `NKE`, `GOOGLE` → `GOOGL`, `FACEBOOK` → `META`
- `AMAZON` → `AMZN`, `COSTCO` → `COST`, `WALMART` → `WMT`, etc.

## API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/` | GET | None | Health check (public) |
| `/api/health` | GET | API Key | System health + budget info |
| `/api/analyze?ticker=XXX` | GET | API Key | Deep ticker analysis |
| `/api/whisper` | GET | API Key | Find high-VRP opportunities |
| `/dispatch` | POST | API Key | Scheduler dispatch (internal) |
| `/telegram` | POST | Webhook Secret | Telegram webhook handler |

### Authentication

Protected endpoints require the `X-API-Key` header:

```bash
# Set your API key
export API_KEY="your_api_key_from_env_file"

# Call protected endpoint
curl -H "X-API-Key: $API_KEY" "https://trading-desk-670614791512.us-east1.run.app/api/health"
```

### Response Formats

Add `?format=cli` for terminal output, `?format=json` for raw data (default).

```bash
# JSON (default)
curl -H "X-API-Key: $API_KEY" "https://trading-desk-670614791512.us-east1.run.app/api/analyze?ticker=AAPL"

# CLI formatted
curl -H "X-API-Key: $API_KEY" "https://trading-desk-670614791512.us-east1.run.app/api/analyze?ticker=AAPL&format=cli"
```

## Configuration

### Environment Variables

```bash
# API Keys (individual - recommended for local dev)
TRADIER_API_KEY=xxx
ALPHA_VANTAGE_KEY=xxx
PERPLEXITY_API_KEY=xxx
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# Security (generate with: openssl rand -hex 32)
API_KEY=xxx
TELEGRAM_WEBHOOK_SECRET=xxx

# Database
DB_PATH=data/ivcrush.db

# GCP (production only)
GOOGLE_CLOUD_PROJECT=trading-desk-prod
GCS_BUCKET=trading-desk-data
```

### Secret Priority Order

The system loads secrets in this order:
1. **Individual env vars** (for local development)
2. **SECRETS JSON blob** (for Docker/Cloud Run)
3. **GCP Secret Manager** (production fallback)

## Scoring System

### VRP (Volatility Risk Premium)

```
VRP Ratio = Implied Move / Historical Mean Move
```

**Historical Mean** uses **intraday moves** (open-to-close on earnings day), not gap moves, to match 2.0 methodology.

| Tier | VRP Ratio | Action |
|------|-----------|--------|
| EXCELLENT | ≥ 7.0x | High confidence |
| GOOD | ≥ 4.0x | Tradeable |
| MARGINAL | ≥ 1.5x | Reduce size |
| SKIP | < 1.5x | No edge |

### Composite Score

| Factor | Weight |
|--------|--------|
| VRP Edge | 55% |
| Move Difficulty | 25% |
| Liquidity | 20% |

### Sentiment Modifier

| Sentiment | Modifier |
|-----------|----------|
| Strong Bullish | +12% |
| Bullish | +7% |
| Neutral | 0% |
| Bearish | -7% |
| Strong Bearish | -12% |

## Liquidity Tiers

| Tier | OI/Position | Spread | Action |
|------|-------------|--------|--------|
| EXCELLENT | ≥5x | ≤8% | Full size |
| GOOD | 2-5x | 8-12% | Full size |
| WARNING | 1-2x | 12-15% | Reduce size |
| REJECT | <1x | >15% | **Never trade** |

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
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      External Services                           │
├─────────────────────────────────────────────────────────────────┤
│  Tradier       │  Options chains, Greeks, IV                    │
│  Alpha Vantage │  Earnings calendar                             │
│  Yahoo Finance │  Stock prices, historical data                 │
│  Perplexity    │  AI sentiment analysis                         │
│  Telegram      │  Notifications, commands                       │
└─────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
5.0/
├── src/
│   ├── main.py              # FastAPI app entry point
│   ├── core/                # Core utilities
│   │   ├── config.py        # Settings, timezone
│   │   ├── logging.py       # Structured logging
│   │   ├── job_manager.py   # Job scheduling
│   │   └── budget.py        # API budget tracking
│   ├── domain/              # Business logic
│   │   ├── vrp.py           # VRP calculation
│   │   ├── liquidity.py     # Liquidity tiers
│   │   ├── scoring.py       # Composite scoring
│   │   └── strategies.py    # Strategy generation
│   ├── integrations/        # API clients
│   │   ├── tradier.py
│   │   ├── perplexity.py
│   │   ├── alphavantage.py
│   │   ├── yahoo.py
│   │   └── telegram.py
│   ├── formatters/          # Output formatting
│   │   ├── telegram.py      # HTML with emoji
│   │   └── cli.py           # ASCII tables
│   └── jobs/                # Scheduled jobs
│       └── __init__.py
├── tests/                   # Test suite
├── data/                    # SQLite database
├── Dockerfile
├── docker-compose.yml
├── cloudbuild.yaml          # GCP Cloud Build
├── requirements.txt
├── .env.template
├── DEPLOYMENT.md            # Full deployment guide
├── DESIGN.md                # Architecture details
└── README.md                # This file
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=src

# Run specific test
pytest tests/test_vrp.py -v
```

## Deployment

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for complete instructions including:
- GCP project setup
- Secret Manager configuration
- Cloud Run deployment
- Cloud Scheduler setup
- Telegram webhook registration

### Quick Deploy

```bash
# Build and deploy to Cloud Run
gcloud builds submit --tag gcr.io/trading-desk-prod/trading-desk
gcloud run deploy trading-desk --image gcr.io/trading-desk-prod/trading-desk --region us-east1
```

**Live Service:** https://trading-desk-670614791512.us-east1.run.app

## Budget Management

### Perplexity API

- **Daily limit**: 40 calls
- **Monthly budget**: $5.00
- **Model**: `sonar` (as of Dec 2024)
- Costs ~$0.002 per sentiment analysis ($1/M tokens)

### Monitoring

```bash
# Check budget via API
curl http://localhost:8080/api/health

# Via Telegram
/health
```

## Metrics & Dashboards

Optional Grafana Cloud integration for visual monitoring.

### Setup

1. Sign up at [grafana.com](https://grafana.com) (free tier)
2. Create a Grafana Cloud stack
3. Get your Graphite endpoint URL and API key
4. Add to `.env`:
   ```bash
   GRAFANA_GRAPHITE_URL=https://graphite-prod-xx-xxx.grafana.net/graphite/metrics
   GRAFANA_USER=your_instance_id
   GRAFANA_API_KEY=your_api_key
   GRAFANA_DASHBOARD_URL=https://your-org.grafana.net/d/xxx/trading-desk
   ```

### Metrics Collected

| Metric | Type | Description |
|--------|------|-------------|
| `ivcrush.request.duration` | timing | Endpoint latency |
| `ivcrush.request.status` | count | Success/error by endpoint |
| `ivcrush.vrp.ratio` | gauge | VRP ratio per ticker |
| `ivcrush.vrp.tier` | count | Count by tier |
| `ivcrush.api.calls` | count | External API calls |
| `ivcrush.budget.remaining` | gauge | Perplexity budget |

### Dashboards

Create 4 dashboards in Grafana UI:
1. **Operations** - Request rate, error rate, latency
2. **Trading** - VRP distribution, qualified tickers
3. **API** - Calls by provider, budget burn
4. **Whisper** - Daily scan results

## Scheduled Jobs

All 12 scheduled jobs are fully implemented with rate limiting, error tracking, and metrics.

### Weekday Jobs (Mon-Fri)

| Time (ET) | Job | Description |
|-----------|-----|-------------|
| 5:30 AM | `pre-market-prep` | Fetch earnings calendar, calculate VRP for upcoming tickers |
| 6:30 AM | `sentiment-scan` | Pre-cache AI sentiment for high-VRP tickers (≥3x) |
| 7:30 AM | `morning-digest` | Send Telegram digest with top 10 ranked opportunities |
| 10:00 AM | `market-open-refresh` | Refresh prices, alert on significant pre-market moves |
| 2:30 PM | `pre-trade-refresh` | Final VRP validation, actionable alert for AMC earnings |
| 4:30 PM | `after-hours-check` | Monitor after-hours prices, alert on moves >1% |
| 7:00 PM | `outcome-recorder` | Record same-day earnings moves to database |
| 8:00 PM | `evening-summary` | Daily summary notification |

### Weekend Jobs

| Day | Time (ET) | Job | Description |
|-----|-----------|-----|-------------|
| Sat | 4:00 AM | `weekly-backfill` | Backfill historical moves for past 7 days |
| Sun | 3:00 AM | `weekly-backup` | Integrity check + upload database to GCS |
| Sun | 3:30 AM | `weekly-cleanup` | Clear expired sentiment cache entries |
| Sun | 4:00 AM | `calendar-sync` | Refresh 3-month earnings calendar |

### Job Features

- **Rate limiting**: 0.5s delay every 5 API calls
- **Error tracking**: Failed tickers tracked for debugging
- **Duplicate detection**: Backfill skips already-recorded earnings
- **Metrics**: Duration and counts recorded for all jobs
- **Telegram alerts**: Error handling for notification failures

## Troubleshooting

### Common Issues

**"No historical data" error**
- Ensure `data/ivcrush.db` exists with historical_moves data
- Copy from 2.0: `cp ../2.0/data/ivcrush.db data/`
- Use stock ticker symbol (e.g., `NKE` not `NIKE`) - common names are auto-converted

**Telegram bot not responding**
- Check webhook is set correctly
- Verify bot token and chat ID
- Check Cloud Run logs for errors

**Rate limit errors**
- System has automatic retry with exponential backoff
- Check API budget limits

### Logs

```bash
# Local
docker compose logs -f

# Cloud Run
gcloud logs read --service=trading-desk --limit=50
```

## License

Private - Internal use only

## Related Projects

- **2.0/** - Core VRP/strategy math (production)
- **4.0/** - AI sentiment layer
- **3.0/** - ML enhancement (development)
