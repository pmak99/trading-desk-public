# 4.0 AI Sentiment Layer

AI-enhanced layer on top of 2.0's proven VRP system. Adds Perplexity-powered sentiment analysis with intelligent caching and budget tracking.

## Design Principles

1. **AI for Discovery, Math for Trading** - Sentiment informs what to look at; 2.0 math decides how to trade
2. **Import 2.0, Don't Copy** - All core logic comes from 2.0 via sys.path injection
3. **Graceful Degradation** - Sentiment never blocks analysis; falls back to WebSearch
4. **Cost-Conscious** - 40 calls/day budget, 3-hour TTL caching, free fallbacks

## Strategy Performance (2025 Verified Data)

| Strategy | Trades | Win Rate | Total P&L | Recommendation |
|----------|-------:|:--------:|----------:|----------------|
| **SINGLE** | 108 | **63.9%** | **+$103,390** | Preferred |
| SPREAD | 86 | 52.3% | +$51,472 | Good |
| STRANGLE | 6 | 33.3% | -$15,100 | Avoid |
| IRON_CONDOR | 3 | 66.7% | -$126,429 | Caution |

**Key insight:** SINGLE options outperform spreads in both win rate and total P&L.

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

## TRR Performance

| Level | Win Rate | P&L | Recommendation |
|-------|:--------:|----:|----------------|
| **LOW** (<1.5x) | **70.6%** | **+$52k** | Preferred |
| NORMAL (1.5-2.5x) | 56.5% | -$38k | Standard |
| HIGH (>2.5x) | 54.8% | -$123k | **Avoid** |

## Critical Rules

1. **Prefer SINGLE options** - 64% vs 52% win rate vs spreads
2. **Respect TRR limits** - LOW TRR: +$52k, HIGH TRR: -$123k
3. **Never roll** - 0% success rate, always makes losses worse
4. **Cut losses early** - don't try to "fix" losing trades

## Budget & Cost

| Limit | Value |
|-------|-------|
| Daily max | 40 calls |
| Warning threshold | 32 calls (80%) |
| Monthly budget | $5.00 |

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

```
Direction: [bullish/bearish/neutral]
Score: [-1 to +1]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

## Directory Structure

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
└── tests/                        # 221 tests
```

## Testing

```bash
cd 4.0
../2.0/venv/bin/python -m pytest tests/ -v    # 221 tests
```

## Related Systems

- **2.0/** - Core VRP math (imported by 4.0)
- **5.0/** - Cloud autopilot (full skew + 3-rule direction system ported from 2.0/4.0)
- **6.0/** - Agent orchestration (uses 4.0 cache directly)

---

**Disclaimer:** For research purposes only. Not financial advice.
