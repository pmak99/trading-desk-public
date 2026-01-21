# Archived Versions

This directory contains deprecated and experimental versions of the Trading Desk system.

**Active Versions:** 2.0, 4.0, 5.0 (see main CLAUDE.md)

---

## 1.0-original-system (Deprecated)

**Status:** Superseded by 2.0 on 2024-11-09
**Last Active Commit:** `b5314c0` - Gemini 2.5 Flash integration
**Size:** ~1.1 MB | 96 files

### What It Was
The original IV Crush earnings trading system with:
- AI client integration (Gemini)
- Core analysis modules for earnings plays
- Reddit scraper for sentiment
- Full test suite (20+ test files)
- Profiling and benchmarking infrastructure

### Why Archived
- Replaced by 2.0's cleaner DDD architecture
- 2.0 added Result[T, E] error handling, DI container, better separation of concerns
- No active dependencies from other versions
- Full git history preserved for reference

### Notable Artifacts
- `profiling/OPTIMIZATION_REPORT.md` - Performance analysis
- `config/trading_criteria.yaml` - Original trading rules
- `tests/validation/` - Validation test suite

---

## 3.0-ml-enhancement (Experimental/Incomplete)

**Status:** Development paused at Phase 2
**Last Active Commit:** `437ebcd` - Thread safety fixes
**Size:** ~540 MB (includes trained ML models)

### What It Was
Machine learning enhancement layer designed to predict:
1. Earnings move **magnitude** (how much will stock move)
2. Earnings move **direction** (up or down)
3. Volatility patterns

### Development Phases Completed

| Phase | Status | Key Outcome |
|-------|--------|-------------|
| Phase 0: Baseline | Complete | 2.0 achieves 57.4% win rate, $261k YTD |
| Phase 1: Data/Features | Complete | 4,926 rows × 83 features, 92% coverage |
| Phase 2: Model Development | Complete | Critical finding: direction unpredictable |
| Phase 3: Integration | Not Started | Paused due to Phase 2 findings |

### Critical Findings (from PROGRESS.md)

**Direction Prediction Fails:**
```
Random Forest (best ML): 54% accuracy
2.0 VRP Baseline:        57.4% accuracy
Conclusion: ML adds no edge for direction
```

The fundamental limitation: historical data cannot predict direction because:
- Each earnings event has unique catalysts
- Market expectations shift between quarters
- Real-time IV data (not available in backtest) is the key signal

**Magnitude Prediction Shows Promise:**
```
Cross-validation R²: 0.26
Use Case: Position sizing based on expected move size
```

### Why Archived
- Phase 2 proved direction prediction doesn't work with available data
- Real-time IV features would require significant infrastructure
- Magnitude prediction useful but not production-critical
- 540MB footprint from trained models

### Notable Artifacts
- `PROGRESS.md` - Complete development documentation
- `models/` - Trained model artifacts (baseline, advanced, validated)
- `notebooks/` - Feature engineering and training analysis
- `src/analysis/vrp.py` - Simplified VRP (ported from 2.0)

### If Resuming Development
1. Focus on **magnitude prediction** for position sizing
2. Integrate **real-time IV features** (requires Tradier streaming)
3. Consider **ensemble with 2.0 VRP** rather than replacement

---

## scripts/ (Deprecated Scripts)

### backfill_yfinance.py

**Status:** Superseded by `2.0/scripts/backfill_historical.py` on 2026-01-21

**Why Archived:**
- Used yfinance for both prices AND timing inference (less accurate)
- `backfill_historical.py` uses Twelve Data (better prices) + database timing (from Finnhub)
- BMO/AMC logic was correct but data sources were unreliable
- Timing inference from yfinance datetime less reliable than Finnhub's explicit "hour": "amc"

**Use Instead:** `python 2.0/scripts/backfill_historical.py TICKER`

### check_alerts.py

**Status:** Superseded by `/alert` skill on 2026-01-21

**Why Archived:**
- Used outdated VRP thresholds (7.0x/4.0x) from LEGACY mode
- Current system uses BALANCED mode (1.8x/1.4x)
- `/alert` skill now uses `trade.sh scan` which has current thresholds

**Use Instead:** `/alert` command

### sync_december_pdf.py

**Status:** One-time migration completed, archived 2026-01-21

**Why Archived:**
- One-time data migration script for December 2025 trades
- Data has been migrated to database
- No longer needed for ongoing operations

---

## Restoration

To restore an archived version:

```bash
# Move back to project root
mv archive/1.0-original-system 1.0
mv archive/3.0-ml-enhancement 3.0
```

Note: These versions have no active dependencies and can be restored without affecting 2.0, 4.0, or 5.0.
