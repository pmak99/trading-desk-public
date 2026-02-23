# Weekly Earnings Calendar (Enhanced)

Display the full week's earnings calendar with deduplication, VRP context, inline trade history, and TRR flags.

## Arguments
$ARGUMENTS (optional: DATE in YYYY-MM-DD format)

Examples:
- `/calendar` - Current/next week (same logic as /whisper)
- `/calendar 2026-02-10` - Week containing that date

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3, Finnhub commands without asking
- This is a read-only dashboard - execute autonomously

## Progress Display
```
[1/8] Determining target week...
[2/8] Loading & deduplicating earnings...
[3/8] Checking sentiment cache readiness...
[4/8] Enriching with Finnhub data...
[5/8] Consolidated data query...
[6/8] Resolving company names...
[7/8] Computing day density...
[8/8] Rendering calendar...
```

## Step-by-Step Instructions

### Step 1: Determine Target Week
Same logic as /whisper:
- Monday-Thursday: current week
- Friday-Sunday: next week
- If date argument provided, use that date's week

```bash
DAY_NUM=$(date '+%u')
if [ $DAY_NUM -ge 5 ]; then
    DAYS_TO_NEXT_MONDAY=$((8 - DAY_NUM))
    TARGET_MONDAY=$(date -v+${DAYS_TO_NEXT_MONDAY}d '+%Y-%m-%d' 2>/dev/null || date -d "+${DAYS_TO_NEXT_MONDAY} days" '+%Y-%m-%d')
else
    DAYS_SINCE_MONDAY=$((DAY_NUM - 1))
    TARGET_MONDAY=$(date -v-${DAYS_SINCE_MONDAY}d '+%Y-%m-%d' 2>/dev/null || date -d "-${DAYS_SINCE_MONDAY} days" '+%Y-%m-%d')
fi
TARGET_FRIDAY=$(date -v+4d -j -f '%Y-%m-%d' "$TARGET_MONDAY" '+%Y-%m-%d' 2>/dev/null)
```

### Step 2: Load & Deduplicate Earnings

Many tickers appear on multiple days (different sources disagree). Deduplicate with a window function:

```sql
WITH ranked AS (
  SELECT ticker, earnings_date, timing, confirmed,
         ROW_NUMBER() OVER (
           PARTITION BY ticker
           ORDER BY
             confirmed DESC,
             CASE WHEN timing IN ('BMO','AMC') THEN 0 ELSE 1 END,
             earnings_date ASC
         ) as rn
  FROM earnings_calendar
  WHERE earnings_date BETWEEN '$MON' AND '$FRI'
)
SELECT ticker, earnings_date, timing, confirmed
FROM ranked WHERE rn = 1
ORDER BY earnings_date, timing, ticker;
```

**Priority rules:** confirmed > unconfirmed, known timing (BMO/AMC) > UNKNOWN, earlier date wins ties.

Also run the raw count to measure dedup impact:
```sql
SELECT COUNT(*) as raw, COUNT(DISTINCT ticker) as deduped
FROM earnings_calendar
WHERE earnings_date BETWEEN '$MON' AND '$FRI';
```

Save the removed duplicates (ticker, removed_date, kept_date) for the dedup notes section at the bottom.

### Step 3: Check Sentiment Cache Readiness

Query `sentiment_cache.db` for cached tickers and today's API budget:

```sql
-- Cache hits (fresh within 3 hours)
SELECT COUNT(DISTINCT ticker) FROM sentiment_cache
WHERE ticker IN ({deduped_ticker_list})
  AND cached_at > datetime('now', '-3 hours');
```

```sql
-- Today's API budget
SELECT COALESCE(SUM(call_count), 0) as calls_today
FROM api_budget
WHERE date = date('now');
```

Report: `Sentiment: X/{total} cached | Budget: Y/60 calls used today`

### Step 4: Finnhub Enrichment

Try the Finnhub MCP call for the target week:
```
mcp__finnhub__finnhub_calendar_data with:
  operation="get_earnings_calendar"
  from_date="$TARGET_MONDAY"
  to_date="$TARGET_FRIDAY"
```

**Overflow protocol:**
1. If the response is truncated, too large (>200KB), or errors out → skip gracefully with note: `"Finnhub: response too large, using DB data only"`
2. If successful:
   - Extract timing info (BMO/AMC) for tickers that are UNKNOWN in DB
   - Identify "notable" new tickers: those NOT in the deduped DB results but WITH history in our `historical_moves` table (ignore tickers we have no history for)
