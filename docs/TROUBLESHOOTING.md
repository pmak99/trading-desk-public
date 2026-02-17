# Troubleshooting Guide

**Purpose**: Quick solutions for common issues with Trading Desk Earnings Analyzer

---

## Common Errors

### ❌ "TRADIER_ACCESS_TOKEN not set"

**Error Message**:
```
ERROR - TRADIER_ACCESS_TOKEN not set - required for real IV data
   Get token at: https://dash.tradier.com/settings/api
```

**Cause**: Missing Tradier API key in environment

**Fix**:
1. Create a free Tradier account: https://dash.tradier.com/signup
2. Navigate to: https://dash.tradier.com/settings/api
3. Generate an Access Token
4. Add to `.env` file:
   ```
   TRADIER_ACCESS_TOKEN=your_token_here
   ```
5. Restart the application

**Alternative**: System will fall back to yfinance (less accurate IV data)

---

### ❌ "Daily limit reached (60/60 calls)"

**Error Message**:
```
ERROR - Daily API limit reached (60/60 calls used today)
```

**Cause**: Hit the daily API call budget limit

**Fix**:

**Option 1 - Wait (Recommended)**:
- Limits reset at midnight EST
- Check current usage: See logs for call counts

**Option 2 - Use Override Flag**:
```bash
python -m src.analysis.earnings_analyzer --tickers "NVDA" 2025-11-08 --override --yes
```
⚠️  Still respects monthly hard caps

**Option 3 - Adjust Limits**:
Edit `config/budget.yaml`:
```yaml
daily_limits:
  sentiment: 50  # Increase from 40
  strategy: 50   # Increase from 40
```

---

### ❌ "Invalid ticker format"

**Error Messages**:
```
ERROR - Invalid ticker format: '123'. Tickers must contain only letters
ERROR - Invalid ticker format: 'TOOLONG'. Tickers must be 1-5 characters
```

**Cause**: Ticker doesn't match expected format (1-5 uppercase letters, optionally followed by a dot and 1-2 uppercase letters)

**Valid Examples**: AAPL, MSFT, GOOGL, META, NVDA, BRK.B, BF.A

**Invalid Examples**: 123, AAPL Inc, A-B, TOOLONGTICKER

**Fix**:
```bash
# ❌ Wrong
python -m src.analysis.earnings_analyzer --tickers "123,AAPL-B"

# ✅ Correct
python -m src.analysis.earnings_analyzer --tickers "AAPL,MSFT" 2025-11-08 --yes
```

---

### ❌ "Invalid date format"

**Error Message**:
```
ERROR - Invalid date format: '11/08/2025'. Expected format: YYYY-MM-DD
```

**Cause**: Date not in YYYY-MM-DD format

**Fix**:
```bash
# ❌ Wrong
python -m src.analysis.earnings_analyzer --tickers "NVDA" 11/08/2025

# ✅ Correct
python -m src.analysis.earnings_analyzer --tickers "NVDA" 2025-11-08 --yes
```

---

### ❌ "No options data found for TICKER"

**Error Message**:
```
WARNING - NVDA: No options data found
```

**Possible Causes**:
1. Ticker doesn't have weekly options
2. Insufficient options liquidity
3. Earnings date too far in future (>60 days)
4. API timeout or rate limit

**Fix**:

**Check if ticker has options**:
- Visit: https://www.tradier.com/markets/options
- Search for your ticker
- Verify weekly options exist

**Try alternative ticker**:
- Use high-liquidity stocks (AAPL, MSFT, NVDA, etc.)
- Avoid small-cap stocks with thin options markets

**Check earnings date**:
```bash
# If date is too far out, options chains may not be available yet
python -m src.analysis.earnings_analyzer --tickers "NVDA" 2025-11-15 --yes
```

---

### ❌ "Rate limit exceeded"

**Error Message**:
```
WARNING - Rate limited, retrying in 2.0s... (attempt 2/4)
ERROR - Failed after 4 attempts: 429 Too Many Requests
```

**Cause**: API rate limits hit (yfinance, Tradier, or AI APIs)

**Fix**:

**Short-term**:
- Script automatically retries with exponential backoff
- Wait 1-2 minutes between runs
- Use `--override` flag carefully

**Long-term**:
```python
# Adjust rate limiting in code
RATE_LIMIT_DELAY_SECONDS = 0.5  # Increase to 1.0 or 2.0
```

**For Tradier**:
- Free tier: 120 requests/minute
- Upgrade for higher limits

**For Perplexity AI**:
- Check monthly usage: config/budget.yaml
- Falls back to free Gemini automatically

---

### ❌ "Circuit breaker OPEN"

**Error Message**:
```
ERROR - Circuit breaker OPEN for fetch_data. Retry in 45s. (5 consecutive failures)
```

**Cause**: API experiencing persistent failures (5+ consecutive errors)

**What it means**:
- Circuit breaker protecting against cascading failures
- API calls are blocked temporarily
- Prevents wasting time on failing service

**Fix**:

**Wait for auto-recovery**:
- Circuit breaker will retry after timeout (default: 60s)
- Enters "half-open" state to test if service recovered
- Auto-closes if service is back

**Manual reset**:
```python
# If you know the service is back
from src.core.retry_utils import CircuitBreaker
breaker.reset()
```

**Check service status**:
- Tradier: https://status.tradier.com/
- yfinance: Try accessing Yahoo Finance directly
- Perplexity: https://status.perplexity.ai/

