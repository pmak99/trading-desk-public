# Trading Desk - IV Crush System

## Project Overview

Production quantitative options trading system focused on **IV Crush** - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**2025 Performance:** 58.1% win rate | 203 strategies | +$13,334 net P&L

## Architecture

```
6.0 Agent Orchestration ──→ Parallel Claude Code agents
5.0 Cloud Autopilot     ──→ 24/7 Cloud Run + Telegram
4.0 AI Sentiment        ──→ Perplexity sentiment layer
2.0 Core Math Engine    ──→ VRP/strategy calculations (shared library)
────────────────────────────────────────────────────────────────────
ivcrush.db (2.0)        ──→ 15 tables | sentiment_cache.db (4.0) ──→ 3 tables
```

All subsystems import 2.0 via `sys.path` injection. 4.0, 5.0, 6.0 never duplicate 2.0's math.

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

Rebalanced Dec 2025 after WDAY/ZS $26,930 loss: POP raised to 40%, liquidity added at 22%.

**4.0 Modifier:** `4.0 Score = 2.0 Score x (1 + sentiment_modifier)` where modifiers reflect accuracy: +1% bullish flat rate, all strength levels (strong_bullish 23% accuracy, no differential justified — May 2026), 0% bearish (0/4 accuracy, zeroed Mar 2026).

**Cutoffs:** 2.0 Score >= 50 (pre-filter) | 4.0 Score >= 55 (post-filter)

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
| HIGH | > 2.5x | 50 | 54.8% win, -$123k |
| NORMAL | 1.5-2.5x | 100 | 56.5% win, -$38k |
| **LOW** | < 1.5x | 100 | **70.6% win, +$52k** |

## Strategy Performance (2025 Verified)

| Strategy | Trades | Win Rate | Total P&L | Recommendation |
|----------|-------:|:--------:|----------:|----------------|
| **SINGLE** | 108 | **63.9%** | **+$103,390** | Preferred |
| SPREAD | 86 | 52.3% | +$51,472 | Good |
| STRANGLE | 6 | 33.3% | -$15,100 | Avoid |
| IRON_CONDOR | 3 | 66.7% | -$126,429 | Caution |

## Trade Adjustment Rules

| Type | Campaign Win Rate | Action |
|------|:-----------------:|--------|
| NEW | 58.2% | Standard |
| REPAIR | 20% | Damage control only |
| ROLL | **0%** | **Never roll** |

## Critical Rules

1. **Prefer SINGLE options** over spreads - 64% vs 52% win rate
2. **Minimum 3 DTE at entry** - 0-2 DTE lost -$209k vs 3-5 DTE gained +$139k at same win rates; enforced in `calculate_expiration_date` (Wed earnings → next Friday)
3. **Exit spreads next trading day** - Spreads held 2+ days: 31% win, -$28k. Singles can hold (78% win, +$66k)
4. **Respect TRR limits** - LOW TRR made +$52k, HIGH TRR lost -$123k
5. **Never roll losing positions** - 0% success rate
6. **Cut losses early** - repairs reduce loss but rarely save campaigns
7. **Check liquidity first** before evaluating VRP
8. **VRP >= 1.8x** for full position sizing
9. **Reduce size for REJECT liquidity** - allowed but penalized in scoring

## 4.0 Directional Bias (3-Rule System)

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Neutral skew + sentiment signal | Sentiment breaks tie |
| 2 | Conflict (skew vs active opposing sentiment) | Go neutral (bearish zeroed May 2026 — no longer triggers) |
| 3 | Otherwise | Keep original skew bias |

## CLI Commands

```bash
# 2.0 Core
cd 2.0/ && ./trade.sh TICKER DATE    # Single analysis
cd 2.0/ && ./trade.sh scan DATE      # Scan all earnings
cd 2.0/ && ./trade.sh whisper        # Most anticipated
cd 2.0/ && ./trade.sh sync-cloud     # Sync DB + backup
cd 2.0/ && ./trade.sh health         # Health check

# 6.0 Agents
cd 6.0/ && ./agent.sh whisper        # Parallel scan
cd 6.0/ && ./agent.sh analyze TICKER # Deep dive
```

