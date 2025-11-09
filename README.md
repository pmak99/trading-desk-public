# Trading Desk - Earnings IV Crush Analyzer

Automated research system for earnings options trading with real-time IV data and AI-powered analysis.

---

## 🎯 What It Does

Analyzes earnings candidates using IV crush strategy:

1. **Filters** tickers by IV Rank (>50%), liquidity, and historical performance
2. **Analyzes** sentiment from retail/institutional sources (Reddit + AI)
3. **Generates** 3-4 trade strategies with position sizing ($20K budget)
4. **Outputs** formatted research reports ready for manual execution

**Performance**: 12-14 seconds (75 tickers) | 80% faster than baseline | 210 API calls

---

## 🚀 Quick Start

### Install
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure (`.env` file)
```bash
# Required
TRADIER_ACCESS_TOKEN=xxx         # Real IV data (free with account)
ALPHA_VANTAGE_API_KEY=xxx        # Earnings calendar (25 calls/day free)

# Optional (AI analysis)
PERPLEXITY_API_KEY=xxx           # Sentiment ($4.98/month cap)
GOOGLE_API_KEY=xxx               # Fallback (free, 1500/day)

# Optional (sentiment)
REDDIT_CLIENT_ID=xxx
REDDIT_CLIENT_SECRET=xxx
```

### Run Analysis
```bash
# Analyze specific tickers
python -m src.analysis.earnings_analyzer --tickers "NVDA,META,GOOGL" 2025-11-08 --yes

# Scan earnings calendar
python -m src.analysis.earnings_analyzer 2025-11-08 10 --yes

# Override daily limits (uses free Gemini fallback)
python -m src.analysis.earnings_analyzer --tickers "AAPL,MSFT" 2025-11-08 --yes --override
```

---

## 📊 Key Features

### Real IV Data (Tradier/ORATS)
- Professional-grade implied volatility (same as $99/month services)
- Real IV Rank vs yfinance RV proxy
- Accurate Greeks and expected moves
- Free with Tradier brokerage account

### Optimized Performance
- **80% faster** - Batch fetching, smart caching, multiprocessing
- **69% fewer API calls** - Eliminated duplicates, reuse data
- **Bounded memory** - LRU caching (360 MB max)
- See commit history for optimization details

### AI-Powered Analysis
- Sentiment: Perplexity Sonar Pro ($0.005/1k tokens)
- Strategies: Perplexity Sonar Pro or free Gemini fallback
- Reddit integration (r/wallstreetbets, r/stocks, r/options)
- Automatic cascade when budgets reached

### Budget Controls
- Perplexity: $4.98/month hard cap
- Total: $5.00/month budget
- Daily: 40 API calls (10+ tickers)
- Auto-fallback to free Gemini when limits hit
- `--override` flag bypasses daily limits

---

## 🏗️ Architecture

```
src/
├── analysis/              # Core filtering and scoring
│   ├── earnings_analyzer.py  # Main orchestrator
│   ├── ticker_filter.py      # IV crush filtering
│   └── scorers.py            # Scoring strategies
├── options/               # Options data
│   ├── tradier_client.py     # Real IV (Tradier/ORATS)
│   └── data_client.py        # Fallback (yfinance)
├── ai/                    # AI analysis
│   ├── sentiment_analyzer.py
│   └── strategy_generator.py
├── data/calendars/        # Earnings calendars
│   ├── alpha_vantage.py      # NASDAQ vendor (recommended)
│   └── base.py               # Nasdaq free tier
├── config/                # Configuration
│   └── config_loader.py      # Shared config system
└── core/                  # Utilities
    ├── lru_cache.py          # Bounded caching
    ├── http_session.py       # Connection pooling
    └── usage_tracker_sqlite.py  # Budget tracking

benchmarks/                # Performance tracking
profiling/                 # Code profiling tools
```

---

## ⚙️ Configuration

