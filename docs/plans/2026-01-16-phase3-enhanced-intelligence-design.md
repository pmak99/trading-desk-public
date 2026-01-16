# 6.0 Phase 3: Enhanced Intelligence Design

**Date:** 2026-01-16
**Status:** Approved
**Author:** Claude Opus 4.5

---

## Overview

Phase 3 extends the 6.0 agent system with four features:

1. **TRR-based Position Sizing** - Surface existing tail risk data to prevent oversizing
2. **Real Sector Data Integration** - Replace placeholder sector grouping with Finnhub data
3. **Automated Data Quality Fixes** - Auto-fix safe issues, flag ambiguous ones
4. **PatternRecognitionAgent** - Mine historical patterns for actionable insights

### Implementation Order

| Order | Feature | Rationale |
|-------|---------|-----------|
| 1 | TRR-based position sizing | Lowest risk - data exists, just integration |
| 2 | Real sector data | Unblocks proper cross-ticker correlation |
| 3 | Automated data quality | Builds on existing MaintenanceOrchestrator |
| 4 | PatternRecognitionAgent | Most complex - new agent with pattern mining |

---

## Feature 1: TRR-based Position Sizing Integration

### Goal

Surface existing TRR data in whisper/analyze output to prevent oversizing on high-risk tickers. Learned from $134k MU loss in December 2025.

### Current State

- `position_limits` table populated with 451 tickers
- Contains: tail_risk_ratio, tail_risk_level, max_contracts, max_notional
- Data NOT surfaced in 6.0 output

### Files Changed

```
6.0/src/integration/
  └── position_limits.py  (NEW)

6.0/src/agents/
  └── ticker_analysis.py  (MODIFY - query position_limits)

6.0/src/orchestrators/
  └── whisper.py  (MODIFY - add TRR warnings)
  └── analyze.py  (MODIFY - show position limits section)

6.0/src/utils/
  └── formatter.py  (MODIFY - format TRR badges/warnings)
  └── schemas.py  (MODIFY - add PositionLimits schema)
```

### Output Changes

**Whisper** - Add TRR badge to high-risk tickers:
```
1. NVDA  VRP: 6.2x  Score: 78  ⚠️ HIGH TRR (max 50 contracts)
2. AAPL  VRP: 4.1x  Score: 72
3. MU    VRP: 5.1x  Score: 68  ⚠️ HIGH TRR (max 50 contracts)
```

**Analyze** - Add position limits section:
```
Position Limits:
  Tail Risk Ratio: 3.05x (HIGH)
  Max Contracts: 50
  Max Notional: $25,000
  Reason: Historical max move 11.21% vs avg 3.68%
```

### Data Flow

1. TickerAnalysisAgent queries `position_limits` table by ticker
2. Returns `tail_risk` and `position_limits` in result dict
3. Orchestrators pass through to formatter
4. Formatter adds badges (whisper) or sections (analyze) based on `tail_risk_level`

### Schema

```python
class PositionLimits(BaseModel):
    tail_risk_ratio: float
    tail_risk_level: Literal["LOW", "NORMAL", "HIGH"]
    max_contracts: int
    max_notional: float
    avg_move: float
    max_move: float
```

---

## Feature 2: Real Sector Data Integration

### Goal

Replace placeholder "first letter" sector grouping with real sector/industry data from Finnhub company profiles.

### Current State

- `ticker_metadata` table exists but is EMPTY
- whisper.py line 287: `# TODO: Use proper sector data from 2.0 company profiles`
- Cross-ticker warnings use first letter as sector proxy

### Files Changed

```
6.0/src/agents/
  └── sector_fetch.py  (NEW)

6.0/src/integration/
  └── ticker_metadata.py  (NEW)

6.0/src/orchestrators/
  └── whisper.py  (MODIFY - use real sector in _detect_cross_ticker_risks)
  └── maintenance.py  (MODIFY - add sector-sync command)

6.0/src/utils/
  └── schemas.py  (MODIFY - add TickerMetadata schema)
```

### Population Strategy

**On-demand** (during whisper/analyze):
- If ticker not in `ticker_metadata`, fetch from Finnhub
- Cache result in database
- Graceful degradation if Finnhub unavailable

**Batch sync** (via maintenance command):
```bash
./agent.sh maintenance sector-sync
```
- Populates all tickers with earnings in next 30 days
- Rate-limited: 1 request/second (Finnhub free tier = 60/min)

### Finnhub API Integration

```python
# Using existing MCP tool
mcp__finnhub__finnhub_stock_market_data(
    operation="company_profile",
    symbol="NVDA"
)

# Returns:
{
    "name": "NVIDIA Corporation",
    "finnhubIndustry": "Semiconductors",
    "marketCapitalization": 1200000,  # millions
    ...
}
```

