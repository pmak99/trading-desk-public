# Trading Desk - IV Crush System

## Project Overview

Production quantitative options trading system focused on **IV Crush** strategy - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**Live Performance (2025):** 57.4% win rate, $155k YTD profit, 1.19 profit factor

## Architecture

```
6.0 Agent Orchestration ──→ Parallel Claude Code agents for analysis
5.0 Cloud Autopilot     ──→ 24/7 Cloud Run service + Telegram bot
4.0 AI Sentiment         ──→ Perplexity-powered sentiment layer
2.0 Core Math Engine     ──→ VRP/strategy calculations (shared library)
────────────────────────────────────────────────────────────────────
SQLite (ivcrush.db)      ──→ 16 tables, 5,675+ historical moves
```

All subsystems import 2.0 as a shared library via `sys.path` injection. 4.0, 5.0, and 6.0 never duplicate 2.0's math.

## Primary Strategy: Volatility Risk Premium (VRP)

```
VRP Ratio = Implied Move / Historical Mean Move
```

**VRP Thresholds (BALANCED mode - default):**
- EXCELLENT: >= 1.8x (top tier, high confidence)
- GOOD: >= 1.4x (tradeable)
- MARGINAL: >= 1.2x (minimum edge, size down)
- SKIP: < 1.2x (no edge)

*Other modes: `VRP_THRESHOLD_MODE=LEGACY` (7x/4x/1.5x), CONSERVATIVE, AGGRESSIVE.*

## Scoring System

| Factor | Weight | Description |
|--------|--------|-------------|
| VRP Edge | 55% | Core signal quality |
| Implied Move Difficulty | 25% | Easier moves get bonus |
| Liquidity Quality | 20% | Open interest, bid-ask spreads |

**4.0 Sentiment Modifier:** `4.0 Score = 2.0 Score x (1 + modifier)`
Strong Bullish +12%, Bullish +7%, Neutral 0%, Bearish -7%, Strong Bearish -12%

**Cutoffs:** 2.0 Score >= 50 (pre-sentiment), 4.0 Score >= 55 (post-sentiment)

## 4-Tier Liquidity System

| Tier | OI/Position | Spread | Score Pts | Action |
|------|-------------|--------|-----------|--------|
| **EXCELLENT** | >=5x | <=8% | 20 | Full size |
| **GOOD** | 2-5x | 8-12% | 16 | Full size |
| **WARNING** | 1-2x | 12-15% | 12 | Reduce size |
| **REJECT** | <1x | >15% | 4 | Do not trade |

*Final tier = worse of (OI tier, Spread tier)*

## Tail Risk Ratio (TRR)

```
TRR = Max Historical Move / Average Historical Move
```

| Level | TRR | Max Contracts | Max Notional | Action |
|-------|-----|---------------|--------------|--------|
| **HIGH** | > 2.5x | 50 | $25,000 | Reduce size 50% |
| NORMAL | 1.5-2.5x | 100 | $50,000 | Standard sizing |
| LOW | < 1.5x | 100 | $50,000 | Standard sizing |

Added after December 2025 MU significant single-trade loss. Notable HIGH TRR: MU (3.05x), DRI (6.96x), GME (5.44x), NKE (4.89x).

## Critical Rules

1. **Never trade REJECT liquidity** - learned from significant loss on WDAY/ZS/SYM
2. **VRP >= 1.8x (EXCELLENT)** for full position sizing
3. **Prefer spreads over naked options** for defined risk
4. **Half-Kelly sizing** (0.25 fraction)
5. **Always check liquidity first** before evaluating VRP
6. **GOOD tier is tradeable** at full size
7. **Respect TRR position limits** - learned from $127k December loss on MU/AVGO
8. **Prefer weekly options** (opt-in with `REQUIRE_WEEKLY_OPTIONS=true`)

## Design Principles

- **AI for discovery** (what to look at)
- **Math for trading** (how to trade it)
- Never let sentiment override VRP/liquidity rules

## Directory Structure

```
Trading Desk/
  2.0/            Core math engine (VRP, liquidity, strategies) - shared library
  4.0/            AI sentiment layer (Perplexity integration)
  5.0/            Cloud autopilot (FastAPI on Cloud Run + Telegram)
  6.0/            Agent orchestration (parallel Claude Code agents)
  scripts/        Root-level utilities and data pipelines
  mcp-servers/    Custom MCP server (perplexity-tracked)
  docs/           Research docs, trade records
  archive/        1.0 (original) and 3.0 (ML research, inconclusive)
  .github/        CI/CD (pr-tests.yml runs all subsystem tests)
```

