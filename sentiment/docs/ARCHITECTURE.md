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

| Command | Usage | Purpose | AI Enhancement |
|---------|-------|---------|----------------|
| `/health` | `/health` | System health + MCP connectivity | None (diagnostic) |
| `/analyze` | `/analyze NVDA` | Deep ticker analysis + strategies | Finnhub news + Perplexity sentiment |
| `/whisper` | `/whisper` | Most anticipated earnings this week | Perplexity for top 3 |
| `/prime` | `/prime` | Pre-cache sentiment for today | Bulk Perplexity fetch |
| `/scan` | `/scan 2025-12-09` | Scan earnings by date | Perplexity for top 3 |
| `/alert` | `/alert` | Today's high-VRP opportunities | Perplexity sentiment |
| `/history` | `/history NVDA` | Historical moves visualization | Claude pattern analysis |
| `/backtest` | `/backtest` | Performance analysis | Claude AI insights |
| `/journal` | `/journal` | Parse Fidelity PDFs | None (utility) |

**Typical Workflow:**
```
Morning:  /health           → Verify all systems operational
          /prime            → Pre-cache sentiment (predictable cost)
          /whisper          → Find best opportunities (instant, cached)
Pick:     /analyze NVDA     → Deep dive on best candidate
Execute:  Manual in Fidelity (human approval required)
```

---

## MCP Server Stack

### Active Servers

| MCP Server | Purpose | Cost | Tools |
|------------|---------|------|-------|
| **finnhub** | News, earnings surprises, insider trades | Free | `finnhub_news_sentiment`, `finnhub_stock_fundamentals`, `finnhub_stock_ownership` |
| **alphavantage** | Earnings calendar, fundamentals | Free | `EARNINGS_CALENDAR`, `COMPANY_OVERVIEW` |
| **alpaca** | Positions, account, market clock | Free | `alpaca_list_positions`, `alpaca_get_clock`, `alpaca_account_overview` |
| **yahoo-finance** | Historical prices, fallback data | Free | `getStockHistory` |
| **memory** | Knowledge graph, trade history | Free | `create_entities`, `search_nodes`, `add_observations` |
| **sequential-thinking** | Complex multi-step reasoning | Free | `sequentialthinking` |

### Perplexity MCP (Requires Setup)

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

---

## Liquidity Tiers

| Tier | Criteria | Action |
|------|----------|--------|
| **EXCELLENT** | High OI, tight spreads, good volume | Full position |
| **WARNING** | Marginal OI or wide spreads | Reduce size, wider stops |
| **REJECT** | Low OI, very wide spreads | **DO NOT TRADE** |

**Critical Rule:** REJECT tier = NO TRADE. Never override. (Lesson from significant loss)

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
SOURCE:       perplexity | websearch (tracks quality)
```

**Flow:**
```
1. Check cache for ticker+date
2. If HIT (< 3 hours old) → return cached (FREE, instant)
3. If MISS → check daily budget
4. If budget OK → call Perplexity, cache result
5. If budget exhausted → fall back to WebSearch
```

### Daily Budget Tracking

- Max: 150 calls/day (~$4.50 of $5 budget)
- Warn: At 80% (120 calls)
- Hard stop: At 100% (graceful degradation to WebSearch)
- Reset: Date comparison on each call (no cron needed)

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
├── health.md              # /health
├── analyze.md             # /analyze TICKER
├── whisper.md             # /whisper
├── prime.md               # /prime
├── scan.md                # /scan DATE
├── alert.md               # /alert
├── history.md             # /history TICKER
├── backtest.md            # /backtest
└── journal.md             # /journal

4.0/
├── docs/
│   └── ARCHITECTURE.md    # This document
├── src/
│   ├── __init__.py        # 2.0 module imports
│   ├── cache/
│   │   ├── sentiment_cache.py   # Perplexity cache wrapper
│   │   └── budget_tracker.py    # Daily API budget
│   └── mcp/
│       └── finnhub_client.py    # Finnhub rate limiting
└── data/
    └── sentiment_cache.db       # SQLite cache + budget
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

## Critical Lessons (from significant Loss)

1. **ALWAYS check liquidity FIRST** - before VRP evaluation
2. **REJECT tier = NO TRADE** - never override
3. **Kelly edge prevents negative EV** - raw R/R is dangerous
4. **Profit zone penalty** - tight strategies fail on large moves
5. **Market hours awareness** - OI-only scoring when closed

---

## Implementation Phases

### Phase 0: Setup
- [ ] Verify/configure Perplexity MCP
- [ ] Remove Gemini MCP (optional)
- [ ] Delete old slash commands to be replaced

### Phase 1: Infrastructure
- [ ] Create `4.0/src/` with 2.0 imports
- [ ] Create `sentiment_cache.py`
- [ ] Create `budget_tracker.py`

### Phase 2: P0 Commands (Core)
- [ ] `/health` - System diagnostics
- [ ] `/analyze` - Single ticker analysis
- [ ] `/whisper` - Weekly opportunities
- [ ] `/prime` - Cache warming

### Phase 3: P1 Commands
- [ ] `/scan` - Date-based scanning
- [ ] `/alert` - Today's opportunities

### Phase 4: P2 Commands
- [ ] `/history` - Historical visualization
- [ ] `/backtest` - Performance analysis

### Phase 5: P3 Commands
- [ ] `/journal` - PDF parsing

### Phase 6: Testing
- [ ] Test cache hits/misses
- [ ] Test fallback chain
- [ ] Verify budget tracking
- [ ] End-to-end workflow testing

---

## References

- **Detailed Implementation Plan:** `/home/user/.claude/plans/quizzical-sauteeing-mango.md`
- **2.0 System Documentation:** `$PROJECT_ROOT/2.0/README.md`
- **Project Instructions:** `$PROJECT_ROOT/CLAUDE.md`
