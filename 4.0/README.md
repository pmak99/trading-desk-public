# 4.0 AI-First Trading System

AI-enhanced layer on top of the proven 2.0 IV Crush system. Adds sentiment analysis, intelligent caching, and data collection for backtesting.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    SLASH COMMANDS                            │
│  /health  /prime  /scan  /analyze  /collect  /backfill      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    4.0 AI LAYER                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Sentiment  │  │   Budget    │  │  Sentiment History  │  │
│  │    Cache    │  │   Tracker   │  │   (Backtesting)     │  │
│  │  (3hr TTL)  │  │ (150/day)   │  │   (Permanent)       │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    2.0 CORE ENGINE                           │
│  VRP Calculation │ Strategy Generation │ Liquidity Scoring  │
│  Historical Data │ Options Pricing     │ Risk Management    │
└─────────────────────────────────────────────────────────────┘
```

## Key Design Principles

1. **Import 2.0, Don't Copy** - All core logic comes from 2.0 to stay in sync
2. **AI is Enhancement** - Sentiment never blocks analysis; graceful degradation
3. **Cost-Conscious** - Budget tracking, caching, free fallbacks (WebSearch)
4. **Data Collection** - Build dataset to validate AI value-add over time

## Slash Commands

| Command | Purpose | AI Cost |
|---------|---------|---------|
| `/health` | System health check | Free |
| `/prime [DATE]` | Pre-cache sentiment for day's earnings | ~3-8 calls |
| `/scan DATE` | Scan all earnings on date | ~3 calls (top 3) |
| `/analyze TICKER` | Deep analysis with strategies | ~1 call |
| `/whisper [DATE]` | Most anticipated earnings | ~3 calls |
| `/collect TICKER` | Explicitly collect sentiment | ~1 call |
| `/backfill` | Record post-earnings outcomes | Free |

## Installation

The 4.0 system uses the same virtual environment as 2.0:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk
source venv/bin/activate
```

No additional dependencies required - 4.0 imports from 2.0.

## Cache Infrastructure

### Sentiment Cache (Temporary)
- **Purpose**: Avoid duplicate API calls within a session
- **TTL**: 3 hours
- **Location**: `4.0/data/sentiment_cache.db`

```python
from cache import get_cached_sentiment, cache_sentiment

# Check cache
sentiment = get_cached_sentiment("NVDA", "2025-12-09")
if sentiment:
    print("Cache hit!")
else:
    # Fetch and cache
    sentiment = fetch_from_api(...)
    cache_sentiment("NVDA", sentiment, source="perplexity")
```

### Budget Tracker
- **Purpose**: Stay within $5/month Perplexity budget
- **Limit**: 150 calls/day (~$4.50)
- **Warning**: At 80% (120 calls)
- **Fallback**: WebSearch when exhausted

```python
from cache import check_budget, record_perplexity_call

can_call, message = check_budget()
if can_call:
    # Make Perplexity call
    record_perplexity_call(cost=0.01)
else:
    # Use WebSearch fallback
    pass
```

### Sentiment History (Permanent)
- **Purpose**: Collect data for backtesting AI value-add
- **Never expires**: Permanent storage
- **Tracks**: Pre-earnings sentiment + post-earnings outcomes

```python
from cache import SentimentHistory, record_sentiment, record_outcome

# Before earnings
record_sentiment(
    ticker="NVDA",
    earnings_date="2025-12-09",
    source="perplexity",
    sentiment_text="Analysts bullish...",
    sentiment_score=0.7,  # -1 to +1
    vrp_ratio=8.2,
    implied_move_pct=12.5
)

# After earnings
record_outcome(
    ticker="NVDA",
    earnings_date="2025-12-09",
    actual_move_pct=5.2,
    actual_direction="UP",
    trade_outcome="WIN"
)

# Analyze accuracy
history = SentimentHistory()
stats = history.get_accuracy_stats()
print(f"Accuracy: {stats['accuracy']:.1%}")
```

