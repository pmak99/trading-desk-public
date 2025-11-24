# IV Crush 2.0 - Quick Reference Guide

Fast reference for common operations and commands.

---

## Command Line

### Scanning

```bash
# Scan specific tickers
python scripts/scan.py --tickers AAPL,MSFT,GOOGL

# Scan earnings for a date
python scripts/scan.py --scan-date 2025-01-31

# Scan with custom expiration offset
python scripts/scan.py --tickers TSLA --expiration-offset 1
```

### Database Migrations

```bash
# Check status
python scripts/migrate.py status

# Apply all pending
python scripts/migrate.py migrate

# Rollback to version
python scripts/migrate.py rollback 2

# Create new migration
python scripts/migrate.py create add_new_feature
```

---

## Python Quick Start

### Analyze Single Ticker

```python
from src.container import Container

container = Container.from_env()
result = container.analyzer.analyze_ticker("AAPL", expiration_days=7)

if result.is_ok():
    rec = result.unwrap()
    best = rec.strategies[0]
    print(f"{best.strategy_type.value}: ${best.max_profit.amount:.2f}")
```

### Custom Configuration

```python
from src.config.config import Config, ScoringWeights

config = Config.from_env()
config.strategy.scoring_weights = ScoringWeights(
    pop_weight=50.0,
    reward_risk_weight=30.0,
    vrp_weight=15.0,
    greeks_weight=5.0
)

container = Container(config)
```

### Collect Metrics

```python
from src.infrastructure.monitoring import MetricsCollector, JSONExporter

collector = MetricsCollector()
collector.increment("api.requests", labels={"endpoint": "vrp"})
collector.gauge("connections.active", 10)

with collector.timer("operation.duration.ms"):
    # Your code here
    pass

JSONExporter().export_to_file(collector.get_all_metrics(), Path("metrics.json"))
```

---

## Configuration

### Key Environment Variables

```bash
# Required
TRADIER_API_KEY=your_key
ALPHA_VANTAGE_KEY=your_key
DB_PATH=data/ivcrush.db

# VRP Thresholds
VRP_MIN_RATIO=1.2
VRP_STRONG_RATIO=1.5
VRP_EXCELLENT_RATIO=2.0

# Risk Management
RISK_BUDGET_PER_TRADE=20000
MAX_CONTRACTS=20
COMMISSION_PER_CONTRACT=0.65
```

---

## Common Patterns

### Safe Result Handling

```python
result = analyzer.analyze_ticker("AAPL", 7)

# Pattern 1: if/else
if result.is_ok():
    data = result.unwrap()
else:
    error = result.unwrap_err()
    print(f"Error: {error}")

# Pattern 2: map/unwrap_or
strategies = result.map(lambda r: r.strategies).unwrap_or([])

# Pattern 3: early return
data = result.ok_or_raise()  # Raises if error
```

### Batch Processing

```python
from concurrent.futures import ThreadPoolExecutor

def analyze(ticker):
    return container.analyzer.analyze_ticker(ticker, 7)

with ThreadPoolExecutor(max_workers=5) as executor:
    results = list(executor.map(analyze, tickers))
```

### Custom Scoring

```python
from src.domain.scoring import StrategyScorer

scorer = StrategyScorer(custom_weights)
scorer.score_strategies(strategies, vrp)
strategies.sort(key=lambda s: s.overall_score, reverse=True)
```

---

## File Locations

### Code Structure

```
2.0/
├── src/
│   ├── domain/              # Domain models & scoring
│   ├── application/         # Business logic
│   ├── infrastructure/      # External services
│   ├── config/              # Configuration
│   └── utils/               # Utilities
├── scripts/                 # CLI tools
├── tests/                   # Test suite
├── data/                    # Databases
└── docs/                    # Documentation
```

### Key Files

- **Config**: `src/config/config.py`
- **DI Container**: `src/container.py`
- **Analyzer**: `src/application/services/analyzer.py`
- **Strategy Gen**: `src/application/services/strategy_generator.py`
- **Scorer**: `src/domain/scoring/strategy_scorer.py`
- **Migrations**: `src/infrastructure/database/migrations/`
- **Monitoring**: `src/infrastructure/monitoring/`

---

## Metrics Reference

### Counter Metrics

```python
collector.increment("api.requests.total", labels={"endpoint": "vrp"})
collector.increment("trades.executed", labels={"strategy": "iron_condor"})
collector.increment("scan.success", labels={"ticker": "AAPL"})
```

### Gauge Metrics

```python
collector.gauge("connections.pool.active", 8)
collector.gauge("portfolio.value.usd", 152000)
collector.gauge("db.pool.saturation", 0.65)
```

### Histogram Metrics

```python
collector.histogram("api.latency.ms", 145.3)
collector.histogram("trade.profit.usd", 450.0)

# Get stats
stats = collector.get_histogram_stats("api.latency.ms")
# Returns: {count, min, max, mean, median, p95, p99}
```

