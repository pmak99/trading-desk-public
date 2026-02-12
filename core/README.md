# core Core Math Engine

Production VRP calculations and strategy generation for IV Crush trading. This is the shared library that all other subsystems (4.0, 5.0, 6.0) build upon.

> **Note:** Application logic (scoring, strategy generation, backtesting) and tests have been removed from this public version. Infrastructure, domain types, and utility patterns are preserved.

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
├── domain/                  # Core domain models and value objects
│   ├── types.py             # Type definitions
│   ├── enums.py             # Domain enumerations
│   ├── protocols.py         # Interface protocols
│   └── errors.py            # Domain errors
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

## Configuration

```bash
# Required
TRADIER_API_KEY=xxx           # Options chains, Greeks, IV
ALPHA_VANTAGE_KEY=xxx         # Earnings calendar
DB_PATH=data/ivcrush.db
```

## How Other Systems Use 2.0

All subsystems import core via `sys.path` injection:

```python
import sys
sys.path.insert(0, "/path/to/core")
from src.container import get_container
```

- **4.0** imports VRP calculations, adds sentiment modifier on top
- **5.0** ports VRP/liquidity/skew/direction logic for cloud deployment
- **6.0** wraps 2.0's container in `Container2_0` for agent access

---

**Disclaimer:** For research purposes only. Not financial advice.
