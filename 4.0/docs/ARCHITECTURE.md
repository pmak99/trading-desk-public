# 4.0 AI-First Trading System Architecture

## Overview

Version 4.0 transforms the IV Crush trading system into an **AI-first platform** powered by Claude Code, MCP integrations, and intelligent agents. The core 2.0 VRP logic remains the foundation, enhanced with AI-driven sentiment analysis, autonomous research, and smarter decision-making.

## Design Principles

1. **AI-Native** - Claude orchestrates all workflows, not just assists
2. **MCP-Powered** - External data via Model Context Protocol servers
3. **Cost-Conscious** - Optimize API usage within budget constraints
4. **Human-in-Loop** - AI recommends, human approves trades

---

## MCP Server Stack

### Active Servers

| MCP Server | Purpose | Cost | Priority |
|------------|---------|------|----------|
| **perplexity** | Sentiment research, news analysis | $5/mo budget | High-value only |
| **finnhub** | Earnings surprises, insider trades, news | Free tier | Primary news source |
| **alphavantage** | Earnings calendar, fundamentals | Free tier | Earnings dates |
| **alpaca** | Paper/live trading, positions, orders | Free | Execution |
| **yahoo-finance** | Historical prices, fallback data | Free | Backup |
| **memory** | Knowledge graph, learning from trades | Free | Context persistence |
| **sequential-thinking** | Complex multi-step reasoning | Free | Analysis |

### Removed Servers

| MCP Server | Reason for Removal |
|------------|-------------------|
| gemini | Redundant - Claude handles reasoning better, image generation not useful for trading |

---

## Perplexity Usage Strategy

### Model Selection

| Model | Response Time | Cost/Query | Use For |
|-------|---------------|------------|---------|
| **sonar-pro** | 2-10 seconds | ~$0.01-0.03 | 95% of queries |
| **sonar-deep-research** | 3-20 minutes | ~$0.15-0.50 | 5% strategic research |

### Budget Allocation ($5/month)

```
Monthly Budget: $5.00
├── sonar-pro queries: ~100-150 ($3.00)
│   ├── Pre-earnings sentiment checks
│   ├── Quick news lookups
│   └── Analyst rating queries
│
└── sonar-deep-research: ~5 queries ($2.00)
    ├── Weekend sector deep dives
    ├── Unfamiliar ticker comprehensive analysis
    └── Complex market condition research
```

### When to Use Each Model

#### sonar-pro (Default)
- "What's the sentiment on NVDA earnings?"
- "Any recent analyst upgrades for AAPL?"
- "Latest news on TSLA delivery numbers"
- "Earnings whisper for AMD?"

#### sonar-deep-research (Rare)
- "Comprehensive analysis of semiconductor sector earnings trends"
- "Deep dive on SMCI - accounting concerns, competitive position, growth outlook"
- "Research AI infrastructure spending by hyperscalers and implications for chip stocks"

### Cost Control Rules

1. **Never use deep-research for single-ticker sentiment** - sonar-pro is sufficient
2. **Batch questions** - Ask multiple things in one query
3. **Use Finnhub first** - Free news/earnings surprises before Perplexity
4. **Reserve deep-research for weekends** - When time isn't critical
5. **Track usage** - Monitor spend mid-month, adjust if needed

---

## Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER REQUEST                                 │
│                    "Analyze NVDA for earnings"                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      CLAUDE ORCHESTRATOR                             │
│                        (Opus 4.5)                                    │
│  • Parse intent                                                      │
│  • Plan data gathering                                               │
│  • Coordinate MCP calls                                              │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
         ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
         │  FINNHUB     │ │ ALPHAVANTAGE │ │   ALPACA     │
         │  (Free)      │ │   (Free)     │ │   (Free)     │
         │              │ │              │ │              │
         │ • News       │ │ • Earnings   │ │ • Positions  │
         │ • Insider    │ │   calendar   │ │ • Quotes     │
         │ • Surprises  │ │ • Fundament. │ │ • Orders     │
         └──────────────┘ └──────────────┘ └──────────────┘
                    │             │             │
                    └─────────────┼─────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    INITIAL ANALYSIS                                  │
│  • VRP calculation (from 2.0 logic)                                  │
│  • Liquidity scoring                                                 │
│  • Historical move analysis                                          │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │  VRP > 4x AND           │
                    │  Liquidity = ACCEPT?    │
                    └─────────────────────────┘
                         │              │
                        YES             NO
                         │              │
                         ▼              ▼
         ┌──────────────────┐    ┌──────────────────┐
         │   PERPLEXITY     │    │   SKIP TICKER    │
         │   (sonar-pro)    │    │   (Save money)   │
         │                  │    └──────────────────┘
         │ Sentiment check  │
         │ on high-VRP only │
         └──────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      MEMORY MCP                                      │