## 2.0 - Core Math Engine

Shared library imported by all other subsystems. Clean DDD architecture with domain/application/infrastructure layers. Dependency injection via `src/container.py`.

**Key Files:**

| File | Purpose |
|------|---------|
| `2.0/trade.sh` | Main CLI entry point |
| `2.0/scripts/scan.py` | Ticker scanning logic |
| `2.0/scripts/analyze.py` | Single ticker deep analysis |
| `2.0/src/application/metrics/vrp.py` | VRP calculation |
| `2.0/src/domain/scoring/strategy_scorer.py` | Composite scoring |
| `2.0/src/application/metrics/liquidity_scorer.py` | Liquidity analysis |
| `2.0/src/application/metrics/implied_move.py` | Implied move from options chain |
| `2.0/src/infrastructure/api/tradier_client.py` | Tradier API client |
| `2.0/src/infrastructure/api/alphavantage_client.py` | Alpha Vantage API client |
| `2.0/src/infrastructure/database/` | SQLite connection pool, repositories |
| `2.0/data/ivcrush.db` | SQLite database |

**CLI Commands:**
```bash
cd 2.0/
./trade.sh TICKER YYYY-MM-DD      # Single ticker analysis
./trade.sh scan YYYY-MM-DD        # Scan all earnings for date
./trade.sh whisper                # Most anticipated earnings
./trade.sh sync                   # Refresh earnings calendar
./trade.sh sync-cloud             # Sync DB with cloud + backup to GDrive
./trade.sh health                 # System health check
```

## 4.0 - AI Sentiment Layer

Adds Perplexity-powered sentiment analysis on top of 2.0's math. Uses "Import 2.0, Don't Copy" pattern.

**Key Features:**
- 3-rule directional adjustment system
- Budget tracking: 40 calls/day, $5/month with token-aware pricing
- Sentiment cache with configurable TTL

**Sentiment-Adjusted Direction (3-rule system):**

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Neutral skew + sentiment signal | Sentiment breaks tie |
| 2 | Conflict (bullish skew + bearish sentiment) | Go neutral (hedge) |
| 3 | Otherwise | Keep original skew bias |

**AI Sentiment Format:**
```
Direction: [bullish/bearish/neutral]
Score: [-1 to +1]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

## 5.0 - Cloud Autopilot

24/7 FastAPI service on Google Cloud Run with Telegram bot integration.

**Base URL:** `https://your-cloud-run-url.run.app`