3. Never retry or parse partial JSON

**Timing enrichment:** For any deduped ticker with timing=UNKNOWN, if Finnhub provides BMO or AMC → update the display timing (not the DB).

### Step 5: Consolidated Data Query

Single query joining all needed data for the deduped ticker list:

```sql
SELECT
  d.ticker,
  d.earnings_date,
  d.timing,
  d.confirmed,
  COALESCE(p.tail_risk_ratio, 0) as trr,
  COALESCE(p.tail_risk_level, 'UNKNOWN') as trr_level,
  COALESCE(p.max_contracts, 100) as max_contracts,
  COALESCE(hm.quarters, 0) as hist_quarters,
  COALESCE(s.prev_trades, 0) as prev_trades,
  COALESCE(s.prev_pnl, 0) as prev_pnl,
  COALESCE(s.prev_win_rate, 0) as prev_win_rate
FROM (VALUES {deduped_tickers_as_values}) d(ticker, earnings_date, timing, confirmed)
LEFT JOIN position_limits p ON d.ticker = p.ticker
LEFT JOIN (
  SELECT ticker, COUNT(*) as quarters
  FROM historical_moves
  GROUP BY ticker
) hm ON d.ticker = hm.ticker
LEFT JOIN (
  SELECT symbol,
         COUNT(*) as prev_trades,
         ROUND(SUM(gain_loss), 0) as prev_pnl,
         ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as prev_win_rate
  FROM strategies
  GROUP BY symbol
) s ON d.ticker = s.symbol
ORDER BY d.earnings_date, d.timing, d.ticker;
```

If the VALUES approach is awkward in sqlite3 CLI, run as separate queries:
1. Position limits for all tickers: `SELECT ticker, tail_risk_ratio, tail_risk_level, max_contracts FROM position_limits WHERE ticker IN (...)`
2. Historical quarters: `SELECT ticker, COUNT(*) as quarters FROM historical_moves WHERE ticker IN (...) GROUP BY ticker`
3. Trade history: `SELECT symbol, COUNT(*) as trades, ROUND(SUM(gain_loss),0) as pnl, ROUND(100.0*SUM(is_winner)/COUNT(*),1) as win_rate FROM strategies WHERE symbol IN (...) GROUP BY symbol`

### Step 6: Company Name Resolution

Use this hardcoded dictionary for company names. Truncate to 15 characters for display.

