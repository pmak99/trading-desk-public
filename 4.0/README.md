# 4.0 AI Sentiment Layer — ARCHIVED (May 2026)

**This subsystem is archived.** 5.0 reimplemented all sentiment caching natively, and the `sentiment_history` and `sentiment_cache` tables were migrated to `2.0/data/ivcrush.db` (schema migration 015). The `/collect` and `/council` skill commands read and write `ivcrush.db` directly. `4.0/data/sentiment_cache.db` is preserved for reference only.

Code is preserved here for reference. Do not extend or depend on this directory. Its tests (139) are not part of active CI.

The sentiment *rules* below remain in force — they are applied by the live skill commands and documented authoritatively in the root [CLAUDE.md](../CLAUDE.md).

---

## What 4.0 Added

```
4.0 Score = 2.0 Score × (1 + Sentiment Modifier)
```

| Sentiment | Modifier | Notes |
|-----------|:--------:|-------|
| Any bullish | +1% | Flat rate — strong_bullish is only 23% accurate, no differential justified |
| Any bearish | 0% | Zeroed May 2026 — 0/4 historical accuracy |
| Neutral | 0% | — |

**Score cutoffs:** 2.0 ≥ 50 (pre-filter) → 4.0 ≥ 55 (post-filter)

## Directional Bias (3-Rule System)

Adjusts 2.0's skew-based direction using sentiment as a secondary signal. Uses the **fused** skew direction, not raw Tradier alone.

| Rule | Condition | Result |
|------|-----------|--------|
| 1 | Skew = NEUTRAL + bullish sentiment (≥+0.3) | BULLISH (sentiment breaks tie) |
| 2 | Skew conflicts with active opposing sentiment | NEUTRAL (hedge) |
| 3 | Otherwise | Keep original skew |

Rule 2 rarely fires since bearish signals are zeroed.

## Sentiment Sources (Fallback Chain)

```
1. Cache check (3hr TTL, priority: council > perplexity > websearch)
   HIT  → Return immediately (free)
   MISS → Continue

2. Perplexity API (~$0.001–0.008/call)
   SUCCESS → Cache result + return
   FAIL    → Continue

3. WebSearch (free fallback)
   SUCCESS → Cache result + return
   FAIL    → Analysis continues without sentiment (graceful degradation)
```

Council mode (`/council`) runs multiple sources in parallel for deeper consensus. Results are cached at `source='council'` and reused by `/analyze`, `/whisper`, and `/alert`.

## Sentiment Format

All sources write the same structured format:

```
Direction: [bullish/bearish/neutral]
Score: [-1.0 to +1.0]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

## Prediction Accuracy (May 2026, at archive time)

52 records, 28 with outcomes:
- Overall: 53.8%
- Bullish: 58.3% (28/48 predictions)
- Bearish: 0% (0/4 predictions) → zeroed modifier justified

## Layout

```
4.0/
├── src/
│   ├── sentiment_direction.py    3-rule directional bias adjustment
│   └── cache/
│       ├── sentiment_cache.py    3-hour TTL cache
│       └── sentiment_history.py  Permanent records for accuracy backtesting
├── data/
│   └── sentiment_cache.db        SQLite (WAL mode) — superseded by ivcrush.db
└── tests/                        139 tests (not in CI)
```

---

*For research purposes only. Not financial advice.*
