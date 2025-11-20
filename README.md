# Trading Desk - Earnings IV Crush Trading System

Automated research system for earnings options trading with real-time IV data and AI-powered analysis.

## Project Structure

This repository contains two versions of the IV Crush trading system:

- **`2.0/`** - **Production system (RECOMMENDED)**
  - Clean architecture with domain-driven design
  - Production-grade resilience (circuit breakers, retry logic, async processing)
  - 201 tests, health checks, monitoring
  - Empirically validated: Sharpe 8.07, 100% win rate on selected trades
  - Uses Tradier and Alpha Vantage APIs only
  - See `2.0/README.md` for usage

- **`1.0/`** - Legacy system (preserved for reference)
  - Original IV crush analyzer with AI-powered analysis
  - Uses Perplexity/Gemini for sentiment and strategy generation
  - See `1.0/src/` for implementation

---

## 🎯 What It Does

Analyzes earnings candidates using IV crush strategy:

1. **Filters** tickers by IV expansion velocity (weekly %), absolute IV level, liquidity, and historical performance
2. **Analyzes** sentiment from retail/institutional sources (Reddit + AI)
3. **Generates** 3-4 trade strategies with position sizing ($20K budget)
4. **Outputs** formatted research reports ready for manual execution

**Performance**: 12-14 seconds (75 tickers) | 80% faster than baseline | 210 API calls

---

## Quick Start (2.0 System)

### Install
```bash
cd 2.0
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure (`.env` file in 2.0/)
```bash
# Required
TRADIER_API_KEY=xxx              # Real IV data (free with account)
ALPHA_VANTAGE_KEY=xxx            # Earnings calendar

# Optional (whisper mode sentiment)
REDDIT_CLIENT_ID=xxx
REDDIT_CLIENT_SECRET=xxx
```

### Run Analysis
```bash
cd 2.0

# Analyze any ticker for earnings
./trade.sh NVDA 2025-11-20

# Scan all earnings for a date
./trade.sh scan 2025-11-20

# Analyze multiple tickers
./trade.sh list NVDA,AMD,WMT 2025-11-20

# Health check
./trade.sh health

# View help
./trade.sh --help
```

See `2.0/README.md` for complete documentation.

---

## Legacy 1.0 System

The 1.0 system is preserved for reference. It uses AI-powered analysis (Perplexity/Gemini).

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

### Run Analysis (1.0 System)
```bash
# Analyze specific tickers
python -m 1.0.src.analysis.earnings_analyzer --tickers "NVDA,META,GOOGL" 2025-11-08 --yes

# Scan earnings calendar
python -m 1.0.src.analysis.earnings_analyzer 2025-11-08 10 --yes
```

> **Note**: The 1.0 system is located in the `1.0/` directory. All import paths start with `1.0.src`

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

## 🏗️ Architecture (1.0 System)

```
1.0/
├── src/
│   ├── analysis/              # Core filtering and scoring
│   │   ├── earnings_analyzer.py  # Main orchestrator
│   │   ├── ticker_filter.py      # IV crush filtering
│   │   └── scorers.py            # Scoring strategies
│   ├── options/               # Options data
│   │   ├── tradier_client.py     # Real IV (Tradier/ORATS)
│   │   └── data_client.py        # Fallback (yfinance)
│   ├── ai/                    # AI analysis
│   │   ├── sentiment_analyzer.py
│   │   └── strategy_generator.py
│   ├── data/calendars/        # Earnings calendars
│   │   ├── alpha_vantage.py      # NASDAQ vendor (recommended)
│   │   └── base.py               # Nasdaq free tier
│   ├── config/                # Configuration
│   │   └── config_loader.py      # Shared config system
│   └── core/                  # Utilities
│       ├── lru_cache.py          # Bounded caching
│       ├── http_session.py       # Connection pooling
│       └── usage_tracker_sqlite.py  # Budget tracking
├── benchmarks/                # Performance tracking
├── profiling/                 # Code profiling tools
└── tests/                     # Test suite
```

---

## ⚙️ Configuration

### Budget (`1.0/config/budget.yaml`)
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

### Trading Criteria (`1.0/config/trading_criteria.yaml`)
```yaml
iv_thresholds:
  minimum: 60      # Hard filter
  excellent: 80

# NEW: IV expansion thresholds (weekly % change)
iv_expansion_thresholds:
  excellent: 80    # +80% weekly change (e.g., 40% → 72%)
  good: 40         # +40% weekly change
  moderate: 20     # +20% weekly change

# Optimized for 1-2 day pre-earnings entries
scoring_weights:
  iv_expansion_velocity: 0.35  # PRIMARY: Weekly IV % change (tactical timing)
  options_liquidity: 0.30      # Volume, OI, spreads (execution quality)
  iv_crush_edge: 0.25          # Historical implied > actual (strategy fit)
  current_iv_level: 0.25       # Absolute IV level (premium size)
  fundamentals: 0.05           # Market cap, price

liquidity_thresholds:
  minimum_volume: 100          # Hard filter
  minimum_open_interest: 500   # Hard filter
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

## 🧪 Testing (1.0 System)

```bash
# Test individual components
python -m 1.0.src.options.tradier_client AAPL       # Test Tradier IV data
python -m 1.0.src.ai.sentiment_analyzer TSLA        # Test sentiment
python -m 1.0.src.data.calendars.alpha_vantage      # View earnings calendar
python -m 1.0.src.core.usage_tracker                # Budget dashboard

# Component comparison
python -m 1.0.src.data.calendars.factory            # Compare calendar sources

# Full test suite
pytest 1.0/tests/ -v
```

---

## 📈 Strategy

**IV Crush Trading (Optimized for 1-2 Day Pre-Earnings Entries):**
1. Identify tickers with **strong IV expansion** (weekly IV +40%+ = premium building)
2. Sell premium **1-2 days before** earnings when IV peaks
3. Buy back **after** earnings when IV crashes
4. Profit from IV crush regardless of price direction

**Primary Filters:**
- **Weekly IV expansion** >+40% (premium building NOW - tactical timing)
- **Absolute IV level** >60% (enough premium to crush)
- **Liquid options** (tight spreads, high OI/volume - execution quality)
- **Historical crush edge** (implied > actual moves)
- **Market cap** >$500M, **daily volume** >100K

**Scoring Breakdown:**
- 35%: IV Expansion Velocity (is premium building right now?)
- 30%: Options Liquidity (can we execute efficiently?)
- 25%: IV Crush Edge (does it historically over-price moves?)
- 25%: Current IV Level (is there enough premium?)
- 5%: Fundamentals (market cap, price range)

**Output:** Research reports with scores, sentiment, and 3-4 trade strategies (strikes, sizing, risk/reward).

---

## Documentation

### 2.0 System (Production)
- `2.0/README.md` - Complete usage guide and system documentation
- `2.0/LIVE_TRADING_GUIDE.md` - Trading operations handbook
- `2.0/POSITION_SIZING_DEPLOYMENT.md` - Position sizing and empirical validation
- `docs/2.0_OVERVIEW.md` - Architecture and implementation timeline

### 1.0 System (Legacy)
- `1.0/config/trading_criteria.yaml` - Filter thresholds and scoring weights
- `1.0/config/budget.yaml` - API budgets and model selection

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
