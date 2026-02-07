# 4.0 AI Sentiment Layer

AI-enhanced layer on top of 2.0's VRP system. Adds Perplexity-powered sentiment analysis with intelligent caching and budget tracking.

## Design Principles

1. **AI for Discovery, Math for Trading** - Sentiment informs what to look at; 2.0 math decides how to trade
2. **Import 2.0, Don't Copy** - All core logic comes from 2.0 via `sys.path` injection
3. **Graceful Degradation** - Sentiment never blocks analysis; falls back to WebSearch then skips
4. **Cost-Conscious** - 40 calls/day budget, 3-hour TTL caching, free fallbacks first

## Sentiment-Adjusted Scoring

```
4.0 Score = 2.0 Score x (1 + Sentiment Modifier)
```

| Sentiment | Score Range | Modifier |
|-----------|-------------|----------|
| Strong Bullish | >= +0.6 | +12% |
| Bullish | +0.2 to +0.6 | +7% |
| Neutral | -0.2 to +0.2 | 0% |
| Bearish | -0.6 to -0.2 | -7% |
| Strong Bearish | <= -0.6 | -12% |

**Cutoffs:** 2.0 Score >= 50 (pre-filter) | 4.0 Score >= 55 (post-filter)

## Directional Bias Rules

3-rule system for adjusting 2.0's skew-based direction using AI sentiment:

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Neutral skew + sentiment signal | Sentiment breaks tie |
| 2 | Conflict (bullish skew + bearish sentiment) | Go neutral (hedge) |
| 3 | Otherwise | Keep original skew bias |

## Budget and Fallback Chain

| Limit | Value |
|-------|-------|
| Daily max | 40 calls |
| Warning threshold | 32 calls (80%) |
| Monthly budget | ~$5.00 |

```
1. Check cache (3hr TTL)
   HIT  -> Return cached (FREE)
   MISS -> Continue

2. Check budget (< 40 calls/day)
   OK       -> Try Perplexity
   EXHAUSTED -> Skip to WebSearch

3. Perplexity API
   SUCCESS -> Cache + return
   FAIL    -> Continue

4. WebSearch (free fallback)
   SUCCESS -> Cache + return
   FAIL    -> Graceful degradation (analysis continues without sentiment)
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
│       └── sentiment_history.py  # Permanent backtesting data
├── data/
│   └── sentiment_cache.db        # SQLite (cache + budget + history)
└── tests/                        # 221 tests
```

## Database

SQLite at `data/sentiment_cache.db`:

| Table | Records | Purpose |
|-------|--------:|---------|
| `sentiment_cache` | 0 | Short-lived cache (3hr TTL, auto-clears) |
| `api_budget` | 17 | Daily Perplexity call counts |
| `sentiment_history` | 27 | Permanent sentiment records for accuracy analysis |

## Structured Sentiment Format

```
Direction: [bullish/bearish/neutral]
Score: [-1 to +1]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

## Testing

```bash
cd 4.0
../2.0/venv/bin/python -m pytest tests/ -v    # 221 tests
```

---

**Disclaimer:** For research purposes only. Not financial advice.
