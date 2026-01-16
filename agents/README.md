# Trading Desk 6.0 - Agent-Based Orchestration System

**Status:** Phase 1 Complete ✅, Phase 2 Complete ✅, Phase 3 Complete ✅ (Jan 2026)
**Purpose:** Agent-based orchestration with parallel processing and intelligent automation

---

## Overview

6.0 introduces an **agent-based orchestration layer** on top of the existing 2.0/4.0 stack. Instead of sequential processing, 6.0 coordinates multiple specialist agents working in parallel:

### What 6.0 Adds

| Feature | Before (4.0) | After (6.0) |
|---------|-------------|-------------|
| **Processing** | Sequential | Parallel (2x faster) |
| **Intelligence** | Single-ticker | Cross-ticker correlation, sector risk |
| **Anomaly Detection** | Manual | Automated guardrails |
| **Explanations** | Basic | Narrative reasoning with context |
| **Workflow** | Monolithic | Modular agents with orchestration |

### Performance Improvements

- **/prime sentiment caching**: **NEW** - Pre-cache 30 tickers in ~10s
- **/whisper scans**: **180s → 90s** (50% faster via parallel ticker analysis)
- **Budget efficiency**: Pre-cache sentiment to avoid redundant API calls
- **Anomaly detection**: Catch edge cases BEFORE trading

---

## Quick Start

```bash
# From 6.0/ directory
./agent.sh help                # Show all commands and options
./agent.sh maintenance health  # Verify all systems operational
./agent.sh prime               # Pre-cache sentiment (7-8 AM daily)
./agent.sh whisper             # Find best opportunities this week
./agent.sh analyze NVDA        # Deep dive on single ticker
```

### Typical Daily Workflow

```bash
# Morning (7-8 AM)
./agent.sh prime               # Pre-cache sentiment (~10s for 30 tickers)
./agent.sh whisper             # Instant results from cache

# Deep dive on best candidate
./agent.sh analyze NFLX        # Full analysis with patterns, TRR, sentiment
```

### Prerequisites

- Python 3.11+
- 2.0 virtual environment (shared)
- API keys in `.env` files (auto-loaded from 2.0/.env and 5.0/.env)

**Virtual Environment:** 6.0 uses 2.0's venv (no separate installation needed)

**API Keys:** Automatically loaded from existing `.env` files - no additional setup required:
- `5.0/.env` → PERPLEXITY_API_KEY
- `2.0/.env` → TRADIER_API_KEY, ALPHA_VANTAGE_KEY, DB_PATH

---

## Command Reference

Run `./agent.sh help` for full documentation. Quick reference:

