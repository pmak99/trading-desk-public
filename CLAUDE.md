# Trading Desk - IV Crush System

## Project Overview

Production quantitative options trading system focused on **IV Crush** strategy - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**Live Performance (2025):** 57.4% win rate, $261k YTD profit, 1.19 profit factor

## Primary Strategy: Volatility Risk Premium (VRP)

The core edge comes from VRP - the ratio of implied move to historical average move:

```
VRP Ratio = Implied Move / Historical Mean Move
```

**VRP Thresholds:**
- EXCELLENT: >= 7.0x (top tier, high confidence)
- GOOD: >= 4.0x (tradeable with caution)
- MARGINAL: >= 1.5x (minimum edge, size down)
- SKIP: < 1.5x (no edge)

## Scoring System (Current Weights)

| Factor | Weight | Description |
|--------|--------|-------------|
| VRP Edge | 55% | Core signal quality |
| Implied Move Difficulty | 25% | Easier moves get bonus |
| Liquidity Quality | 20% | Open interest, bid-ask spreads |

## Critical Rules

1. **Never trade REJECT liquidity** - learned from significant loss on WDAY/ZS/SYM
2. **VRP > 4x minimum** for full position sizing
3. **Prefer spreads over naked options** for defined risk
4. **Half-Kelly sizing** (0.25 fraction) for position sizing
5. **Always check liquidity score first** before evaluating VRP

## API Priority Order

1. **Tradier** - Primary for options Greeks (IV, delta, theta, vega)
2. **Alpha Vantage** - Earnings calendar and dates
3. **Yahoo Finance** - Free fallback for prices and historical data

## Directory Structure

- `2.0/` - **CURRENT PRODUCTION** system (use this)
- `3.0/` - ML enhancement project (in development)
- `1.0/` - Deprecated original system

## Common Commands

```bash
# From 2.0/ directory
./trade.sh TICKER YYYY-MM-DD      # Single ticker analysis
./trade.sh scan YYYY-MM-DD        # Scan all earnings for date
./trade.sh whisper                # Most anticipated earnings
./trade.sh sync                   # Refresh earnings calendar
./trade.sh health                 # System health check
```

## Key Files

| File | Purpose |
|------|---------|
| `2.0/trade.sh` | Main CLI entry point |
| `2.0/scripts/scan.py` | Ticker scanning logic |
| `2.0/scripts/analyze.py` | Single ticker deep analysis |
| `2.0/src/application/metrics/vrp.py` | VRP calculation |
| `2.0/src/domain/scoring/strategy_scorer.py` | Composite scoring |
| `2.0/src/application/metrics/liquidity_scorer.py` | Liquidity analysis |
| `2.0/data/ivcrush.db` | Historical moves database (4,926 records) |

## Strategy Types Generated

| Strategy | Risk Level | When to Use |
|----------|------------|-------------|
| Naked Calls/Puts | High | Excellent VRP + acceptable liquidity |
| Bull/Bear Spreads | Medium | Balanced risk/reward |
| Iron Condors | Low | Conservative, neutral bias |
| Strangles/Straddles | Variable | Neutral directional bets |

## When Analyzing Trades

1. Run health check first (`./trade.sh health`)
2. Check liquidity tier - reject if WARNING or REJECT
3. Verify VRP ratio meets threshold (>4x preferred)
4. Review implied vs historical move spread
5. Check POP (probability of profit) - target 60%+
6. Validate theta decay is positive
7. Consider position sizing via Half-Kelly

## Database Schema

Main table: `historical_moves`
- `ticker`, `earnings_date`, `close_before`, `close_after`
- `actual_move_pct`, `direction` (UP/DOWN)
- Used to calculate historical average moves per ticker

## Environment Variables Required

```bash
TRADIER_API_KEY=xxx      # Options data
ALPHA_VANTAGE_KEY=xxx    # Earnings calendar
DB_PATH=data/ivcrush.db  # Database location
```

## MCP Servers Available

- **alphavantage** - Earnings, fundamentals, economic data
- **yahoo-finance** - Stock history and quotes
- **alpaca** - Paper trading account, positions, orders
- **memory** - Knowledge graph for context
- **gemini** - AI assistance
- **finnhub** - News, earnings surprises, insider trades (if configured)
