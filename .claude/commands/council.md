# Council — Multi-Source Sentiment Consensus

7-source AI sentiment council for pre-earnings consensus analysis. Combines free data (Finnhub, DB, WebSearch) with paid AI (Perplexity) into a weighted consensus score that feeds into `/analyze` and `/whisper`.

## Arguments
$ARGUMENTS (format: TICKER)

Examples:
- `/council PANW` - Run 7-source council for PANW
- `/council NVDA` - Run 7-source council for NVDA

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read, MCP commands without asking
- Only pause for Perplexity calls to confirm API usage

## BANNED API Calls
**NEVER call these — they return massive responses (12k+ tokens):**
- `finnhub_stock_ownership` — BANNED
- `finnhub_stock_fundamentals` — BANNED

## Council Members (7 Sources)

| # | Member | Weight | Source | Cost |
|---|--------|--------|--------|------|
| 1 | Perplexity Research | 25% | `perplexity_research` (deep) | ~$0.008 |
| 2 | Finnhub Analysts | 20% | `get_recommendations` | Free |
| 3 | Web Search | 15% | `WebSearch` tool | Free |
| 4 | Perplexity Quick | 10% | `perplexity_ask` (cache first) | ~$0.001 |
| 5 | Finnhub News | 10% | `get_company_news` (7-day) | Free |
| 6 | Options Skew | 10% | `bias_predictions` DB | Free |
| 7 | Historical Pattern | 10% | `historical_moves` + `position_limits` DB | Free |

Total: 2 Perplexity calls max per invocation.

## Progress Display
```
[1/8] Detecting earnings and current price...
[2/8] Running free council members (5 sources)...
[3/8] Running Perplexity Research (budget gate)...
[4/8] Calculating weighted consensus...
[5/8] Determining direction and modifiers...
[6/8] Caching council result...
[7/8] Calculating agreement metrics...
[8/8] Displaying council report...
```

## Reference Tables

### Sentiment Tiers
| Consensus Score | Tier | Modifier |
|----------------|------|----------|
| >= +0.6 | Strong Bullish | +5% |
| >= +0.2 | Bullish | +3% |
| -0.2 to +0.2 | Neutral | 0% |
| <= -0.2 | Bearish | -7% |
| <= -0.6 | Strong Bearish | -12% |

### 3-Rule Direction System
| Original Skew | Council | Result | Rule |
|---------------|---------|--------|------|
| NEUTRAL | Bullish (>=+0.3) | BULLISH | Sentiment breaks tie |
| NEUTRAL | Bearish (<=-0.3) | BEARISH | Sentiment breaks tie |
| BULLISH | Bearish (<=-0.3) | NEUTRAL | Conflict -> hedge |
| BEARISH | Bullish (>=+0.3) | NEUTRAL | Conflict -> hedge |
| Any | Aligned/Neutral | Keep original | Skew dominates |

## Step-by-Step Instructions

### Step 1: Sanitize Ticker + Detect Earnings Date [1/8]

**Sanitize ticker (CRITICAL for SQL safety):**
```bash
TICKER=$(echo "$RAW_TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')
```

**Look up next earnings date from database:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT earnings_date, timing, CAST(julianday(earnings_date) - julianday('now') AS INTEGER) as days_until
   FROM earnings_calendar WHERE ticker='$TICKER' AND earnings_date >= date('now')
   ORDER BY earnings_date ASC LIMIT 1;"
```

**If no upcoming earnings in database, try Finnhub as fallback:**
```
mcp__finnhub__finnhub_calendar_data with:
  operation="get_earnings_calendar"
  symbol="{TICKER}"
  from_date="{TODAY}"
  to_date="{90_DAYS_FROM_NOW}"
```

**If still no earnings found:**
```
No upcoming earnings found for {TICKER}
   Neither database nor Finnhub have upcoming earnings.
   Cannot run council without an earnings date.
```

**Get current price:**
```
mcp__finnhub__finnhub_stock_market_data with:
  operation="get_quote"
  symbol="{TICKER}"
```

**Get VRP and TRR context (for report header):**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT tail_risk_ratio, tail_risk_level FROM position_limits WHERE ticker='$TICKER';"
```

