# Trading Desk - IV Crush System

## Project Overview

Production quantitative options trading system focused on **IV Crush** strategy - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**2025 Performance:** ~55-60% win rate | 203 strategies | positive net P&L net P&L

## Architecture

```
6.0 Agent Orchestration ──→ Parallel Claude Code agents for analysis
5.0 Cloud Autopilot     ──→ 24/7 Cloud Run service + Telegram bot
4.0 AI Sentiment        ──→ Perplexity-powered sentiment layer
2.0 Core Math Engine    ──→ VRP/strategy calculations (shared library)
────────────────────────────────────────────────────────────────────
SQLite (ivcrush.db)     ──→ 15 tables, 6,164 historical moves
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

## Scoring System

| Factor | Weight | Description |
|--------|--------|-------------|
| VRP Edge | 55% | Core signal quality |
| Implied Move Difficulty | 25% | Easier moves get bonus |
| Liquidity Quality | 20% | Open interest, bid-ask spreads |

**4.0 Sentiment Modifier:** `4.0 Score = 2.0 Score x (1 + modifier)`

## 4-Tier Liquidity System (Relaxed Feb 2026)

| Tier | OI/Position | Spread | Score Pts | Action |
|------|-------------|--------|-----------|--------|
| **EXCELLENT** | >=5x | <=12% | 20 | Full size |
| **GOOD** | 2-5x | 12-18% | 16 | Full size |
| **WARNING** | 1-2x | 18-25% | 12 | Reduce size |
| **REJECT** | <1x | >25% | 4 | Reduce size (allowed) |

## Tail Risk Ratio (TRR)

```
TRR = Max Historical Move / Average Historical Move
```

| Level | TRR | Max Contracts | Performance (2025) |
|-------|-----|---------------|-------------------|
| **HIGH** | > 2.5x | 50 | ~55% win, **significant loss** |
| NORMAL | 1.5-2.5x | 100 | ~57% win, moderate loss |
| **LOW** | < 1.5x | 100 | **~70% win, strong profit** |

**Key insight:** LOW TRR tickers are most profitable. HIGH TRR lost significant losses - avoid or strictly limit size.

## Strategy Performance (Verified 2025 Data)

| Strategy | Trades | Win Rate | Total P&L | Recommendation |
|----------|-------:|:--------:|----------:|----------------|
| **SINGLE** | 108 | **~60-65%** | **strongest performer** | Preferred |
| **SPREAD** | 86 | ~50-55% | positive | Good |
| STRANGLE | 6 | ~33% | negative | Avoid |
| IRON_CONDOR | 3 | ~67% | significant loss (sizing) | Caution |

**Key insight:** SINGLE options outperform spreads (higher win rate, better P&L per trade).

## Trade Adjustment Rules

| Type | Definition | Leg Win Rate | Campaign Win Rate | Action |
|------|------------|:------------:|:-----------------:|--------|
| **NEW** | Original position | - | 58.2% | Standard |
| **REPAIR** | Add leg (single→spread) | 80% | 20% | Damage control only |
| **ROLL** | Close + reopen new strike/exp | 0% | 0% | **Never roll** |

**Key insight:** Repairs reduce losses but rarely save campaigns. Rolls always make things worse.

## Critical Rules

1. **Reduce size for REJECT liquidity** - thresholds relaxed Feb 2026, still penalized in scoring
2. **Prefer SINGLE options** over spreads - 64% vs 52% win rate
3. **Respect TRR limits** - HIGH TRR lost significant losses, LOW TRR made strong profit
4. **Never roll losing positions** - 0% success rate, -$129k total
5. **Cut losses early** - repairs reduce loss but rarely turn profitable
6. **VRP >= 1.8x (EXCELLENT)** for full position sizing
7. **Check liquidity first** before evaluating VRP

## Directory Structure

```
Trading Desk/
  2.0/            Core math engine (VRP, liquidity, strategies)
  4.0/            AI sentiment layer (Perplexity integration)
  5.0/            Cloud autopilot (FastAPI + Telegram)
  6.0/            Agent orchestration (parallel Claude Code)
  scripts/        Root-level utilities and data pipelines
  docs/           Research docs, trade records
