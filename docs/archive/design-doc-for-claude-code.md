# Earnings Options Automation System
## Design Document for Claude Code Implementation

**Project:** Earnings Options MVP with Incremental Feature Addition  
**Date:** October 27, 2025  
**Development Method:** Claude Code (Incremental with Unit Tests)  
**Version Control:** Git (commit after each feature)

---

## EXECUTIVE OVERVIEW

Build a semi-automated earnings options trading system in **4 incremental phases**, each with unit tests and git commits. Start with MVP (Sonar + GPT-5 + Alpha Vantage), then add features based on validation.

**Timeline:** 4 weeks (4-5 hours/week)  
**Approach:** Test-Driven Development with incremental commits  
**Goal:** Production-ready MVP by Week 4, scale features after validation

---

## PROJECT STRUCTURE

```
earnings-options-bot/
├── .git/                          # Git repository
├── .gitignore                     # Ignore patterns
├── README.md                      # Project overview
├── requirements.txt               # Python dependencies
├── .env.example                   # API key template
├── pytest.ini                     # Test configuration
│
├── config/
│   ├── __init__.py
│   ├── models.yaml                # Model configurations
│   ├── data_sources.yaml          # Data source configs
│   └── settings.py                # Runtime settings
│
├── src/
│   ├── __init__.py
│   ├── earnings_scanner.py        # Phase 1: Earnings calendar
│   ├── reddit_scraper.py          # Phase 1: Reddit sentiment
│   ├── sentiment_analyzer.py      # Phase 2: Sonar integration
│   ├── strategy_generator.py      # Phase 2: GPT-5 integration
│   ├── options_pricer.py          # Phase 2: Alpha Vantage
│   ├── position_sizer.py          # Phase 3: Position sizing
│   ├── report_generator.py        # Phase 3: CSV reports
│   ├── trade_logger.py            # Phase 3: Trade tracking
│   ├── api_clients.py             # API wrappers
│   └── utils.py                   # Helper functions
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # Pytest fixtures
│   ├── test_earnings_scanner.py
│   ├── test_reddit_scraper.py
│   ├── test_sentiment_analyzer.py
│   ├── test_strategy_generator.py
│   ├── test_options_pricer.py
│   ├── test_position_sizer.py
│   ├── test_report_generator.py
│   └── test_integration.py
│
├── scripts/
│   ├── setup.py                   # Initial setup script
│   └── run_daily.py               # Daily execution
│
└── main.py                        # Entry point
```

---

## PHASE 1: FOUNDATION (MVP - Week 1)

### Goal
Get basic data collection working with tests. No AI yet.

### Features to Build

#### 1.1 Project Setup
```python
# File: requirements.txt
yfinance==0.2.32
requests==2.31.0
praw==7.7.1
pyyaml==6.0.1
python-dotenv==1.0.0
pytest==7.4.3
pytest-cov==4.1.0
pandas==2.1.3
```

#### 1.2 Earnings Scanner
```python
# File: src/earnings_scanner.py

"""
Earnings calendar scanner using yfinance.
Finds earnings in next N days for specified tickers.
"""

import yfinance as yf
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class EarningsScanner:
    """Scan for upcoming earnings dates."""
    
    def __init__(self, tickers: Optional[List[str]] = None):
        """
        Initialize scanner with ticker list.
        
        Args:
            tickers: List of ticker symbols to monitor
        """
        self.tickers = tickers or self._get_default_tickers()
    
    def _get_default_tickers(self) -> List[str]:
        """Default tickers to monitor."""
        return [
            "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL",
            "AMZN", "META", "NFLX", "AMD", "MSTR",
            "JPM", "BAC", "WFC", "GS", "MS",
            "DIS", "NKE", "SBUX", "MCD", "CMG"
        ]
    
    def get_earnings_candidates(
        self, 
        days_ahead: int = 14,
        min_market_cap: float = 10e9
    ) -> List[Dict]:
        """
        Get tickers with earnings in next N days.
        
        Args:
            days_ahead: Days to look ahead
            min_market_cap: Minimum market cap filter
            
        Returns:
            List of dicts with ticker info
        """
        candidates = []
        today = datetime.now()
        
        for ticker_str in self.tickers:
            try:
                ticker = yf.Ticker(ticker_str)
                info = ticker.info
                
                # Get earnings date
                earnings_date = info.get('earningsDate')
                if not earnings_date:
                    continue
                
                # Check if within range
                if isinstance(earnings_date, list):
                    earnings_date = earnings_date[0]
                
                days_until = (earnings_date - today).days
                
                if 0 <= days_until <= days_ahead:
                    # Get market cap
                    market_cap = info.get('marketCap', 0)
                    
                    if market_cap >= min_market_cap:
                        candidates.append({
                            'ticker': ticker_str,
                            'earnings_date': earnings_date,
                            'days_until': days_until,
                            'market_cap': market_cap,
                            'sector': info.get('sector', 'Unknown'),
                            'industry': info.get('industry', 'Unknown')
                        })
            
            except Exception as e:
                logger.warning(f"Error processing {ticker_str}: {e}")
                continue
        
        # Sort by days until earnings
        return sorted(candidates, key=lambda x: x['days_until'])
    
    def get_earnings_for_ticker(self, ticker: str) -> Optional[Dict]:
        """
        Get earnings info for specific ticker.
        
        Args:
            ticker: Ticker symbol
            
        Returns:
            Dict with earnings info or None
        """
        try:
            t = yf.Ticker(ticker)
            info = t.info
            
            earnings_date = info.get('earningsDate')
            if not earnings_date:
                return None
            
            if isinstance(earnings_date, list):
                earnings_date = earnings_date[0]
            
            return {
                'ticker': ticker,
                'earnings_date': earnings_date,
                'market_cap': info.get('marketCap', 0),
                'sector': info.get('sector', 'Unknown')
            }
        
        except Exception as e:
            logger.error(f"Error getting earnings for {ticker}: {e}")
            return None


# CLI for testing
if __name__ == "__main__":
    scanner = EarningsScanner()
    candidates = scanner.get_earnings_candidates(days_ahead=14)
    
    print(f"\nFound {len(candidates)} earnings in next 14 days:\n")
    for c in candidates:
        print(f"{c['ticker']:6} - {c['earnings_date'].strftime('%Y-%m-%d')} "
              f"({c['days_until']} days) - {c['sector']}")
```

