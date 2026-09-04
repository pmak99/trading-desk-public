# 5.0 Cloud Autopilot

24/7 cloud-native trading system on GCP Cloud Run. Runs scheduled pre-market scans, delivers Telegram alerts, and exposes a REST API for the full IV Crush analysis stack.

**Live:** `https://your-service.a.run.app` | Rate limit: 60 req/min

5.0 ports 2.0's domain math for cloud deployment — it never reimplements it independently. `5.0/common/` is a **shadow copy** of root `common/`; when changing shared logic, update both.

---

## Quick Start

### Local Development

```bash
cd 5.0
../2.0/venv/bin/pip install -r requirements.txt
cp .env.template .env   # fill in API keys
export $(cat .env | xargs)
uvicorn src.main:app --reload --port 8080
```

### Docker

```bash
docker build -t ivcrush:local .
docker run -p 8080:8080 --env-file .env -v $(pwd)/data:/app/data ivcrush:local
```

### Deploy

```bash
./deploy.sh           # Full deploy: DB sync → GCS upload → Cloud Run
./deploy.sh --quick   # Code-only (skip DB sync)
```

The Claude Code command `/deploy` wraps this and adds `--status`, `--logs`, and `--rollback` (via gcloud directly).

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health ping (no auth) |
| `/api/health` | GET | System health: APIs, DB, uptime |
| `/api/analyze?ticker=XXX` | GET | Full analysis |
| `/api/whisper` | GET | High-VRP opportunities |
| `/api/scan?date=YYYY-MM-DD` | GET | Scan all earnings on a date |
| `/api/council?ticker=XXX` | GET | 6-source AI sentiment consensus |
| `/prime` | POST | Pre-cache sentiment for upcoming earnings |
| `/dispatch` | POST | Trigger a scheduled job manually |
| `/telegram` | POST | Telegram webhook (bot commands) |

**Auth:** `X-API-Key` header required on all `/api/*` and `/prime`/`/dispatch` endpoints.

```bash
curl -H "X-API-Key: $KEY" https://your-service.a.run.app/api/health
```

**Known scoring divergence:** cloud scoring (`src/domain/scoring.py`) uses the simpler composite from `common/constants.py` (VRP 55%, Move difficulty 25%, Liquidity 20%), so `/api/whisper` rankings can order tickers differently than the local 2.0 `/whisper`. The 2.0 scan quality score is authoritative for trade decisions.

---

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/health` | System status |
| `/whisper` | High-VRP opportunities |
| `/analyze TICKER` | Full analysis |
| `/council TICKER` | 6-source AI sentiment |

**Common aliases:** NIKE→NKE, GOOGLE→GOOGL, FACEBOOK→META

---

## Scheduled Jobs

All jobs are gated by `SCHEDULED_JOBS_ENABLED` — set it `false` to silence every Telegram digest/alert while keeping the service up (`/dispatch` returns `{"status":"disabled"}` immediately):

```bash
gcloud run services update trading-desk --region us-east1 \
  --update-env-vars SCHEDULED_JOBS_ENABLED=false   # re-enable with =true
```

### Weekday (Mon–Fri, US/Eastern)

| Time | Job | Description |
|------|-----|-------------|
| 5:30 AM | pre-market-prep | Fetch earnings calendar, calculate VRP |
| 6:30 AM | sentiment-scan | Pre-cache Perplexity sentiment for high-VRP tickers |
| 7:30 AM | morning-digest | Top 10 opportunities pushed to Telegram |
| 10:00 AM | market-open-refresh | Refresh prices post-open |
| 2:30 PM | pre-trade-refresh | Final VRP validation before close |
| 4:30 PM | after-hours-check | Monitor after-hours moves |
| 7:00 PM | outcome-recorder | Record earnings outcomes to DB |
| 8:00 PM | evening-summary | Daily P&L summary to Telegram |

### Weekend

| Day | Time | Job |
|-----|------|-----|
| Sat | 4:00 AM | weekly-backfill (past 7 days of outcomes) |
| Sun | 3:00 AM | weekly-backup (DB integrity + GCS sync) |
| Sun | 3:30 AM | weekly-cleanup (expired cache) |
| Sun | 4:00 AM | calendar-sync (3-month earnings refresh) |

Job implementations live in `src/jobs/handlers/` (one module per job).

---

## Architecture

```
5.0/
├── src/
│   ├── main.py                  FastAPI entry point
│   ├── api/
│   │   ├── routers/             analyze, whisper, scan, council, health, jobs, operations, webhooks
│   │   ├── middleware.py        Rate limiting, API key auth
│   │   ├── dependencies.py      FastAPI dependency injection
│   │   └── state.py             Application state
│   ├── core/
│   │   ├── config.py            Environment config (incl. SCHEDULED_JOBS_ENABLED)
│   │   ├── database.py          DB connection management
│   │   ├── job_manager.py       Scheduled job runner
│   │   ├── logging.py           Structured logging — print(json.dumps()) is intentional Cloud Logging output
│   │   └── metrics.py           Metrics push
│   ├── domain/                  VRP, liquidity, skew, direction, scoring (ported from 2.0)
│   │   └── council/             Council package: types.py, scoring.py, runner.py
│   ├── integrations/            Tradier, Perplexity, Finnhub, Telegram, Twelve Data, Yahoo, SEC EDGAR, FINRA
│   ├── formatters/              Telegram HTML and CLI ASCII formatters
│   ├── application/             Business logic (filters)
│   └── jobs/                    base.py + handlers/ (one module per scheduled job)
├── common/                      Shadow copy of root common/ — keep in sync
├── terraform/                   GCP uptime monitoring + alerting
├── dashboards/                  Grafana dashboard JSON exports
├── data/                        ivcrush.db (gitignored, synced from 2.0/data/)
├── deploy.sh                    Deploy script (--quick to skip DB sync)
├── Dockerfile
└── docker-compose.yml
```

---

## Configuration

```bash
# Core
TRADIER_API_KEY=xxx
FINNHUB_API_KEY=xxx
PERPLEXITY_API_KEY=xxx
TWELVE_DATA_KEY=xxx
DB_PATH=data/ivcrush.db

# Feature flags
ORATS_ENABLED=false          # disabled by default — requires a live ORATS subscription to enable
SCHEDULED_JOBS_ENABLED=true  # false = silence all Telegram digests/alerts

# Security
API_KEY=xxx
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
TELEGRAM_WEBHOOK_SECRET=xxx

# GCP
GOOGLE_CLOUD_PROJECT=trading-desk-prod
GCS_BUCKET=your-gcs-bucket
```

Secrets are stored in GCP Secret Manager. `deploy.sh` reads them automatically. Both feature flags read from the environment at runtime — toggling requires no code change or redeploy (`gcloud run services update ... --update-env-vars`).

---

## Monthly Cost

| Item | Cost |
|------|------|
| Perplexity API | ~$3–5 |
| GCP Cloud Run + Storage | ~$1 |
| **Total** | **~$4–6/month** |

(ORATS $49/month ended with the subscription, Jun 2026.)

---

## Testing

```bash
../2.0/venv/bin/python -m pytest tests/ -v    # 545 tests
```

---

*For research purposes only. Not financial advice.*
