# Export Trading Report

Generate formatted trading reports and export scan results to spreadsheets (CSV/JSON).

## Arguments
$ARGUMENTS (optional: scan | journal | performance | TICKER)

Examples:
- `/export-report` - Export today's scan results
- `/export-report scan` - Export today's scan to CSV/JSON
- `/export-report journal` - Export trade journal summary
- `/export-report performance` - Export performance summary
- `/export-report NVDA` - Export all NVDA trades

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, python, sqlite3, Read commands without asking
- This is a utility command - execute autonomously

## Progress Display
```
[1/3] Gathering data...
[2/3] Formatting and exporting...
[3/3] Writing output files...
```

## Step-by-Step Instructions

### Mode 1: Export Scan Results (default or `scan`)

**Run scan and pipe to export script:**
```bash
cd "$PROJECT_ROOT/core" && ./trade.sh scan $(date +%Y-%m-%d) 2>&1 | "$PROJECT_ROOT/core/venv/bin/python" "$PROJECT_ROOT/scripts/export_scan_results.py"
```

Output files in `core/docs/scan_exports/`:
- `scan_YYYYMMDD.csv` - CSV format
- `scan_YYYYMMDD.json` - JSON format

**CSV columns:** ticker, scan_date, vrp_ratio, vrp_tier, implied_move_pct, historical_mean_pct, liquidity_tier, edge_score, tradeable, recommended_strategy, pop, overall_score

### Mode 2: Export Trade Journal (`journal`)

Query strategies database and format:
```bash
sqlite3 -header -csv "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT symbol, strategy_type, acquired_date, sale_date, days_held,
          gain_loss, is_winner, trade_type, campaign_id, trr_at_entry
   FROM strategies
   ORDER BY sale_date DESC;"
```

Save output to a timestamped file:
```bash
sqlite3 -header -csv "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT symbol, strategy_type, acquired_date, sale_date, days_held,
          gain_loss, is_winner, trade_type, campaign_id, trr_at_entry
   FROM strategies
   ORDER BY sale_date DESC;" > "$PROJECT_ROOT/core/docs/scan_exports/strategies_$(date +%Y%m%d).csv"
```

### Mode 3: Export Performance Summary (`performance`)

Generate a comprehensive performance CSV:
```bash
sqlite3 -header -csv "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT symbol,
          strategy_type,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(AVG(gain_loss), 0) as avg_pnl,
          ROUND(AVG(CASE WHEN is_winner THEN gain_loss END), 0) as avg_win,
          ROUND(AVG(CASE WHEN NOT is_winner THEN gain_loss END), 0) as avg_loss
   FROM strategies
   GROUP BY symbol, strategy_type
   ORDER BY total_pnl DESC;" > "$PROJECT_ROOT/core/docs/scan_exports/performance_$(date +%Y%m%d).csv"
```

### Mode 4: Export Ticker Report (`TICKER`)

Export all trades for a specific ticker:
```bash
TICKER=$(echo "$RAW" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 -header -csv "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT s.symbol, s.strategy_type, s.acquired_date, s.sale_date,
          s.gain_loss, s.is_winner, s.trade_type, s.campaign_id,
          tj.option_type, tj.strike, tj.expiration, tj.cost_basis, tj.proceeds
   FROM strategies s
   LEFT JOIN trade_journal tj ON tj.strategy_id = s.id
   WHERE s.symbol='$TICKER'
   ORDER BY s.sale_date DESC;" > "$PROJECT_ROOT/core/docs/scan_exports/${TICKER}_trades_$(date +%Y%m%d).csv"
```

Also query historical moves:
```bash
sqlite3 -header -csv "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT * FROM historical_moves
   WHERE ticker='$TICKER'
   ORDER BY earnings_date DESC;" > "$PROJECT_ROOT/core/docs/scan_exports/${TICKER}_history_$(date +%Y%m%d).csv"
```

## Output Format

```
==============================================================
EXPORT REPORT
==============================================================

Export Type: {scan/journal/performance/TICKER}

FILES GENERATED:
  CSV:  core/docs/scan_exports/{filename}.csv
  JSON: core/docs/scan_exports/{filename}.json (if applicable)

SUMMARY:
  Records exported: {N}
  {Mode-specific summary}

The files are ready in the scan_exports directory.
==============================================================
```

## Cost Control
- No MCP usage (local data only)
- Database queries and file writes only
