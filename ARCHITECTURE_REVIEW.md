# Architecture Overview

**Last Updated**: October 2025 (Phase 3)

---

## System Design

```
┌─────────────────────────────────────────────────────────────┐
│                      TRADING DESK                           │
│              Automated Earnings Research System             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌──────────────────────────────────────┐
        │   1. EARNINGS CALENDAR SCANNER       │
        │   (earnings_calendar.py)             │
        │   - Nasdaq API                       │
        │   - Filters already-reported         │
        │   - Skips weekends/holidays          │
        └──────────────────────────────────────┘
                              │
                              ▼
        ┌──────────────────────────────────────┐
        │   2. TICKER FILTER                   │
        │   (ticker_filter.py)                 │
        │   - Actual IV % ≥ 60% (from Tradier) │
        │   - IV crush edge (implied > actual) │
        │   - Options liquidity check          │
        └──────────────────────────────────────┘
                              │
                              ▼
        ┌──────────────────────────────────────┐
        │   3. DATA COLLECTION                 │
        │   ├─ Tradier: Real IV % (ORATS)      │
        │   │  (tradier_options_client.py)     │
        │   ├─ Reddit: Retail sentiment        │
        │   │  (reddit_scraper.py)             │
        │   └─ yfinance: Historical data       │
        └──────────────────────────────────────┘
                              │
                              ▼
        ┌──────────────────────────────────────┐
        │   4. SENTIMENT ANALYSIS              │
        │   (sentiment_analyzer.py)            │
        │   - Reddit sentiment integration     │
        │   - AI analysis (Sonar Pro)          │
        │   - Retail/institutional/HF views    │
        └──────────────────────────────────────┘
                              │
                              ▼
        ┌──────────────────────────────────────┐
        │   5. STRATEGY GENERATION             │
        │   (strategy_generator.py)            │
        │   - 3-4 options strategies           │
        │   - Position sizing ($20K risk)      │
        │   - Probability of profit            │
        └──────────────────────────────────────┘
                              │
                              ▼
        ┌──────────────────────────────────────┐
        │   6. RESEARCH REPORT OUTPUT          │
        │   - Formatted for manual review      │
        │   - Execute on Fidelity manually     │
        └──────────────────────────────────────┘
```

---

## Budget Control System

