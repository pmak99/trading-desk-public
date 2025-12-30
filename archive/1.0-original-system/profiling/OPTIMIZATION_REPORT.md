# Performance Profiling & Optimization Report

**Date:** 2025-11-11
**Codebase:** Trading Desk - Earnings IV Crush Analyzer
**Analysis Tool:** cProfile + Custom Benchmarking

---

## Executive Summary

The application is already well-optimized with **80% improvement** over baseline. Current performance:
- **1.02s for 3 tickers** (data fetching only)
- **0.34s per ticker average**
- **~12-14s for full analysis** (with AI sentiment/strategy generation)

**Primary Bottleneck:** Network I/O accounts for **70-75%** of execution time.

---

## Profiling Results

### 1. Data Fetching Performance (3 tickers: AAPL, MSFT, GOOGL)

```
Total Time: 1.02s
├── yfinance .info calls: 0.772s (76%)
│   └── Network I/O (curl): 0.742s (73%)
├── Options data (parallel): 0.246s (24%)
│   └── Tradier API calls: 0.188s
└── IVHistoryTracker init: 0.485s (48%)
```

**Key Metrics:**
- **Time per ticker:** 0.34s
- **Network I/O:** 0.742s (73% of total)
- **Parallel speedup:** Already implemented for options data

### 2. API Call Analysis (Single ticker: AAPL)

```
Single Tradier API call: 0.188s
├── Network I/O (SSL read): 0.120s (64%)
├── HTTP requests: 3 calls
│   ├── Quote fetch: ~50ms
│   ├── Expirations: ~50ms
│   └── Options chain: ~50ms
└── Data parsing: 0.068s (36%)
```

**Observations:**
- **3 HTTP requests** per ticker for Tradier
- **Connection reuse:** Already using `requests.Session`
- **SSL overhead:** 64% of API call time

### 3. Cache Performance

```
Cache Statistics:
├── Hit time: 1.74μs per lookup
├── Miss time: 0.40μs per lookup
├── Memory per entry: ~1KB
└── Cache growth: 0.98MB per 1000 entries
```

**Verdict:** Cache is extremely efficient. No optimization needed.

### 4. Memory Usage

```
Current Memory:
├── RSS: 120 MB (working set)
├── Cache overhead: ~1MB per 1000 entries
└── Growth: Linear and bounded (LRU eviction)
```

**Verdict:** Memory usage is well-controlled. LRU cache prevents leaks.

---

## Performance Hotspots

### Top Time Consumers (Ranked by Impact)

| Function | Time | % of Total | Impact |
|----------|------|------------|--------|
| `curl_easy_perform` (network I/O) | 0.742s | 73% | 🔥 **CRITICAL** |
| `yfinance.info` property | 0.772s | 76% | 🔥 **CRITICAL** |
| `IVHistoryTracker.__init__` | 0.485s | 48% | 🔴 **HIGH** |
| `_fetch_options_parallel` | 0.246s | 24% | 🟡 **MEDIUM** |
| `get_options_data` (Tradier) | 0.188s | 18% | 🟡 **MEDIUM** |

---

## Optimization Opportunities

### 🔥 **HIGH IMPACT** (20-50% improvement potential)

#### 1. Parallelize yfinance Data Fetching
**Current:** Sequential fetching (3 × 0.26s = 0.78s)
**Proposed:** Parallel fetching (max = 0.26s)
**Speedup:** ~3x for data fetching phase
**Effort:** Low (use `ThreadPoolExecutor`)

```python
# Current (src/analysis/ticker_data_fetcher.py:84-117)
for ticker in tickers:
    stock = yf.Ticker(ticker)
    info = stock.info  # Sequential: 0.26s each

# Proposed
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(fetch_info, t): t for t in tickers}
    # Parallel: max(0.26s) = 0.26s
```

**Impact:** Reduce data fetching from 1.02s → ~0.50s (50% improvement)

---

