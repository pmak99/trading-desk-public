# Backtest Performance Analysis

Analyze trading performance with AI-powered insights and recommendations.

## Arguments
$ARGUMENTS (format: [TICKER] - optional)

Examples:
- `/backtest` - Analyze all trades
- `/backtest NVDA` - Analyze NVDA trades only

## Purpose
Review trading performance to identify:
- Win rate by VRP tier
- Best/worst performing tickers
- Strategy type effectiveness
- Lessons from losses

## Step-by-Step Instructions

### Step 1: Parse Arguments
- If ticker provided, filter to that ticker
- If no ticker, analyze all trades

### Step 2: Run Backtest Report Script
Execute the backtest script:
```bash
cd $PROJECT_ROOT && python scripts/backtest_report.py $TICKER
```

If script doesn't exist or fails, query the trade journal directly:
```bash
# Check for trade journal in 4.0/data/
ls $PROJECT_ROOT/4.0/data/*.json 2>/dev/null

# Or query historical trades from any available source
```

### Step 3: Calculate Performance Metrics
From trade data, compute:

**Overall Metrics:**
- Total trades
- Win rate (%)
- Total P&L ($)
- Average win ($)
- Average loss ($)
- Profit factor (gross wins / gross losses)
- Largest win / loss

**By VRP Tier:**
- EXCELLENT (≥7x): Win rate, avg P&L
- GOOD (≥4x): Win rate, avg P&L
- MARGINAL (≥1.5x): Win rate, avg P&L
- SKIP (<1.5x): Win rate, avg P&L (should be 0 trades)

**By Liquidity Tier:**
- EXCELLENT: Win rate, avg P&L
- WARNING: Win rate, avg P&L
- REJECT: Win rate, avg P&L (should be 0 trades)

**By Strategy Type:**
- Iron Condors: Win rate, avg P&L
- Spreads: Win rate, avg P&L
- Naked options: Win rate, avg P&L
- Strangles: Win rate, avg P&L

### Step 4: AI Performance Analysis
Using Claude's built-in analysis (no MCP cost):

1. **Edge Validation**
   - Does higher VRP correlate with better results?
   - Is the VRP threshold (4x) appropriate?

2. **Liquidity Impact**
   - Are WARNING tier trades underperforming?
   - Any REJECT tier violations to flag?

3. **Strategy Effectiveness**
   - Which strategy types work best?
   - Any strategies to avoid?

4. **Ticker Patterns**
   - Best performing tickers (consistent winners)
   - Worst performing tickers (avoid list)
   - Any sector patterns?

5. **Loss Analysis**
   - Common causes of losses
   - Avoidable vs unavoidable losses
   - Lessons to apply

6. **Recommendations**
   - Actionable improvements
   - Risk management suggestions
   - Position sizing adjustments

## Output Format

```
══════════════════════════════════════════════════════
BACKTEST REPORT {[TICKER] or "ALL TRADES"}
══════════════════════════════════════════════════════

📊 OVERALL PERFORMANCE
┌────────────────────┬───────────────┐
│ Metric             │ Value         │
├────────────────────┼───────────────┤
│ Total Trades       │ {N}           │
│ Win Rate           │ {X.X}%        │
│ Total P&L          │ ${X,XXX}      │
│ Average Win        │ ${XXX}        │
│ Average Loss       │ -${XXX}       │
│ Profit Factor      │ {X.XX}        │
│ Largest Win        │ ${X,XXX}      │
│ Largest Loss       │ -${X,XXX}     │
└────────────────────┴───────────────┘

📈 PERFORMANCE BY VRP TIER
┌──────────────┬────────┬──────────┬───────────┐
│ VRP Tier     │ Trades │ Win Rate │ Avg P&L   │
├──────────────┼────────┼──────────┼───────────┤
│ EXCELLENT ⭐ │ {N}    │ {X}%     │ ${XXX}    │
│ GOOD ✓       │ {N}    │ {X}%     │ ${XXX}    │
│ MARGINAL ○   │ {N}    │ {X}%     │ ${XXX}    │
│ SKIP ✗       │ {N}    │ {X}%     │ ${XXX}    │
└──────────────┴────────┴──────────┴───────────┘

💧 PERFORMANCE BY LIQUIDITY
┌──────────────┬────────┬──────────┬───────────┐
│ Liquidity    │ Trades │ Win Rate │ Avg P&L   │
├──────────────┼────────┼──────────┼───────────┤
│ EXCELLENT    │ {N}    │ {X}%     │ ${XXX}    │
│ WARNING ⚠️   │ {N}    │ {X}%     │ ${XXX}    │
│ REJECT 🚫    │ {N}    │ {X}%     │ ${XXX}    │
└──────────────┴────────┴──────────┴───────────┘

📋 PERFORMANCE BY STRATEGY
┌──────────────┬────────┬──────────┬───────────┐
│ Strategy     │ Trades │ Win Rate │ Avg P&L   │
├──────────────┼────────┼──────────┼───────────┤
│ Iron Condor  │ {N}    │ {X}%     │ ${XXX}    │
│ Put Spread   │ {N}    │ {X}%     │ ${XXX}    │
│ Call Spread  │ {N}    │ {X}%     │ ${XXX}    │
│ Naked Put    │ {N}    │ {X}%     │ ${XXX}    │
│ Strangle     │ {N}    │ {X}%     │ ${XXX}    │
└──────────────┴────────┴──────────┴───────────┘

🏆 TOP PERFORMERS (Best Avg P&L)
1. {TICKER} - {N} trades, {X}% win rate, ${XXX} avg
2. {TICKER} - {N} trades, {X}% win rate, ${XXX} avg
3. {TICKER} - {N} trades, {X}% win rate, ${XXX} avg

⚠️ UNDERPERFORMERS (Worst Avg P&L)
1. {TICKER} - {N} trades, {X}% win rate, -${XXX} avg
2. {TICKER} - {N} trades, {X}% win rate, -${XXX} avg
3. {TICKER} - {N} trades, {X}% win rate, -${XXX} avg

🧠 AI ANALYSIS & INSIGHTS

**Edge Validation:**
{Analysis of VRP correlation with results}

**Liquidity Impact:**
{Analysis of liquidity tier performance}
[If REJECT trades exist: 🚫 WARNING: {N} trades in REJECT liquidity
 These should have been skipped. Total loss: -${XXX}]

**Strategy Effectiveness:**
{Which strategies work best for your trading style}

**Key Lessons from Losses:**
• {Lesson 1}
• {Lesson 2}
• {Lesson 3}

**Actionable Recommendations:**
1. {Specific improvement}
2. {Specific improvement}
3. {Specific improvement}

══════════════════════════════════════════════════════
```

## No Data Output

```
══════════════════════════════════════════════════════
BACKTEST REPORT
══════════════════════════════════════════════════════

❌ NO TRADE DATA FOUND

No trade journal data available for analysis.

To populate trade history:
1. Run `/journal` to parse Fidelity statements
2. Or manually add trades to journal

Once you have trade data, run `/backtest` again.

══════════════════════════════════════════════════════
```

## Cost Control
- No Perplexity calls (uses Claude's built-in analysis)
- Local data query only
- AI insights generated in-context
