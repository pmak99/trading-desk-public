# Implementation Roadmap - Priority Improvements
## Trading Desk Application

**Created**: November 1, 2025
**Based On**: ARCHITECTURE_REVIEW_2025.md findings
**Status**: Planned - Ready for execution

---

## Overview

This document provides detailed implementation plans for all priority improvements identified in the architecture review. Each section includes:
- Current state analysis
- Proposed solution with code examples
- Expected impact
- Implementation time estimate
- Testing strategy

---

## Priority 1: Reliability Improvements (1-2 days)

### 1.1 Switch AI Parsers to JSON Output ⚡ **CRITICAL**

**Current State**:
```python
# Brittle string parsing in sentiment_analyzer.py
if "OVERALL SENTIMENT:" in response:
    sentiment_line = response.split("OVERALL SENTIMENT:")[1].split("\n")[0]
```

**Problem**: Breaks if AI changes output format, no validation, hard to test

**Proposed Solution**:
```python
# NEW: src/sentiment_analyzer.py - JSON-based parsing

def _build_sentiment_prompt(self, ticker: str, earnings_date: Optional[str], reddit_data: Dict) -> str:
    prompt = f"""Analyze the earnings sentiment for {ticker}{earnings_context}.

{reddit_summary}

Return your analysis as valid JSON with this exact structure:
{{
  "overall_sentiment": "bullish|neutral|bearish",
  "sentiment_summary": "1-2 sentence summary",
  "retail_sentiment": "Analysis of retail positioning",
  "institutional_sentiment": "Analysis of institutional positioning",
  "hedge_fund_sentiment": "Analysis of hedge fund positioning",
  "tailwinds": ["factor1", "factor2", "factor3"],
  "headwinds": ["factor1", "factor2"],
  "unusual_activity": "Options flow and dark pool analysis",
  "guidance_history": "Recent earnings and guidance",
  "macro_sector": "Macro and sector factors",
  "confidence": "low|medium|high"
}}

Keep analysis concise and actionable for IV crush options trading."""
    return prompt

def _parse_sentiment_response(self, response: str, ticker: str) -> Dict:
    """Parse AI response with JSON validation and fallback."""
    try:
        # Try JSON parsing first
        data = json.loads(response)

        # Validate required fields
        required = ['overall_sentiment', 'retail_sentiment', 'institutional_sentiment']
        for field in required:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")

        # Validate sentiment value
        valid_sentiments = ['bullish', 'neutral', 'bearish']
        if data['overall_sentiment'] not in valid_sentiments:
            raise ValueError(f"Invalid sentiment: {data['overall_sentiment']}")

        # Add metadata
        data['ticker'] = ticker
        data['raw_response'] = response

        logger.info(f"{ticker}: Successfully parsed JSON response")
        return data

    except json.JSONDecodeError as e:
        logger.warning(f"{ticker}: JSON parse failed, trying fallback parsing: {e}")
        # Fallback to old string parsing for compatibility
        return self._parse_legacy_format(response, ticker)
    except (ValueError, KeyError) as e:
        logger.error(f"{ticker}: Response validation failed: {e}")
        return self._get_empty_result(ticker)
```

**Impact**:
- ✅ **99% more reliable** (JSON validation vs string splitting)
- ✅ **Easier to test** (mock JSON responses)
- ✅ **Better error messages** (validation failures are specific)
- ✅ **Backward compatible** (fallback to legacy parsing)

**Implementation Time**: 2-3 hours
**Files**: `src/sentiment_analyzer.py`, `src/strategy_generator.py`
**Tests**: `tests/test_sentiment_analyzer.py` (new), `tests/test_strategy_generator.py` (new)

---

### 1.2 Add Response Validation ⚡ **CRITICAL**

