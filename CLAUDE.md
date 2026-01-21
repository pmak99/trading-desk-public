# Trading Desk - IV Crush System

## Project Overview

Production quantitative options trading system focused on **IV Crush** strategy - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**Live Performance (2025):** 57.4% win rate, $155k YTD profit, 1.19 profit factor

## Primary Strategy: Volatility Risk Premium (VRP)

The core edge comes from VRP - the ratio of implied move to historical average move:

```
VRP Ratio = Implied Move / Historical Mean Move
```

**VRP Thresholds (BALANCED mode - default):**
- EXCELLENT: >= 1.8x (top tier, high confidence)
- GOOD: >= 1.4x (tradeable)
- MARGINAL: >= 1.2x (minimum edge, size down)
- SKIP: < 1.2x (no edge)

*Note: Set `VRP_THRESHOLD_MODE=LEGACY` for old thresholds (7x/4x/1.5x). Other modes: CONSERVATIVE, AGGRESSIVE.*

## Scoring System (Current Weights)

| Factor | Weight | Description |
|--------|--------|-------------|
| VRP Edge | 55% | Core signal quality |
| Implied Move Difficulty | 25% | Easier moves get bonus |
| Liquidity Quality | 20% | Open interest, bid-ask spreads |

## 4-Tier Liquidity System

| Tier | OI/Position | Spread | Score Pts | Action |
|------|-------------|--------|-----------|--------|
| **EXCELLENT** | ≥5x | ≤8% | 20 | Full size |
| **GOOD** | 2-5x | 8-12% | 16 | Full size |
| **WARNING** | 1-2x | 12-15% | 12 | Reduce size |
| **REJECT** | <1x | >15% | 4 | Do not trade |

*Final tier = worse of (OI tier, Spread tier)*

## Critical Rules

1. **Never trade REJECT liquidity** - learned from significant loss on WDAY/ZS/SYM
2. **VRP ≥ 1.8x (EXCELLENT tier)** for full position sizing
3. **Prefer spreads over naked options** for defined risk
4. **Half-Kelly sizing** (0.25 fraction) for position sizing
5. **Always check liquidity score first** before evaluating VRP
6. **GOOD tier is tradeable** at full size (2-5x OI, 8-12% spread)
7. **Respect TRR position limits** - learned from $127k December loss on MU/AVGO
8. **Prefer weekly options** (opt-in with `REQUIRE_WEEKLY_OPTIONS=true`) - better liquidity/spreads

## Tail Risk Ratio (TRR)

Measures how extreme a ticker's worst historical move is compared to its average. Added after December 2025 when 200-contract MU position caused significant single-trade loss.

```
TRR = Max Historical Move / Average Historical Move
```

**TRR Thresholds:**

| Level | TRR | Max Contracts | Max Notional | Action |
|-------|-----|---------------|--------------|--------|
| **HIGH** | > 2.5x | 50 | $25,000 | Reduce size 50% |
| NORMAL | 1.5-2.5x | 100 | $50,000 | Standard sizing |
| LOW | < 1.5x | 100 | $50,000 | Standard sizing |

**Notable HIGH TRR Tickers:**
- MU (3.05x) - max move 11.21% vs avg 3.68%
- DRI (6.96x) - extreme tail risk
- GME (5.44x) - meme stock volatility
- NKE (4.89x) - earnings surprises

**API Response:** `/api/analyze` includes `tail_risk` and `position_limits` fields:
```json
{
  "tail_risk": {"ratio": 3.05, "level": "HIGH", "max_move": 11.21},
  "position_limits": {"max_contracts": 50, "max_notional": 25000}
}
```

## Weekly Options Filter (Opt-in)

Filters tickers to only those with weekly options available. Weekly options provide better liquidity, tighter spreads, and more flexible expiration timing around earnings.

**Detection Logic:**
- Get expirations from Tradier API
- Count Friday expirations within next 21 days
- If fridays >= 2 → has weekly options

**Configuration:**
```bash
# Enable filter (opt-in, default OFF)
export REQUIRE_WEEKLY_OPTIONS=true
```

**CLI Override:**
```bash
# Skip weekly filter (include monthly-only tickers)
./trade.sh scan 2026-01-22 --skip-weekly-filter
```

**API Response:** `/api/analyze` includes `has_weekly_options` and `weekly_warning` fields:
```json
{
  "has_weekly_options": true,
  "weekly_warning": null
}
```

