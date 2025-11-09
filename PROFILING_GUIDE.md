# Profiling Guide - Trading Desk

Quick guide for profiling the Trading Desk earnings analyzer.

---

## Prerequisites

✅ **Dependencies Installed:**
```bash
pip install psutil  # For benchmarking - INSTALLED
pip install pytz    # For timezone handling - INSTALLED
```

Note: Use virtual environment if system is externally-managed:
```bash
source venv/bin/activate
pip install psutil pytz
```

---

## Performance Benchmarking

### Create Baseline
```bash
source venv/bin/activate
python benchmarks/performance_tracker.py \
  --tickers "AAPL,MSFT,GOOGL" \
  --date 2025-11-08 \
  --baseline
```

### Compare Performance
```bash
source venv/bin/activate
python benchmarks/performance_tracker.py \
  --tickers "AAPL,MSFT,GOOGL" \
  --date 2025-11-08 \
  --compare
```

### View History
```bash
source venv/bin/activate
python benchmarks/performance_tracker.py --history
```

---

## Code Profiling

### Method 1: Direct cProfile
```bash
source venv/bin/activate
python -m cProfile -o profiling/results/test.prof \
  -m src.analysis.earnings_analyzer \
  --tickers "AAPL,MSFT,GOOGL" 2025-11-08 --yes
```

### Method 2: Using Profiler Tool
```bash
# Activate venv first (uses 'python' within venv)
source venv/bin/activate
python profiling/profiler.py --run \
  "-m src.analysis.earnings_analyzer --tickers AAPL,MSFT,GOOGL 2025-11-08 --yes"
```

### Analyze Results
```bash
source venv/bin/activate

# View top functions
python profiling/profiler.py --analyze profiling/results/test.prof

# Find hotspots (>0.1s)
python profiling/profiler.py --hotspots profiling/results/test.prof

# Interactive analysis
python -m pstats profiling/results/test.prof
>>> stats.sort_stats('cumulative')
>>> stats.print_stats(20)
>>> stats.print_callers(10)
```

---

## Quick Performance Test

### Simple Timing
```bash
source venv/bin/activate
time python -m src.analysis.earnings_analyzer \
  --tickers "AAPL,MSFT,GOOGL" 2025-11-08 --yes
```

### Memory Usage
```bash
source venv/bin/activate
/usr/bin/time -l python -m src.analysis.earnings_analyzer \
  --tickers "AAPL,MSFT,GOOGL" 2025-11-08 --yes \
  2>&1 | grep "maximum resident set size"
```

---

## Current Performance Metrics

**Latest Profiling Results (3 tickers: AAPL, MSFT, GOOGL):**
- **Total Time**: 2.19s
- **Memory**: 107 MB → 119 MB (Δ11 MB)
- **API Calls**: 58% of runtime (yfinance + Tradier)
- **Module Imports**: 39% (one-time startup cost)
- **Analysis Logic**: 3% (optimized filtering/scoring)

**Performance Breakdown:**
```
Total: 2.77s (from cProfile)
├─ API calls: 1.62s (58%)
│  ├─ yfinance: 0.82s
│  └─ Tradier: 0.80s
├─ Module imports: 1.09s (39%)
└─ Analysis logic: 0.06s (3%)
```

**Baseline (75 tickers):**
- Time: ~12-14 seconds
- API calls: ~210
- Memory: ~150-200 MB peak

**Optimizations Applied:**
- ✅ Batch fetching (30-50% improvement)
- ✅ LRU caching (prevents memory leaks)
- ✅ Smart multiprocessing (sequential <3, parallel ≥3)
- ✅ Eliminated duplicate API calls
- ✅ History reuse across functions

**Total Improvement:** 80-83% faster than baseline (71s → 12-14s)

---

## Profiling Workflow

1. **Create baseline:** Measure current performance
2. **Make changes:** Implement optimization
3. **Profile:** Identify remaining bottlenecks
4. **Compare:** Verify improvement
5. **Repeat:** Iterate on hotspots

---

## Common Hotspots to Check

- API calls (yfinance, Tradier)
- History fetching (stock.history())
- Options chain parsing
- AI sentiment/strategy calls
- Data serialization

---

## Troubleshooting

### Module Not Found Errors
```bash
# Check Python environment
which python3
python3 --version

# Verify dependencies
pip3 list | grep -E "yfinance|psutil|pytz"

# Install missing
pip3 install -r requirements.txt
```

### cProfile Issues
```bash
# Use simpler approach
python3 -m cProfile -m your_module > profile.txt
less profile.txt
```

---

## Next Steps

After profiling:
1. Identify functions >1s cumulative time
2. Check for duplicate operations
3. Look for unnecessary API calls
4. Verify caching is working
5. Consider async for I/O operations

---

See `ENHANCEMENTS.md` for more details on performance tools.
