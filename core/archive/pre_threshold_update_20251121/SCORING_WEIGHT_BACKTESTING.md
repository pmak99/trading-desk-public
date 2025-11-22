# Scoring Weight Backtesting Guide

Complete guide to testing and optimizing scoring weights using both historical backtesting and paper trading.

## Two Complementary Approaches

### 1. **Historical Backtesting** (Fast, Comprehensive)
- Tests on past earnings events with known outcomes
- Runs in minutes, covers months/years of data
- Best for: Initial weight optimization, rapid iteration

### 2. **Paper Trading** (Realistic, Forward-Looking)
- Tests on upcoming earnings with real market conditions
- Runs in real-time, validates live execution
- Best for: Final validation, detecting regime changes

## Recommendation: Use Both

1. **Start with historical backtesting** to identify top 2-3 configs
2. **Validate with paper trading** for 4-8 weeks before going live
3. **Monitor continuously** and re-optimize quarterly

---

## Historical Backtesting (Existing System)

### Quick Start

```bash
# Step 1: Ensure you have historical data (Q2-Q4 2024)
cd "Trading Desk/2.0"

# Step 2: Run backtests on all 8 configurations
python scripts/run_backtests.py \
    --start-date 2024-04-01 \
    --end-date 2024-12-31 \
    --output results/backtest_results.json

# Step 3: Analyze results
python scripts/analyze_backtest_results.py

# Step 4: Test specific configuration
python scripts/run_backtests.py \
    --config balanced \
    --start-date 2024-07-01 \
    --end-date 2024-12-31
```

### Available Configurations

Based on `src/config/scoring_config.py`:

| Config | VRP | Consistency | Skew | Liquidity | Best For |
|--------|-----|-------------|------|-----------|----------|
| **Balanced** | 40% | 25% | 15% | 20% | Well-rounded approach |
| **Liquidity-First** | 30% | 20% | 15% | 35% | Tight spreads, large sizes |
| **Consistency-Heavy** | 35% | 45% | 10% | 10% | Predictable moves (best Sharpe) |
| VRP-Dominant | 70% | 20% | 5% | 5% | Maximum edge focus |
| Skew-Aware | 35% | 20% | 30% | 15% | Directional overlays |
| Aggressive | 55% | 20% | 10% | 15% | Higher volume |
| Conservative | 40% | 35% | 15% | 10% | High conviction only |
| Hybrid | 45% | 20% | 15% | 20% | Adaptive balanced |

### Performance (Q2-Q4 2024)

From `BACKTESTING.md`:

| Config | Sharpe | Win Rate | Avg P&L | Total P&L | Trades |
|--------|--------|----------|---------|-----------|--------|
| **Consistency-Heavy** | **0.28** | **62.5%** | **0.91%** | 7.31% | 8 |
| VRP-Dominant | 0.27 | 60.0% | 0.79% | 7.90% | 10 |
| Liquidity-First | 0.27 | 60.0% | 0.79% | 7.90% | 10 |

### Customizing Weights

```python
from src.config.scoring_config import ScoringConfig, ScoringWeights, ScoringThresholds
from src.application.services.backtest_engine import BacktestEngine
from datetime import date
from pathlib import Path

# Create custom configuration
my_config = ScoringConfig(
    name="MyCustom",
    description="Optimized for my style",
    weights=ScoringWeights(
        vrp_weight=0.45,      # Adjust these!
        consistency_weight=0.30,
        skew_weight=0.15,
        liquidity_weight=0.10,
    ),
    thresholds=ScoringThresholds(),
    max_positions=10,
    min_score=60.0,
)

# Run backtest
engine = BacktestEngine(Path("data/ivcrush.db"))
result = engine.run_backtest(
    config=my_config,
    start_date=date(2024, 4, 1),
    end_date=date(2024, 12, 31),
)

# Analyze results
print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
print(f"Win Rate: {result.win_rate:.1f}%")
print(f"Total P&L: {result.total_pnl:.2f}%")
print(f"Trades: {result.selected_trades}")
```

### Backtest Metrics Explained

**Sharpe Ratio**
- Risk-adjusted return (return per unit of volatility)
- >0.5: Excellent | 0.3-0.5: Good | <0.3: Needs work
- **Use this as primary metric for config selection**

**Win Rate**
- Percentage of profitable trades
- >65%: Excellent | 55-65%: Good | <55%: Concerning
- High win rate with low Sharpe = small wins, big losses

**Sortino Ratio**
- Like Sharpe, but only penalizes downside volatility
- Better metric for asymmetric strategies
- >0.7: Excellent | 0.4-0.7: Good

