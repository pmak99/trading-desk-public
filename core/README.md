# IV Crush 2.0

Production options trading system using Volatility Risk Premium (VRP). Sells options before earnings when implied volatility exceeds historical moves, profiting from IV crush after announcements.

**Live Performance (2025):** 57.4% win rate, $261k YTD profit, 1.19 profit factor

## Quick Start

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure .env
TRADIER_API_KEY=your_key
ALPHA_VANTAGE_KEY=your_key
DB_PATH=data/ivcrush.db

# Run
./trade.sh NVDA 2025-12-10
./trade.sh scan 2025-12-10
./trade.sh whisper
./trade.sh health
```

## Commands

| Command | Description |
|---------|-------------|
| `./trade.sh TICKER DATE` | Analyze single ticker |
| `./trade.sh list TICKERS DATE` | Analyze multiple tickers (comma-separated) |
| `./trade.sh scan DATE` | Scan all earnings for date |
| `./trade.sh whisper [DATE]` | Most anticipated earnings |
| `./trade.sh sync [--dry-run]` | Sync earnings calendar |
| `./trade.sh health` | System health check |

**Freshness Validation:** When analyzing tickers with earnings ≤7 days away, the system validates cached dates against Alpha Vantage if the cache is >24h old. This catches date changes automatically.

## VRP Thresholds

The core edge comes from VRP - the ratio of implied move to historical average:

| Tier | VRP Ratio | Action |
|------|-----------|--------|
| EXCELLENT | >= 1.8x | High confidence, full size |
| GOOD | >= 1.4x | Tradeable |
| MARGINAL | >= 1.2x | Minimum edge, size down |
| SKIP | < 1.2x | No edge |

*BALANCED mode (default). Set `VRP_THRESHOLD_MODE=LEGACY` for 7x/4x/1.5x thresholds.*

## Scoring Weights (Dec 2025)

| Factor | Weight | Description |
|--------|--------|-------------|
| POP | 40% | Probability of profit |
| Liquidity | 22% | Open interest, bid-ask spreads |
| VRP Edge | 17% | Core signal quality |
| Kelly Edge | 13% | Risk/reward × probability |
| Greeks | 8% | Theta/vega quality |

## Critical Rules

1. **Never trade REJECT liquidity** - learned from significant loss
2. **VRP ≥ 1.8x (EXCELLENT tier)** for full position sizing
3. **Prefer spreads over naked options** for defined risk
4. **Half-Kelly sizing** (0.25 fraction)
5. **Always check liquidity first** before evaluating VRP

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

## 4-Tier Liquidity System

| Tier | OI/Position | Spread | Score Pts | Action |
|------|-------------|--------|-----------|--------|
| **EXCELLENT** | ≥5x | ≤8% | 20 | Full size |
| **GOOD** | 2-5x | 8-12% | 16 | Full size |
| **WARNING** | 1-2x | 12-15% | 12 | Reduce size |
| **REJECT** | <1x | >15% | 4 | Do not trade |

*Final tier = worse of (OI tier, Spread tier)*

## Claude Code Skills

Slash commands available when using Claude Code:

| Command | Description |
|---------|-------------|
| `/analyze TICKER` | Deep analysis with VRP + sentiment |
| `/scan DATE` | Scan all earnings for specific date |
| `/whisper` | Most anticipated earnings with sentiment |
| `/prime` | Pre-cache sentiment (run morning) |
| `/alert` | Today's high-VRP opportunities |
| `/backtest` | Generate backtest performance report |
| `/history TICKER` | ASCII charts of historical moves |
| `/journal` | Parse Fidelity PDF statements |
| `/maintenance` | System housekeeping tasks |
| `/collect TICKER` | Manually collect sentiment |
| `/backfill` | Record post-earnings outcomes |
| `/health` | System health check |

## Configuration

Environment variables in `.env`:

```bash
# Required
TRADIER_API_KEY=your_key
ALPHA_VANTAGE_KEY=your_key

# Optional
VRP_THRESHOLD_MODE=BALANCED  # CONSERVATIVE, BALANCED, AGGRESSIVE
USE_KELLY_SIZING=true
KELLY_FRACTION=0.25
```

## Project Structure

```
2.0/
├── src/
│   ├── domain/          # Value objects, protocols, enums
│   ├── application/     # VRP calculator, strategy generator
│   ├── infrastructure/  # API clients (Tradier, Alpha Vantage)
│   └── config/          # Configuration and thresholds
├── scripts/
│   ├── scan.py          # Main scanning logic
│   ├── analyze.py       # Single ticker deep analysis
│   └── health_check.py  # System verification
├── tests/               # Unit and integration tests
├── data/ivcrush.db      # Historical moves (5,070 records, 398 tickers)
└── trade.sh             # CLI entry point
```

## API Priority

1. **Tradier** - Primary for options Greeks (IV, delta, theta, vega)
2. **Alpha Vantage** - Earnings calendar and dates
3. **Yahoo Finance** - Free fallback for prices and historical data

## Testing

```bash
pytest tests/                    # Run all tests
pytest tests/unit/ -v           # Unit tests with verbose output
pytest tests/ --tb=short        # With short tracebacks
```

**Test Coverage (Dec 2025):** 521 tests pass, 36% code coverage

Key test files:
- `test_calculators.py` - VRP and implied move calculations
- `test_liquidity_scorer.py` - 4-tier liquidity system
- `test_kelly_sizing.py` - EV-based position sizing
- `test_consistency_enhanced.py` - Move pattern analysis

---

**Disclaimer:** For research purposes only. Not financial advice. Options trading involves substantial risk.