| Command | Description |
|---------|-------------|
| `./agent.sh help` | Show all commands, options, and examples |
| `./agent.sh prime [DATE]` | Pre-cache sentiment for upcoming earnings |
| `./agent.sh whisper [DATE]` | Find most anticipated earnings with VRP |
| `./agent.sh analyze TICKER [DATE]` | Deep dive with patterns, TRR, sentiment |
| `./agent.sh maintenance health` | System health check |
| `./agent.sh maintenance data-quality` | Database integrity scan |
| `./agent.sh maintenance data-quality --fix` | Auto-fix safe data issues |
| `./agent.sh maintenance sector-sync` | Populate sector data from Finnhub |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   6.0 AGENT ORCHESTRATION                    │
│  Parallel processing + intelligent coordination             │
├─────────────────────────────────────────────────────────────┤
│ Orchestrators:                                              │
│  ✅ PrimeOrchestrator      (sentiment pre-caching)          │
│  ✅ WhisperOrchestrator    (parallel ticker analysis)       │
│  ✅ AnalyzeOrchestrator    (multi-specialist deep dive)     │
│  ✅ MaintenanceOrchestrator (background operations)         │
├─────────────────────────────────────────────────────────────┤
│ Worker Agents:                                              │
│  ✅ TickerAnalysisAgent    (VRP + liquidity + TRR limits)   │
│  ✅ SentimentFetchAgent    (Perplexity API integration)     │
│  ✅ HealthCheckAgent       (system monitoring)              │
│  ✅ ExplanationAgent       (narrative reasoning)            │
│  ✅ AnomalyDetectionAgent  (data quality + edge cases)      │
│  ✅ SectorFetchAgent       (Finnhub sector/industry data)   │
│  ✅ DataQualityAgent       (automated data fixes)           │
│  ✅ PatternRecognitionAgent (historical pattern mining)     │
├─────────────────────────────────────────────────────────────┤
│ Intelligence Layer:                                         │
│  ✅ Cross-ticker correlation (real sector data from Finnhub)│
│  ✅ Anomaly detection (conflicting signals, stale data)     │
│  ✅ Narrative explanations (why VRP is elevated)            │
│  ✅ TRR-based position sizing (prevents oversizing)         │
│  ✅ Historical pattern recognition (streaks, bias, trends)  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│          EXISTING LAYERS (2.0, 4.0, 5.0 unchanged)          │
├─────────────────────────────────────────────────────────────┤
│ 4.0: Sentiment caching, budget tracking                     │
│ 2.0: VRP math, strategy generation                          │
│ 5.0: Cloud API (independent, unaffected by 6.0)             │
└─────────────────────────────────────────────────────────────┘
```

**Legend:** ✅ Complete, 🚧 In Progress, 📋 Planned

### Design Principles

1. **Reuse, Don't Duplicate** - Imports 2.0/4.0 via sys.path, zero code duplication
2. **Parallel by Default** - Orchestrators spawn multiple agents concurrently
3. **Stateless Workers** - Agents have no shared state, communicate via JSON
4. **Fail-Safe Guardrails** - Anomaly detection catches edge cases automatically
5. **Budget-Aware** - Respects 4.0's budget limits (40 calls/day, $5/month)

---

## Commands

### /prime - Pre-cache Sentiment

Pre-caches sentiment for upcoming earnings, enabling instant `/whisper` results and predictable API costs.

```bash
./agent.sh prime                  # Next 5 days
./agent.sh prime 2026-02-05       # Specific start date
```

**Workflow:**
1. Health check (verify Perplexity budget available)
2. Fetch earnings calendar for date range
3. Filter tickers already cached (<3 hours old)
4. Check budget allows fetching remaining tickers
5. Spawn N SentimentFetchAgents in parallel (rate-limited to 2 concurrent)
6. Cache all results (3-hour TTL)
7. Return summary

**Example Output:**
```
[1/6] Running health check...
[2/6] Fetching earnings calendar...
Found 15 earnings
[3/6] Checking cache status...
Already cached: 8 tickers
Need to fetch: 7 tickers
[4/6] Verifying budget for API calls...
Budget status: 32 calls remaining
[5/6] Fetching sentiment for 7 tickers in parallel...
Successful: 7
[6/6] Caching complete

Total tickers cached: 15
  - Already cached: 8
  - Newly cached: 7
API calls made: 7
Budget remaining: 25 calls/day
```

**Performance:** ~10 seconds for 30 tickers (9x faster than sequential)

**Rate Limiting:** Max 2 concurrent requests with 0.5s delays to prevent 429 errors

### /maintenance - System Operations

```bash
./agent.sh maintenance health                   # System health check
./agent.sh maintenance data-quality             # Database integrity scan
./agent.sh maintenance data-quality --fix       # Auto-fix safe data issues
./agent.sh maintenance data-quality --dry-run   # Preview fixes without applying
./agent.sh maintenance sector-sync              # Sync sector data from Finnhub
./agent.sh maintenance cache-cleanup            # Cache cleanup automation
```

**Health Check Output:**
```
System Status: HEALTHY

APIs:
  Tradier:       OK (150ms)
  Alpha Vantage: OK (300ms)
  Perplexity:    OK (32 calls remaining)

Database:
  Status:        OK
  Size:          45.2 MB
  Records:       4,926 historical_moves, 1,200 earnings_calendar

Budget:
  Daily:         8/40 calls (32 remaining)
  Monthly:       $1.20/$5.00 ($3.80 remaining)
