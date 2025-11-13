# IV Crush 2.0 - Production-Grade Options Trading System

**Version:** 2.0.1
**Status:** 🟢 Fully Optimized (Phase 4 Complete)
**Test Coverage:** 59.87% (201/201 tests passing)

A production-grade options trading system for analyzing implied volatility moves and identifying optimal earnings trade opportunities using the Volatility Risk Premium (VRP) strategy.

---

## Status: 🟢 Fully Optimized

**Current Phase**: Phase 4 Complete - Algorithmic Optimization
**Progress**: 201 tests passing, all phases complete

The 2.0 system is fully optimized and production-ready:
- ✅ **Phase 1**: Critical resilience (circuit breakers, retry logic, health checks)
- ✅ **Phase 2**: Data persistence (hybrid cache, configuration validation, performance tracking)
- ✅ **Phase 3**: Production deployment (edge case tests, load testing, deployment guides)
- ✅ **Phase 4**: Algorithmic optimization (polynomial skew, weighted consistency, interpolation)

## Documentation

- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Complete deployment guide for production
- **[RUNBOOK.md](RUNBOOK.md)** - Operational procedures and troubleshooting
- **[PROGRESS.md](PROGRESS.md)** - Detailed session-by-session progress tracker
- **[docs/SCANNING_MODES.md](docs/SCANNING_MODES.md)** - NEW: Scanning & Ticker modes guide
- **docs/2.0_OVERVIEW.md** - System architecture and design
- **docs/2.0_IMPLEMENTATION.md** - Implementation guide

## Key Features

### Production Resilience
- ✅ **Circuit Breakers**: Automatic failure detection and recovery
- ✅ **Retry Logic**: Exponential backoff with configurable attempts
- ✅ **Health Checks**: Monitor Tradier API, database, and cache
- ✅ **Correlation ID Tracing**: Track requests across system

### Performance
- ✅ **Hybrid Caching**: L1 (memory) + L2 (SQLite) with automatic promotion
- ✅ **Concurrent Processing**: Handle 50-100 tickers concurrently
- ✅ **Performance Monitoring**: Track metrics, identify slow operations
- ✅ **Linear Scaling**: 2.79x response time for 4x load

### Data & Configuration
- ✅ **SQLite Database**: Historical moves with WAL mode for concurrency
- ✅ **Configuration Validation**: Fail-fast with detailed error messages
- ✅ **Environment-Based**: Secure configuration via .env files

### Testing & Quality
- ✅ **201 Tests**: 193 unit + 8 load tests, all passing
- ✅ **59.87% Coverage**: Core business logic thoroughly tested
- ✅ **Edge Case Tests**: 27 tests for unusual inputs and boundaries
- ✅ **Load Tests**: Validated up to 100 concurrent tickers
- ✅ **Phase 4 Tests**: 29 tests for enhanced algorithms (93%+ coverage)

### Architecture
1. **Domain Layer**: Immutable types (Money, Percentage, Strike, OptionChain)
2. **Application Layer**: Business logic (ImpliedMove, VRP calculators)
3. **Infrastructure Layer**: API clients, database, cache
4. **Result Pattern**: Functional error handling with Result[T, Error]
5. **Dependency Injection**: Clean separation with container pattern

## Quick Start

### Prerequisites