**Behavior by Endpoint:**
- `/api/whisper`, `/api/scan`: Filter out non-weekly tickers when enabled
- `/api/analyze`: Show warning but include ticker (for direct lookups)

**Error Handling:** On API error, defaults to `true` (permissive - don't block trades).

**VRP Cache Extension:** Weekly options status is stored alongside VRP data to avoid extra API calls:
```python
# Fields added to vrp_cache data dict
{
    "has_weekly_options": True,   # Boolean
    "weekly_reason": "3 Friday expirations in next 21 days"  # Explanation
}
```
Cache TTL remains 6h (far earnings) / 1h (near earnings). Weekly check happens once on first fetch, then cached.

## API Priority Order

1. **Tradier** - Primary for options Greeks (IV, delta, theta, vega)
2. **Alpha Vantage** - Earnings calendar and dates
3. **Twelve Data** - Historical stock prices (800 calls/day free, more reliable than yfinance)
4. **Yahoo Finance** - Fallback for metadata only (NOT for price data around earnings)

## Directory Structure

**Active Systems:**
- `6.0/` - **AGENT ORCHESTRATION** - Local Claude Code agent system with parallel analysis
- `5.0/` - **CLOUD AUTOPILOT** - 24/7 Cloud Run service with real implied moves from Tradier
- `4.0/` - **AI SENTIMENT** - Perplexity-powered sentiment with caching and budget tracking

**Shared Libraries:**
- `2.0/` - **CORE MATH** - VRP/strategy calculations (imported by 4.0, 5.0, 6.0)

**Archived (see `archive/README.md`):**
- `archive/1.0-original-system/` - Deprecated original system (superseded by 2.0)
- `archive/3.0-ml-enhancement/` - ML research project (Phase 2 complete, direction prediction inconclusive)

## Common Commands

```bash
# From 2.0/ directory
./trade.sh TICKER YYYY-MM-DD      # Single ticker analysis
./trade.sh scan YYYY-MM-DD        # Scan all earnings for date
./trade.sh whisper                # Most anticipated earnings
./trade.sh sync                   # Refresh earnings calendar
./trade.sh sync-cloud             # Sync DB with cloud + backup to GDrive
./trade.sh health                 # System health check
```

## Key Files

| File | Purpose |
|------|---------|
| `2.0/trade.sh` | Main CLI entry point |
| `2.0/scripts/scan.py` | Ticker scanning logic |
| `2.0/scripts/analyze.py` | Single ticker deep analysis |
| `2.0/src/application/metrics/vrp.py` | VRP calculation |
| `2.0/src/domain/scoring/strategy_scorer.py` | Composite scoring |
| `2.0/src/application/metrics/liquidity_scorer.py` | Liquidity analysis |
| `2.0/data/ivcrush.db` | SQLite database (historical_moves, trade_journal, etc.) |
| `scripts/parse_fidelity_csv.py` | Fidelity CSV parser with VRP correlation |

## Historical Data Backfill

Script: `2.0/scripts/backfill_historical.py`

Uses Twelve Data for prices + database earnings_calendar for BMO/AMC timing.

**BMO vs AMC Timing (Critical for Accuracy):**

| Timing | Announcement | Reference Close | Reaction Day |
|--------|--------------|-----------------|--------------|
| **BMO** | Before market open | Previous day close | Earnings day |
| **AMC** | After market close | Earnings day close | Next trading day |

**Example - UAL 2026-01-20 (AMC):**
```
Earnings announced: Jan 20 after close (AMC)
Reference close:    Jan 20 $108.57 (before announcement)
Reaction day:       Jan 21 (market reacts next morning)
Move calculation:   (Jan 21 close - Jan 20 close) / Jan 20 close
```

**Usage:**
```bash
# Requires TWELVE_DATA_KEY environment variable
python scripts/backfill_historical.py UAL --start-date 2026-01-15
python scripts/backfill_historical.py MU ORCL AVGO
python scripts/backfill_historical.py --file tickers.txt --start-date 2025-01-01
```

**Data Sources:**
- **Prices**: Twelve Data (800 calls/day free, accurate around market open/close)
- **Timing**: Database earnings_calendar (populated from Finnhub with explicit BMO/AMC)

*Note: yfinance-based backfill script archived to `archive/scripts/` - less accurate timing inference.*

## Strategy Types Generated

| Strategy | Risk Level | When to Use |
|----------|------------|-------------|
| Naked Calls/Puts | High | Excellent VRP + acceptable liquidity |
| Bull/Bear Spreads | Medium | Balanced risk/reward |
| Iron Condors | Low | Conservative, neutral bias |
| Strangles/Straddles | Variable | Neutral directional bets |

## When Analyzing Trades

1. Run health check first (`./trade.sh health`)
2. Check liquidity tier - REJECT is no-trade, WARNING reduce size, GOOD/EXCELLENT full size
3. Verify VRP ratio meets threshold (≥1.8x EXCELLENT, ≥1.4x GOOD)
4. Review implied vs historical move spread
5. Check POP (probability of profit) - target 60%+
6. Validate theta decay is positive
7. Consider position sizing via Half-Kelly

## Database Sync & Backup

**Architecture:** 2.0 local and 5.0 cloud have independent SQLite databases that are synced bidirectionally.

| Database | Location | Backup |
|----------|----------|--------|
| 2.0 Local | `2.0/data/ivcrush.db` | Google Drive (on sync-cloud) |
| 5.0 Cloud | `gs://your-gcs-bucket/ivcrush.db` | GCS backups (weekly Sun 3AM) |

**Sync Strategy:**
- `historical_moves`: Union (UNIQUE ticker+date prevents dupes)
- `earnings_calendar`: Newest `updated_at` wins
- `trade_journal`: Union (UNIQUE constraint prevents dupes)

**Sync Command:** `./trade.sh sync-cloud` from 2.0/ directory
- Downloads cloud DB from GCS
- Merges tables bidirectionally
- Uploads synced DB to GCS
- Backs up local DB to Google Drive

**Script:** `scripts/sync_databases.py`

## Database Schema

### historical_moves
Historical post-earnings price movements for VRP calculation.
- `ticker`, `earnings_date`, `close_before`, `close_after`
- `gap_move_pct`, `intraday_move_pct`, `direction` (UP/DOWN)
- 5,675 records, used to calculate historical average moves per ticker

### trade_journal
Actual executed trades imported from Fidelity CSV exports.
- `symbol`, `acquired_date`, `sale_date`, `days_held`
- `option_type` (PUT/CALL/NULL for stocks), `strike`, `expiration`
- `quantity`, `cost_basis`, `proceeds`, `gain_loss`, `is_winner`
- `term` (SHORT/LONG), `wash_sale_amount`
- `earnings_date`, `actual_move` - linked to historical_moves when trade straddled earnings

**Unique constraint:** `(symbol, acquired_date, sale_date, option_type, strike, cost_basis)`

**Import command:** `python scripts/parse_fidelity_csv.py /path/to/export.csv`

**Sample queries:**
```sql
-- Win rate by ticker for earnings trades
SELECT symbol, COUNT(*) as trades,
       ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
       ROUND(SUM(gain_loss), 2) as total_pnl
FROM trade_journal
WHERE earnings_date IS NOT NULL
GROUP BY symbol ORDER BY total_pnl DESC;

-- Monthly P&L
SELECT strftime('%Y-%m', sale_date) as month,
       COUNT(*) as trades, ROUND(SUM(gain_loss), 2) as pnl
FROM trade_journal GROUP BY month;
```

#### Strategy Grouping and Orphan Legs

The `trade_journal` table links individual option legs to multi-leg strategies via the `strategy_id` foreign key referencing the `strategies` table.

**Current Coverage:**
- 1-leg strategies (naked calls/puts): Automatically grouped
- 2-leg strategies (spreads): Automatically grouped
- 4-leg strategies (iron condors): Automatically grouped
- **3+ leg strategies**: Require manual review (Task 7, deferred)

**Orphan Legs (Expected Behavior):**

As of January 2026, 262 trade journal legs (49.9% of total trades) have `strategy_id IS NULL`. These are **not defects** but 3+ leg strategies awaiting implementation of Task 7 (manual strategy assignment UI).

**Query orphan legs:**
```sql
-- Count orphan legs
SELECT COUNT(*) FROM trade_journal WHERE strategy_id IS NULL;

-- View orphan legs by ticker
SELECT symbol, COUNT(*) as orphan_legs
FROM trade_journal
WHERE strategy_id IS NULL
GROUP BY symbol ORDER BY orphan_legs DESC;
```

The `strategies` table tracks:
- `id` - Unique strategy identifier
- `strategy_type` - Type (e.g., BULL_CALL_SPREAD, IRON_CONDOR)
- `ticker` - Underlying symbol
- `entry_date`, `exit_date` - Strategy lifecycle dates
- `total_pnl` - Aggregate P&L across all legs
- `is_winner` - Whether strategy was profitable overall

## Environment Variables Required

```bash
TRADIER_API_KEY=xxx          # Options data
ALPHA_VANTAGE_KEY=xxx        # Earnings calendar
TWELVE_DATA_KEY=xxx          # Historical stock prices (800/day free)
DB_PATH=data/ivcrush.db      # Database location
REQUIRE_WEEKLY_OPTIONS=false # Filter to weekly options only (opt-in)
```

## MCP Servers Available

- **alphavantage** - Earnings, fundamentals, economic data
- **yahoo-finance** - Stock history and quotes
- **alpaca** - Paper trading account, positions, orders
- **memory** - Knowledge graph for context
- **finnhub** - News, earnings surprises, insider trades
- **perplexity** - AI sentiment analysis with token-aware cost tracking (see below)

### Perplexity Token-Aware Budget Tracking

Custom MCP server (`mcp-servers/perplexity-tracked/`) that wraps the Perplexity API with actual token-based cost tracking.

**Invoice-Verified Pricing (January 2026):**

| Model | Rate | Example |
|-------|------|---------|
| sonar output | $0.000001/token | 1000 tokens = $0.001 |
| sonar-pro output | $0.000015/token | 1000 tokens = $0.015 |
| reasoning-pro | $0.000003/token | 1000 tokens = $0.003 |
| Search API | $0.005/request | Flat rate |

**Budget Limits:**
- Daily: 40 calls
- Monthly: $5.00

**Database Schema (`api_budget` table):**
```sql
-- Token columns added January 2026
output_tokens INTEGER DEFAULT 0,
reasoning_tokens INTEGER DEFAULT 0,
search_requests INTEGER DEFAULT 0
```

**Query budget status:**
```sql
-- Today's usage with token breakdown
SELECT date, calls, cost, output_tokens, reasoning_tokens, search_requests
FROM api_budget WHERE date = date('now') ORDER BY date DESC;

-- Monthly token totals
SELECT SUM(output_tokens) as total_output,
       SUM(reasoning_tokens) as total_reasoning,
       SUM(search_requests) as total_searches,
       SUM(cost) as total_cost
FROM api_budget WHERE date LIKE strftime('%Y-%m', 'now') || '%';
```

**Migration:** Run `python scripts/migrate_budget_schema.py` to add token columns to existing databases.

## 4.0 Slash Commands

### Discovery & Analysis
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `/whisper` | `[DATE]` | Find most anticipated earnings this week with VRP + sentiment |
| `/analyze` | `TICKER [DATE]` | Deep dive on single ticker - VRP, strategies, sentiment |
| `/scan` | `DATE` | Scan all tickers with earnings on specific date |
| `/alert` | none | Today's high-VRP opportunities (auto-uses today's date) |

