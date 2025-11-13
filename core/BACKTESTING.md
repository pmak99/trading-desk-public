# IV Crush 2.0 - Backtesting System

Complete guide to the A/B testing and backtesting framework for optimizing ticker selection weights.

## Overview

The backtesting system allows you to:
- **Test multiple scoring configurations** on historical data
- **Compare performance** across different weight combinations
- **Identify optimal weights** for your trading style
- **Validate strategies** before risking capital

## Quick Start

```bash
# 1. Backfill historical data (Q3-Q4 2024)
python scripts/backfill_yfinance.py --file data/backfill_tickers.txt \
    --start-date 2024-07-01 --end-date 2024-12-31

# 2. Run A/B tests on all 8 configurations
python scripts/run_backtests.py \
    --start-date 2024-07-01 --end-date 2024-12-31 \
    --output data/backtest_results.json

# 3. Run deep analysis
python scripts/analyze_backtest_results.py
```

## Architecture

### Components

1. **ScoringConfig** (`src/config/scoring_config.py`)
   - Defines weight configurations
   - 8 pre-built configs: VRP-Dominant, Balanced, Liquidity-First, etc.
   - Customizable thresholds and limits

2. **TickerScorer** (`src/application/services/scorer.py`)
   - Scores tickers on 0-100 scale
   - Combines VRP, consistency, skew, and liquidity
   - Ranks and selects top N candidates

3. **BacktestEngine** (`src/application/services/backtest_engine.py`)
   - Simulates historical trades
   - Calculates performance metrics
   - Handles edge cases (limited data, etc.)

4. **Scripts**
   - `backfill_yfinance.py` - Populate historical data
   - `run_backtests.py` - Execute A/B tests
   - `analyze_backtest_results.py` - Deep analysis

## Scoring System

### Component Scores (0-100 scale)

**VRP Score:**
- Based on VRP ratio (implied / historical move)
- Excellent (≥2.0x): 100 pts
- Good (≥1.5x): 75 pts
- Marginal (≥1.2x): 50 pts

**Consistency Score:**
- Based on predictability of historical moves
- Uses coefficient of variation
- Higher = more consistent moves

**Skew Score:**
- Neutral skew (±15%): 100 pts (best for straddles)
- Moderate skew: 70 pts
- Extreme skew: 40 pts

**Liquidity Score:**
- Open Interest: 0-10 pts
- Bid-Ask Spread: 0-10 pts
- Volume: 0-5 pts

### Composite Score

```
Composite = VRP_score × VRP_weight +
            Consistency_score × Consistency_weight +
            Skew_score × Skew_weight +
            Liquidity_score × Liquidity_weight
```

Tickers with composite score ≥ threshold are qualified.

## Configurations

### 1. VRP-Dominant (Baseline)
```python
VRP: 70%, Consistency: 20%, Skew: 5%, Liquidity: 5%
Min Score: 65
Max Positions: 10
```
**Use when:** You want raw edge and willing to trade less liquid names

### 2. Balanced
```python
VRP: 40%, Consistency: 25%, Skew: 15%, Liquidity: 20%
Min Score: 60
Max Positions: 12
```
**Use when:** You want a well-rounded approach

### 3. Liquidity-First ⭐ **Recommended**
```python
VRP: 30%, Consistency: 20%, Skew: 15%, Liquidity: 35%
Min Score: 60
Max Positions: 10
```
**Use when:** You prioritize execution quality and tight spreads

### 4. Consistency-Heavy ⭐ **Best Sharpe**
```python
VRP: 35%, Consistency: 45%, Skew: 10%, Liquidity: 10%
Min Score: 65
Max Positions: 8
```
**Use when:** You want predictable, high-quality trades

### 5. Skew-Aware
```python
VRP: 35%, Consistency: 20%, Skew: 30%, Liquidity: 15%
Min Score: 60
Max Positions: 10
```
**Use when:** You use skew for directional overlays

### 6. Aggressive
```python
VRP: 55%, Consistency: 20%, Skew: 10%, Liquidity: 15%
Min Score: 50 (lower threshold)
Max Positions: 15
```
**Use when:** You want volume and can handle more variance

### 7. Conservative
```python
VRP: 40%, Consistency: 35%, Skew: 15%, Liquidity: 10%
Min Score: 70 (higher threshold)
Max Positions: 6
```
**Use when:** You want only the highest-conviction trades

### 8. Hybrid
```python
VRP: 45%, Consistency: 20%, Skew: 15%, Liquidity: 20%
Min Score: 62
Max Positions: 10
```
**Use when:** You want adaptive balanced approach

