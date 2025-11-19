# Trading System 3.0

**MCP-Integrated IV Crush Earnings Strategy Platform**

## Overview

Version 3.0 evolves the trading system by integrating Model Context Protocol (MCP) servers as the primary data and reasoning layer. All existing functionality is preserved while adding:

- **Smarter decisions** via Sequential Thinking MCP
- **Strategy validation** via Composer backtesting
- **Earnings research** via Octagon transcripts
- **Paper trading** via Alpaca
- **Conversational access** to your trades and screening data

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture Overview](docs/ARCHITECTURE_OVERVIEW.md) | System design, MCP utilization, key decisions |
| [Implementation Plan](docs/IMPLEMENTATION_PLAN.md) | Technical details, code patterns, phased rollout |

## Quick Start

```bash
# Copy 2.0 codebase as foundation
cp -r ../2.0/src ./src
cp -r ../2.0/scripts ./scripts
cp ../2.0/trade.sh ./trade.sh

# Enable MCP integration
export USE_MCP=true

# Run existing modes (unchanged interface)
./trade.sh whisper
./trade.sh list AAPL,NVDA,TSLA
./trade.sh AAPL
```

## Key Features

- **Zero breaking changes** - All trade.sh modes work identically
- **Easy rollback** - Set `USE_MCP=false` to revert
- **Optimized for free tiers** - Aggressive caching (6hr for Alpha Vantage)
- **Conversational queries** - Ask Claude about your trades and screening

## MCP Servers

### External (7 servers)
- Alpha Vantage - Earnings calendar, prices, NEWS_SENTIMENT, technical indicators (RSI, BBANDS, ATR, MACD)
- Yahoo Finance - Market cap, history
- Sequential Thinking - Trade reasoning
- Octagon - Earnings transcripts
- Composer - Backtesting
- Alpaca - Paper trading
- Memory - Persistent preferences, ticker notes, strategy configs

### Custom (2 servers)
- trades-history - Query your positions
- screening-results - Query your analysis

## Status

**Planning Phase** - See implementation plan for phased rollout.