```
┌──────────────────────────────────────────────────────────────┐
│                  USAGE TRACKER (usage_tracker.py)            │
│                                                              │
│  Monthly Budget: $5.00                                       │
│  Perplexity Limit: $4.90 (HARD STOP)                        │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Pre-Flight Check:                                     │ │
│  │  • Can we afford this call?                            │ │
│  │  • Perplexity limit exceeded?                          │ │
│  │  • Daily limits exceeded?                              │ │
│  └────────────────────────────────────────────────────────┘ │
│                           │                                  │
│                           ▼                                  │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  API CALL                                              │ │
│  └────────────────────────────────────────────────────────┘ │
│                           │                                  │
│                           ▼                                  │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Post-Call Logging:                                    │ │
│  │  • Tokens used                                         │ │
│  │  • Cost calculated                                     │ │
│  │  • Update data/usage.json                              │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## AI Client Fallback Cascade

```
┌───────────────────────────────────────────────────────────┐
│                  AI CLIENT (ai_client.py)                 │
│                                                           │
│  1. Check Perplexity budget ($4.90 limit)                │
│     │                                                     │
│     ├─ Available? ──> Use Sonar Pro ($0.005/1k tokens)  │
│     │                                                     │
│     └─ EXHAUSTED ──> 2. Fallback to Gemini              │
│                         (FREE - 1500 calls/day)          │
│                                                           │
│  All calls logged to usage_tracker                       │
└───────────────────────────────────────────────────────────┘
```

**Models**:
- `sonar-pro`: Perplexity's fast model ($5/1M tokens)
- `gemini-2.0-flash`: Google's free model (1500 RPD)

---

## Data Sources

### Earnings Calendar
- **Source**: Nasdaq API
- **Cost**: FREE
- **Data**: Upcoming earnings dates, times (pre/post market)
- **Filter**: Already-reported earnings, weekends, holidays

### IV Rank & Options Data
- **Source**: Tradier API (ORATS data)
- **Cost**: FREE (with Tradier account)
- **Data**:
  - Current implied volatility % (e.g., 93.82%, 123.39%, 207.68%)
  - Real IV Rank (52-week percentile) - *not yet implemented*
  - Expected move from ATM straddle
  - Options volume, open interest
  - Greeks (delta, gamma, theta, vega)
- **Note**: Tradier returns IV as 1.23 = 123%, automatically converted to percentage format

### Reddit Sentiment
- **Source**: Reddit API (PRAW)
- **Cost**: FREE
- **Subreddits**: r/wallstreetbets, r/stocks, r/options
- **Data**:
  - Post titles, scores, comment counts
  - Sentiment score (-1.0 to 1.0)
  - Top posts for context

### AI Analysis
- **Primary**: Perplexity Sonar Pro
- **Fallback**: Google Gemini
- **Data**: Sentiment analysis, strategy recommendations

---

## File Structure

```
Trading Desk/
├── src/
│   ├── earnings_calendar.py      # Nasdaq earnings scanner
│   ├── ticker_filter.py           # Filter by IV criteria
│   ├── tradier_options_client.py  # Real IV Rank via ORATS
│   ├── reddit_scraper.py          # Reddit sentiment scraper
│   ├── sentiment_analyzer.py      # AI sentiment with Reddit
│   ├── strategy_generator.py      # AI strategy generation
│   ├── ai_client.py               # Unified AI client
│   └── usage_tracker.py           # Budget tracking
│
├── config/
│   └── budget.yaml                # Budget limits & model config
│
├── data/
│   └── usage.json                 # Monthly usage log (auto-created)
│
├── .env                           # API keys (NOT in git)
├── requirements.txt               # Python dependencies
└── README.md                      # User documentation
```

---

## Key Design Decisions

### 1. Tradier for IV Data
**Why**: Free, professional-grade IV Rank from ORATS (same data as $99/month services)
**Alternative**: yfinance (free but no IV Rank), Alpha Vantage (paid)

### 2. Reddit Integration
**Why**: Retail sentiment is critical for earnings trades - Reddit captures WSB positioning
**Implementation**: Pre-fetch Reddit data, include in AI prompt for analysis

### 3. Perplexity → Gemini Cascade
**Why**:
- Perplexity has $4.90 hard limit (user constraint)
- Gemini is FREE with 1500 calls/day (sufficient fallback)
- Removed Anthropic (requires prepaid credits, not free)

### 4. Thread-Safe Budget Tracking
**Why**: Prepared for multiprocessing (Phase 4)
**Implementation**: File-based persistence with locking (ready for concurrent access)

### 5. Manual Execution Only
**Why**: User wants research, not auto-trading
**Implementation**: Generate formatted reports for manual review and execution

---

## Performance Characteristics

**Budget Utilization**:
- Per ticker: ~$0.01-0.02
- Monthly capacity: ~250 tickers (~8/day)
- Perplexity: Primary until $4.90
- Gemini: Unlimited fallback (1500/day)

**API Calls per Ticker**:
- Nasdaq: 1 call (earnings calendar)
- Tradier: 2-3 calls (quotes, expirations, chains)
- Reddit: 3 calls (3 subreddits)
- AI: 2 calls (sentiment + strategy)
- **Total**: ~8-9 API calls per ticker

**Speed**:
- Reddit scraping: ~5-10 seconds
- Tradier data: ~2-3 seconds
- AI analysis: ~10-20 seconds
- **Total**: ~20-35 seconds per ticker

---

## Cost Breakdown

**Monthly Budget**: $5.00

**API Costs**:
- Nasdaq: $0 (free)
- Tradier: $0 (free with account)
- Reddit: $0 (free)
- Perplexity: $0.01-0.02 per ticker (until $4.90)
- Gemini: $0 (free - 1500/day)

**Bottleneck**: Perplexity $4.90 limit (~250 tickers/month)

---

## Error Handling

### Budget Exceeded
- Pre-flight check fails → automatic cascade to Gemini
- All models exhausted → raise BudgetExceededError
- User informed via logs

### API Failures
- Tradier down → fallback to yfinance (limited data)
- Reddit down → continue without Reddit sentiment
- AI down → retry with fallback model
- Earnings calendar down → abort (no data to process)

### Data Quality
- Missing IV Rank → skip ticker (critical for strategy)
- Low options volume → flag in report (liquidity risk)
- No Reddit posts → note in sentiment (limited retail data)

---

## Thread Safety (Phase 4 Ready)

**Usage Tracker**:
- File-based persistence (`data/usage.json`)
- Thread lock for concurrent access
- Atomic read-modify-write operations
- Ready for multiprocessing implementation

**Next Steps for Phase 4**:
- Add `multiprocessing.Pool` for parallel ticker processing
- Add file locking (fcntl) for cross-process budget sync
- Add worker result aggregation
- Maintain sequential budget checks (critical path)

---

## Security Considerations

**.env File**:
- Contains API keys
- In `.gitignore` (never committed)
- Loaded via `python-dotenv`

**API Keys Required**:
- PERPLEXITY_API_KEY (required - primary AI)
- GOOGLE_API_KEY (required - fallback AI)
- TRADIER_ACCESS_TOKEN (required - IV data)
- REDDIT_CLIENT_ID (required - sentiment)
- REDDIT_CLIENT_SECRET (required - sentiment)

**No Secrets in Code**:
- All API keys from environment
- No hardcoded credentials
- Budget config in YAML (no secrets)

---

## Testing Strategy

**Unit Tests**: Individual components
```bash
python3 -m src.reddit_scraper          # Reddit API
python3 -m src.sentiment_analyzer AAPL # Sentiment + Reddit
python3 -m src.tradier_options_client  # Tradier IV data
python3 -m src.earnings_calendar       # Nasdaq calendar
python3 -m src.usage_tracker           # Budget dashboard
```

**Integration Test**: Full pipeline (TODO)
```bash
python3 -m src.earnings_analyzer       # End-to-end
```

---

## Future Enhancements (Phase 4+)

1. **Multiprocessing** - Parallel ticker processing
2. **Weekly options filtering** - Only include weekly expirations
3. **Automated scheduling** - Cron job for daily analysis
4. **Position tracking** - Log trades, track P/L
5. **Backtesting** - Historical earnings analysis
6. **Email reports** - Daily summary to inbox

---

**This architecture is production-ready for manual research workflows.**
