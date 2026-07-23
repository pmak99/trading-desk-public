# 6.0 Agent Orchestration — ARCHIVED

**This subsystem is archived** (moved to `archived/6.0/`). Its parallel deep-dive workflow was superseded by the Claude Code skill commands (`/analyze`, `/whisper`, `/council`) and the 5.0 cloud API. Code is preserved for reference. Do not extend or depend on this directory; its tests (82) are not part of active CI.

Some integration paths referenced below no longer exist in their original form — 4.0's `sentiment_cache.db` was migrated into `ivcrush.db` (May 2026), and ORATS was retired (Jul 2026).

---

Parallel specialist agents that coordinated deep-dive analysis on top of the 2.0/4.0 stack: multiple independent analyses run concurrently, then synthesized.

## Commands (historical)

```bash
cd 6.0

./agent.sh whisper                    # Find high-VRP opportunities (parallel)
./agent.sh analyze NVDA               # Deep dive on a single ticker
./agent.sh prime                      # Pre-cache sentiment for upcoming earnings
./agent.sh maintenance health         # Verify all systems
./agent.sh maintenance data-quality [--fix]
./agent.sh maintenance cache-cleanup
```

**Prerequisites:** Python 3.11+, 2.0 venv at `../2.0/venv/`. API keys auto-loaded from `2.0/.env`.

## Architecture

```
6.0/
├── agent.sh                    CLI entry point
├── src/
│   ├── orchestrators/          Coordinate parallel agent execution
│   │   ├── analyze.py          PreFlight+Health → [TickerAnalysis+Sentiment+News+Pattern] → [Explanation+Anomaly] → Synthesize
│   │   ├── whisper.py          Parallel VRP scan across all upcoming earnings
│   │   ├── prime.py            Parallel sentiment pre-caching
│   │   └── base.py             Shared orchestration logic
│   ├── agents/                 Specialist workers (ticker_analysis, sentiment_fetch,
│   │                           explanation, anomaly, pattern_recognition, news_fetch,
│   │                           health, preflight, sector_fetch, data_quality)
│   ├── integration/            Cross-system bridges (container_2_0, cache_4_0,
│   │                           perplexity_5_0, position_limits, ticker_metadata)
│   ├── cli/                    CLI wrappers for each command
│   └── utils/                  schemas, formatter, timeout, retry, paths
└── tests/                      82 tests (not in CI)
```

## Analyze Pipeline

`AnalyzeOrchestrator` ran agents in three waves:

```
Wave 1 (parallel):  PreFlightAgent + HealthCheckAgent
Wave 2 (parallel):  TickerAnalysisAgent + SentimentFetchAgent + NewsFetchAgent + PatternRecognitionAgent
Wave 3 (parallel):  ExplanationAgent + AnomalyDetectionAgent
Final:              Synthesize all results → formatted output
```

Agent timeouts: TickerAnalysis/Sentiment/Explanation 30s, Anomaly 20s, News/Health 10s, PreFlight 1s.

## 2.0 Integration

6.0 used `importlib.util.spec_from_file_location` (not `sys.path`) to avoid `src` namespace collision with 2.0:

```python
from src.integration.container_2_0 import Container2_0

c = Container2_0()
result = await c.container.implied_move_calculator.calculate(ticker, expiration)
```

`container.container` is a property, not a method call.

---

*For research purposes only. Not financial advice.*