### Timer Metrics

```python
with collector.timer("db.query.duration.ms"):
    results = execute_query()

with collector.timer("scan.ticker.duration.ms", labels={"ticker": "AAPL"}):
    analyze_ticker("AAPL")
```

---

## Scoring Weights

### Default Weights

```python
pop_weight: 45.0           # Probability of profit
reward_risk_weight: 20.0   # Reward/risk ratio
vrp_weight: 20.0           # VRP edge strength
greeks_weight: 10.0        # Theta/vega quality
size_weight: 5.0           # Position sizing
```

### Default Thresholds

```python
target_pop: 0.65                    # 65% POP target
target_rr: 0.30                     # 30% R/R target
target_vrp: 2.0                     # 2.0x VRP target
vrp_excellent_threshold: 2.0        # Excellent VRP
vrp_strong_threshold: 1.5           # Strong VRP
rr_favorable_threshold: 0.35        # Favorable R/R
pop_high_threshold: 0.70            # High POP
theta_positive_threshold: 30.0      # Positive theta
vega_beneficial_threshold: -50.0    # Beneficial vega
```

---

## Strategy Types

### Available Strategies

- `BULL_PUT_SPREAD` - Credit spread below price (bullish)
- `BEAR_CALL_SPREAD` - Credit spread above price (bearish)
- `IRON_CONDOR` - Put spread + Call spread (neutral, wide)
- `IRON_BUTTERFLY` - ATM straddle + OTM strangle (neutral, tight)

### Selection Logic

```python
# VRP >= 2.0 and neutral bias → Try Iron Butterfly
# VRP >= 1.5 and neutral bias → Iron Condor
# VRP >= 1.2 and bullish bias → Bull Put Spread
# VRP >= 1.2 and bearish bias → Bear Call Spread
```

---

## Troubleshooting

### Common Issues

**"No module named 'src'"**
```bash
# Add to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:/path/to/Trading Desk/2.0"
```

**"Database locked"**
```python
# Increase timeout or use connection pool
config.database.timeout = 60  # seconds
```

**"Migration failed"**
```bash
# Check status
python scripts/migrate.py status

# Rollback if needed
python scripts/migrate.py rollback 1
```

**"Connection pool exhausted"**
```python
# Increase pool size
pool = ConnectionPool(db_path, pool_size=10, max_overflow=20)
```

---

## Performance Tips

### Optimize Scans

```python
# Use connection pooling (automatic in Container)
container = Container(config)  # Pool enabled by default

# Batch process with progress bar
from tqdm import tqdm
for ticker in tqdm(tickers):
    analyze_ticker(ticker)

# Use multiprocessing for CPU-bound work
from concurrent.futures import ProcessPoolExecutor
with ProcessPoolExecutor(max_workers=4) as executor:
    results = executor.map(analyze, tickers)
```

### Cache Effectively

```python
# Configure cache TTLs
CACHE_L1_TTL_SECONDS = 3600      # 1 hour memory
CACHE_L2_TTL_SECONDS = 518400    # 6 days disk

# Use per-key TTLs
cache.set("key", value, ttl=7200)  # 2 hours
```

### Monitor Performance

```python
from src.infrastructure.monitoring import MetricsCollector

collector = MetricsCollector()

with collector.timer("operation.duration.ms"):
    # Your operation

stats = collector.get_histogram_stats("operation.duration.ms")
print(f"Mean: {stats['mean']:.1f}ms, P95: {stats['p95']:.1f}ms")
```

---

## Testing Commands

### Run Unit Tests

```bash
# Install pytest if needed
pip install pytest

# Run all tests
pytest tests/unit/

# Run specific test
pytest tests/unit/test_strategy_scorer.py -v

# Run with coverage
pytest --cov=src tests/
```

### Manual Testing

```python
# Test with mock data
from unittest.mock import Mock

mock_tradier = Mock()
mock_tradier.get_option_chain.return_value = Ok(mock_chain)

container.with_mock_tradier(mock_tradier)
```

---

## Useful Queries

### Check Recent Earnings

```sql
SELECT ticker, earnings_date, timing
FROM earnings_calendar
WHERE earnings_date >= date('now')
ORDER BY earnings_date
LIMIT 10;
```

### View Migration History

```sql
SELECT version, name, applied_at
FROM schema_migrations
ORDER BY version;
```

### Check Cache Stats

```sql
SELECT COUNT(*) as total,
       COUNT(CASE WHEN expiration > datetime('now') THEN 1 END) as valid
FROM cache;
```

---

## Links

- [Full Usage Examples](USAGE_EXAMPLES.md)
- [Architecture Decisions](adr/README.md)
- [Session Summary](../SESSION_SUMMARY.md)
- [P1 Improvements](../CHANGELOG_P1_IMPROVEMENTS.md)
- [P0 Refactoring](../CHANGELOG_REFACTORING.md)
