# 2.0 Core Math Engine

Production VRP calculations and strategy generation for IV Crush trading. This is the foundation that 4.0, 5.0, and 6.0 build upon.

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
```

## Commands

| Command | Description |
|---------|-------------|
| `./trade.sh TICKER DATE` | Analyze single ticker |
| `./trade.sh list TICKERS DATE` | Analyze multiple (comma-separated) |
| `./trade.sh scan DATE` | Scan all earnings for date |
| `./trade.sh whisper [DATE]` | Most anticipated earnings |
| `./trade.sh sync [--dry-run]` | Refresh earnings calendar |
| `./trade.sh sync-cloud` | Sync with cloud + backup |
| `./trade.sh health` | System health check |

## VRP Calculation

```
VRP Ratio = Implied Move / Historical Mean Move
```

The implied move comes from ATM straddle pricing via Tradier. Historical mean uses intraday moves (open-to-close on earnings day).

| Tier | VRP Ratio | Action |
|------|-----------|--------|
| EXCELLENT | >= 1.8x | High confidence, full size |
| GOOD | >= 1.4x | Tradeable |
| MARGINAL | >= 1.2x | Minimum edge, reduce size |
| SKIP | < 1.2x | No edge |

*BALANCED mode (default). Set `VRP_THRESHOLD_MODE=LEGACY` for 7x/4x/1.5x thresholds.*

## Scoring Weights

| Factor | Weight | Description |
|--------|--------|-------------|
| VRP Edge | 55% | Core signal quality |
| Move Difficulty | 25% | Easier moves score higher |
| Liquidity Quality | 20% | OI and spread quality |

## Liquidity Tiers

| Tier | OI/Position | Spread | Score | Action |
|------|-------------|--------|-------|--------|
| EXCELLENT | >= 5x | <= 8% | 20 | Full size |
| GOOD | 2-5x | 8-12% | 16 | Full size |
| WARNING | 1-2x | 12-15% | 12 | Reduce size |
| REJECT | < 1x | > 15% | 4 | Never trade |

Final tier = worse of (OI tier, Spread tier)

## Output Example

```
TRADEABLE OPPORTUNITY
VRP Ratio: 2.26x -> EXCELLENT
Implied Move: 8.00% | Historical Mean: 3.54%
Liquidity Tier: EXCELLENT

RECOMMENDED: BULL PUT SPREAD
  Short $177.50P / Long $170.00P
  Credit: $2.20 | Max Profit: $8,158 (37 contracts)
  Probability: 69.1% | Theta: +$329/day
```

## Architecture

```
src/
├── domain/           # Value objects, protocols, enums
│   ├── models.py     # TickerAnalysis, Strategy, Greeks
│   ├── enums.py      # LiquidityTier, VRPRecommendation
│   └── protocols.py  # Repository interfaces
├── application/      # Business logic
│   ├── metrics/      # VRP calculator, liquidity scorer
│   ├── services/     # Analyzer, strategy generator
│   └── sizing/       # Kelly criterion position sizing
├── infrastructure/   # External integrations
│   ├── tradier.py    # Options chains, Greeks
│   ├── alphavantage.py  # Earnings calendar
│   └── yahoo.py      # Price fallback
└── config/           # Configuration, thresholds
```

## Database

SQLite database at `data/ivcrush.db`:

| Table | Records | Purpose |
|-------|---------|---------|
| historical_moves | 5,675 | Post-earnings price movements |
| earnings_calendar | 6,305 | Upcoming earnings dates |
| trade_journal | 556 | Individual option legs |
| strategies | 221 | Multi-leg strategy groupings |
| position_limits | 417 | TRR-based position sizing limits |

### Key Queries

```sql
-- VRP analysis for ticker
SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct
FROM historical_moves WHERE ticker = 'NVDA'
ORDER BY earnings_date DESC LIMIT 12;

-- Strategy-level performance
SELECT strategy_type, COUNT(*) trades,
       ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) win_rate,
       ROUND(SUM(gain_loss), 2) pnl
FROM strategies GROUP BY strategy_type;
```

## Configuration

```bash
# Required
TRADIER_API_KEY=xxx
ALPHA_VANTAGE_KEY=xxx
DB_PATH=data/ivcrush.db

# Optional
VRP_THRESHOLD_MODE=BALANCED   # CONSERVATIVE, BALANCED, AGGRESSIVE, LEGACY
USE_KELLY_SIZING=true
KELLY_FRACTION=0.25
```

## API Priority

1. **Tradier** - Options Greeks (IV, delta, theta, vega)
2. **Alpha Vantage** - Earnings calendar
3. **Yahoo Finance** - Price fallback only

## Testing

```bash
./venv/bin/python -m pytest tests/ -v           # All 514 tests
./venv/bin/python -m pytest tests/unit/ -v      # Unit tests only
./venv/bin/python -m pytest tests/ --cov=src    # With coverage
```

Key test files:
- `test_calculators.py` - VRP and implied move calculations
- `test_liquidity_scorer.py` - 4-tier liquidity system
- `test_kelly_sizing.py` - Position sizing
- `test_consistency_enhanced.py` - Move pattern analysis

## Freshness Validation

When analyzing tickers with earnings <= 7 days away, the system validates cached dates against Alpha Vantage if cache is > 24h old. This catches date changes automatically.

## Related Systems

- **4.0/** - Adds AI sentiment on top of 2.0 math
- **5.0/** - Cloud autopilot using 2.0 calculations
- **6.0/** - Agent orchestration importing 2.0 directly

---

**Disclaimer:** For research purposes only. Not financial advice.