#### 1.3 Unit Tests for Earnings Scanner
```python
# File: tests/test_earnings_scanner.py

"""Unit tests for earnings scanner."""

import pytest
from datetime import datetime, timedelta
from src.earnings_scanner import EarningsScanner


@pytest.fixture
def scanner():
    """Create scanner instance."""
    return EarningsScanner(tickers=["NVDA", "TSLA", "AAPL"])


def test_scanner_initialization():
    """Test scanner initializes with default tickers."""
    scanner = EarningsScanner()
    assert len(scanner.tickers) > 0
    assert isinstance(scanner.tickers, list)


def test_scanner_custom_tickers():
    """Test scanner with custom ticker list."""
    custom_tickers = ["NVDA", "TSLA"]
    scanner = EarningsScanner(tickers=custom_tickers)
    assert scanner.tickers == custom_tickers


def test_get_earnings_candidates(scanner):
    """Test getting earnings candidates."""
    candidates = scanner.get_earnings_candidates(days_ahead=30)
    
    assert isinstance(candidates, list)
    
    if len(candidates) > 0:
        candidate = candidates[0]
        assert 'ticker' in candidate
        assert 'earnings_date' in candidate
        assert 'days_until' in candidate
        assert 'market_cap' in candidate
        assert isinstance(candidate['earnings_date'], datetime)


def test_earnings_sorted_by_date(scanner):
    """Test that earnings are sorted by days_until."""
    candidates = scanner.get_earnings_candidates(days_ahead=30)
    
    if len(candidates) > 1:
        days_until = [c['days_until'] for c in candidates]
        assert days_until == sorted(days_until)


def test_market_cap_filter():
    """Test market cap filtering."""
    scanner = EarningsScanner(tickers=["NVDA"])
    
    # High market cap requirement
    candidates_high = scanner.get_earnings_candidates(
        days_ahead=30,
        min_market_cap=100e9  # 100B
    )
    
    # Low market cap requirement
    candidates_low = scanner.get_earnings_candidates(
        days_ahead=30,
        min_market_cap=1e9  # 1B
    )
    
    # More candidates with lower requirement
    assert len(candidates_low) >= len(candidates_high)


def test_get_earnings_for_ticker(scanner):
    """Test getting earnings for specific ticker."""
    result = scanner.get_earnings_for_ticker("NVDA")
    
    if result:  # May be None if no earnings scheduled
        assert result['ticker'] == "NVDA"
        assert 'earnings_date' in result
        assert 'market_cap' in result


def test_invalid_ticker(scanner):
    """Test handling invalid ticker."""
    result = scanner.get_earnings_for_ticker("INVALID_TICKER_XYZ")
    assert result is None


def test_empty_ticker_list():
    """Test with empty ticker list."""
    scanner = EarningsScanner(tickers=[])
    candidates = scanner.get_earnings_candidates()
    assert candidates == []
```

#### 1.4 Reddit Scraper (Basic)
```python
# File: src/reddit_scraper.py

"""
Reddit sentiment scraper for r/wallstreetbets and related forums.
"""

import praw
from typing import List, Dict
from datetime import datetime, timedelta
import os
import logging

logger = logging.getLogger(__name__)


class RedditScraper:
    """Scrape Reddit for ticker sentiment."""
    
    def __init__(self):
        """Initialize Reddit API client."""
        self.reddit = praw.Reddit(
            client_id=os.getenv('REDDIT_CLIENT_ID'),
            client_secret=os.getenv('REDDIT_CLIENT_SECRET'),
            user_agent='earnings-bot/1.0'
        )
    
    def get_ticker_sentiment(
        self,
        ticker: str,
        subreddits: List[str] = None,
        limit: int = 20
    ) -> Dict:
        """
        Get sentiment for ticker from Reddit.
        
        Args:
            ticker: Ticker symbol
            subreddits: List of subreddits to check
            limit: Max posts to analyze
            
        Returns:
            Dict with sentiment summary
        """
        subreddits = subreddits or ['wallstreetbets', 'stocks', 'options']
        
        posts = []
        for subreddit_name in subreddits:
            try:
                subreddit = self.reddit.subreddit(subreddit_name)
                
                # Search for ticker mentions
                for post in subreddit.search(
                    ticker,
                    time_filter='week',
                    limit=limit
                ):
                    posts.append({
                        'title': post.title,
                        'score': post.score,
                        'num_comments': post.num_comments,
                        'created_utc': datetime.fromtimestamp(post.created_utc),
                        'subreddit': subreddit_name
                    })
            
            except Exception as e:
                logger.warning(f"Error scraping r/{subreddit_name}: {e}")
                continue
        
        # Analyze sentiment (simple scoring)
        if not posts:
            return {
                'ticker': ticker,
                'posts_found': 0,
                'sentiment_score': 0.0,
                'avg_score': 0,
                'total_comments': 0
            }
        
        total_score = sum(p['score'] for p in posts)
        total_comments = sum(p['num_comments'] for p in posts)
        
        # Simple sentiment: positive if avg score > 10
        avg_score = total_score / len(posts)
        sentiment_score = min(max(avg_score / 100, -1.0), 1.0)
        
        return {
            'ticker': ticker,
            'posts_found': len(posts),
            'sentiment_score': sentiment_score,
            'avg_score': avg_score,
            'total_comments': total_comments,
            'top_posts': sorted(posts, key=lambda x: x['score'], reverse=True)[:5]
        }


# CLI for testing
if __name__ == "__main__":
    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment("NVDA")
    
    print(f"\nReddit sentiment for {result['ticker']}:")
    print(f"Posts found: {result['posts_found']}")
    print(f"Sentiment score: {result['sentiment_score']:.2f}")
    print(f"Avg score: {result['avg_score']:.1f}")
    print(f"Total comments: {result['total_comments']}")
```