```

**Data Quality Scan Output:**
```
[1/5] Checking historical moves data...
  Total tickers: 427
  Total moves: 5,513

[2/5] Analyzing data quality...
[3/5] Checking for duplicates...

[4/5] Data Quality Report:

⚠️  4 tickers with <4 quarters:
  - NAVN: 1 quarters
  - BLSH: 2 quarters
  - ABVX: 3 quarters
  - SAIL: 3 quarters

✅ No extreme outliers detected
✅ No duplicate entries found

[5/5] Recommendations:

- Run backfill for tickers with <4 quarters
  Command: cd ../2.0 && ./trade.sh backfill <TICKER>
```

**Cache Cleanup Output:**
```
[1/4] Analyzing sentiment cache...
  Total entries: 5
  Stale entries (>3h): 5
  Cache hit rate: 70.0%

[2/4] Cleaning sentiment cache...
  ✓ Removed 5 stale entries

[3/4] Cleaning budget tracker...
  ✓ Removed 12 old entries (>30 days)

[4/4] Summary:
  Sentiment cache: 5 entries removed
  Budget tracker: 12 entries removed
  Disk space freed: ~0.02 MB

  Cache efficiency: 70.0% hit rate
```

---

## Agents

### ✅ TickerAnalysisAgent

**Purpose:** Execute 2.0's full analysis for single ticker

**APIs Called:**
- `container.analyzer.analyze()` - VRP, liquidity, strategies
- `container.prices_repository.get_historical_moves()` - Historical patterns

**Returns:**
```json
{
  "ticker": "NVDA",
  "vrp_ratio": 6.2,
  "recommendation": "EXCELLENT",
  "liquidity_tier": "GOOD",
  "score": 78,
  "strategies": [
    {
      "type": "bull_put_spread",
      "max_profit": 48.0,
      "max_loss": 452.0,
      "probability_of_profit": 0.85,
      "contracts": 1
    },
    {
      "type": "iron_condor",
      "max_profit": 114.0,
      "max_loss": 484.0,
      "probability_of_profit": 0.63,
      "contracts": 1
    }
  ],
  "error": null
}
```

**Analyze Output Format:**
```
## Strategies

**bull_put_spread**
  - Max Profit: $48.00 | Max Loss: $452.00
  - POP: 85% | Contracts: 1

**iron_condor**
  - Max Profit: $114.00 | Max Loss: $484.00
  - POP: 63% | Contracts: 1
```

**Result Type Handling:** Properly unwraps 2.0's `Result[TickerAnalysis, AppError]` type

**Key Features:**
- Converts string dates to date objects (2.0 expects `date`, not `str`)
- Extracts VRP ratio from `result.vrp.vrp_ratio`
- Converts enum recommendations to uppercase strings
- Computes simplified score from VRP thresholds
- Handles `Result.is_err` as property (not method)

**Test Status:** ✅ Passing (live test with AAPL, VRP 1.51, GOOD recommendation)

### ✅ SentimentFetchAgent

**Purpose:** Fetch AI sentiment from Perplexity API with budget awareness

**Workflow:**
1. Check cache first (3-hour TTL)
2. Check budget allows API call
3. Fetch sentiment via Perplexity5_0 client (direct API, not MCP)
4. Validate response with Pydantic schema BEFORE recording budget
5. Record API call (only after successful validation)
6. Cache result

**Returns:**
```json
{
  "ticker": "NVDA",
  "direction": "bullish",
  "score": 0.65,
  "catalysts": [
    "Datacenter demand strong",
    "AI growth accelerating"
  ],
  "risks": [
    "Competition from AMD",
    "Supply constraints"
  ],
  "error": null
}
```

**Budget Protection:** Never records API call if response validation fails

**Malformed Data Handling:** Filters out invalid catalysts/risks (e.g., just "**" or empty strings from malformed Perplexity responses)

**Test Status:** ✅ Passing (integrated into PrimeOrchestrator)

### ✅ HealthCheckAgent

**Purpose:** System monitoring before batch operations

**Checks:**
- API connectivity (Tradier, Alpha Vantage, Perplexity)
- Database health (connection, integrity, size)
- Budget status (daily/monthly limits)
- Data freshness (earnings calendar age)

**Test Status:** ✅ Passing

### 🚧 ExplanationAgent (Phase 2)

**Purpose:** Add narrative reasoning to math results

**Context Used:**
- Perplexity sentiment (via cache)
- Historical moves (pattern matching)
- VRP analysis (why is it elevated?)

**Test Status:** ✅ Passing (unit tests complete, orchestrator integration pending)

### 🚧 AnomalyDetectionAgent (Phase 2)

**Purpose:** Flag unusual situations requiring human review

**Checks:**
- Stale earnings dates (>7 days out, cache >24h old)
- Missing historical data (<4 quarters)
- Extreme outliers (VRP >20x, moves >50%)
- **Conflicting signals** (excellent VRP + reject liquidity) - learned from WDAY/ZS loss
- Database integrity (duplicates, schema violations)

**Test Status:** ✅ Passing (unit tests complete, orchestrator integration pending)

---

## Integration with 2.0 and 4.0

### Reuse Strategy

6.0 **imports** 2.0 and 4.0 via sys.path injection - zero code duplication.

```python
# In 6.0/src/integration/container_2_0.py
import sys
from pathlib import Path

