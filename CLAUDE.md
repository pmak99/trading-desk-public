# Trading Desk - IV Crush System

## Project Overview

Production quantitative options trading system focused on **IV Crush** - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**2025 Performance:** ~55-60% win rate | 200+ strategies | positive net P&L

## Architecture

```
agents  Agent Orchestration ──→ Parallel Claude Code agents
cloud   Cloud Autopilot     ──→ 24/7 Cloud Run + Telegram
sentiment AI Sentiment        ──→ Perplexity sentiment layer
core    Core Math Engine    ──→ VRP/strategy calculations (shared library)
────────────────────────────────────────────────────────────────────
ivcrush.db (core)        ──→ 15 tables | sentiment_cache.db (sentiment) ──→ 3 tables
```

All subsystems import core via `sys.path` injection. Sentiment, cloud, and agents never duplicate core's math.

## Volatility Risk Premium (VRP)

```
VRP Ratio = Implied Move / Historical Mean Move
```

| Tier | VRP Ratio | Action |
|------|-----------|--------|
| EXCELLENT | >= 1.8x | High confidence, full size |
| GOOD | >= 1.4x | Tradeable |
| MARGINAL | >= 1.2x | Minimum edge, reduce size |
| SKIP | < 1.2x | No edge |

## Scoring System

**Two scoring layers:**

**Ticker Selection** (`scoring_config.py` — scan mode, ranks tickers):

| Preset | VRP | Consistency | Skew | Liquidity |
|--------|-----|-------------|------|-----------|
| **Balanced** (default) | 40% | 25% | 15% | 20% |
| VRP-Dominant | 70% | 20% | 5% | 5% |
| Liquidity-First | 30% | 20% | 15% | 35% |

8 presets available for A/B testing. Min composite score: 60.

**Strategy Scoring** (`config.py ScoringWeights` — scores individual option strategies):

| Factor | With Greeks | Without Greeks |
|--------|-------------|----------------|
| Probability of Profit (POP) | 40% | 45% |
| Liquidity Quality | 22% | 26% |
| VRP Edge | 17% | 17% |
| Kelly Edge (R/R x POP) | 13% | 12% |
| Greeks (theta/vega) | 8% | — |

Rebalanced Dec 2025 after significant loss: POP raised to 40%, liquidity added at 22%.

**Sentiment Modifier:** `Sentiment Score = Core Score x (1 + sentiment_modifier)` where modifiers range from -12% (strong bearish) to +12% (strong bullish).

**Cutoffs:** Core Score >= 50 (pre-filter) | Sentiment Score >= 55 (post-filter)

## Liquidity Tiers (Relaxed Feb 2026)

| Tier | OI/Position | Spread | Score Pts | Action |
|------|-------------|--------|-----------|--------|
| EXCELLENT | >=5x | <=12% | 20 | Full size |
| GOOD | 2-5x | 12-18% | 16 | Full size |
| WARNING | 1-2x | 18-25% | 12 | Reduce size |
| REJECT | <1x | >25% | 4 | Reduce size (allowed) |

## Tail Risk Ratio (TRR)

```
TRR = Max Historical Move / Average Historical Move
```

| Level | TRR | Max Contracts | Performance (2025) |
|-------|-----|---------------|-------------------|
| HIGH | > 2.5x | 50 | ~55% win, significant loss |
| NORMAL | 1.5-2.5x | 100 | ~57% win, moderate loss |
| **LOW** | < 1.5x | 100 | **~70% win, strong profit** |

## Strategy Performance (2025 Verified)

| Strategy | Trades | Win Rate | P&L | Recommendation |
|----------|-------:|:--------:|----------:|----------------|
| **SINGLE** | 100+ | **~60-65%** | **strongest performer** | Preferred |
| SPREAD | 80+ | ~50-55% | positive | Good |
| STRANGLE | <10 | ~33% | negative | Avoid |
| IRON_CONDOR | <5 | ~67% | significant loss (sizing) | Caution |

