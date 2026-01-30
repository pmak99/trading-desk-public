# 2.0 Core Math Engine

Production VRP calculations and strategy generation for IV Crush trading. This is the shared library that 4.0, 5.0, and 6.0 build upon.

## Quick Start

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -e .

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run
./trade.sh NVDA 2026-01-20      # Single ticker
./trade.sh scan 2026-01-20      # All earnings on date
./trade.sh whisper              # Most anticipated earnings
./trade.sh health               # System check
./trade.sh sync-cloud           # Sync with cloud + backup
```

## Commands

| Command | Description |
|---------|-------------|
| `./trade.sh TICKER DATE` | Analyze single ticker |
| `./trade.sh scan DATE` | Scan all earnings for date |
| `./trade.sh whisper [DATE]` | Most anticipated earnings |
| `./trade.sh sync-cloud` | Sync with cloud + backup to GDrive |
| `./trade.sh health` | System health check |

## VRP Calculation

```
VRP Ratio = Implied Move / Historical Mean Move
```

| Tier | VRP Ratio | Action |
|------|-----------|--------|
| EXCELLENT | >= 1.8x | High confidence, full size |
| GOOD | >= 1.4x | Tradeable |
| MARGINAL | >= 1.2x | Minimum edge, reduce size |
| SKIP | < 1.2x | No edge |

## Strategy Performance (2025 Verified Data)

| Strategy | Trades | Win Rate | Total P&L | Recommendation |
|----------|-------:|:--------:|----------:|----------------|
| **SINGLE** | 108 | **63.9%** | **+$103,390** | Preferred |
| SPREAD | 86 | 52.3% | +$51,472 | Good |
| STRANGLE | 6 | 33.3% | -$15,100 | Avoid |
| IRON_CONDOR | 3 | 66.7% | -$126,429 | Caution |

**Key insight:** SINGLE options outperform spreads in both win rate and total P&L.

## TRR Performance

| Level | Win Rate | P&L | Recommendation |
|-------|:--------:|----:|----------------|
| **LOW** (<1.5x) | **70.6%** | **+$52k** | Preferred |
| NORMAL (1.5-2.5x) | 56.5% | -$38k | Standard |
| HIGH (>2.5x) | 54.8% | -$123k | **Avoid** |

## Liquidity Tiers

| Tier | OI/Position | Spread | Action |
|------|-------------|--------|--------|
| EXCELLENT | >= 5x | <= 8% | Full size |
| GOOD | 2-5x | 8-12% | Full size |
| WARNING | 1-2x | 12-15% | Reduce size |
| REJECT | < 1x | > 15% | Never trade |

## Architecture

```
src/
├── domain/           # Value objects, protocols, enums
│   ├── models.py     # TickerAnalysis, Strategy, Greeks
│   └── enums.py      # LiquidityTier, VRPRecommendation
├── application/      # Business logic
│   ├── metrics/      # VRP calculator, liquidity scorer
│   └── sizing/       # Kelly criterion position sizing
├── infrastructure/   # External integrations
│   ├── tradier.py    # Options chains, Greeks
│   └── alphavantage.py  # Earnings calendar
└── container.py      # Dependency injection
```

## Database

SQLite database at `data/ivcrush.db`:

| Table | Records | Purpose |
|-------|---------|---------|
| historical_moves | 6,165 | Post-earnings price movements |
| strategies | 203 | Multi-leg strategy groupings |
| trade_journal | 556 | Individual option legs |
| position_limits | 417 | TRR-based position sizing |

### Enhanced Strategies Schema

```sql
CREATE TABLE strategies (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy_type TEXT CHECK(strategy_type IN ('SINGLE', 'SPREAD', 'IRON_CONDOR', 'STRANGLE')),
    gain_loss REAL,
    is_winner BOOLEAN,
    -- Trade tracking columns
    trade_type TEXT CHECK(trade_type IN ('NEW', 'ROLL', 'REPAIR', 'ADJUSTMENT')),
    parent_strategy_id INTEGER REFERENCES strategies(id),
    campaign_id TEXT,
    trr_at_entry REAL,
    position_limit_at_entry INTEGER
);
```

## Configuration

```bash
# Required
TRADIER_API_KEY=xxx
ALPHA_VANTAGE_KEY=xxx
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

## Related Systems

- **4.0/** - Adds AI sentiment on top of 2.0 math
- **5.0/** - Cloud autopilot using 2.0 calculations
- **6.0/** - Agent orchestration importing 2.0 directly

---

**Disclaimer:** For research purposes only. Not financial advice.