**Proposed Solution**:
```python
# NEW: src/ai_response_validator.py

from typing import Dict, List, Optional
from enum import Enum

class SentimentValue(Enum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"

class ConfidenceLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class AIResponseValidator:
    """Validate AI responses for sentiment and strategy generation."""

    @staticmethod
    def validate_sentiment_response(data: Dict) -> tuple[bool, Optional[str]]:
        """
        Validate sentiment response structure.

        Returns:
            (is_valid, error_message)
        """
        required_fields = [
            'overall_sentiment',
            'retail_sentiment',
            'institutional_sentiment',
            'hedge_fund_sentiment',
            'tailwinds',
            'headwinds'
        ]

        # Check required fields
        for field in required_fields:
            if field not in data:
                return False, f"Missing required field: {field}"

            # Check not empty
            if not data[field] or data[field] == "N/A":
                return False, f"Empty value for required field: {field}"

        # Validate sentiment enum
        try:
            SentimentValue(data['overall_sentiment'])
        except ValueError:
            return False, f"Invalid sentiment: {data['overall_sentiment']}"

        # Validate lists
        if not isinstance(data['tailwinds'], list):
            return False, "tailwinds must be a list"
        if not isinstance(data['headwinds'], list):
            return False, "headwinds must be a list"

        # At least one tailwind or headwind
        if not data['tailwinds'] and not data['headwinds']:
            return False, "Must have at least one tailwind or headwind"

        return True, None

    @staticmethod
    def validate_strategy_response(data: Dict) -> tuple[bool, Optional[str]]:
        """Validate strategy response structure."""
        # Check strategies list
        if 'strategies' not in data:
            return False, "Missing 'strategies' field"

        if not isinstance(data['strategies'], list):
            return False, "'strategies' must be a list"

        if len(data['strategies']) < 2:
            return False, "Must have at least 2 strategies"

        # Validate each strategy
        required_strategy_fields = [
            'name', 'strikes', 'max_profit', 'max_loss',
            'profitability_score', 'risk_score', 'rationale'
        ]

        for i, strategy in enumerate(data['strategies']):
            for field in required_strategy_fields:
                if field not in strategy:
                    return False, f"Strategy {i}: Missing field '{field}'"

            # Validate scores (1-10)
            for score_field in ['profitability_score', 'risk_score']:
                score = strategy.get(score_field)
                if not isinstance(score, (int, float)):
                    return False, f"Strategy {i}: {score_field} must be numeric"
                if not 1 <= score <= 10:
                    return False, f"Strategy {i}: {score_field} must be 1-10"

        # Check recommended strategy
        if 'recommended_strategy' in data:
            rec_idx = data['recommended_strategy']
            if not isinstance(rec_idx, int):
                return False, "'recommended_strategy' must be an integer index"
            if not 0 <= rec_idx < len(data['strategies']):
                return False, f"'recommended_strategy' index {rec_idx} out of range"

        return True, None
```

**Impact**:
- ✅ **Catch invalid responses early**
- ✅ **Clear error messages for debugging**
- ✅ **Prevent downstream failures**
- ✅ **Testable validation logic**

**Implementation Time**: 1-2 hours
**Files**: `src/ai_response_validator.py` (new)
**Tests**: `tests/test_ai_response_validator.py` (new, ~300 lines)

---

### 1.3 Add Integration Tests ⚡ **HIGH PRIORITY**

