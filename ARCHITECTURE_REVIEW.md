# Architecture Review & Improvement Plan

## Executive Summary

**Status**: Phase 2 is functionally complete but has several design issues that should be addressed before production use.

**Priority Issues**:
1. UsageTracker not integrated (HIGH)
2. Duplicate API calls (MEDIUM)
3. IV Rank calculation using wrong metric (HIGH - strategy critical)
4. Tight coupling (LOW)
5. No caching (MEDIUM - cost impact)

---

## Issue 1: UsageTracker Not Integrated (HIGH PRIORITY)

### Problem
- Complete `UsageTracker` exists in `src/usage_tracker.py`
- Budget config exists in `config/budget.yaml`
- **BUT**: No API client actually uses it!
- Result: No cost tracking, no budget enforcement

### Impact
- Could exceed budget without warning
- No visibility into API costs
- No daily limit enforcement

### Fix
All API clients should:
```python
class SentimentAnalyzer:
    def __init__(self):
        self.tracker = UsageTracker()

    def _make_request(self, prompt):
        # Check budget BEFORE call
        can_call, reason = self.tracker.can_make_call('sonar-pro', estimated_tokens=1000)
        if not can_call:
            raise BudgetExceededError(reason)

        # Make API call
        response = requests.post(...)

        # Log usage AFTER call
        tokens = response['usage']['total_tokens']
        cost = (tokens / 1000) * 0.005
        self.tracker.log_api_call('sonar-pro', tokens, cost, ticker, success=True)
```

### Files to Update
- `src/sentiment_analyzer.py`
- `src/strategy_generator.py`
- `src/alpha_vantage_client.py` (if actually calling paid API)

---

## Issue 2: Duplicate API Calls (MEDIUM PRIORITY)

### Problem
```
ticker_filter.get_ticker_data(ticker)
  ├─> yf.Ticker(ticker)  # Call 1
  └─> options_client.get_options_data(ticker)
        └─> yf.Ticker(ticker)  # Call 2 - DUPLICATE!
```

### Impact
- 2x yfinance API calls per ticker
- Slower performance
- Risk of rate limiting

### Fix Option A: Pass yfinance object through
```python
def get_ticker_data(self, ticker: str) -> Optional[Dict]:
    stock = yf.Ticker(ticker)
    info = stock.info
    hist = stock.history(period='1mo')

    # Pass stock object to avoid re-fetching
    if self.options_client:
        options_data = self.options_client.get_options_data_from_stock(stock, ticker)
```

### Fix Option B: Cache in ticker_filter
```python
def get_ticker_data(self, ticker: str) -> Optional[Dict]:
    stock = yf.Ticker(ticker)

    # Get all data in one place
    data = {
        'ticker': ticker,
        'price': ...,
        'volume': ...,
    }

    # Get options data directly here instead of delegating
    options_data = self._get_options_data_direct(stock)
    data['options_data'] = options_data
```

**Recommended**: Option B - simpler, clearer

---

## Issue 3: IV Rank Calculation (HIGH PRIORITY - STRATEGY CRITICAL)

### Problem
Current code (line 148-155 in alpha_vantage_client.py):
```python
hist['rv_30'] = hist['returns'].rolling(window=30).std() * (252 ** 0.5)
current_rv = hist['rv_30'].iloc[-1]
min_rv = hist['rv_30'].min()
max_rv = hist['rv_30'].max()
iv_rank = ((current_rv - min_rv) / (max_rv - min_rv)) * 100
```

**This calculates realized volatility rank, NOT implied volatility rank!**

### Why This Matters
- Your entire strategy depends on IV Rank > 50%
- Realized vol ≠ Implied vol
- Can be 20-50% different around earnings
- **This could filter out good trades or include bad ones**

### Real IV Rank Formula
```
IV Rank = (Current IV - 52w Low IV) / (52w High IV - 52w Low IV) × 100
```

Requires: Historical IV data for past year

### Solutions

**Option A: Keep current approach (least accurate)**
- Acknowledge it's realized vol rank, not IV rank
- Still useful as proxy
- Rename to `rv_rank` for clarity

**Option B: Use current IV from options chain (better)**
```python
# Get current IV from ATM options
current_iv = atm_options['impliedVolatility'].mean()

# Get IV history by pulling options data daily and storing
# Requires: Daily cron job to save IV snapshots
iv_52w_low = historical_iv['iv'].min()
iv_52w_high = historical_iv['iv'].max()
iv_rank = ((current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low)) * 100
```

**Option C: Use paid IV Rank data (most accurate)**
- TastyTrade API
- ThinkorSwim
- CBOE