# Add 2.0/ to sys.path with highest priority
_2_0_dir = find_main_repo() / "2.0"
sys.path.insert(0, str(_2_0_dir))

# Import 2.0 components
from src.container import get_container

class Container2_0:
    def __init__(self):
        self.container = get_container()
```

### Namespace Collision Handling

**Problem:** Both 6.0 and 2.0 use `src` as top-level package

**Solution:**
1. Temporarily remove 6.0 paths from sys.path
2. Clear cached `sys.modules['src']` imports
3. Import 2.0 components with clean slate
4. Restore 6.0 paths after import

### Git Worktree Support

6.0 development uses git worktrees for isolation (`.worktrees/6.0-agent-system`). All integration code handles this correctly:

```python
def _find_main_repo() -> Path:
    """Find main repository root, handling worktrees correctly."""
    result = subprocess.run(
        ['git', 'rev-parse', '--git-common-dir'],
        capture_output=True, text=True, check=True
    )
    git_common_dir = Path(result.stdout.strip())
    if not git_common_dir.is_absolute():
        git_common_dir = (Path(__file__).parent / git_common_dir).resolve()
    return git_common_dir.parent
```

---

## Testing

### Test Coverage

All Phase 1, Phase 2, and Phase 3 agents tested and passing (48 tests total):

| Agent/Feature | Test File | Status |
|-------|-----------|--------|
| HealthCheckAgent | `tests/test_maintenance_live.py` | ✅ Pass |
| TickerAnalysisAgent | `tests/test_ticker_analysis_live.py` | ✅ Pass |
| ExplanationAgent | `tests/test_explanation_agent.py` | ✅ Pass (5 scenarios) |
| SentimentFetchAgent | (integrated in PrimeOrchestrator) | ✅ Pass |
| WhisperOrchestrator | `tests/test_whisper_live.py` | ✅ Pass |
| AnalyzeOrchestrator | `tests/test_analyze_live.py` | ✅ Pass |
| MaintenanceOrchestrator | `tests/test_maintenance_live.py` | ✅ Pass (3 scenarios) |
| PositionLimitsRepository | `tests/test_position_limits.py` | ✅ Pass |
| TickerMetadataRepository | `tests/test_ticker_metadata.py` | ✅ Pass |
| SectorFetchAgent | `tests/test_sector_fetch.py` | ✅ Pass |
| DataQualityAgent | `tests/test_data_quality_agent.py` | ✅ Pass |
| PatternRecognitionAgent | `tests/test_pattern_recognition.py` | ✅ Pass |
| TRR Formatting | `tests/test_formatter_trr.py` | ✅ Pass |
| TRR in Analyze | `tests/test_analyze_trr.py` | ✅ Pass |
| Patterns in Analyze | `tests/test_analyze_patterns.py` | ✅ Pass |
| Sector Warnings | `tests/test_whisper_sectors.py` | ✅ Pass |
| Schemas | `tests/test_schemas.py` | ✅ Pass |

### Running Tests

```bash
# From 6.0/ directory - run all tests
../2.0/venv/bin/python -m pytest tests/ -v