---

### ❌ "No module named 'X'"

**Error Messages**:
```
ModuleNotFoundError: No module named 'yfinance'
ModuleNotFoundError: No module named 'psutil'
ModuleNotFoundError: No module named 'pytz'
```

**Cause**: Missing Python dependencies

**Fix**:

**Using virtual environment** (recommended):
```bash
source venv/bin/activate
pip install -r requirements.txt
```

**System-wide** (if no venv):
```bash
pip3 install --user yfinance psutil pytz requests pyyaml
```

**Verify installation**:
```bash
python3 -c "import yfinance, psutil, pytz; print('All imports successful')"
```

---

##  Configuration Issues

### ❌ "Config file not found"

**Error Message**:
```
ERROR - Config file not found: config/budget.yaml
ERROR - Config file not found: config/trading_criteria.yaml
```

**Cause**: Missing or misplaced configuration files

**Fix**:

**Check file exists**:
```bash
ls -la config/
```

**Verify you're in project root**:
```bash
pwd
# Should show: /path/to/Trading Desk
```

**Create from template** (if missing):
```bash
cp config/budget.yaml.example config/budget.yaml
cp config/trading_criteria.yaml.example config/trading_criteria.yaml
```

---

### ❌ "Invalid YAML syntax"

**Error Message**:
```
ERROR - budget.yaml: Invalid YAML syntax
   found undefined tag handle 'tag:yaml.org,2002:python/object'
```

**Cause**: Syntax error in YAML config file

**Fix**:

**Validate YAML**:
```bash
python3 -c "import yaml; yaml.safe_load(open('config/budget.yaml'))"
```

**Common mistakes**:
```yaml
# ❌ Wrong - inconsistent indentation
models:
  sonar-pro:
  input_cost_per_1k: 0.003

# ✅ Correct
models:
  sonar-pro:
    input_cost_per_1k: 0.003
    output_cost_per_1k: 0.015
    per_request_fee: 0.005
```

**Use online validator**: https://www.yamllint.com/

---

## Performance Issues

### ⚠️ "Analysis taking too long"

**Symptoms**:
- Analysis stuck for >5 minutes
- High CPU usage
- Memory increasing

**Causes & Fixes**:

**Too many tickers**:
```bash
# ❌ Analyzing 100 tickers (will take 30+ minutes)
python -m src.analysis.earnings_analyzer 2025-11-08 100

# ✅ Start with fewer tickers
python -m src.analysis.earnings_analyzer 2025-11-08 5
```

**Network issues**:
- Check internet connection
- API services may be slow
- Circuit breaker will prevent wasting time

**Multiprocessing overhead**:
- <3 tickers: Sequential (fast)
- ≥3 tickers: Parallel (faster for large batches)

---

### ⚠️ "Memory usage growing"

**Symptoms**:
- Process using >2GB RAM
- System slowing down

**Fixes**:

**LRU cache is bounded** (shouldn't grow indefinitely):
```python
# Default cache sizes (already optimized)
_info_cache: LRUCache(max_size=200, ttl_minutes=60)
_history_cache: LRUCache(max_size=100, ttl_minutes=60)
```

**Reduce analysis count**:
```bash
# Instead of 75 tickers at once
python -m src.analysis.earnings_analyzer 2025-11-08 75

# Break into smaller batches
python -m src.analysis.earnings_analyzer --tickers "NVDA,META,GOOGL" 2025-11-08 --yes
```

**Monitor memory**:
```bash
# Use profiling tools
python benchmarks/performance_tracker.py --tickers "AAPL,MSFT" --baseline
```

---

## API Key Verification

### How to verify API keys are working

**Tradier**:
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "https://api.tradier.com/v1/markets/quotes?symbols=AAPL"

# Should return JSON with AAPL quote data
```

**Perplexity**:
```bash
curl -X POST "https://api.perplexity.ai/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama-3.1-sonar-small-128k-online","messages":[{"role":"user","content":"test"}]}'

# Should return JSON response
```

**Google Gemini**:
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent?key=YOUR_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"contents":[{"parts":[{"text":"test"}]}]}'

# Should return JSON response
```

---

## Getting Help

### Logs

**Check log output**:
- Logs are printed to console
- Look for ERROR and WARNING messages
- Recent commits include line numbers for errors

**Enable debug logging** (if needed):
```python
# In earnings_analyzer.py
logging.basicConfig(
    level=logging.DEBUG,  # Change from INFO
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

---

### Profiling Performance

**If analysis is slow, profile it**:
```bash
# Create baseline
python benchmarks/performance_tracker.py \
  --tickers "AAPL,MSFT,GOOGL" \
  --date 2025-11-08 \
  --baseline

# Profile code
python -m cProfile -o profiling/results/test.prof \
  -m src.analysis.earnings_analyzer \
  --tickers "AAPL,MSFT,GOOGL" 2025-11-08 --yes

# Analyze hotspots
python profiling/profiler.py --hotspots profiling/results/test.prof
```

See `PROFILING_GUIDE.md` for details.

---

### Still Stuck?

1. **Check README.md** for setup instructions
2. **Review IMPROVEMENT_PLAN.md** for known issues
3. **Check recent commits** for bug fixes
4. **Create GitHub issue** with:
   - Error message (full text)
   - Command you ran
   - Python version (`python3 --version`)
   - OS (`uname -a`)

---

**Last Updated**: November 9, 2025
