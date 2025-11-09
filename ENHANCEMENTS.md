# Performance Enhancements & Tools

This document describes the advanced performance tools and enhancements available in the Trading Desk.

---

## 🎯 Overview

Three powerful tools have been added for performance optimization and monitoring:

1. **Performance Benchmarking** - Track performance improvements over time
2. **Market Calendar Integration** - Accurate holiday detection with pandas-market-calendars
3. **Profiling Utilities** - Find bottlenecks with cProfile

---

## 1. 📊 Performance Benchmarking

### Purpose
Track execution time, memory usage, and API call counts across runs to validate optimizations.

### Location
`benchmarks/performance_tracker.py`

### Installation
No additional dependencies required (uses built-in `psutil`).

### Usage

#### Create a Baseline
```bash
python benchmarks/performance_tracker.py \
  --tickers "AAPL,MSFT,GOOGL" \
  --baseline
```

#### Run Benchmark and Compare
```bash
python benchmarks/performance_tracker.py \
  --tickers "AAPL,MSFT,GOOGL" \
  --compare
```

#### View History
```bash
python benchmarks/performance_tracker.py --history
```

### Output Example
```
🏁 Benchmark started
⏱️  Elapsed: 12.45s
💾 Memory: 125.3MB → 156.8MB (Δ31.5MB)
📊 Results saved to: benchmarks/results/benchmark_20251108_143022.json

📈 COMPARISON vs baseline_20251107_120000.json:
   Time: 82.5% improvement
   Memory: 15.3% improvement
```

### Metrics Tracked
- **Execution time** (seconds)
- **Memory usage** (MB)
- **Memory delta** (growth during execution)
- **Tickers analyzed** (count)
- **Success/failure rates**

### Results Storage
- Saved in `benchmarks/results/*.json`
- `.gitignore` configured to exclude result files
- Easy to parse for visualization

---

## 2. 📅 Market Calendar Integration

### Purpose
Accurate US market holiday detection using industry-standard pandas-market-calendars library.

### Location
`src/data/calendars/market_calendar.py`

### Installation
```bash
pip install pandas-market-calendars
```

### Features
- ✅ Accurate market holidays (NYSE, NASDAQ, etc.)
- ✅ Half-day detection (early close days)
- ✅ Historical data back to 1962
- ✅ Automatic fallback if library not installed
- ✅ Trading hours with timezone support

### Usage

```python
from src.data.calendars.market_calendar import MarketCalendarClient

# Initialize
calendar = MarketCalendarClient(exchange='NYSE')

# Check if today is a trading day
is_trading = calendar.is_trading_day(datetime.now())

# Get next trading day
next_day = calendar.get_next_trading_day(datetime.now())

# Get trading hours
hours = calendar.get_trading_hours(datetime.now())
if hours:
    print(f"Open: {hours['open']}")
    print(f"Close: {hours['close']}")
    if hours['early_close']:
        print("⚠️  EARLY CLOSE")

# Get all holidays for a year
holidays = calendar.get_holidays(2025)
```

### Advantages Over Manual Lists
| Feature | Manual Lists | pandas-market-calendars |
|---------|-------------|-------------------------|
| Holidays through | 2027 | Forever |
| Early close days | ❌ | ✅ |
| Historical data | ❌ | ✅ (back to 1962) |
| Maintenance | Manual updates | Automatic |
| Accuracy | Good | Excellent |

### Fallback Behavior
If `pandas-market-calendars` is not installed, falls back to basic weekend detection.

---

## 3. 🔍 Profiling Utilities

### Purpose
Find performance bottlenecks using Python's cProfile with enhanced analysis.

### Location
`profiling/profiler.py`

### Dependencies
Built-in Python (cProfile, pstats)

### Usage Methods

#### Method 1: Decorator
```python
from profiling.profiler import profile_function

@profile_function
def expensive_operation():
    # Your code here
    ...
```

#### Method 2: Context Manager
```python
from profiling.profiler import Profiler

with Profiler("data_processing"):
    # Code to profile
    process_data()
```

#### Method 3: CLI - Profile Any Command
```bash
python profiling/profiler.py --run \
  "python -m src.analysis.earnings_analyzer --tickers AAPL --yes"
```

### Analysis Tools

#### Analyze Saved Profile
```bash
python profiling/profiler.py --analyze profiling/results/my_profile.prof
```

#### Find Hotspots (slow functions)
```bash
python profiling/profiler.py --hotspots profiling/results/my_profile.prof
```

#### Compare Two Profiles (before/after optimization)
```bash
python profiling/profiler.py --compare baseline.prof optimized.prof
```

### Output Example
```
🔍 Profiling started: earnings_analyzer
✅ Profiling completed: earnings_analyzer (14.23s)

==================================================================
TOP 20 FUNCTIONS BY CUMULATIVE TIME - earnings_analyzer
==================================================================
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.001    0.001   14.230   14.230 earnings_analyzer.py:393(analyze_specific_tickers)
       75    0.052    0.001   10.456    0.139 ticker_filter.py:244(get_ticker_data)
      150    8.234    0.055    8.234    0.055 {method 'history' of 'yfinance'}
...

📊 Detailed stats saved to: profiling/results/earnings_analyzer_20251108.prof
   Analyze with: python -m pstats profiling/results/earnings_analyzer_20251108.prof
```