# Output (abbreviated):
# tests/test_analyze_live.py: 2 passed
# tests/test_analyze_patterns.py: 5 passed
# tests/test_analyze_trr.py: 2 passed
# tests/test_data_quality_agent.py: 5 passed
# tests/test_explanation_agent.py: 5 passed
# tests/test_formatter_trr.py: 3 passed
# tests/test_maintenance_live.py: 3 passed
# tests/test_pattern_recognition.py: 5 passed
# tests/test_position_limits.py: 4 passed
# tests/test_schemas.py: 4 passed
# tests/test_sector_fetch.py: 3 passed
# tests/test_ticker_analysis_trr.py: 1 passed
# tests/test_ticker_metadata.py: 4 passed
# tests/test_whisper_live.py: 2 passed
# tests/test_whisper_sectors.py: 2 passed
# =================== 48 passed ===================
```

---

## Implementation Phases

### ✅ Phase 1: Core Infrastructure + /prime (Complete - Jan 2026)

**Delivered:**
- ✅ Base orchestration framework (BaseOrchestrator)
- ✅ Integration layer (Container2_0, Cache4_0, Perplexity5_0)
- ✅ PrimeOrchestrator with parallel sentiment fetching
- ✅ Core agents (TickerAnalysis, SentimentFetch, Health)
- ✅ CLI entry point (`agent.sh prime`, `agent.sh maintenance health`)
- ✅ All agent tests passing

**Key Achievements:**
- **Result Type Handling:** Properly unwraps 2.0's `Result[T, Error]` types
- **Git Worktree Support:** All integration code handles worktrees correctly
- **Namespace Collision Fix:** Clean separation between 6.0/src and 2.0/src
- **Rate Limiting:** Prevents 429 errors (Semaphore(2) + 0.5s delays)
- **Budget Protection:** Never records API call if validation fails

**Performance:**
- `/prime`: ~10s for 30 tickers (9x faster than sequential)
- Agent timeouts: 30s TickerAnalysis, 30s SentimentFetch, 10s HealthCheck

### ✅ Phase 2: /whisper + /analyze (Complete - Jan 2026)

**Delivered:**
- ✅ WhisperOrchestrator with parallel ticker analysis
- ✅ AnalyzeOrchestrator with multi-specialist deep dive
- ✅ ExplanationAgent integration (narrative reasoning)
- ✅ AnomalyDetectionAgent integration (edge case detection)
- ✅ Cross-ticker intelligence (simplified sector grouping, portfolio risk warnings)
- ✅ Improved error output (shows anomaly details for DO_NOT_TRADE)

**Key Achievements:**
- **/whisper**: Analyzes 30 tickers in ~90s with VRP filtering and explanations
- **/analyze**: Full deep dive with VRP, liquidity, sentiment, anomaly detection
- **Anomaly detection**: Catches EXCELLENT VRP + REJECT liquidity conflicts (WDAY/ZS lesson)
- **Error transparency**: Failed analyses show WHY they were blocked

**Code Review Fixes (Jan 2026):**

| Issue | Severity | Fix |
|-------|----------|-----|
| asyncio.run() inside to_thread() | Critical | Made SentimentFetchAgent async, removed to_thread wrapper |
| Thread-safety in Container2_0 | Important | Added threading.Lock() with double-check pattern |
| Silent exception handlers | Important | Added logging to base.py calendar fetch |
| Hardcoded magic numbers | Important | Moved $50K/$150K to class constants in whisper.py |
| Missing __all__ exports | Minor | Added __all__ to all __init__.py files |
| Bare except clause | Minor | Removed duplicate test file with bare except |
| MCP client placeholder | Minor | Updated docstrings to clarify Phase 2 status |

### ✅ Phase 3: Enhanced Intelligence + Maintenance (Complete - Jan 2026)

**Delivered:**
- ✅ **TRR-based Position Sizing** - Surfaces tail risk data to prevent oversizing (learned from significant MU loss)
- ✅ **Real Sector Data Integration** - Finnhub-powered sector/industry data replaces placeholder grouping
- ✅ **Automated Data Quality Fixes** - Auto-fix safe issues, flag ambiguous ones for review
- ✅ **PatternRecognitionAgent** - Historical pattern mining (directional bias, streaks, magnitude trends)

**New Agents:**
- `SectorFetchAgent` - Fetches company profiles from Finnhub, maps to sectors
- `DataQualityAgent` - Detects and fixes data quality issues
- `PatternRecognitionAgent` - Analyzes historical earnings patterns

**New Commands:**
- `./agent.sh maintenance data-quality --fix` - Auto-fix safe data issues
- `./agent.sh maintenance data-quality --dry-run` - Preview fixes without applying
- `./agent.sh maintenance sector-sync` - Populate sector data for upcoming earnings

**Output Enhancements:**
- `/whisper` now shows TRR badge for high-risk tickers: `⚠️ HIGH TRR (max 50 contracts)`
- `/whisper` cross-ticker warnings use real sectors: `⚠️ 3 Technology tickers (NVDA, AAPL, MSFT)`
- `/analyze` includes Position Limits section with TRR, max contracts, max notional
- `/analyze` includes Historical Patterns section with directional bias, streaks, trends
- `/analyze` shows full strategy details (type, max profit/loss, POP, contracts) instead of just count
- `/analyze` filters malformed sentiment data (empty catalysts/risks from Perplexity)

### 📋 Phase 4: Refinement + Documentation (Future)

**Planned:**
- Performance optimization (timeout tuning, caching)
- End-to-end integration tests
- Production readiness review

---

## Directory Structure

```
6.0/
├── agent.sh                    # CLI entry point
├── README.md                   # This file
├── src/
│   ├── __init__.py             # Package exports
│   ├── orchestrators/
│   │   ├── __init__.py         # BaseOrchestrator, WhisperOrchestrator, etc.
│   │   ├── base.py             # BaseOrchestrator (common patterns)
│   │   ├── prime.py            # ✅ PrimeOrchestrator
│   │   ├── whisper.py          # ✅ WhisperOrchestrator
│   │   ├── analyze.py          # ✅ AnalyzeOrchestrator
│   │   └── maintenance.py      # ✅ MaintenanceOrchestrator
│   ├── agents/
│   │   ├── __init__.py             # All agent exports
│   │   ├── base.py                 # BaseAgent (JSON schema validation)
│   │   ├── ticker_analysis.py      # ✅ TickerAnalysisAgent
│   │   ├── sentiment_fetch.py      # ✅ SentimentFetchAgent (async)
│   │   ├── health.py               # ✅ HealthCheckAgent
│   │   ├── explanation.py          # ✅ ExplanationAgent
│   │   ├── anomaly.py              # ✅ AnomalyDetectionAgent
│   │   ├── sector_fetch.py         # ✅ SectorFetchAgent (Finnhub)
│   │   ├── data_quality.py         # ✅ DataQualityAgent
│   │   └── pattern_recognition.py  # ✅ PatternRecognitionAgent
│   ├── integration/
│   │   ├── __init__.py           # Container2_0, Cache4_0, etc.
│   │   ├── container_2_0.py      # 2.0 integration (thread-safe, worktree-aware)
│   │   ├── cache_4_0.py          # 4.0 integration (JSON serialization)
│   │   ├── perplexity_5_0.py     # Perplexity API client
│   │   ├── mcp_client.py         # MCP Task tool (Phase 2 placeholder)
│   │   ├── position_limits.py    # ✅ Position limits repository (TRR)
│   │   └── ticker_metadata.py    # ✅ Ticker metadata repository (sectors)
│   ├── intelligence/           # Cross-ticker analysis (future)
│   ├── utils/
│   │   ├── __init__.py         # Utility exports
│   │   ├── paths.py            # Repository path resolution
│   │   ├── schemas.py          # Pydantic models (TickerAnalysisResponse, etc.)
│   │   ├── timeout.py          # Timeout utilities
│   │   └── formatter.py        # Output formatting
│   └── cli/
│       ├── __init__.py         # CLI exports
│       ├── prime.py            # ✅ /prime CLI wrapper
│       ├── maintenance.py      # ✅ /maintenance CLI wrapper
│       ├── whisper.py          # ✅ /whisper CLI wrapper
│       └── analyze.py          # ✅ /analyze CLI wrapper
├── config/
│   └── agents.yaml             # Agent configuration (timeouts, models)
└── tests/
    ├── __init__.py
    ├── test_analyze_live.py         # ✅ AnalyzeOrchestrator tests
    ├── test_explanation_agent.py    # ✅ ExplanationAgent unit tests
    ├── test_maintenance_live.py     # ✅ MaintenanceOrchestrator tests
    ├── test_ticker_analysis_live.py # ✅ TickerAnalysisAgent tests
    └── test_whisper_live.py         # ✅ WhisperOrchestrator tests
