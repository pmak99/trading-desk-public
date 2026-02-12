# Logging Standards

**Purpose**: Consistent, informative logging across the Trading Desk codebase

---

## Core Principles

### 1. **Consistent Format**
```python
# For ticker-specific operations
logger.info(f"{ticker}: Starting analysis (IV={iv:.1f}%, Score={score:.1f})")
logger.warning(f"{ticker}: API rate limit hit, retrying in {delay}s")
logger.error(f"{ticker}: Failed to fetch data: {error}", exc_info=True)

# For system-level operations
logger.info("Initializing Earnings Analyzer...")
logger.warning("Daily API limit approaching (35/40 calls)")
logger.error("Failed to load config file", exc_info=True)
```

### 2. **Emojis: CLI Only, Not Logs**
```python
# ❌ BAD - emojis in logs
logger.info(f"✅ {ticker}: Analysis complete")

# ✅ GOOD - emojis in CLI output only
print(f"✅ {ticker}: Analysis complete")  # User-facing CLI
logger.info(f"{ticker}: Analysis complete")  # Log file
```

### 3. **Always Include Exception Info**
```python
# ❌ BAD - loses stack trace
except Exception as e:
    logger.error(f"API error: {e}")

# ✅ GOOD - includes full traceback
except Exception as e:
    logger.error(f"API error: {e}", exc_info=True)
```

### 4. **Remove Debug Spam from Hot Paths**
```python
# ❌ BAD - logged 75 times per run
for ticker in tickers:
    logger.debug(f"{ticker}: Cache hit")  # DON'T LOG THIS
    process_ticker(ticker)

# ✅ GOOD - only log when useful
cache_hits = sum(1 for t in tickers if is_cached(t))
if cache_hits > 0:
    logger.info(f"Cache hits: {cache_hits}/{len(tickers)} tickers")
```

---

## Log Levels

### ERROR - Failures that prevent operation
```python
logger.error(f"{ticker}: Failed to fetch options data", exc_info=True)
logger.error("TRADIER_ACCESS_TOKEN not set - cannot continue", exc_info=False)
```

**When to use**:
- API calls that fail after retries
- Missing required configuration
- Data processing errors

### WARNING - Issues that don't prevent operation
```python
logger.warning(f"{ticker}: API rate limit hit, retrying in {delay}s")
logger.warning("Using yfinance fallback - Tradier token not configured")
logger.warning(f"{ticker}: IV={iv:.1f}% < minimum 60% - skipping")
```

**When to use**:
- Rate limit warnings
- Fallback to alternative data source
- Tickers filtered out by criteria

### INFO - Normal operational messages
```python
logger.info(f"{ticker}: IV={iv:.1f}%, IV Rank={iv_rank:.1f}%, Score={score:.1f}")
logger.info(f"Successfully analyzed {count}/{total} tickers")
logger.info("✅ Startup validation passed")  # Exception: startup uses emoji
```

**When to use**:
- Analysis progress
- Summary statistics
- Configuration loaded
- Major workflow steps

### DEBUG - Detailed debugging (rarely used)
```python
logger.debug(f"Cache lookup: {ticker} -> {'hit' if cached else 'miss'}")
logger.debug(f"IV calculation: HV={hv:.2f}, RV={rv:.2f}")
```

**When to use**:
- Development/debugging only
- Should NOT appear in production logs
- Remove debug statements from hot loops

---

## Examples

### Ticker Analysis
```python
# Start
logger.info(f"{ticker}: Starting analysis (IV={iv:.1f}%, Score={score:.1f})")

# Warning (still processing)
logger.warning(f"{ticker}: Low liquidity (volume={volume}, OI={oi})")

# Error (can't continue with this ticker)
logger.error(f"{ticker}: Failed to fetch options data: {error}", exc_info=True)

# Success
logger.info(f"{ticker}: Analysis complete (Sentiment={sentiment}, Strategies={len(strategies)})")
```

### API Calls
```python
# Before
logger.debug(f"{ticker}: Fetching options data from Tradier...")

# Rate limit
logger.warning(f"{ticker}: Rate limited, retrying in {delay}s (attempt {attempt}/{max_attempts})")

# Error
try:
    data = api.get_data(ticker)
except requests.Timeout as e:
    logger.error(f"{ticker}: API timeout after {timeout}s: {e}", exc_info=True)
    raise
except Exception as e:
    logger.error(f"{ticker}: Unexpected API error: {e}", exc_info=True)
    raise
```

### System Operations
```python
# Initialization
logger.info("Initializing Earnings Analyzer...")
logger.info("Using Tradier API for real IV data")

# Configuration
logger.info(f"Loaded config: IV threshold={min_iv}%, Max analyze={max_analyze}")
logger.warning("ALPHA_VANTAGE_API_KEY not set - will use Nasdaq calendar")

# Cleanup
logger.info(f"Cleaned up {count} old reports (>15 days)")
```

---

## Migration Checklist

### For Each File:

1. **Remove emojis from logger calls**
   - Find: `logger.(info|warning|error)\(.*[✅❌📊💡🎯🔍]`
   - Keep emojis in `print()` statements for CLI

2. **Add ticker prefix to ticker-specific logs**
   - Before: `logger.info(f"IV={iv}%")`
   - After: `logger.info(f"{ticker}: IV={iv:.1f}%")`

3. **Add exc_info=True to error logs**
   - Find: `logger.error\([^)]*\)(?!.*exc_info)`
   - Add: `, exc_info=True`

4. **Remove debug logs from hot paths**
   - Remove: Cache hit/miss logs in loops
   - Remove: Per-ticker data fetching logs
   - Keep: Summary statistics

5. **Standardize warning messages**
   - Include context (ticker, values, thresholds)
   - Explain impact (e.g., "retrying", "skipping", "using fallback")

---

## Priority Files to Update

### High Priority (Most Log Output):
1. `src/analysis/ticker_filter.py` - Heavy debug spam
2. `src/analysis/earnings_analyzer.py` - Main workflow
3. `src/options/tradier_client.py` - API calls

### Medium Priority:
4. `src/ai/sentiment_analyzer.py`
5. `src/data/calendars/alpha_vantage.py`

### Low Priority:
7. All other `src/` files

---

## Example Pull Request Changes

### Before:
```python
logger.debug(f"Checking cache for {ticker}")
if ticker in self._info_cache:
    logger.debug(f"✅ Cache hit for {ticker}")
    return self._info_cache[ticker]
logger.debug(f"❌ Cache miss for {ticker}")
return self._fetch_from_api(ticker)
```

### After:
```python
# Cache lookup (no debug spam)
if ticker in self._info_cache:
    return self._info_cache[ticker]
return self._fetch_from_api(ticker)

# Only log cache stats at summary level
cache_hit_rate = hits / total if total > 0 else 0
logger.info(f"Cache hit rate: {cache_hit_rate:.1%} ({hits}/{total})")
```

---

## Testing

After migration, verify:
1. ✅ No emojis in log files (only in console output)
2. ✅ All error logs include `exc_info=True`
3. ✅ Ticker-specific logs have ticker prefix
4. ✅ Log volume reduced (fewer debug statements)
5. ✅ Logs are still informative and useful

---

**Last Updated**: November 9, 2025