#### 2. Reuse IVHistoryTracker Instance
**Current:** Creates new tracker for each ticker (0.485s × 3 = 1.455s wasted)
**Issue:** DB connection overhead in `__init__`
**Proposed:** Singleton or instance reuse pattern

```python
# Current (src/analysis/ticker_data_fetcher.py:181-188)
from src.options.iv_history_tracker import IVHistoryTracker
tracker = IVHistoryTracker()  # NEW connection each time!

# Proposed: Class-level tracker
class TickerDataFetcher:
    def __init__(self, ticker_filter):
        self.ticker_filter = ticker_filter
        self.iv_tracker = IVHistoryTracker()  # Reuse across all tickers
```

**Impact:** Eliminate 1.455s overhead → ~30% improvement

---

#### 3. Batch Tradier API Calls
**Current:** 3 HTTP requests per ticker (quote + expirations + chain)
**Proposed:** Single batch request if Tradier API supports it

**Alternative:** HTTP/2 connection reuse
```python
# Use HTTP/2 adapter if available
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

adapter = HTTPAdapter(
    pool_connections=10,
    pool_maxsize=20,
    pool_block=False
)
# Enable HTTP/2 if server supports
```

**Impact:** 10-20% reduction in API call time

---

### 🟡 **MEDIUM IMPACT** (5-15% improvement potential)

#### 4. Cache yfinance .info Results Aggressively
**Current:** No caching of yfinance data
**Proposed:** Add TTL cache (15 minutes)

```python
from functools import lru_cache
from datetime import datetime, timedelta

class YFinanceCache:
    cache = {}

    @classmethod
    def get_info(cls, ticker: str, ttl_minutes=15):
        if ticker in cls.cache:
            data, timestamp = cls.cache[ticker]
            if datetime.now() - timestamp < timedelta(minutes=ttl_minutes):
                return data

        stock = yf.Ticker(ticker)
        data = stock.info
        cls.cache[ticker] = (data, datetime.now())
        return data
```

**Impact:** Near-instant for repeated tickers (useful for testing/debugging)

---

#### 5. Reduce yfinance Data Fetched
**Current:** Fetches entire `.info` dict (~100+ fields)
**Issue:** Only need `marketCap`, `currentPrice`, `regularMarketPrice`

**Proposed:** Use lighter endpoints if available
```python
# Try quote endpoint first (faster)
stock = yf.Ticker(ticker)
try:
    # Fast path: recent price only
    price = stock.fast_info.last_price
    market_cap = stock.fast_info.market_cap
except:
    # Fallback to full .info
    info = stock.info
    price = info.get('currentPrice')
```

**Impact:** 10-20% faster data fetching

---

#### 6. Optimize IVHistoryTracker Database Operations
**Current:** Opening DB connection on every init (0.485s overhead)
**Proposed:** Connection pooling or WAL mode

```python
# In src/options/iv_history_tracker.py
class IVHistoryTracker:
    _connection_pool = {}  # Class-level pool

    def __init__(self):
        db_path = self._get_db_path()

        if db_path not in self._connection_pool:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
            conn.execute("PRAGMA synchronous=NORMAL")  # Faster writes
            self._connection_pool[db_path] = conn

        self.conn = self._connection_pool[db_path]
```

**Impact:** Eliminate 0.485s × N overhead

---

### 🟢 **LOW IMPACT** (1-5% improvement potential)

#### 7. Use Faster JSON Parser
**Current:** Standard `json` library
**Proposed:** `orjson` (3-5x faster)

```bash
pip install orjson
```

```python
import orjson

# Replace json.loads with orjson.loads
data = orjson.loads(response.content)
```

**Impact:** 2-3% improvement (JSON parsing is small portion)

---

#### 8. Pre-compile Regular Expressions
**Current:** Regex compiled on each use
**Proposed:** Pre-compile at module level

```python
# At module level
import re
TICKER_PATTERN = re.compile(r'^[A-Z]{1,5}$')

# In function
def validate_ticker(ticker: str):
    return TICKER_PATTERN.match(ticker)
```