### System Operations
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `/prime` | `[DATE]` | Sync calendar + pre-cache sentiment for week's earnings (run 7-8 AM) |
| `/health` | none | Verify MCP connections, market status, API budgets |
| `/maintenance` | `[task]` | Backups, cleanup, sync calendar, integrity checks |

### Data Collection (Backtesting)
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `/collect` | `TICKER [DATE]` | Store pre-earnings sentiment for backtesting |
| `/backfill` | `TICKER DATE \| --pending \| --stats` | Record post-earnings outcomes |

### Analysis & Reporting
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `/history` | `TICKER` | Visualize historical earnings moves with AI patterns |
| `/backtest` | `[TICKER]` | Analyze trading performance with AI insights |
| `/journal` | `[csv\|pdf]` | Parse Fidelity exports into trade journal (CSV preferred) |
| `/export-report` | none | Export scan results to CSV/Excel |

### Typical Daily Workflow
```
7:00 AM   /health              → Verify all systems operational
7:15 AM   /prime               → Sync calendar + pre-cache sentiment
9:30 AM   /whisper             → Instant results from cache
          /analyze NVDA        → Deep dive on best candidate
          Execute in Fidelity  → Human approval required
Evening   /backfill --pending  → Record outcomes for completed earnings
```

**Command Features:**
- All commands show progress updates (`[1/N] Step description...`)
- No tool permission prompts except for Perplexity API calls
- `/prime` auto-syncs stale earnings dates (>24h old) before caching sentiment
- `/whisper` shows ALL tickers including REJECT liquidity (marked 🚫)
- Sentiment cached after `/prime` for instant subsequent commands
- **TRR warnings** displayed for HIGH tail risk tickers (⚠️ badge + max 50 contracts)

