# 6.0 Agent Orchestration

Agent-based orchestration layer with parallel processing and intelligent automation. Coordinates multiple specialist agents working concurrently on top of the 2.0/4.0 stack.

## Strategy Performance (2025 Verified Data)

| Strategy | Trades | Win Rate | Total P&L | Recommendation |
|----------|-------:|:--------:|----------:|----------------|
| **SINGLE** | 108 | **~60-65%** | **strongest performer** | Preferred |
| SPREAD | 86 | ~50-55% | positive | Good |
| STRANGLE | 6 | ~33% | negative | Avoid |
| IRON_CONDOR | 3 | ~67% | significant loss (sizing) | Caution |

**Key insight:** SINGLE options outperform spreads in both win rate and total P&L.

## What 6.0 Adds

| Feature | Before (4.0) | After (6.0) |
|---------|--------------|-------------|
| Processing | Sequential | Parallel (2x faster) |
| Intelligence | Single-ticker | Cross-ticker correlation |
| Anomaly Detection | Manual | Automated guardrails |
| Explanations | Basic | Narrative reasoning |
| Patterns | None | Historical pattern mining |

## Quick Start

```bash
cd 6.0

# Show all commands
./agent.sh help

# Daily workflow
./agent.sh maintenance health   # Verify systems
./agent.sh prime                # Pre-cache sentiment
./agent.sh whisper              # Find opportunities (parallel)
./agent.sh analyze NVDA         # Deep dive
```

**Prerequisites:**
- Python 3.11+
- 2.0 virtual environment (shared)
- API keys auto-loaded from `2.0/.env` and `5.0/.env`

## Commands

| Command | Description |
|---------|-------------|
| `./agent.sh help` | Show all commands and options |
| `./agent.sh prime [DATE]` | Pre-cache sentiment for upcoming earnings |
| `./agent.sh whisper [DATE]` | Find most anticipated earnings (parallel) |
| `./agent.sh analyze TICKER [DATE]` | Deep dive with patterns, TRR, sentiment |
| `./agent.sh maintenance health` | System health check |
| `./agent.sh maintenance data-quality` | Database integrity scan |
| `./agent.sh maintenance data-quality --fix` | Auto-fix safe issues |
| `./agent.sh maintenance sector-sync` | Sync sector data from Finnhub |
| `./agent.sh maintenance cache-cleanup` | Clear expired cache |

## Critical Rules

1. **Prefer SINGLE options** - 64% vs 52% win rate vs spreads
2. **Respect TRR limits** - LOW TRR: strong profit, HIGH TRR: significant loss
3. **Never roll** - 0% success rate, always makes losses worse
4. **Never trade REJECT liquidity**
5. **Cut losses early** - don't try to "fix" losing trades

## TRR Performance

| Level | Win Rate | P&L | Recommendation |
|-------|:--------:|----:|----------------|
| **LOW** (<1.5x) | **70.6%** | **strong profit** | Preferred |
| NORMAL (1.5-2.5x) | 56.5% | moderate loss | Standard |
| HIGH (>2.5x) | 54.8% | significant loss | **Avoid** |

## Recommendation Logic

| VRP | Liquidity | Action |
|-----|-----------|--------|
| >= 1.8x | EXCELLENT/GOOD | TRADE |
| >= 1.4x | EXCELLENT/GOOD | TRADE_CAUTIOUSLY |
| >= 1.4x | WARNING | TRADE_CAUTIOUSLY (reduce size) |
| < 1.4x | Any | SKIP (insufficient VRP) |
| Any | REJECT | SKIP (liquidity) |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   6.0 AGENT ORCHESTRATION                    │
├─────────────────────────────────────────────────────────────┤
│ Orchestrators:                                              │
│   PrimeOrchestrator      - Parallel sentiment pre-caching   │
│   WhisperOrchestrator    - Parallel ticker analysis         │
│   AnalyzeOrchestrator    - Multi-specialist deep dive       │
│   MaintenanceOrchestrator - Background operations           │
├─────────────────────────────────────────────────────────────┤
│ Worker Agents:                                              │
│   TickerAnalysisAgent    - VRP + liquidity + TRR            │
│   SentimentFetchAgent    - Perplexity API integration       │
│   HealthCheckAgent       - System monitoring                │
│   ExplanationAgent       - Narrative reasoning              │
│   AnomalyDetectionAgent  - Edge case detection              │
│   PatternRecognitionAgent - Historical pattern mining       │
│   SectorFetchAgent       - Finnhub sector data              │
│   DataQualityAgent       - Automated data fixes             │
├─────────────────────────────────────────────────────────────┤
│ Intelligence:                                               │
│   Cross-ticker correlation (real sector data)               │
│   Anomaly detection (conflicting signals)                   │
│   TRR-based position sizing                                 │
│   Historical pattern recognition                            │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│          EXISTING LAYERS (2.0, 4.0 unchanged)               │
│   4.0: Sentiment caching, budget tracking                   │
│   2.0: VRP math, strategy generation                        │
└─────────────────────────────────────────────────────────────┘
```

## Design Principles

1. **Reuse, Don't Duplicate** - Imports 2.0/4.0 via sys.path, zero code duplication
2. **Parallel by Default** - Orchestrators spawn agents concurrently
3. **Stateless Workers** - Agents communicate via JSON
4. **Fail-Safe Guardrails** - Anomaly detection catches edge cases
5. **Budget-Aware** - Respects 4.0's limits (40 calls/day)

## Performance

| Command | Time | Notes |
|---------|------|-------|
| `/prime` (30 tickers) | ~10s | 9x faster than sequential |
| `/whisper` (30 tickers) | ~90s | 2x faster than 4.0 |
| `/analyze` (single) | ~60s | Full deep dive |

## Output Examples

### /whisper

```
## High-VRP Opportunities (Next 5 Days)

