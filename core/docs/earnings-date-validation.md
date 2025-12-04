# Earnings Date Cross-Reference System

## Overview

The earnings date validation system cross-references earnings dates from multiple sources to ensure accuracy and flag conflicts. This addresses the issue where Alpha Vantage (the previous single source) sometimes provides incorrect or stale earnings dates.

## Data Sources (Priority Order)

1. **Yahoo Finance** (Highest confidence: 1.0)
   - Most reliable and up-to-date
   - Provides timing (BMO/AMC/DMH) from timestamps
   - Real-time updates

2. **Earnings Whisper** (High confidence: 0.85)
   - Good for near-term most-anticipated earnings
   - Scraped from @eWhispers Twitter account
   - Currently extracts tickers only (date extraction planned)

3. **Alpha Vantage** (Lower confidence: 0.70)
   - Known to have stale or incorrect dates
   - Used as fallback and for conflict detection
   - Primary source before this system

## Components

### 1. Yahoo Finance Earnings Fetcher
**File:** `src/infrastructure/data_sources/yahoo_finance_earnings.py`

```python
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings

fetcher = YahooFinanceEarnings()
result = fetcher.get_next_earnings_date("MRVL")
# Returns: (date(2025, 12, 2), EarningsTiming.AMC)
```

### 2. Earnings Date Validator
**File:** `src/application/services/earnings_date_validator.py`

Cross-references all sources and returns consensus:

```python
from src.application.services.earnings_date_validator import EarningsDateValidator

validator = EarningsDateValidator(
    alpha_vantage=alpha_vantage_client,
    yahoo_finance=yahoo_finance_fetcher
)

result = validator.validate_earnings_date("MRVL")
# Returns: ValidationResult with consensus date and conflict info
```

### 3. Validation Script
**File:** `scripts/validate_earnings_dates.py`

CLI tool to validate and update earnings dates:

```bash
# Validate specific tickers
python scripts/validate_earnings_dates.py MRVL AEO SNOW CRM

# Validate from file
python scripts/validate_earnings_dates.py --file tickers.txt

# Validate whisper week tickers
python scripts/validate_earnings_dates.py --whisper-week

# Validate upcoming earnings (next 7 days)
python scripts/validate_earnings_dates.py --upcoming 7

# Dry run (don't update database)
python scripts/validate_earnings_dates.py MRVL --dry-run
```

## Usage Examples

### Validate Whisper Mode Tickers

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0
./venv/bin/python scripts/validate_earnings_dates.py --whisper-week
```

### Validate Before Trading

```bash
# Validate all earnings in next 3 days
./venv/bin/python scripts/validate_earnings_dates.py --upcoming 3

# If conflicts found, inspect manually:
./venv/bin/python scripts/validate_earnings_dates.py TICKER --log-level DEBUG
```

### Integrate into Workflow

Add to `trade.sh` or run before scanning:

```bash
# In trade.sh whisper mode:
if [[ "$MODE" == "whisper" ]]; then
    echo "🔍 Validating earnings dates..."
    python scripts/validate_earnings_dates.py --whisper-week --dry-run
    # ... rest of whisper mode logic
fi
```

## Conflict Detection

When dates differ by more than 7 days, a conflict is flagged:

```
⚠️  CONFLICT: Dates differ by 2 days:
    Yahoo Finance: 2025-12-02 (AMC) |
    Alpha Vantage: 2025-12-04 (UNKNOWN)
```

The system automatically chooses the most reliable source (Yahoo Finance) but logs the conflict for review.

## Historical Example (MRVL, AEO)

**Problem:** Alpha Vantage had wrong dates for MRVL and AEO on 12/3/2025

| Ticker | Correct Date | Alpha Vantage | Difference |
|--------|-------------|---------------|------------|
| MRVL   | 2025-12-02  | 2025-12-04    | +2 days ❌ |
| AEO    | 2025-12-02  | 2025-12-03    | +1 day ❌  |

**Solution:** Cross-reference system caught the conflicts and used Yahoo Finance (correct dates).

## Future Enhancements

1. **Earnings Whisper Date Extraction**
   - Parse dates from tweet text ("November 17" → 2025-11-17)
   - Add as third validation source
   - Particularly useful for whisper mode

2. **Automatic Daily Validation**
   - Cron job to validate upcoming earnings
   - Email alerts for conflicts
   - Auto-update database with consensus dates

3. **Conflict Resolution UI**
   - Dashboard showing date conflicts
   - Manual override capability
   - Historical conflict tracking

## API Response Formats

### Yahoo Finance
```python
{
    'Earnings Date': [datetime.date(2025, 12, 2)],
    'Earnings High': 0.81,
    'Earnings Low': 0.75,
    ...
}
```

### Alpha Vantage
```csv
symbol,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay
MRVL,2025-12-04,2025-10-31,0.74,USD,
```

## Configuration

Environment variables (optional):
```bash
export ALPHA_VANTAGE_KEY="your_key_here"
export DB_PATH="data/ivcrush.db"
```

## Troubleshooting

### "No earnings date found"
- Check if ticker is valid
- Verify ticker has upcoming earnings
- Try with `--log-level DEBUG` for details

### "Failed to update database"
- Check database permissions
- Verify DB_PATH is correct
- Ensure database schema is up to date

### Rate Limits
- Yahoo Finance: No API key required, but may throttle aggressive requests
- Alpha Vantage: 25 requests/day (free tier)

## Testing

```bash
# Test Yahoo Finance fetcher
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0
./venv/bin/python -m src.infrastructure.data_sources.yahoo_finance_earnings

# Test validator
./venv/bin/python -c "
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings
from src.infrastructure.api.alpha_vantage import AlphaVantageAPI
from src.application.services.earnings_date_validator import EarningsDateValidator
import os

validator = EarningsDateValidator(
    alpha_vantage=AlphaVantageAPI(api_key=os.getenv('ALPHA_VANTAGE_KEY', ''), rate_limiter=None),
    yahoo_finance=YahooFinanceEarnings()
)

result = validator.validate_earnings_date('MRVL')
print(result.value if result.is_ok else result.error)
"
```

## References

- Yahoo Finance Python Package: https://github.com/ranaroussi/yfinance
- Alpha Vantage API: https://www.alphavantage.co/documentation/
- Earnings Whisper Twitter: https://twitter.com/eWhispers
