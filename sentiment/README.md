# 4.0 AI Sentiment Layer

AI-enhanced layer on top of 2.0's proven VRP system. Adds Perplexity-powered sentiment analysis with intelligent caching and budget tracking.

## Design Principles

1. **AI for Discovery, Math for Trading** - Sentiment informs what to look at; 2.0 math decides how to trade
2. **Import 2.0, Don't Copy** - All core logic comes from 2.0 via sys.path injection
3. **Graceful Degradation** - Sentiment never blocks analysis; falls back to WebSearch
4. **Cost-Conscious** - 40 calls/day budget, 3-hour TTL caching, free fallbacks

## Architecture

```
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
└─────────────────────────────────────────────────────────────┘
```

## Sentiment-Adjusted Scoring

### Formula

```
4.0 Score = 2.0 Score × (1 + Sentiment Modifier)
```

### Modifiers

| Sentiment | Score Range | Modifier |
|-----------|-------------|----------|
| Strong Bullish | >= +0.6 | +12% |
| Bullish | +0.2 to +0.6 | +7% |
| Neutral | -0.2 to +0.2 | 0% |
| Bearish | -0.6 to -0.2 | -7% |
| Strong Bearish | <= -0.6 | -12% |

### Minimum Cutoffs

- **2.0 Score >= 50** (pre-sentiment filter)
- **4.0 Score >= 55** (post-sentiment filter)

## Directional Bias Rules

3-rule system for adjusting 2.0's skew-based direction using AI sentiment:

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Neutral skew + sentiment signal | Sentiment breaks tie |
| 2 | Conflict (bullish skew + bearish sentiment) | Go neutral (hedge) |
| 3 | Otherwise | Keep original skew bias |

## Slash Commands

Available in Claude Code:

### Discovery & Analysis

| Command | Purpose | API Cost |
|---------|---------|----------|
| `/whisper [DATE]` | Most anticipated earnings | ~3-8 calls |
| `/analyze TICKER [DATE]` | Deep analysis with strategies | ~1 call |
| `/scan DATE` | Scan all earnings on date | Free |
| `/alert` | Today's high-VRP opportunities | ~3 calls |

### System Operations

| Command | Purpose | API Cost |
|---------|---------|----------|
| `/prime [DATE]` | Sync calendar + pre-cache sentiment | ~3-8 calls |
| `/health` | System health check | Free |
| `/maintenance [task]` | Backup, cleanup, sync | Free |

### Data Collection

| Command | Purpose | API Cost |
|---------|---------|----------|
| `/collect TICKER [DATE]` | Store sentiment for backtesting | ~1 call |
| `/backfill TICKER DATE` | Record post-earnings outcome | Free |
| `/backfill --pending` | Backfill all pending | Free |
| `/backfill --stats` | Show prediction accuracy | Free |

### Reporting

| Command | Purpose | API Cost |
|---------|---------|----------|
| `/history TICKER` | Historical moves visualization | Free |
| `/backtest [TICKER]` | Performance analysis | Free |
| `/journal` | Parse Fidelity exports | Free |
| `/export-report` | Export scan results | Free |

## Daily Workflow

```
7:00 AM   /health              # Verify systems
7:15 AM   /prime               # Sync calendar + pre-cache sentiment
9:30 AM   /whisper             # Find opportunities (instant from cache)
          /analyze NVDA        # Deep dive
          Execute in Fidelity  # Human approval required
Evening   /backfill --pending  # Record outcomes
```

## Budget & Cost

| Limit | Value |
|-------|-------|
| Daily max | 40 calls |
| Warning threshold | 32 calls (80%) |
| Monthly budget | $5.00 |
| Cost per call | ~$0.006 |

### Fallback Chain

```
1. Check cache (3hr TTL)
   └─ HIT → Return cached (FREE)
   └─ MISS → Continue

2. Check budget (< 40 calls/day)
   └─ OK → Try Perplexity
   └─ EXHAUSTED → Skip to WebSearch

3. Perplexity API
   └─ SUCCESS → Cache + return
   └─ FAIL → Continue

4. WebSearch (free fallback)
   └─ SUCCESS → Cache + return
   └─ FAIL → Graceful degradation
```

## Structured Sentiment Format

All queries return:

```
Direction: [bullish/bearish/neutral]
Score: [-1 to +1]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

## Architecture

```
4.0/
├── src/
│   ├── __init__.py               # Imports from 2.0
│   ├── sentiment_direction.py    # 3-rule directional bias
│   └── cache/
│       ├── sentiment_cache.py    # 3-hour TTL cache
│       ├── budget_tracker.py     # API budget (40/day)
│       └── sentiment_history.py  # Backtesting data
├── data/
│   └── sentiment_cache.db        # SQLite cache + budget
└── tests/                        # 186 tests
```

## Testing

```bash
cd 4.0
../2.0/venv/bin/python -m pytest tests/ -v    # 186 tests
```

Key test files:
- `test_sentiment_direction.py` - 3-rule directional bias
- `test_sentiment_cache.py` - Cache TTL, invalidation
- `test_budget_tracker.py` - Budget enforcement
- `test_sentiment_history.py` - Backtesting storage

## TRR Warnings

All discovery commands display warnings for HIGH tail risk tickers (TRR > 2.5x). These are limited to 50 contracts / $25k notional.

## Related Systems

- **2.0/** - Core VRP math (imported by 4.0)
- **5.0/** - Cloud autopilot (full skew + 3-rule direction system ported from 2.0/4.0)
- **6.0/** - Agent orchestration (uses 4.0 cache directly)

---

**Disclaimer:** For research purposes only. Not financial advice.
