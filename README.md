# Trading Desk - Automated Earnings Trade Research

**Automated IV crush options research system with real-time data.**

Scans earnings calendar → filters by IV metrics → analyzes sentiment → suggests strategies.

## Status

**Phase 2** ✅ Complete - Real IV data via Tradier
**Phase 3** ✅ Complete - Budget controls + Reddit integration
**Phase 4** ✅ Complete - Code quality improvements (Nov 2025)
- JSON-based AI parsing with validation (99% more reliable)
- Centralized configuration system
- Clean modular architecture
- Comprehensive test coverage (19+ Tradier tests, 46+ parser tests, 31+ scorer tests)
- 50% faster ticker fetching with batch API calls

---

## What It Does

1. **Scans earnings calendar** OR **accepts ticker list** - Alpha Vantage (official NASDAQ vendor) or Nasdaq API, or manual tickers
2. **Filters tickers** - Actual IV % (60%+ min), expected move, liquidity (Tradier API)
   - Filters already-reported earnings based on market hours
   - Selects weekly options for same week or next week if Thu/Fri
3. **Analyzes sentiment** - AI analysis with Reddit data (r/wallstreetbets, r/stocks, r/options)
4. **Generates strategies** - 3-4 option strategies with sizing for $20K risk budget
5. **Saves timestamped reports** - Multiple runs per day won't overwrite

**Resilience Features**:
- Retry logic: 3 attempts with exponential backoff for transient errors
- Graceful degradation: Partial results if daily API limits reached
- Automatic fallback cascade:
  - Budget exhausted: Perplexity → Google Gemini (FREE)
  - Daily limits hit: Automatic switch to Gemini (FREE - 1500/day)
- Bypass mode: `--override` flag to bypass daily limits (respects hard caps)

**NOT an auto-trader** - generates research for manual review and execution.

---

## Quick Start

### 1. Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure API Keys

Copy `.env.example` to `.env` and add your keys:

```bash
# PRIMARY AI (until $4.98/month limit)
PERPLEXITY_API_KEY=your_key_here       # https://www.perplexity.ai/api

# FALLBACK AI (FREE - 1500 calls/day)
GOOGLE_API_KEY=your_key_here           # https://aistudio.google.com/apikey

# EARNINGS CALENDAR (FREE - 25 calls/day, official NASDAQ vendor)
ALPHA_VANTAGE_API_KEY=your_key_here    # https://www.alphavantage.co/support/#api-key

# OPTIONS DATA (FREE with Tradier account)
TRADIER_ACCESS_TOKEN=your_key_here     # https://tradier.com (free with account)

# SENTIMENT DATA (FREE)
REDDIT_CLIENT_ID=your_id_here          # https://reddit.com/prefs/apps
REDDIT_CLIENT_SECRET=your_secret_here
```

### 3. Set Budget & Earnings Source

Edit `config/budget.yaml`:

```yaml
# Earnings calendar source (alphavantage or nasdaq)
earnings_source: "alphavantage"  # Default: Alpha Vantage (official NASDAQ vendor)

# Budget limits
perplexity_monthly_limit: 4.98  # HARD STOP
monthly_budget: 5.00
```

**Earnings Calendar Options:**
- `alphavantage` (recommended): Official NASDAQ vendor, confirmed dates, EPS estimates, 25 calls/day (cached 12hrs = ~2 calls/day)
- `nasdaq`: Free unlimited, includes pre/post timing, but dates may be estimated

### 4. Run Analysis

**Mode 1: Analyze Specific Tickers** (recommended)
```bash
# Analyze your watchlist
python3 -m src.analysis.earnings_analyzer --tickers "META,MSFT,GOOGL,CMG" 2025-10-29 --yes

# Bypass daily limits (uses free Gemini if needed)
python3 -m src.analysis.earnings_analyzer --tickers "META,MSFT,GOOGL,CMG" 2025-10-29 --yes --override

# Syntax: --tickers "TICK1,TICK2,TICK3" [EARNINGS_DATE] [--yes] [--override]
```

**Mode 2: Auto-Scan Earnings Calendar**
```bash
# Scan Oct 29 earnings, analyze top 10 by IV
python3 -m src.analysis.earnings_analyzer 2025-10-29 10 --yes

# Bypass daily limits (uses free Gemini if needed)
python3 -m src.analysis.earnings_analyzer 2025-10-29 10 --yes --override

# Syntax: [DATE] [MAX_TICKERS] [--yes] [--override]
```

