# API Caching Quick Reference

## Cache Overview

| Cache | Location | TTL | Max Size | Purpose |
|-------|----------|-----|----------|---------|
| Options Chains | `TradierOptionsClient._options_chain_cache` | 15 min | 200 | Options contracts with Greeks |
| Stock Quotes | `TradierOptionsClient._quote_cache` | 1 min | 500 | Current stock prices |
| Expirations | `TradierOptionsClient._expirations_cache` | 24 hr | 500 | Available expiration dates |
| Weekly Options | `TradierOptionsClient._weekly_options_cache` | 24 hr | 500 | Weekly options availability |

## Thread-Safety

All caches are **thread-safe**:
- ✅ `LRUCache` uses `threading.Lock()`
- ✅ Memoization decorators use `threading.Lock()`
- ✅ Safe for concurrent `ThreadPoolExecutor` usage

## Quick Usage

### Check Cache Stats

```python
from src.options.tradier_client import TradierOptionsClient

client = TradierOptionsClient()

# After running analysis
stats = client._options_chain_cache.stats()
print(f"Hit rate: {stats['hit_rate']:.1f}%")
print(f"Size: {stats['size']}/{stats['max_size']}")
```

### Clear Caches

```python
# Clear all caches
client._options_chain_cache.clear()
client._quote_cache.clear()
client._expirations_cache.clear()
client._weekly_options_cache.clear()
```

### Enable Debug Logging

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('src.options.tradier_client')
logger.setLevel(logging.DEBUG)

# You'll see cache hits/misses:
# DEBUG:src.options.tradier_client:AAPL: Quote cache hit (price: $182.45)
```

## Performance Impact

### Typical Workflow (50 tickers)

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| API calls | 400 | 150 | **62.5%** reduction |
| 2nd run (within TTL) | 400 | ~0 | **~100%** reduction |

### Per-Call Latency (AAPL Example)

| Call # | Before | After | Improvement |
|--------|--------|-------|-------------|
| 1st    | 199ms  | 199ms | 0% (miss) |
| 2nd    | 127ms  | ~0ms  | **100%** |

## Troubleshooting

### Low Hit Rate?

Check if:
1. TTL is too short for your workflow
2. Cache size is too small (check evictions)
3. Tickers are being processed multiple times with different parameters

### Memory Concerns?

Total cache memory: ~11 MB (negligible)

Reduce if needed:
```python
# In tradier_client.py __init__:
self._options_chain_cache = LRUCache(max_size=100, ttl_minutes=15)  # Reduced from 200
```

### Stale Data?

Caches automatically expire based on TTL:
- Quotes: 1 minute
- Options chains: 15 minutes
- Expirations: 24 hours

Force refresh by clearing caches (see above).

## Files Modified

- `src/core/lru_cache.py` - Added thread-safety
- `src/core/memoization.py` - Added thread-safety
- `src/options/tradier_client.py` - Added API caching

## Full Documentation

See [CACHING_IMPLEMENTATION.md](./CACHING_IMPLEMENTATION.md) for complete details.

---

**Version**: 1.0
**Last Updated**: 2025-11-11