**Recommended for now**: Option A with clear documentation that it's a proxy

---

## Issue 4: Tight Coupling (LOW PRIORITY)

### Problem
```python
# ticker_filter.py line 46
self.options_client = AlphaVantageClient()
```

Hard-coded dependency makes testing difficult.

### Fix
```python
def __init__(self, options_client=None, usage_tracker=None):
    self.options_client = options_client
    self.usage_tracker = usage_tracker or UsageTracker()
```

Allows:
- Easy mocking for tests
- Swap implementations
- Disable components

---

## Issue 5: No Caching (MEDIUM PRIORITY)

### Problem
- Options data fetched multiple times per ticker
- Sentiment might be re-fetched if script runs multiple times
- Costs add up

### Fix
Simple in-memory cache:
```python
from functools import lru_cache
from datetime import datetime, timedelta

class OptionsDataClient:
    def __init__(self):
        self._cache = {}
        self._cache_ttl = timedelta(hours=1)

    def get_options_data(self, ticker):
        # Check cache
        cache_key = f"{ticker}_{datetime.now().strftime('%Y-%m-%d-%H')}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Fetch and cache
        data = self._fetch_options_data(ticker)
        self._cache[cache_key] = data
        return data
```

---

## Issue 6: Misleading Class Name

### Problem
`AlphaVantageClient` uses yfinance, not Alpha Vantage API

### Fix
Rename to `OptionsDataClient` or `YFinanceOptionsClient`

---

## Issue 7: Missing OpenAI API Key Handling

### Problem
```python
# strategy_generator.py line 30
if not self.api_key:
    logger.warning("OPENAI_API_KEY not found")
    self.api_key = None  # Silently continues
```

Will fail later when trying to generate strategies.

### Fix
```python
if not self.api_key:
    raise ValueError("OPENAI_API_KEY not found in environment")
```

OR keep current behavior but document it's optional.

---

## Redundant Code to Remove

### 1. Alpha Vantage API Code
Lines 43-71 in `alpha_vantage_client.py` - the `_make_request()` method:
- Never actually called
- No Alpha Vantage endpoints used
- **Can delete entirely**

### 2. Duplicate Logging Setup
Each file has:
```python
logger = logging.getLogger(__name__)
```

Should centralize logging config in one place.

---

## Optimization Opportunities

### 1. Batch Processing
```python
# Current: Sequential
for ticker in tickers:
    analyze(ticker)  # 30-60 sec each

# Better: Parallel (if APIs allow)
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(analyze, t) for t in tickers]
    results = [f.result() for f in futures]
```

### 2. Reduce Strategy Generator Prompt Length
Current prompt is ~800 tokens = higher cost

Could reduce to ~400 tokens by being more concise.

### 3. Use Cheaper Models Where Possible
- Sentiment: `sonar-pro` (current) ✓
- Strategy: Use `gpt-4o-mini` instead of `gpt-4o` (5x cheaper)

---

## Recommended Refactor Priority

### Phase 1: Critical Fixes (Do Now)
1. ✅ Integrate UsageTracker into all API clients
2. ✅ Fix duplicate yfinance calls
3. ✅ Document IV Rank is proxy (or implement real IV Rank)

### Phase 2: Important Improvements (Next)
4. ✅ Add caching for options data
5. ✅ Rename AlphaVantageClient → OptionsDataClient
6. ✅ Remove unused Alpha Vantage _make_request code

### Phase 3: Nice to Have (Later)
7. ⬜ Dependency injection for testing
8. ⬜ Parallel processing for multiple tickers
9. ⬜ Optimize prompt lengths

---

## Design Principles Being Followed ✅

1. **Separation of Concerns** - Each component has single responsibility
2. **DRY (mostly)** - Some duplication to fix
3. **Error Handling** - Good coverage
4. **Logging** - Comprehensive
5. **Documentation** - Excellent docstrings
6. **Type Hints** - Used throughout

---

## Final Recommendation

**Architecture is 80% solid.** The main issues are:

1. **Missing cost tracking** (UsageTracker not integrated)
2. **IV Rank using wrong metric** (strategy-critical)
3. **Some inefficiency** (duplicate calls, no caching)

**Suggested approach**:
1. Integrate UsageTracker now (30 min)
2. Fix duplicate yfinance calls (30 min)
3. Document IV Rank limitation in README
4. Add caching in next iteration
5. Consider real IV Rank data if budget allows

**Cost/benefit**: 1 hour of refactoring would improve reliability and reduce costs significantly.

Should I proceed with these fixes?