All endpoints require `X-API-Key` header except `/` (public health check).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analyze?ticker=AAPL` | GET | Deep analysis - VRP, liquidity, sentiment, strategies |
| `/api/whisper` | GET | Most anticipated earnings (next 5 days) |
| `/api/scan?date=2026-01-15` | GET | Scan all earnings for specific date |
| `/api/health` | GET | Detailed health with budget and job status |
| `/api/budget` | GET | API budget with 7-day spending history |
| `/prime` | POST | Pre-cache sentiment for upcoming earnings |
| `/dispatch` | POST | Scheduler endpoint (runs time-based jobs) |
| `/telegram` | POST | Telegram bot webhook |
| `/alerts/ingest` | POST | GCP Monitoring webhook receiver |

**Infrastructure:** Dockerfile, cloudbuild.yaml, Terraform in `5.0/terraform/`

**Example API Calls:**
```bash
API_KEY=$(gcloud secrets versions access latest --secret=trading-desk-secrets | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('API_KEY',''))")
curl -s "https://your-cloud-run-url.run.app/api/analyze?ticker=NVDA" -H "X-API-Key: $API_KEY"
```

**Performance:** Parallel ticker analysis (asyncio.Semaphore, MAX_CONCURRENT_ANALYSIS=5), VRP caching with smart TTL (6h far / 1h near earnings), batch DB queries.

## 6.0 - Agent Orchestration

Local Claude Code agent system for parallel ticker analysis. Coordinates multiple agents for concurrent earnings scanning. Uses BALANCED VRP thresholds throughout.

**CLI:**
```bash
cd 6.0/
./agent.sh prime                    # Prime all systems
./agent.sh whisper                  # Most anticipated earnings (VRP ≥ 1.8x)
./agent.sh analyze TICKER [DATE]    # Single ticker deep dive
./agent.sh scan DATE                # Scan all earnings for date
./agent.sh maintenance              # System maintenance
```

**Key Files:**

| File | Purpose |
|------|---------|
| `6.0/agent.sh` | CLI entry point |
| `6.0/config/agents.yaml` | Agent definitions and orchestration rules |
| `6.0/src/agents/ticker_analysis.py` | VRP calculation, liquidity scoring, strategy generation |
| `6.0/src/agents/explanation.py` | Narrative explanations with win probability |
| `6.0/src/orchestrators/analyze.py` | Single-ticker deep dive with recommendation logic |
| `6.0/src/orchestrators/whisper.py` | Most anticipated earnings (parallel, VRP ≥ 1.8x discovery) |
| `6.0/src/utils/formatter.py` | ASCII table output (box-drawing chars, tier icons) |
| `6.0/src/integration/container_2_0.py` | 2.0 container wrapper (sys.path/sys.modules management) |
| `6.0/src/integration/` | Service integrations (4.0 cache, 5.0 Perplexity, MCP) |

**Recommendation Logic (analyze):**

| VRP | Liquidity | Action |
|-----|-----------|--------|
| ≥ 1.8x | EXCELLENT/GOOD | TRADE |
| ≥ 1.4x | EXCELLENT/GOOD | TRADE_CAUTIOUSLY |
| ≥ 1.4x | WARNING | TRADE_CAUTIOUSLY (reduce size) |
| < 1.4x | Any | SKIP (insufficient VRP) |
| Any | REJECT | SKIP (liquidity) |

**Liquidity in Whisper Mode:** When `generate_strategies=False`, liquidity tier is not available from strategy objects. The `TickerAnalysisAgent` falls back to calling 2.0's `classify_hybrid_tier_market_aware` directly using the already-cached option chain (no extra API calls).

**Note:** `container_2_0.py` uses `threading.RLock` and careful `sys.modules` manipulation to import 2.0's `src` package without colliding with 6.0's own `src` package. Access inner 2.0 container via `self.container.container` (e.g., `self.container.container.tradier`, `self.container.container.liquidity_scorer`).

## Root Scripts

| Script | Purpose |
|--------|---------|
| `scripts/parse_fidelity_csv.py` | Parse Fidelity CSV trade exports into trade_journal |
| `scripts/sync_databases.py` | Bidirectional local<->cloud DB sync |
| `scripts/migrate_budget_schema.py` | Token-aware budget tracking migration |
| `scripts/backfill_earnings_dates.py` | Earnings date validation/correction |
| `scripts/backfill_strategies.py` | Strategy grouping for multi-leg trades |
| `scripts/regroup_strategies.py` | Reorganize multi-leg trade groups |
| `scripts/db_health_check.py` | Database integrity verification |
| `scripts/journal_stats.py` | Trade journal analytics |
| `scripts/export_scan_results.py` | CSV/Excel export |
| `scripts/backtest_report.py` | Backtesting analysis |

## Slash Commands (Claude Code)

### Discovery & Analysis
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `/whisper` | `[DATE]` | Most anticipated earnings this week with VRP + sentiment |
| `/analyze` | `TICKER [DATE]` | Deep dive - VRP, strategies, sentiment |
| `/scan` | `DATE` | Scan all tickers with earnings on date |
| `/alert` | none | Today's high-VRP opportunities |

### System Operations
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `/prime` | `[DATE]` | Sync calendar + pre-cache sentiment (run 7-8 AM) |
| `/health` | none | Verify MCP connections, market status, budgets |
| `/maintenance` | `[task]` | Backups, cleanup, sync calendar, integrity checks |

### Data Collection
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `/collect` | `TICKER [DATE]` | Store pre-earnings sentiment for backtesting |
| `/backfill` | `TICKER DATE \| --pending \| --stats` | Record post-earnings outcomes |

### Reporting
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `/history` | `TICKER` | Historical earnings moves with AI patterns |
| `/backtest` | `[TICKER]` | Trading performance with AI insights |
| `/journal` | `[csv\|pdf]` | Parse Fidelity exports into trade journal |
| `/export-report` | none | Export scan results to CSV/Excel |

### Typical Daily Workflow
```
7:00 AM   /health              Verify systems operational
7:15 AM   /prime               Sync calendar + pre-cache sentiment
9:30 AM   /whisper             Instant results from cache
          /analyze NVDA        Deep dive on best candidate
          Execute in Fidelity  Human approval required