### Step 2: Run Free Council Members (5 sources in parallel) [2/8]

Run these 5 queries concurrently — they are all free and independent:

**2a. Perplexity Quick (10% weight) — check cache first:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "SELECT sentiment, source, cached_at FROM sentiment_cache
   WHERE ticker='$TICKER' AND date='$EARNINGS_DATE'
   AND cached_at > datetime('now', '-3 hours')
   ORDER BY CASE source WHEN 'council' THEN 0 WHEN 'perplexity' THEN 1 ELSE 2 END LIMIT 1;"
```
- If cached: use cached score, mark status as "cached"
- If cache miss: call Perplexity Quick (see prompt below), mark status as "fresh"
- If Perplexity fails: mark member as failed

**Perplexity Quick prompt (if cache miss):**
```
mcp__perplexity__perplexity_ask with query="For {TICKER} earnings on {EARNINGS_DATE}, respond ONLY in this format:
Direction: [bullish/bearish/neutral]
Score: [number -1 to +1]
Catalysts: [3 bullets, max 10 words each]
Risks: [2 bullets, max 10 words each]"
```

**2b. Finnhub Analysts (20% weight):**
```
mcp__finnhub__finnhub_stock_estimates with:
  operation="get_recommendations"
  symbol="{TICKER}"
```

**Normalize score:**
```
raw = (strongBuy×2 + buy×1 - sell×1 - strongSell×2) / total_analysts
score = clamp(raw / 2.0, -1.0, +1.0)
```
Map to direction: score >= +0.3 = BULLISH, <= -0.3 = BEARISH, else NEUTRAL.
Status: "{N} analysts"

**2c. Web Search (15% weight):**
```
WebSearch with query="{TICKER} earnings sentiment analyst expectations {EARNINGS_DATE}"
```

From results, determine:
- Overall sentiment direction (bullish/bearish/neutral)
- Score between -1.0 and +1.0 based on sentiment strength
- Status: "fresh"

**2d. Finnhub News (10% weight):**
```
mcp__finnhub__finnhub_news_sentiment with:
  operation="get_company_news"
  symbol="{TICKER}"
  from_date="{7_DAYS_AGO}"
  to_date="{TODAY}"
  max_results=10
```

**IMPORTANT:** Use a 7-day window. If response is truncated/too large, reduce to 3-day window.

From news articles, determine:
- Overall tone of headlines (bullish/bearish/neutral)
- Score between -1.0 and +1.0
- Status: "{N} articles"

**2e. Options Skew (10% weight):**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT directional_bias, bias_confidence FROM bias_predictions
   WHERE ticker='$TICKER' AND earnings_date='$EARNINGS_DATE'
   ORDER BY predicted_at DESC LIMIT 1;"
```

If no match for exact date, try most recent entry:
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT directional_bias, bias_confidence, earnings_date FROM bias_predictions
   WHERE ticker='$TICKER'
   ORDER BY earnings_date DESC LIMIT 1;"
```

**Skew score mapping:**
```
STRONG_BULLISH -> +0.7, BULLISH -> +0.5, WEAK_BULLISH -> +0.3
NEUTRAL -> 0.0
WEAK_BEARISH -> -0.3, BEARISH -> -0.5, STRONG_BEARISH -> -0.7
```
Status: "bias_predictions"

If no bias_predictions exist for ticker: mark member as failed, redistribute weight.

**2f. Historical Pattern (10% weight):**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT earnings_date, gap_move_pct FROM historical_moves
   WHERE ticker='$TICKER'
   ORDER BY earnings_date DESC LIMIT 12;"
```

**Calculate score:**
```
up_count = count where gap_move_pct > 0
total = count of all records
up_ratio = up_count / total
overall_score = (up_ratio - 0.5) * 2.0

recent_4 = last 4 quarters
recent_up_count = count where gap_move_pct > 0 in recent_4
recent_score = (recent_up_count/4 - 0.5) * 2.0

score = 0.6 * overall_score + 0.4 * recent_score
```
Map to direction: score >= +0.3 = BULLISH, <= -0.3 = BEARISH, else NEUTRAL.
Status: "{N} quarters"

