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

## 4-Tier Liquidity System

| Tier | OI/Position | Spread | Score Pts | Action |
|------|-------------|--------|-----------|--------|
| **EXCELLENT** | ≥5x | ≤8% | 20 | Full size |
| **GOOD** | 2-5x | 8-12% | 16 | Full size |
| **WARNING** | 1-2x | 12-15% | 12 | Reduce size |
| **REJECT** | <1x | >15% | 4 | Do not trade |

*Final tier = worse of (OI tier, Spread tier)*

## Critical Rules

1. **Never trade REJECT liquidity** - learned from significant loss on WDAY/ZS/SYM
2. **VRP > 4x minimum** for full position sizing
3. **Prefer spreads over naked options** for defined risk
4. **Half-Kelly sizing** (0.25 fraction) for position sizing
5. **Always check liquidity score first** before evaluating VRP
6. **GOOD tier is tradeable** at full size (2-5x OI, 8-12% spread)

## API Priority Order

1. **Tradier** - Primary for options Greeks (IV, delta, theta, vega)
2. **Alpha Vantage** - Earnings calendar and dates
3. **Yahoo Finance** - Free fallback for prices and historical data

## Directory Structure

- `2.0/` - **CURRENT PRODUCTION** system (core VRP/strategy math)
- `4.0/` - AI sentiment layer (Perplexity, caching, budget tracking)
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
2. Check liquidity tier - REJECT is no-trade, WARNING reduce size, GOOD/EXCELLENT full size
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
- **finnhub** - News, earnings surprises, insider trades
- **perplexity** - AI sentiment analysis (budget: 40 calls/day, $5/month)

## 4.0 Slash Commands

| Command | Purpose |
|---------|---------|
| `/prime` | Pre-cache sentiment for today's earnings (run morning) |
| `/whisper` | Find most anticipated earnings with sentiment |
| `/analyze TICKER` | Deep dive with VRP + sentiment + strategies |
| `/alert` | Today's high-VRP opportunities |
| `/scan DATE` | Scan all earnings for specific date |
| `/collect TICKER` | Manually collect sentiment for backtesting |
| `/health` | System health check |

## 4.0 Scoring System

**2.0 Score:** VRP (55%) + Move Difficulty (25%) + Liquidity (20%)

**4.0 Score:** 2.0 Score × (1 + Sentiment Modifier)

| Sentiment | Modifier |
|-----------|----------|
| Strong Bullish | +12% |
| Bullish | +7% |
| Neutral | 0% |
| Bearish | -7% |
| Strong Bearish | -12% |

**Minimum Cutoffs:**
- 2.0 Score ≥ 50 (pre-sentiment)
- 4.0 Score ≥ 55 (post-sentiment)

## AI Sentiment Format

All sentiment queries return structured data:
```
Direction: [bullish/bearish/neutral]
Score: [-1 to +1]
Catalysts: [2-3 bullets, max 10 words each]
Risks: [1-2 bullets, max 10 words each]
```

## Design Principles

- **AI for discovery** (what to look at)
- **Math for trading** (how to trade it)
- Never let sentiment override VRP/liquidity rules
