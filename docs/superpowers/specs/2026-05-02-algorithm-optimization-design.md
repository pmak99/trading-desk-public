# Algorithm Optimization — Direction Bias & Modifier Calibration

**Date:** 2026-05-02
**Branch:** fix/sentiment-bias-calibration
**Status:** Implemented

---

## Problem Statement

Backfill data (48 validated `bias_predictions`) revealed two miscalibrations in the sentiment/direction layer:

### 1. Conflict-hedge routing fires on a zeroed signal

The bearish modifier was zeroed in March 2026 (0/4 accuracy = 0% correct).
But `adjust_direction()` in `common/direction.py` still converted
`bullish_skew + bearish_sentiment → neutral` via the conflict_hedge rule.
A good bullish-skew trade was silently downgraded to neutral based on proven noise.

### 2. Modifier scaling inversely correlated with accuracy

| Level | Modifier (before) | Accuracy |
|---|---|---|
| strong_bullish | +2% | 23% |
| weak_bullish | +1% | 75% |

No empirical basis for the differential — and the stronger signal was worse.

---

## Solution: Option B — Routing fix + flatten modifiers

### Change 1 — `ZEROED_SENTIMENT_DIRECTIONS` constant

**File:** `common/constants.py` (and shadow copy `5.0/common/constants.py`)

Added a single source of truth for which sentiment directions have no predictive signal:

```python
ZEROED_SENTIMENT_DIRECTIONS: frozenset = frozenset({'bearish', 'strong_bearish'})
```

To re-enable bearish in the future: remove it from this set. Both routing and any callers update automatically.

### Change 2 — `eff_sent_dir` in `adjust_direction()`

**File:** `common/direction.py` (and shadow copy `5.0/common/direction.py`)

After determining the raw sentiment direction from the score or explicit parameter, remap zeroed directions to neutral before the 3-rule logic:

```python
eff_sent_dir = "neutral" if sent_dir in ZEROED_SENTIMENT_DIRECTIONS else sent_dir
```

All 3 rules and confidence calculation use `eff_sent_dir` instead of `sent_dir`.

**Behavioral change:**
- `bullish_skew + bearish_sentiment` → stays `BULLISH` (was `NEUTRAL`)
- `bullish_skew + strong_bearish_sentiment` → stays `BULLISH` (was `NEUTRAL`)
- `bearish_skew + bullish_sentiment` → still `NEUTRAL` via conflict_hedge (bullish remains active)

### Change 3 — Flatten bullish modifiers

**File:** `common/constants.py` (and shadow copy `5.0/common/constants.py`)

```python
SENTIMENT_MODIFIER_STRONG_BULLISH = 0.01   # was 0.02
SENTIMENT_MODIFIER_BULLISH = 0.01          # unchanged
```

Full modifier table:

| Direction | Modifier | Was |
|---|---|---|
| strong_bullish | 0.01 (+1%) | 0.02 (+2%) |
| bullish | 0.01 (+1%) | 0.01 (+1%) |
| neutral | 0.0 | 0.0 |
| bearish | 0.0 | 0.0 |
| strong_bearish | 0.0 | 0.0 |

---

## Files Changed

| File | Change |
|---|---|
| `common/constants.py` | Added `ZEROED_SENTIMENT_DIRECTIONS`, flattened `SENTIMENT_MODIFIER_STRONG_BULLISH` 0.02→0.01 |
| `common/direction.py` | Imported `ZEROED_SENTIMENT_DIRECTIONS`, added `eff_sent_dir` remapping, updated 3-rule logic |
| `5.0/common/constants.py` | Mirror of root constants (shadow copy) |
| `5.0/common/direction.py` | Mirror of root direction (shadow copy) |
| `5.0/tests/test_direction.py` | Updated 6 existing tests, added 2 new zeroed-bearish tests |
| `5.0/tests/test_scoring.py` | Updated strong_bullish assertion 81.6→80.8 (80×1.01) |
| `5.0/tests/test_integration_flow.py` | Updated 9 tests for new modifier and routing behavior |
| `4.0/tests/test_sentiment_direction.py` | Updated 9 tests for zeroed-bearish behavior |
| `CLAUDE.md` | Updated 4.0 Modifier description and Rule 2 table entry |

---

## Test Results

| Suite | Result |
|---|---|
| 5.0 | 495 passed |
| 4.0 | 139 passed |
| 2.0 | 775 passed, 5 pre-existing failures (Tradier API, earnings) |
| 6.0 | 63 passed, 19 pre-existing failures (test_pipeline_validation ordering) |

---

## Data Behind the Decision

- **n=48** validated `bias_predictions` as of May 2026
- **bearish accuracy: 0/4 (0%)** — zeroed March 2026, confirmed here
- **strong_bullish accuracy: ~23%** — lower than weak_bullish (75%), no differential justified
- **HIGH_BULLISH_WARNING (>=0.7)** validated: 23% of strong_bullish events had >10% crash

### Size modifier note (unchanged)

The contrarian position sizing (`SIZE_MODIFIER_BULLISH = 0.9`, `SIZE_MODIFIER_BEARISH = 1.1`) is based on n=23 samples with outlier sensitivity. It remains as a hypothesis to validate at n=50+. The `HIGH_BULLISH_WARNING` flag for tail-risk tracking is kept.
