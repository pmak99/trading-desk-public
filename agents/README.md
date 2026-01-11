# Trading Desk 6.0 - Agent-Based System

Agent orchestration layer for Trading Desk, enabling parallel processing, automated explanations, and intelligent guardrails.

## Overview

6.0 enhances existing 4.0 workflows with:

1. **Explanations** - Automates manual Perplexity workflow
2. **Speed** - Parallel processing (3 min → 90 sec for /whisper)
3. **Intelligence** - Anomaly detection prevents costly mistakes
4. **Orchestration** - Production-quality agent coordination

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  6.0 AGENT LAYER                        │
├─────────────────────────────────────────────────────────┤
│ Orchestrators:                                          │
│  - WhisperOrchestrator    (parallel ticker analysis)    │
│  - AnalyzeOrchestrator    (multi-faceted deep dive)     │
│  - MaintenanceOrchestrator (background ops)             │
├─────────────────────────────────────────────────────────┤
│ Worker Agents:                                          │
│  - TickerAnalysisAgent    (VRP + liquidity + sentiment) │
│  - ExplanationAgent       (narrative reasoning)         │
│  - AnomalyDetectionAgent  (data quality, outliers)      │
│  - HealthCheckAgent       (system monitoring)           │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│         EXISTING LAYERS (2.0, 4.0, 5.0)                 │
├─────────────────────────────────────────────────────────┤
│ 2.0: VRP math, strategy generation                      │
│ 4.0: Sentiment caching, budget tracking                 │
│ 5.0: Cloud API (unchanged, remains independent)         │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
cd 6.0

# Most anticipated earnings (next 5 days)
./agent.sh whisper

# Analyze single ticker
./agent.sh analyze NVDA

# System health check
./agent.sh maintenance health
```

## Commands

### /whisper - Most Anticipated Earnings

Discovers and ranks earnings opportunities with parallel analysis:

```bash
./agent.sh whisper                # Next 5 days
./agent.sh whisper 2026-02-05     # Specific date range
```

**Workflow:**
1. Health check (fail fast if APIs down)
2. Fetch earnings calendar
3. Analyze N tickers in parallel (30 max)
4. Filter by VRP >= 3.0x
5. Add explanations to top candidates
6. Run anomaly detection
7. Apply cross-ticker intelligence
8. Return top 10 ranked opportunities

**Performance:** 90 seconds for 30 tickers (vs 180 seconds sequential)

### /analyze - Single Ticker Deep Dive

Multi-specialist analysis with comprehensive insights:

```bash
./agent.sh analyze NVDA               # Auto-detect earnings date
./agent.sh analyze NVDA 2026-02-05    # Specific earnings date
```

**Workflow:**
1. Run AnomalyDetectionAgent first (fail fast if data issues)
2. Spawn 5 specialists in parallel:
   - VRPAgent (calculate VRP)
   - LiquidityAgent (analyze options chain)
   - SentimentAgent (fetch/cache AI sentiment)
   - ExplanationAgent (generate narrative)
   - AnomalyAgent (check data quality)
3. Synthesize insights into comprehensive report
4. Generate strategies using 2.0's StrategyGenerator
5. Return markdown report with trade/no-trade recommendation

**Performance:** 60 seconds (vs 45 seconds in 5.0, but much richer analysis)

### /maintenance - Background Operations

System monitoring and data quality:

```bash
./agent.sh maintenance health          # Health check
./agent.sh maintenance data-quality    # Database integrity scan
./agent.sh maintenance cache-cleanup   # Cache management
```

## Agents

### Orchestrators

**WhisperOrchestrator**
- Coordinates parallel ticker analysis
- Applies cross-ticker intelligence (sector correlation, portfolio risk)
- Returns ranked opportunities

**AnalyzeOrchestrator** (Phase 2)
- Coordinates multi-specialist deep dive
- Synthesizes insights from 5 agents
- Generates comprehensive trading report

**MaintenanceOrchestrator** (Phase 3)
- Executes background operations
- Monitors system health
- Manages data quality

### Worker Agents

**TickerAnalysisAgent**
- Executes 2.0's full analysis for single ticker
- Returns VRP, liquidity, strategies, score
- Model: Haiku (fast)
- Timeout: 30 seconds

**ExplanationAgent**
- Generates narrative explanations
- Automates manual Perplexity workflow
- Explains why VRP is elevated, what's driving it
- Model: Sonnet (richer reasoning)
- Timeout: 30 seconds

**AnomalyDetectionAgent**
- Catches data quality issues
- Flags conflicting signals (EXCELLENT VRP + REJECT liquidity)
- Implements lessons from past losses (WDAY/ZS significant loss)
- Model: Haiku (fast checks)
- Timeout: 20 seconds

**HealthCheckAgent**
- Verifies system health before batch operations
- Checks APIs (Tradier, Alpha Vantage, Perplexity)
- Monitors database and budget status
- Model: Haiku (fast checks)
- Timeout: 20 seconds

## Integration

6.0 reuses existing layers without duplication:

**2.0 Integration** (`src/integration/container_2_0.py`)
- Wraps 2.0's dependency injection container
- Provides access to analyzer, repositories, API clients

**4.0 Integration** (`src/integration/cache_4_0.py`)
- Wraps 4.0's sentiment cache and budget tracker
- Respects 40 calls/day, $5/month limits

**MCP Client** (`src/integration/mcp_client.py`)
- Wrapper around Claude Desktop's Task tool
- Spawns agents via MCP protocol
- Handles JSON parsing and error recovery

## Configuration

Agent prompt templates and timeouts: `config/agents.yaml`

```yaml
TickerAnalysisAgent:
  model: "haiku"
  timeout: 30
  prompt: |
    You are a TickerAnalysisAgent. Analyze {ticker}...