**Note:** Discovery threshold is 1.8x VRP (aligned with EXCELLENT tier for high-confidence alerts).

**TRR in Slash Commands:** All discovery commands (`/analyze`, `/whisper`, `/scan`, `/alert`) query the `position_limits` table and display warnings for HIGH TRR tickers. This prevents oversized positions on volatile tickers like MU (learned from significant loss).

## 4.0 Scoring System

**2.0 Score:** VRP (55%) + Move Difficulty (25%) + Liquidity (20%)

**4.0 Score:** 2.0 Score × (1 + Sentiment Modifier)

| Sentiment | Modifier |
|-----------|----------|
| Strong Bullish | +12% |
| Bullish | +7% |
| Neutral | 0% |
| Bearish | -7% |
| Strong Bearish | -12% |

**Minimum Cutoffs:**
- 2.0 Score ≥ 50 (pre-sentiment)
- 4.0 Score ≥ 55 (post-sentiment)

## AI Sentiment Format

All sentiment queries return structured data:
```
Direction: [bullish/bearish/neutral]
Score: [-1 to +1]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

## Sentiment-Adjusted Directional Bias

3-rule system for adjusting 2.0's skew-based direction using AI sentiment:

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Neutral skew + sentiment signal | Sentiment breaks tie |
| 2 | Conflict (bullish skew + bearish sentiment) | Go neutral (hedge) |
| 3 | Otherwise | Keep original skew bias |

## Design Principles

- **AI for discovery** (what to look at)
- **Math for trading** (how to trade it)
- Never let sentiment override VRP/liquidity rules

## 5.0 Cloud API Endpoints

Base URL: `https://your-cloud-run-url.run.app`