#### 1.5 Git Commits (Phase 1)
```bash
# After completing earnings scanner
git add src/earnings_scanner.py tests/test_earnings_scanner.py
git commit -m "feat: earnings calendar scanner with unit tests

- Implemented EarningsScanner class
- Filters by days ahead and market cap
- Added comprehensive unit tests
- Test coverage: 95%"

# After Reddit scraper
git add src/reddit_scraper.py requirements.txt
git commit -m "feat: reddit sentiment scraper

- Scrapes r/wallstreetbets, r/stocks, r/options
- Basic sentiment scoring algorithm
- Returns top posts and aggregate metrics"

# After Phase 1 complete
git add .
git commit -m "chore: Phase 1 complete - data collection layer

- Earnings scanner working
- Reddit scraper working
- All tests passing
- Ready for Phase 2 (AI integration)"
```

---

## PHASE 2: AI INTEGRATION (Week 2)

### Goal
Integrate Sonar, GPT-5, and Alpha Vantage with tests.

### Features to Build

#### 2.1 API Client Wrapper
```python
# File: src/api_clients.py

"""API client wrappers for external services."""

import requests
import os
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class PerplexityAPI:
    """Wrapper for Perplexity API (Sonar + GPT-5)."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Perplexity client.
        
        Args:
            api_key: Perplexity API key (or from env)
        """
        self.api_key = api_key or os.getenv('PERPLEXITY_API_KEY')
        self.base_url = 'https://api.perplexity.com/chat/completions'
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
    
    def call_sonar(
        self,
        prompt: str,
        model: str = 'sonar-deep-research',
        timeout: int = 20
    ) -> Dict:
        """
        Call Sonar Deep Research.
        
        Args:
            prompt: User prompt
            model: Model name
            timeout: Timeout in seconds
            
        Returns:
            Response dict
        """
        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.2,
            'max_tokens': 1500
        }
        
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        
        except requests.Timeout:
            logger.error(f"Sonar API timeout after {timeout}s")
            raise
        except requests.HTTPError as e:
            logger.error(f"Sonar API error: {e}")
            raise
    
    def call_gpt5_thinking(
        self,
        prompt: str,
        timeout: int = 15
    ) -> Dict:
        """
        Call GPT-5 Thinking.
        
        Args:
            prompt: User prompt
            timeout: Timeout in seconds
            
        Returns:
            Response dict
        """
        payload = {
            'model': 'gpt-5-thinking',
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.2,
            'max_tokens': 1000
        }
        
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        
        except requests.Timeout:
            logger.error(f"GPT-5 API timeout after {timeout}s")
            raise
        except requests.HTTPError as e:
            logger.error(f"GPT-5 API error: {e}")
            raise


class AlphaVantageAPI:
    """Wrapper for Alpha Vantage options data."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Alpha Vantage client.
        
        Args:
            api_key: Alpha Vantage API key (or from env)
        """
        self.api_key = api_key or os.getenv('ALPHA_VANTAGE_API_KEY')
        self.base_url = 'https://www.alphavantage.co/query'
    
    def get_option_chain(
        self,
        symbol: str,
        expiration: str,
        timeout: int = 10
    ) -> Dict:
        """
        Get options chain for symbol and expiration.
        
        Args:
            symbol: Ticker symbol
            expiration: Expiration date (YYYY-MM-DD)
            timeout: Timeout in seconds
            
        Returns:
            Options chain data
        """
        params = {
            'function': 'OPTION_CHAIN',
            'symbol': symbol,
            'expiration_date': expiration,
            'apikey': self.api_key
        }
        
        try:
            response = requests.get(
                self.base_url,
                params=params,
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        
        except requests.Timeout:
            logger.error(f"Alpha Vantage timeout for {symbol}")
            raise
        except requests.HTTPError as e:
            logger.error(f"Alpha Vantage error: {e}")
            raise
```

