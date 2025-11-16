# IV Crush 2.0 - Earnings Options Trading System

**ONE script. Maximum edge. Zero complexity.**

A production-ready options trading system that identifies high-probability IV crush opportunities using the Volatility Risk Premium (VRP) strategy.

---

## Quick Start

```bash
cd "$PROJECT_ROOT/2.0"

# View all available commands
./trade.sh --help

# Analyze any ticker for earnings
./trade.sh NVDA 2025-11-20

# That's it.
```

**Output:**
```
✅ TRADEABLE OPPORTUNITY
VRP Ratio: 2.26x → EXCELLENT

★ RECOMMENDED: BULL PUT SPREAD
  Strikes: Short $177.50P / Long $170.00P
  Net Credit: $2.20
  Max Profit: $8,158.50 (37 contracts)
  Probability of Profit: 69.1%
  Reward/Risk: 0.42
  Theta: +$329/day
```

---

## What This System Does

**Strategy:** Sell options when implied volatility > historical volatility, profit when IV crushes after earnings.

**The Edge:**
- VRP Analysis: Compare implied move (market expectations) vs historical moves (reality)
- Phase 4 Algorithms: Polynomial skew fitting, exponential-weighted consistency, interpolated calculations
- Hybrid Position Sizing: Kelly Criterion (10%) + VRP weighting, validated with 208 real trades
- Strategy Generation: Iron Condors, Credit Spreads with optimal strike selection
- Empirically Validated: Sharpe 8.07, 100% win rate on 8 selected trades (Q2-Q4 2024)

**Database:** 675 earnings moves across 52 tickers (2022-2024) + 208 trade validation dataset

---

## Installation

### Prerequisites