## Backtest Results (Q3-Q4 2024)

### Top 3 Performers

| Config | Sharpe | Win% | Avg P&L | Total P&L | Trades |
|--------|--------|------|---------|-----------|--------|
| **Consistency-Heavy** | **0.28** | **62.5%** | **0.91%** | 7.31% | 8 |
| VRP-Dominant | 0.27 | 60.0% | 0.79% | 7.90% | 10 |
| Liquidity-First | 0.27 | 60.0% | 0.79% | 7.90% | 10 |

### Key Insights

1. **Consistency matters** - Highest Sharpe and win rate
2. **Best tickers:** UNH, WFC, BAC, JNJ, GS (100% win rate)
3. **Worst tickers:** MS, JPM, C, NFLX, AMD (0% win rate)
4. **Sortino ratio:** Consistency-Heavy = 0.54 (best downside protection)

## Customizing Configurations

### Create Your Own Config

```python
from src.config.scoring_config import ScoringConfig, ScoringWeights, ScoringThresholds

my_config = ScoringConfig(
    name="MyCustom",
    description="Custom config for my style",
    weights=ScoringWeights(
        vrp_weight=0.45,
        consistency_weight=0.30,
        skew_weight=0.15,
        liquidity_weight=0.10,
    ),
    thresholds=ScoringThresholds(),
    max_positions=12,
    min_score=58.0,
)
```

### Run Custom Backtest

```python
from src.application.services.backtest_engine import BacktestEngine
from datetime import date

engine = BacktestEngine(Path("data/ivcrush.db"))
result = engine.run_backtest(
    my_config,
    start_date=date(2024, 7, 1),
    end_date=date(2024, 12, 31),
)
```

## Interpreting Results

### Sharpe Ratio
- **>0.5:** Excellent
- **0.3-0.5:** Good
- **0.1-0.3:** Acceptable
- **<0.1:** Poor

### Win Rate
- **>65%:** Excellent
- **55-65%:** Good
- **45-55%:** Acceptable (if high avg P&L)
- **<45%:** Concerning

### Sortino Ratio
- Better than Sharpe (only penalizes downside)
- **>0.7:** Excellent
- **0.4-0.7:** Good
- **<0.4:** Needs improvement

## Limitations

1. **Simplified P&L model** - Uses 50% premium collection estimate
2. **No slippage/commissions** - Real P&L will be slightly lower
3. **Limited historical data** - Only Q3-Q4 2024 in current backtest
4. **No liquidity data** - Simulated values used for backtest
5. **No skew data** - Defaults to neutral in backtest

## Next Steps

1. **Start with recommended config** (Liquidity-First or Consistency-Heavy)
2. **Paper trade for 10-20 earnings events**
3. **Track actual vs backtested performance**
4. **Adjust weights** based on live results:
   - Too few trades → lower min_score or adjust weights
   - Too many losers → increase consistency_weight
   - Bad fills → increase liquidity_weight

## Database Schema

```sql
-- Backtest runs
CREATE TABLE backtest_runs (
    run_id TEXT PRIMARY KEY,
    config_name TEXT,
    start_date DATE,
    end_date DATE,
    sharpe_ratio REAL,
    win_rate REAL,
    ...
);

-- Individual trades
CREATE TABLE backtest_trades (
    id INTEGER PRIMARY KEY,
    run_id TEXT,
    ticker TEXT,
    earnings_date DATE,
    composite_score REAL,
    simulated_pnl REAL,
    ...
);
```

## Files Reference

- `src/config/scoring_config.py` - Weight configurations
- `src/application/services/scorer.py` - Scoring engine
- `src/application/services/backtest_engine.py` - Backtest engine
- `scripts/run_backtests.py` - A/B testing runner
- `scripts/analyze_backtest_results.py` - Deep analysis
- `data/backtest_results.json` - Latest results

## FAQ

**Q: Why are some configs tied in performance?**
A: With limited backtest data (40 events), small weight differences may select identical tickers.

**Q: How do I add more historical data?**
A: Run `backfill_yfinance.py` with earlier dates or more tickers.

**Q: Can I backtest on 2023 data?**
A: Yes! Adjust date range in backfill and backtest scripts.

**Q: Should I use the exact weights from backtest?**
A: Start with them, but adjust based on live execution quality.

**Q: What if my broker doesn't have good liquidity?**
A: Increase liquidity_weight to 40-45% and use Liquidity-First config.

## Support

For issues or questions:
1. Check DEPLOYMENT.md and RUNBOOK.md
2. Review test coverage: `pytest tests/ -v`
3. Examine logs in backtest output