#### 2.2 Sentiment Analyzer (Sonar)
```python
# File: src/sentiment_analyzer.py

"""Sentiment analysis using Sonar Deep Research."""

from typing import Dict
from src.api_clients import PerplexityAPI
import logging

logger = logging.getLogger(__name__)


class SentimentAnalyzer:
    """Analyze earnings sentiment using Sonar."""
    
    def __init__(self):
        """Initialize sentiment analyzer."""
        self.perplexity = PerplexityAPI()
    
    def analyze_earnings_sentiment(
        self,
        ticker: str,
        earnings_date: str
    ) -> Dict:
        """
        Analyze sentiment for ticker earnings.
        
        Args:
            ticker: Ticker symbol
            earnings_date: Earnings date string
            
        Returns:
            Sentiment analysis dict
        """
        prompt = f"""
        Analyze {ticker} earnings sentiment (BRIEF):
        
        1. Direction: Bullish or bearish? (score -1 to +1)
        2. Key catalyst: What's driving sentiment? (1 sentence)
        3. Retail vs institutional view: Are they aligned?
        
        Earnings date: {earnings_date}
        
        Keep response under 200 words. Provide specific score.
        """
        
        try:
            response = self.perplexity.call_sonar(prompt)
            content = response['choices'][0]['message']['content']
            
            # Parse response
            sentiment_score = self._extract_sentiment_score(content)
            
            return {
                'ticker': ticker,
                'sentiment_score': sentiment_score,
                'narrative': content,
                'confidence': 0.75,  # Default confidence
                'source': 'sonar'
            }
        
        except Exception as e:
            logger.error(f"Sentiment analysis failed for {ticker}: {e}")
            return {
                'ticker': ticker,
                'sentiment_score': 0.0,
                'narrative': f"Error: {str(e)}",
                'confidence': 0.0,
                'source': 'sonar'
            }
    
    def _extract_sentiment_score(self, content: str) -> float:
        """
        Extract sentiment score from response.
        
        Args:
            content: Response text
            
        Returns:
            Sentiment score (-1 to 1)
        """
        content_lower = content.lower()
        
        # Simple heuristic extraction
        if 'bullish' in content_lower:
            return 0.7
        elif 'bearish' in content_lower:
            return -0.7
        elif 'neutral' in content_lower:
            return 0.0
        
        # Try to find numeric score
        import re
        score_pattern = r'(?:score|sentiment):\s*([-+]?\d*\.?\d+)'
        match = re.search(score_pattern, content_lower)
        
        if match:
            score = float(match.group(1))
            return max(min(score, 1.0), -1.0)
        
        return 0.0  # Default neutral


# CLI for testing
if __name__ == "__main__":
    analyzer = SentimentAnalyzer()
    result = analyzer.analyze_earnings_sentiment("NVDA", "2025-11-15")
    
    print(f"\nSentiment for {result['ticker']}:")
    print(f"Score: {result['sentiment_score']:.2f}")
    print(f"Confidence: {result['confidence']:.0%}")
    print(f"\nNarrative:\n{result['narrative']}")
```

#### 2.3 Strategy Generator (GPT-5)
```python
# File: src/strategy_generator.py

"""Strategy generation using GPT-5 Thinking."""

from typing import Dict
from src.api_clients import PerplexityAPI
import logging

logger = logging.getLogger(__name__)


class StrategyGenerator:
    """Generate options strategy using GPT-5 Thinking."""
    
    def __init__(self):
        """Initialize strategy generator."""
        self.perplexity = PerplexityAPI()
    
    def generate_strategy(
        self,
        ticker: str,
        sentiment: Dict,
        iv_rank: float = None
    ) -> Dict:
        """
        Generate strategy based on sentiment.
        
        Args:
            ticker: Ticker symbol
            sentiment: Sentiment analysis dict
            iv_rank: IV Rank (0-100)
            
        Returns:
            Strategy recommendation dict
        """
        sentiment_score = sentiment.get('sentiment_score', 0.0)
        narrative = sentiment.get('narrative', '')
        
        prompt = f"""
        {ticker} earnings strategy recommendation:
        
        Sentiment score: {sentiment_score:.2f} (-1=bearish, +1=bullish)
        Key info: {narrative[:200]}
        IV Rank: {iv_rank or 'Unknown'}
        
        Recommend:
        1. Strategy type: Bull put spread, bear call spread, iron condor, or calendar?
        2. Short leg delta: What delta range? (e.g., 20-30)
        3. Long leg delta: What delta range? (e.g., 10-15)
        4. Why: Brief reasoning (1-2 sentences)
        
        Format response clearly with each item labeled.
        """
        
        try:
            response = self.perplexity.call_gpt5_thinking(prompt)
            content = response['choices'][0]['message']['content']
            
            # Parse strategy
            strategy = self._parse_strategy(content)
            strategy['ticker'] = ticker
            strategy['input_sentiment'] = sentiment_score
            
            return strategy
        
        except Exception as e:
            logger.error(f"Strategy generation failed for {ticker}: {e}")
            return {
                'ticker': ticker,
                'strategy_type': 'unknown',
                'short_delta': None,
                'long_delta': None,
                'reasoning': f"Error: {str(e)}",
                'confidence': 0.0
            }
    
    def _parse_strategy(self, content: str) -> Dict:
        """
        Parse strategy from GPT-5 response.
        
        Args:
            content: Response text
            
        Returns:
            Parsed strategy dict
        """
        content_lower = content.lower()
        
        # Extract strategy type
        if 'bull put' in content_lower:
            strategy_type = 'bull_put_spread'
        elif 'bear call' in content_lower:
            strategy_type = 'bear_call_spread'
        elif 'iron condor' in content_lower:
            strategy_type = 'iron_condor'
        elif 'calendar' in content_lower:
            strategy_type = 'calendar_spread'
        else:
            strategy_type = 'unknown'
        
        # Extract deltas (simple regex)
        import re
        
        short_delta_pattern = r'short.*?(\d+)\s*-?\s*(\d+)?.*?delta'
        long_delta_pattern = r'long.*?(\d+)\s*-?\s*(\d+)?.*?delta'
        
        short_match = re.search(short_delta_pattern, content_lower)
        long_match = re.search(long_delta_pattern, content_lower)
        
        short_delta = None
        if short_match:
            short_delta = int(short_match.group(1))
        
        long_delta = None
        if long_match:
            long_delta = int(long_match.group(1))
        
        return {
            'strategy_type': strategy_type,
            'short_delta': short_delta,
            'long_delta': long_delta,
            'reasoning': content,
            'confidence': 0.8  # Default
        }


# CLI for testing
if __name__ == "__main__":
    from src.sentiment_analyzer import SentimentAnalyzer
    
    # Get sentiment first
    analyzer = SentimentAnalyzer()
    sentiment = analyzer.analyze_earnings_sentiment("NVDA", "2025-11-15")
    
    # Generate strategy
    generator = StrategyGenerator()
    strategy = generator.generate_strategy("NVDA", sentiment, iv_rank=82)
    
    print(f"\nStrategy for {strategy['ticker']}:")
    print(f"Type: {strategy['strategy_type']}")
    print(f"Short delta: {strategy['short_delta']}")
    print(f"Long delta: {strategy['long_delta']}")
    print(f"\nReasoning:\n{strategy['reasoning']}")
```

