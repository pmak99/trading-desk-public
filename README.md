# Trading Desk - Automated Earnings Trade Research

**Automated research system for IV crush options trades on earnings.**

Automates your manual research workflow from Perplexity → generates complete trade analysis with strategies, sentiment, and position sizing.

## Project Status

**Phase 1**: Data Collection ✅ Complete  
**Phase 2**: Analysis & Strategy ✅ Complete (Refactored)  
**Version:** 1.0.0

---

## What This Does

Automates the research process from your `Trading Research Prompt.pdf`:

1. **Filters tickers** by IV crush criteria (IV Rank > 50%, implied > actual moves)
2. **Analyzes sentiment** (retail/institutional/hedge fund positioning)
3. **Generates 3-4 trade strategies** with strikes, sizing, and probability of profit
4. **Outputs formatted research report** for manual execution on Fidelity

**NOT an execution system** - you manually review and execute trades.

---

## Quick Start

### 1. Install Dependencies

```bash
cd "Trading Desk"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure API Keys

Edit `.env`:
```bash
# Required for Phase 2
PERPLEXITY_API_KEY=your_key_here  # Get from https://www.perplexity.ai/api
OPENAI_API_KEY=your_key_here      # Get from https://platform.openai.com
```

### 3. Set Budget Limits

Edit `config/budget.yaml`:
```yaml
monthly_budget: 5.00  # USD
warn_at_percentage: 80
hard_stop: true
```

### 4. Run Daily Analysis

```bash
# Analyze today's earnings (default: 2 tickers max)
python3 -m src.earnings_analyzer

# Analyze specific date with custom limit
python3 -m src.earnings_analyzer 2025-11-20 3
```

**Output**: Research report saved to `data/earnings_analysis_YYYY-MM-DD.txt`

---

## ⚠️ Important Limitations

### IV Rank Calculation

**Current**: Uses **realized volatility rank** as a proxy for implied volatility rank.

**Why This Matters**:
- Real IV Rank requires historical IV data (not available in free APIs)
- Realized vol ≠ Implied vol (can differ by 20-50% around earnings)
- **This affects your primary filter** for IV crush strategy

**Accuracy**: ~70-80% correlation - good enough for initial filtering, not perfect.

**To Get Real IV Rank**: TastyTrade API, CBOE DataShop, Interactive Brokers API, or paid Alpha Vantage.

**Recommended**: Use this system for initial screening, verify IV Rank manually before trading.

---

## Cost Breakdown

**Per Ticker Analysis**:
- Earnings Calendar: $0.00 (free)
- Ticker Filtering: $0.00 (yfinance free)
- Sentiment Analysis: ~$0.01-0.02 (Perplexity)
- Strategy Generation: ~$0.005-0.01 (OpenAI)
- **Total**: ~$0.02-0.03 per ticker

**Monthly Budget ($5.00)**:
- ~150-250 ticker analyses per month
- ~5-8 tickers per day
- Good for daily trading routine

---

## Architecture

```
src/
├── earnings_calendar.py      # Get upcoming earnings (Nasdaq API)
├── ticker_filter.py           # Filter by IV crush criteria
├── options_data_client.py     # IV Rank, expected move, liquidity (yfinance)
├── sentiment_analyzer.py      # Perplexity Sonar sentiment analysis
├── strategy_generator.py      # OpenAI GPT-4 strategy generation
├── earnings_analyzer.py       # Master orchestrator
└── usage_tracker.py           # Budget tracking & cost controls
```

---

## Testing Individual Components

```bash
# Test earnings calendar (free)
python3 -m src.earnings_calendar

# Test ticker filter (free)
python3 -m src.ticker_filter

# Test options data (free)
python3 -m src.options_data_client AAPL

# Test sentiment ($0.01 cost)
python3 -m src.sentiment_analyzer NVDA

# View usage dashboard
python3 -m src.usage_tracker
```

---

## Configuration

### Budget Config (`config/budget.yaml`)

```yaml
monthly_budget: 5.00
defaults:
  sentiment_model: "sonar-pro"      # $0.005/1k tokens
  strategy_model: "gpt-4o-mini"     # $0.00015/1k tokens

daily_limits:
  max_tickers: 5
  sonar-pro_calls: 20
  gpt-4o-mini_calls: 20
```

### Trading Criteria (`ticker_filter.py`)

```python
# IV Rank thresholds
IV_RANK_MIN = 50      # Skip below this
IV_RANK_GOOD = 60     # Standard $20K allocation
IV_RANK_EXCELLENT = 75 # Larger allocation

# Scoring weights
weights = {
    'iv_rank': 0.50,           # 50% - PRIMARY
    'iv_crush_edge': 0.30,     # 30% - Implied > actual
    'options_liquidity': 0.15, # 15% - Volume, OI, spreads
    'fundamentals': 0.05       # 5% - Market cap, price
}
```

---

## Recent Refactoring (Option B - Full)

✅ **Completed Improvements**:
1. Integrated UsageTracker into all API clients for cost control
2. Eliminated duplicate yfinance calls (was fetching twice per ticker)
3. Renamed AlphaVantageClient → OptionsDataClient (clarity)
4. Documented IV Rank limitation clearly
5. Added comprehensive README

**Performance**: ~50% reduction in yfinance API calls

---

## Disclaimer

**FOR RESEARCH ONLY. NOT FINANCIAL ADVICE.**

- This tool generates research, NOT trade recommendations
- Always verify data and analysis before trading
- IV Rank uses realized volatility as a proxy (see limitations)
- Options trading involves substantial risk of loss

Use at your own risk.

---

## License

Private/Personal Use Only

---

**See `ARCHITECTURE_REVIEW.md` for detailed technical analysis.**
