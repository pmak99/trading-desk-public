# Visualize Trading Data (Charting Skill)

Generate charts and visualizations for trading analysis using available data.

## Quick Visualization Script

For a specific ticker with implied move comparison:

```bash
cd $PROJECT_ROOT && python scripts/visualize_moves.py TICKER [IMPLIED_MOVE]
```

Example: `python scripts/visualize_moves.py NVDA 12.5`

## Available Visualizations

### 1. Historical Move Distribution
Show how a ticker's actual earnings moves compare to current implied move:

```bash
cd $PROJECT_ROOT/2.0 && sqlite3 -header -csv data/ivcrush.db "SELECT ticker, earnings_date, actual_move_pct, direction FROM historical_moves WHERE ticker = 'TICKER' ORDER BY earnings_date DESC LIMIT 20"
```

Create ASCII histogram or describe distribution:
- Mean, median, std dev of moves
- How current implied move compares
- Number of times stock exceeded implied move

### 2. VRP Ratio Trends
For a scanned date, show VRP distribution across tickers:

```bash
cd $PROJECT_ROOT/2.0 && ./trade.sh scan DATE 2>&1 | grep -E "VRP|EXCELLENT|GOOD|MARGINAL"
```

Visualize:
- VRP ratio distribution (histogram)
- Tier breakdown (pie chart description)
- Top VRP opportunities ranked

### 3. P&L Over Time
Read trade journal and show cumulative P&L:

```bash
cat "$PROJECT_ROOT/docs/2025 Trades/trading_data_2025_v3.json"
```

Generate:
- Cumulative P&L curve (text-based or description)
- Monthly P&L bar chart
- Win rate trend over time

### 4. Liquidity Analysis
Show liquidity tier distribution for a scan:
- EXCELLENT vs WARNING vs REJECT breakdown
- Correlation between liquidity and recommended strategy
- Tickers to avoid due to liquidity

### 5. Implied Move vs Actual Move
Compare predicted (implied) vs actual outcomes:

```bash
cd $PROJECT_ROOT/2.0 && sqlite3 data/ivcrush.db "SELECT ticker, earnings_date, actual_move_pct FROM historical_moves ORDER BY earnings_date DESC LIMIT 50"
```

Show:
- Scatter plot description (implied vs actual)
- Mean absolute error
- How often actual < implied (strategy success rate)

## Chart Output Formats

### ASCII Art Charts
For terminal display, generate ASCII-based:
- Bar charts using `|` and `=` characters
- Histograms with frequency buckets
- Sparklines for trends

### Markdown Tables
Structured data in table format for easy reading

### Chart Descriptions
For complex visualizations, describe what the chart would show:
- Axes labels and scales
- Key data points and trends
- Interpretation and insights

## Usage Examples

"Show me NVDA's historical earnings moves"
"Visualize today's scan results by VRP tier"
"Chart my YTD P&L curve"
"Compare implied vs actual moves for recent earnings"

## Data Sources

- **Historical moves**: `2.0/data/ivcrush.db` (4,926 records)
- **Trade journal**: `docs/2025 Trades/trading_data_2025_v3.json`
- **Scan results**: Live from `./trade.sh scan`
