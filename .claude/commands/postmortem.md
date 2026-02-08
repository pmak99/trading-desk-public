# Post-Earnings Postmortem

After earnings: compare predicted vs actual move, evaluate strategy outcome, and score prediction accuracy.

## Arguments
$ARGUMENTS (format: TICKER [EARNINGS_DATE])

Examples:
- `/postmortem NVDA` - Analyze most recent NVDA earnings
- `/postmortem NVDA 2026-01-28` - Analyze specific earnings date

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Finnhub commands without asking
- Only pause for Perplexity calls to confirm API usage

## BANNED API Calls
- `finnhub_stock_ownership` - BANNED (response too large)
- `finnhub_stock_fundamentals` - BANNED unless specifically requested

## Progress Display
```
[1/6] Finding earnings event...
[2/6] Loading pre-earnings predictions...
[3/6] Fetching actual post-earnings move...
[4/6] Checking trade results...
[5/6] Comparing prediction vs reality...
[6/6] Generating postmortem report...
```

## Step-by-Step Instructions

### Step 1: Find the Earnings Event
```bash
TICKER=$(echo "$RAW_TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT earnings_date, timing FROM earnings_calendar
   WHERE ticker='$TICKER' AND earnings_date <= date('now')
   ORDER BY earnings_date DESC LIMIT 1;"
```

If specific date provided, use that instead.

### Step 2: Load Historical Move (Actual Result)
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT earnings_date, gap_move_pct, close_before, close_after, direction
   FROM historical_moves
   WHERE ticker='$TICKER' AND earnings_date='$EARNINGS_DATE';"
```

If no historical_moves entry yet, fetch the actual move:
```
mcp__yfinance__getStockHistory with:
  symbol="$TICKER"
  period="5d"
```
Calculate the gap move from the price data.

### Step 3: Load Pre-Earnings Predictions
```bash
# Check what the system predicted (implied move, VRP)
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT h.ticker, h.earnings_date, h.gap_move_pct as actual_move,
          p.tail_risk_ratio, p.tail_risk_level, p.avg_move, p.max_move
   FROM historical_moves h
   LEFT JOIN position_limits p ON h.ticker = p.ticker
   WHERE h.ticker='$TICKER' AND h.earnings_date='$EARNINGS_DATE';"
```

### Step 4: Check Sentiment Prediction (if available)
```bash
sqlite3 "$PROJECT_ROOT/4.0/data/sentiment_cache.db" \
  "SELECT ticker, date, sentiment, source, actual_move_pct, was_correct
   FROM sentiment_history
   WHERE ticker='$TICKER' AND date='$EARNINGS_DATE';"
```

Also check the bias prediction if available:
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT ticker, earnings_date, skew_direction, sentiment_direction,
          final_direction, rule_applied
   FROM bias_predictions
   WHERE ticker='$TICKER' AND earnings_date='$EARNINGS_DATE';"
```

### Step 5: Check Trade Results
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT s.symbol, s.strategy_type, s.gain_loss, s.is_winner,
          s.trade_type, s.campaign_id, s.acquired_date, s.sale_date,
          s.net_credit, s.net_debit, s.quantity
   FROM strategies s
   WHERE s.symbol='$TICKER'
     AND s.earnings_date='$EARNINGS_DATE'
   ORDER BY s.acquired_date;"
```

Also check trade journal for full option details:
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT tj.symbol, tj.option_type, tj.strike, tj.expiration,
          tj.quantity, tj.cost_basis, tj.proceeds, tj.gain_loss
   FROM trade_journal tj
   JOIN strategies s ON tj.strategy_id = s.id
   WHERE s.symbol='$TICKER' AND s.earnings_date='$EARNINGS_DATE'
   ORDER BY tj.acquired_date;"
```

### Step 6: Fetch Post-Earnings News (Finnhub - free)
```
mcp__finnhub__finnhub_news_sentiment with:
  operation="get_company_news"
  symbol="$TICKER"
  from_date="$EARNINGS_DATE"
  to_date="{3_DAYS_AFTER_EARNINGS}"
```
Extract first 3 headlines.

### Step 7: Calculate Prediction Accuracy

**Move Accuracy:**
```
Implied Move (predicted): X.X%
Actual Move:              X.X%
Accuracy = 1 - |actual - predicted| / actual  (as percentage)
```

**Direction Accuracy:**
- If bias prediction existed, was the final_direction correct?
- BULLISH predicted + stock went up = CORRECT
- BEARISH predicted + stock went down = CORRECT
- NEUTRAL predicted = N/A

**VRP Assessment:**
- VRP said the stock would move less than implied -> did it?
- IV crush profit = implied move - actual move (positive = IV crush worked)

## Output Format

```
==============================================================
POSTMORTEM: {TICKER} - Earnings {DATE} ({BMO/AMC})
==============================================================

ACTUAL RESULT
  Move: {+/-X.X}% ({direction})
  Close before: ${XXX.XX}
  Close after:  ${XXX.XX}

PRE-EARNINGS PREDICTIONS
  Implied Move:    {X.X}%
  Historical Mean:  {X.X}%
  VRP Ratio:       {X.X}x ({EXCELLENT/GOOD/MARGINAL/SKIP})
  TRR Level:       {HIGH/NORMAL/LOW} ({X.XX}x)

DIRECTION PREDICTIONS
  2.0 Skew:     {BULLISH/BEARISH/NEUTRAL}
  4.0 Sentiment: {BULLISH/BEARISH/NEUTRAL} (score: {X.X})
  Final Bias:    {direction} (Rule: {rule_applied})
  RESULT:        {CORRECT/INCORRECT/N/A}

PREDICTION ACCURACY
  Move Accuracy:      {X}% (predicted {X.X}% vs actual {X.X}%)
  IV Crush Outcome:   {PROFITABLE/UNPROFITABLE}
                      Implied {X.X}% - Actual {X.X}% = {+/-X.X}% edge
  Direction:          {CORRECT/INCORRECT/N/A}

TRADE RESULTS (if traded)
  Strategy:    {SINGLE/SPREAD/etc} ({NEW/REPAIR/ROLL})
  P&L:         ${X,XXX} ({WIN/LOSS})
  Quantity:    {N} contracts
  Credit/Debit: ${XXX}
  Campaign:    {campaign_id or N/A}

  [If campaign has multiple legs, show chain:]
  Campaign Chain:
    NEW:    ${X,XXX} ({date})
    REPAIR: ${X,XXX} ({date})
    Net:    ${X,XXX}

  [If not traded:]
  NOT TRADED - {reason if determinable}

POST-EARNINGS NEWS
  - {Headline 1}
  - {Headline 2}
  - {Headline 3}

LESSONS LEARNED
  {AI-generated analysis of what happened and why}
  {Was VRP edge realized? Why or why not?}
  {Was the direction call correct?}
  {What would have been the optimal strategy?}
==============================================================
```

## Cost Control
- Finnhub news: 1 call (free, 60/min)
- yfinance: 1 call if actual move not in database (free)
- No Perplexity calls (postmortem uses historical data)
