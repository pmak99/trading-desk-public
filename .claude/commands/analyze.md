# Analyze Ticker for IV Crush

Deep dive on a single ticker with full strategy generation - YOUR GO-TO FOR TRADING DECISIONS.

## Arguments
$ARGUMENTS (format: TICKER [EARNINGS_DATE])

Examples:
- `/analyze NVDA` - Analyze NVDA with auto-detected earnings date
- `/analyze NVDA 2025-12-19` - Analyze NVDA for specific earnings date

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read, Finnhub commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
Show progress updates as you work:
```
[1/6] Checking existing positions...
[2/6] Running 2.0 core analysis...
[3/6] Fetching news data (Finnhub)...
[4/6] Loading/fetching sentiment...
[5/6] Calculating 4.0 adjusted direction...
[6/6] Generating final report...
```

## Step-by-Step Instructions

### Step 1: Check Existing Positions (Alpaca MCP)
```
mcp__alpaca__alpaca_list_positions
```

Check if user already has exposure to this ticker:
- Look for any position where the symbol starts with the ticker (e.g., "NVDA" matches "NVDA", "NVDA250117C00140000")
- If found, display warning:
  ```
  ⚠️ EXISTING POSITION: You have {qty} {symbol} contracts
     Current P&L: ${unrealized_pl}
     Consider risk of over-concentration before adding more exposure.
  ```

### Step 2: Run 2.0 Core Analysis
Execute the proven 2.0 analysis script:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh $ARGUMENTS
```

This provides:
- VRP ratio and tier (EXCELLENT ≥7x, GOOD ≥4x, MARGINAL ≥1.5x, SKIP <1.5x)
- Implied move vs historical mean
- Liquidity tier (EXCELLENT/WARNING/REJECT)
- Strategy recommendations with Greeks
- Position sizing (Half-Kelly)

**CRITICAL:** If Liquidity = REJECT, display prominent warning:
```
🚫 LIQUIDITY REJECT - DO NOT TRADE
   Low open interest or wide spreads make this untradeable.
   (Lesson from $26,930 loss on WDAY/ZS/SYM)
```

### Step 3: Gather Free News Data (Finnhub MCP)
Always fetch this regardless of VRP - it's free:

**Recent News:**
```
mcp__finnhub__finnhub_news_sentiment with operation="company_news" and symbol="{TICKER}"
```

**Earnings Surprises:**
```
mcp__finnhub__finnhub_stock_fundamentals with operation="earnings_surprises" and symbol="{TICKER}"
```

**Insider Trades:**
```
mcp__finnhub__finnhub_stock_ownership with operation="insider_transactions" and symbol="{TICKER}"
```

Display a summary of key findings from each.

### Step 4: AI Sentiment (Conditional - Only if VRP ≥ 3x AND Liquidity ≠ REJECT)

**Skip sentiment if:**
- VRP < 3x (insufficient edge for discovery)
- Liquidity = REJECT (not tradeable anyway)

**If qualified, use fallback chain:**

1. **Check sentiment cache first:**
   ```bash
   sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
     "SELECT sentiment, source, cached_at FROM sentiment_cache WHERE ticker='$TICKER' AND date='$(date +%Y-%m-%d)' ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
   ```
   If found and < 3 hours old → use cached sentiment, note "(cached)"

2. **If cache miss, check budget:**
   ```bash
   sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
     "SELECT calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
   ```
   If calls ≥ 150 → skip to WebSearch fallback

3. **Try Perplexity (if budget OK):**
   ```
   mcp__perplexity__perplexity_ask with query="For {TICKER} earnings, respond ONLY in this format:
   Direction: [bullish/bearish/neutral]
   Score: [number -1 to +1]
   Catalysts: [3 bullets, max 10 words each]
   Risks: [2 bullets, max 10 words each]"
   ```
   - Cache result: `INSERT INTO sentiment_cache (ticker, date, source, sentiment, cached_at) VALUES ('{TICKER}', '{DATE}', 'perplexity', '{RESULT}', '{NOW}');`
   - Record API call: `INSERT OR REPLACE INTO api_budget (date, calls, cost, last_updated) VALUES ('{DATE}', COALESCE((SELECT calls FROM api_budget WHERE date='{DATE}'), 0) + 1, COALESCE((SELECT cost FROM api_budget WHERE date='{DATE}'), 0) + 0.006, '{NOW}');`

4. **If Perplexity fails, try WebSearch:**
   ```
   WebSearch with query="{TICKER} earnings sentiment analyst rating {DATE}"
   ```
   - Summarize results into the same structured format above
   - Cache with source="websearch"

5. **If all fail, show graceful message:**
   ```
   ℹ️ AI sentiment unavailable. Displaying raw news from Finnhub above.
   ```

### Step 5: Sentiment-Adjusted Direction (4.0 Enhancement)

If sentiment was gathered, adjust the directional bias from 2.0's skew analysis:

**Run the adjustment:**
```python
# From the 2.0 output, extract skew bias (e.g., "NEUTRAL", "BULLISH", "STRONG_BULLISH")
# From sentiment, extract score (-1 to +1)

