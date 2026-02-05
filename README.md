# Trading Desk

Production options trading system for **IV Crush** strategies - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

**2025 Performance:** ~55-60% win rate | 203 strategies | positive net P&L net P&L

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

### Strategy Performance (2025 Verified)

| Strategy | Trades | Win Rate | P&L | Recommendation |
|----------|-------:|:--------:|----:|----------------|
| **SINGLE** | 108 | ~60-65% | +$103k | **Preferred** |
| SPREAD | 86 | ~50-55% | +$51k | Good |
| STRANGLE | 6 | ~33% | -$15k | Avoid |
| IRON_CONDOR | 3 | ~67% | -$126k | Caution |

### Tail Risk Ratio (TRR)

| Level | TRR | Performance | Action |
|-------|-----|-------------|--------|
| **LOW** | < 1.5x | **~70% win, strong profit** | Preferred |
| NORMAL | 1.5-2.5x | ~57% win, moderate loss | Standard |
| HIGH | > 2.5x | ~55% win, significant loss | **Avoid** |

### Trade Adjustments

| Type | Success Rate | Recommendation |
|------|:------------:|----------------|
| NEW | 58.2% | Standard trading |
| REPAIR | 20% campaigns | Damage control only |
| ROLL | **0%** | **Never roll** |

## Critical Rules

1. **Prefer SINGLE options** - 64% vs 52% win rate vs spreads
2. **Respect TRR limits** - LOW TRR: strong profit, HIGH TRR: significant loss
3. **Never roll** - 0% success rate, always makes losses worse
4. **Reduce size for REJECT liquidity** - allowed but penalized in scoring
5. **Cut losses early** - don't try to "fix" losing trades

## Database

| Table | Records | Purpose |
|-------|---------|---------|
| historical_moves | 6,165 | Post-earnings price movements |
| strategies | 203 | Grouped trades with P&L tracking |
| trade_journal | 556 | Individual option legs |
| position_limits | 417 | TRR-based position sizing |

**Locations:**
- Local: `2.0/data/ivcrush.db`
- Cloud: `gs://your-gcs-bucket/ivcrush.db`

**Sync:** `./trade.sh sync-cloud` from 2.0/

## Environment

```bash
TRADIER_API_KEY=xxx           # Options chains, Greeks
ALPHA_VANTAGE_KEY=xxx         # Earnings calendar
TWELVE_DATA_KEY=xxx           # Historical prices
```

## Testing

```bash
cd 2.0 && ./venv/bin/python -m pytest tests/ -v    # 690 tests
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/  # 221 tests
cd 5.0 && ../2.0/venv/bin/python -m pytest tests/  # 308 tests
cd 6.0 && ../2.0/venv/bin/python -m pytest tests/  # 48 tests
```

## Archived Systems

| Version | Status | Reason |
|---------|--------|--------|
| 1.0 | Deprecated | Superseded by 2.0's DDD architecture |
| 3.0 | Paused | ML direction prediction showed no edge |

---

**Disclaimer:** For research purposes only. Not financial advice. Options trading involves substantial risk.