#### 2.4 Unit Tests (Phase 2)
```python
# File: tests/test_api_clients.py

"""Unit tests for API clients."""

import pytest
from src.api_clients import PerplexityAPI, AlphaVantageAPI
from unittest.mock import patch, Mock


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv('PERPLEXITY_API_KEY', 'test_key_123')
    monkeypatch.setenv('ALPHA_VANTAGE_API_KEY', 'test_av_key_456')


def test_perplexity_init(mock_env):
    """Test Perplexity client initialization."""
    client = PerplexityAPI()
    assert client.api_key == 'test_key_123'


def test_alphavantage_init(mock_env):
    """Test Alpha Vantage client initialization."""
    client = AlphaVantageAPI()
    assert client.api_key == 'test_av_key_456'


@patch('src.api_clients.requests.post')
def test_sonar_call_success(mock_post, mock_env):
    """Test successful Sonar API call."""
    mock_response = Mock()
    mock_response.json.return_value = {
        'choices': [{'message': {'content': 'Bullish sentiment'}}]
    }
    mock_post.return_value = mock_response
    
    client = PerplexityAPI()
    result = client.call_sonar("Test prompt")
    
    assert 'choices' in result
    mock_post.assert_called_once()


@patch('src.api_clients.requests.post')
def test_gpt5_call_timeout(mock_post, mock_env):
    """Test GPT-5 API timeout handling."""
    mock_post.side_effect = requests.Timeout()
    
    client = PerplexityAPI()
    
    with pytest.raises(requests.Timeout):
        client.call_gpt5_thinking("Test prompt")


# File: tests/test_sentiment_analyzer.py

"""Unit tests for sentiment analyzer."""

import pytest
from src.sentiment_analyzer import SentimentAnalyzer
from unittest.mock import patch, Mock


@pytest.fixture
def analyzer():
    """Create analyzer instance."""
    return SentimentAnalyzer()


def test_extract_sentiment_score(analyzer):
    """Test sentiment score extraction."""
    bullish_text = "The sentiment is strongly bullish"
    assert analyzer._extract_sentiment_score(bullish_text) == 0.7
    
    bearish_text = "The outlook is bearish"
    assert analyzer._extract_sentiment_score(bearish_text) == -0.7
    
    neutral_text = "The sentiment is neutral"
    assert analyzer._extract_sentiment_score(neutral_text) == 0.0


@patch('src.api_clients.PerplexityAPI.call_sonar')
def test_analyze_sentiment_success(mock_sonar, analyzer):
    """Test successful sentiment analysis."""
    mock_sonar.return_value = {
        'choices': [{'message': {'content': 'Bullish sentiment, score: 0.8'}}]
    }
    
    result = analyzer.analyze_earnings_sentiment("NVDA", "2025-11-15")
    
    assert result['ticker'] == "NVDA"
    assert 'sentiment_score' in result
    assert 'narrative' in result
    assert result['source'] == 'sonar'


@patch('src.api_clients.PerplexityAPI.call_sonar')
def test_analyze_sentiment_error(mock_sonar, analyzer):
    """Test sentiment analysis error handling."""
    mock_sonar.side_effect = Exception("API error")
    
    result = analyzer.analyze_earnings_sentiment("NVDA", "2025-11-15")
    
    assert result['sentiment_score'] == 0.0
    assert result['confidence'] == 0.0
    assert 'Error' in result['narrative']
```

#### 2.5 Git Commits (Phase 2)
```bash
git add src/api_clients.py tests/test_api_clients.py
git commit -m "feat: API client wrappers with tests

- Perplexity API client (Sonar + GPT-5)
- Alpha Vantage API client
- Unit tests with mocking
- Error handling and timeouts"

git add src/sentiment_analyzer.py tests/test_sentiment_analyzer.py
git commit -m "feat: sentiment analyzer with Sonar

- Analyzes earnings sentiment
- Extracts score and narrative
- Unit tests with mocked API calls"

git add src/strategy_generator.py tests/test_strategy_generator.py
git commit -m "feat: strategy generator with GPT-5

- Generates strategy recommendations
- Parses delta ranges
- Unit tests included"

git add .
git commit -m "chore: Phase 2 complete - AI integration

- Sonar sentiment working
- GPT-5 strategy working
- Alpha Vantage options pricing ready
- All tests passing
- Ready for Phase 3 (reports)"
```

---

## PHASE 3: REPORTS & EXECUTION (Week 3)

### Goal
Generate CSV reports, position sizing, and trade logging.

### Features to Build

