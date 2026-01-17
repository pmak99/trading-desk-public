# 4.0 AI-First Trading System

AI-enhanced layer on top of the proven 2.0 IV Crush system. Adds sentiment analysis, intelligent caching, and sentiment-adjusted scoring.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    SLASH COMMANDS (13 total)                │
│  Discovery: /whisper /analyze /scan /alert                  │
│  System: /prime /health /maintenance                        │
│  Data: /collect /backfill  Reports: /history /backtest      │
│  Utils: /journal /export-report                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    4.0 AI LAYER                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Sentiment  │  │   Budget    │  │  Sentiment History  │  │
│  │    Cache    │  │   Tracker   │  │   (Backtesting)     │  │
│  │  (3hr TTL)  │  │  (40/day)   │  │   (Permanent)       │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    2.0 CORE ENGINE                          │
│  VRP Calculation │ Strategy Generation │ Liquidity Scoring │
│  Historical Data │ Options Pricing     │ Risk Management   │
└─────────────────────────────────────────────────────────────┘
```

## Key Design Principles

1. **AI for Discovery, Math for Trading** - Sentiment informs what to look at, 2.0 math decides how to trade
2. **Import 2.0, Don't Copy** - All core logic comes from 2.0 to stay in sync
3. **Graceful Degradation** - Sentiment never blocks analysis
4. **Cost-Conscious** - 40 calls/day budget, caching, free fallbacks

## Sentiment-Adjusted Directional Bias

3-rule system for adjusting 2.0's skew-based direction using AI sentiment:

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Neutral skew + sentiment signal | Sentiment breaks tie |
| 2 | Conflict (bullish skew + bearish sentiment) | Go neutral (hedge) |
| 3 | Otherwise | Keep original skew bias |

This allows AI to provide directional insight without overriding quantitative signals.

## 4.0 Scoring System

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

### Minimum Cutoffs
- **2.0 Score ≥ 50** (pre-sentiment filter)
- **4.0 Score ≥ 55** (post-sentiment filter)

## Structured Sentiment Format

All sentiment queries return structured data to minimize context:
```
Direction: [bullish/bearish/neutral]
Score: [-1 to +1]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

## Slash Commands

### Discovery & Analysis
| Command | Purpose | AI Cost |
|---------|---------|---------|
| `/whisper [DATE]` | Most anticipated earnings this week | ~3-8 calls |
| `/analyze TICKER [DATE]` | Deep analysis with strategies | ~1 call |
| `/scan DATE` | Scan all earnings on specific date | Free |
| `/alert` | Today's high-VRP opportunities | ~3 calls |

### System Operations
| Command | Purpose | AI Cost |
|---------|---------|---------|
| `/prime [DATE]` | Pre-cache sentiment (uses whisper list) | ~3-8 calls |
| `/health` | System health check | Free |
| `/maintenance [task]` | Database backup, cleanup, sync | Free |

### Data Collection (Backtesting)
| Command | Purpose | AI Cost |
|---------|---------|---------|
| `/collect TICKER [DATE]` | Explicitly collect sentiment | ~1 call |
| `/backfill TICKER DATE` | Record post-earnings outcome | Free |
| `/backfill --pending` | Backfill all pending outcomes | Free |
| `/backfill --stats` | Show prediction accuracy | Free |

### Analysis & Reporting
| Command | Purpose | AI Cost |
|---------|---------|---------|
| `/history TICKER` | Visualize historical moves | Free |
| `/backtest [TICKER]` | Performance analysis | Free |
| `/journal` | Parse Fidelity PDFs | Free |
| `/export-report` | Export scan results to CSV | Free |

**Note:** Discovery threshold is 1.8x VRP (EXCELLENT tier).

**TRR Warnings:** All discovery commands display ⚠️ warnings for HIGH tail risk tickers (TRR > 2.5x). These tickers are limited to 50 contracts / $25k notional to prevent catastrophic losses (learned from significant MU loss in Dec 2025).

## Daily Workflow

```
7:00 AM   /health              # Verify all systems operational
7:15 AM   /prime               # Pre-cache sentiment (predictable cost)
9:30 AM   /whisper             # Find opportunities (instant - cached)
          /analyze NVDA        # Deep dive (instant sentiment)
          Manual in Fidelity   # Human approval required
Evening   /backfill --pending  # Record outcomes for backtesting
```

## Budget & Cost

### Daily Limits
- **Max:** 40 calls/day (~$0.24/day with sonar model)
- **Warn:** At 80% (32 calls)
- **Fallback:** WebSearch when exhausted

### Monthly Cost
| Item | Cost |
|------|------|
| Perplexity API | ~$3-5 |
| All other MCPs | Free |
| **Total** | **< $5/month** |

## Fallback Chain

```
1. Check sentiment_cache (3hr TTL)
   └─ HIT → Return cached (FREE, instant)
   └─ MISS → Continue

2. Check budget (< 40 calls/day)
   └─ OK → Try Perplexity
   └─ EXHAUSTED → Skip to WebSearch

3. Perplexity API
   └─ SUCCESS → Cache + return
   └─ FAIL/TIMEOUT → Continue

4. WebSearch (free fallback)
   └─ SUCCESS → Cache + return
   └─ FAIL → Graceful degradation
```

## File Structure

```
4.0/
├── README.md                 # This file
├── data/
│   └── sentiment_cache.db    # SQLite: cache + budget + history
├── src/
│   ├── __init__.py               # Imports from 2.0
│   ├── sentiment_direction.py    # 3-rule directional bias adjustment
│   └── cache/
│       ├── __init__.py
│       ├── sentiment_cache.py    # 3-hour TTL cache
│       ├── budget_tracker.py     # API budget tracking (40/day)
│       └── sentiment_history.py  # Permanent backtesting data
└── tests/                        # Unit tests (186 tests)
    ├── test_budget_tracker.py
    ├── test_sentiment_cache.py
    ├── test_sentiment_direction.py
    └── test_sentiment_history.py
```

## MCP Servers Used

| Server | Purpose | Cost |
|--------|---------|------|
| `perplexity` | AI sentiment synthesis | ~$0.006/call |
| `alpaca` | Positions, market clock | Free |
| `alphavantage` | Earnings calendar | Free |
| `finnhub` | News, insider trades | Free |
| `yahoo-finance` | Price data fallback | Free |
| `memory` | Knowledge graph | Free |

## Testing

```bash
# From project root
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/ -v
```

**Test Coverage (Dec 2025):** 184 tests pass

Key test files:
- `test_sentiment_direction.py` - 3-rule directional bias
- `test_sentiment_cache.py` - Cache TTL, invalidation
- `test_budget_tracker.py` - API budget enforcement
- `test_sentiment_history.py` - Backtesting data storage

## Related Documentation

- [CLAUDE.md](../CLAUDE.md) - Project-wide instructions
- [2.0 README](../2.0/README.md) - Core trading system