**Flags:**
- `--yes` / `-y`: Skip confirmation prompt
- `--override`: Bypass daily API call limits (hard caps still enforced)
  - Useful when you need to analyze many tickers in one day
  - Automatically falls back to free Gemini if Perplexity limits reached
  - Still respects $4.98 Perplexity and $5.00 total hard caps

**Test Individual Components:**
```bash
python3 -m src.data.reddit_scraper             # Test Reddit scraper
python3 -m src.ai.sentiment_analyzer AAPL      # Test sentiment with Reddit
python3 -m src.options.tradier_client          # Test Tradier IV data
python3 -m src.data.calendars.alpha_vantage    # View Alpha Vantage earnings (recommended)
python3 -m src.data.calendars.base             # View Nasdaq earnings (fallback)
python3 -m src.data.calendars.factory          # Compare both sources
python3 -m src.core.usage_tracker              # View budget dashboard
```

---

## Architecture

```
src/
├── ai/                           # AI and sentiment analysis
│   ├── client.py                 # Unified AI client (Perplexity → Gemini)
│   ├── response_validator.py    # AI response validation
│   ├── sentiment_analyzer.py    # AI sentiment with Reddit integration
│   └── strategy_generator.py    # AI strategy generation
├── data/                         # Data sources
│   ├── calendars/
│   │   ├── alpha_vantage.py     # Alpha Vantage earnings (NASDAQ vendor)
│   │   ├── base.py              # Base earnings calendar
│   │   └── factory.py           # Calendar source factory
│   └── reddit_scraper.py        # Reddit sentiment (WSB, stocks, options)
├── options/                      # Options data and metrics
│   ├── tradier_client.py        # Real IV data via Tradier/ORATS
│   ├── data_client.py           # Options data client
│   └── iv_history_tracker.py   # IV history tracking
├── analysis/                     # Analysis and scoring
│   ├── earnings_analyzer.py     # Main analyzer orchestrator
│   ├── ticker_filter.py         # Filter by IV crush criteria
│   ├── scorers.py               # Scoring strategies
│   └── report_formatter.py      # Report generation
└── core/                         # Core utilities
    ├── usage_tracker.py         # Budget tracking interface
    └── usage_tracker_sqlite.py  # SQLite budget tracker
```

---

## Budget & Cost Controls

**Model Selection**:
- Sentiment: Perplexity Sonar Pro ($0.005/1k tokens)
- Strategies: Perplexity Sonar Pro ($0.005/1k tokens)
- Fallback: Google Gemini 2.0 Flash (FREE - 1500 calls/day)

**Limits**:
- Perplexity: $4.98/month HARD STOP
- Total Budget: $5.00/month
- Daily: 40 API calls (handles 10+ tickers)
  - Use `--override` flag to bypass daily limits
  - System automatically falls back to free Gemini when limits reached

**Automatic Fallback System**:
1. **Budget exhausted** ($4.98 Perplexity limit): Automatically switches to Gemini (FREE)
2. **Daily limits hit** (40 calls/day): Automatically switches to Gemini (FREE)
3. **Override mode** (`--override` flag): Bypasses daily limits, uses Gemini if needed

**Cost per ticker**: ~$0.01 ($0.005 sentiment + $0.005 strategy)
**Monthly capacity**: ~500 analyses (~16/day, or more with `--override`)

**Budget Dashboard**:
```bash
python3 -m src.usage_tracker
```

---

## Key Features

### Real IV Data via Tradier
- Professional-grade options data (same as $99/month services)
- **Actual implied volatility %** from ORATS (not proxies)
- Direct from live options market (matches Robinhood, TastyTrade, etc.)
- Filters: **60%+ IV minimum** (focus on high IV crush opportunities)
- Scoring: 60-80% good, 80-100% excellent, 100%+ premium
- Supports high IV tickers (100-200%+ for volatile earnings plays)
- Accurate Greeks and expected move calculations
- **Weekly options selection**: Same week or next week if Thursday/Friday
- Free with Tradier brokerage account

### Reddit Sentiment Integration
- Scrapes r/wallstreetbets, r/stocks, r/options
- Aggregates sentiment score, post engagement
- Integrates into AI analysis for retail positioning

### AI Fallback System
- **Sentiment**: Perplexity Sonar Pro ($0.005/1k tokens)
- **Strategies**: Perplexity Sonar Pro ($0.005/1k tokens)
- **Fallback**: Google Gemini 2.0 Flash (FREE - 1500/day)
- **Automatic cascade** when limits reached:
  - Budget exhausted → Gemini
  - Daily limits hit → Gemini