```
COMPANY_NAMES = {
    "A": "Agilent",
    "AA": "Alcoa",
    "AAL": "American Air",
    "AAPL": "Apple",
    "ABBV": "AbbVie",
    "ABNB": "Airbnb",
    "ABT": "Abbott Labs",
    "ACN": "Accenture",
    "ADBE": "Adobe",
    "ADI": "Analog Devices",
    "ADP": "ADP",
    "ADSK": "Autodesk",
    "AMAT": "Applied Matls",
    "AMD": "AMD",
    "AMGN": "Amgen",
    "AMZN": "Amazon",
    "ANET": "Arista",
    "APLD": "Applied Digital",
    "APP": "AppLovin",
    "ARM": "Arm Holdings",
    "ASAN": "Asana",
    "ASML": "ASML",
    "AVGO": "Broadcom",
    "AXP": "Amex",
    "BA": "Boeing",
    "BABA": "Alibaba",
    "BAC": "Bank of America",
    "BBY": "Best Buy",
    "BIDU": "Baidu",
    "BIIB": "Biogen",
    "BK": "BNY Mellon",
    "BLK": "BlackRock",
    "BMY": "Bristol-Myers",
    "CAT": "Caterpillar",
    "CHWY": "Chewy",
    "CI": "Cigna",
    "CL": "Colgate",
    "CLF": "Cleveland-Clif",
    "CMG": "Chipotle",
    "COIN": "Coinbase",
    "COP": "ConocoPhillips",
    "COST": "Costco",
    "CPRT": "Copart",
    "CRDO": "Credo Tech",
    "CRM": "Salesforce",
    "CRWD": "CrowdStrike",
    "CSCO": "Cisco",
    "CTAS": "Cintas",
    "CVNA": "Carvana",
    "CVS": "CVS Health",
    "CVX": "Chevron",
    "DAL": "Delta Air",
    "DASH": "DoorDash",
    "DDOG": "Datadog",
    "DE": "Deere",
    "DECK": "Deckers",
    "DELL": "Dell",
    "DIS": "Disney",
    "DKNG": "DraftKings",
    "DOCN": "DigitalOcean",
    "DOCU": "DocuSign",
    "EA": "EA",
    "EBAY": "eBay",
    "EL": "Estee Lauder",
    "ENPH": "Enphase",
    "ESTC": "Elastic",
    "F": "Ford",
    "FANG": "Diamondback",
    "FAST": "Fastenal",
    "FDX": "FedEx",
    "FICO": "Fair Isaac",
    "FIG": "Simplify ETF",
    "FISV": "Fiserv",
    "FIVE": "Five Below",
    "GE": "GE Aerospace",
    "GILD": "Gilead",
    "GM": "GM",
    "GME": "GameStop",
    "GOOG": "Alphabet C",
    "GOOGL": "Alphabet A",
    "GS": "Goldman Sachs",
    "GTLB": "GitLab",
    "HD": "Home Depot",
    "HIMS": "Hims & Hers",
    "HLT": "Hilton",
    "HON": "Honeywell",
    "HOOD": "Robinhood",
    "HPQ": "HP",
    "HUBS": "HubSpot",
    "IBM": "IBM",
    "ICE": "ICE",
    "INTC": "Intel",
    "INTU": "Intuit",
    "ISRG": "Intuitive Surg",
    "JNJ": "J&J",
    "JPM": "JPMorgan",
    "K": "Kellanova",
    "KEYS": "Keysight",
    "KO": "Coca-Cola",
    "LEN": "Lennar",
    "LLY": "Eli Lilly",
    "LMND": "Lemonade",
    "LOW": "Lowe's",
    "LRCX": "Lam Research",
    "LULU": "Lululemon",
    "LUV": "Southwest Air",
    "LYFT": "Lyft",
    "M": "Macy's",
    "MA": "Mastercard",
    "MCD": "McDonald's",
    "MCHP": "Microchip Tech",
    "MDB": "MongoDB",
    "MDLZ": "Mondelez",
    "MDT": "Medtronic",
    "MET": "MetLife",
    "META": "Meta",
    "MMM": "3M",
    "MNDY": "monday.com",
    "MPC": "Marathon Petro",
    "MRK": "Merck",
    "MRNA": "Moderna",
    "MRVL": "Marvell",
    "MS": "Morgan Stanley",
    "MSFT": "Microsoft",
    "MSTR": "Strategy",
    "MU": "Micron",
    "NET": "Cloudflare",
    "NFLX": "Netflix",
    "NKE": "Nike",
    "NOW": "ServiceNow",
    "NVDA": "Nvidia",
    "NVO": "Novo Nordisk",
    "NXPI": "NXP Semi",
    "OKLO": "Oklo",
    "OKTA": "Okta",
    "ORCL": "Oracle",
    "OSCR": "Oscar Health",
    "OXM": "Oxford Indus",
    "PANW": "Palo Alto",
    "PATH": "UiPath",
    "PDD": "PDD Holdings",
    "PEP": "PepsiCo",
    "PFE": "Pfizer",
    "PG": "Procter&Gamble",
    "PINS": "Pinterest",
    "PLTR": "Palantir",
    "PYPL": "PayPal",
    "QCOM": "Qualcomm",
    "RBLX": "Roblox",
    "RBRK": "Rubrik",
    "RDDT": "Reddit",
    "RIVN": "Rivian",
    "ROKU": "Roku",
    "ROST": "Ross Stores",
    "S": "SentinelOne",
    "SHOP": "Shopify",
    "SMCI": "Super Micro",
    "SNAP": "Snap",
    "SNOW": "Snowflake",
    "SOFI": "SoFi",
    "SPOT": "Spotify",
    "SQ": "Block",
    "SYM": "Symbotic",
    "TER": "Teradyne",
    "TGT": "Target",
    "TOST": "Toast",
    "TSLA": "Tesla",
    "TSM": "TSMC",
    "TTD": "Trade Desk",
    "TWLO": "Twilio",
    "TXN": "Texas Instr",
    "UAL": "United Air",
    "UBER": "Uber",
    "ULTA": "Ulta Beauty",
    "UNH": "UnitedHealth",
    "UPS": "UPS",
    "UPST": "Upstart",
    "UPWK": "Upwork",
    "V": "Visa",
    "VEEV": "Veeva Systems",
    "VZ": "Verizon",
    "WDAY": "Workday",
    "WFC": "Wells Fargo",
    "WM": "Waste Mgmt",
    "WMT": "Walmart",
    "XOM": "ExxonMobil",
    "ZM": "Zoom",
    "ZS": "Zscaler",
}
```