| Ticker | Date | VRP | Score | Liquidity | Direction |
|--------|------|-----|-------|-----------|-----------|
| NFLX | 01/22 | 2.1x | 78 | GOOD | bullish |
| NVDA | 01/23 | 1.9x | 75 | EXCELLENT | bullish |
| MU | 01/24 | 2.4x | 72 | GOOD | neutral | ⚠️ HIGH TRR (max 50)

⚠️ Sector concentration: 3 Technology tickers
```

### /analyze

```
## NFLX Analysis (Earnings: 2026-01-22)

### VRP Analysis
VRP Ratio: 2.1x -> EXCELLENT
Implied Move: 8.5% | Historical Mean: 4.0%

### Liquidity
Tier: GOOD | OI: 3.2x position | Spread: 9%

### Sentiment
Direction: bullish | Score: 0.65
Catalysts: Strong subscriber growth, Ad tier momentum
Risks: Competition, Content costs

### Position Limits
TRR: 1.8x (NORMAL)
Max Contracts: 100 | Max Notional: $50,000

### Historical Patterns
Directional Bias: 58% UP (last 12 earnings)
Recent Streak: 3 wins
Magnitude Trend: Stable

### Strategies
bull_put_spread: POP 75% | Max Profit: $520 | Max Loss: $480
iron_condor: POP 62% | Max Profit: $380 | Max Loss: $620
```

## Directory Structure

```
6.0/
├── agent.sh                    # CLI entry point
├── src/
│   ├── orchestrators/          # Prime, Whisper, Analyze, Maintenance
│   ├── agents/                 # All worker agents
│   ├── integration/            # Container2_0, Cache4_0, Perplexity5_0
│   ├── utils/                  # Schemas, formatters, timeouts
│   └── cli/                    # CLI wrappers
├── config/
│   └── agents.yaml             # Agent timeouts and models
└── tests/                      # 48 tests
```

## Configuration

6.0 inherits all configuration from 2.0 and 4.0:

```bash
# From 2.0
TRADIER_API_KEY=xxx
ALPHA_VANTAGE_KEY=xxx
DB_PATH=data/ivcrush.db

# From 5.0
PERPLEXITY_API_KEY=xxx
```

### Timeouts

| Agent | Timeout | Rationale |
|-------|---------|-----------|
| TickerAnalysisAgent | 30s | 2.0 analysis is fast |
| SentimentFetchAgent | 30s | Perplexity typically < 10s |
| ExplanationAgent | 30s | Narrative generation |
| AnomalyDetectionAgent | 20s | Fast validation |
| HealthCheckAgent | 10s | Simple connectivity |

## Testing

```bash
cd 6.0
../2.0/venv/bin/python -m pytest tests/ -v    # 48 tests
```

## Integration with 2.0/4.0

6.0 imports existing systems via sys.path injection:

```python
# Container2_0 handles namespace collision
from src.container import get_container

class Container2_0:
    def __init__(self):
        self.container = get_container()
```

**Git Worktree Support:** All integration code handles worktrees correctly via `git rev-parse --git-common-dir`.

## Related Systems

- **2.0/** - Core VRP math (imported directly)
- **4.0/** - Sentiment cache (used via Cache4_0)
- **5.0/** - Cloud alternative (independent)

---

**Disclaimer:** For research purposes only. Not financial advice.