Note: Finnhub returns `finnhubIndustry` not separate sector/industry. We'll map to sectors:

| Finnhub Industry | Sector |
|------------------|--------|
| Semiconductors | Technology |
| Software | Technology |
| Banks | Financial Services |
| Pharmaceuticals | Healthcare |
| Retail | Consumer Cyclical |
| ... | ... |

### Cross-Ticker Warning Update

**Before (placeholder):**
```
⚠️ Sector concentration: 3 tickers starting with 'N' (NVDA, NFLX, NKE)
```

**After (real sectors):**
```
⚠️ Sector concentration: 3 Technology tickers (NVDA, AAPL, MSFT)
⚠️ Industry concentration: 2 Semiconductors (NVDA, AMD)
```

### Schema

```python
class TickerMetadata(BaseModel):
    ticker: str
    company_name: str
    sector: str
    industry: str
    market_cap: Optional[float]
    updated_at: datetime
```

---

## Feature 3: Automated Data Quality Fixes

### Goal

Extend MaintenanceOrchestrator to auto-fix safe data issues instead of just reporting them.

### Current State

`./agent.sh maintenance data-quality` reports issues:
```
⚠️ 4 tickers with <4 quarters: NAVN, BLSH, ABVX, SAIL
  Command: cd ../2.0 && ./trade.sh backfill <TICKER>
```

No auto-fix capability.

### Files Changed

```
6.0/src/agents/
  └── data_quality.py  (NEW)

6.0/src/orchestrators/
  └── maintenance.py  (MODIFY - add --fix and --dry-run flags)

6.0/src/cli/
  └── maintenance.py  (MODIFY - parse new flags)
```

### New Commands

```bash
./agent.sh maintenance data-quality           # Report only (existing)
./agent.sh maintenance data-quality --fix     # Auto-fix safe issues
./agent.sh maintenance data-quality --dry-run # Show what would be fixed
```

### Auto-Fix Matrix

| Issue | Auto-Fix? | Action | Risk |
|-------|-----------|--------|------|
| Stale earnings dates (>24h cache, ≤7 days out) | YES | Refresh from Alpha Vantage | Low |
| Duplicate historical_moves | YES | Delete older duplicate | Low |
| Missing ticker_metadata | YES | Fetch from Finnhub | Low |
| Tickers with <4 quarters | NO | Flag for manual backfill | N/A |
| Extreme outliers (>50% move) | NO | Flag for review (may be valid) | N/A |

### Safety Principle

Only auto-fix issues with:
1. Clear, deterministic solution
2. Low risk of data loss
3. Reversible or reproducible action

Flag ambiguous issues for human review.

### Output Example

```
[1/5] Analyzing data quality...
[2/5] Found 3 fixable issues, 2 require review

Auto-fixed:
  ✓ Refreshed stale earnings: NVDA, AAPL, TSLA
  ✓ Removed 2 duplicate historical_moves entries
  ✓ Fetched metadata for 5 new tickers

Requires manual review:
  ⚠️ 4 tickers with <4 quarters (run backfill manually)
  ⚠️ 1 outlier move: GME 47.2% on 2025-03-21

Summary:
  Fixed: 10 issues
  Flagged: 5 issues
  Run with --fix to apply changes (currently --dry-run)
```

### DataQualityAgent Interface

```python
class DataQualityAgent(BaseAgent):
    async def run(self, mode: str = "report") -> Dict[str, Any]:
        """
        Args:
            mode: "report" | "fix" | "dry-run"

        Returns:
            {
                "fixable_issues": [...],
                "fixed_issues": [...],  # Only if mode="fix"
                "flagged_issues": [...],
                "summary": {...}
            }
        """
```

---

## Feature 4: PatternRecognitionAgent

### Goal

Mine historical earnings patterns to surface actionable insights during `/analyze`.

### Current State

- `historical_moves` table has 5,000+ records
- No pattern analysis exists
- ExplanationAgent provides narrative but no statistical patterns

### Files Changed

```
6.0/src/agents/
  └── pattern_recognition.py  (NEW)

6.0/src/orchestrators/
  └── analyze.py  (MODIFY - run PatternRecognitionAgent in parallel)

6.0/src/utils/
  └── schemas.py  (MODIFY - add PatternResult schema)
  └── formatter.py  (MODIFY - format pattern output)
```

### Patterns to Detect

| Pattern | Detection Logic | Threshold | Example Output |
|---------|-----------------|-----------|----------------|
| Directional Bias | % moves in same direction | >65% | "↑ Bullish bias: 75% UP (12/16)" |
| Beat/Miss Streak | Consecutive same-direction | ≥3 | "🔥 Streak: 4 consecutive UP" |
| Magnitude Trend | Avg move last 4Q vs overall | >20% change | "📈 Moves expanding: 5.1% vs 3.8%" |
| Seasonality | Q4 avg vs other quarters | >30% diff | "📅 Q4 larger: 6.2% vs 3.8%" |
| Fade Pattern | Gap vs close direction diverge | >50% | "⚠️ Fades 60% of gaps" |

