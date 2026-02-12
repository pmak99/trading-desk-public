# IV Crush cloud - Autopilot

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

Instead of 11 separate Cloud Scheduler jobs, we use ONE job that runs every 15 minutes and dispatches to the correct handler based on current time:

```
Cloud Scheduler: */15 * * * * → POST /dispatch
```

> Detailed job slot assignments, dependencies, configuration constants, and handler implementation removed from public version.

## On-Demand Commands

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

## Notifications (Telegram)

### Message Types

**Critical Alert (immediate push):**
```
AVGO | VRP 7.2x | Score 82
BULLISH | Sentiment +0.7
Bull Put Spread 165/160
   Credit $2.10 | Risk $2.90 | POP 68%
```

**Morning Digest (7:30 AM):**
```
Dec 12 EARNINGS (3 qualified)

1. AVGO | 7.2x | 82 | BULLISH
2. LULU | 4.8x | 71 | NEUTRAL
3. ORCL | 3.9x | 65 | BEARISH
```

## Telemetry (Grafana Cloud)

Uses Graphite protocol for simple HTTP POST metrics push to Grafana Cloud.

| Metric | Type | Description |
|--------|------|-------------|
| `ivcrush.request.duration` | timing | Endpoint latency (ms) |
| `ivcrush.request.status` | count | Success/error by endpoint |
| `ivcrush.api.calls` | count | External API calls by provider |
| `ivcrush.budget.calls_remaining` | gauge | Perplexity calls remaining |

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
