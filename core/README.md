# IV Crush 2.0

**Production-grade earnings options trading system leveraging volatility risk premium.**

Sell options when implied volatility exceeds historical moves. Profit from IV crush after earnings.

---

## Quick Start

```bash
# Analyze a ticker's earnings opportunity
./trade.sh NVDA 2025-11-20

# Scan all earnings for a specific date
./trade.sh scan 2025-11-20

# Health check
./trade.sh health
```

**Output:**
```
✅ TRADEABLE OPPORTUNITY
VRP Ratio: 2.26x → EXCELLENT
Implied Move: 8.00% | Historical Mean: 3.54%

★ RECOMMENDED: BULL PUT SPREAD
  Short $177.50P / Long $170.00P
  Net Credit: $2.20 | Max Profit: $8,158 (37 contracts)
  Probability: 69.1% | Theta: +$329/day
```

---

## Core Edge

**Volatility Risk Premium (VRP)** - The market consistently overprices earnings volatility.

Our system:
1. **Measures the gap** - Implied move vs historical mean move
2. **Quantifies quality** - VRP profiles (CONSERVATIVE, BALANCED, AGGRESSIVE)
3. **Sizes positions** - Kelly Criterion with fractional sizing (25%)
4. **Selects strikes** - Delta-based with directional bias detection

**Validated:** 8 selected trades Q2-Q4 2024 → 100% win rate, Sharpe 8.07

---

## Installation

### Prerequisites

