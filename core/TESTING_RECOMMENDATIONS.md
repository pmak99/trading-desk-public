# Testing Recommendations - IV Crush 2.0

## Quick Reference

**Before Each Trading Week**: Run the Weekly Checklist (see below)
**Before Going Live**: Complete the Pre-Production Checklist
**After Each Trade**: Update the Trade Log

---

## Weekly Testing Checklist

Run this every Sunday evening before the trading week:

### 1. Environment Check (2 minutes)
```bash
# Activate environment
cd "$PROJECT_ROOT/2.0"
source venv/bin/activate

# Verify .env is loaded
python -c "from src.config.config import Config; c = Config.from_env(); print(f'✓ Config loaded: Tradier={c.api.tradier_api_key[:10]}...')"
```

**Expected**: ✓ Config loaded successfully

---

### 2. API Health Check (1 minute)
```bash
# Test Tradier API
python -c "from src.container import get_container; c = get_container(); result = c.tradier.get_stock_price('SPY'); print(f'✓ Tradier API: SPY = ${result.value.amount}')"
```

**Expected**: ✓ Tradier API returns SPY price

---

### 3. Database Health Check (1 minute)
```bash
# Check database size and recent data
sqlite3 data/ivcrush.db "SELECT COUNT(*) as total_moves FROM historical_moves;"
sqlite3 data/ivcrush.db "SELECT ticker, MAX(earnings_date) as latest FROM historical_moves GROUP BY ticker ORDER BY latest DESC LIMIT 5;"
```

**Expected**:
- Total moves > 0
- Latest earnings dates within last 6 months

---

### 4. Test Next Week's Tickers (5 minutes)

Create earnings calendar CSV for next week:
```csv
ticker,earnings_date,expiration_date
TICKER1,2025-11-XX,2025-11-YY
TICKER2,2025-11-XX,2025-11-YY
```

Run analysis:
```bash
python scripts/analyze_batch.py \
  --earnings-file data/next_week_earnings.csv \
  --tickers TICKER1,TICKER2 \
  --continue-on-error
```

**Expected**:
- No crashes
- All tickers analyzed or appropriately skipped
- Tradeable opportunities identified

---

### 5. Validate Historical Data (2 minutes)
```bash
# For each ticker in next week's calendar, verify historical data exists
sqlite3 data/ivcrush.db << EOF
SELECT ticker, COUNT(*) as quarters
FROM historical_moves
WHERE ticker IN ('TICKER1', 'TICKER2')
GROUP BY ticker;
EOF
```

**Expected**: Each ticker has >= 4 quarters (or MIN_HISTORICAL_QUARTERS from .env)

---

## Pre-Production Checklist

Complete this before first live trade:

### Setup
- [ ] Virtual environment created (`venv/`)
- [ ] Dependencies installed (numpy, requests, python-dotenv)
- [ ] `.env` file configured with production API keys
- [ ] `.gitignore` updated to exclude `.env`
- [ ] Database initialized with schema
- [ ] Historical data backfilled (12+ quarters)

### Configuration Validation
- [ ] `MIN_HISTORICAL_QUARTERS=4` (production value)
- [ ] `USE_INTERPOLATED_MOVE=true` (more accurate)
- [ ] Tradier production API key (not sandbox)
- [ ] Database path is absolute and correct

### Testing
- [ ] Ran analyze_batch.py with 10+ tickers successfully
- [ ] Verified VRP calculations manually for 3 sample tickers
- [ ] Tested error handling (invalid ticker, no data, expired options)
- [ ] Confirmed .env loading works without manual exports

### Monitoring Setup
- [ ] Created trade log spreadsheet
- [ ] Set up calendar alerts for earnings dates
- [ ] Documented incident response plan
- [ ] Created backup of database

---

## Regression Testing

Run these tests after any code changes:

### Unit Tests
```bash
# Run existing unit tests
pytest tests/unit/test_scorer.py -v

# Add more unit tests as needed
pytest tests/unit/ -v --cov=src --cov-report=html
```

**Expected**: All tests pass, coverage > 80%

---

### Integration Tests

#### Test 1: End-to-End Analysis
```bash
# Pick a ticker with known good data (e.g., AAPL, MSFT)
python scripts/analyze.py AAPL 2025-12-20
```

**Expected**:
- ✓ Implied move calculated
- ✓ Historical moves retrieved
- ✓ VRP calculated
- ✓ Recommendation provided

#### Test 2: Batch Processing
```bash
# Test with 5-10 tickers
python scripts/analyze_batch.py \
  --tickers AAPL,MSFT,GOOGL,META,NVDA \
  --earnings-file data/test_earnings_calendar.csv \
  --continue-on-error
```

**Expected**:
- All tickers processed
- No crashes
- Summary shows correct counts

#### Test 3: Error Handling
```bash
# Test with invalid ticker
python scripts/analyze.py INVALID_TICKER 2025-12-20
```

**Expected**:
- Graceful error message
- No crash
- Exit code 1

---

## Data Quality Checks

### Monthly Review (First Sunday of Month)

#### 1. Historical Data Freshness
```bash
sqlite3 data/ivcrush.db << EOF
SELECT
    ticker,
    MAX(earnings_date) as latest_earnings,
    COUNT(*) as total_quarters
FROM historical_moves
GROUP BY ticker
HAVING latest_earnings < DATE('now', '-6 months')
ORDER BY latest_earnings;
EOF
```

**Action**: Backfill any tickers with stale data (> 6 months old)

---

#### 2. Data Sanity Checks
```bash
sqlite3 data/ivcrush.db << EOF
-- Check for outliers (moves > 30% are suspicious)
SELECT ticker, earnings_date, intraday_move_pct
FROM historical_moves
WHERE intraday_move_pct > 30.0
ORDER BY intraday_move_pct DESC;

-- Check for missing data
SELECT ticker, COUNT(*) as quarters
FROM historical_moves
GROUP BY ticker
HAVING quarters < 4
ORDER BY quarters;
EOF
```

**Action**: Investigate and fix any anomalies

---

#### 3. Cache Performance
```bash
python -c "
from src.container import get_container
c = get_container()
stats = c.get_cache_stats()
print(f'Cache Stats: {stats}')
"
```

**Expected**: Hit rate > 50% during active use

---

## Performance Testing

### Load Test - High Volume Week

Simulate a week with 50+ earnings:

```bash
# Create test file with 50 tickers
python scripts/analyze_batch.py \
  --file data/heavy_week_tickers.txt \
  --earnings-file data/heavy_week_calendar.csv \
  --continue-on-error \
  --log-level WARNING
```

**Monitor**:
- Total execution time (should be < 2 minutes for 50 tickers)
- API rate limit warnings (should be none)
- Memory usage (should stay < 500MB)
- Error rate (should be < 5%)

---

## Troubleshooting Guide

### Issue: "NODATA: No price data for TICKER"

**Possible Causes**:
1. Invalid ticker symbol
2. Tradier API down
3. Rate limit exceeded

**Debug Steps**:
```bash
# Test Tradier directly
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://api.tradier.com/v1/markets/quotes?symbols=TICKER"

# Check rate limits
python -c "from src.container import get_container; print(get_container().tradier)"
```

---

### Issue: "NODATA: Need 4+ quarters, got X"

**Cause**: Insufficient historical data

**Fix**:
```bash
# Backfill historical data
python scripts/backfill.py TICKER

# Or temporarily lower threshold for testing
export MIN_HISTORICAL_QUARTERS=2
```

---

### Issue: "Configuration error: TRADIER_API_KEY is required"

**Cause**: .env file not loaded