│  • Store analysis results                                            │
│  • Track trade outcomes                                              │
│  • Build ticker knowledge                                            │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   TRADE RECOMMENDATION                               │
│  • Strategy type (spread, naked, iron condor)                        │
│  • Position sizing (Half-Kelly)                                      │
│  • Risk parameters                                                   │
│  • Confidence score                                                  │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │   HUMAN APPROVAL        │
                    │   (Required for trades) │
                    └─────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      ALPACA MCP                                      │
│  • Execute approved trades                                           │
│  • Monitor positions                                                 │
│  • Track P&L                                                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Agent Roles

### 1. Research Agent
**Purpose:** Gather and synthesize market data
**MCPs Used:** Finnhub, Alpha Vantage, Yahoo Finance, Perplexity (sparingly)
**Responsibilities:**
- Fetch earnings dates and historical surprises
- Collect news and insider trading data
- Sentiment analysis (Perplexity for high-conviction only)

### 2. Analysis Agent
**Purpose:** Apply VRP logic and scoring
**MCPs Used:** Memory (for historical context)
**Responsibilities:**
- Calculate VRP ratio
- Score liquidity
- Apply composite scoring (55% VRP, 25% Move, 20% Liquidity)
- Compare to historical patterns

### 3. Strategy Agent
**Purpose:** Generate trade recommendations
**MCPs Used:** Alpaca (for current positions/account)
**Responsibilities:**
- Select optimal strategy type
- Calculate position sizing
- Define entry/exit criteria
- Risk management parameters

### 4. Execution Agent
**Purpose:** Trade execution and monitoring
**MCPs Used:** Alpaca
**Responsibilities:**
- Place approved orders
- Monitor fills
- Track position P&L
- Alert on significant moves

### 5. Learning Agent
**Purpose:** Improve system over time
**MCPs Used:** Memory
**Responsibilities:**
- Record all trade outcomes
- Identify pattern successes/failures
- Update ticker-specific knowledge
- Surface insights from historical data

---

## Directory Structure

```
4.0/
├── docs/
│   ├── ARCHITECTURE.md          # This document
│   ├── MCP_GUIDE.md             # MCP configuration and usage
│   └── PERPLEXITY_STRATEGY.md   # Detailed Perplexity usage rules
│
├── src/
│   ├── agents/
│   │   ├── research.py          # Research agent
│   │   ├── analysis.py          # VRP/scoring agent
│   │   ├── strategy.py          # Trade strategy agent
│   │   ├── execution.py         # Order execution agent
│   │   └── learning.py          # Learning/memory agent
│   │
│   ├── mcp/
│   │   ├── perplexity.py        # Perplexity MCP wrapper with cost tracking
│   │   ├── finnhub.py           # Finnhub MCP wrapper
│   │   └── alpaca.py            # Alpaca MCP wrapper
│   │
│   ├── core/
│   │   └── vrp.py               # VRP logic (inherited from 2.0)
│   │
│   └── cli/
│       └── main.py              # CLI entry point
│
├── prompts/
│   ├── research.md              # Research agent prompts
│   ├── analysis.md              # Analysis agent prompts
│   └── strategy.md              # Strategy agent prompts
│
└── trade.sh                     # Main entry point
```

---

## Key Improvements Over 2.0

| Aspect | 2.0 | 4.0 |
|--------|-----|-----|
| **Data Gathering** | Manual API calls | MCP-orchestrated |
| **Sentiment** | None | Perplexity AI-powered |
| **Decision Logic** | Rule-based only | AI-augmented reasoning |
| **Learning** | Static | Memory MCP knowledge graph |
| **Execution** | Manual | Alpaca MCP (with approval) |
| **Context** | Per-session | Persistent via Memory |

---

## Cost Summary

| Component | Monthly Cost |
|-----------|--------------|
| Claude Code | Existing subscription |
| Perplexity API | $5 (included in Pro) |
| Finnhub | Free |
| Alpha Vantage | Free |
| Alpaca | Free |
| Yahoo Finance | Free |
| Memory MCP | Free |
| **Total Additional** | **$0** (using existing subscriptions) |

---

## Next Steps

1. [ ] Set up 4.0 directory structure
2. [ ] Create MCP wrapper modules with cost tracking
3. [ ] Port 2.0 VRP logic to 4.0
4. [ ] Implement agent framework
5. [ ] Build CLI interface
6. [ ] Test with paper trading
7. [ ] Document operational procedures