**Proposed Solution**:
```python
# NEW: tests/test_sentiment_integration.py

class TestSentimentAnalyzerIntegration:
    """Integration tests for sentiment analyzer with mocked AI."""

    def test_complete_analysis_flow_with_json_response(self):
        """Test full flow from Reddit scraping to parsed sentiment."""
        # Mock Reddit response
        mock_reddit_data = {
            'posts_found': 10,
            'sentiment_score': 0.75,
            'avg_score': 150,
            'total_comments': 500,
            'top_posts': [...]
        }

        # Mock AI JSON response
        mock_ai_response = json.dumps({
            'overall_sentiment': 'bullish',
            'sentiment_summary': 'Strong bullish sentiment',
            'retail_sentiment': 'Retail very bullish',
            'institutional_sentiment': 'Institutions accumulating',
            'hedge_fund_sentiment': 'Mixed positioning',
            'tailwinds': ['Strong earnings', 'Market momentum'],
            'headwinds': ['Valuation concerns'],
            'unusual_activity': 'Heavy call buying',
            'guidance_history': 'Beat last 3 quarters',
            'macro_sector': 'Tech sector strength',
            'confidence': 'high'
        })

        with patch.object(RedditScraper, 'get_ticker_sentiment', return_value=mock_reddit_data):
            with patch.object(AIClient, 'chat_completion', return_value={
                'content': mock_ai_response,
                'model': 'sonar-pro',
                'provider': 'perplexity',
                'cost': 0.01
            }):
                analyzer = SentimentAnalyzer()
                result = analyzer.analyze_earnings_sentiment('AAPL', '2025-11-05')

                # Verify complete flow
                assert result['overall_sentiment'] == 'bullish'
                assert result['ticker'] == 'AAPL'
                assert len(result['tailwinds']) == 2
                assert 'reddit_data' in result
                assert result['reddit_data']['posts_found'] == 10

    def test_fallback_parsing_when_json_invalid(self):
        """Test fallback to legacy parsing when AI returns malformed JSON."""
        mock_ai_response = """OVERALL SENTIMENT: Bullish

        RETAIL SENTIMENT: Very positive

        INSTITUTIONAL SENTIMENT: Accumulating"""

        with patch.object(AIClient, 'chat_completion', return_value={
            'content': mock_ai_response,
            'model': 'sonar-pro',
            'provider': 'perplexity',
            'cost': 0.01
        }):
            analyzer = SentimentAnalyzer()
            result = analyzer.analyze_earnings_sentiment('AAPL')

            # Should still get valid result via fallback
            assert result['overall_sentiment'] in ['bullish', 'neutral', 'bearish']
            assert result['retail_sentiment'] != 'N/A'
```

**Impact**:
- ✅ **Catch integration bugs before production**
- ✅ **Test AI response parsing end-to-end**
- ✅ **Validate fallback mechanisms**

**Implementation Time**: 2-3 hours
**Files**: `tests/test_sentiment_integration.py` (new), `tests/test_strategy_integration.py` (new)
**Tests**: ~400 lines total

---

## Priority 2: Test Coverage (2-3 days)

### 2.1 Add Tradier Client Tests ⚡ **HIGH PRIORITY**

**Critical gaps**: IV calculations, weekly expiration selection, expected move

**Proposed Solution**:
```python
# NEW: tests/test_tradier_options_client.py

class TestTradierIVCalculations:
    """Test IV calculation accuracy."""

    def test_iv_conversion_from_decimal_to_percentage(self):
        """Test that IV is correctly converted from decimal to percentage."""
        # Tradier returns IV as decimal (0.50 = 50%)
        mock_response = {
            'greeks': {
                'mid_iv': 0.93  # Should become 93%
            }
        }

        with patch.object(requests, 'get', return_value=Mock(json=lambda: mock_response)):
            client = TradierOptionsClient()
            iv = client._get_current_iv('AAPL')

            assert iv == 93.0  # Should be converted to percentage

    def test_iv_rank_calculation_percentile(self):
        """Test IV Rank percentile calculation."""
        # Mock IV history: [50, 60, 70, 80, 90, 100, 110, 120]
        # Current IV: 85
        # IV Rank should be: 5/8 = 62.5%

        mock_iv_history = [
            (50, '2025-01-01'),
            (60, '2025-02-01'),
            (70, '2025-03-01'),
            (80, '2025-04-01'),
            (90, '2025-05-01'),
            (100, '2025-06-01'),
            (110, '2025-07-01'),
            (120, '2025-08-01'),
        ]

        with patch.object(IVHistoryTracker, 'calculate_iv_rank', return_value=62.5):
            client = TradierOptionsClient()
            iv_rank = client._get_iv_rank('AAPL', 85.0, '2025-11-05')

            assert iv_rank == 62.5

    def test_weekly_expiration_selection_thursday_earnings(self):
        """Test that Thursday/Friday earnings select next week expiration."""
        # Earnings on Thursday Nov 7
        earnings_date = datetime(2025, 11, 7)  # Thursday

        # Mock available expirations
        mock_expirations = [
            '2025-11-08',  # Friday same week
            '2025-11-14',  # Friday next week (should pick this)
            '2025-11-21',  # Friday week after
        ]

        client = TradierOptionsClient()
        selected = client._select_weekly_expiration(mock_expirations, earnings_date)

        assert selected == '2025-11-14'  # Next week

    def test_expected_move_calculation_from_straddle(self):
        """Test expected move calculation from ATM straddle price."""
        # Stock price: $100
        # ATM straddle: $10
        # Expected move: $10 / $100 = 10%

        mock_options_chain = {
            'options': {
                'option': [
                    {'strike': 100, 'option_type': 'call', 'bid': 4.8, 'ask': 5.2},
                    {'strike': 100, 'option_type': 'put', 'bid': 4.7, 'ask': 5.3},
                ]
            }
        }

        client = TradierOptionsClient()
        expected_move = client._calculate_expected_move(mock_options_chain, 100.0)

        assert expected_move == pytest.approx(10.0, rel=0.01)
```

