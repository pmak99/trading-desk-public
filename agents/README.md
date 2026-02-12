# agents Agent Orchestration

Agent-based orchestration layer with parallel processing. Coordinates multiple specialist agents working concurrently on top of the core/sentiment stack.

## What agents Adds

| Feature | Before (4.0) | After (6.0) |
|---------|--------------|-------------|
| Processing | Sequential | Parallel (2x faster) |
| Intelligence | Single-ticker | Cross-ticker correlation |
| Anomaly Detection | Manual | Automated guardrails |
| Explanations | Basic | Narrative reasoning |
| Patterns | None | Historical pattern mining |

## Quick Start

```bash
cd agents

./agent.sh help                          # Show all commands
./agent.sh maintenance health            # Verify systems
./agent.sh prime                         # Pre-cache sentiment
./agent.sh whisper                       # Find opportunities (parallel)
./agent.sh analyze NVDA                  # Deep dive
```

**Prerequisites:** Python 3.11+, core venv (shared), API keys auto-loaded from `core/.env` and `cloud/.env`

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

## Architecture

```
agents/
├── agent.sh                    # CLI entry point
├── src/
│   ├── orchestrators/          # Coordinate parallel agent execution
│   │   ├── prime.py            # PrimeOrchestrator (sentiment pre-caching)
│   │   ├── whisper.py          # WhisperOrchestrator (parallel ticker scan)
│   │   ├── analyze.py          # AnalyzeOrchestrator (multi-specialist deep dive)
│   │   └── base.py             # BaseOrchestrator (shared logic)
│   ├── agents/                 # Specialist worker agents
│   │   ├── ticker_analysis.py  # VRP + liquidity + TRR
│   │   ├── sentiment_fetch.py  # Perplexity API integration
│   │   ├── health.py           # System monitoring
│   │   ├── explanation.py      # Narrative reasoning
│   │   ├── anomaly.py          # Edge case detection
│   │   ├── pattern_recognition.py  # Historical pattern mining
│   │   ├── preflight.py        # Pre-flight ticker validation
│   │   ├── news_fetch.py       # Finnhub news headlines
│   │   ├── sector_fetch.py     # Finnhub sector data
│   │   ├── data_quality.py     # Automated data fixes
│   │   └── base.py             # BaseAgent (shared logic)
│   ├── integration/            # Cross-system bridges
│   │   ├── container_2_0.py    # Wraps 2.0's container
│   │   ├── cache_4_0.py        # Uses 4.0's sentiment cache
│   │   ├── perplexity_5_0.py   # Uses 5.0's Perplexity client
│   │   ├── position_limits.py  # TRR and position sizing
│   │   ├── ticker_metadata.py  # Ticker metadata access
│   │   └── mcp_client.py       # MCP server client
│   ├── utils/                  # Schemas, formatters, timeouts, retry, paths
│   └── cli/                    # CLI wrappers (prime, whisper, analyze, maintenance)
├── config/
│   └── agents.yaml             # Agent timeouts and model config
└── tests/                      # 82 tests
```

## Performance

| Command | Time | Notes |
|---------|------|-------|
| `prime` (30 tickers) | ~10s | 9x faster than sequential |
| `whisper` (30 tickers) | ~90s | 2x faster than sentiment |
| `analyze` (single) | ~20-30s | Full parallel deep dive |

## Agent Timeouts

| Agent | Timeout | Rationale |
|-------|---------|-----------|
| TickerAnalysisAgent | 30s | core analysis is fast |
| SentimentFetchAgent | 30s | Perplexity typically < 10s |
| ExplanationAgent | 30s | Narrative generation |
| AnomalyDetectionAgent | 20s | Fast validation |
| HealthCheckAgent | 10s | Simple connectivity |
| PreFlightAgent | 1s | DB-only ticker validation |
| NewsFetchAgent | 10s | Finnhub news (non-critical) |

## Integration with core/sentiment

6.0 imports existing systems via `sys.path` injection:

```python
from src.integration.container_2_0 import Container2_0

container = Container2_0()
result = container.container  # Access 2.0's DI container
```

Git worktree support via `git rev-parse --git-common-dir`.

## Testing

```bash
cd agents
../core/venv/bin/python -m pytest tests/ -v    # 82 tests
```

---

**Disclaimer:** For research purposes only. Not financial advice.
