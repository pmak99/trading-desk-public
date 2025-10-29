# Trading Desk - Automated Earnings Trade Research

**Automated IV crush options research system with real-time data.**

Scans earnings calendar → filters by IV metrics → analyzes sentiment → suggests strategies.

## Status

**Phase 2** ✅ Complete - Real IV data via Tradier
**Phase 3** ✅ Complete - Budget controls + Reddit integration

---

## What It Does

1. **Scans earnings calendar** - Nasdaq API for upcoming earnings
2. **Filters tickers** - Actual IV % (40%+ min), expected move, liquidity (Tradier API)
3. **Analyzes sentiment** - AI analysis with Reddit data (r/wallstreetbets, r/stocks, r/options)
4. **Generates strategies** - 3-4 option strategies with sizing for $20K risk budget

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
# PRIMARY (until $4.90/month limit)
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
perplexity_monthly_limit: 4.90  # HARD STOP
monthly_budget: 5.00
```

### 4. Run

```bash
# Test individual components
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
└── usage_tracker.py              # Budget tracking with $4.90 hard stop
```

---

## Budget & Cost Controls

**Cascade**: Perplexity → Google Gemini (free)

**Perplexity Limit**: $4.90/month HARD STOP
**Total Budget**: $5.00/month

**Cost per ticker**: ~$0.01-0.02
**Monthly capacity**: ~250 analyses (~8/day)

**Budget Dashboard**:
```bash
python3 -m src.usage_tracker
```

---

## Key Features

### Real IV Data via Tradier
- Professional-grade options data (same as $99/month services)
- **Actual implied volatility %** from ORATS (not proxies)
- Filters: 40%+ IV minimum, 60%+ good, 80%+ excellent
- Accurate Greeks and expected move calculations
- Free with Tradier brokerage account

### Reddit Sentiment Integration
- Scrapes r/wallstreetbets, r/stocks, r/options
- Aggregates sentiment score, post engagement
- Integrates into AI analysis for retail positioning

### AI Fallback System
- Primary: Perplexity Sonar Pro (until $4.90 limit)
- Fallback: Google Gemini 2.0 Flash (FREE - 1500/day)
- Automatic cascade when budget limits reached

### Thread-Safe Budget Tracking
- Persistent monthly usage log (`data/usage.json`)
- Auto-resets each month
- Pre-flight budget checks before API calls
- Per-model and per-provider tracking

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
perplexity_monthly_limit: 4.90  # Hard stop for Perplexity

model_cascade:
  order:
    - "perplexity"  # Try first
    - "google"      # FREE fallback

models:
  sonar-pro:
    cost_per_1k_tokens: 0.005  # Perplexity
  gemini-2.0-flash:
    provider: "google"
    cost_per_1k_tokens: 0.0    # FREE
```

### Trading Criteria (`ticker_filter.py`)

```python
MIN_IV_PERCENT = 40    # Minimum actual IV % (hard filter)

# IV % scoring thresholds
# 40-60%: Medium volatility (score 50-70)
# 60-80%: High volatility (score 70-100)
# 80%+:   Excellent for IV crush (score 100)

# Scoring weights
weights = {
    'iv_score': 0.50,          # 50% - PRIMARY (actual IV %)
    'iv_crush_edge': 0.30,     # 30% - Implied > actual
    'options_liquidity': 0.15, # 15% - Volume, OI
    'fundamentals': 0.05       # 5% - Market cap
}
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