Also get TRR from position_limits:
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT tail_risk_ratio, tail_risk_level FROM position_limits WHERE ticker='$TICKER';"
```

If no historical_moves exist for ticker: mark member as failed, redistribute weight.

### Step 3: Check Budget + Run Perplexity Research (25% weight) [3/8]

**Budget gate:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "SELECT COALESCE(calls, 0) as calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
```

**If calls < 60:** Run Perplexity Research:
```
mcp__perplexity__perplexity_research with query="For {TICKER} earnings on {EARNINGS_DATE}, analyze:
1. Analyst consensus (Buy/Hold/Sell) and recent changes (30 days)
2. EPS/revenue estimates vs whisper numbers
3. Key business metric to watch
4. Bull case (2 bullets) and bear case (2 bullets)
5. Key risk (1 bullet)

Respond ONLY in this format:
Direction: [bullish/bearish/neutral]
Score: [number -1.0 to +1.0]
Bull Case: [2 bullets, max 15 words each]
Bear Case: [2 bullets, max 15 words each]
Key Risk: [1 bullet, max 20 words]
Analyst Trend: [upgrading/stable/downgrading]"
```

Parse response for direction, score, and details. Status: "fresh"

**If calls >= 60:** Skip Perplexity Research, mark member as failed, redistribute 25% weight to others.

### Step 4: Calculate Weighted Consensus [4/8]

```
For each active member:
  weighted_sum += member_score * member_weight

consensus_score = weighted_sum / sum_of_active_weights
```

**Member failure handling:**
- If a member failed: exclude from both numerator and denominator
- Redistribute: `effective_weight = original_weight / sum_of_active_weights`
- **Minimum 3 active members required.** If fewer than 3 succeed, abort:
  ```
  INSUFFICIENT DATA — council inconclusive
     Only {N}/7 members returned data. Minimum 3 required.
     Try /analyze {TICKER} for single-source analysis.
  ```

### Step 5: Determine Direction and Modifiers [5/8]

**Map consensus score to sentiment tier:**
```
>= +0.6: Strong Bullish -> modifier = +0.05
>= +0.2: Bullish        -> modifier = +0.03
> -0.2 and < +0.2: Neutral -> modifier = 0.00
<= -0.2: Bearish        -> modifier = -0.07
<= -0.6: Strong Bearish -> modifier = -0.12
```

**Apply 3-rule direction system:**
Use the Options Skew member's direction as the "2.0 skew" and the consensus direction as "sentiment":
- Rule 1: Neutral skew + council signal (>=+0.3 or <=-0.3) -> use council direction
- Rule 2: Skew conflicts with council -> go NEUTRAL (hedge)
- Rule 3: Otherwise -> keep original skew

**Calculate 4.0 Score:**
To get the 2.0 Score, run a quick analysis or estimate from VRP/liquidity context. If unavailable, note "2.0 Score: run /analyze for full scoring".
```
4.0 Score = 2.0 Score * (1 + modifier)
```

### Step 6: Cache Council Result [6/8]

**Cache in sentiment_cache (3hr TTL):**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "INSERT OR REPLACE INTO sentiment_cache (ticker, date, sentiment, source, cached_at)
   VALUES ('$TICKER', '$EARNINGS_DATE',
           '{\"direction\": \"$DIRECTION\", \"score\": $SCORE, \"modifier\": $MODIFIER, \"agreement\": \"$AGREEMENT\", \"members\": {$MEMBER_JSON}}',
           'council', datetime('now'));"
```

**Save to sentiment_history (permanent):**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "INSERT OR REPLACE INTO sentiment_history (ticker, earnings_date, collected_at, source, sentiment_text, sentiment_score, sentiment_direction)
   VALUES ('$TICKER', '$EARNINGS_DATE', datetime('now'), 'council',
           '{\"members_active\": $ACTIVE_COUNT, \"agreement\": \"$AGREEMENT\", \"modifier\": $MODIFIER}',
           $SCORE, '$DIRECTION');"
```

**Update api_budget if Perplexity was called:**
```bash
# Add 1 for each Perplexity call made (max 2: research + quick)
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "INSERT INTO api_budget (date, calls) VALUES ('$(date +%Y-%m-%d)', $PERPLEXITY_CALLS)
   ON CONFLICT(date) DO UPDATE SET calls = calls + $PERPLEXITY_CALLS;"
```