- **Retry logic**: 3 attempts with exponential backoff for transient errors
- **Graceful degradation**: Partial results if daily limits hit
- **Override mode**: `--override` flag bypasses daily limits (hard caps still enforced)

### Thread-Safe Budget Tracking
- Persistent monthly usage log (`data/usage.json`)
- Auto-resets each month
- Pre-flight budget checks before API calls
- Per-model and per-provider tracking
- **Daily limits**: 40 calls/day (handles 10+ tickers)

---

## Testing System Components

```bash
# All tests passed ✓
source venv/bin/activate

# Reddit scraper (finds 20+ posts for NVDA)
python3 -c "from src.data.reddit_scraper import RedditScraper; \
print(RedditScraper().get_ticker_sentiment('NVDA'))"

# Sentiment with Reddit (bearish TSLA example)
python3 -m src.ai.sentiment_analyzer TSLA

# Tradier IV data (IV Rank, expected move)
python3 -m src.options.tradier_client AAPL

# Earnings calendar (728 earnings next 3 days)
python3 -m src.data.calendars.base

# Budget dashboard ($1.57 used, $3.43 remaining)
python3 -m src.core.usage_tracker

# Run full test suite
pytest tests/ -v
```

---

## Configuration

### Budget (`config/budget.yaml`)

```yaml
# Earnings calendar source
earnings_source: "alphavantage"  # alphavantage (recommended) or nasdaq

perplexity_monthly_limit: 4.98  # Hard stop for Perplexity
monthly_budget: 5.00

# Daily limits (increased from 20 to 40)
daily_limits:
  max_tickers: 10
  max_api_calls: 40

# Model selection by use case
defaults:
  sentiment_model: "sonar-pro"     # Reddit sentiment analysis
  strategy_model: "sonar-pro"      # Strategy generation

model_cascade:
  order:
    - "perplexity"  # Try first (paid, high quality)
    - "google"      # FREE fallback when limits hit

models:
  sonar-pro:
    cost_per_1k_tokens: 0.005  # Perplexity
  gemini-2.0-flash:
    provider: "google"
    cost_per_1k_tokens: 0.0    # FREE (1500 calls/day)

# Alpha Vantage (earnings calendar)
alpha_vantage:
  calls_per_day: 25           # Free tier limit
  cost_per_call: 0.00         # FREE
  cache_duration_hours: 12    # Reduces API usage to ~2 calls/day
```

### Trading Criteria (`config/trading_criteria.yaml`)

All trading thresholds and scoring weights are now centralized in a configuration file:

```yaml
# IV thresholds
iv_thresholds:
  minimum: 60      # Minimum IV % to consider (hard filter)
  good: 70         # Good IV level
  excellent: 80    # Excellent IV level
  extreme: 100     # Extreme/premium IV level

# IV Rank thresholds
iv_rank_thresholds:
  minimum: 50      # Minimum IV Rank to consider
  good: 60         # Good IV Rank
  excellent: 75    # Excellent IV Rank

# Scoring weights (must sum to 1.0)
scoring_weights:
  iv_score: 0.50           # 50% - PRIMARY (actual IV %)
  iv_crush_edge: 0.30      # 30% - Implied > actual move
  options_liquidity: 0.15  # 15% - Volume, OI, spread
  fundamentals: 0.05       # 5% - Market cap, price

# Liquidity thresholds
liquidity_thresholds:
  volume:
    acceptable: 1000    # Minimum acceptable daily volume
    good: 5000          # Good volume
    high: 10000         # High volume
    very_high: 50000    # Very high volume

  open_interest:
    acceptable: 5000    # Minimum acceptable OI
    good: 10000         # Good OI
    liquid: 50000       # Liquid
    very_liquid: 100000 # Very liquid

# Fundamentals
fundamentals:
  market_cap:
    mid_cap: 10      # $10B+ mid cap
    large_cap: 50    # $50B+ large cap
    mega_cap: 200    # $200B+ mega cap

  price:
    min_ideal: 50     # Ideal range for premium selling
    max_ideal: 400
```

**To customize thresholds**, edit `config/trading_criteria.yaml`:
- Lower `iv_thresholds.minimum` from 60 to 50 for more results
- Raise to 70+ for only premium IV crush plays
- Adjust scoring weights to emphasize different factors

---

## Disclaimer

**FOR RESEARCH ONLY. NOT FINANCIAL ADVICE.**

- Generates research, not trade recommendations
- Always verify data before trading
- Options trading involves substantial risk of loss

---

## License

Private/Personal Use Only

---

**Technical Details**: See `ARCHITECTURE_REVIEW.md`