1. **Python 3.11+** installed
2. **Tradier API key** ([get one](https://documentation.tradier.com/))
3. **Alpha Vantage API key** ([get one](https://www.alphavantage.co/support/#api-key))

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/trading-desk.git
cd trading-desk/2.0

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env and add your API keys
```

### Configuration

Create `.env` file with:

```ini
TRADIER_API_KEY=your_tradier_key_here
ALPHA_VANTAGE_KEY=your_alphavantage_key_here
DATABASE_PATH=./data/ivcrush.db
LOG_LEVEL=INFO
```

### Initialize Database

```bash
# Create data directory
mkdir -p data logs

# Initialize database
python -c "from src.infrastructure.database.init_schema import initialize_database; initialize_database('./data/ivcrush.db')"

# Enable WAL mode for production
sqlite3 ./data/ivcrush.db "PRAGMA journal_mode=WAL;"
```

### Verify Installation

```bash
# Run tests
pytest tests/unit/ tests/performance/ -v
# Expected: 172/172 tests pass

# Run health check
python scripts/health_check.py
# Expected: All services healthy
```

### Usage

#### 🆕 Scanning Mode (Recommended)

Automatically scan all earnings for a specific date:

```bash
# Scan all earnings for a specific date
python scripts/scan.py --scan-date 2025-01-31
```

#### 🆕 Ticker Mode (Recommended)

Analyze specific tickers without CSV files:

```bash
# Analyze tickers from command line (auto-fetches earnings dates)
python scripts/scan.py --tickers AAPL,MSFT,GOOGL
```

See **[docs/SCANNING_MODES.md](docs/SCANNING_MODES.md)** for complete guide on scanning and ticker modes.

#### Analyze Single Ticker (Manual)

```bash
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01
```

#### Bulk Analysis (CSV-based)

```bash
# Create ticker list and earnings calendar CSV
echo -e "ticker,earnings_date,expiration_date\nAAPL,2025-01-31,2025-02-01" > earnings.csv

# Analyze all
python scripts/analyze_batch.py --tickers AAPL,MSFT --earnings-file earnings.csv
```

#### Backfill Historical Data

```bash
python scripts/backfill.py AAPL
python scripts/backfill.py --tickers AAPL,MSFT
```

---

## Production Deployment

For production deployment, see [DEPLOYMENT.md](DEPLOYMENT.md) for:
- Environment setup
- Security configuration
- Database optimization
- Health checks and monitoring
- Backup procedures

For day-to-day operations, see [RUNBOOK.md](RUNBOOK.md) for:
- Common tasks
- Troubleshooting guide
- Performance tuning
- Maintenance procedures

---

## Testing

```bash
# Run all tests
pytest tests/unit/ tests/performance/ -v

# Run specific test suites
pytest tests/unit/ -v                    # Unit tests (164)
pytest tests/performance/ -v             # Load tests (8)
pytest tests/unit/test_edge_cases.py -v # Edge cases (27)

# Run with coverage
pytest tests/unit/ --cov=src --cov-report=html
```

---

## Performance Benchmarks

From load testing (tests/performance/test_load.py):

- **10 tickers:** 0.010s (1.0ms avg per ticker)
- **50 tickers:** 0.017s (0.3ms avg per ticker)
- **100 tickers:** 0.032s (0.3ms avg per ticker)
- **Scaling:** 2.79x time for 4x load (excellent linear scaling)

---

## Project Structure

```
2.0/
├── src/
│   ├── domain/           # Domain types, errors, protocols
│   ├── application/      # Business logic (calculators, services)
│   ├── infrastructure/   # API clients, database, cache
│   ├── config/          # Configuration and validation
│   ├── utils/           # Utilities (retry, circuit breaker, etc.)
│   └── container.py     # Dependency injection
├── tests/
│   ├── unit/            # Unit tests (164 tests)
│   ├── performance/     # Load tests (8 tests)
│   └── integration/     # Integration tests
├── scripts/
│   ├── analyze.py       # Main analysis script
│   ├── backfill.py      # Historical data backfill
│   └── health_check.py  # Health monitoring
├── docs/               # Additional documentation
├── DEPLOYMENT.md       # Production deployment guide
├── RUNBOOK.md          # Operational runbook
├── PROGRESS.md         # Development progress tracker
└── README.md           # This file
```

---

## Phase 4 Complete: Enhanced Algorithms ✅

- ✅ **Polynomial Skew Fitting**: 5+ OTM points, 2nd-degree polynomial, directional bias detection
- ✅ **Exponential-Weighted Consistency**: Recent quarters weighted 85% per quarter, trend detection
- ✅ **Straddle Interpolation**: Linear interpolation between strikes, smoother calculations
- ✅ **Comprehensive Testing**: 29 new tests, 93%+ coverage on all new modules

### Benefits of Phase 4 Enhancements:
- **Better Edge Detection**: Polynomial skew catches directional bias missed by single-point analysis
- **Weighted History**: Recent earnings moves appropriately weighted vs older quarters
- **Smoother Calculations**: Interpolation eliminates rounding discontinuities for between-strike prices
- **Trend Awareness**: System detects if volatility is increasing (bad signal for IV crush)
- **Confidence Scoring**: Trustworthiness metrics inform strategy decisions

---

**Note**: The existing 1.0 system is preserved in the `1.0/` directory.