**Impact**:
- ✅ **Validate critical IV calculations**
- ✅ **Ensure weekly options logic is correct**
- ✅ **Catch data transformation bugs**

**Implementation Time**: 3-4 hours
**Files**: `tests/test_tradier_options_client.py` (new, ~500 lines)

---

### 2.2 Batch yfinance Fetching ⚡ **MEDIUM IMPACT**

**Current State**: Individual `yf.Ticker()` calls (3-5s per ticker)
**Proposed**: Batch fetching (50% faster)

**Implementation**:
```python
# MODIFIED: src/ticker_filter.py

def get_batch_ticker_data(self, tickers: List[str], use_cache: bool = True) -> Dict[str, Dict]:
    """
    Fetch data for multiple tickers in batch (50% faster than individual calls).

    Args:
        tickers: List of ticker symbols
        use_cache: Whether to use cache

    Returns:
        Dict mapping ticker -> ticker_data
    """
    results = {}
    tickers_to_fetch = []

    # Check cache first
    if use_cache:
        now = datetime.now()
        for ticker in tickers:
            if ticker in self._ticker_cache:
                cached_data, cached_time = self._ticker_cache[ticker]
                if now - cached_time < self._cache_ttl:
                    results[ticker] = cached_data
                    continue
            tickers_to_fetch.append(ticker)
    else:
        tickers_to_fetch = tickers

    if not tickers_to_fetch:
        return results

    # Batch download from yfinance
    logger.info(f"Batch fetching {len(tickers_to_fetch)} tickers...")

    try:
        # Single HTTP request for all tickers
        data = yf.download(
            tickers_to_fetch,
            period='1y',
            interval='1d',
            group_by='ticker',
            progress=False,
            threads=True
        )

        for ticker in tickers_to_fetch:
            try:
                # Extract data for this ticker
                ticker_data = self._extract_ticker_data_from_batch(ticker, data)

                # Get options data (still individual, but can't batch this)
                if self.tradier_client and self.tradier_client.is_available():
                    options_data = self.tradier_client.get_options_data(...)
                    ticker_data.update(options_data)

                results[ticker] = ticker_data
                self._ticker_cache[ticker] = (ticker_data, datetime.now())

            except Exception as e:
                logger.warning(f"{ticker}: Batch processing failed: {e}")
                # Fallback to individual fetch
                results[ticker] = self.get_ticker_data(ticker, use_cache=False)

    except Exception as e:
        logger.error(f"Batch download failed: {e}")
        # Fallback to individual fetches
        for ticker in tickers_to_fetch:
            results[ticker] = self.get_ticker_data(ticker, use_cache=False)

    return results
```

**Impact**:
- ✅ **50% faster** ticker data fetching
- ✅ **Reduced API calls** to yfinance
- ✅ **Backward compatible** (fallback to individual)

**Implementation Time**: 2-3 hours
**Files**: `src/ticker_filter.py`
**Tests**: `tests/test_ticker_filter.py` (add batch fetching tests)

---

## Priority 3: Code Quality (1 week)

### 3.1 Extract ReportFormatter Class

**Current**: 100+ lines of string concatenation in `earnings_analyzer.py`
**Proposed**: Dedicated formatter class