#### 3.1 Position Sizer
```python
# File: src/position_sizer.py

"""Position sizing calculator."""

from typing import Dict
import logging

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculate position sizes based on confidence."""
    
    def __init__(
        self,
        base_risk: float = 20000,
        min_position: float = 5000,
        max_position: float = 30000
    ):
        """
        Initialize position sizer.
        
        Args:
            base_risk: Base risk amount
            min_position: Minimum position size
            max_position: Maximum position size
        """
        self.base_risk = base_risk
        self.min_position = min_position
        self.max_position = max_position
    
    def calculate_position_size(
        self,
        sentiment_confidence: float,
        strategy_confidence: float,
        sentiment_weight: float = 0.45,
        strategy_weight: float = 0.40
    ) -> Dict:
        """
        Calculate position size.
        
        Args:
            sentiment_confidence: Sentiment confidence (0-1)
            strategy_confidence: Strategy confidence (0-1)
            sentiment_weight: Weight for sentiment
            strategy_weight: Weight for strategy
            
        Returns:
            Position sizing dict
        """
        # Weighted average confidence
        combined_confidence = (
            sentiment_confidence * sentiment_weight +
            strategy_confidence * strategy_weight
        )
        
        # Apply to base risk
        raw_position = self.base_risk * combined_confidence
        
        # Apply min/max constraints
        final_position = max(
            self.min_position,
            min(self.max_position, raw_position)
        )
        
        return {
            'combined_confidence': combined_confidence,
            'raw_position': raw_position,
            'final_position': final_position,
            'contracts': int(final_position / 100),  # Rough estimate
            'sentiment_weight': sentiment_weight,
            'strategy_weight': strategy_weight
        }


# CLI for testing
if __name__ == "__main__":
    sizer = PositionSizer()
    
    # Test different confidence levels
    test_cases = [
        (0.75, 0.80, "High confidence"),
        (0.50, 0.60, "Medium confidence"),
        (0.30, 0.40, "Low confidence")
    ]
    
    for sent_conf, strat_conf, label in test_cases:
        result = sizer.calculate_position_size(sent_conf, strat_conf)
        print(f"\n{label}:")
        print(f"  Combined confidence: {result['combined_confidence']:.0%}")
        print(f"  Position size: ${result['final_position']:,.0f}")
        print(f"  Contracts: {result['contracts']}")
```

#### 3.2 Report Generator
```python
# File: src/report_generator.py

"""CSV report generator."""

import csv
from typing import List, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate daily CSV reports."""
    
    def __init__(self, output_dir: str = '.'):
        """
        Initialize report generator.
        
        Args:
            output_dir: Output directory for CSV files
        """
        self.output_dir = output_dir
    
    def generate_daily_report(
        self,
        recommendations: List[Dict],
        filename: str = None
    ) -> str:
        """
        Generate daily CSV report.
        
        Args:
            recommendations: List of recommendation dicts
            filename: Output filename (auto-generated if None)
            
        Returns:
            Path to generated file
        """
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d')
            filename = f'{self.output_dir}/daily_report_{timestamp}.csv'
        
        headers = [
            'Ticker',
            'Earnings Date',
            'Sentiment Score',
            'Strategy Type',
            'Short Delta',
            'Long Delta',
            'Position Size',
            'Confidence',
            'Execute?'
        ]
        
        try:
            with open(filename, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                
                for rec in recommendations:
                    writer.writerow({
                        'Ticker': rec.get('ticker', ''),
                        'Earnings Date': rec.get('earnings_date', ''),
                        'Sentiment Score': f"{rec.get('sentiment_score', 0):.2f}",
                        'Strategy Type': rec.get('strategy_type', ''),
                        'Short Delta': rec.get('short_delta', ''),
                        'Long Delta': rec.get('long_delta', ''),
                        'Position Size': f"${rec.get('position_size', 0):,.0f}",
                        'Confidence': f"{rec.get('confidence', 0):.0%}",
                        'Execute?': ''  # Manual entry
                    })
            
            logger.info(f"Generated report: {filename}")
            return filename
        
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            raise


# CLI for testing
if __name__ == "__main__":
    generator = ReportGenerator()
    
    # Sample data
    sample_recs = [
        {
            'ticker': 'NVDA',
            'earnings_date': '2025-11-15',
            'sentiment_score': 0.75,
            'strategy_type': 'bull_put_spread',
            'short_delta': 25,
            'long_delta': 15,
            'position_size': 18000,
            'confidence': 0.80
        },
        {
            'ticker': 'TSLA',
            'earnings_date': '2025-11-20',
            'sentiment_score': 0.60,
            'strategy_type': 'iron_condor',
            'short_delta': 20,
            'long_delta': 10,
            'position_size': 15000,
            'confidence': 0.65
        }
    ]
    
    filename = generator.generate_daily_report(sample_recs)
    print(f"\nGenerated: {filename}")
```