### Finding Bottlenecks
The profiler helps identify:
- **Slow functions** (high cumulative time)
- **Called-too-often** (high call count)
- **I/O bottlenecks** (API calls, file access)
- **CPU-intensive operations** (high total time)

---

## 🎯 Complete Workflow Example

### Step 1: Create Baseline
```bash
# Create baseline before optimizations
python benchmarks/performance_tracker.py \
  --tickers "AAPL,MSFT,GOOGL,NVDA,META" \
  --baseline
```

### Step 2: Profile to Find Bottlenecks
```bash
# Find what's slow
python profiling/profiler.py --run \
  "python -m src.analysis.earnings_analyzer --tickers AAPL,MSFT,GOOGL --yes"

# Analyze results
python profiling/profiler.py --hotspots profiling/results/profile_*.prof
```

### Step 3: Make Optimizations
```python
# Example: Found API calls are slow
# Implement caching, batch fetching, etc.
```

### Step 4: Verify Improvement
```bash
# Run benchmark and compare
python benchmarks/performance_tracker.py \
  --tickers "AAPL,MSFT,GOOGL,NVDA,META" \
  --compare

# Expected output:
# Time: 45.2% improvement ✅
```

---

## 📈 Performance Tracking Best Practices

### 1. **Consistent Test Cases**
Always use the same tickers for benchmarks:
```bash
# Good: Consistent
--tickers "AAPL,MSFT,GOOGL"

# Bad: Different each time
--tickers "RANDOM,TICKERS,HERE"
```

### 2. **Multiple Runs**
Run 3-5 times and average:
```bash
for i in {1..5}; do
  python benchmarks/performance_tracker.py --tickers "AAPL,MSFT" --compare
done
```

### 3. **Control Variables**
- Same time of day (API performance varies)
- Same network conditions
- Close other applications

### 4. **Document Changes**
Save what you changed between benchmarks:
```bash
# Tag commits
git tag -a "v1.0-baseline" -m "Performance baseline"

# Run benchmark
python benchmarks/performance_tracker.py --baseline

# After optimization
git tag -a "v1.1-optimized" -m "Added caching"
python benchmarks/performance_tracker.py --compare
```

---

## 🔧 Troubleshooting

### Benchmark Shows Regression
```bash
# 1. Profile to find new bottleneck
python profiling/profiler.py --run "your_command"

# 2. Compare profiles before/after
python profiling/profiler.py --compare old.prof new.prof

# 3. Find specific hotspot
python profiling/profiler.py --hotspots new.prof
```

### pandas-market-calendars Not Working
```bash
# Verify installation
pip show pandas-market-calendars

# Reinstall if needed
pip install --upgrade pandas-market-calendars

# Test
python -c "from src.data.calendars.market_calendar import MarketCalendarClient; MarketCalendarClient()"
```

### Profiling Slows Down Too Much
```bash
# Use sampling profiler for faster profiling
# (profiles every N function calls instead of all)

# Or profile specific sections only
with Profiler("specific_section"):
    slow_function()  # Only this gets profiled
```

---

## 📚 Additional Resources

### cProfile Documentation
- [Python cProfile docs](https://docs.python.org/3/library/profile.html)
- Understanding output: `python -m pstats your_file.prof`

### pandas-market-calendars
- [GitHub repo](https://github.com/rsheftel/pandas_market_calendars)
- [Documentation](https://pandas-market-calendars.readthedocs.io/)
- Supported exchanges: NYSE, NASDAQ, CME, LSE, TSX, etc.

### Visualization Tools
Convert profiling data to visual call graphs:
```bash
# Install graphviz
pip install gprof2dot graphviz

# Generate call graph
gprof2dot -f pstats profiling/results/my_profile.prof | dot -Tpng -o profile.png
```

---

## 🎯 Quick Reference

### Benchmarking
```bash
# Create baseline
python benchmarks/performance_tracker.py --tickers "TICKERS" --baseline

# Compare
python benchmarks/performance_tracker.py --tickers "TICKERS" --compare

# History
python benchmarks/performance_tracker.py --history
```

### Market Calendar
```python
from src.data.calendars.market_calendar import MarketCalendarClient
calendar = MarketCalendarClient()
is_trading = calendar.is_trading_day(date)
```

### Profiling
```bash
# Profile command
python profiling/profiler.py --run "COMMAND"

# Analyze
python profiling/profiler.py --analyze FILE.prof

# Find hotspots
python profiling/profiler.py --hotspots FILE.prof
```

---

## 🎉 Results Expected

With these tools, you can:

✅ **Quantify improvements** - Know exactly how much faster your code is
✅ **Find bottlenecks** - Identify the slowest 1% of code
✅ **Track regressions** - Catch performance degradation early
✅ **Validate optimizations** - Prove optimizations actually work
✅ **Accurate holidays** - No more manual calendar updates

---

**Happy Optimizing!** 🚀
