# Trading System 3.0 - Architecture Overview

## Vision

Trading System 3.0 evolves the IV crush earnings strategy platform by integrating Model Context Protocol (MCP) servers as the primary data and reasoning layer. This creates a more intelligent, conversational, and maintainable system while preserving all existing functionality.

**Core Principle:** MCPs as first-class citizens, with direct APIs as fallbacks.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Claude Conversations                        │
│   "What's my win rate?"  "Show today's candidates"  "Backtest"  │
└─────────────────────────────┬───────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        v                     v                     v
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ trades-history│   │   screening   │   │   External    │
│     MCP       │   │  results MCP  │   │     MCPs      │
│  (your data)  │   │  (your data)  │   │  (6 servers)  │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │
        └─────────┬─────────┴─────────┬─────────┘
                  │                   │
                  v                   v
┌─────────────────────────────────────────────────────────────────┐
│                    Application Layer (3.0)                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ Scanner  │  │ Analyzer │  │Backtester│  │ Executor │         │
│  │(whisper, │  │ (single  │  │(Composer)│  │ (Alpaca  │         │
│  │ ticker,  │  │  ticker) │  │          │  │  paper)  │         │
│  │ scan)    │  │          │  │          │  │          │         │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘         │
└───────┼─────────────┼─────────────┼─────────────┼───────────────┘
        │             │             │             │
        └─────────────┴──────┬──────┴─────────────┘
                             │
┌────────────────────────────┴────────────────────────────────────┐
│                    Unified Cache Layer                           │
│         SQLite L2 (persistent) + Memory L1 (hot data)           │
│    TTLs: Earnings=6h, Prices=5m, Transcripts=7d, MarketCap=24h  │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────┴────────────────────────────────────┐
│                   MCP Adapter Layer                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │  Alpha   │ │  Yahoo   │ │Sequential│ │ Octagon  │            │
│  │ Vantage  │ │ Finance  │ │ Thinking │ │ Research │            │
│  │   MCP    │ │   MCP    │ │   MCP    │ │   MCP    │            │
│  └────┬─────┘ └──────────┘ └──────────┘ └──────────┘            │
│       │                                                          │
│  ┌────┴─────┐  ┌──────────┐ ┌──────────┐                        │
│  │  Direct  │  │ Composer │ │  Alpaca  │                        │
│  │   API    │  │ Backtest │ │  Paper   │                        │
│  │(fallback)│  │   MCP    │ │   MCP    │                        │
│  └──────────┘  └──────────┘ └──────────┘                        │
└─────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────┴────────────────────────────────────┐
│                   Data Layer (Preserved)                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                       │
│  │ Tradier  │  │ Twitter  │  │ ivcrush  │                       │
│  │   API    │  │ Scraper  │  │   .db    │                       │
│  │(options) │  │(whisper) │  │(history) │                       │
│  └──────────┘  └──────────┘  └──────────┘                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## MCP Server Utilization

### External MCPs (7 Servers)

| MCP Server | Primary Use Cases | Cache TTL | Rate Limit |
|------------|-------------------|-----------|------------|
| **Alpha Vantage** | Earnings calendar, daily prices, NEWS_SENTIMENT, technical indicators (RSI, BBANDS, ATR, MACD) | 6 hours | 25/day |
| **Yahoo Finance** | Market cap filter, stock history | 5 minutes | Unlimited |
| **Sequential Thinking** | Trade reasoning, strategy selection, position sizing | None | Unlimited |
| **Octagon** | Earnings transcripts, institutional holdings, deep research | 24 hours | Trial |
| **Composer Trade** | Strategy backtesting, parameter optimization | None | Free tier |
| **Alpaca** | Paper trading, account status, order execution | None | Unlimited |
| **Memory** | Persistent preferences, ticker notes, strategy configs, session context | Persistent | Unlimited |

### Custom MCPs (2 Servers - Your Data)

| MCP Server | Tables Queried | Tools Provided |
|------------|----------------|----------------|
| **trades-history** | `positions`, `position_legs`, `performance_metrics` | `query_trades`, `get_strategy_performance`, `get_open_positions`, `get_symbol_history`, `calculate_metrics` |
| **screening-results** | `analysis_log`, `earnings_calendar`, `historical_moves` | `get_todays_candidates`, `get_ticker_analysis`, `get_upcoming_earnings`, `search_by_criteria`, `get_historical_screening`, `get_historical_moves` |

---

## Key Design Decisions

### 1. Adapter Pattern for Zero Breaking Changes

All MCP integrations wrap existing interfaces. The Container returns MCP adapters when `USE_MCP=true`, original implementations otherwise.

```
AlphaVantageAPI.get_earnings_calendar()
    → AlphaVantageMCPAdapter.get_earnings_calendar()
      (returns identical format)
```

**Benefit:** `trade.sh` and all modes work unchanged.