**Fix**:
```bash
# Verify .env exists in project root
ls -la .env

# Check .env contains key
grep TRADIER_API_KEY .env

# Test manual load
python -c "from dotenv import load_dotenv; load_dotenv('.env'); import os; print(os.getenv('TRADIER_API_KEY'))"
```

---

### Issue: TypeError with Money/Percentage types

**Cause**: Attempting to use domain types as primitives

**Fix**:
```python
# WRONG
float(money_obj)

# CORRECT
float(money_obj.amount)
```

---

## Test Data Management

### Creating Test Earnings Calendars

**Template** (`data/test_earnings_calendar.csv`):
```csv
ticker,earnings_date,expiration_date
AAPL,2025-11-XX,2025-11-YY
MSFT,2025-11-XX,2025-11-YY
```

**Guidelines**:
- Earnings date = actual earnings release date (BMO or AMC)
- Expiration date = Friday after earnings (weekly options)
- Use realistic dates (not in past, not > 2 months out)

---

### Sample Test Tickers

**Safe tickers for testing** (liquid, reliable data):
- **Tech**: AAPL, MSFT, GOOGL, META, NVDA
- **Finance**: JPM, BAC, GS, WFC
- **Healthcare**: UNH, JNJ, PFE
- **Consumer**: WMT, TGT, HD, MCD

**Avoid for testing**:
- Recently IPO'd stocks (< 2 years)
- Low volume stocks
- Foreign ADRs (inconsistent data)

---

## Continuous Improvement

### After Each Trade
Document in trade log:
- Ticker, earnings date, expiration
- Implied move (predicted)
- Actual move (realized)
- VRP ratio
- P&L result
- Notes/lessons learned

### Monthly Review
Analyze trade log:
- Win rate vs backtested expectations
- Average VRP ratio of winners vs losers
- Identify patterns in misses
- Update configuration if needed

### Quarterly Deep Dive
- Review and update this testing guide
- Add new test cases based on production issues
- Update thresholds based on performance data
- Archive old test data

---

## Automated Testing (Future Enhancement)

### GitHub Actions / CI/CD
```yaml
# .github/workflows/test.yml
name: Test Suite
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
      - run: pip install -r requirements.txt
      - run: pytest tests/ -v
```

### Scheduled Tests
```bash
# crontab -e
# Run health check every Sunday at 6pm
0 18 * * 0 cd /path/to/2.0 && ./scripts/weekly_health_check.sh
```

---

## Contact & Escalation

**For Production Issues**:
1. Check this guide first
2. Review TESTING_SUMMARY.md for known issues
3. Check logs in analysis_log table
4. Create detailed bug report with reproduction steps

**Emergency Contacts**:
- Tradier API Support: https://documentation.tradier.com/
- Database Issues: Restore from backup in data/backups/

---

## Version History

- **v1.0** (2025-11-12): Initial testing recommendations after Phase 4 completion
- **Future**: Add automated testing, monitoring dashboards, alerting

---

## Appendix: Test Data Samples

### Sample .env for Testing
```env
TRADIER_API_KEY=your_key_here
ALPHA_VANTAGE_KEY=your_key_here
DB_PATH=data/ivcrush.db
MIN_HISTORICAL_QUARTERS=2
USE_INTERPOLATED_MOVE=true
```

### Sample Test Output (Expected)
```
================================================================================
Analyzing NVDA
================================================================================
Earnings Date: 2025-11-19
Expiration: 2025-11-21

📊 Calculating Implied Move...
✓ Implied Move: 8.02%
  Stock Price: $193.80
  ATM Strike: $192.50
  Straddle Cost: $15.55

📊 Fetching Historical Moves...
✓ Found 2 historical moves

📊 Calculating VRP...
✓ VRP Ratio: 2.08x
  Implied Move: 8.02%
  Historical Mean: 3.86%
  Edge Score: 1.81
  Recommendation: EXCELLENT

✅ TRADEABLE OPPORTUNITY
```