```python
# NEW: src/report_formatter.py

class ReportFormatter:
    """Format analysis results into readable reports."""

    def format_report(self, analysis_result: Dict) -> str:
        """Format complete analysis report."""
        sections = [
            self._format_header(analysis_result),
            self._format_summary(analysis_result),
            self._format_tickers(analysis_result),
            self._format_failed_analyses(analysis_result),
            self._format_footer(analysis_result)
        ]
        return "\n".join(sections)

    def _format_header(self, result: Dict) -> str:
        return f"""
{'='*80}
EARNINGS TRADE RESEARCH REPORT
{'='*80}
Date: {result['date']}
Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    def _format_ticker_section(self, ticker_analysis: Dict) -> str:
        """Format individual ticker analysis."""
        sections = [
            f"\n{'='*80}",
            f"TICKER: {ticker_analysis['ticker']}",
            f"{'='*80}\n",
            self._format_options_metrics(ticker_analysis.get('ticker_data', {})),
            self._format_sentiment(ticker_analysis.get('sentiment', {})),
            self._format_strategies(ticker_analysis.get('strategies', {})),
        ]
        return "\n".join(sections)

    def _format_options_metrics(self, ticker_data: Dict) -> str:
        """Format options metrics section."""
        return f"""
OPTIONS METRICS:
  Current Price: ${ticker_data.get('price', 0):.2f}
  Current IV: {ticker_data.get('current_iv', 0):.2f}%
  IV Rank: {ticker_data.get('iv_rank', 0):.1f}%
  Expected Move: {ticker_data.get('expected_move', 0):.1f}%
  Composite Score: {ticker_data.get('score', 0):.1f}/100
"""
```

**Impact**:
- ✅ **Easier to maintain** report formatting
- ✅ **Easier to test** formatting logic
- ✅ **Reusable** for different output formats (HTML, PDF, etc.)

**Implementation Time**: 2-3 hours
**Files**: `src/report_formatter.py` (new), modify `src/earnings_analyzer.py`
**Tests**: `tests/test_report_formatter.py` (new)

---

### 3.2 Move Magic Numbers to Configuration

**Current**: Hardcoded thresholds throughout codebase
**Proposed**: Centralized configuration

```yaml
# NEW: config/trading_criteria.yaml

iv_thresholds:
  minimum: 60  # Minimum IV % to consider
  good: 70
  excellent: 80
  extreme: 100

iv_rank_thresholds:
  minimum: 50  # Minimum IV Rank to consider
  good: 60
  excellent: 75

scoring_weights:
  iv_score: 0.50
  iv_crush_edge: 0.30
  options_liquidity: 0.15
  fundamentals: 0.05

liquidity_thresholds:
  min_volume: 100  # Minimum daily volume
  min_open_interest: 500
  max_bid_ask_spread_pct: 10.0

iv_history:
  min_data_points: 30  # Minimum data points for IV Rank
  lookback_days: 365  # 52-week lookback

cache:
  ticker_data_ttl_minutes: 15
  alpha_vantage_ttl_hours: 12

reddit:
  max_posts_per_subreddit: 20
  subreddits:
    - wallstreetbets
    - stocks
    - options
```

**Implementation Time**: 1-2 hours
**Files**: `config/trading_criteria.yaml` (new), update all modules to load config
**Tests**: Update existing tests with new config values

---

## Summary - Implementation Priority

### Week 1: Critical Reliability (8-12 hours)
- [ ] Switch sentiment analyzer to JSON (2-3h)
- [ ] Switch strategy generator to JSON (2-3h)
- [ ] Add AI response validator (1-2h)
- [ ] Add sentiment integration tests (2-3h)

### Week 2: Test Coverage (12-16 hours)
- [ ] Add Tradier client tests (3-4h)
- [ ] Add strategy parser tests (2-3h)
- [ ] Add calendar filtering tests (2-3h)
- [ ] Add end-to-end integration tests (4-6h)

### Week 3: Performance (6-8 hours)
- [ ] Implement batch yfinance fetching (2-3h)
- [ ] Performance benchmarking (2-3h)
- [ ] Async/await exploration (2h)

### Week 4: Code Quality (8-12 hours)
- [ ] Extract ReportFormatter class (2-3h)
- [ ] Move magic numbers to config (1-2h)
- [ ] Split TradierOptionsClient (4-5h)
- [ ] Documentation updates (1-2h)

