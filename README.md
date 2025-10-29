# Trading Desk - Automated Earnings Trade Research

**Automated IV crush options research system with real-time data.**

Scans earnings calendar → filters by IV metrics → analyzes sentiment → suggests strategies.

## Status

**Phase 2** ✅ Complete - Real IV data via Tradier
**Phase 3** ✅ Complete - Budget controls + Reddit integration

---

## What It Does

1. **Scans earnings calendar** OR **accepts ticker list** - Nasdaq API or manual tickers
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
# PRIMARY (until $4.98/month limit)
PERPLEXITY_API_KEY=your_key_here       # https://www.perplexity.ai/api

# FALLBACK (FREE - 1500 calls/day)
GOOGLE_API_KEY=your_key_here           # https://aistudio.google.com/apikey

# DATA SOURCES
TRADIER_ACCESS_TOKEN=your_key_here     # https://tradier.com (free with account)
REDDIT_CLIENT_ID=your_id_here          # https://reddit.com/prefs/apps
REDDIT_CLIENT_SECRET=your_secret_here
```

### 3. Set Budget

Edit `config/budget.yaml`:

```yaml
perplexity_monthly_limit: 4.98  # HARD STOP
monthly_budget: 5.00
```

### 4. Run Analysis

**Mode 1: Analyze Specific Tickers** (recommended)
```bash
# Analyze your watchlist
python3 -m src.earnings_analyzer --tickers "META,MSFT,GOOGL,CMG" 2025-10-29 --yes

# Bypass daily limits (uses free Gemini if needed)
python3 -m src.earnings_analyzer --tickers "META,MSFT,GOOGL,CMG" 2025-10-29 --yes --override

# Syntax: --tickers "TICK1,TICK2,TICK3" [EARNINGS_DATE] [--yes] [--override]
```

**Mode 2: Auto-Scan Earnings Calendar**
```bash
# Scan Oct 29 earnings, analyze top 10 by IV
python3 -m src.earnings_analyzer 2025-10-29 10 --yes

# Bypass daily limits (uses free Gemini if needed)
python3 -m src.earnings_analyzer 2025-10-29 10 --yes --override

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
python3 -m src.reddit_scraper          # Test Reddit scraper
python3 -m src.sentiment_analyzer AAPL # Test sentiment with Reddit
python3 -m src.tradier_options_client  # Test Tradier IV data
python3 -m src.earnings_calendar       # View upcoming earnings
python3 -m src.usage_tracker           # View budget dashboard
```

---

## Architecture

```
src/
├── earnings_calendar.py         # Nasdaq earnings calendar
├── ticker_filter.py              # Filter by IV crush criteria
├── tradier_options_client.py     # Real IV Rank via ORATS
├── reddit_scraper.py             # Reddit sentiment (WSB, stocks, options)
├── sentiment_analyzer.py         # AI analysis with Reddit integration
├── strategy_generator.py         # AI strategy generation
├── ai_client.py                  # Unified AI client (Perplexity → Gemini)
└── usage_tracker.py              # Budget tracking with $4.98 hard stop
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
python3 -c "from src.reddit_scraper import RedditScraper; \
print(RedditScraper().get_ticker_sentiment('NVDA'))"

# Sentiment with Reddit (bearish TSLA example)
python3 -m src.sentiment_analyzer TSLA

# Tradier IV data (IV Rank, expected move)
python3 -m src.tradier_options_client AAPL

# Earnings calendar (728 earnings next 3 days)
python3 -m src.earnings_calendar

# Budget dashboard ($1.57 used, $3.43 remaining)
python3 -m src.usage_tracker
```

---

## Configuration

### Budget (`config/budget.yaml`)

```yaml
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
```

### Trading Criteria (`ticker_filter.py`)

```python
MIN_IV_PERCENT = 60    # Minimum actual IV % (hard filter)
                       # Focus on high IV crush opportunities only

# IV % scoring thresholds (from Tradier ORATS data)
# 60-80%:   Good volatility (score 60-80)
# 80-100%:  Excellent for IV crush (score 80-100)
# 100%+:    Premium IV crush opportunity (score 100)

# Note: Tradier returns IV in format 1.23 = 123%, 0.50 = 50%
#       We multiply by 100 to get standard percentage format

# Scoring weights
weights = {
    'iv_score': 0.50,          # 50% - PRIMARY (actual IV %)
    'iv_crush_edge': 0.30,     # 30% - Implied > actual
    'options_liquidity': 0.15, # 15% - Volume, OI
    'fundamentals': 0.05       # 5% - Market cap
}
```

**To customize the IV threshold**, edit `src/ticker_filter.py` line 204:
```python
MIN_IV_PERCENT = 60  # Lower to 50 for more results, raise to 70+ for only premium plays
```

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
