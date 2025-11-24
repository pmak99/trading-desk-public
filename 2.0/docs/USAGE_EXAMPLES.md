# IV Crush 2.0 - Usage Examples

This guide demonstrates how to use the IV Crush 2.0 trading system with examples for all major features.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Configuration](#configuration)
3. [Database Migrations](#database-migrations)
4. [Strategy Analysis](#strategy-analysis)
5. [Metrics Collection](#metrics-collection)
6. [Custom Scoring](#custom-scoring)
7. [Connection Pooling](#connection-pooling)
8. [Testing](#testing)

---

## Quick Start

### Basic Ticker Analysis

```python
from src.config.config import Config
from src.container import Container

# Initialize system
config = Config.from_env()
container = Container(config)

# Analyze a ticker
analyzer = container.analyzer
result = analyzer.analyze_ticker("AAPL", expiration_days=7)

if result.is_ok():
    recommendation = result.unwrap()
    print(f"Best Strategy: {recommendation.strategies[0].strategy_type.value}")
    print(f"Max Profit: ${recommendation.strategies[0].max_profit.amount:.2f}")
    print(f"Win Probability: {recommendation.strategies[0].probability_of_profit:.1%}")
else:
    print(f"Analysis failed: {result.unwrap_err()}")
```

### Batch Scanning

```python
from scripts.scan import main
import sys

# Scan multiple tickers
sys.argv = ['scan.py', '--tickers', 'AAPL,MSFT,GOOGL']
main()
```

---

## Configuration

### Using Environment Variables

Create a `.env` file (see `.env.example`):

```bash
# API Keys
TRADIER_API_KEY=your_key_here
ALPHA_VANTAGE_KEY=your_key_here

# Database
DB_PATH=data/ivcrush.db

# VRP Thresholds
VRP_MIN_RATIO=1.2
VRP_STRONG_RATIO=1.5
VRP_EXCELLENT_RATIO=2.0

# Risk Management
RISK_BUDGET_PER_TRADE=20000
MAX_CONTRACTS=20
```

### Programmatic Configuration

```python
from src.config.config import Config, DatabaseConfig, APIConfig, ScoringWeights

# Custom configuration
config = Config(
    database=DatabaseConfig(
        path=Path("data/custom.db"),
        timeout=60
    ),
    api=APIConfig(
        tradier_api_key="your_key",
        alpha_vantage_key="your_key"
    ),
    strategy=StrategyConfig(
        scoring_weights=ScoringWeights(
            pop_weight=50.0,      # Emphasize probability
            reward_risk_weight=15.0,
            vrp_weight=25.0,
            greeks_weight=10.0
        )
    )
)

container = Container(config)
```

---

## Database Migrations

### Check Migration Status

```bash
python scripts/migrate.py status
```

Output:
```
📊 Migration Status for ivcrush.db
   Database: data/ivcrush.db
   Current version: 2
   Latest version: 2
   Pending migrations: 0

✅ Applied Migrations:
     1. create_schema_migrations_table
     2. add_cache_expiration_column

✨ Database is up to date!
```

### Apply Migrations

```bash
# Apply all pending migrations
python scripts/migrate.py migrate

# Apply up to specific version
python scripts/migrate.py migrate 3
```

### Create New Migration

```bash
python scripts/migrate.py create add_user_settings_table
```

This generates a template you can add to `migration_manager.py`:

```python
self.migrations.append(Migration(
    version=3,
    name="add_user_settings_table",
    sql_up="""
        CREATE TABLE user_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    sql_down="DROP TABLE user_settings"
))
```

### Rollback Migration

```bash
# Rollback to version 1 (careful!)
python scripts/migrate.py rollback 1
```

---

## Strategy Analysis

### Analyze with VRP Threshold

```python
from src.container import Container
from src.config.config import Config

config = Config.from_env()
container = Container(config)

# Analyze ticker
analyzer = container.analyzer
result = analyzer.analyze_ticker(
    ticker="NVDA",
    expiration_days=7
)

if result.is_ok():
    rec = result.unwrap()

    # Check VRP quality
    print(f"VRP Ratio: {rec.vrp_ratio:.2f}x")

    if rec.vrp_ratio >= 2.0:
        print("✅ Excellent VRP edge!")
    elif rec.vrp_ratio >= 1.5:
        print("✅ Strong VRP")
    elif rec.vrp_ratio >= 1.2:
        print("⚠️  Marginal VRP")
    else:
        print("❌ Insufficient VRP edge")

    # Review strategies
    for i, strategy in enumerate(rec.strategies):
        print(f"\nStrategy {i+1}: {strategy.strategy_type.value}")
        print(f"  Score: {strategy.overall_score:.1f}/100")
        print(f"  Max Profit: ${strategy.max_profit.amount:.2f}")
        print(f"  Max Loss: ${strategy.max_loss.amount:.2f}")
        print(f"  POP: {strategy.probability_of_profit:.1%}")
        print(f"  Rationale: {strategy.rationale}")
```

### Generate Strategies Directly

```python
from src.container import Container
from src.config.config import Config

container = Container(Config.from_env())

# Get options data
tradier = container.tradier
chain_result = tradier.get_option_chain(
    symbol="TSLA",
    expiration_date="2024-12-20"
)

if chain_result.is_ok():
    option_chain = chain_result.unwrap()

    # Calculate VRP
    vrp_calc = container.vrp_calculator
    vrp = vrp_calc.calculate_vrp("TSLA", option_chain)

    # Generate strategies
    strategy_gen = container.strategy_generator
    recommendation = strategy_gen.generate_strategies(
        ticker="TSLA",
        option_chain=option_chain,
        vrp=vrp
    )

    print(f"Generated {len(recommendation.strategies)} strategies")
    print(f"Best: {recommendation.strategies[0].strategy_type.value}")
```

---

## Metrics Collection

### Basic Metrics Collection

```python
from src.infrastructure.monitoring import MetricsCollector, JSONExporter
from pathlib import Path

# Create collector
collector = MetricsCollector()

# Count operations
collector.increment("api.requests.total", labels={"endpoint": "vrp"})
collector.increment("api.requests.total", labels={"endpoint": "options"})

# Record values
collector.gauge("connections.pool.active", 8)
collector.gauge("connections.pool.total", 15)

# Time operations
with collector.timer("db.query.duration.ms"):
    # Your database query here
    pass

# Record histogram values
collector.histogram("api.latency.ms", 145.3)
collector.histogram("api.latency.ms", 98.7)
collector.histogram("api.latency.ms", 203.1)

# Export to JSON
exporter = JSONExporter()
exporter.export_to_file(
    collector.get_all_metrics(),
    Path("metrics/output.json")
)
```

### Prometheus Export

```python
from src.infrastructure.monitoring import MetricsCollector, PrometheusExporter
from pathlib import Path

collector = MetricsCollector()

# Collect metrics...
collector.increment("trades.executed.total", labels={"strategy": "iron_condor"})
collector.gauge("portfolio.value.usd", 152000)

# Export for Prometheus
exporter = PrometheusExporter()

# Define metric descriptions
descriptions = {
    "trades.executed.total": "Total number of trades executed",
    "portfolio.value.usd": "Current portfolio value in USD"
}

exporter.export_to_file(
    collector.get_all_metrics(),
    Path("/var/metrics/ivcrush.prom"),
    descriptions=descriptions
)
```

Output (`ivcrush.prom`):
```
# HELP trades_executed_total Total number of trades executed
# TYPE trades_executed_total counter
trades_executed_total{strategy="iron_condor"} 5

# HELP portfolio_value_usd Current portfolio value in USD
# TYPE portfolio_value_usd gauge
portfolio_value_usd 152000
```

### Integration with Scan

```python
from src.infrastructure.monitoring import get_global_collector
from src.container import Container

collector = get_global_collector()

# In your scan loop
for ticker in tickers:
    with collector.timer("scan.ticker.duration.ms", labels={"ticker": ticker}):
        result = analyzer.analyze_ticker(ticker, expiration_days=7)

        if result.is_ok():
            collector.increment("scan.success.total", labels={"ticker": ticker})
        else:
            collector.increment("scan.failure.total", labels={"ticker": ticker})

# Export at end
from src.infrastructure.monitoring import JSONExporter
exporter = JSONExporter()
exporter.export_to_file(collector.get_all_metrics(), Path("scan_metrics.json"))
```

---

## Custom Scoring

### Custom Scoring Weights

```python
from src.config.config import Config, ScoringWeights
from src.domain.scoring import StrategyScorer
from src.container import Container

# Create custom weights
custom_weights = ScoringWeights(
    # Emphasize reward/risk over probability
    pop_weight=30.0,
    reward_risk_weight=35.0,  # Increased from 20%
    vrp_weight=25.0,
    greeks_weight=10.0,
    size_weight=5.0,

    # Custom thresholds
    vrp_excellent_threshold=1.8,  # Lower threshold
    rr_favorable_threshold=0.40,   # Higher threshold
)

# Create scorer
scorer = StrategyScorer(custom_weights)

# Use with strategies
recommendation = strategy_gen.generate_strategies(...)
scorer.score_strategies(recommendation.strategies, vrp)

# Strategies are now scored with custom weights
print(f"Top strategy score: {recommendation.strategies[0].overall_score:.1f}")
```

### A/B Testing Scorers

```python
from src.domain.scoring import StrategyScorer
from src.config.config import ScoringWeights

# Strategy A: Conservative (emphasize POP)
weights_a = ScoringWeights(
    pop_weight=55.0,
    reward_risk_weight=15.0,
    vrp_weight=20.0,
    greeks_weight=10.0
)
scorer_a = StrategyScorer(weights_a)

# Strategy B: Aggressive (emphasize R/R)
weights_b = ScoringWeights(
    pop_weight=35.0,
    reward_risk_weight=35.0,
    vrp_weight=20.0,
    greeks_weight=10.0
)
scorer_b = StrategyScorer(weights_b)

# Compare
strategies_a = [...] # Copy of strategies
strategies_b = [...] # Copy of strategies

scorer_a.score_strategies(strategies_a, vrp)
scorer_b.score_strategies(strategies_b, vrp)

print(f"Conservative top: {strategies_a[0].strategy_type.value}")
print(f"Aggressive top: {strategies_b[0].strategy_type.value}")
```

---

## Connection Pooling

### Manual Pool Usage

```python
from src.infrastructure.database.connection_pool import ConnectionPool
from pathlib import Path

# Create pool
pool = ConnectionPool(
    db_path=Path("data/ivcrush.db"),
    pool_size=5,
    max_overflow=10,
    connection_timeout=30
)

# Use connection
with pool.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM earnings_calendar LIMIT 10")
    results = cursor.fetchall()

# Close pool when done
pool.close_all()
```

### Container Integration

```python
from src.container import Container
from src.config.config import Config

# Pool automatically created and managed
container = Container(Config.from_env())

# Use repositories (they use the pool automatically)
repo = container.earnings_repository
earnings = repo.get_upcoming_earnings(days_ahead=7)

# Pool automatically closed when container is reset
from src.container import reset_container
reset_container()
```

### Monitoring Pool Usage

```python
from src.infrastructure.monitoring import MetricsCollector

collector = MetricsCollector()

# In your repository methods
with pool.get_connection() as conn:
    collector.gauge("db.pool.connections.active", pool._total_connections)

    with collector.timer("db.query.duration.ms"):
        cursor.execute(query)
        results = cursor.fetchall()

# Track pool saturation
saturation = pool._total_connections / (pool.pool_size + pool.max_overflow)
collector.gauge("db.pool.saturation", saturation)

if saturation > 0.8:
    logger.warning(f"Connection pool saturation high: {saturation:.1%}")
```

---

## Testing

### Unit Testing with Mocks

```python
import pytest
from unittest.mock import Mock
from src.container import Container
from src.config.config import Config

def test_analyzer_with_mock_tradier():
    """Test analyzer with mocked Tradier API."""

    # Create container
    config = Config.from_env()
    container = Container(config, skip_validation=True, run_migrations=False)

    # Mock Tradier API
    mock_tradier = Mock()
    mock_tradier.get_option_chain.return_value = Ok(mock_option_chain)

    container.with_mock_tradier(mock_tradier)

    # Test analyzer
    analyzer = container.analyzer
    result = analyzer.analyze_ticker("TEST", expiration_days=7)

    assert result.is_ok()
    assert mock_tradier.get_option_chain.called
```

### Testing with Test Database

```python
from pathlib import Path
from src.container import Container
from src.config.config import Config, DatabaseConfig

def test_with_temporary_db(tmp_path):
    """Test with temporary database."""

    # Create test database
    test_db = tmp_path / "test.db"

    config = Config.from_env()
    config = Config(
        database=DatabaseConfig(path=test_db, timeout=30),
        api=config.api,
        strategy=config.strategy
    )

    container = Container(config)

    # Use container with test database
    repo = container.earnings_repository
    repo.save_earnings_event("TEST", "2024-12-20", "amc")

    # Verify
    events = repo.get_upcoming_earnings(days_ahead=30)
    assert len(events) == 1
```

### Testing Migrations

```python
from src.infrastructure.database.migrations import MigrationManager
from pathlib import Path

def test_migrations(tmp_path):
    """Test database migrations."""

    test_db = tmp_path / "test.db"
    manager = MigrationManager(test_db)

    # Check initial state
    assert manager.get_current_version() == 0

    # Apply migrations
    count = manager.migrate()
    assert count == 2  # Should apply migrations 1 and 2

    # Verify version
    assert manager.get_current_version() == 2

    # Migrations should be idempotent
    count = manager.migrate()
    assert count == 0  # No new migrations
```

---

## Advanced Usage

### Custom Strategy Filtering

```python
from src.container import Container

container = Container.from_env()
recommendation = container.analyzer.analyze_ticker("AAPL", 7).unwrap()

# Filter strategies by criteria
high_pop_strategies = [
    s for s in recommendation.strategies
    if s.probability_of_profit >= 0.75
]

high_rr_strategies = [
    s for s in recommendation.strategies
    if s.reward_risk_ratio >= 0.35
]

# Find best by specific criteria
best_by_profitability = max(
    recommendation.strategies,
    key=lambda s: s.profitability_score
)

best_by_safety = min(
    recommendation.strategies,
    key=lambda s: s.risk_score
)
```

### Batch Processing with Progress

```python
from tqdm import tqdm
from src.container import Container

container = Container.from_env()
analyzer = container.analyzer

tickers = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA"]
results = []

for ticker in tqdm(tickers, desc="Analyzing"):
    result = analyzer.analyze_ticker(ticker, expiration_days=7)
    if result.is_ok():
        results.append(result.unwrap())

# Sort by best overall score
results.sort(
    key=lambda r: r.strategies[0].overall_score,
    reverse=True
)

print(f"\nTop 3 opportunities:")
for i, rec in enumerate(results[:3], 1):
    best = rec.strategies[0]
    print(f"{i}. {rec.ticker}: {best.strategy_type.value}")
    print(f"   Score: {best.overall_score:.1f}, POP: {best.probability_of_profit:.1%}")
```

---

## Troubleshooting

### Enable Debug Logging

```python
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Now all debug messages will be shown
```

### Check Database Migrations

```bash
python scripts/migrate.py status
```

### Verify Configuration

```python
from src.config.config import Config
from src.config.validation import validate_configuration

config = Config.from_env()

try:
    validate_configuration(config)
    print("✅ Configuration valid")
except ValueError as e:
    print(f"❌ Configuration error: {e}")
```

### Connection Pool Issues

```python
from src.container import Container

container = Container.from_env()

# Check pool status
pool = container.db_pool
print(f"Pool size: {pool.pool_size}")
print(f"Max overflow: {pool.max_overflow}")
print(f"Active connections: {pool._total_connections}")

# If pool is stuck, reset container
from src.container import reset_container
reset_container()
```

---

## Next Steps

1. Review [SESSION_SUMMARY.md](../SESSION_SUMMARY.md) for complete refactoring details
2. Read [Architecture Decision Records](adr/README.md) for design rationale
3. Check [CHANGELOG_P1_IMPROVEMENTS.md](../CHANGELOG_P1_IMPROVEMENTS.md) for recent features
4. See [.env.example](../.env.example) for all configuration options

---

## Support

For issues or questions:
- Check [GitHub Issues](https://github.com/pmak99/trading-desk/issues)
- Review [docs/adr/](adr/) for architectural decisions
- See session documentation in repo root