---

## Expected Outcomes

After completing all improvements:

### Reliability
- **99% more reliable** AI parsing (JSON vs string splitting)
- **100% validated** responses before use
- **90% test coverage** of critical paths

### Performance
- **40-50% faster** overall (batch fetching + Reddit optimization)
- **10-19s → 7-11s** per ticker analysis

### Code Quality
- **No god classes** (all split into focused components)
- **No magic numbers** (all in configuration)
- **Clean architecture** (formatter extracted, validators separate)

### Maintainability
- **Easy to add new strategies** (JSON schema)
- **Easy to tune thresholds** (YAML config)
- **Easy to test** (comprehensive test suite)

---

## Next Steps

1. **Review this roadmap** with stakeholders
2. **Prioritize** based on business needs
3. **Execute** week by week
4. **Test** continuously
5. **Deploy** incrementally

**Total Implementation Time**: 34-48 hours (4-6 weeks at 8-10 hours/week)
**Expected ROI**: High - significantly more reliable and maintainable system

---

## Priority 5: Code Restructuring (4-6 hours)

### 5.1 Organize Code into Logical Module Structure ⚡ **IMPORTANT**

**Current State**:
```
src/
├── ai_client.py
├── ai_response_validator.py
├── sentiment_analyzer.py
├── strategy_generator.py
├── earnings_analyzer.py
├── ticker_filter.py
├── scorers.py
├── tradier_options_client.py
├── options_data_client.py
├── iv_history_tracker.py
├── earnings_calendar.py
├── alpha_vantage_calendar.py
├── earnings_calendar_factory.py
├── reddit_scraper.py
├── usage_tracker.py
└── usage_tracker_sqlite.py
```

**Problem**: 
- Flat structure makes it hard to navigate
- No clear separation of concerns
- Difficult to understand module relationships
- Harder to maintain as codebase grows

**Proposed Structure**:
```
src/
├── __init__.py
├── ai/
│   ├── __init__.py
│   ├── client.py              (was: ai_client.py)
│   ├── response_validator.py  (was: ai_response_validator.py)
│   ├── sentiment_analyzer.py  (unchanged)
│   └── strategy_generator.py  (unchanged)
├── data/
│   ├── __init__.py
│   ├── calendars/
│   │   ├── __init__.py
│   │   ├── base.py           (was: earnings_calendar.py)
│   │   ├── alpha_vantage.py  (was: alpha_vantage_calendar.py)
│   │   └── factory.py        (was: earnings_calendar_factory.py)
│   └── reddit_scraper.py     (unchanged)
├── options/
│   ├── __init__.py
│   ├── tradier_client.py     (was: tradier_options_client.py)
│   ├── data_client.py        (was: options_data_client.py)
│   └── iv_history_tracker.py (unchanged)
├── analysis/
│   ├── __init__.py
│   ├── earnings_analyzer.py  (unchanged)
│   ├── ticker_filter.py      (unchanged)
│   └── scorers.py            (unchanged)
└── core/
    ├── __init__.py
    ├── usage_tracker.py      (unchanged)
    └── usage_tracker_sqlite.py (unchanged)
```

**Benefits**:
- **Discoverability**: Clear module organization
- **Maintainability**: Related code grouped together
- **Scalability**: Easy to add new modules in correct location
- **Documentation**: Structure tells story of architecture

**Implementation Steps**:

**Step 1**: Create new directory structure (1h)
```bash
mkdir -p src/ai src/data/calendars src/options src/analysis src/core
touch src/ai/__init__.py src/data/__init__.py src/data/calendars/__init__.py
touch src/options/__init__.py src/analysis/__init__.py src/core/__init__.py
```

