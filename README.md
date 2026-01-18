# Trading Desk

Production options trading system for **IV Crush** strategies - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**2025 Performance:** 57.4% win rate | $155k YTD profit | 1.19 profit factor

## Systems

| Version | Purpose | Status |
|---------|---------|--------|
| [2.0](2.0/) | Core VRP math & strategy generation | Production |
| [4.0](4.0/) | AI sentiment layer (Perplexity) | Production |
| [5.0](5.0/) | Cloud autopilot (24/7 Cloud Run + Telegram) | Production |
| [6.0](6.0/) | Agent orchestration (parallel processing) | Production |

```
6.0 Agent Orchestration  ──┐
                           │
5.0 Cloud Autopilot  ──────┼──▶ Telegram, scheduled jobs, API
                           │
4.0 AI Sentiment  ─────────┼──▶ Perplexity sentiment, caching
                           │
2.0 Core Engine  ──────────┴──▶ VRP, liquidity, strategies
```

## Quick Start

```bash
# Local analysis (2.0)
cd 2.0
./trade.sh NVDA 2026-01-20      # Single ticker
./trade.sh scan 2026-01-20      # All earnings on date
./trade.sh whisper              # Most anticipated earnings

# Agent orchestration (6.0)
cd 6.0
./agent.sh prime                # Pre-cache sentiment
./agent.sh whisper              # Find opportunities (parallel)
./agent.sh analyze NVDA         # Deep dive

# Cloud API (5.0)
curl https://your-cloud-run-url.run.app/api/whisper -H "X-API-Key: $KEY"
```

## Core Strategy

### Volatility Risk Premium (VRP)

```
VRP Ratio = Implied Move / Historical Mean Move
```

| Tier | VRP Ratio | Action |
|------|-----------|--------|
| EXCELLENT | >= 1.8x | High confidence, full size |
| GOOD | >= 1.4x | Tradeable |
| MARGINAL | >= 1.2x | Minimum edge, reduce size |
| SKIP | < 1.2x | No edge |

### Scoring Weights

| Factor | Weight |
|--------|--------|
| VRP Edge | 55% |
| Move Difficulty | 25% |
| Liquidity Quality | 20% |

### Liquidity Tiers

| Tier | OI/Position | Spread | Action |
|------|-------------|--------|--------|
| EXCELLENT | >= 5x | <= 8% | Full size |
| GOOD | 2-5x | 8-12% | Full size |
| WARNING | 1-2x | 12-15% | Reduce size |
| REJECT | < 1x | > 15% | Never trade |

### Tail Risk Ratio (TRR)

Limits position size on volatile tickers (added after significant MU loss):

```
TRR = Max Historical Move / Average Historical Move
```

| Level | TRR | Max Contracts | Max Notional |
|-------|-----|---------------|--------------|
| HIGH | > 2.5x | 50 | $25,000 |
| NORMAL | <= 2.5x | 100 | $50,000 |

## Critical Rules

1. **Never trade REJECT liquidity** - learned from significant WDAY/ZS/SYM loss
2. **Respect TRR limits** - learned from significant December MU/AVGO loss
3. **VRP >= 1.8x (EXCELLENT)** for full position sizing
4. **Prefer spreads** over naked options for defined risk
5. **Check liquidity first** before evaluating VRP

## Database

| Table | Records | Purpose |
|-------|---------|---------|
| historical_moves | 5,675 | Post-earnings price movements |
| earnings_calendar | 6,305 | Upcoming earnings dates |
| trade_journal | 556 | Individual option legs |
| strategies | 221 | Multi-leg strategy groupings |
| position_limits | 417 | TRR-based position sizing limits |

**Locations:**
- Local: `2.0/data/ivcrush.db`
- Cloud: `gs://your-gcs-bucket/ivcrush.db`

**Sync:** `./trade.sh sync-cloud` from 2.0/

## Environment

```bash
# Required
TRADIER_API_KEY=xxx           # Options chains, Greeks
ALPHA_VANTAGE_KEY=xxx         # Earnings calendar
PERPLEXITY_API_KEY=xxx        # AI sentiment

# Optional (5.0 Cloud)
TELEGRAM_BOT_TOKEN=xxx        # Bot notifications
TELEGRAM_CHAT_ID=xxx          # Chat ID
API_KEY=xxx                   # API authentication
```

## Testing

```bash
cd 2.0 && ./venv/bin/python -m pytest tests/ -v    # 514 tests
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/  # 186 tests
cd 5.0 && ../2.0/venv/bin/python -m pytest tests/  # 193 tests
cd 6.0 && ../2.0/venv/bin/python -m pytest tests/  # 48 tests
```

## Archived Systems

| Version | Status | Reason |
|---------|--------|--------|
| 1.0 | Deprecated | Superseded by 2.0's DDD architecture |
| 3.0 | Paused | ML direction prediction showed no edge (54% vs 57.4% baseline) |

See [archive/](archive/) for details.

## License

Private - Internal use only

---

**Disclaimer:** For research purposes only. Not financial advice. Options trading involves substantial risk.
