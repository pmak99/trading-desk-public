# 2.0 Core Math Engine

Production VRP calculations and strategy generation for IV Crush trading. This is the shared library that all other subsystems (4.0, 5.0, 6.0) build upon.

## Quick Start

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -e .

# Configure
cp .env.example .env   # Edit with your API keys

# Run
./trade.sh NVDA 2026-02-10      # Single ticker
./trade.sh scan 2026-02-10      # All earnings on date
./trade.sh whisper              # Most anticipated earnings
./trade.sh health               # System health check
./trade.sh sync-cloud           # Sync with GCS + backup to GDrive
```

## Commands

| Command | Description |
|---------|-------------|
| `./trade.sh TICKER DATE` | Analyze single ticker for specific earnings date |
| `./trade.sh scan DATE` | Scan all tickers with earnings on date |
| `./trade.sh whisper [DATE]` | Find most anticipated earnings this week |
| `./trade.sh sync-cloud` | Sync DB with Google Cloud Storage + backup |
| `./trade.sh health` | System health check (API connectivity, DB integrity) |

## Architecture

Domain-Driven Design with clean separation of concerns:

```
src/
├── config/                  # Configuration and scoring parameters
│   ├── config.py            # Environment-based config loading
│   ├── scoring_config.py    # VRP thresholds, weights, tier definitions
│   └── validation.py        # Config validation
├── domain/                  # Core domain models and value objects
│   └── scoring/             # Scoring domain logic
├── application/             # Business logic layer
│   ├── metrics/             # VRP, liquidity, skew, implied move calculators
│   │   ├── vrp.py           # VRP ratio calculation
│   │   ├── liquidity_scorer.py  # 4-tier liquidity scoring
│   │   ├── skew_enhanced.py     # IV skew analysis (polynomial fit)
│   │   ├── implied_move.py      # Implied move from options chains
│   │   ├── consistency_enhanced.py  # Historical consistency scoring
│   │   ├── term_structure_analyzer.py  # IV term structure
│   │   ├── market_conditions.py  # Market regime detection
│   │   └── adaptive_thresholds.py  # Dynamic threshold adjustment
│   ├── async_metrics/       # Async VRP analysis
│   ├── filters/             # Weekly options filtering
│   ├── services/            # Application services
│   │   ├── analyzer.py      # Main analysis orchestration
│   │   ├── scorer.py        # Score aggregation (55/25/20 weights)
│   │   ├── strategy_generator.py  # Strategy recommendation engine
│   │   ├── backtest_engine.py     # Backtesting service
│   │   ├── health.py        # Health check service
│   │   └── earnings_date_validator.py  # Earnings date validation
│   └── handlers/            # Command handlers
├── infrastructure/          # External integrations
│   ├── api/                 # API clients
│   │   ├── tradier.py       # Options chains, Greeks, IV
│   │   ├── alpha_vantage.py # Earnings calendar
│   │   └── yfinance_async.py  # Yahoo Finance (prices, earnings)
│   ├── database/            # Database layer
│   │   ├── repositories/    # Repository pattern (analysis, earnings, prices)
│   │   ├── connection_pool.py  # SQLite connection pooling
│   │   ├── init_schema.py   # Schema initialization
│   │   └── migrations/      # Database migrations
│   ├── cache/               # Caching layer
│   │   ├── memory_cache.py  # In-memory LRU cache
│   │   └── hybrid_cache.py  # Memory + DB hybrid cache
│   └── monitoring/          # Monitoring infrastructure
├── utils/                   # Cross-cutting utilities
│   ├── rate_limiter.py      # API rate limiting
│   ├── circuit_breaker.py   # Circuit breaker for API failures
│   ├── retry.py             # Retry with exponential backoff
│   ├── concurrent_scanner.py  # Parallel ticker scanning
│   ├── market_hours.py      # Market hours/trading day helpers
│   └── shutdown.py          # Graceful shutdown handling
└── container.py             # Dependency injection container
```

## Database

SQLite at `data/ivcrush.db` with 15 tables. See root [README](../README.md#databases) for full table list.

Key schema:

```sql
CREATE TABLE strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy_type TEXT NOT NULL CHECK(strategy_type IN ('SINGLE','SPREAD','IRON_CONDOR','STRANGLE')),
    acquired_date DATE NOT NULL,
    sale_date DATE NOT NULL,
    days_held INTEGER,
    expiration DATE,
    quantity INTEGER,
    net_credit REAL,
    net_debit REAL,
    gain_loss REAL NOT NULL,
    is_winner BOOLEAN NOT NULL,
    earnings_date DATE,
    actual_move REAL,
    trade_type TEXT CHECK(trade_type IN ('NEW','ROLL','REPAIR','ADJUSTMENT')),
    parent_strategy_id INTEGER REFERENCES strategies(id),
    campaign_id TEXT,
    trr_at_entry REAL,
    position_limit_at_entry INTEGER
);
```

## Configuration

```bash
# Required
TRADIER_API_KEY=xxx           # Options chains, Greeks, IV
ALPHA_VANTAGE_KEY=xxx         # Earnings calendar
DB_PATH=data/ivcrush.db

# Optional
VRP_THRESHOLD_MODE=BALANCED   # CONSERVATIVE, BALANCED, AGGRESSIVE
USE_KELLY_SIZING=true
KELLY_FRACTION=0.25
```

## Testing

```bash
./venv/bin/python -m pytest tests/ -v           # All 690 tests
./venv/bin/python -m pytest tests/unit/ -v      # Unit tests only
./venv/bin/python -m pytest tests/ --cov=src    # With coverage
```

## How Other Systems Use 2.0

All subsystems import 2.0 via `sys.path` injection:

```python
import sys
sys.path.insert(0, "/path/to/2.0")
from src.container import get_container
```

- **4.0** imports VRP calculations, adds sentiment modifier on top
- **5.0** ports VRP/liquidity/skew/direction logic for cloud deployment
- **6.0** wraps 2.0's container in `Container2_0` for agent access

---

**Disclaimer:** For research purposes only. Not financial advice.