## Trade Adjustment Rules

| Type | Campaign Win Rate | Action |
|------|:-----------------:|--------|
| NEW | ~58% | Standard |
| REPAIR | 20% | Damage control only |
| ROLL | **0%** | **Never roll** |

## Critical Rules

1. **Prefer SINGLE options** over spreads - ~60-65% vs ~50-55% win rate
2. **Respect TRR limits** - LOW TRR profitable, HIGH TRR significant losses
3. **Never roll losing positions** - 0% success rate
4. **Cut losses early** - repairs reduce loss but rarely save campaigns
5. **Check liquidity first** before evaluating VRP
6. **VRP >= 1.8x** for full position sizing
7. **Reduce size for REJECT liquidity** - allowed but penalized in scoring

## sentiment Directional Bias (3-Rule System)

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Neutral skew + sentiment signal | Sentiment breaks tie |
| 2 | Conflict (bullish skew + bearish sentiment) | Go neutral |
| 3 | Otherwise | Keep original skew bias |

## CLI Commands

```bash
# core Core
cd core/ && ./trade.sh TICKER DATE    # Single analysis
cd core/ && ./trade.sh scan DATE      # Scan all earnings
cd core/ && ./trade.sh whisper        # Most anticipated
cd core/ && ./trade.sh sync-cloud     # Sync DB + backup
cd core/ && ./trade.sh health         # Health check

# agents Agents
cd agents/ && ./agent.sh whisper        # Parallel scan
cd agents/ && ./agent.sh analyze TICKER # Deep dive
```

## cloud Cloud API

**Base:** `https://your-cloud-run-url.run.app`

| Endpoint | Description |
|----------|-------------|
| `/api/analyze?ticker=XXX` | Deep analysis |
| `/api/whisper` | High-VRP opportunities |
| `/api/scan?date=YYYY-MM-DD` | Scan date |
| `/api/health` | System health |

Rate limit: 60 req/min per IP.

## Databases

**ivcrush.db** (`core/data/ivcrush.db` | `gs://your-gcs-bucket/ivcrush.db`) — 15 tables, schema v6:
- `historical_moves` (6,921) | `earnings_calendar` (6,762) | `strategies` (235)
- `trade_journal` (634) | `position_limits` (428) | `bias_predictions` (28) | `iv_log` (16)
- Empty: `analysis_log`, `cache`, `rate_limits`, `backtest_runs`, `backtest_trades`, `job_status`, `ticker_metadata`
- Note: ~239 trade_journal rows have sale_date < acquired_date — this is Fidelity's convention for credit trades (sell-to-open), not a bug. Strategies table is normalized to chronological order (acquired=open, sale=close).

**sentiment_cache.db** (`sentiment/data/sentiment_cache.db`) — 3 tables, WAL mode:
- `sentiment_cache` (3hr TTL) | `api_budget` (daily counts) | `sentiment_history` (permanent)

## Key Queries

```sql
-- Strategy performance
SELECT strategy_type, COUNT(*) trades,
       ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) win_rate,
       ROUND(SUM(gain_loss), 0) pnl
FROM strategies GROUP BY strategy_type ORDER BY pnl DESC;

-- Performance by TRR level
SELECT CASE WHEN trr_at_entry > 2.5 THEN 'HIGH'
            WHEN trr_at_entry >= 1.5 THEN 'NORMAL'
            ELSE 'LOW' END as trr_level,
       COUNT(*) trades, ROUND(SUM(gain_loss), 0) pnl
FROM strategies WHERE trr_at_entry IS NOT NULL GROUP BY trr_level;

-- Campaign performance (linked trades)
SELECT campaign_id, SUM(gain_loss) total,
       GROUP_CONCAT(trade_type || ': $' || ROUND(gain_loss, 0)) chain
FROM strategies WHERE campaign_id IS NOT NULL
GROUP BY campaign_id ORDER BY total;
```