### 2. Unified Cache with Data-Type TTLs

Single cache system for all sources. TTLs based on data volatility, not source:

- Earnings dates change rarely → 6 hour TTL
- Stock prices need freshness → 5 minute TTL
- Transcripts are static → 7 day TTL

**Benefit:** Optimal free-tier usage, consistent caching logic.

### 3. Sequential Thinking for Complex Decisions

Replace simple threshold-based logic with multi-step reasoning:

- Trade opportunity analysis (5-step evaluation)
- Strategy selection (iron condor vs put spread)
- Position sizing (Kelly with portfolio considerations)

**Benefit:** Better trade decisions, explainable recommendations.

### 4. Composer for Strategy Validation

Backtest before risking capital:

- Validate IV crush strategy historically
- Optimize VRP thresholds, DTE ranges
- Compare to baseline strategies

**Benefit:** Data-driven parameters, confidence in strategy.

### 5. Custom MCPs for Conversational Access

Query your own data through Claude:

- "What's my win rate on iron condors?"
- "Show my NFLX trading history"
- "Find candidates with edge_score > 70"

**Benefit:** Natural language access to your data.

---

## Preserved Components (from 2.0)

These remain unchanged:

| Component | Reason |
|-----------|--------|
| `trade.sh` | Orchestration works, all modes preserved |
| `EarningsWhisperScraper` | Unique Twitter/OCR capability |
| `TradierAPI` | No MCP equivalent, brokerage integration |
| CLI interfaces | All flags preserved |
| Output formats | grep patterns in trade.sh work |
| `Result` pattern | Error handling unchanged |
| Database schema | All tables preserved |

---

## Operational Modes

All existing modes continue working:

### Whisper Mode
```bash
./trade.sh whisper
```
Enhanced with:
- Yahoo Finance MCP for market cap (unlimited)
- Sequential Thinking for better ranking

### Ticker Mode
```bash
./trade.sh list AAPL,NVDA,TSLA
```
Enhanced with:
- Alpha Vantage MCP for calendar (cached)
- Optional Octagon research

### Scanning Mode
```bash
./trade.sh scan 2024-01-20
```
Enhanced with:
- MCP-backed data fetching
- Better caching

### Single Ticker Analysis
```bash
./trade.sh NVDA --with-reasoning
```
Enhanced with:
- Sequential Thinking analysis
- Octagon earnings research
- Strategy recommendation with explanation

### NEW: Backtesting Mode
```bash
./trade.sh backtest validate
./trade.sh backtest optimize
```
- Composer MCP integration
- Parameter optimization

### NEW: Paper Trading
```bash
./trade.sh paper NVDA --strategy bull_put_spread
```
- Alpaca MCP execution
- Track paper P/L

---

## Benefits Over 2.0

| Aspect | 2.0 | 3.0 |
|--------|-----|-----|
| **Trade decisions** | Simple VRP threshold | Multi-step reasoning |
| **Strategy selection** | Fixed rules | AI-selected with explanation |
| **Position sizing** | Basic Kelly | Portfolio-aware Kelly |
| **Parameter tuning** | Manual trial/error | Automated backtesting |
| **Data access** | SQL queries | Natural language |
| **API management** | Per-source limiters | Unified cache with TTLs |
| **Extensibility** | Add more API code | Add MCP server |
| **Pre-earnings sentiment** | None | NEWS_SENTIMENT analysis |
| **Technical timing** | None | RSI, BBANDS, ATR, MACD |
| **Context persistence** | None | Memory MCP for preferences |

---

## Free Tier Optimization

| MCP | Limit | Strategy |
|-----|-------|----------|
| Alpha Vantage | 25/day | 6-hour cache, batch requests (includes NEWS_SENTIMENT + technical indicators) |
| Yahoo Finance | Unlimited | Light 5-min cache |
| Octagon | Trial (2 weeks) | 24-hour cache, prioritize research |
| Composer | Free backtesting | Use for validation only |
| Alpaca | Unlimited | No cache (real-time needed) |
| Sequential Thinking | Unlimited | Use freely for reasoning |
| Memory | Unlimited | Persistent storage for preferences and notes |

---

## Migration Path

1. **Phase 1:** Create MCP adapters with fallbacks
2. **Phase 2:** Build custom MCP servers
3. **Phase 3:** Add Sequential Thinking integration
4. **Phase 4:** Add Composer backtesting
5. **Phase 5:** Optimize and remove deprecated code

**Rollback:** Set `USE_MCP=false` to revert to 2.0 behavior.

---

## Success Metrics

After 3.0 deployment:

- [ ] All trade.sh modes work identically
- [ ] MCP calls are cached appropriately
- [ ] Win rate improves with better reasoning
- [ ] Strategy parameters are optimized via backtesting
- [ ] Can query trades/screening via Claude conversation
- [ ] Free tier limits are never exceeded
