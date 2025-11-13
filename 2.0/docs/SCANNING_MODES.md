# Scanning and Ticker Modes - User Guide

## Overview

The 2.0 system now supports two powerful modes for analyzing IV Crush opportunities:

1. **Scanning Mode**: Automatically scan all earnings for a specific date
2. **Ticker Mode**: Analyze specific tickers from command line without CSV files

Both modes automatically fetch earnings dates and calculate optimal expiration dates based on earnings timing.

---

## ⚠️ Important Rate Limit Considerations

### Alpha Vantage Free Tier Limits

The system uses Alpha Vantage API for earnings calendar data:
- **5 API calls per minute**
- **500 API calls per day**

### Impact on Different Modes

**Scanning Mode (✅ More Efficient)**:
- Uses **1 API call total** to fetch all earnings for a date
- Then analyzes each ticker (uses Tradier API, not Alpha Vantage)
- **Recommended** for daily routines and discovering opportunities

**Ticker Mode (⚠️ Uses More API Calls)**:
- Uses **1 API call per ticker** to fetch earnings dates
- Analyzing 10 tickers = 10 API calls to Alpha Vantage
- **Automatic rate limiting**: System pauses 60 seconds after every 5 tickers
- Still useful for watchlist analysis, just be aware of limits

### Example API Usage

```bash
# Scanning mode - Only 1 Alpha Vantage API call
python scripts/scan.py --scan-date 2025-01-31
# → Analyzes 15 tickers with earnings on that date
# → Alpha Vantage calls: 1

# Ticker mode - 1 API call per ticker
python scripts/scan.py --tickers AAPL,MSFT,GOOGL,AMZN,META
# → Analyzes 5 tickers
# → Alpha Vantage calls: 5
# → No rate limit pause needed

# Ticker mode with 10 tickers - automatic rate limiting
python scripts/scan.py --tickers AAPL,MSFT,GOOGL,AMZN,META,NVDA,TSLA,NFLX,AMD,INTC
# → Analyzes 10 tickers
# → Alpha Vantage calls: 10
# → Automatic 60-second pause after 5th ticker
```

### Best Practices

1. **Prefer Scanning Mode** when discovering opportunities for upcoming dates
2. **Use Ticker Mode** sparingly for your watchlist
3. **Batch your analyses** - analyze multiple tickers at once rather than running script multiple times
4. **Consider upgrading** to Alpha Vantage paid tier if analyzing many tickers daily

---

## Prerequisites

### Required API Keys

Both modes require API keys configured in `.env`:

```bash
# Required for options data and price quotes
TRADIER_API_KEY=your_tradier_key_here

# Required for earnings calendar
ALPHA_VANTAGE_API_KEY=your_alphavantage_key_here

# Optional - system configuration
DATABASE_PATH=./data/ivcrush.db
LOG_LEVEL=INFO
```

### Historical Data

For accurate VRP (Volatility Risk Premium) calculations, backfill historical earnings data:

```bash
# Backfill single ticker
python scripts/backfill.py AAPL

# Backfill multiple tickers
python scripts/backfill.py --tickers AAPL,MSFT,GOOGL
```

---

## Mode 1: Scanning Mode

### What It Does

Scanning mode automatically:
1. Fetches all earnings events for a specific date from Alpha Vantage
2. Calculates optimal expiration dates based on earnings timing (BMO/AMC)
3. Analyzes each ticker for IV Crush opportunities
4. Ranks opportunities by VRP ratio and provides trade recommendations

### Usage

```bash
# Scan earnings for a specific date
python scripts/scan.py --scan-date 2025-01-31

# Scan with debug logging
python scripts/scan.py --scan-date 2025-02-15 --log-level DEBUG

# Scan with custom expiration offset (override auto-calculation)
python scripts/scan.py --scan-date 2025-01-31 --expiration-offset 2
```

### Example Output

