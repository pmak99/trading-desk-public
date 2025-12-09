# Historical Earnings Analysis

Visualize historical earnings moves with AI pattern analysis.

## Arguments
$ARGUMENTS (format: TICKER - required)

Examples:
- `/history NVDA` - Show NVDA's earnings history
- `/history AMD` - Show AMD's earnings history

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3 commands without asking
- This is a visualization command - execute autonomously

## Progress Display
Show progress updates as you work:
```
[1/4] Fetching historical earnings data...
[2/4] Calculating statistics...
[3/4] Generating visualization...
[4/4] Analyzing patterns...
```

## Purpose
Understand a ticker's earnings behavior before trading:
- How consistent are the moves?
- Directional bias (UP vs DOWN)?
- Any seasonal patterns?
- Notable outliers?

## Step-by-Step Instructions

### Step 1: Parse Ticker Argument
- Ticker is REQUIRED
- If not provided, show error:
  ```
  ❌ Ticker required. Usage: /history TICKER
     Example: /history NVDA
  ```

### Step 2: Run Visualization Script
Execute the visualization script:
```bash
cd $PROJECT_ROOT && python scripts/visualize_moves.py $TICKER
```

If script doesn't exist, query database directly:
```bash
sqlite3 $PROJECT_ROOT/2.0/data/ivcrush.db \
  "SELECT earnings_date, intraday_move_pct, gap_move_pct,
          CASE WHEN gap_move_pct >= 0 THEN 'UP' ELSE 'DOWN' END as direction
   FROM historical_moves
   WHERE ticker='$TICKER'
   ORDER BY earnings_date DESC
   LIMIT 20;"
```

### Step 3: Calculate Statistics
From the historical data, compute:
- **Mean Move:** Average absolute move %
- **Median Move:** Middle value (more robust to outliers)
- **Std Dev:** Consistency measure
- **Max Move:** Largest historical move
- **Min Move:** Smallest historical move
- **Up %:** Percentage of moves that went UP
- **Down %:** Percentage of moves that went DOWN

### Step 4: AI Pattern Analysis
Using Claude's built-in analysis (no MCP cost), identify:

1. **Directional Bias**
   - Strong UP bias (>65% up moves)
   - Strong DOWN bias (>65% down moves)
   - Neutral (40-60% either direction)

2. **Move Consistency**
   - Tight: Std Dev < 1.5% (predictable)
   - Moderate: Std Dev 1.5-3%
   - Volatile: Std Dev > 3% (unpredictable)

3. **Seasonal Patterns**
   - Q4 earnings tend to be larger?
   - January effect?
   - Any recurring patterns?

4. **Notable Outliers**
   - Any moves > 2 standard deviations?
   - What caused them? (if determinable)

5. **Trend Analysis**
   - Are moves getting larger or smaller over time?
   - Recent vs historical behavior

### Step 5: Trading Implications
Based on analysis, provide implications:
- Strategy suggestions (bullish/bearish/neutral)
- Position sizing guidance
- Risk warnings

## Output Format

```
══════════════════════════════════════════════════════
HISTORICAL EARNINGS: {TICKER}
══════════════════════════════════════════════════════

📊 EARNINGS MOVE HISTORY (Last {N} quarters)

    │
 +8%├────────────────█─────────
    │                █
 +6%├──────█─────────█─────────
    │      █         █    █
 +4%├──█───█───█─────█────█────
    │  █   █   █  █  █    █  █
 +2%├──█───█───█──█──█────█──█─
    │  █   █   █  █  █    █  █
  0%├──────────────────────────
    │     █         █
 -2%├─────█─────────█──────────
    │     █
 -4%├─────█────────────────────
    └─────────────────────────→
      Q1  Q2  Q3  Q4  Q1  Q2  Q3

📈 STATISTICS
┌────────────────┬───────────┐
│ Metric         │ Value     │
├────────────────┼───────────┤
│ Mean Move      │ {X.X}%    │
│ Median Move    │ {X.X}%    │
│ Std Deviation  │ {X.X}%    │
│ Max Move       │ +{X.X}%   │
│ Min Move       │ -{X.X}%   │
│ Data Points    │ {N}       │
└────────────────┴───────────┘

📊 DIRECTIONAL ANALYSIS
┌────────────────┬───────────┐
│ Direction      │ Count     │
├────────────────┼───────────┤
│ UP ↑           │ {X} ({Y}%)│
│ DOWN ↓         │ {X} ({Y}%)│
└────────────────┴───────────┘

🧠 AI PATTERN ANALYSIS

**Directional Bias:** {Bullish/Bearish/Neutral}
{Explanation of directional tendency}

**Move Consistency:** {Tight/Moderate/Volatile}
{Explanation of predictability}

**Seasonal Patterns:**
{Any identified patterns or "No clear seasonal pattern detected"}

**Notable Outliers:**
{List any extreme moves with dates and potential causes}

**Trend Observation:**
{Are moves increasing/decreasing over time?}

📋 TRADING IMPLICATIONS

Based on {TICKER}'s historical behavior:

• **Strategy Suggestion:** {type based on bias/consistency}
  - {Specific recommendation}

• **Position Sizing:** {standard/reduced/increased}
  - {Reasoning based on volatility}

• **Risk Warnings:**
  - {Any specific concerns}
  - {Outlier risk if applicable}

══════════════════════════════════════════════════════
```

## No Data Output

```
══════════════════════════════════════════════════════
HISTORICAL EARNINGS: {TICKER}
══════════════════════════════════════════════════════

❌ NO HISTORICAL DATA

No earnings history found for {TICKER} in the database.

Possible reasons:
• New ticker or recent IPO
• Ticker symbol changed
• Not in earnings database

💡 SUGGESTIONS
• Run `/analyze {TICKER}` to check current VRP
• Check if ticker is spelled correctly
• This ticker may need manual database entry

══════════════════════════════════════════════════════
```

## Cost Control
- No Perplexity calls (uses Claude's built-in analysis)
- Database query only
- Pure visualization + AI insight