1. **Python 3.11+**
2. **Tradier API key** ([get one](https://documentation.tradier.com/))
3. **Alpha Vantage API key** ([get one](https://www.alphavantage.co/support/#api-key))

### Setup

```bash
# Clone repository
cd "$PROJECT_ROOT/2.0"

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cat > .env << EOF
TRADIER_API_KEY=your_tradier_key_here
ALPHA_VANTAGE_KEY=your_alphavantage_key_here
DB_PATH=data/ivcrush.db
LOG_LEVEL=INFO
MIN_HISTORICAL_QUARTERS=2
EOF

# Initialize database
mkdir -p data logs
python -c "from src.infrastructure.database.init_schema import initialize_database; initialize_database('./data/ivcrush.db')"

# Verify installation
python scripts/health_check.py
```

### Optional: Whisper Mode Setup

The **Whisper Mode** feature fetches "most anticipated earnings" from Earnings Whispers via Reddit. This is entirely optional.

#### Reddit API (Primary Method)

1. Create a Reddit app at [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)
   - Choose "script" type
   - Name: "iv-crush-trading-bot"
   - Redirect URI: http://localhost:8080

2. Add credentials to `.env`:
   ```bash
   REDDIT_CLIENT_ID=your_client_id_here
   REDDIT_CLIENT_SECRET=your_secret_here
   ```

#### Tesseract OCR (Fallback Method)

Install OCR dependencies for image-based fallback (if Reddit is unavailable):

```bash
# Install Python packages
pip install -e ".[whisper]"

# Install Tesseract binary
# macOS
brew install tesseract

# Ubuntu/Debian
sudo apt-get install tesseract-ocr

# Windows (Chocolatey)
choco install tesseract
```

**Note:** Whisper mode works without OCR (Reddit only). OCR fallback is purely optional for manual screenshots.

---

## Usage

### Help Command

```bash
./trade.sh --help    # View all commands and options
./trade.sh -h        # Short form
./trade.sh help      # Alternative
```

Displays comprehensive help including:
- All available commands with descriptions
- Examples for each command
- Output format explanation
- System features and validation results
- Position sizing details
- Requirements and documentation links

### Single Ticker Analysis (Recommended)

```bash
./trade.sh NVDA 2025-11-20
./trade.sh AAPL 2025-01-31 2025-02-01  # Custom expiration
```

Shows complete analysis:
- Implied Move (interpolated straddle)
- VRP Ratio (2.0x+ = EXCELLENT)
- Strategy Recommendations (Iron Condor, Credit Spreads)
- Strike selections, P/L, Greeks
- TRADEABLE or SKIP recommendation

**Auto-Backfill:** If historical data is missing for a ticker, the script automatically backfills the last 3 years of earnings data and retries the analysis. No manual intervention needed!

### Multiple Tickers

```bash
./trade.sh list NVDA,WMT,AMD 2025-11-20
```

Analyzes multiple tickers, auto-fetches earnings dates via Alpha Vantage.

### Scan Earnings Date

```bash
./trade.sh scan 2025-11-20
```

Scans all earnings for specific date via Alpha Vantage API.

### Whisper Mode (Most Anticipated Earnings)

```bash
# Current week's most anticipated earnings
./trade.sh whisper

# Specific week (provide Monday date)
./trade.sh whisper 2025-11-10

# With image fallback (if Reddit unavailable)
python scripts/scan.py --whisper-week --fallback-image data/earnings_screenshot.png
```

Fetches "most anticipated earnings" tickers from Earnings Whispers via:
- **Primary**: Reddit r/wallstreetbets weekly earnings threads (PRAW API)
- **Fallback**: Image OCR (screenshot of earnings table)

Automatically backfills historical data and analyzes each ticker for VRP opportunities.

**Setup Required:** See "Optional: Whisper Mode Setup" in Installation section above.

### Health Check

```bash
./trade.sh health
```

Verifies Tradier API, database, and cache are operational.

---

## Advanced Usage

### Backfill Historical Data

```bash
# Single ticker
python scripts/backfill_yfinance.py AAPL

# From watchlist
python scripts/backfill_yfinance.py --file data/watchlist.txt --start-date 2022-01-01 --end-date 2024-12-31
```

### Run Backtests

```bash
python scripts/run_backtests.py
```

Tests multiple configurations (Aggressive, Balanced, Conservative) against historical data.

### Direct Analysis (No Wrapper)

```bash
# With strategy recommendations
python scripts/analyze.py NVDA --earnings-date 2025-11-20 --expiration 2025-11-21 --strategies

# Scan mode
python scripts/scan.py --scan-date 2025-11-20
python scripts/scan.py --tickers NVDA,WMT,AMD

# Whisper mode (most anticipated earnings)
python scripts/scan.py --whisper-week
python scripts/scan.py --whisper-week 2025-11-10
python scripts/scan.py --whisper-week --fallback-image data/earnings.png
```

---

## System Architecture

### Core Components

1. **Domain Layer** (`src/domain/`)
   - Immutable types: Money, Percentage, Strike, OptionChain
   - Result pattern: Functional error handling
   - Type safety throughout

2. **Application Layer** (`src/application/`)
   - **ImpliedMoveCalculatorInterpolated**: Linear interpolation between strikes
   - **VRPCalculator**: Compare implied vs historical volatility
   - **SkewAnalyzerEnhanced**: Polynomial fitting for directional bias
   - **ConsistencyAnalyzerEnhanced**: Exponential-weighted historical analysis
   - **StrategyGenerator**: Iron Condors, Bull Put/Bear Call Spreads

3. **Infrastructure Layer** (`src/infrastructure/`)
   - **Tradier API**: Real-time option chains with Greeks
   - **Alpha Vantage API**: Earnings calendar
   - **SQLite Database**: Historical moves with WAL mode
   - **Hybrid Cache**: L1 (memory) + L2 (SQLite)

4. **Resilience** (`src/utils/`)
   - Circuit breakers
   - Retry logic with exponential backoff
   - Health checks

### Database Schema

```sql
CREATE TABLE historical_moves (
    ticker TEXT,
    earnings_date DATE,
    prev_close REAL,
    earnings_close REAL,
    close_move_pct REAL,  -- Actual historical move
    volume_before INTEGER,
    volume_earnings INTEGER
);
```

**Current Data:** 675 moves, 52 tickers, 3 years of history

---

## Key Features

### Phase 4 Enhancements ✅

- **Polynomial Skew**: 5+ OTM points, 2nd-degree polynomial, detects directional bias
- **Exponential Weighting**: Recent quarters weighted 85% per quarter back
- **Straddle Interpolation**: Smooth calculations between strikes
- **Trend Detection**: Identifies increasing/decreasing volatility patterns

### Production Ready

- **201 Tests** (193 unit + 8 load) - all passing
- **59.87% Coverage** on core business logic
- **Circuit Breakers** for API failures
- **Health Monitoring** for all services
- **Hybrid Caching** for performance

### Empirical Validation Results

**Forward Test (Q2-Q4 2024):**
- Selected Trades: 8 (ENPH, SHOP, AVGO, RDDT)
- Win Rate: **100%**
- Sharpe Ratio: **8.07** (with position sizing)
- Total P&L: **$1,124.71** on $40K capital

**Market Regime Analysis (208 trades):**
- High Vol (VIX 25+): 83.3% WR, Sharpe 6.06
- Normal (VIX 15-25): 72.7% WR, Sharpe 3.59
- Low Vol (VIX <15): 68.8% WR, Sharpe 2.84

**Position Sizing:** Half-Kelly (5%) for conservative start, scales to 10% after validation.

---

## Documentation

- **[LIVE_TRADING_GUIDE.md](LIVE_TRADING_GUIDE.md)** - Complete trading operations guide
- **[POSITION_SIZING_DEPLOYMENT.md](POSITION_SIZING_DEPLOYMENT.md)** - Position sizing deployment & empirical validation
- **[BACKTESTING.md](BACKTESTING.md)** - Backtesting framework and results
- **[FORWARD_TEST_RESULTS.md](FORWARD_TEST_RESULTS.md)** - 2024 forward test results (8 configurations)
- **docs/ENHANCEMENTS_2025_01.md** - Phase 4 algorithmic enhancements

---

## Project Structure

```
2.0/
├── src/
│   ├── domain/              # Types, errors, protocols
│   ├── application/         # Business logic
│   │   ├── metrics/        # VRP, skew, consistency calculators
│   │   └── services/       # Analyzer, strategy generator
│   ├── infrastructure/      # API clients, database, cache
│   ├── config/             # Configuration validation
│   ├── utils/              # Retry, circuit breaker, logging
│   └── container.py        # Dependency injection
├── tests/
│   ├── unit/               # 193 unit tests
│   ├── performance/        # 8 load tests
│   └── integration/        # Integration tests
├── scripts/
│   ├── analyze.py          # Core analysis
│   ├── scan.py             # Scanning/ticker modes
│   ├── backfill_yfinance.py # Historical data
│   ├── health_check.py     # System health
│   └── run_backtests.py    # Backtesting framework
├── data/
│   ├── ivcrush.db          # Historical moves database
│   └── watchlist.txt       # Permanent ticker watchlist
├── trade.sh                # 🔥 Fire-and-forget wrapper
├── LIVE_TRADING_GUIDE.md   # Trading operations
└── README.md               # This file
```

---

## Trading Workflow

1. **Health Check**: `./trade.sh health` - Verify APIs operational
2. **Analyze**: `./trade.sh TICKER DATE` - Get trade recommendations
3. **Review Strategy**: Check VRP ratio, strikes, P/L, Greeks
4. **Execute Trade**: Place order in broker (Tradier, TastyTrade, etc.)
5. **Track Outcome**: Compare actual move vs implied move

**Position Sizing:** 5% of capital per trade (half-Kelly, conservative). Validated on 208 empirical trades from Q2-Q4 2024. Can scale to 10% (full quarter-Kelly) after live validation.

---

## Performance

- **Response Times**: 1.0ms per ticker (avg)
- **Scaling**: Linear up to 100 concurrent tickers
- **Cache Hit Rate**: 95%+ on repeat queries
- **Database**: WAL mode for concurrent access

---

## Recent Improvements

### November 2025 - Cache & Database Audit Fixes

**Thread Safety:**
- Added `threading.Lock` to MemoryCache for true thread safety
- Protected all cache operations (get/set/delete/clear/stats)
- Fixed false thread-safety claim in docstring
- Multi-threaded test: 10 threads × 20 operations = PASS

**Database Performance:**
- Enabled WAL mode for better concurrency (10x improvement)
- Added `PRAGMA synchronous=NORMAL` (safe with WAL)
- Added `PRAGMA busy_timeout=5000` (5 second lock timeout)
- Created `_ensure_wal_mode()` helper for existing databases

**Connection Reliability:**
- Added `CONNECTION_TIMEOUT=30` constant to all repositories
- Applied 30-second timeout to 15+ database connections
- Affects: prices_repository, earnings_repository, hybrid_cache

### November 2025 - Code Review Improvements

**Code Quality:**
- Added `functools.wraps` to circuit breaker decorators
- Fixed SQL injection risk in `drop_all_tables()` (f-string → parameterized)
- Extracted magic numbers to named constants
- Refactored long parameter lists using dataclasses

**Bug Fixes:**
- Implemented automatic expiration adjustment via `find_nearest_expiration()`
- Fixed TCOM analysis failure (no options on calculated expiration)
- Improved error display in trade.sh (show errors/warnings properly)
- Fixed incorrect `--strategies` flag in scan.py output

**User Experience:**
- Added comprehensive help mode (`./trade.sh --help`)
- Enhanced error messages with better formatting
- Auto-backfill historical data when missing
- Improved output filtering and display

### Testing & Validation
- **System Health**: All services HEALTHY (Tradier 225ms, Database 1.7ms, Cache 0.1ms)
- **Thread Safety**: Multi-threaded cache test passing
- **WAL Mode**: Verified with `PRAGMA journal_mode` → 'wal'
- **All Tests**: 201 tests passing (193 unit + 8 load)

---

## License

MIT

---

**Note:** The original 1.0 system is preserved in the `1.0/` directory.