## Slash Commands

| Command | Purpose |
|---------|---------|
| `/whisper` | Most anticipated earnings with VRP |
| `/analyze TICKER` | Deep dive for trade decision |
| `/scan DATE` | Scan all earnings on date |
| `/prime` | Pre-cache sentiment for the week |
| `/alert` | Today's high-VRP alerts |
| `/health` | System status check |
| `/maintenance MODE` | System maintenance (sync, backup, backfill, cleanup, validate) |
| `/journal FILE` | Parse Fidelity CSV/PDF |
| `/backtest [TICKER]` | Performance analysis |
| `/history TICKER` | Historical earnings moves |
| `/backfill ARGS` | Record post-earnings outcomes |
| `/collect TICKER` | Collect pre-earnings sentiment |
| `/export-report [MODE]` | Export to CSV/JSON |
| `/positions [TICKER]` | Open positions and 30-day exposure dashboard |
| `/risk [DAYS]` | Portfolio risk assessment (TRR, concentration, drawdown) |
| `/calendar [DATE]` | Weekly earnings calendar with history and TRR flags |
| `/pnl [PERIOD]` | P&L summary (week/month/ytd/year/quarter/N days) |
| `/postmortem TICKER` | Post-earnings: predicted vs actual move analysis |
| `/deploy [--quick\|--status\|--logs\|--rollback]` | Deploy cloud to Cloud Run |

## Environment Variables

```bash
TRADIER_API_KEY=xxx          # Options data (primary)
ALPHA_VANTAGE_KEY=xxx        # Earnings calendar
TWELVE_DATA_KEY=xxx          # Historical prices
PERPLEXITY_API_KEY=xxx       # AI sentiment
DB_PATH=data/ivcrush.db
```

## Testing

```bash
cd core && ./venv/bin/python -m pytest tests/ -v    # 766 tests
cd sentiment && ../core/venv/bin/python -m pytest tests/  # 221 tests
cd cloud && ../core/venv/bin/python -m pytest tests/  # 449 tests
cd agents && ../core/venv/bin/python -m pytest tests/  # 82 tests
```

## Working Style Preferences

1. **Confirm scope before executing** — For data operations (backfill, sync, cleanup), confirm the exact scope (which tickers, which date range, which database) before executing. For batch operations, always scope to the user's actual data (historically traded tickers from `strategies`/`trade_journal`, existing watchlists) rather than arbitrary/random selections.
2. **Respect rewrite requests** — When asked for a full rewrite or ground-up rebuild, do NOT attempt incremental fixes first. Go straight to the clean-slate approach.
3. **Audit all related configs** — When fixing any issue, find ALL files that reference the same service, URL, endpoint, or config value and fix them together. A fix in one file often needs to be mirrored in others.
4. **Source env before API calls** — Always check for required environment variables (`TRADIER_API_KEY`, `TWELVE_DATA_KEY`, `PERPLEXITY_API_KEY`, etc.) before executing scripts that need them. Source `.env` if needed.
5. **Verify data, don't infer** — When checking external sources (job postings, ticker lookups, API responses), verify actual data fields rather than inferring from URLs or titles. Validate ticker symbols against exchange lookup (e.g., "PFIZER" -> "PFE").
6. **Complete output, no truncation** — Slash commands (`/prime`, `/whisper`, `/analyze`, `/backfill`, `/scan`, etc.) must run to full completion with complete output displayed. Never truncate results.

## When Analyzing Trades

1. Check TRR level - prefer LOW, avoid HIGH
2. Check liquidity tier - REJECT = reduce size (allowed but penalized)
3. Verify VRP >= 1.4x minimum, >= 1.8x preferred
4. **Prefer SINGLE options** over spreads
5. If trade goes wrong: **cut the loss, don't roll**
6. Repair only as last resort (convert single to spread)