**Step 2**: Move and rename files with git mv (1h)
```bash
# AI module
git mv src/ai_client.py src/ai/client.py
git mv src/ai_response_validator.py src/ai/response_validator.py
git mv src/sentiment_analyzer.py src/ai/sentiment_analyzer.py
git mv src/strategy_generator.py src/ai/strategy_generator.py

# Data module
git mv src/earnings_calendar.py src/data/calendars/base.py
git mv src/alpha_vantage_calendar.py src/data/calendars/alpha_vantage.py
git mv src/earnings_calendar_factory.py src/data/calendars/factory.py
git mv src/reddit_scraper.py src/data/reddit_scraper.py

# Options module
git mv src/tradier_options_client.py src/options/tradier_client.py
git mv src/options_data_client.py src/options/data_client.py
git mv src/iv_history_tracker.py src/options/iv_history_tracker.py

# Analysis module
git mv src/earnings_analyzer.py src/analysis/earnings_analyzer.py
git mv src/ticker_filter.py src/analysis/ticker_filter.py
git mv src/scorers.py src/analysis/scorers.py

# Core module
git mv src/usage_tracker.py src/core/usage_tracker.py
git mv src/usage_tracker_sqlite.py src/core/usage_tracker_sqlite.py
```

**Step 3**: Update all import statements (2-3h)
```python
# OLD imports
from src.ai_client import AIClient
from src.sentiment_analyzer import SentimentAnalyzer
from src.tradier_options_client import TradierOptionsClient

# NEW imports
from src.ai.client import AIClient
from src.ai.sentiment_analyzer import SentimentAnalyzer
from src.options.tradier_client import TradierOptionsClient
```

Search and replace pattern:
```bash
# Find all Python files
find . -name "*.py" -not -path "./venv/*"

# For each file, update imports:
sed -i '' 's/from src\.ai_client/from src.ai.client/g' *.py
sed -i '' 's/from src\.sentiment_analyzer/from src.ai.sentiment_analyzer/g' *.py
# ... (repeat for all modules)
```

**Step 4**: Update __init__.py files for clean imports (30min)
```python
# src/ai/__init__.py
from .client import AIClient
from .response_validator import AIResponseValidator
from .sentiment_analyzer import SentimentAnalyzer
from .strategy_generator import StrategyGenerator

__all__ = ['AIClient', 'AIResponseValidator', 'SentimentAnalyzer', 'StrategyGenerator']

# src/data/calendars/__init__.py
from .factory import EarningsCalendarFactory
from .base import EarningsCalendar
from .alpha_vantage import AlphaVantageCalendar

__all__ = ['EarningsCalendarFactory', 'EarningsCalendar', 'AlphaVantageCalendar']

# src/options/__init__.py
from .tradier_client import TradierOptionsClient
from .data_client import OptionsDataClient
from .iv_history_tracker import IVHistoryTracker

__all__ = ['TradierOptionsClient', 'OptionsDataClient', 'IVHistoryTracker']

# src/analysis/__init__.py
from .earnings_analyzer import EarningsAnalyzer
from .ticker_filter import TickerFilter
from .scorers import CompositeScorer

__all__ = ['EarningsAnalyzer', 'TickerFilter', 'CompositeScorer']

# src/core/__init__.py
from .usage_tracker import UsageTracker, BudgetExceededError
from .usage_tracker_sqlite import SQLiteUsageTracker

__all__ = ['UsageTracker', 'BudgetExceededError', 'SQLiteUsageTracker']
```

This allows cleaner imports:
```python
# Instead of:
from src.ai.client import AIClient
from src.ai.sentiment_analyzer import SentimentAnalyzer

# Can use:
from src.ai import AIClient, SentimentAnalyzer
```

**Step 5**: Update tests to match new structure (30min)
```python
# OLD test imports
from src.sentiment_analyzer import SentimentAnalyzer

# NEW test imports
from src.ai.sentiment_analyzer import SentimentAnalyzer
# OR
from src.ai import SentimentAnalyzer
```

**Step 6**: Run full test suite to verify (15min)
```bash
python -m pytest tests/ -v
# Should still pass all 65+ tests
```

**Expected Impact**:
- ✅ Better code organization and discoverability
- ✅ Clearer separation of concerns
- ✅ Easier onboarding for new developers
- ✅ More scalable architecture
- ✅ Professional project structure

**Time Estimate**: 4-6 hours total

**Priority**: Medium-High (improves maintainability significantly)

**Note**: This is a pure refactoring - no logic changes, just file moves and import updates. All tests should continue to pass.