### Step 7: Calculate Agreement Metrics [7/8]

Count how many members agree with the final consensus direction:
```
agreement_count = number of members whose direction matches consensus direction
agreement_pct = agreement_count / active_member_count

HIGH:   >= 71% (5/7 or better)
MEDIUM: >= 57% (4/7)
LOW:    < 57%  (3/7 or fewer)
```

### Step 8: Display Council Report [8/8]

## Output Format

```
==============================================================
COUNCIL: {TICKER} — Earnings {DATE} ({BMO/AMC})
Current: ${PRICE} ({CHANGE}%) | VRP: {X.X}x | TRR: {X.XX}x
==============================================================

COUNCIL MEMBERS             Direction  Score   Weight  Status
-------------------------------------------------------------
Perplexity Research         BULLISH    +0.60   25%     fresh
Finnhub Analysts            BULLISH    +0.55   20%     33 analysts
Web Search                  NEUTRAL    +0.10   15%     fresh
Perplexity Quick            NEUTRAL    +0.15   10%     cached
Finnhub News                BULLISH    +0.30   10%     10 articles
Options Skew                STR BULL   +0.70   10%     bias_predictions
Historical Pattern          BULLISH    +0.34   10%     12 quarters
-------------------------------------------------------------
WEIGHTED CONSENSUS          BULLISH    +0.42   100%    HIGH (6/7)

4.0 SCORING
   Consensus:    +0.42 -> Bullish (+7% modifier)
   2.0 Score:    62.5
   4.0 Score:    66.9 (62.5 x 1.07)
   Tradeable:    YES (>= 55)

DIRECTION (3-Rule System)
   Skew (2.0):   {BIAS} | Council: {DIRECTION}
   Rule:         {rule_name}
   Final:        {DIRECTION} {* if changed}

MEMBER DETAILS
   Perplexity Research:
     Bull: {bullet 1} | {bullet 2}
     Bear: {bullet 1} | {bullet 2}
     Risk: {key risk}
     Analyst Trend: {upgrading/stable/downgrading}

   Finnhub Analysts:
     Buy: {N} | Hold: {N} | Sell: {N}
     Strong Buy: {N} | Strong Sell: {N}

   Web Search:
     {key finding 1}
     {key finding 2}

   Perplexity Quick:
     Catalysts: {3 bullets}
     Risks: {2 bullets}

   Finnhub News:
     {headline 1} ({date})
     {headline 2} ({date})
     {headline 3} ({date})

   Options Skew:
     Bias: {BIAS} | Confidence: {X}%

   Historical Pattern:
     Last 12Q: {up_count}/{total} up ({pct}%)
     Last 4Q: {recent_up}/{4} up ({pct}%)
     TRR: {X.XX}x ({LEVEL})

RISK FLAGS
   {TRR HIGH warning if applicable}
   {Low agreement warning if applicable}
   {Contrarian signal if skew != consensus}
   {Budget warning if Perplexity skipped}

CACHE STATUS
   Council cached as source="council" (3hr TTL)
   Saved to sentiment_history (permanent)
   Budget: {N}/60 calls today

NEXT STEPS
   Run /analyze {TICKER} for strategy recommendations
==============================================================
```

## Error Handling

- Individual member failure: exclude from weighted sum, redistribute weight
- Perplexity budget exceeded: skip both Perplexity members, proceed with 5 free sources
- Finnhub 403 (premium endpoint): mark member failed, proceed
- <3 active members: abort with "INSUFFICIENT DATA — council inconclusive"
- No bias_predictions for ticker: try most recent entry, else exclude skew member
- No historical_moves for ticker: exclude historical member
- Finnhub news too large: reduce window from 7 to 3 days

## Cost Control
- Maximum 2 Perplexity calls per invocation (research + quick)
- 5 of 7 sources are completely free
- Perplexity Quick checks cache first (3hr TTL)
- Budget gate: skip Perplexity if daily calls >= 60
- Council result is cached and reused by /analyze, /whisper, /alert, /scan

## Typical Workflow
```
/council PANW           -> Full 7-source consensus
/analyze PANW           -> Uses council cache for sentiment
/whisper                -> Council-cached tickers scored instantly
```