## Sentiment Collection Workflow

### Daily Workflow
```
Morning (7-8 AM):
  /prime                    # Cache sentiment for today's earnings

During Day:
  /whisper                  # Find opportunities (instant - cached)
  /analyze NVDA             # Deep dive (instant sentiment)

After Earnings:
  /backfill --pending       # Record outcomes for past earnings
```

### Building the Dataset
```
Week 1-2:  Collect sentiment via /prime and /collect
Week 2-4:  Run /backfill --pending after each earnings day
After 30+: /backfill --stats shows prediction accuracy
```

### Expected Results
- **Baseline**: 50% (random)
- **Target**: 70-75% directional accuracy
- **Value**: ~$390/month (skip avoidance + sizing adjustments)

## File Structure

```
4.0/
├── README.md                 # This file
├── data/
│   └── sentiment_cache.db    # SQLite: cache + budget + history
├── src/
│   ├── __init__.py           # Imports from 2.0
│   ├── cache/
│   │   ├── __init__.py       # Public exports
│   │   ├── sentiment_cache.py    # 3-hour TTL cache
│   │   ├── budget_tracker.py     # API budget tracking
│   │   └── sentiment_history.py  # Permanent backtesting data
│   └── mcp/
│       └── __init__.py       # MCP client wrappers (future)
└── docs/
    └── ARCHITECTURE.md       # Detailed architecture docs
```

## MCP Servers Used

| Server | Purpose | Cost |
|--------|---------|------|
| `alpaca` | Positions, market clock | Free |
| `alphavantage` | Earnings calendar | Free |
| `finnhub` | News, insider trades | Free |
| `yahoo-finance` | Price data fallback | Free |
| `perplexity` | AI sentiment synthesis | ~$0.01/call |
| `memory` | Knowledge graph | Free |

## Fallback Chain

When fetching sentiment:

```
1. Check sentiment_cache (3hr TTL)
   └─ HIT → Return cached (FREE, instant)
   └─ MISS → Continue

2. Check budget (< 150 calls/day)
   └─ OK → Try Perplexity
   └─ EXHAUSTED → Skip to WebSearch

3. Perplexity API
   └─ SUCCESS → Cache + return
   └─ FAIL/TIMEOUT → Continue

4. WebSearch (free fallback)
   └─ SUCCESS → Cache + return
   └─ FAIL → Continue

5. Graceful degradation
   └─ Show "Sentiment unavailable"
   └─ Continue with VRP analysis only
```

## Performance Benchmarks

| Operation | Latency |
|-----------|---------|
| Cache GET | 0.06ms |
| Cache SET | 0.27ms |
| Budget check | 0.07ms |
| 2.0 analyze | ~140ms |
| 2.0 health | ~240ms |
| Database query | 0.15-0.83ms |

## Cost Analysis

| Item | Monthly Cost |
|------|--------------|
| Perplexity API | ~$1.50-3.00 |
| All other MCPs | Free |
| **Total** | **< $5/month** |

| Item | Monthly Value |
|------|---------------|
| Skip bad trades | $200-400 |
| Position sizing | $50-100 |
| Direction bias | $50-150 |
| **Total** | **~$390/month** |

**ROI: ~100-200x** (Value vs Cost)

## Development

### Running Tests
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk
./venv/bin/python scripts/benchmark_4_0.py
```

### Testing Cache Infrastructure
```python
cd /Users/prashant/PycharmProjects/Trading\ Desk
./venv/bin/python -c "
from cache import SentimentCache, BudgetTracker, SentimentHistory
cache = SentimentCache()
print(cache.stats())
"
```

## Related Documentation

- [2.0 README](../2.0/README.md) - Core trading system
- [Architecture](docs/ARCHITECTURE.md) - Detailed design docs
- [CLAUDE.md](../CLAUDE.md) - Project-wide instructions