**Max Drawdown**
- Largest peak-to-trough decline
- Indicates worst-case scenario
- Keep below 10-15% for conservative strategies

---

## Paper Trading Backtesting (New)

### Prerequisites

You already have Alpaca MCP configured! Verify with:

```bash
# Check MCP configuration
claude mcp list

# Should show: @alpaca
```

### Quick Start

```bash
# 1. Test single configuration for 4 weeks
python scripts/paper_trading_backtest.py \
    --config balanced \
    --weeks 4

# 2. Compare multiple configurations (sequential)
python scripts/paper_trading_backtest.py \
    --configs balanced,liquidity_first,consistency_heavy \
    --weeks 2

# 3. Monitor existing paper positions
python scripts/paper_trading_backtest.py --monitor

# 4. List available configurations
python scripts/paper_trading_backtest.py --list-configs
```

### How It Works

1. **Weekly Scan**: Fetches upcoming earnings from Alpha Vantage
2. **Score & Select**: Scores tickers using your configuration, selects top N
3. **Place Trades**: Executes paper trades on Alpaca paper account
4. **Monitor**: Tracks positions and calculates P&L
5. **Report**: Generates performance metrics after test period

### Integration with Alpaca MCP

The paper trading script uses your Alpaca MCP integration:

```python
# Example: Place paper trade (in production)
import mcp_alpaca

# Create options order
order = mcp_alpaca.alpaca_create_order(
    symbol=ticker,
    side="sell",          # Sell premium (iron condor, credit spread)
    type="limit",
    qty=contracts,
    limit_price=net_credit,
    time_in_force="day"
)

# Monitor position
positions = mcp_alpaca.alpaca_list_positions()

# Check P&L
for position in positions:
    print(f"{position.symbol}: {position.unrealized_pl}")
```

### Advantages of Paper Trading

1. **Real Market Conditions**
   - Actual bid-ask spreads (not simulated)
   - Real liquidity constraints
   - Market impact and slippage

2. **Forward-Looking**
   - Tests on future events (no look-ahead bias)
   - Detects regime changes
   - Validates in current market environment

3. **Execution Validation**
   - Tests your broker's fills
   - Identifies order routing issues
   - Validates strategy logic end-to-end

4. **Confidence Building**
   - Psychological preparation
   - Workflow testing
   - Risk management validation

### Disadvantages

1. **Slow** - Takes weeks/months to collect data
2. **Limited Data** - Fewer earnings events than historical
3. **Non-Repeatable** - Can't rewind and test different weights
4. **Resource Intensive** - Requires monitoring and maintenance

---

## Combined Workflow (Best Practice)

### Phase 1: Historical Optimization (1-2 days)

```bash
# Step 1: Run full A/B test on historical data
python scripts/run_backtests.py \
    --start-date 2024-04-01 \
    --end-date 2024-12-31 \
    --output results/backtest_q2_q4.json

# Step 2: Analyze and identify top 3 configs
python scripts/analyze_backtest_results.py

# Step 3: Run walk-forward validation (prevents overfitting)
python scripts/run_backtests.py \
    --walk-forward \
    --train-days 180 \
    --test-days 90
```

**Expected Output:**
- Top 3 configs ranked by Sharpe ratio
- Win rate and P&L analysis
- Trade quality metrics (avg score of winners vs losers)

### Phase 2: Paper Trading Validation (4-8 weeks)

```bash
# Test top 3 configs in parallel (sequential execution)
python scripts/paper_trading_backtest.py \
    --configs consistency_heavy,balanced,liquidity_first \
    --weeks 4

# Monitor weekly
python scripts/paper_trading_backtest.py --monitor
```

**What to Look For:**
- Win rate matches historical (±10%)
- Sharpe ratio doesn't degrade significantly
- Execution quality (fills, slippage)
- Number of qualified opportunities

### Phase 3: Live Trading (Continuous)

After successful paper trading:

1. **Start with 1-2 positions** using winning config
2. **Track performance** vs paper trading
3. **Re-optimize quarterly** with new data
4. **A/B test** new configs on paper account

---

## Interpreting Results

### When Historical and Paper Trading Disagree

| Scenario | Likely Cause | Action |
|----------|--------------|--------|
| Good historical, poor paper | Regime change, liquidity issues | Re-evaluate thresholds |
| Poor historical, good paper | Lucky paper trades | Need more paper data |
| Both good | ✅ High confidence | Deploy to live trading |
| Both poor | Config not suitable | Try different weights |

### Red Flags 🚩

- **Win rate drops >15%** from historical to paper → Overfitting
- **Sharpe < 0.1** in paper trading → Not enough edge
- **Max drawdown > 20%** → Too much risk
- **Execution quality poor** (slippage >5%) → Increase liquidity_weight

