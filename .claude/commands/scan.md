# Scan Earnings by Date

Scan all tickers with earnings on a specific date with VRP analysis.

## Arguments
$ARGUMENTS (format: DATE - required, YYYY-MM-DD)

Examples:
- `/scan 2026-02-10` - Scan all earnings on February 10th

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
```
[1/5] Validating date...
[2/5] Running 2.0 scan for date...
[3/5] Filtering high-VRP tickers...
[4/5] Checking tail risk ratios...
[5/5] Fetching sentiment for top 3...
```

## TRR Reference

| Level | TRR | Max Contracts | Action |
|-------|-----|---------------|--------|
| HIGH | > 2.5x | 50 | Warning badge |
| NORMAL | 1.5-2.5x | 100 | No badge |
| LOW | < 1.5x | 100 | No badge |

## Step-by-Step Instructions

### Step 1: Parse Date Argument
- Date is REQUIRED in YYYY-MM-DD format
- If not provided:
  ```
  Date required. Usage: /scan YYYY-MM-DD
     Example: /scan 2026-02-10
  ```

### Step 2: Check Market Status (informational)
```bash
DAY_OF_WEEK=$(date '+%A')
```
Display market status as informational note (open/closed/weekend).

### Step 3: Run 2.0 Scan for Date
```bash
cd "$PROJECT_ROOT/2.0" && ./trade.sh scan $DATE
```

This analyzes every ticker with earnings on that date. Extract for each ticker:
- Ticker symbol and timing (BMO/AMC)
- VRP ratio and rating
- Implied move percentage
- 2.0 Score
- Liquidity tier

### Step 4: Identify TOP 5 VRP >= 1.8x Tickers
From scan results, identify the top 5 tickers where VRP >= 1.8x (EXCELLENT tier).

Query TRR for all qualified tickers:
```bash
TICKERS="'NVDA','AMD','MU'"  # Use actual tickers from scan

sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT ticker, tail_risk_ratio, tail_risk_level, max_contracts
   FROM position_limits WHERE ticker IN ($TICKERS) AND tail_risk_level = 'HIGH';"
```

### Step 5: Add Sentiment for TOP 3 (Conditional)

For EACH of the top 3 non-REJECT tickers:

**5a. Check sentiment cache:**
```bash
TICKER=$(echo "$RAW_TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 "$PROJECT_ROOT/4.0/data/sentiment_cache.db" \
  "SELECT sentiment, source, cached_at FROM sentiment_cache
   WHERE ticker='$TICKER' AND date='$DATE'
   AND cached_at > datetime('now', '-3 hours')
   ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
```

**5b. If cache miss, use fallback chain:**
1. Check budget (40/day limit)
2. Try Perplexity (max 3 calls total)
3. Fall back to `mcp__perplexity__perplexity_search`
4. Graceful skip if all fail

Calculate 4.0 Score for each:
```
Modifier: Strong Bullish +0.12, Bullish +0.07, Neutral 0.00, Bearish -0.07, Strong Bearish -0.12
4.0 Score = 2.0 Score * (1 + modifier)
```

## Output Format

```
==============================================================
EARNINGS SCAN: {DATE}
==============================================================

Market: [OPEN/CLOSED/WEEKEND]

ALL EARNINGS FOR {DATE}

 Rank  Ticker  VRP       Liq      Score  TRR
  1    NVDA    8.2x      EXCEL    92
  2    AMD     6.1x      EXCEL    85
  3    AVGO    5.4x      WARN     72     HIGH
  4    MU      4.2x      EXCEL    68     HIGH
  5    ORCL    3.1x      EXCEL    55
  6    CRM     2.8x      WARN     48
  7    WDAY    2.1x      REJCT    32
  ...

SUMMARY
   Total earnings: {N}
   VRP >= 1.8x: {M} tickers
   Liquidity REJECT: {R} tickers

TOP 3 OPPORTUNITIES

1. NVDA - Earnings {BMO/AMC}
   VRP: 8.2x (EXCELLENT) | Implied: 8.5% | 2.0 Score: 92
   Liquidity: EXCELLENT | TRR: LOW
   Sentiment: {summary} | 4.0 Score: {X.X}

2. AMD - Earnings {BMO/AMC}
   VRP: 6.1x (EXCELLENT) | Implied: 6.2% | 2.0 Score: 85
   Liquidity: EXCELLENT | TRR: NORMAL
   Sentiment: {summary} | 4.0 Score: {X.X}

3. AVGO - Earnings {BMO/AMC}
   VRP: 5.4x (EXCELLENT) | Implied: 5.8% | 2.0 Score: 72
   Liquidity: WARNING | TRR: HIGH -> Max 50 contracts
   Sentiment: {summary} | 4.0 Score: {X.X}

HIGH TAIL RISK (if any):
   {TICKER}: TRR {X.XX}x -> Max 50 contracts / $25k notional

NEXT STEPS
   Run /analyze NVDA for full strategy recommendations
==============================================================
```

## Cost Control
- Maximum 3 Perplexity calls (top 3 only)
- Only for VRP >= 1.8x AND Liquidity != REJECT
- Cache-aware to avoid duplicate calls
- If already primed with /prime, all sentiment from cache