- Python 3.11+
- [Tradier API key](https://documentation.tradier.com/)
- [Alpha Vantage API key](https://www.alphavantage.co/support/#api-key)

### Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cat > .env << EOF
TRADIER_API_KEY=your_tradier_key
ALPHA_VANTAGE_KEY=your_alphavantage_key
DB_PATH=data/ivcrush.db
LOG_LEVEL=INFO
MIN_HISTORICAL_QUARTERS=2
VRP_THRESHOLD_MODE=BALANCED
EOF

# Initialize database
mkdir -p data logs backups
python -c "from src.infrastructure.database.init_schema import initialize_database; initialize_database('./data/ivcrush.db')"

# Verify
python scripts/health_check.py
```

---

## Usage

### Basic Commands

```bash
# Single ticker analysis
./trade.sh NVDA 2025-11-20

# Multiple tickers
./trade.sh list NVDA,WMT,AMD 2025-11-20

# Scan earnings date
./trade.sh scan 2025-11-20

# Most anticipated earnings (current week)
./trade.sh whisper

# Help
./trade.sh --help
```

### Configuration Profiles

**VRP Threshold Profiles** (set via `VRP_THRESHOLD_MODE` in .env):

| Profile | VRP Excellent | VRP Good | Use Case |
|---------|---------------|----------|----------|
| CONSERVATIVE | 2.0x | 1.5x | Fewer, higher-quality trades |
| BALANCED | 1.8x | 1.4x | **Default** - empirically validated |
| AGGRESSIVE | 1.5x | 1.3x | More trades, accept lower VRP |

**Position Sizing** (set via Kelly parameters in .env):

```bash
USE_KELLY_SIZING=true          # Enable Kelly Criterion (default)
KELLY_FRACTION=0.25            # 25% of full Kelly (conservative)
KELLY_MIN_EDGE=0.02            # Minimum 2% edge required (credit spreads: 2-4%)
```

Kelly Criterion automatically adjusts position size based on:
- Probability of profit (from skew analysis)
- Reward/risk ratio
- Edge (expected value)

---

## Architecture

### Design Principles

- **Clean Architecture** - Domain/Application/Infrastructure layers
- **Functional Core** - Immutable types, Result monads, no exceptions
- **Protocol-Based DI** - Testable, swappable components
- **Fail-Fast** - Validation at boundaries, trust internal guarantees

### Core Components

**Domain Layer** (`src/domain/`)
- Immutable value objects: `Money`, `Percentage`, `Strike`
- Result pattern for error handling
- Business protocols (no implementations)

**Application Layer** (`src/application/`)
- **VRPCalculator** - Implied move vs historical mean
- **SkewAnalyzerEnhanced** - Polynomial IV skew fitting
- **ConsistencyAnalyzerEnhanced** - Exponential-weighted reliability
- **StrategyGenerator** - Iron condors, credit spreads with Kelly sizing
- **LiquidityScorer** - 3-tier classification (EXCELLENT/WARNING/REJECT)

**Infrastructure Layer** (`src/infrastructure/`)
- **TradierClient** - Real-time option chains + Greeks
- **AlphaVantageClient** - Earnings calendar
- **SQLite + WAL mode** - 675 earnings moves, 52 tickers, 2022-2024
- **Hybrid cache** - L1 (memory) + L2 (SQLite), 95%+ hit rate

### Resilience

- Circuit breakers with exponential backoff
- Retry logic on transient failures
- Health monitoring (Tradier, DB, cache)
- Automatic database backups (every 6 hours)

---

## Database

**Schema:**
```sql
CREATE TABLE historical_moves (
    ticker TEXT,
    earnings_date DATE,
    prev_close REAL,
    earnings_close REAL,
    close_move_pct REAL,        -- Actual historical move
    volume_before INTEGER,
    volume_earnings INTEGER
);
```

**Backups:**
- Auto-triggered every 6 hours when running `./trade.sh`
- Stored in `backups/ivcrush_YYYYMMDD_HHMMSS.db`
- Retained 30 days (automatic cleanup)
- Recommend syncing `backups/` to Google Drive

**Restore:**
```bash
./scripts/restore_database.sh
```

---

## Validation Results

### Forward Test (Q2-Q4 2024)

**Selected Trades:** 8 high-VRP opportunities (ENPH, SHOP, AVGO, RDDT)

| Metric | Result |
|--------|--------|
| Win Rate | **100%** |
| Sharpe Ratio | **8.07** |
| Total P&L | $1,124.71 |
| Capital | $40,000 |

### Market Regime Analysis (208 trades)

| VIX Regime | Win Rate | Sharpe |
|------------|----------|--------|
| High (25+) | 83.3% | 6.06 |
| Normal (15-25) | 72.7% | 3.59 |
| Low (<15) | 68.8% | 2.84 |

**Key Insight:** System performs best in elevated volatility (VRP more pronounced).

---

## Recent Enhancements

### December 2025 - Kelly Criterion & Strategy Scoring

**Kelly Criterion Position Sizing:**
- Formula: `f* = (p × b - q) / b` where p=POP, b=win/loss ratio
- 25% fractional Kelly for conservative capital growth
- Minimum 2% edge requirement (adjusted for credit spreads)
- Automatic contract calculation based on probability and edge

**Kelly Edge Scoring Fix:**
- Replaced R/R-only scoring with Kelly edge scoring in strategy selection
- Prevents negative EV trades from outscoring positive EV trades
- Edge = (POP × R/R) - (1 - POP) combines probability and reward
- Negative edge strategies score 0 points for edge component

**Profit Zone vs Implied Move Fix:**
- Penalizes narrow profit zones when implied move is large
- Prevents Iron Butterflies from being recommended with huge expected moves
- Example: 2.96% profit zone with 12.98% implied move → 53% penalty
- Credit spreads exempted (one-sided risk, handle large moves better)

**VRP Profile System:**
- 4 profiles: CONSERVATIVE, BALANCED, AGGRESSIVE, LEGACY
- Research-backed thresholds (vs. overfitted 7.0x/4.0x)
- Profile selection via `VRP_THRESHOLD_MODE` environment variable
- Individual threshold overrides supported

**Improvements:**
- POP validation (0.0-1.0 range check)
- Warning logs when env vars override profiles
- Comprehensive test suite (20 VRP profile tests, 11 Kelly tests)

See `FIXES_SUMMARY.md`, `CONFIG_REFERENCE.md`, and `CODE_REVIEW_KELLY_VRP.md` for details.

### December 2025 - Technical Debt Cleanup

**Removed:**
- 66 lines of dead Reddit parsing code (Twitter/X is primary source)
- Misleading "legacy" comments on active functions

**Impact:**
- Cleaner codebase, no functional changes
- All tests passing (22 test files, 100+ scenarios)

See `TECH_DEBT_CLEANUP.md` for analysis.

### November 2025 - Cache & Database Improvements

**Thread Safety:**
- Added `threading.Lock` to MemoryCache
- Multi-threaded test: 10 threads × 20 operations = PASS

**Database Performance:**
- Enabled WAL mode (10x concurrency improvement)
- 30-second connection timeout across all repositories
- `PRAGMA synchronous=NORMAL` for better performance

**UX Improvements:**
- Comprehensive help mode (`./trade.sh --help`)
- Auto-backfill missing historical data
- Improved error messages and display

---

## Testing

**Test Suite:**
- 22 test files covering unit, integration, and performance
- Core business logic (VRP, Kelly, skew, consistency)
- Infrastructure (API clients, database, cache)
- Validation scenarios (edge cases, invalid inputs)

**Run tests:**
```bash
pytest tests/
```

**Coverage:**
```bash
pytest --cov=src --cov-report=term-missing
```

---

## Project Structure

```
2.0/
├── src/
│   ├── domain/              # Value objects, protocols, errors
│   ├── application/         # Business logic
│   │   ├── metrics/        # VRP, skew, consistency calculators
│   │   └── services/       # Analyzer, strategy generator
│   ├── infrastructure/      # API clients, database, cache
│   ├── config/             # Configuration & validation
│   ├── utils/              # Retry, circuit breaker, logging
│   └── container.py        # Dependency injection
├── tests/
│   ├── unit/               # Unit tests (Kelly, VRP, etc.)
│   ├── integration/        # Integration tests
│   └── performance/        # Load tests
├── scripts/
│   ├── analyze.py          # Core analysis engine
│   ├── scan.py             # Scanning/ticker modes
│   ├── backfill_yfinance.py # Historical data backfill
│   ├── health_check.py     # System health verification
│   └── restore_database.sh # Database restore utility
├── data/
│   ├── ivcrush.db          # Historical moves database
│   └── watchlist.txt       # Ticker watchlist
├── backups/                # Automatic database backups
├── docs/                   # ADRs and technical documentation
├── archive/                # Legacy code and old configs
├── trade.sh                # Main entry point
├── .env                    # Configuration (not in git)
├── .env.example            # Configuration template
└── README.md               # This file
```

---

## Configuration Reference

### Core Settings

```bash
# APIs
TRADIER_API_KEY=your_key
ALPHA_VANTAGE_KEY=your_key

# Database
DB_PATH=data/ivcrush.db
MIN_HISTORICAL_QUARTERS=2

# VRP Thresholds
VRP_THRESHOLD_MODE=BALANCED      # CONSERVATIVE | BALANCED | AGGRESSIVE | LEGACY
VRP_EXCELLENT=1.8                # Override profile default
VRP_GOOD=1.4                     # Override profile default

# Kelly Criterion
USE_KELLY_SIZING=true
KELLY_FRACTION=0.25              # 25% of full Kelly
KELLY_MIN_EDGE=0.02              # 2% minimum edge
KELLY_MIN_CONTRACTS=1
```

See `.env.example` and `CONFIG_REFERENCE.md` for complete reference.

---

## Documentation

**User Guides:**
- `CONFIG_REFERENCE.md` - Configuration parameters quick reference
- `FIXES_SUMMARY.md` - Kelly Criterion & VRP profile implementation
- `MCP_USAGE_GUIDE.md` - Model Context Protocol integration

**Technical:**
- `CODE_REVIEW_KELLY_VRP.md` - Code review of Kelly/VRP changes
- `ADVISORY_IMPROVEMENTS.md` - Post-review enhancements
- `TECH_DEBT_CLEANUP.md` - Technical debt analysis
- `CHANGELOG.md` - Version history
- `docs/adr/` - Architecture Decision Records

**Legacy:**
- `archive/` - Old configurations and deprecated code
- `../1.0/` - Original system (reference only)

---

## Workflow

### Pre-Trade
1. **Health check**: `./trade.sh health`
2. **Analyze ticker**: `./trade.sh NVDA 2025-11-20`
3. **Review output**: VRP ratio, strategy, Greeks, liquidity
4. **Verify historical data**: Check consistency score, sample size

### Trade Execution
5. **Validate strikes**: Confirm bid/ask spreads acceptable
6. **Size position**: Use Kelly-recommended contracts (or adjust)
7. **Place order**: Execute in broker (Tradier, TastyTrade, etc.)
8. **Set alerts**: Earnings date, target profit, max loss

### Post-Trade
9. **Track P&L**: Monitor through expiration
10. **Record outcome**: Actual move vs implied move
11. **Update database**: Backfill actual results if needed

---

## Performance

| Metric | Value |
|--------|-------|
| Response time | ~1.0ms per ticker (avg) |
| Scaling | Linear to 100 concurrent tickers |
| Cache hit rate | 95%+ on repeat queries |
| Database mode | WAL (concurrent-safe) |
| Backup frequency | Every 6 hours |
| Test coverage | Core business logic |

---

## Troubleshooting

### Common Issues

**Missing historical data:**
- System auto-backfills last 3 years on first analysis
- Manual: `python scripts/backfill_yfinance.py TICKER`

**API rate limits:**
- Alpha Vantage: 5 calls/min, 500/day (free tier)
- Tradier: 120 calls/min (sandbox/brokerage)
- System respects limits via circuit breakers

**Database locked:**
- WAL mode prevents most lock issues
- If locked: check for long-running queries
- Worst case: `./scripts/restore_database.sh`

**Health check failing:**
- Check API keys in `.env`
- Verify network connectivity
- Check Tradier sandbox vs production endpoint

---

## License

MIT License

---

## Disclaimer

**FOR RESEARCH AND EDUCATIONAL PURPOSES ONLY.**

This software provides analytical tools for options trading research. It is **not financial advice**. Options trading involves substantial risk of loss. Always verify data, strategies, and calculations independently before risking capital.

The authors and contributors are not responsible for any trading losses incurred using this software.

---

**Built with Claude Code** | December 2025