### Green Flags ✅

- **Win rate ±10%** of historical
- **Sharpe >0.3** in paper trading
- **Avg winner > Avg loser** (positive expectancy)
- **Execution quality good** (slippage <3%)

---

## Advanced: Custom Optimization

### Grid Search

```python
from itertools import product
from src.config.scoring_config import ScoringConfig, ScoringWeights
from src.application.services.backtest_engine import BacktestEngine

# Define parameter grid
vrp_weights = [0.30, 0.40, 0.50, 0.60]
consistency_weights = [0.20, 0.30, 0.40]
# Keep skew and liquidity fixed for simplicity

results = []

for vrp_w, cons_w in product(vrp_weights, consistency_weights):
    # Ensure weights sum to 1.0
    remaining = 1.0 - vrp_w - cons_w
    if remaining < 0.20:  # Need minimum for skew + liquidity
        continue

    skew_w = 0.15
    liq_w = remaining - skew_w

    config = ScoringConfig(
        name=f"VRP{vrp_w:.0%}_CONS{cons_w:.0%}",
        weights=ScoringWeights(vrp_w, cons_w, skew_w, liq_w),
        # ... other params
    )

    result = engine.run_backtest(config, start_date, end_date)
    results.append((config, result))

# Find best by Sharpe
best = max(results, key=lambda x: x[1].sharpe_ratio)
print(f"Best config: {best[0].name}")
print(f"Sharpe: {best[1].sharpe_ratio:.2f}")
```

### Bayesian Optimization

For more advanced users, consider Bayesian optimization to find optimal weights efficiently.

```bash
pip install scikit-optimize

# Then use Gaussian Process optimization
# See: scikit-optimize docs
```

---

## Troubleshooting

### "No historical data for ticker"

```bash
# Backfill data for Q2-Q4 2024
python scripts/backfill_yfinance.py \
    --file data/backfill_tickers.txt \
    --start-date 2024-04-01 \
    --end-date 2024-12-31
```

### "Alpaca MCP not available"

```bash
# Check MCP status
claude mcp list

# If not configured, add:
claude mcp add --transport stdio alpaca -- \
    npx -y @ideadesignmedia/alpaca-mcp
```

### "All configs perform similarly"

- **Cause**: Limited data or similar weight distributions
- **Solution**:
  1. Extend backtest period (more historical data)
  2. Use more extreme weight differences
  3. Test on different market regimes (2023 vs 2024)

### "Paper trading results differ from historical"

- **Expected!** Market conditions change
- Check:
  1. VIX levels (high vol = better VRP opportunities)
  2. Earnings season density
  3. Liquidity conditions (spreads widening?)

---

## Next Steps

1. ✅ **Run historical backtest** on Q2-Q4 2024 data
2. ✅ **Identify top 3 configs** by Sharpe ratio
3. 🔄 **Run paper trading** for 4 weeks on top configs
4. 📊 **Compare results** and select winner
5. 🚀 **Deploy to live trading** with conservative sizing

---

## Files Reference

**Historical Backtesting:**
- `src/config/scoring_config.py` - Weight definitions
- `src/application/services/backtest_engine.py` - Backtest engine
- `scripts/run_backtests.py` - Backtest runner
- `scripts/analyze_backtest_results.py` - Results analysis
- `BACKTESTING.md` - Full backtesting guide

**Paper Trading:**
- `scripts/paper_trading_backtest.py` - Paper trading script (NEW)
- Alpaca MCP integration (configured)
- `MCP_USAGE_GUIDE.md` - MCP documentation

**Results:**
- `results/backtest_q2_q4.json` - Historical results
- `results/paper_trading_comparison.json` - Paper trading results

---

## FAQ

**Q: Should I trust historical or paper trading results more?**
A: Trust historical for relative comparison (which config is better), trust paper for absolute performance (what returns to expect).

**Q: How long should I paper trade before going live?**
A: Minimum 4 weeks (8-12 earnings trades), ideally 8 weeks (15-20 trades).

**Q: Can I paper trade multiple configs simultaneously?**
A: Not recommended - runs sequentially to avoid interference. Use historical for parallel comparison.

**Q: What if my paper trading account is too small?**
A: Historical backtesting doesn't require capital. Paper trade with $5-10k to test execution, use historical for performance.

**Q: How often should I re-optimize weights?**
A: Quarterly or after significant market regime changes (VIX >30, earnings season density shifts).

**Q: Which config should I start with?**
A: Start with **Balanced** or **Consistency-Heavy** - both have good Sharpe and win rates.
