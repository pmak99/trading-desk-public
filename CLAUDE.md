# Trading Desk - IV Crush System

## Project Overview

Production quantitative options trading system focused on **IV Crush** strategy - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**Live Performance (2025):** 57.4% win rate, $261k YTD profit, 1.19 profit factor

## Primary Strategy: Volatility Risk Premium (VRP)

The core edge comes from VRP - the ratio of implied move to historical average move:

```
VRP Ratio = Implied Move / Historical Mean Move
```

**VRP Thresholds:**
- EXCELLENT: >= 7.0x (top tier, high confidence)
- GOOD: >= 4.0x (tradeable with caution)
- MARGINAL: >= 1.5x (minimum edge, size down)
- SKIP: < 1.5x (no edge)

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
2. **VRP > 4x minimum** for full position sizing
3. **Prefer spreads over naked options** for defined risk
4. **Half-Kelly sizing** (0.25 fraction) for position sizing
5. **Always check liquidity score first** before evaluating VRP
6. **GOOD tier is tradeable** at full size (2-5x OI, 8-12% spread)

## API Priority Order

1. **Tradier** - Primary for options Greeks (IV, delta, theta, vega)
2. **Alpha Vantage** - Earnings calendar and dates
3. **Yahoo Finance** - Free fallback for prices and historical data

## Directory Structure

**Active Versions:**
- `5.0/` - **CLOUD AUTOPILOT** - 24/7 Cloud Run service with real implied moves from Tradier
- `2.0/` - **CORE MATH** - VRP/strategy calculations (used by 4.0 and 5.0)
- `4.0/` - AI sentiment layer (Perplexity, caching, budget tracking)

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
3. Verify VRP ratio meets threshold (>4x preferred)
4. Review implied vs historical move spread
5. Check POP (probability of profit) - target 60%+
6. Validate theta decay is positive
7. Consider position sizing via Half-Kelly

## Database Schema

### historical_moves
Historical post-earnings price movements for VRP calculation.
- `ticker`, `earnings_date`, `close_before`, `close_after`
- `gap_move_pct`, `intraday_move_pct`, `direction` (UP/DOWN)
- 4,926 records, used to calculate historical average moves per ticker

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

## Environment Variables Required

```bash
TRADIER_API_KEY=xxx      # Options data
ALPHA_VANTAGE_KEY=xxx    # Earnings calendar
DB_PATH=data/ivcrush.db  # Database location
```

## MCP Servers Available

- **alphavantage** - Earnings, fundamentals, economic data
- **yahoo-finance** - Stock history and quotes
- **alpaca** - Paper trading account, positions, orders
- **memory** - Knowledge graph for context
- **finnhub** - News, earnings surprises, insider trades
- **perplexity** - AI sentiment analysis (budget: 40 calls/day, $5/month)

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
| `/prime` | `[DATE]` | Pre-cache sentiment for week's earnings (run 7-8 AM) |
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
7:15 AM   /prime               → Pre-cache sentiment (predictable cost)
9:30 AM   /whisper             → Instant results from cache
          /analyze NVDA        → Deep dive on best candidate
          Execute in Fidelity  → Human approval required
Evening   /backfill --pending  → Record outcomes for completed earnings
```

**Command Features:**
- All commands show progress updates (`[1/N] Step description...`)
- No tool permission prompts except for Perplexity API calls
- `/whisper` shows ALL tickers including REJECT liquidity (marked 🚫)
- Sentiment cached after `/prime` for instant subsequent commands

**Note:** Discovery threshold is 3x VRP (position sizing still uses 4x rule).

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

## Testing

```bash
# 2.0 tests (452 pass)
cd 2.0 && ./venv/bin/python -m pytest tests/ -v

# 4.0 tests (184 pass)
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/ -v

# 5.0 tests
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
- Stale earnings cache if sync fails (mitigated by 6-day TTL)

### 4.0 - AI Sentiment Layer (Grade: A-)

**Architecture Strengths:**
- "Import 2.0, Don't Copy" pattern via sys.path injection
- 3-rule directional adjustment (simple, covers 99% cases)
- Budget tracking: 40 calls/day, $5/month
- 184 tests with edge cases
- Zero external dependencies (pure stdlib)

**Known Issues:**
- Timezone handling: naive datetime edge cases in cache expiry
- No input validation on direction strings in `record_outcome()`
- Inconsistent confidence calculation across rules

### 5.0 - Cloud Autopilot (17 issues identified)

**Architecture Strengths:**
- Elegant single-dispatcher job routing
- Proper Eastern Time handling
- Job dependency DAG with cycle detection
- Structured JSON logging with request IDs

**Priority Issues to Address:**
1. **HIGH:** Database sync race condition in optimistic locking retry
2. **HIGH:** Verify terraform state not in git (currently OK in .gitignore)
3. **MEDIUM:** Telegram webhook secret fails open in dev mode
4. **MEDIUM:** Global mutable singletons (thread-unsafe)
5. **MEDIUM:** Empty earnings calendar returns success status
6. **MEDIUM:** JSON secrets parsing lacks error handling

**Full details:** See exploration agents' reports in conversation history.