All endpoints require `X-API-Key` header except `/` (public health check).

### Discovery & Analysis

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analyze?ticker=AAPL` | GET | Deep analysis - VRP, liquidity, sentiment, strategies |
| `/api/whisper` | GET | Most anticipated earnings (next 5 days) |
| `/api/scan?date=2026-01-15` | GET | Scan all earnings for specific date |

### System Operations

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Public health check (no auth) |
| `/api/health` | GET | Detailed health with budget and job status |
| `/api/budget` | GET | Detailed API budget with 7-day spending history |
| `/prime` | POST | Pre-cache sentiment for upcoming earnings |
| `/dispatch` | POST | Scheduler endpoint (runs time-based jobs) |
| `/dispatch?force=JOB` | POST | Force-run specific job |

### Telegram

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/telegram` | POST | Telegram bot webhook (secret token auth) |

### Alerting

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/alerts/ingest` | POST | GCP Monitoring webhook receiver |

### Example API Calls

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

## Testing

```bash
# 2.0 tests (514 pass)
cd 2.0 && ./venv/bin/python -m pytest tests/ -v

# 4.0 tests (186 pass)
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/ -v

# 5.0 tests (193 pass)
cd 5.0 && ../2.0/venv/bin/python -m pytest tests/ -v
```

## Code Review Summary (December 2025)

### 2.0 - Core Math Engine (Grade: 7.9/10)

**Architecture Strengths:**
- Clean DDD with domain/application/infrastructure layers
- Dependency injection via `src/container.py` (lazy-loaded singletons)
- Result[T, Error] pattern forces explicit error handling
- 452 unit tests, 40% coverage
- Post-loss improvements embedded (liquidity scoring after significant WDAY/ZS/SYM loss)

**Known Issues:**
- Float precision in DB (REAL vs Decimal) - low impact for percentages
- No schema versioning/migrations

**Issues Fixed (January 2026):**
- ✅ Stale earnings cache - freshness validation against Alpha Vantage when earnings ≤7 days and cache >24h old

### 4.0 - AI Sentiment Layer (Grade: A)

**Architecture Strengths:**
- "Import 2.0, Don't Copy" pattern via sys.path injection
- 3-rule directional adjustment (simple, covers 99% cases)
- Budget tracking: 40 calls/day, $5/month
- 186 tests with edge cases
- Zero external dependencies (pure stdlib)
- Consistent confidence calculation via `_calculate_confidence()`

**Issues Fixed (December 2025):**
- ✅ Timezone handling in cache expiry
- ✅ Direction string validation in `record_outcome()`
- ✅ Confidence calculation unified across all rules

**Issues Fixed (January 2026):**
- ✅ Budget tracker default cost mismatch (0.01→0.006 to match COST_PER_CALL_ESTIMATE)
- ✅ Added validation tests for invalid direction/trade_outcome
- ✅ Removed unused empty MCP module
- ✅ Fixed README broken link to non-existent docs/ARCHITECTURE.md
- ✅ Token-aware budget tracking with invoice-verified Perplexity pricing (replaces fixed $0.006/call estimates)

### 5.0 - Cloud Autopilot (All issues resolved ✅)

**Architecture Strengths:**
- Elegant single-dispatcher job routing
- Proper Eastern Time handling
- Job dependency DAG with cycle detection
- Structured JSON logging with request IDs
- FastAPI lifespan pattern for proper resource management
- Centralized ticker validation

**Issues Fixed (December 2025):**
1. ✅ Database sync race condition - proper generation-based conflict detection
2. ✅ Terraform state in .gitignore
3. ✅ Telegram webhook secret fails closed (security)
4. ✅ Global singletons → FastAPI lifespan with AppState dataclass
5. ✅ Empty earnings calendar returns proper status
6. ✅ JSON secrets parsing has error handling
7. ✅ Added missing endpoints: `/prime`, `/api/budget`, `/api/scan`

**Issues Fixed (January 2026):**
8. ✅ Midnight hour overflow in job dispatcher (23:55 + 7min → 24:02 bug)
9. ✅ Zero mid-price validation in implied move calculation (prevents 0% implied move)
10. ✅ Secret Manager exception logging (shows error type for debugging)
11. ✅ Empty alert suppression (morning digest, evening summary only send with data)
12. ✅ Budget cost consistency (0.005→0.006 to match 4.0's COST_PER_CALL_ESTIMATE)
13. ✅ Removed empty scripts/ directory and fixed Dockerfile COPY instruction
14. ✅ Earnings date freshness validation in analyze endpoint (validates against Alpha Vantage when ≤7 days out)
15. ✅ Token-aware budget tracking with invoice-verified Perplexity pricing
16. ✅ Direction consistency - `/whisper` and job handlers now use `get_direction()` (matches `/analyze` 3-rule system)
17. ✅ Next-quarter detection - prevents "correcting" to different quarter when API returns 45+ days different
18. ✅ Bidirectional date difference - uses `abs()` to catch both later (next quarter) and earlier (DB has future date) mismatches

**Next-Quarter Detection Logic:**
When validating stale cache dates, if API returns a date 45+ days **different** (earlier OR later) than DB, the system recognizes this as a cross-quarter mismatch. Instead of blindly correcting to the API date, it skips the ticker and logs a warning.

| Scenario | Date Diff | Action |
|----------|-----------|--------|
| API shows next quarter | +45 to +90 days | Skip (earnings already reported) |
| DB has next quarter | -45 to -90 days | Skip (DB date was wrong/stale) |
| Same-quarter correction | -44 to +44 days | Accept API date |

This fixes false "date changed" warnings for tickers like IBKR where the DB had a stale/wrong date. The `abs()` fix also handles the edge case where DB has a future (next quarter) date but API shows current quarter.

**Performance Optimizations (January 2026):**
15. ✅ Parallel ticker analysis - asyncio.Semaphore with MAX_CONCURRENT_ANALYSIS=5 (~60s→~15s)
16. ✅ VRP caching with smart TTL - 6h far earnings, 1h near earnings (89% API reduction)
17. ✅ Batch DB queries - window functions for per-ticker limiting (97% query reduction)
18. ✅ Cache hit/miss metrics - Grafana tracking for cache effectiveness

**Stale Artifacts Removed (January 2026):**
- `2.0/ivcrush.egg-info/`, `2.0/src/ivcrush.egg-info/`, `2.0/src/iv_crush_2.egg-info/` - stale build artifacts
- `scripts/parse_trade_statements.py`, `scripts/parse_trade_statements_v2.py` - superseded by v3