```

## 2.0 - Core Math Engine

**CLI Commands:**
```bash
cd 2.0/
./trade.sh TICKER YYYY-MM-DD      # Single ticker analysis
./trade.sh scan YYYY-MM-DD        # Scan all earnings for date
./trade.sh whisper                # Most anticipated earnings
./trade.sh sync-cloud             # Sync DB with cloud + backup
./trade.sh health                 # System health check
```

## 4.0 - AI Sentiment Layer

**Sentiment-Adjusted Direction (3-rule system):**

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Neutral skew + sentiment signal | Sentiment breaks tie |
| 2 | Conflict (bullish skew + bearish sentiment) | Go neutral |
| 3 | Otherwise | Keep original skew bias |

## 5.0 - Cloud Autopilot

**Base URL:** `https://your-cloud-run-url.run.app`

| Endpoint | Description |
|----------|-------------|
| `/api/analyze?ticker=AAPL` | Deep analysis |
| `/api/whisper` | Most anticipated earnings |
| `/api/scan?date=2026-01-15` | Scan date |
| `/api/health` | System health |

**Rate Limiting:** 60 requests/minute per IP (in-memory sliding window).

## 6.0 - Agent Orchestration

```bash
cd 6.0/
./agent.sh whisper              # Find opportunities
./agent.sh analyze TICKER       # Deep dive
./agent.sh scan DATE            # Scan all earnings
```

## Database

**Location:** `2.0/data/ivcrush.db` (local), `gs://your-gcs-bucket/ivcrush.db` (cloud)

### Key Tables

| Table | Records | Purpose |
|-------|---------|---------|
| `historical_moves` | 6,165 | Post-earnings moves for VRP |
| `strategies` | 203 | Grouped trades with P&L |
| `trade_journal` | 556 | Individual option legs |
| `position_limits` | 417 | TRR-based sizing |

### Enhanced Strategies Schema

```sql
CREATE TABLE strategies (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy_type TEXT CHECK(strategy_type IN ('SINGLE', 'SPREAD', 'IRON_CONDOR', 'STRANGLE')),
    acquired_date DATE,
    sale_date DATE,
    gain_loss REAL,
    is_winner BOOLEAN,
    -- Trade tracking (added Jan 2026)
    trade_type TEXT CHECK(trade_type IN ('NEW', 'ROLL', 'REPAIR', 'ADJUSTMENT')),
    parent_strategy_id INTEGER REFERENCES strategies(id),
    campaign_id TEXT,           -- e.g., "MU-2025-12"
    trr_at_entry REAL,
    position_limit_at_entry INTEGER
);
```

### Key Queries

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
FROM strategies WHERE trr_at_entry IS NOT NULL
GROUP BY trr_level;

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
| `/analyze TICKER` | Deep dive analysis |
| `/scan DATE` | Scan all earnings |
| `/health` | System status |
| `/journal FILE` | Parse Fidelity CSV |
| `/backtest` | Performance analysis |

## Environment Variables

```bash
TRADIER_API_KEY=xxx          # Options data (primary)
ALPHA_VANTAGE_KEY=xxx        # Earnings calendar
TWELVE_DATA_KEY=xxx          # Historical prices
DB_PATH=data/ivcrush.db
```

## Testing

```bash
cd 2.0 && ./venv/bin/python -m pytest tests/ -v    # 690 tests
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/  # 221 tests
cd 5.0 && ../2.0/venv/bin/python -m pytest tests/  # 308 tests
cd 6.0 && ../2.0/venv/bin/python -m pytest tests/  # 48 tests
```

## When Analyzing Trades

1. Check TRR level - prefer LOW, avoid HIGH
2. Check liquidity tier - REJECT = reduce size (allowed but penalized)
3. Verify VRP >= 1.4x minimum, >= 1.8x preferred
4. **Prefer SINGLE options** over spreads
5. If trade goes wrong: **cut the loss, don't roll**
6. Repair only as last resort (convert single→spread)