```
================================================================================
SCANNING MODE: Earnings Date Scan
================================================================================
Scan Date: 2025-01-31

Fetching earnings calendar for 2025-01-31...
Fetched 247 total earnings events
Found 12 earnings on 2025-01-31

AAPL: Earnings AMC
Calculated expiration: 2025-02-01

================================================================================
Analyzing AAPL
================================================================================
Earnings Date: 2025-01-31
Expiration: 2025-02-01

📊 Calculating Implied Move...
✓ Implied Move: 4.25%
  Stock Price: $185.50
  ATM Strike: $185.00
  Straddle Cost: $7.88

📊 Fetching Historical Moves...
✓ Found 12 historical moves

📊 Calculating VRP...
✓ VRP Ratio: 1.85x
  Implied Move: 4.25%
  Historical Mean: 2.30%
  Edge Score: 85.00
  Recommendation: GOOD

✅ TRADEABLE OPPORTUNITY

================================================================================
Scan Complete
================================================================================
Total Earnings: 12
✓ Analyzed: 10
⏭️  Skipped: 2 (no historical data)
✗ Errors: 0

🎯 3 TRADEABLE OPPORTUNITIES:
  AAPL  : VRP 1.85x, Edge 85.00, GOOD
  MSFT  : VRP 2.10x, Edge 92.50, EXCELLENT
  GOOGL : VRP 1.52x, Edge 76.00, GOOD
```

### When to Use Scanning Mode

- **Weekly Planning**: Scan upcoming earnings week by week
- **Opportunity Discovery**: Find all tradeable opportunities for a specific date
- **Market-Wide Analysis**: See overall IV pricing across all earnings
- **Automated Workflows**: Schedule daily scans for upcoming earnings

---

## Mode 2: Ticker Mode

### What It Does

Ticker mode automatically:
1. Fetches earnings dates for specified tickers from Alpha Vantage
2. Calculates optimal expiration dates based on earnings timing
3. Analyzes each ticker for IV Crush opportunities
4. Provides detailed trade recommendations

### Usage

```bash
# Analyze specific tickers (comma-separated, no spaces)
python scripts/scan.py --tickers AAPL,MSFT,GOOGL

# Single ticker
python scripts/scan.py --tickers AAPL

# With debug logging
python scripts/scan.py --tickers AAPL,MSFT --log-level DEBUG

# Custom expiration offset (days from earnings)
python scripts/scan.py --tickers AAPL --expiration-offset 1
```

### Example Output

```
================================================================================
TICKER MODE: Command Line Tickers
================================================================================
Tickers: AAPL, MSFT, GOOGL

================================================================================
Processing AAPL
================================================================================
AAPL: Earnings on 2025-01-31 (AMC)
Calculated expiration: 2025-02-01

📊 Calculating Implied Move...
✓ Implied Move: 4.25%
  Stock Price: $185.50
  ATM Strike: $185.00
  Straddle Cost: $7.88

📊 Fetching Historical Moves...
✓ Found 12 historical moves

📊 Calculating VRP...
✓ VRP Ratio: 1.85x
  Implied Move: 4.25%
  Historical Mean: 2.30%
  Edge Score: 85.00
  Recommendation: GOOD

✅ TRADEABLE OPPORTUNITY

================================================================================
Ticker Analysis Complete
================================================================================
Total Tickers: 3
✓ Analyzed: 3
⏭️  Skipped: 0
✗ Errors: 0

🎯 2 TRADEABLE OPPORTUNITIES:
  MSFT  : VRP 2.10x, Edge 92.50, EXCELLENT
  AAPL  : VRP 1.85x, Edge 85.00, GOOD
```

### When to Use Ticker Mode

- **Watchlist Analysis**: Analyze your personal watchlist of tickers
- **Quick Checks**: Verify if specific stocks have tradeable setups
- **Follow-Up Analysis**: Re-analyze tickers after new data
- **Manual Selection**: Analyze hand-picked opportunities

---

## Expiration Date Calculation

Both modes automatically calculate optimal expiration dates based on earnings timing:

### Automatic Calculation

| Earnings Timing | Expiration Strategy | Example |
|-----------------|---------------------|---------|
| **BMO (Before Market Open)** | Same day if Friday, else next Friday | Earnings: Thu → Exp: Fri |
| **AMC (After Market Close)** | Next day if Thursday, else next Friday | Earnings: Thu → Exp: Fri |
| **UNKNOWN** | Next Friday (conservative) | Earnings: Tue → Exp: Fri |

### Custom Offset

Override automatic calculation with `--expiration-offset`:

```bash
# 1 day after earnings (1DTE)
python scripts/scan.py --tickers AAPL --expiration-offset 1

# 2 days after earnings (2DTE)
python scripts/scan.py --scan-date 2025-01-31 --expiration-offset 2

# Same day (0DTE)
python scripts/scan.py --tickers AAPL --expiration-offset 0
```

**Use Cases:**
- **0DTE**: Maximum time decay, highest risk
- **1DTE**: Balance of decay and safety
- **2-3DTE**: Conservative approach for volatile stocks

### Automatic Weekend Adjustment

The system automatically adjusts expiration dates that fall on weekends:
- **Saturday** → Moved to Monday
- **Sunday** → Moved to Monday

This ensures you never get invalid expiration dates when using custom offsets.

**Example**:
```bash
# Earnings on Friday, offset 1 day = Saturday
python scripts/scan.py --tickers AAPL --expiration-offset 1
# → System automatically adjusts Saturday to Monday
```

### Expiration Date Validation

The system validates all expiration dates before analysis:

✅ **Valid Expiration**:
- Must be today or in the future
- Must be on or after earnings date
- Must be a weekday (Mon-Fri)
- Must be within 30 days of earnings

❌ **Invalid Expiration** (rejected with error):
- In the past
- Before earnings date
- On a weekend (shouldn't happen with auto-adjustment)
- More than 30 days after earnings

**Note**: The system does **not** check for market holidays (e.g., July 4th, Christmas). Always verify expiration dates are valid trading days before placing orders.

---

## Trade Recommendations

### VRP Ratio Thresholds

The system evaluates opportunities based on VRP (Volatility Risk Premium) ratio:

| Recommendation | VRP Ratio | Interpretation |
|----------------|-----------|----------------|
| **EXCELLENT** | ≥ 2.0x | Implied move is 2x+ historical average |
| **GOOD** | ≥ 1.5x | Implied move is 1.5x+ historical average |
| **MARGINAL** | ≥ 1.2x | Implied move is 1.2x+ historical average |
| **SKIP** | < 1.2x | Insufficient edge for IV Crush trade |

### Edge Score

The edge score (0-100) indicates trade quality:
- **90-100**: Exceptional opportunity
- **80-89**: Strong opportunity
- **70-79**: Good opportunity
- **60-69**: Marginal opportunity
- **< 60**: Skip

### Tradeable vs Skip

A ticker is marked **tradeable** if:
- VRP Ratio ≥ 1.2x
- Historical data available (12+ quarters)
- Options are liquid enough for entry/exit

---

## Workflow Examples

### Daily Morning Routine

```bash
# 1. Scan today's earnings
python scripts/scan.py --scan-date 2025-01-31

# 2. Review tradeable opportunities
# 3. Backfill any tickers missing historical data
python scripts/backfill.py TICKER

# 4. Re-analyze after backfill
python scripts/scan.py --tickers TICKER
```

### Weekly Planning

```bash
# Scan Monday through Friday
python scripts/scan.py --scan-date 2025-01-27  # Monday
python scripts/scan.py --scan-date 2025-01-28  # Tuesday
python scripts/scan.py --scan-date 2025-01-29  # Wednesday
python scripts/scan.py --scan-date 2025-01-30  # Thursday
python scripts/scan.py --scan-date 2025-01-31  # Friday

# Review all results and prioritize by VRP ratio
```

### Watchlist Monitoring

```bash
# Create watchlist file (optional)
echo "AAPL
MSFT
GOOGL
AMZN
META" > my_watchlist.txt

# Analyze entire watchlist
python scripts/scan.py --tickers $(cat my_watchlist.txt | tr '\n' ',')
```

---

## Comparison with Batch Mode

The new `scan.py` modes vs the existing `analyze_batch.py`:

| Feature | scan.py (New) | analyze_batch.py (Old) |
|---------|---------------|------------------------|
| **Earnings Date Fetching** | ✅ Automatic | ❌ Manual CSV required |
| **Expiration Calculation** | ✅ Automatic | ❌ Manual CSV required |
| **Scanning by Date** | ✅ Yes | ❌ No |
| **Command Line Tickers** | ✅ Yes (no CSV) | ⚠️ Yes (but needs CSV) |
| **Bulk Analysis** | ✅ Yes | ✅ Yes |
| **Custom Expiration** | ✅ Offset days | ✅ Exact dates in CSV |

### When to Use Each

**Use `scan.py`:**
- Quick analysis without preparing CSV files
- Scanning all earnings for a date
- Auto-fetch earnings dates from API
- Ad-hoc ticker analysis

**Use `analyze_batch.py`:**
- Custom earnings dates (e.g., historical analysis)
- Precise control over expiration dates
- Pre-prepared ticker lists with dates
- Reproducible batch jobs

---

## Troubleshooting

### "No earnings found for this date"

**Cause**: No companies report earnings on the specified date.

**Solution**: Try a different date or check Alpha Vantage API status.

### "No upcoming earnings found for TICKER"

**Cause**: Ticker has no earnings in next 3 months, or ticker symbol is invalid.

**Solutions**:
- Verify ticker symbol
- Check if earnings are further out (>3 months)
- Use `analyze_batch.py` with custom dates for historical analysis

### "No historical data"

**Cause**: Ticker hasn't been backfilled yet.

**Solution**:
```bash
# Backfill the ticker
python scripts/backfill.py TICKER

# Re-analyze
python scripts/scan.py --tickers TICKER
```

### "Alpha Vantage rate limit exceeded"

**Cause**: Free tier allows 5 calls/minute, 500 calls/day.

**Solutions**:
- **Use scanning mode instead**: Only 1 API call vs 1 per ticker
- **System handles rate limiting automatically** in ticker mode (pauses after 5 calls)
- Wait 1 minute between manual scans
- Upgrade to paid Alpha Vantage plan
- Use `analyze_batch.py` with pre-prepared CSV (no API calls)

**Note**: The system now automatically pauses for 60 seconds after every 5 API calls in ticker mode, so you should rarely see this error.

### "Failed to calculate implied move"

**Cause**: Options chain unavailable or market closed.

**Solutions**:
- Run during market hours (9:30 AM - 4:00 PM ET)
- Check if options are listed for the ticker
- Verify expiration date is valid

### "Invalid expiration date" errors

**New in this version**: The system validates expiration dates before analysis.

**Common validation errors**:

1. **"Expiration date is in the past"**
   - Cause: Trying to analyze historical earnings
   - Solution: Use current or future earnings dates

2. **"Expiration before earnings"**
   - Cause: Custom offset is negative or logic error
   - Solution: Check your `--expiration-offset` value

3. **"Expiration is on weekend"**
   - Cause: Programming error (should auto-adjust)
   - Solution: Report as bug

4. **"Expiration is X days after earnings (> 30 days)"**
   - Cause: Custom offset too large
   - Solution: Use reasonable offset (0-7 days typical)

---

## Advanced Usage

### Automated Scanning Script

Create a shell script for daily scanning:

```bash
#!/bin/bash
# daily_scan.sh

DATE=$(date +%Y-%m-%d)
LOG_DIR="./logs/scans"
mkdir -p "$LOG_DIR"

echo "Scanning earnings for $DATE..."
python scripts/scan.py --scan-date "$DATE" > "$LOG_DIR/scan_$DATE.log" 2>&1

# Email results (optional)
# mail -s "IV Crush Scan $DATE" your@email.com < "$LOG_DIR/scan_$DATE.log"
```

### Cron Job Setup

```bash
# Edit crontab
crontab -e

# Add daily scan at 8:00 AM
0 8 * * * cd /path/to/trading-desk/2.0 && ./daily_scan.sh
```

### JSON Output (Future Enhancement)

For programmatic processing, consider adding `--output json` flag:

```bash
# Future enhancement
python scripts/scan.py --scan-date 2025-01-31 --output json > results.json
```

---

## Summary

### Scanning Mode
```bash
python scripts/scan.py --scan-date YYYY-MM-DD
```
- Scans all earnings for a specific date
- Auto-fetches earnings calendar
- Auto-calculates expirations
- Perfect for daily routines

### Ticker Mode
```bash
python scripts/scan.py --tickers AAPL,MSFT,GOOGL
```
- Analyzes specific tickers
- Auto-fetches earnings dates
- Auto-calculates expirations
- Perfect for watchlist analysis

Both modes provide:
- ✅ Automatic earnings date fetching
- ✅ Intelligent expiration calculation
- ✅ VRP-based recommendations
- ✅ Trade quality scoring
- ✅ No CSV files required

Happy trading! 📈