timeouts:
  whisper_orchestrator: 90
  analyze_orchestrator: 60
  maintenance_orchestrator: 120
```

## Development Status

**Phase 1: Core Infrastructure + /whisper** ✅
- Base orchestration framework
- Integration layer (2.0, 4.0, MCP)
- WhisperOrchestrator
- Core agents (TickerAnalysis, Explanation, Anomaly, Health)
- CLI entry point

**Phase 2: /analyze + Enhanced Intelligence** (Planned)
- AnalyzeOrchestrator
- Enhanced ExplanationAgent
- Comprehensive markdown reports

**Phase 3: Maintenance + Pattern Recognition** (Planned)
- MaintenanceOrchestrator
- PatternRecognitionAgent
- Historical pattern mining

**Phase 4: Refinement + Documentation** (Planned)
- Performance optimization
- Testing suite
- Documentation

## Testing

```bash
# Run tests (Phase 2)
cd 6.0
../2.0/venv/bin/python -m pytest tests/ -v
```

## Directory Structure

```
6.0/
├── agent.sh                    # CLI entry point
├── README.md                   # This file
├── requirements.txt            # Dependencies
├── config/
│   └── agents.yaml             # Agent configurations
├── src/
│   ├── orchestrators/
│   │   ├── base.py             # BaseOrchestrator
│   │   ├── whisper.py          # WhisperOrchestrator
│   │   ├── analyze.py          # AnalyzeOrchestrator (Phase 2)
│   │   └── maintenance.py      # MaintenanceOrchestrator (Phase 3)
│   ├── agents/
│   │   ├── base.py             # BaseAgent utilities
│   │   ├── ticker_analysis.py  # TickerAnalysisAgent
│   │   ├── explanation.py      # ExplanationAgent
│   │   ├── anomaly.py          # AnomalyDetectionAgent
│   │   └── health.py           # HealthCheckAgent
│   ├── intelligence/
│   │   ├── cross_ticker.py     # Cross-ticker intelligence (Phase 2)
│   │   └── explainer.py        # Narrative generation (Phase 2)
│   ├── integration/
│   │   ├── container_2_0.py    # 2.0 integration
│   │   ├── cache_4_0.py        # 4.0 integration
│   │   └── mcp_client.py       # MCP Task tool wrapper
│   └── utils/
│       ├── schemas.py          # Pydantic models
│       ├── timeout.py          # Timeout utilities
│       └── formatter.py        # Output formatting
└── tests/                      # Test suite (Phase 2)
```

## Design Principles

1. **Enhance, Don't Replace** - 4.0 commands stay the same, agents add orchestration
2. **Automate Manual Work** - Eliminate manual Perplexity queries
3. **Intelligent Guardrails** - Prevent mistakes via anomaly detection
4. **Simple Integration** - Reuse 2.0/4.0 via sys.path, no duplication

## Success Metrics

**Correctness:**
- ✅ VRP calculations match 2.0 exactly
- ✅ Liquidity tiers match 2.0 exactly
- ✅ Anomaly detection catches known bad cases

**Performance:**
- ✅ /whisper completes <90s for 30 tickers (50% faster than 5.0)
- ⏳ /analyze completes <60s for single ticker (Phase 2)

**Intelligence:**
- ✅ Explanations are coherent and accurate
- ⏳ Cross-ticker warnings detect sector clusters (Phase 2)
- ✅ Anomaly detection reduces false positives

## Contributing

See `docs/plans/2026-01-11-6.0-agent-design.md` for full design document.

## License

Proprietary - Internal Trading Desk use only