### Budget (`config/budget.yaml`)
```yaml
earnings_source: "alphavantage"  # or "nasdaq"
perplexity_monthly_limit: 4.98   # Hard stop
monthly_budget: 5.00

daily_limits:
  max_tickers: 10
  max_api_calls: 40

defaults:
  sentiment_model: "sonar-pro"
  strategy_model: "sonar-pro"

model_cascade:
  order: ["perplexity", "google"]  # Auto-fallback
```

### Trading Criteria (`config/trading_criteria.yaml`)
```yaml
iv_thresholds:
  minimum: 60      # Hard filter
  excellent: 80

iv_rank_thresholds:
  minimum: 50      # Hard filter
  excellent: 75

scoring_weights:
  iv_score: 0.40           # IV level/rank
  options_liquidity: 0.30  # Volume, OI, spreads
  iv_crush_edge: 0.25      # Historical implied > actual
  fundamentals: 0.05       # Market cap, price

liquidity_thresholds:
  minimum_volume: 100
  minimum_open_interest: 500
```

---

## 🔧 Advanced Tools

### Performance Benchmarking
```bash
python benchmarks/performance_tracker.py --tickers "AAPL,MSFT,GOOGL" --baseline
python benchmarks/performance_tracker.py --tickers "AAPL,MSFT,GOOGL" --compare
python benchmarks/performance_tracker.py --history
```

### Profiling
```bash
python profiling/profiler.py --run "python -m src.analysis.earnings_analyzer..."
python profiling/profiler.py --analyze results/profile.prof
python profiling/profiler.py --hotspots results/profile.prof
```

### Market Calendar (Optional)
```bash
pip install pandas-market-calendars

python -c "from src.data.calendars.market_calendar import MarketCalendarClient; \
  calendar = MarketCalendarClient(); \
  print(f'Trading day: {calendar.is_trading_day(datetime.now())}')"
```

See `ENHANCEMENTS.md` for complete guide.

---

## 🧪 Testing

```bash
# Test individual components
python -m src.options.tradier_client AAPL       # Test Tradier IV data
python -m src.ai.sentiment_analyzer TSLA        # Test sentiment
python -m src.data.calendars.alpha_vantage      # View earnings calendar
python -m src.core.usage_tracker                # Budget dashboard

# Component comparison
python -m src.data.calendars.factory            # Compare calendar sources

# Full test suite
pytest tests/ -v
```

---

## 📈 Strategy

**IV Crush Trading:**
1. Sell premium **before** earnings when IV is elevated (Rank >75%)
2. Buy back **after** earnings when IV crashes
3. Profit from IV crush regardless of price direction

**Filters:**
- IV Rank >50% (prefer 75%+)
- Liquid options (tight spreads, high OI/volume)
- Historical implied > actual moves
- Market cap >$500M, daily volume >100K

**Output:** Research reports with scores, sentiment, and 3-4 trade strategies (strikes, sizing, risk/reward).

---

## 📚 Documentation

- `ENHANCEMENTS.md` - Performance tools (benchmarking, profiling, market calendar)
- `config/trading_criteria.yaml` - Filter thresholds and scoring weights
- `config/budget.yaml` - API budgets and model selection

---

## 🎓 Optimization Highlights

**Recent improvements (80-83% faster):**
- ✅ Batch API fetching (30-50% improvement)
- ✅ Smart multiprocessing (sequential <3 tickers, parallel ≥3)
- ✅ LRU caching with automatic eviction (bounded memory)
- ✅ Eliminated duplicate API calls (save ~150 calls)
- ✅ Reuse history data across functions
- ✅ Specific exception handling for better debugging

Total: **71s → 12-14s (80% faster) | 670 → 210 API calls (69% reduction)**

See commit log for implementation details.

---

## ⚠️ Disclaimer

**FOR RESEARCH PURPOSES ONLY. NOT FINANCIAL ADVICE.**

Options trading carries significant risk of loss. This tool provides research data for manual review and execution. Always verify data and strategies before trading real money.

---

## 📜 License

MIT License - See LICENSE file

---

**Built with Claude Code** 🤖

*Version: Optimized 2025*