Evening   /backfill --pending  Record outcomes
```

## API Priority Order

1. **Tradier** - Options Greeks (IV, delta, theta, vega)
2. **Alpha Vantage** - Earnings calendar and dates
3. **Twelve Data** - Historical stock prices (800 calls/day free)
4. **Yahoo Finance** - Fallback for metadata only (NOT for price data around earnings)

## Weekly Options Filter

Opt-in filter (`REQUIRE_WEEKLY_OPTIONS=true`) for tickers with weekly options. Detects by counting Friday expirations within 21 days from Tradier API. On API error, defaults to `true` (permissive).

- `/api/whisper`, `/api/scan`, job handlers: Filter out non-weekly tickers when enabled
- `/api/analyze`: Show warning but include ticker
- CLI override: `./trade.sh scan 2026-01-22 --skip-weekly-filter`

## Historical Data Backfill

Script: `2.0/scripts/backfill_historical.py` (uses Twelve Data for prices)

**BMO vs AMC Timing:**

| Timing | Reference Close | Reaction Day |
|--------|-----------------|--------------|
| **BMO** | Previous day close | Earnings day |
| **AMC** | Earnings day close | Next trading day |

```bash
python scripts/backfill_historical.py UAL --start-date 2026-01-15
python scripts/backfill_historical.py MU ORCL AVGO
python scripts/backfill_historical.py --file tickers.txt --start-date 2025-01-01
```

## Database

**Location:** `2.0/data/ivcrush.db` (local), `gs://your-gcs-bucket/ivcrush.db` (cloud)

**Sync:** `scripts/sync_databases.py` - bidirectional merge
- `historical_moves`: Union (UNIQUE ticker+date)
- `earnings_calendar`: Newest `updated_at` wins
- `trade_journal`: Union (UNIQUE constraint)

### Schema (16 tables)

| Table | Records | Purpose |
|-------|---------|---------|
| `historical_moves` | 5,675 | Post-earnings price movements for VRP |
| `earnings_calendar` | 6,305 | Upcoming earnings dates |
| `trade_journal` | 556 | Individual option legs from Fidelity |
| `strategies` | 221 | Multi-leg strategy groupings |
| `position_limits` | 417 | TRR-based position sizing |
| `option_chain_snapshots` | - | Pre-earnings Greeks snapshots |
| `bias_predictions` | - | Direction bias forecasts |
| `bias_accuracy_stats` | - | Bias model accuracy |
| `api_budget` | - | Perplexity API budget tracking (token-aware) |
| `cache` | - | Query result caching |
| `rate_limits` | - | API rate limit tracking |
| `iv_log` | - | IV history log |
| `analysis_log` | - | Analysis execution log |
| `job_status` | - | Cloud job tracking |
| `backtest_runs` | - | Strategy backtest results |
| `backtest_trades` | - | Individual backtest trades |

### Key Tables Detail

**historical_moves:** `ticker`, `earnings_date`, `close_before`, `close_after`, `gap_move_pct`, `intraday_move_pct`, `direction` (UP/DOWN)

**trade_journal:** `symbol`, `acquired_date`, `sale_date`, `days_held`, `option_type` (PUT/CALL/NULL), `strike`, `expiration`, `quantity`, `cost_basis`, `proceeds`, `gain_loss`, `is_winner`, `strategy_id` (FK to strategies), `earnings_date`, `actual_move`
- Unique: `(symbol, acquired_date, sale_date, option_type, strike, cost_basis)`
- Import: `python scripts/parse_fidelity_csv.py /path/to/export.csv`
- 262 legs have `strategy_id IS NULL` (3+ leg strategies awaiting manual assignment)

**strategies:** `id`, `strategy_type` (BULL_CALL_SPREAD, IRON_CONDOR, etc.), `ticker`, `entry_date`, `exit_date`, `total_pnl`, `is_winner`

**api_budget:** `date`, `calls`, `cost`, `output_tokens`, `reasoning_tokens`, `search_requests`

### Useful Queries

```sql
-- Win rate by ticker for earnings trades
SELECT symbol, COUNT(*) as trades,
       ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
       ROUND(SUM(gain_loss), 2) as total_pnl
FROM trade_journal WHERE earnings_date IS NOT NULL
GROUP BY symbol ORDER BY total_pnl DESC;

-- Monthly P&L
SELECT strftime('%Y-%m', sale_date) as month,
       COUNT(*) as trades, ROUND(SUM(gain_loss), 2) as pnl
FROM trade_journal GROUP BY month;

-- Today's API budget
SELECT date, calls, cost, output_tokens, reasoning_tokens, search_requests
FROM api_budget WHERE date = date('now');
```