For tickers not in this dictionary, display ticker only (no name). Do NOT make API calls.

### Step 7: Compute Day Density

Count deduped tickers per day. Mark the heaviest day with `***`.

Format: `Mon(5) Tue(14)*** Wed(11) Thu(3) Fri(0)`

### Step 8: Render Output

## Output Format

```
================================================================
EARNINGS CALENDAR — Week of {MONDAY} to {FRIDAY}
================================================================

Week at a Glance:  Mon(5)  Tue(14)***  Wed(11)  Thu(3)  Fri(0)
Sentiment: 8/33 cached | Budget: 12/60 calls used today

────────────────────────────────────────────────────────────────
MONDAY {DATE}
────────────────────────────────────────────────────────────────

BMO (Before Market Open):
  Ticker   Company          Conf  Hist  TRR      Trade History
  NVDA     Nvidia           Yes   12q   LOW      5T +$3,200 80%
  AMD      AMD              Yes   10q   HIGH!    3T -$450 33%

AMC (After Market Close):
  MU       Micron           Yes   8q    NORMAL   2T +$890 50%

UNKNOWN:
  XYZ      --               Est   3q    --       --

────────────────────────────────────────────────────────────────
TUESDAY {DATE}
────────────────────────────────────────────────────────────────
  ...

────────────────────────────────────────────────────────────────

PREVIOUSLY TRADED (sorted by total P&L):
  NVDA: 5 trades, 80.0% win, +$3,200     SINGLE preferred
  MU:   2 trades, 50.0% win, +$890       SPREAD preferred
  AMD:  3 trades, 33.3% win, -$450       Caution

HIGH TRR WATCHLIST:
  AMD(2.8x)  AVGO(3.1x)  SHOP(2.6x)  SMCI(4.2x)
  → Max 50 contracts / $25k notional each

FINNHUB-ONLY NOTABLES (not in DB, have history):
  DOCU(BMO)  PATH(AMC)  HUBS(BMO)

DEDUP NOTES:
  NVDA: removed from Wed (unconfirmed), kept Mon (confirmed BMO)
  AMD: removed from Tue (UNKNOWN timing), kept Mon (confirmed BMO)
  → {N} duplicates removed from {M} raw entries

SUMMARY
  Total tickers (deduped):  {N}
  Confirmed:                {N}
  Estimated:                {N}
  With history:             {N} (of {N})
  Previously traded:        {N}
  HIGH TRR:                 {N}

NEXT STEPS
  /whisper — VRP-ranked opportunities for the week
  /analyze TICKER — Deep dive on a specific ticker
  /prime — Pre-cache sentiment for the week
================================================================
```

### Column Format Details

**Daily table columns:**
- `Ticker` — 8 char, left-aligned
- `Company` — 15 char, left-aligned (from dictionary, `--` if unknown)
- `Conf` — 3 char: `Yes` or `Est`
- `Hist` — 4 char: `12q` (quarters of historical data)
- `TRR` — 8 char: `LOW`, `NORMAL`, `HIGH!`, or `--`
- `Trade History` — inline: `{N}T +${P&L} {win%}%` or `--` if never traded

**TRR display:**
- HIGH (>2.5x): Show as `HIGH!` (with bang) — max 50 contracts
- NORMAL (1.5-2.5x): Show as `NORMAL`
- LOW (<1.5x): Show as `LOW`
- Unknown: Show as `--`

**Trade History inline format:**
- `3T +$2,994 67%` means 3 trades, +$2,994 total P&L, 67% win rate
- `1T -$500 0%` means 1 trade, -$500 loss, 0% win rate
- `--` means never traded this ticker

**Previously Traded section:**
- Sort by total P&L descending (biggest winners first)
- Show preferred strategy type (most used) after the stats
- If win rate < 40%, add `Caution`

## Cost Control
- Finnhub calls: 1 max (earnings calendar - free, 60/min limit), skip if overflow
- No Perplexity calls (use /whisper for sentiment)
- Sentiment cache: read-only check (no API calls)
- Database queries only otherwise
