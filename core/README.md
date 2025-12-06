# IV Crush 2.0

Earnings options trading system using Volatility Risk Premium (VRP). Sell options when implied volatility exceeds historical moves, profit from IV crush after earnings.

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
| `./trade.sh list TICKERS DATE` | Analyze multiple tickers |
| `./trade.sh scan DATE` | Scan all earnings for date |
| `./trade.sh whisper [DATE]` | Most anticipated earnings |
| `./trade.sh sync [--dry-run]` | Sync earnings calendar |
| `./trade.sh health` | System health check |

## Output

```
TRADEABLE OPPORTUNITY
VRP Ratio: 2.26x -> EXCELLENT
Implied Move: 8.00% | Historical Mean: 3.54%

RECOMMENDED: BULL PUT SPREAD
  Short $177.50P / Long $170.00P
  Credit: $2.20 | Max Profit: $8,158 (37 contracts)
  Probability: 69.1% | Theta: +$329/day
```

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
│   ├── domain/          # Value objects, protocols
│   ├── application/     # VRP calculator, strategy generator
│   ├── infrastructure/  # API clients, database
│   └── config/          # Configuration
├── scripts/
│   ├── scan.py          # Main analysis
│   ├── analyze.py       # Single ticker analysis
│   └── health_check.py  # Health verification
├── tests/               # Unit and integration tests
├── data/ivcrush.db      # Historical moves database
└── trade.sh             # CLI entry point
```

## Testing

```bash
pytest tests/
```

---

**Disclaimer:** For research purposes only. Not financial advice. Options trading involves substantial risk.