import sys
sys.path.insert(0, '/Users/prashant/PycharmProjects/Trading Desk/4.0/src')
from sentiment_direction import adjust_direction, format_adjustment

# Example:
adj = adjust_direction(skew_bias="NEUTRAL", sentiment_score=0.4)
print(format_adjustment(adj))
```

**Simple 3-Rule System:**
| Original Skew | Sentiment | Result | Rule |
|---------------|-----------|--------|------|
| NEUTRAL | Bullish (≥+0.2) | → BULLISH | Sentiment breaks tie |
| NEUTRAL | Bearish (≤-0.2) | → BEARISH | Sentiment breaks tie |
| BULLISH | Bearish (≤-0.2) | → NEUTRAL | Conflict → hedge |
| BEARISH | Bullish (≥+0.2) | → NEUTRAL | Conflict → hedge |
| Any | Aligned/Neutral | → Keep original | Skew dominates |

**Display in output:**
```
🎯 DIRECTION (4.0 Adjusted)
   2.0 Skew: {original} → 4.0: {adjusted}
   Rule: {tiebreak_bullish|conflict_hedge|skew_dominates}
   Confidence: {X%}
```

**Strategy Impact:**
- BULLISH → Favor bull put spreads over straddles
- BEARISH → Favor bear call spreads over straddles
- NEUTRAL → Straddles, iron condors (hedged)
- Conflict (CHANGED to NEUTRAL) → Strongly prefer hedged strategies

### Step 6: Store in Memory MCP (Optional)
For high-conviction trades (VRP ≥ 7x), store analysis:
```
mcp__memory__create_entities with entities=[{
  "name": "{TICKER}-{DATE}",
  "entityType": "analysis",
  "observations": [
    "VRP: {ratio}x ({tier})",
    "Implied Move: {pct}%",
    "Liquidity: {tier}",
    "Sentiment: {summary}"
  ]
}]
```

## Output Format

```
══════════════════════════════════════════════════════
ANALYSIS: {TICKER}
══════════════════════════════════════════════════════

[If existing position: ⚠️ EXISTING POSITION warning]

📅 EARNINGS INFO
   Date: {date} ({BMO/AMC})
   Days until: {N}

📊 VRP ASSESSMENT
   Implied Move: {X.X}%
   Historical Mean: {X.X}%
   VRP Ratio: {X.X}x → {EXCELLENT/GOOD/MARGINAL/SKIP}
   [Scoring weights: 55% VRP, 25% Move, 20% Liquidity]

💧 LIQUIDITY
   Tier: {EXCELLENT/WARNING/REJECT}
   [Details: OI, spread width, volume]
   [If REJECT: 🚫 DO NOT TRADE]

📰 NEWS SUMMARY (Finnhub)
   • {Recent headline 1}
   • {Recent headline 2}
   • Earnings history: {beat/miss pattern}
   • Insider activity: {summary}

🧠 AI SENTIMENT {(cached/fresh/websearch)}
   Direction: {BULLISH/BEARISH/NEUTRAL} | Score: {-1 to +1}
   Catalysts: {bullet list, 3 max}
   Risks: {bullet list, 2 max}
   [Or: "ℹ️ Skipped - VRP < 3x" / "ℹ️ Unavailable"]

🎯 DIRECTION (4.0 Adjusted)
   2.0 Skew: {NEUTRAL/BULLISH/BEARISH} → 4.0: {ADJUSTED}
   Rule: {tiebreak|conflict_hedge|skew_dominates}
   [If CHANGED: "⚠️ Sentiment shifted direction - review strategy alignment"]

📈 STRATEGY RECOMMENDATIONS
   [2-3 ranked strategies from 2.0 with:]
   - Strategy type and strikes
   - Credit/debit and max profit/loss
   - POP (probability of profit)
   - Greeks (delta, theta, vega)
   - Position sizing (Half-Kelly)

⚠️ RISK NOTES
   • [Any concerns from sentiment]
   • [Liquidity warnings if WARNING tier]
   • [High implied move caution if > 15%]

══════════════════════════════════════════════════════
```

## Cost Control
- Finnhub calls: Always (free, 60/min limit)
- Perplexity: Only if VRP ≥ 3x AND Liquidity ≠ REJECT AND cache miss AND budget OK
- Maximum 1 Perplexity call per /analyze