## 5.0 Cloud API

**Base:** `https://trading-desk-vquzm76kja-ue.a.run.app`

| Endpoint | Description |
|----------|-------------|
| `/api/analyze?ticker=XXX` | Deep analysis |
| `/api/whisper` | High-VRP opportunities |
| `/api/scan?date=YYYY-MM-DD` | Scan date |
| `/api/council?ticker=XXX` | 6-source AI sentiment council |
| `/api/health` | System health |

Rate limit: 60 req/min per IP.

## Databases

**ivcrush.db** (`2.0/data/ivcrush.db` | `gs://trading-desk-data/ivcrush.db`) — 15 tables, schema v6:
- `historical_moves` (6,921) | `earnings_calendar` (6,762) | `strategies` (235 rows, 203 verified 2025 trades — remainder are multi-year or cancelled entries)
- `trade_journal` (634) | `position_limits` (428) | `bias_predictions` (28) | `iv_log` (16)
- Empty: `analysis_log`, `cache`, `rate_limits`, `backtest_runs`, `backtest_trades`, `job_status`, `ticker_metadata`
- Note: ~239 trade_journal rows have sale_date < acquired_date — this is Fidelity's convention for credit trades (sell-to-open), not a bug. Strategies table is normalized to chronological order (acquired=open, sale=close).
- `job_status` table: currently empty but under active development (branch `job-status-gcs`).

**sentiment_cache.db** (`4.0/data/sentiment_cache.db`) — 3 tables, WAL mode:
- `sentiment_cache` (3hr TTL) | `api_budget` (dormant) | `sentiment_history` (permanent)

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
| `/council TICKER` | AI sentiment consensus (7-source local, 6-source cloud) |
| `/deploy [--quick\|--status\|--logs\|--rollback]` | Deploy 5.0 to Cloud Run |

## Environment Variables

`.env` files live at `2.0/.env` and `5.0/.env`. Source before running scripts: `source 2.0/.env`.

```bash
TRADIER_API_KEY=xxx          # Options data (primary)
ALPHA_VANTAGE_KEY=xxx        # Earnings calendar
TWELVE_DATA_KEY=xxx          # Historical prices
PERPLEXITY_API_KEY=xxx       # AI sentiment
FINNHUB_API_KEY=xxx          # Analyst data + news (5.0 council)
DB_PATH=data/ivcrush.db
```

GCS access: `gcloud auth application-default login` (needed for `sync-cloud` and GCS backups).

## Testing

```bash
cd 2.0 && ./venv/bin/python -m pytest tests/ -v    # 766 tests
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/  # 221 tests
cd 5.0 && ../2.0/venv/bin/python -m pytest tests/  # 507 tests
cd 6.0 && ../2.0/venv/bin/python -m pytest tests/  # 82 tests
```

## Working Style Preferences

1. **Confirm scope before executing** — For data operations (backfill, sync, cleanup), confirm which tickers, date range, and database before executing. Always scope batch ops to historically traded tickers from `strategies`/`trade_journal` or existing watchlists.
2. **Respect rewrite requests** — When asked for a full rewrite or ground-up rebuild, go straight to the clean-slate approach. Do NOT attempt incremental fixes first.
3. **Audit all related configs** — When fixing any issue, find ALL files referencing the same service, URL, endpoint, or config value and fix them together.
4. **Validate ticker symbols** against exchange lookup (e.g., "PFIZER" → "PFE"). Verify actual API response fields, don't infer from URL or title.
5. **Complete output, no truncation** — Slash commands (`/prime`, `/whisper`, `/analyze`, `/backfill`, `/scan`, etc.) must run to full completion with complete output displayed.

## When Analyzing Trades

1. Check TRR level - prefer LOW, avoid HIGH
2. Check liquidity tier - REJECT = reduce size (allowed but penalized)
3. Verify VRP >= 1.4x minimum, >= 1.8x preferred
4. **Verify DTE >= 3** - system enforces this, but double-check edge cases
5. **Prefer SINGLE options** over spreads
6. **Spreads: exit next trading day** - do not hold spreads into day 2
7. If trade goes wrong: **cut the loss, don't roll**
8. Repair only as last resort (convert single to spread)