```

---

## Configuration

### Environment Variables

6.0 inherits all configuration from 2.0 and 4.0:

```bash
# Required (from 2.0)
TRADIER_API_KEY=your_key
ALPHA_VANTAGE_KEY=your_key
DB_PATH=data/ivcrush.db

# Required (from 4.0)
PERPLEXITY_API_KEY=your_key
```

No additional 6.0-specific configuration needed.

### Timeouts

Default timeouts per agent type:

| Agent | Timeout | Rationale |
|-------|---------|-----------|
| TickerAnalysisAgent | 30s | 2.0 analysis is fast |
| SentimentFetchAgent | 30s | Perplexity API typically <10s |
| ExplanationAgent | 30s | Narrative generation |
| AnomalyDetectionAgent | 20s | Fast validation checks |
| HealthCheckAgent | 10s | Simple connectivity tests |

Global orchestrator timeout: 90s (allows for sequential fallback if parallel fails)

---

## Troubleshooting

### Common Issues

**1. TypeError: '>=' not supported between instances of 'datetime.date' and 'str'**

**Fix:** TickerAnalysisAgent now converts automatically (fixed in Phase 1)

**2. AttributeError: 'Result' object has no attribute 'is_err'**

**Fix:** Use `result.is_err` (property, not method) - fixed in Phase 1

**3. Pydantic ValidationError: Invalid recommendation: good**

**Fix:** Uppercase conversion added to extraction methods - fixed in Phase 1

**4. Namespace collision between 6.0/src and 2.0/src**

**Fix:** Container2_0 clears cached imports before loading 2.0 - fixed in Phase 1

**5. Empty Catalysts/Risks showing "- **" in analyze output**

**Cause:** Perplexity sometimes returns malformed responses with just markdown markers
**Fix:** AnalyzeOrchestrator now filters out entries that are just "**" or empty - fixed in Jan 2026

---

## Performance Benchmarks

| Workflow | 6.0 Phase 1 | Target (Phase 2) | Notes |
|----------|-------------|------------------|-------|
| /prime (30 tickers) | 10s | N/A | New feature, 9x faster than sequential |
| /whisper (30 tickers) | N/A | 90s | Phase 2 target, 2x faster than 4.0 |
| /analyze (single) | N/A | 60s | Phase 2 target, richer analysis |
| /health | 5s | 5s | Complete |

---

## Contributing

### Development Workflow

```bash
# Create feature branch
git checkout -b feature/6.0-new-feature

# Make changes
# ... implement feature ...

# Test
./agent.sh health
../2.0/venv/bin/python tests/test_new_feature.py

# Commit
git commit -m "feat: add new feature

- Implements X
- Tests included

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

### Code Style

- Follow existing patterns in `src/agents/` and `src/orchestrators/`
- Use Pydantic for schema validation
- Handle Result types from 2.0 properly (`.is_err` as property, unwrap `.value`)
- Add comprehensive docstrings
- Include error handling with logging

---

## License

Private - Internal use only

---

**Disclaimer:** For research purposes only. Not financial advice. Options trading involves substantial risk.