#### 3.3 Unit Tests (Phase 3)
```python
# File: tests/test_position_sizer.py

"""Unit tests for position sizer."""

import pytest
from src.position_sizer import PositionSizer


@pytest.fixture
def sizer():
    """Create sizer instance."""
    return PositionSizer()


def test_position_sizer_initialization():
    """Test sizer initializes with defaults."""
    sizer = PositionSizer()
    assert sizer.base_risk == 20000
    assert sizer.min_position == 5000
    assert sizer.max_position == 30000


def test_custom_initialization():
    """Test sizer with custom parameters."""
    sizer = PositionSizer(
        base_risk=30000,
        min_position=10000,
        max_position=50000
    )
    assert sizer.base_risk == 30000


def test_high_confidence_sizing(sizer):
    """Test sizing with high confidence."""
    result = sizer.calculate_position_size(
        sentiment_confidence=0.90,
        strategy_confidence=0.85
    )
    
    assert result['combined_confidence'] > 0.8
    assert result['final_position'] >= sizer.base_risk * 0.8


def test_low_confidence_sizing(sizer):
    """Test sizing with low confidence."""
    result = sizer.calculate_position_size(
        sentiment_confidence=0.30,
        strategy_confidence=0.25
    )
    
    assert result['combined_confidence'] < 0.5
    # Should hit minimum
    assert result['final_position'] == sizer.min_position


def test_max_position_constraint(sizer):
    """Test maximum position constraint."""
    result = sizer.calculate_position_size(
        sentiment_confidence=1.0,
        strategy_confidence=1.0
    )
    
    # Should be capped at max
    assert result['final_position'] <= sizer.max_position


def test_contracts_calculation(sizer):
    """Test contracts calculation."""
    result = sizer.calculate_position_size(0.75, 0.80)
    
    expected_contracts = int(result['final_position'] / 100)
    assert result['contracts'] == expected_contracts


# File: tests/test_report_generator.py

"""Unit tests for report generator."""

import pytest
import os
import csv
from src.report_generator import ReportGenerator


@pytest.fixture
def generator(tmp_path):
    """Create generator with temp directory."""
    return ReportGenerator(output_dir=str(tmp_path))


def test_report_generator_init():
    """Test generator initialization."""
    gen = ReportGenerator()
    assert gen.output_dir == '.'


def test_generate_daily_report(generator, tmp_path):
    """Test generating daily report."""
    sample_recs = [
        {
            'ticker': 'NVDA',
            'earnings_date': '2025-11-15',
            'sentiment_score': 0.75,
            'strategy_type': 'bull_put_spread',
            'short_delta': 25,
            'long_delta': 15,
            'position_size': 18000,
            'confidence': 0.80
        }
    ]
    
    filename = generator.generate_daily_report(sample_recs)
    
    assert os.path.exists(filename)
    
    # Read and validate
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        
        assert len(rows) == 1
        assert rows[0]['Ticker'] == 'NVDA'
        assert 'NVDA' in rows[0]['Ticker']


def test_empty_recommendations(generator):
    """Test generating report with no recommendations."""
    filename = generator.generate_daily_report([])
    
    assert os.path.exists(filename)
    
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 0
```

#### 3.4 Git Commits (Phase 3)
```bash
git add src/position_sizer.py tests/test_position_sizer.py
git commit -m "feat: position sizer with confidence weighting

- Weighted average of sentiment + strategy confidence
- Min/max constraints
- Contract count calculation
- Comprehensive unit tests"

git add src/report_generator.py tests/test_report_generator.py
git commit -m "feat: CSV report generator

- Daily report generation
- CSV format for Excel review
- Unit tests with temp files"

git add .
git commit -m "chore: Phase 3 complete - reports and logging

- Position sizing working
- CSV reports generating
- Ready for Phase 4 (deployment)"
```

---

## PHASE 4: DEPLOYMENT & INTEGRATION (Week 4)

### Goal
Create main entry point, deployment script, and documentation.

### Features to Build

#### 4.1 Main Entry Point
```python
# File: main.py

"""
Main entry point for earnings options automation system.
"""

import os
import logging
from datetime import datetime
from typing import List, Dict

from src.earnings_scanner import EarningsScanner
from src.reddit_scraper import RedditScraper
from src.sentiment_analyzer import SentimentAnalyzer
from src.strategy_generator import StrategyGenerator
from src.position_sizer import PositionSizer
from src.report_generator import ReportGenerator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Run daily earnings analysis."""
    
    logger.info("Starting earnings analysis...")
    
    try:
        # Phase 1: Get earnings candidates
        logger.info("Phase 1: Scanning for earnings...")
        scanner = EarningsScanner()
        candidates = scanner.get_earnings_candidates(days_ahead=14)
        logger.info(f"Found {len(candidates)} earnings candidates")
        
        if not candidates:
            logger.info("No earnings found. Exiting.")
            return
        
        # Phase 2: Analyze each candidate
        logger.info("Phase 2: Analyzing candidates...")
        analyzer = SentimentAnalyzer()
        generator = StrategyGenerator()
        sizer = PositionSizer()
        
        recommendations = []
        
        for candidate in candidates[:10]:  # Limit to 10 per day
            ticker = candidate['ticker']
            earnings_date = candidate['earnings_date'].strftime('%Y-%m-%d')
            
            logger.info(f"Analyzing {ticker}...")
            
            try:
                # Get sentiment
                sentiment = analyzer.analyze_earnings_sentiment(
                    ticker,
                    earnings_date
                )
                
                # Generate strategy
                strategy = generator.generate_strategy(
                    ticker,
                    sentiment
                )
                
                # Calculate position size
                position = sizer.calculate_position_size(
                    sentiment_confidence=sentiment['confidence'],
                    strategy_confidence=strategy['confidence']
                )
                
                # Combine results
                recommendations.append({
                    'ticker': ticker,
                    'earnings_date': earnings_date,
                    'sentiment_score': sentiment['sentiment_score'],
                    'strategy_type': strategy['strategy_type'],
                    'short_delta': strategy['short_delta'],
                    'long_delta': strategy['long_delta'],
                    'position_size': position['final_position'],
                    'confidence': position['combined_confidence']
                })
                
                logger.info(f"✓ {ticker} analyzed successfully")
            
            except Exception as e:
                logger.error(f"Error analyzing {ticker}: {e}")
                continue
        
        # Phase 3: Generate report
        logger.info("Phase 3: Generating report...")
        report_gen = ReportGenerator(output_dir='./data')
        
        os.makedirs('./data', exist_ok=True)
        filename = report_gen.generate_daily_report(recommendations)
        
        logger.info(f"✓ Report generated: {filename}")
        logger.info(f"✓ {len(recommendations)} recommendations ready for review")
        
        print(f"\n{'='*60}")
        print(f"Daily Report Generated: {filename}")
        print(f"Recommendations: {len(recommendations)}")
        print(f"{'='*60}\n")
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
```

#### 4.2 Deployment Script
```python
# File: scripts/run_daily.py

"""
Daily execution script.
Can be scheduled via cron or Task Scheduler.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import main

if __name__ == "__main__":
    main()
```