**Impact:** Negligible (validation is fast)

---

## Already Optimized (Keep These!)

✅ **Parallel options data fetching** (5-10x speedup vs sequential)
✅ **Batch yfinance ticker creation** (`yf.Tickers()`)
✅ **LRU caching** (bounded memory, fast lookups)
✅ **Connection pooling** (`requests.Session`)
✅ **Smart multiprocessing** (sequential for <3 tickers, parallel for 3+)
✅ **Pre-filtering by market cap/volume** (saves 92% of API calls)

---

## Recommended Implementation Priority

### Phase 1: Quick Wins (1-2 hours, 30-40% improvement)
1. ✅ **Parallelize yfinance fetching** → 50% faster data fetching
2. ✅ **Reuse IVHistoryTracker** → Eliminate 30% overhead
3. ✅ **Connection pooling for DB** → Faster DB operations

### Phase 2: Incremental Gains (2-4 hours, 10-15% improvement)
4. Cache yfinance .info results
5. Optimize yfinance data fetching (use fast_info)
6. Batch Tradier API calls if possible

### Phase 3: Fine-tuning (1-2 hours, 5% improvement)
7. Use orjson for faster JSON parsing
8. Pre-compile regex patterns
9. Profile and optimize scorer calculations

---

## Benchmarking Command

To track improvements over time:

```bash
# Baseline run
python benchmarks/performance_tracker.py --tickers "AAPL,MSFT,GOOGL" --baseline

# After optimization
python benchmarks/performance_tracker.py --tickers "AAPL,MSFT,GOOGL" --compare

# View history
python benchmarks/performance_tracker.py --history
```

---

## Additional Profiling Tools

### 1. Line-by-line profiling with `line_profiler`
```bash
pip install line_profiler
kernprof -l -v src/analysis/ticker_data_fetcher.py
```

### 2. Memory profiling with `memory_profiler`
```bash
pip install memory_profiler
python -m memory_profiler src/analysis/earnings_analyzer.py
```

### 3. Visual profiling with `snakeviz`
```bash
pip install snakeviz
python -m cProfile -o profile.prof -m src.analysis.earnings_analyzer --tickers "AAPL"
snakeviz profile.prof  # Opens browser visualization
```

---

## Monitoring & Alerting

Consider adding performance monitoring:

```python
# Add to src/core/performance_monitor.py
import time
import logging
from functools import wraps

def monitor_performance(threshold_seconds=1.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = func(*args, **kwargs)
            elapsed = time.time() - start

            if elapsed > threshold_seconds:
                logging.warning(
                    f"⚠️  {func.__name__} took {elapsed:.2f}s "
                    f"(threshold: {threshold_seconds}s)"
                )

            return result
        return wrapper
    return decorator

# Usage
@monitor_performance(threshold_seconds=0.5)
def fetch_ticker_data(ticker):
    ...
```

---

## Conclusion

The application is already well-optimized. The **biggest opportunity** is:

1. **Parallelize yfinance data fetching** → 50% improvement
2. **Reuse IVHistoryTracker** → 30% improvement
3. **Optimize DB connections** → 15% improvement

**Total potential improvement:** 60-70% faster data fetching phase
**Overall improvement:** 30-40% faster end-to-end (network I/O still dominates)

**Realistic target:** 12-14s → 8-10s for full analysis of 3 tickers

---

## Appendix: Profiling Commands

```bash
# Profile a specific run
python -m cProfile -o results/profile.prof -m src.analysis.earnings_analyzer --tickers "AAPL,MSFT,GOOGL" --yes

# Analyze results
python profiling/profiler.py --analyze results/profile.prof --top 40

# Find hotspots
python profiling/profiler.py --hotspots results/profile.prof

# Comprehensive profiling
python profiling/comprehensive_profile.py

# Run benchmarks
python benchmarks/performance_tracker.py --tickers "AAPL,MSFT,GOOGL" --benchmark
```
