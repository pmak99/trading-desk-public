# Trading Desk - IV Crush System

Production quantitative options trading system focused on **IV Crush** strategy - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**Live Performance (2025):** 57.4% win rate, $155k YTD profit, 1.19 profit factor

---

## Table of Contents

- [Quick Start](#quick-start)
- [System Architecture](#system-architecture)
- [Core Strategy](#core-strategy)
- [Commands](#commands)
- [Database](#database)
- [Configuration](#configuration)
- [Testing](#testing)
- [Deployment](#deployment)
- [Project Structure](#project-structure)
- [Archived Versions](#archived-versions)

---

## Quick Start

### Local Development (2.0 Core System)

```bash
# Setup
cd 2.0
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure .env
TRADIER_API_KEY=your_key
ALPHA_VANTAGE_KEY=your_key
DB_PATH=data/ivcrush.db

# Run
./trade.sh NVDA 2026-01-15      # Analyze single ticker
./trade.sh scan 2026-01-15      # Scan all earnings for date
./trade.sh whisper              # Most anticipated earnings
./trade.sh health               # System health check
```

### Cloud Autopilot (5.0)

24/7 automated system with Telegram notifications.

**Live Service:** https://your-cloud-run-url.run.app

```bash
# Local development
cd 5.0
source venv/bin/activate
export $(cat .env | xargs)
uvicorn src.main:app --reload --port 8080
```

See [5.0 README](5.0/README.md) for full setup including Telegram bot.

---

## System Architecture

The Trading Desk consists of four active systems:

```
┌─────────────────────────────────────────────────────────────┐
│                  6.0 AGENT ORCHESTRATION                     │
│  Parallel processing + intelligent coordination (Dev)       │
│  - Parallel ticker analysis (2x faster /whisper)            │
│  - Multi-specialist analysis (/analyze with explanations)   │
│  - Automated sentiment pre-caching (/prime)                 │
│  - Intelligent guardrails (anomaly detection)               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    5.0 CLOUD AUTOPILOT                       │
│  24/7 Cloud Run service with scheduled jobs & Telegram bot  │
│  - Pre-market scans (5:30 AM)                               │
│  - AI sentiment caching (6:30 AM)                           │
│  - Real-time alerts (throughout day)                        │
│  - Outcome tracking (evening)                               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    4.0 AI SENTIMENT LAYER                    │
│  Perplexity-powered sentiment with budget tracking          │
│  - Sentiment cache (3hr TTL)                                │
│  - Budget tracker (40 calls/day, $5/month)                  │
│  - Sentiment-adjusted scoring                               │
│  - Backtesting data collection                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    2.0 CORE MATH ENGINE                      │
│  Production VRP calculations & strategy generation           │
│  - VRP ratio calculation                                     │
│  - 4-tier liquidity scoring                                 │
│  - Tail Risk Ratio (TRR) position limits                    │
│  - Multi-leg strategy generation                            │
│  - Historical moves database (4,926 records)                │
└─────────────────────────────────────────────────────────────┘
```

### Version Summary

| Version | Status | Purpose |
|---------|--------|---------|
| **2.0** | Production | Core VRP/strategy math |
| **4.0** | Production | AI sentiment layer |
| **5.0** | Production | Cloud autopilot (24/7) |
| **6.0** | Phase 2 Complete ✅ | Agent orchestration (parallel processing, intelligent automation) |
| 1.0 | Archived | Original system (superseded by 2.0) |
| 3.0 | Archived | ML research (direction prediction inconclusive) |

---

## Core Strategy

### Volatility Risk Premium (VRP)

The core edge comes from VRP - the ratio of implied move to historical average move:

```
VRP Ratio = Implied Move / Historical Mean Move
```

**VRP Thresholds:**

| Tier | VRP Ratio | Action |
|------|-----------|--------|
| EXCELLENT | >= 7.0x | High confidence, full size |
| GOOD | >= 4.0x | Tradeable with caution |
| MARGINAL | >= 1.5x | Minimum edge, size down |
| SKIP | < 1.5x | No edge |

### Scoring System

**2.0 Score (Base):**
- VRP Edge: 55%
- Move Difficulty: 25%
- Liquidity Quality: 20%

**4.0 Score (with AI):**
```
4.0 Score = 2.0 Score × (1 + Sentiment Modifier)
```

| Sentiment | Modifier |
|-----------|----------|
| Strong Bullish | +12% |
| Bullish | +7% |
| Neutral | 0% |
| Bearish | -7% |
| Strong Bearish | -12% |

### 4-Tier Liquidity System

| Tier | OI/Position | Spread | Score Pts | Action |
|------|-------------|--------|-----------|--------|
| **EXCELLENT** | ≥5x | ≤8% | 20 | Full size |
| **GOOD** | 2-5x | 8-12% | 16 | Full size |
| **WARNING** | 1-2x | 12-15% | 12 | Reduce size |
| **REJECT** | <1x | >15% | 4 | **Never trade** |

*Final tier = worse of (OI tier, Spread tier)*

### Tail Risk Ratio (TRR)

Measures how extreme a ticker's worst historical move is compared to its average. Added after December 2025 when 200-contract MU position caused significant single-trade loss.

```
TRR = Max Historical Move / Average Historical Move
```

| Level | TRR | Max Contracts | Max Notional |
|-------|-----|---------------|--------------|
| **HIGH** | > 2.5x | 50 | $25,000 |
| NORMAL | 1.5-2.5x | 100 | $50,000 |
| LOW | < 1.5x | 100 | $50,000 |

**Notable HIGH TRR Tickers:** MU (3.05x), DRI (6.96x), GME (5.44x), NKE (4.89x)

### Critical Rules

1. **Never trade REJECT liquidity** - learned from significant loss on WDAY/ZS/SYM
2. **VRP > 4x minimum** for full position sizing
3. **Prefer spreads over naked options** for defined risk
4. **Half-Kelly sizing** (0.25 fraction)
5. **Always check liquidity score first** before evaluating VRP
6. **Respect TRR position limits** - learned from $127k December loss on MU/AVGO

---

## Commands

### 2.0 CLI Commands

From `2.0/` directory:

| Command | Description |
|---------|-------------|
| `./trade.sh TICKER DATE` | Single ticker analysis |
| `./trade.sh scan DATE` | Scan all earnings for date |
| `./trade.sh whisper [DATE]` | Most anticipated earnings |
| `./trade.sh sync [--dry-run]` | Refresh earnings calendar |
| `./trade.sh sync-cloud` | Sync DB with cloud + backup to GDrive |
| `./trade.sh health` | System health check |

**Freshness Validation:** When analyzing tickers with earnings ≤7 days away, the system validates cached dates against Alpha Vantage if the cache is >24h old.

### 4.0 Slash Commands (Claude Code)

Available when using Claude Code CLI:

**Discovery & Analysis:**
- `/whisper [DATE]` - Find most anticipated earnings with VRP + sentiment
- `/analyze TICKER [DATE]` - Deep dive on single ticker
- `/scan DATE` - Scan all tickers with earnings on date
- `/alert` - Today's high-VRP opportunities (auto-uses today's date)

**System Operations:**
- `/prime [DATE]` - Pre-cache sentiment for week's earnings (run 7-8 AM)
- `/health` - Verify MCP connections, market status, API budgets
- `/maintenance [task]` - Backups, cleanup, sync calendar, integrity checks

**Data Collection (Backtesting):**
- `/collect TICKER [DATE]` - Store pre-earnings sentiment
- `/backfill TICKER DATE | --pending | --stats` - Record post-earnings outcomes

**Analysis & Reporting:**
- `/history TICKER` - Visualize historical earnings moves with AI patterns
- `/backtest [TICKER]` - Analyze trading performance with AI insights
- `/journal [csv|pdf]` - Parse Fidelity exports into trade journal (CSV preferred)
- `/export-report` - Export scan results to CSV/Excel

**Typical Daily Workflow:**
```
7:00 AM   /health              → Verify all systems operational
7:15 AM   /prime               → Pre-cache sentiment (predictable cost)
9:30 AM   /whisper             → Instant results from cache
          /analyze NVDA        → Deep dive on best candidate
          Execute in Fidelity  → Human approval required
Evening   /backfill --pending  → Record outcomes for completed earnings
```

### 6.0 Agent Commands

From `6.0/` directory (git worktree):

**System Operations:**
- `/prime [DATE]` - Parallel sentiment pre-caching with rate limiting (replaces 4.0 version)
- `/maintenance [health|data-quality|cache-cleanup]` - System health checks and diagnostics

**Coming in Phase 2:**
- `/whisper [DATE]` - Parallel ticker analysis (2x faster than 5.0)
- `/analyze TICKER [DATE]` - Multi-specialist deep dive with explanations

**Key Features:**
- Parallel processing with asyncio (semaphore-based rate limiting)
- Intelligent error handling (partial results on timeout)
- Progress indicators for long-running operations
- Comprehensive health checks (APIs, database, budget)

**Example:**
```bash
cd 6.0
./agent.sh prime          # Pre-cache sentiment for upcoming week
./agent.sh maintenance health    # Check system health
```

See [6.0/README.md](6.0/README.md) for full documentation.

### 5.0 Cloud API Endpoints

Base URL: `https://your-cloud-run-url.run.app`

All endpoints require `X-API-Key` header except `/` (public health check).

**Discovery & Analysis:**
- `GET /api/analyze?ticker=AAPL` - Deep analysis with VRP, liquidity, sentiment, strategies
- `GET /api/whisper` - Most anticipated earnings (next 5 days)
- `GET /api/scan?date=2026-01-15` - Scan all earnings for specific date

**System Operations:**
- `GET /` - Public health check (no auth)
- `GET /api/health` - Detailed health with budget and job status
- `GET /api/budget` - Detailed API budget with 7-day spending history
- `POST /prime` - Pre-cache sentiment for upcoming earnings
- `POST /dispatch` - Scheduler endpoint (runs time-based jobs)
- `POST /dispatch?force=JOB` - Force-run specific job

**Telegram:**
- `POST /telegram` - Telegram bot webhook (secret token auth)

**Example API Calls:**
```bash
# Set API key
API_KEY=$(gcloud secrets versions access latest --secret=trading-desk-secrets | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('API_KEY',''))")

# Analyze ticker
curl -s "https://your-cloud-run-url.run.app/api/analyze?ticker=NVDA" -H "X-API-Key: $API_KEY"

# Scan date
curl -s "https://your-cloud-run-url.run.app/api/scan?date=2026-01-15" -H "X-API-Key: $API_KEY"

# Pre-cache sentiment
curl -s -X POST "https://your-cloud-run-url.run.app/prime" -H "X-API-Key: $API_KEY"

# Budget status
curl -s "https://your-cloud-run-url.run.app/api/budget" -H "X-API-Key: $API_KEY"
```

### 5.0 Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/health` | System status and API budget |
| `/whisper` | Today's high-VRP opportunities |
| `/analyze TICKER` | Deep analysis of specific ticker |
| `/dashboard` | Link to Grafana metrics dashboard |

**Ticker Aliases:** Common company names automatically converted (NIKE→NKE, GOOGLE→GOOGL, etc.)

---

## Database

### Location & Sync

| Database | Location | Backup |
|----------|----------|--------|
| 2.0 Local | `2.0/data/ivcrush.db` | Google Drive (on sync-cloud) |
| 5.0 Cloud | `gs://your-gcs-bucket/ivcrush.db` | GCS backups (weekly Sun 3AM) |

**Sync Strategy:**
- `historical_moves`: Union (UNIQUE ticker+date prevents dupes)
- `earnings_calendar`: Newest `updated_at` wins
- `trade_journal`: Union (UNIQUE constraint prevents dupes)
- `strategies`: Union (auto-increment IDs maintained)

**Sync Command:** `./trade.sh sync-cloud` from 2.0/ directory

**Script:** `scripts/sync_databases.py`

### Schema

#### historical_moves

Historical post-earnings price movements for VRP calculation.

```sql
CREATE TABLE historical_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    earnings_date DATE NOT NULL,
    close_before REAL,
    close_after REAL,
    gap_move_pct REAL,
    intraday_move_pct REAL,
    direction TEXT,  -- UP/DOWN
    UNIQUE(ticker, earnings_date)
);
```

**Current Data:** 4,926 records covering 398 unique tickers

#### strategies

Multi-leg strategy tracking (added Jan 2026). Groups individual option legs into strategies for accurate win rate calculation.

```sql
CREATE TABLE strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy_type TEXT NOT NULL CHECK(strategy_type IN ('SINGLE', 'SPREAD', 'IRON_CONDOR')),
    acquired_date DATE NOT NULL,
    sale_date DATE NOT NULL,
    days_held INTEGER,
    expiration DATE,
    quantity INTEGER,
    net_credit REAL,
    net_debit REAL,
    gain_loss REAL NOT NULL,
    is_winner BOOLEAN NOT NULL,
    earnings_date DATE,
    actual_move REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Strategy Types:**
- **SINGLE** (1 leg): Naked put or call
- **SPREAD** (2 legs): Bull put, bear call, etc.
- **IRON_CONDOR** (4 legs): 4-leg neutral strategy

**Migration:** See `scripts/migrations/001_add_strategies.py`

#### trade_journal

Individual option legs linked to strategies via `strategy_id`.

```sql
CREATE TABLE trade_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    acquired_date DATE NOT NULL,
    sale_date DATE NOT NULL,
    days_held INTEGER,
    option_type TEXT,  -- PUT/CALL/NULL for stocks
    strike REAL,
    expiration DATE,
    quantity INTEGER,
    cost_basis REAL,
    proceeds REAL,
    gain_loss REAL,
    is_winner BOOLEAN,
    term TEXT,  -- SHORT/LONG
    wash_sale_amount REAL,
    earnings_date DATE,
    actual_move REAL,
    strategy_id INTEGER REFERENCES strategies(id),  -- Links to parent strategy
    UNIQUE(symbol, acquired_date, sale_date, option_type, strike, cost_basis)
);
```

**Import:** `python scripts/parse_fidelity_csv.py /path/to/export.csv`

**Current Data:** 525 total legs, 176 strategies (263 linked, 262 orphans awaiting Task 7)

#### Strategy Grouping and Orphan Legs

The backfill process (`scripts/backfill_strategies.py`) auto-detects and groups legs into strategies based on matching criteria:
- Same symbol
- Same acquired_date
- Same sale_date
- Same expiration

**Orphan Legs:** 262 legs (49.9%) have NULL `strategy_id` - these are 3, 5+ leg strategies awaiting manual review (Task 7). This is expected behavior.

**Query orphan legs:**
```sql
SELECT symbol, acquired_date, sale_date, COUNT(*) as leg_count
FROM trade_journal
WHERE strategy_id IS NULL
GROUP BY symbol, acquired_date, sale_date
ORDER BY leg_count DESC;
```

**Scripts:**
- `scripts/strategy_grouper.py` - Auto-detection logic with confidence scoring
- `scripts/backfill_strategies.py` - Backfill existing trades into strategies
- `scripts/journal_stats.py` - Query strategy-level statistics

**Win Rate Accuracy:**
- **Before:** 50% win rate (2 legs of a winning spread counted separately)
- **After:** 59.1% win rate (strategy-level is_winner flag)

#### earnings_calendar

Upcoming earnings dates from Alpha Vantage.

```sql
CREATE TABLE earnings_calendar (
    ticker TEXT PRIMARY KEY,
    earnings_date DATE,
    fiscal_quarter TEXT,
    estimate REAL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Sync:** `./trade.sh sync` (weekly recommended)

### Sample Queries

**Strategy-level win rate:**
```sql
SELECT strategy_type,
       COUNT(*) as trades,
       ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
       ROUND(SUM(gain_loss), 2) as total_pnl
FROM strategies
GROUP BY strategy_type;
```

**Monthly P&L:**
```sql
SELECT strftime('%Y-%m', sale_date) as month,
       COUNT(*) as trades,
       ROUND(SUM(gain_loss), 2) as pnl
FROM strategies
GROUP BY month
ORDER BY month DESC;
```

**Position reconstruction:**
```sql
SELECT s.strategy_type, s.gain_loss as combined_pnl,
       t.option_type, t.strike, t.quantity, t.gain_loss as leg_pnl
FROM strategies s
JOIN trade_journal t ON t.strategy_id = s.id
WHERE s.id = 42;
```

---

## Configuration

### Environment Variables

Create `.env` in 2.0/ or 5.0/ directory:

```bash
# Required
TRADIER_API_KEY=your_key
ALPHA_VANTAGE_KEY=your_key

# Optional (4.0/5.0)
PERPLEXITY_API_KEY=your_key  # AI sentiment
TELEGRAM_BOT_TOKEN=your_token  # 5.0 only
TELEGRAM_CHAT_ID=your_id       # 5.0 only

# Security (5.0 only)
API_KEY=your_api_key
TELEGRAM_WEBHOOK_SECRET=your_secret

# Database
DB_PATH=data/ivcrush.db

# GCP (5.0 production)
GOOGLE_CLOUD_PROJECT=your-gcp-project
GCS_BUCKET=your-gcs-bucket
```

### API Priority Order

1. **Tradier** - Primary for options Greeks (IV, delta, theta, vega)
2. **Alpha Vantage** - Earnings calendar and dates
3. **Yahoo Finance** - Free fallback for prices and historical data

### MCP Servers Available

| Server | Purpose | Cost |
|--------|---------|------|
| `perplexity` | AI sentiment synthesis | ~$0.006/call |
| `alphavantage` | Earnings, fundamentals | Free |
| `yahoo-finance` | Stock history | Free |
| `finnhub` | News, earnings surprises | Free |
| `alpaca` | Paper trading account | Free |
| `memory` | Knowledge graph | Free |

---

## Testing

### 2.0 Core System

```bash
cd 2.0
./venv/bin/python -m pytest tests/ -v
```

**Coverage:** 496 tests pass, 36% code coverage

Key test files:
- `test_calculators.py` - VRP and implied move calculations
- `test_liquidity_scorer.py` - 4-tier liquidity system
- `test_kelly_sizing.py` - EV-based position sizing
- `test_consistency_enhanced.py` - Move pattern analysis

### 4.0 AI Layer

```bash
cd 4.0
../2.0/venv/bin/python -m pytest tests/ -v
```

**Coverage:** 186 tests pass

Key test files:
- `test_sentiment_direction.py` - 3-rule directional bias
- `test_sentiment_cache.py` - Cache TTL, invalidation
- `test_budget_tracker.py` - API budget enforcement
- `test_sentiment_history.py` - Backtesting data storage

### 5.0 Cloud Autopilot

```bash
cd 5.0
../2.0/venv/bin/python -m pytest tests/ -v
```

**Coverage:** 170 tests pass

Key test files:
- `test_vrp.py` - VRP calculation
- `test_liquidity.py` - Liquidity scoring
- `test_job_manager.py` - Job scheduling
- `test_telegram_formatter.py` - Telegram message formatting

### 6.0 Agent System

```bash
cd 6.0
../2.0/venv/bin/python -m pytest tests/ -v

# Live integration tests
./tests/test_health_live.py
./tests/test_ticker_analysis_live.py
./tests/test_prime_live.py
```

**Coverage:** Phase 1 complete - all core agents tested

Key test files:
- `test_health_live.py` - HealthCheckAgent with API verification
- `test_ticker_analysis_live.py` - TickerAnalysisAgent with Result type handling
- `test_prime_live.py` - PrimeOrchestrator with parallel processing
- `test_anomaly_detection.py` - AnomalyDetectionAgent edge cases
- `test_explanation.py` - ExplanationAgent narrative generation

**Test Features:**
- Live integration tests with real API calls
- Result[T, Error] type handling validation
- Date type conversion testing (string → date objects)
- Enum case conversion verification (lowercase → uppercase)

### Strategy Scripts

```bash
# From project root
PYTHONPATH="$PWD:$PYTHONPATH" ./2.0/venv/bin/python -m pytest scripts/tests/ -v
```

**Coverage:** 18 tests pass (12 grouper, 6 backfill)

Key test files:
- `test_strategy_grouper.py` - Multi-leg grouping logic
- `test_backfill_strategies.py` - Backfill with transaction safety

---

## Deployment

### Local Development

See individual README files:
- [2.0 README](2.0/README.md) - Core system
- [4.0 README](4.0/README.md) - AI layer
- [5.0 README](5.0/README.md) - Cloud autopilot

### 5.0 Cloud Deployment

See [5.0/DEPLOYMENT.md](5.0/DEPLOYMENT.md) for complete instructions.

**Quick Deploy:**
```bash
cd 5.0
gcloud builds submit --tag gcr.io/your-gcp-project/trading-desk
gcloud run deploy trading-desk \
  --image gcr.io/your-gcp-project/trading-desk \
  --region us-east1 \
  --platform managed \
  --allow-unauthenticated
```

**Live Service:** https://your-cloud-run-url.run.app

**Scheduled Jobs:** 12 jobs via Cloud Scheduler (pre-market prep, sentiment scan, digests, etc.)

**Cost:** ~$6/month ($3-5 Perplexity API + $1 GCP services)

---

## Project Structure

```
Trading Desk/
├── 2.0/                         # Core math engine (production)
│   ├── src/
│   │   ├── domain/              # Value objects, protocols, enums
│   │   ├── application/         # VRP, strategy generator, metrics
│   │   ├── infrastructure/      # API clients (Tradier, Alpha Vantage)
│   │   └── config/              # Configuration and thresholds
│   ├── scripts/
│   │   ├── scan.py              # Main scanning logic
│   │   ├── analyze.py           # Single ticker analysis
│   │   └── health_check.py      # System verification
│   ├── tests/                   # 496 unit tests
│   ├── data/ivcrush.db          # SQLite database
│   └── trade.sh                 # CLI entry point
│
├── 4.0/                         # AI sentiment layer (production)
│   ├── src/
│   │   ├── sentiment_direction.py   # 3-rule directional bias
│   │   └── cache/
│   │       ├── sentiment_cache.py   # 3-hour TTL cache
│   │       ├── budget_tracker.py    # API budget (40/day)
│   │       └── sentiment_history.py # Backtesting data
│   ├── data/sentiment_cache.db  # SQLite cache
│   └── tests/                   # 186 unit tests
│
├── 5.0/                         # Cloud autopilot (production)
│   ├── src/
│   │   ├── main.py              # FastAPI app
│   │   ├── core/                # Config, logging, job manager
│   │   ├── domain/              # VRP, liquidity, scoring
│   │   ├── integrations/        # Tradier, Perplexity, Telegram
│   │   └── jobs/                # 12 scheduled jobs
│   ├── terraform/               # GCP infrastructure
│   ├── data/ivcrush.db          # Cloud database
│   ├── tests/                   # 170 unit tests
│   ├── Dockerfile
│   └── DEPLOYMENT.md            # Full deployment guide
│
├── 6.0/                         # Agent orchestration (in development)
│   ├── src/
│   │   ├── orchestrators/       # PrimeOrchestrator, WhisperOrchestrator
│   │   ├── agents/              # TickerAnalysis, Sentiment, Health, Anomaly
│   │   ├── integration/         # Container2_0, Cache4_0 wrappers
│   │   └── utils/               # Schemas, formatters, timeouts
│   ├── tests/                   # Live integration tests
│   ├── config/                  # Agent configurations
│   ├── agent.sh                 # CLI entry point
│   └── README.md                # Full documentation
│
├── .worktrees/
│   └── 6.0-agent-system/        # Git worktree for 6.0 development
│
├── scripts/                     # Project-wide utilities
│   ├── migrations/              # Database migrations
│   │   └── 001_add_strategies.py
│   ├── strategy_grouper.py      # Multi-leg strategy detection
│   ├── backfill_strategies.py   # Backfill existing trades
│   ├── journal_stats.py         # Strategy-level statistics
│   ├── parse_fidelity_csv.py    # CSV import from Fidelity
│   ├── sync_databases.py        # Bidirectional DB sync
│   └── tests/                   # 18 tests for strategy scripts
│
├── docs/
│   └── plans/                   # Design documents
│       ├── 2026-01-08-multi-leg-strategy-tracking-design.md
│       ├── 2026-01-08-multi-leg-strategy-implementation.md
│       └── 2026-01-11-6.0-agent-design.md
│
├── archive/
│   ├── 1.0-original-system/     # Deprecated (superseded by 2.0)
│   └── 3.0-ml-enhancement/      # ML research (direction prediction inconclusive)
│
├── CLAUDE.md                    # AI assistant instructions
└── README.md                    # This file
```

---

## Archived Versions

### 1.0 - Original System (Deprecated)

**Status:** Superseded by 2.0 on 2024-11-09
**Why Archived:** Replaced by 2.0's cleaner DDD architecture, Result[T, E] error handling, DI container

See [archive/README.md](archive/README.md) for details.

### 3.0 - ML Enhancement (Experimental)

**Status:** Development paused at Phase 2
**Why Archived:** Critical finding - direction prediction doesn't work with available data

**Key Findings:**
- Random Forest (best ML): 54% accuracy
- 2.0 VRP Baseline: 57.4% accuracy
- Conclusion: ML adds no edge for direction prediction

**Magnitude prediction** showed promise (R²: 0.26) but not production-critical.

See [archive/3.0-ml-enhancement/PROGRESS.md](archive/3.0-ml-enhancement/PROGRESS.md) for full research.

---

## Documentation

- [CLAUDE.md](CLAUDE.md) - Project-wide AI assistant instructions
- [2.0/README.md](2.0/README.md) - Core system documentation
- [4.0/README.md](4.0/README.md) - AI layer documentation
- [5.0/README.md](5.0/README.md) - Cloud autopilot documentation
- [5.0/DEPLOYMENT.md](5.0/DEPLOYMENT.md) - Full deployment guide
- [5.0/DESIGN.md](5.0/DESIGN.md) - Architecture details
- [6.0/README.md](6.0/README.md) - Agent orchestration documentation
- [archive/README.md](archive/README.md) - Archived versions

---

## License

Private - Internal use only

---

**Disclaimer:** For research purposes only. Not financial advice. Options trading involves substantial risk.