## Environment Variables

```bash
TRADIER_API_KEY=xxx          # Options data (primary)
ALPHA_VANTAGE_KEY=xxx        # Earnings calendar
TWELVE_DATA_KEY=xxx          # Historical stock prices (800/day free)
DB_PATH=data/ivcrush.db      # Database location
REQUIRE_WEEKLY_OPTIONS=false # Filter to weekly options only (opt-in)
```

**Cloud Run (set via `gcloud run services update`, not in secrets JSON):**

| Env Var | Source | Notes |
|---------|--------|-------|
| `REQUIRE_WEEKLY_OPTIONS` | Set directly | Read from `os.environ` |
| `GOOGLE_CLOUD_PROJECT` | Set directly | GCP project ID |
| `GCS_BUCKET` | Set directly | GCS bucket name |
| API keys | Via `SECRETS` ref | Loaded by `_load_secrets()` |

## MCP Servers

- **alphavantage** - Earnings, fundamentals, economic data
- **yahoo-finance** - Stock history and quotes
- **finnhub** - News, earnings surprises, insider trades
- **perplexity** - AI sentiment with token-aware cost tracking (custom: `mcp-servers/perplexity-tracked/`)
- **memory** - Knowledge graph for context
- **alpaca** - Paper trading account, positions, orders

**Perplexity Budget:** 40 calls/day, $5/month. Token-aware pricing (sonar $0.000001/token, sonar-pro $0.000015/token, reasoning-pro $0.000003/token, search $0.005/request).

## Strategy Types

| Strategy | Risk | When |
|----------|------|------|
| Naked Calls/Puts | High | Excellent VRP + acceptable liquidity |
| Bull/Bear Spreads | Medium | Balanced risk/reward |
| Iron Condors | Low | Conservative, neutral bias |
| Strangles/Straddles | Variable | Neutral directional bets |

## When Analyzing Trades

1. Run health check first (`./trade.sh health` or `/health`)
2. Check liquidity tier - REJECT = no-trade, WARNING = reduce size, GOOD/EXCELLENT = full size
3. Verify VRP ratio meets threshold (>=1.8x EXCELLENT, >=1.4x GOOD)
4. Review implied vs historical move spread
5. Check POP (probability of profit) - target 60%+
6. Validate theta decay is positive
7. Consider position sizing via Half-Kelly
8. Check TRR - reduce to 50 contracts max if HIGH

## Testing

```bash
# 2.0 tests (690 pass)
cd 2.0 && ./venv/bin/python -m pytest tests/ -v

# 4.0 tests (221 pass)
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/ -v

# 5.0 tests (308 pass)
cd 5.0 && ../2.0/venv/bin/python -m pytest tests/ -v

# 6.0 tests (48 pass)
cd 6.0 && ../2.0/venv/bin/python -m pytest tests/ -v
```

**CI:** `.github/workflows/pr-tests.yml` runs 2.0, 4.0, and 5.0 tests on all PRs (Python 3.12, ubuntu-latest).

## Architecture Notes

**2.0:** Clean DDD (domain/application/infrastructure). Dependency injection via `src/container.py` (lazy-loaded singletons). `Result[T, Error]` pattern for explicit error handling. Known limitation: float precision in DB (REAL vs Decimal) - low impact for percentages.

**4.0:** Zero external dependencies (pure stdlib + 2.0 import). Consistent confidence calculation via `_calculate_confidence()`.

**5.0:** FastAPI lifespan pattern (AppState dataclass, no global singletons). Single-dispatcher job routing. Eastern Time handling. Job dependency DAG with cycle detection. Structured JSON logging with request IDs. Earnings date freshness validation (validates against Alpha Vantage when <=7 days out, skips cross-quarter mismatches >45 days).

**6.0:** Parallel agent orchestration with BALANCED VRP thresholds. `container_2_0.py` manages sys.path/sys.modules to import 2.0's `src` without colliding with 6.0's `src`. Uses `threading.RLock` for thread-safe container initialization. Liquidity fallback: when strategies are skipped (whisper mode), calls 2.0's `classify_hybrid_tier_market_aware` directly on cached option chain. 2.0 domain types require unwrapping (e.g., `Percentage.value`, `Result.value`).