#### 4.3 Integration Test
```python
# File: tests/test_integration.py

"""End-to-end integration tests."""

import pytest
from unittest.mock import patch, Mock
from main import main


@patch('src.earnings_scanner.EarningsScanner.get_earnings_candidates')
@patch('src.sentiment_analyzer.SentimentAnalyzer.analyze_earnings_sentiment')
@patch('src.strategy_generator.StrategyGenerator.generate_strategy')
def test_main_flow(mock_strategy, mock_sentiment, mock_scanner, tmp_path):
    """Test complete flow from earnings to report."""
    
    # Mock earnings
    mock_scanner.return_value = [{
        'ticker': 'NVDA',
        'earnings_date': datetime.now() + timedelta(days=5),
        'market_cap': 100e9,
        'sector': 'Technology'
    }]
    
    # Mock sentiment
    mock_sentiment.return_value = {
        'ticker': 'NVDA',
        'sentiment_score': 0.75,
        'confidence': 0.80,
        'narrative': 'Bullish'
    }
    
    # Mock strategy
    mock_strategy.return_value = {
        'ticker': 'NVDA',
        'strategy_type': 'bull_put_spread',
        'short_delta': 25,
        'long_delta': 15,
        'confidence': 0.80
    }
    
    # Run main
    main()
    
    # Verify report exists
    import glob
    reports = glob.glob('./data/daily_report_*.csv')
    assert len(reports) > 0
```

#### 4.4 Documentation
```markdown
# File: docs/SETUP.md

# Setup Guide

## Prerequisites

- Python 3.9+
- pip
- Git

## Installation

1. Clone repository:
```bash
git clone <your-repo-url>
cd earnings-options-bot
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure API keys:
```bash
cp .env.example .env
# Edit .env and add your API keys
```

## API Keys Required

- Perplexity API: Get from https://www.perplexity.ai/api
- Alpha Vantage: Get from https://www.alphavantage.co/api/
- Reddit API: Get from https://www.reddit.com/prefs/apps

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src tests/

# Run specific test file
pytest tests/test_earnings_scanner.py
```

## Usage

```bash
# Run daily analysis
python main.py

# Output will be in ./data/daily_report_YYYYMMDD.csv
```
```

#### 4.5 Git Commits (Phase 4)
```bash
git add main.py scripts/run_daily.py
git commit -m "feat: main entry point and daily runner

- Complete integration of all components
- Daily analysis workflow
- Error handling and logging"

git add tests/test_integration.py
git commit -m "test: end-to-end integration tests

- Mock-based integration test
- Validates full pipeline"

git add docs/
git commit -m "docs: setup and usage documentation

- Installation guide
- API key setup
- Testing instructions
- Usage examples"

git add .
git commit -m "chore: Phase 4 complete - MVP ready for production

- All phases integrated
- Unit tests: 95%+ coverage
- Documentation complete
- Ready for Week 5 deployment
- 18 commits total"

git tag -a v1.0.0-mvp -m "MVP Release - Week 4 Complete"
```

---

## FUTURE ENHANCEMENTS (Month 2+)

### Month 2: Add Grok + Reddit Integration
```python
# After MVP validation, add:
# - Grok 4 real-time sentiment (10% weight)
# - Enhanced Reddit analysis (5% weight)
# - Update ensemble weights
```

### Month 3: Add Walk-Forward Validation
```python
# Add historical backtesting
# Validate edge exists with real data
```

### Month 6: Add Bayesian Confidence
```python
# After collecting 50-100 trades
# Implement Bayesian with real priors
# Dynamic position sizing
```

---

## TESTING STRATEGY

### Unit Tests
- Test each module independently
- Mock external API calls
- Aim for 90%+ coverage

### Integration Tests
- Test end-to-end flow
- Use mock data
- Validate output format

### Manual Testing
- Run on real earnings data
- Review CSV outputs
- Verify calculations

---

## GIT WORKFLOW SUMMARY

```
Total commits by end: 16-18

Phase 1 (Week 1): 3-4 commits
├─ feat: earnings scanner
├─ feat: reddit scraper
└─ chore: Phase 1 complete

Phase 2 (Week 2): 4-5 commits
├─ feat: API clients
├─ feat: sentiment analyzer
├─ feat: strategy generator
├─ feat: options pricer
└─ chore: Phase 2 complete

Phase 3 (Week 3): 3-4 commits
├─ feat: position sizer
├─ feat: report generator
├─ feat: trade logger
└─ chore: Phase 3 complete

Phase 4 (Week 4): 4-5 commits
├─ feat: main entry point
├─ test: integration tests
├─ docs: documentation
├─ chore: Phase 4 complete
└─ tag: v1.0.0-mvp

Clean git history with:
- Descriptive commit messages
- Tagged releases
- Logical progression
```

---

## SUCCESS METRICS

### Week 1
- ✅ Earnings scanner working
- ✅ Reddit scraper working
- ✅ Unit tests passing

### Week 2
- ✅ Sonar integration complete
- ✅ GPT-5 integration complete
- ✅ Alpha Vantage working

### Week 3
- ✅ CSV reports generating
- ✅ Position sizing working
- ✅ All tests passing

### Week 4
- ✅ End-to-end flow working
- ✅ Documentation complete
- ✅ Ready for production
- ✅ First live trades possible

---

## EXECUTION PLAN

### This Week (Week 1)
1. Set up git repository
2. Build earnings scanner
3. Build reddit scraper
4. Write unit tests
5. Commit to git

### Next Steps
- Week 2: AI integration
- Week 3: Reports & logging
- Week 4: Production deployment
- Week 5: Live trading begins

**You have everything needed to build this incrementally with Claude Code. Start Week 1 now.**
