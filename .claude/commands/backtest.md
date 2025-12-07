# Backtest Report Generator

Generate comprehensive backtest reports analyzing historical IV Crush strategy performance.

## Quick Backtest Report

Run the full backtest report:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk && python scripts/backtest_report.py
```

For a specific ticker:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk && python scripts/backtest_report.py NVDA
```

## Manual Queries

Analyze historical performance using the moves database:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && sqlite3 -header data/ivcrush.db "
SELECT
    COUNT(*) as total_earnings,
    AVG(actual_move_pct) as avg_move,
    MAX(actual_move_pct) as max_move,
    MIN(actual_move_pct) as min_move,
    SUM(CASE WHEN direction = 'UP' THEN 1 ELSE 0 END) as up_moves,
    SUM(CASE WHEN direction = 'DOWN' THEN 1 ELSE 0 END) as down_moves
FROM historical_moves
"
```

## Backtest Scenarios

### 1. VRP Threshold Analysis
Test different VRP thresholds to find optimal entry criteria:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && sqlite3 data/ivcrush.db "
SELECT ticker, COUNT(*) as earnings_count, AVG(actual_move_pct) as avg_move
FROM historical_moves
GROUP BY ticker
HAVING COUNT(*) >= 4
ORDER BY avg_move ASC
LIMIT 20
"
```

Best tickers for IV Crush = Low average actual moves (consistently small movers)

### 2. Win Rate by Move Size
Calculate theoretical win rate at different implied move levels:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && sqlite3 data/ivcrush.db "
SELECT
    CASE
        WHEN actual_move_pct < 5 THEN '0-5%'
        WHEN actual_move_pct < 10 THEN '5-10%'
        WHEN actual_move_pct < 15 THEN '10-15%'
        ELSE '15%+'
    END as move_bucket,
    COUNT(*) as count,
    AVG(actual_move_pct) as avg_move
FROM historical_moves
GROUP BY move_bucket
ORDER BY move_bucket
"
```

### 3. Seasonal Analysis
Performance by month/quarter:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && sqlite3 data/ivcrush.db "
SELECT
    strftime('%m', earnings_date) as month,
    COUNT(*) as earnings_count,
    AVG(actual_move_pct) as avg_move
FROM historical_moves
GROUP BY month
ORDER BY month
"
```

### 4. Direction Bias Analysis
Analyze up vs down move distribution:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && sqlite3 data/ivcrush.db "
SELECT
    direction,
    COUNT(*) as count,
    AVG(actual_move_pct) as avg_move,
    MAX(actual_move_pct) as max_move
FROM historical_moves
GROUP BY direction
"
```

## Backtest Report Template

Generate a comprehensive report including:

### Executive Summary
- Total earnings events analyzed
- Historical win rate (actual < implied)
- Average move vs typical implied move
- Best and worst performing periods

### Risk Metrics
- Maximum drawdown scenario
- Worst single event (largest actual move)
- Tail risk (moves > 20%)
- VaR at 95% confidence

### Strategy Optimization
- Optimal VRP threshold based on historical data
- Best tickers for strategy (low volatility, consistent)
- Tickers to avoid (high volatility, unpredictable)
- Seasonal adjustments

### Performance Attribution
- P&L by ticker
- P&L by month
- Win rate trends over time
- Profit factor analysis

## Live Performance Comparison

Compare backtest to actual 2025 trading results:

```bash
cat "$PROJECT_ROOT/docs/2025 Trades/trading_data_2025_v3.json"
```

Report should show:
- Backtest expected win rate vs actual
- Backtest expected P&L vs actual
- Variance analysis
- Areas for improvement

## Key Metrics to Calculate

| Metric | Formula | Target |
|--------|---------|--------|
| Win Rate | Wins / Total Trades | > 55% |
| Profit Factor | Gross Wins / Gross Losses | > 1.2 |
| Sharpe Ratio | (Return - Rf) / Std Dev | > 1.0 |
| Max Drawdown | Peak to Trough | < 15% |
| Avg Win/Loss | Avg Win / Avg Loss | > 0.8 |

## Usage Examples

"Run a backtest report for NVDA"
"Analyze seasonal performance patterns"
"Compare my 2025 results to historical expectations"
"Find the optimal VRP threshold"
"Which tickers should I focus on?"

## Database Info

Historical moves database: `2.0/data/ivcrush.db`
- Table: `historical_moves`
- Records: 4,926 earnings events
- Fields: ticker, earnings_date, close_before, close_after, actual_move_pct, direction
