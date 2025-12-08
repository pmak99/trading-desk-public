# 4.0 AI-First Trading System Architecture

## Overview

Version 4.0 transforms the IV Crush trading system into an **AI-first platform** powered by Claude Code slash commands, MCP integrations, and intelligent workflows. The core 2.0 VRP logic remains the foundation, enhanced with AI-driven sentiment analysis and smarter decision-making.

**Key Design Decision:** HYBRID approach
- Core VRP/strategy logic → Reuse proven 2.0 Python scripts (import, don't copy)
- AI enrichment → MCPs for news, sentiment (Perplexity sparingly)
- Fallback chain → Perplexity → WebSearch → graceful degradation

---

## Design Principles

1. **AI-Native** - Claude orchestrates all workflows via slash commands
2. **MCP-Powered** - External data via Model Context Protocol servers
3. **Cost-Conscious** - Perplexity only for VRP > 4x, with caching
4. **Human-in-Loop** - AI recommends, human approves trades
5. **Graceful Degradation** - Always complete analysis, even if sentiment fails

---

## Slash Commands

### Discovery & Analysis
| Command | Usage | Purpose | AI Enhancement |
|---------|-------|---------|----------------|
| `/whisper` | `/whisper [DATE]` | Most anticipated earnings this week | Perplexity sentiment for all qualified |
| `/analyze` | `/analyze TICKER [DATE]` | Deep ticker analysis + strategies | Finnhub news + Perplexity sentiment |
| `/scan` | `/scan DATE` | Scan all earnings on specific date | VRP analysis, no sentiment |
| `/alert` | `/alert` | Today's high-VRP opportunities | Perplexity sentiment |

### System Operations
| Command | Usage | Purpose | AI Enhancement |
|---------|-------|---------|----------------|
| `/prime` | `/prime [DATE]` | Pre-cache sentiment for week | Bulk Perplexity fetch |
| `/health` | `/health` | System health + MCP connectivity | None (diagnostic) |
| `/maintenance` | `/maintenance [task]` | Backups, cleanup, sync, integrity | None (utility) |

### Data Collection (Backtesting)
| Command | Usage | Purpose | AI Enhancement |
|---------|-------|---------|----------------|
| `/collect` | `/collect TICKER [DATE]` | Store pre-earnings sentiment | Perplexity sentiment |
| `/backfill` | `/backfill TICKER DATE` | Record post-earnings outcomes | None (data entry) |
| `/backfill` | `/backfill --pending` | Backfill all pending outcomes | None (batch) |
| `/backfill` | `/backfill --stats` | Show prediction accuracy | None (analysis) |

### Analysis & Reporting
| Command | Usage | Purpose | AI Enhancement |
|---------|-------|---------|----------------|
| `/history` | `/history TICKER` | Historical moves visualization | Claude pattern analysis |
| `/backtest` | `/backtest [TICKER]` | Performance analysis | Claude AI insights |
| `/journal` | `/journal` | Parse Fidelity PDFs | None (utility) |
| `/export-report` | `/export-report` | Export scan results to CSV | None (utility) |

**Typical Daily Workflow:**
```
7:00 AM   /health           → Verify all systems operational
7:15 AM   /prime            → Pre-cache sentiment (predictable cost)
9:30 AM   /whisper          → Find best opportunities (instant, cached)
          /analyze NVDA     → Deep dive on best candidate
          Manual in Fidelity → Human approval required
Evening   /backfill --pending → Record outcomes for completed earnings
```

---

## MCP Server Stack

### Active Servers

| MCP Server | Purpose | Cost | Tools |
|------------|---------|------|-------|
| **finnhub** | News, earnings surprises, insider trades | Free (60/min) | `finnhub_news_sentiment`, `finnhub_stock_fundamentals`, `finnhub_stock_ownership` |
| **alphavantage** | Earnings calendar, fundamentals | Free | `EARNINGS_CALENDAR`, `COMPANY_OVERVIEW` |
| **alpaca** | Positions, account, market clock | Free | `alpaca_list_positions`, `alpaca_get_clock`, `alpaca_account_overview` |
| **yahoo-finance** | Historical prices, fallback data | Free | `getStockHistory` |
| **memory** | Knowledge graph, trade history | Free | `create_entities`, `search_nodes`, `add_observations` |
| **sequential-thinking** | Complex multi-step reasoning | Free | `sequentialthinking` |

### Perplexity MCP (Configured ✅)

| Tool | Purpose | Cost | Speed |
|------|---------|------|-------|
| `perplexity_ask` | Quick sentiment Q&A | ~$0.01 | 2-5s |
| `perplexity_search` | Web search with ranking | ~$0.01 | 2-5s |
| `perplexity_research` | Deep research | ~$0.15-0.50 | 3-20 min |
| `perplexity_reason` | Complex reasoning | ~$0.05 | 5-15s |

**Usage Rule:** Use `perplexity_ask` for 95% of queries. AVOID `perplexity_research` (too slow/expensive).

### Not Used in 4.0

| MCP Server | Reason |
|------------|--------|
| gemini | Image generation not useful for options trading workflows |

### External APIs (via 2.0)

| API | Purpose | Notes |
|-----|---------|-------|
| **Tradier** | Options Greeks, IV, chains | Primary source for implied move calculation |
| **Alpha Vantage** | Earnings dates | Also available via MCP |

---

## Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER COMMAND                                 │
│                      /analyze NVDA                                   │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 STEP 1: CHECK EXISTING POSITIONS                     │
│                        (Alpaca MCP - Free)                           │
│  • mcp__alpaca__alpaca_list_positions                                │
│  • Warn if existing exposure to ticker                               │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 STEP 2: RUN 2.0 CORE ANALYSIS                        │
│                    (Proven Python Scripts)                           │
│  • VRP calculation (Tradier → implied move)                          │
│  • Liquidity scoring (OI, spreads, volume)                           │
│  • Historical move analysis (SQLite database)                        │
│  • Strategy generation with Greeks                                   │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 STEP 3: GATHER FREE DATA                             │
│                     (Always, Every Ticker)                           │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │
│  │  FINNHUB    │  │  FINNHUB    │  │  FINNHUB    │                  │
│  │  News       │  │  Earnings   │  │  Insider    │                  │
│  │  Headlines  │  │  Surprises  │  │  Trades     │                  │
│  └─────────────┘  └─────────────┘  └─────────────┘                  │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │  VRP > 4x AND           │
                    │  Liquidity != REJECT?   │
                    └─────────────────────────┘
                         │              │
                        YES             NO
                         │              │
                         ▼              ▼
┌──────────────────────────────┐  ┌──────────────────────────────┐
│  STEP 4: SENTIMENT SYNTHESIS │  │  SKIP SENTIMENT              │
│  (Conditional, Cached)       │  │  (Display raw news only)     │
│                              │  └──────────────────────────────┘
│  1. Check cache (3hr TTL)    │
│     └─ HIT → return cached   │
│                              │
│  2. Try Perplexity           │
│     └─ FAIL → try WebSearch  │
│                              │
│  3. Try Claude WebSearch     │
│     └─ FAIL → graceful skip  │
│                              │
│  4. Cache result             │
└──────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 STEP 5: STORE IN MEMORY MCP                          │
│  • Create/update ticker entity                                       │
│  • Add analysis observations                                         │
│  • Link to any resulting trades                                      │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      OUTPUT TO USER                                  │
│  • Position warning (if existing exposure)                           │
│  • VRP assessment + tier (EXCELLENT/GOOD/MARGINAL/SKIP)             │
│  • Liquidity grade (EXCELLENT/WARNING/REJECT)                        │
│  • News summary (Finnhub - always shown)                             │
│  • AI Sentiment (if VRP > 4x, from cache or fresh)                  │
│  • Strategy recommendations (2-3 ranked)                             │
│  • Position sizing (Half-Kelly)                                      │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │   HUMAN DECISION        │
                    │   Execute in Fidelity   │
                    └─────────────────────────┘
```

---

## VRP Tiers and Thresholds

```
VRP Ratio = Implied Move % / Historical Mean Move %

Tiers:
├── EXCELLENT: >= 7.0x  → High confidence, full position
├── GOOD:      >= 4.0x  → Tradeable, standard position
├── MARGINAL:  >= 1.5x  → Minimal edge, reduce size
└── SKIP:      <  1.5x  → No edge, don't trade
```

**Cost Control Rule:** Only fetch Perplexity sentiment for VRP ≥ 4.0x tickers.

### Composite Scoring Weights (Scan Mode)

| Factor | Weight | Description |
|--------|--------|-------------|
| VRP Edge | 55% | Core signal quality (`min(VRP/4.0, 1.0) × 55`) |
| Implied Move Difficulty | 25% | Easier moves get bonus |
| Liquidity Quality | 20% | EXCELLENT=20, WARNING=12, REJECT=4 |

---

## Liquidity Tiers

| Tier | Criteria | Action |
|------|----------|--------|
| **EXCELLENT** | High OI, tight spreads, good volume | Full position |
| **WARNING** | Marginal OI or wide spreads | Reduce size, wider stops |
| **REJECT** | Low OI, very wide spreads | **DO NOT TRADE** |

**Critical Rule:** REJECT tier = NO TRADE. Never override. (Lesson from $26,930 loss)

---

## 4.0 Sentiment-Adjusted Scoring

### Formula
```
4.0 Score = 2.0 Score × (1 + Sentiment_Modifier)
```

### Sentiment Modifiers
| Sentiment | Score Range | Modifier |
|-----------|-------------|----------|
| Strong Bullish | +0.7 to +1.0 | +12% |
| Bullish | +0.3 to +0.6 | +7% |
| Neutral | -0.2 to +0.2 | 0% |
| Bearish | -0.6 to -0.3 | -7% |
| Strong Bearish | -1.0 to -0.7 | -12% |

### Minimum Score Cutoffs
- **2.0 Score ≥ 50** (pre-sentiment filter)
- **4.0 Score ≥ 55** (post-sentiment filter)

### Structured Sentiment Format
All sentiment queries return structured data to minimize context usage:
```
Direction: [bullish/bearish/neutral]
Score: [-1 to +1]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

### Design Principle
- **AI for discovery** (what to look at)
- **Math for trading** (how to trade it)
- Never let sentiment override VRP/liquidity rules

---

## Sentiment Caching Strategy

### Problem
Same ticker queried multiple times per day wastes API budget:
- `/whisper` finds NVDA → queries Perplexity
- `/analyze NVDA` later → would query again

### Solution: 3-Hour Cache

```
Cache Key:    sentiment:{TICKER}:{YYYY-MM-DD}:{SOURCE}
TTL:          3 hours (allows refresh for breaking news)
Storage:      SQLite via 2.0's HybridCache
SOURCE:       perplexity | websearch (tracks data quality)
```

**Flow:**
```
1. Check cache for ticker+date (any SOURCE, return newest)
2. If HIT (< 3 hours old) → return cached (FREE, instant)
3. If MISS → check daily budget
4. If budget OK → call Perplexity, cache result
5. If budget exhausted → fall back to WebSearch, cache with source=websearch
```

**Cache Lookup Priority:** Any source accepted. If multiple cached entries exist, prefer `perplexity` over `websearch` for higher quality sentiment.

### Daily Budget Tracking

- Max: 40 calls/day (~$0.24/day with sonar model)
- Warn: At 80% (32 calls)
- Hard stop: At 100% (graceful degradation to WebSearch)
- Reset: Date comparison on each call (no cron needed)
- Monthly budget: $5.00

---

## Fallback Chain (Sentiment)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  CACHE HIT  │ ──► │  PERPLEXITY │ ──► │  WEBSEARCH  │ ──► Graceful Skip
│  (instant)  │     │  (primary)  │     │  (fallback) │     (raw news only)
└─────────────┘     └─────────────┘     └─────────────┘
     FREE              ~$0.01              FREE
```

**Key Principle:** Sentiment is ENHANCEMENT, not REQUIREMENT. Analysis always completes.

---

## Error Handling

### Finnhub Errors (News Gathering)
| Error | Action |
|-------|--------|
| Rate limited (60/min) | Wait 1s, retry once, then skip news |
| API error | Skip news section, show "News unavailable" |
| No data | Show "No recent news" (not an error) |

### Perplexity Errors (Sentiment)
| Error | Action |
|-------|--------|
| Timeout (> 30s) | Fall back to WebSearch |
| Rate limited | Fall back to WebSearch |
| Budget exhausted | Fall back to WebSearch, show warning |
| API error | Fall back to WebSearch |

### 2.0 Script Errors
| Error | Action |
|-------|--------|
| Script timeout (5 min) | Abort command with error |
| Database locked | Retry once, then abort |
| Missing ticker data | Show "No historical data" warning |

### MCP Connection Errors
| Error | Action |
|-------|--------|
| Server disconnected | Suggest `/health` check |
| Tool not found | Skip that data source |

---

## 2.0 Integration (Import, Don't Copy)

```python
# 4.0/src/__init__.py
import sys
from pathlib import Path

# Add 2.0/src to Python path - stay in sync with 2.0
_2_0_src = Path(__file__).parent.parent.parent / "2.0" / "src"
sys.path.insert(0, str(_2_0_src))

# Now all 2.0 modules are importable
from domain.types import *
from domain.errors import Result, Ok, Err
from infrastructure.cache.hybrid_cache import HybridCache
```

### Key 2.0 Components Used

| Component | Purpose |
|-----------|---------|
| `domain/types.py` | Money, Strike, OptionQuote, VRPResult, Strategy |
| `domain/errors.py` | Result[T,E] pattern for error handling |
| `application/metrics/vrp.py` | Core VRP calculation |
| `application/metrics/liquidity_scorer.py` | 3-tier liquidity scoring |
| `application/services/strategy_generator.py` | Strategy generation with Greeks |
| `infrastructure/cache/hybrid_cache.py` | 2-tier L1/L2 caching |
| `utils/rate_limiter.py` | Token bucket rate limiting |
| `utils/circuit_breaker.py` | API failure protection |

---

## Directory Structure

```
.claude/commands/           # Slash command definitions
├── alert.md               # /alert - Today's opportunities
├── analyze.md             # /analyze TICKER [DATE] - Deep analysis
├── backfill.md            # /backfill - Record post-earnings outcomes
├── backtest.md            # /backtest [TICKER] - Performance analysis
├── collect.md             # /collect TICKER - Explicit sentiment collection
├── export-report.md       # /export-report - CSV/Excel export
├── health.md              # /health - System diagnostics
├── history.md             # /history TICKER - Historical moves
├── journal.md             # /journal - PDF parsing
├── maintenance.md         # /maintenance [task] - System maintenance
├── prime.md               # /prime [DATE] - Cache warming
├── scan.md                # /scan DATE - Date scanning
└── whisper.md             # /whisper [DATE] - Weekly opportunities

4.0/
├── README.md              # System overview and usage
├── docs/
│   └── ARCHITECTURE.md    # This document
├── src/
│   ├── __init__.py        # 2.0 module imports
│   └── cache/
│       ├── __init__.py          # Cache module exports
│       ├── sentiment_cache.py   # 3-hour TTL cache
│       ├── budget_tracker.py    # Daily API budget (40/day)
│       └── sentiment_history.py # Permanent backtesting storage
└── data/
    └── sentiment_cache.db       # SQLite: cache + budget + history
```

---

## Market Hours Awareness

| Scenario | VRP Validity | Action |
|----------|--------------|--------|
| Market open (9:30 AM - 4:00 PM ET) | Live IV data | Normal analysis |
| Pre-market (before 9:30 AM) | Prior close IV | Show warning, OK for planning |
| After hours | Prior close IV | Show warning |
| Weekend/Holiday | Stale data | Skip Perplexity (save budget), warn user |

**Detection:** Use `mcp__alpaca__alpaca_get_clock` to check `is_open`.

---

## Cost Summary

| Component | Monthly Cost |
|-----------|--------------|
| Claude Code | Existing subscription |
| Perplexity API | ~$3 (with caching) |
| Finnhub | Free |
| Alpha Vantage | Free |
| Alpaca | Free |
| Yahoo Finance | Free |
| Memory MCP | Free |
| **Total Additional** | **~$3/month** |

---

## Critical Lessons (from $26,930 Loss)

1. **ALWAYS check liquidity FIRST** - before VRP evaluation
2. **REJECT tier = NO TRADE** - never override
3. **Kelly edge prevents negative EV** - raw R/R is dangerous
4. **Profit zone penalty** - tight strategies fail on large moves
5. **Market hours awareness** - OI-only scoring when closed

---

## Implementation Status

### Phase 0: Setup ✅ COMPLETE
- [x] Verify/configure Perplexity MCP
- [x] Gemini MCP retained (image generation available)
- [x] Old slash commands replaced with 4.0 versions

### Phase 1: Infrastructure ✅ COMPLETE
- [x] Create `4.0/src/` with 2.0 imports
- [x] Create `sentiment_cache.py` (3-hour TTL cache)
- [x] Create `budget_tracker.py` (150 calls/day limit)
- [x] Create `sentiment_history.py` (permanent backtesting storage)

### Phase 2: P0 Commands ✅ COMPLETE
- [x] `/health` - System diagnostics
- [x] `/analyze` - Single ticker analysis
- [x] `/whisper` - Weekly opportunities
- [x] `/prime` - Cache warming with history collection

### Phase 3: P1 Commands ✅ COMPLETE
- [x] `/scan` - Date-based scanning
- [x] `/alert` - Today's opportunities

### Phase 4: P2 Commands ✅ COMPLETE
- [x] `/history` - Historical visualization
- [x] `/backtest` - Performance analysis

### Phase 5: P3 Commands ✅ COMPLETE
- [x] `/journal` - PDF parsing

### Phase 6: Data Collection ✅ COMPLETE
- [x] `/collect` - Explicit sentiment collection
- [x] `/backfill` - Post-earnings outcome recording

### Phase 7: Testing
- [x] Cache operations benchmarked (<1ms)
- [x] Budget tracking verified
- [ ] Live trading validation (ongoing)

---

## References

- **Detailed Implementation Plan:** `.claude/plans/` (search for "4.0 Slash Commands")
- **2.0 System Documentation:** `2.0/README.md`
- **Project Instructions:** `CLAUDE.md`