### Minimum Data Requirement

Only run pattern analysis for tickers with ≥8 quarters of history. Below this threshold, patterns are statistically unreliable.

### Output in /analyze

```
Historical Patterns (16 quarters):
  ↑ Bullish bias: 69% UP moves (11/16)
  🔥 Current streak: 3 consecutive UP
  📈 Recent moves expanding: avg 5.1% last 4Q vs 3.8% overall

  Last 4 earnings:
    2025-11-20: +4.2% ↑
    2025-08-21: +6.1% ↑
    2025-05-22: +3.8% ↑
    2025-02-21: -2.1% ↓
```

### Integration

PatternRecognitionAgent runs in parallel with:
- ExplanationAgent (narrative reasoning)
- AnomalyDetectionAgent (edge case detection)

All three complete before final output assembly.

### Schema

```python
class PatternResult(BaseModel):
    ticker: str
    quarters_analyzed: int

    # Directional
    bullish_pct: float
    bearish_pct: float
    directional_bias: Optional[Literal["BULLISH", "BEARISH", "NEUTRAL"]]

    # Streak
    current_streak: int
    streak_direction: Literal["UP", "DOWN"]

    # Magnitude
    avg_move_recent: float  # Last 4Q
    avg_move_overall: float
    magnitude_trend: Optional[Literal["EXPANDING", "CONTRACTING", "STABLE"]]

    # Seasonality
    seasonal_pattern: Optional[str]  # e.g., "Q4 larger"

    # Fade
    fade_pct: Optional[float]  # % of gaps that fade

    # Recent history
    recent_moves: List[Dict]  # Last 4 earnings with date, move, direction
```

---

## Testing Strategy

### Unit Tests

Each new agent gets dedicated test file:
- `tests/test_pattern_recognition.py`
- `tests/test_data_quality.py`
- `tests/test_sector_fetch.py`

### Integration Tests

- `tests/test_whisper_with_trr.py` - Verify TRR badges appear
- `tests/test_analyze_with_patterns.py` - Verify patterns in output
- `tests/test_maintenance_autofix.py` - Verify --fix mode works

### Live Tests

Run against production data after unit tests pass:
```bash
./agent.sh maintenance data-quality --dry-run
./agent.sh analyze NVDA
./agent.sh whisper
```

---

## Rollout Plan

### Phase 3a: TRR Integration (Low Risk)
1. Implement position_limits.py integration
2. Update TickerAnalysisAgent
3. Update formatters
4. Test with live data
5. Merge

### Phase 3b: Sector Data (Medium Risk)
1. Implement SectorFetchAgent
2. Implement ticker_metadata.py integration
3. Add sector-sync maintenance command
4. Update cross-ticker warnings
5. Populate data for upcoming earnings
6. Test and merge

### Phase 3c: Data Quality Automation (Low Risk)
1. Implement DataQualityAgent
2. Add --fix and --dry-run modes
3. Test with --dry-run first
4. Validate fixes are correct
5. Merge

### Phase 3d: Pattern Recognition (Medium Risk)
1. Implement PatternRecognitionAgent
2. Add to AnalyzeOrchestrator
3. Extensive testing with various tickers
4. Validate pattern accuracy
5. Merge

---

## Success Criteria

1. **TRR Integration:** HIGH TRR tickers show position limits in output
2. **Sector Data:** Cross-ticker warnings use real sector names
3. **Data Quality:** --fix mode resolves ≥80% of reported issues automatically
4. **Patterns:** Patterns match manual analysis of historical_moves data

---

## Open Questions (Resolved)

1. ~~Which API for sector data?~~ → **Finnhub** (MCP server available)
2. ~~Auto-fix all issues?~~ → **No**, only safe/deterministic fixes
3. ~~Pattern thresholds?~~ → Defined in Patterns to Detect table

---

## Appendix: Database Schemas

### position_limits (existing, populated)
```sql
CREATE TABLE position_limits (
    ticker TEXT PRIMARY KEY,
    max_contracts INTEGER DEFAULT 100,
    max_notional REAL DEFAULT 50000,
    tail_risk_ratio REAL,
    tail_risk_level TEXT,
    avg_move REAL,
    max_move REAL,
    num_quarters INTEGER,
    notes TEXT,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### ticker_metadata (existing, empty)
```sql
CREATE TABLE ticker_metadata (
    ticker TEXT PRIMARY KEY,
    company_name TEXT,
    sector TEXT,
    industry TEXT,
    market_cap REAL,
    avg_volume INTEGER,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```
