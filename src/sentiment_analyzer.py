"""
Perplexity Sonar sentiment analyzer for earnings trades.

Analyzes sentiment based on Trading Research Prompt.pdf criteria:
- Overall sentiment: Retail vs Institutional vs Hedge Fund
- Key headwinds and tailwinds
- Recent news, guidance history, unusual options flow
- Dark pool activity and positioning
"""

import os
import requests
import logging
from typing import Dict, Optional
from dotenv import load_dotenv
from src.usage_tracker import UsageTracker

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when API budget is exceeded."""
    pass


class SentimentAnalyzer:
    """Perplexity Sonar API client for sentiment analysis."""

    def __init__(self, model: str = "sonar-pro", usage_tracker: Optional[UsageTracker] = None):
        """
        Initialize sentiment analyzer.

        Args:
            model: Perplexity model to use
                   - "sonar-pro": Fast, cheap ($5/1M tokens) - default
                   - "sonar-deep-research": Expensive (~$0.75/call) - manual only
            usage_tracker: Optional UsageTracker instance for cost control
        """
        self.api_key = os.getenv('PERPLEXITY_API_KEY')
        if not self.api_key:
            raise ValueError("PERPLEXITY_API_KEY not found in environment")

        self.model = model
        self.api_url = "https://api.perplexity.ai/chat/completions"
        self.calls_made = 0

        # Initialize usage tracker for cost control
        self.usage_tracker = usage_tracker or UsageTracker()

    def analyze_earnings_sentiment(self, ticker: str, earnings_date: Optional[str] = None) -> Dict:
        """
        Analyze earnings sentiment for a ticker.

        Based on Trading Research Prompt.pdf criteria:
        1. Overall sentiment: Retail vs Institutional vs Hedge Fund
        2. Key headwinds and tailwinds
        3. Recent news, guidance history, macro factors
        4. Unusual options flow and dark pool activity

        Args:
            ticker: Ticker symbol
            earnings_date: Optional earnings date (YYYY-MM-DD)

        Returns:
            Dict with:
            - overall_sentiment: "bullish", "neutral", "bearish"
            - retail_sentiment: Analysis of retail positioning
            - institutional_sentiment: Analysis of institutional positioning
            - hedge_fund_sentiment: Analysis of hedge fund positioning
            - headwinds: List of negative factors
            - tailwinds: List of positive factors
            - unusual_activity: Options flow, dark pool activity
            - guidance_history: Recent guidance and earnings results
            - confidence: Low/Medium/High based on data quality
        """
        logger.info(f"Analyzing sentiment for {ticker}...")

        try:
            # Construct prompt based on your criteria
            prompt = self._build_sentiment_prompt(ticker, earnings_date)

            # Call Perplexity API with budget tracking
            response = self._make_request(prompt, ticker=ticker)

            # Parse response into structured format
            result = self._parse_sentiment_response(response, ticker)

            logger.info(f"{ticker}: Overall sentiment = {result.get('overall_sentiment', 'unknown')}")

            return result

        except Exception as e:
            logger.error(f"Error analyzing sentiment for {ticker}: {e}")
            return self._get_empty_result(ticker)

    def _build_sentiment_prompt(self, ticker: str, earnings_date: Optional[str]) -> str:
        """
        Build sentiment analysis prompt based on user's criteria.

        Args:
            ticker: Ticker symbol
            earnings_date: Optional earnings date

        Returns:
            Prompt string for Perplexity
        """
        earnings_context = f" with earnings on {earnings_date}" if earnings_date else " heading into upcoming earnings"

        prompt = f"""Analyze the earnings sentiment for {ticker}{earnings_context}.

Structure your response EXACTLY as follows:

OVERALL SENTIMENT: [Bullish/Neutral/Bearish] - [1-2 sentence summary]

RETAIL SENTIMENT:
[Analysis of retail trader positioning and sentiment]

INSTITUTIONAL SENTIMENT:
[Analysis of institutional investor positioning and sentiment]

HEDGE FUND SENTIMENT:
[Analysis of hedge fund positioning and sentiment]

KEY TAILWINDS:
- [Positive factor 1]
- [Positive factor 2]
- [etc.]

KEY HEADWINDS:
- [Negative factor 1]
- [Negative factor 2]
- [etc.]

UNUSUAL ACTIVITY:
[Any unusual options flow, dark pool activity, or notable positioning changes]

GUIDANCE HISTORY:
[Recent earnings results, guidance beats/misses, and management commentary]

MACRO & SECTOR FACTORS:
[Relevant macro trends and sector-specific impacts]

Focus on actionable intelligence for an options trader looking to sell premium (IV crush strategy). Keep response under 500 words total."""

        return prompt

    def _make_request(self, prompt: str, ticker: Optional[str] = None) -> str:
        """
        Make API request to Perplexity with budget checking.

        Args:
            prompt: Prompt string
            ticker: Ticker symbol (for logging)

        Returns:
            Response text

        Raises:
            BudgetExceededError: If budget limit would be exceeded
            Exception: If request fails
        """
        # Estimate tokens (rough: ~4 chars per token)
        estimated_tokens = len(prompt) / 4 + 1500  # prompt + max_tokens response

        # Check budget BEFORE making call
        can_call, reason = self.usage_tracker.can_make_call(self.model, estimated_tokens)
        if not can_call:
            logger.error(f"Budget check failed: {reason}")
            raise BudgetExceededError(reason)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional options trading analyst specializing in earnings plays and sentiment analysis."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.2,  # Low temperature for consistent, factual analysis
            "max_tokens": 1500
        }

        success = False
        tokens_used = 0
        cost = 0.0

        try:
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()

            data = response.json()

            # Get actual token usage from response
            if 'usage' in data:
                tokens_used = data['usage'].get('total_tokens', estimated_tokens)
            else:
                tokens_used = estimated_tokens  # Fallback to estimate

            # Calculate cost
            cost_per_1k = self.usage_tracker.config['models'][self.model]['cost_per_1k_tokens']
            cost = (tokens_used / 1000) * cost_per_1k

            success = True
            self.calls_made += 1

            # Log usage AFTER successful call
            self.usage_tracker.log_api_call(self.model, tokens_used, cost, ticker, success=True)

            return data['choices'][0]['message']['content']

        except requests.exceptions.RequestException as e:
            logger.error(f"Perplexity API request failed: {e}")
            # Log failed call with estimated cost
            if estimated_tokens > 0:
                cost_per_1k = self.usage_tracker.config['models'][self.model]['cost_per_1k_tokens']
                cost = (estimated_tokens / 1000) * cost_per_1k
                self.usage_tracker.log_api_call(self.model, estimated_tokens, cost, ticker, success=False)
            raise

    def _parse_sentiment_response(self, response: str, ticker: str) -> Dict:
        """
        Parse Perplexity response into structured format.

        Args:
            response: Raw text response from Perplexity
            ticker: Ticker symbol

        Returns:
            Structured sentiment dict
        """
        try:
            # Extract overall sentiment
            overall_sentiment = "neutral"
            if "OVERALL SENTIMENT:" in response:
                sentiment_line = response.split("OVERALL SENTIMENT:")[1].split("\n")[0].lower()
                if "bullish" in sentiment_line:
                    overall_sentiment = "bullish"
                elif "bearish" in sentiment_line:
                    overall_sentiment = "bearish"

            # Extract sections
            result = {
                'ticker': ticker,
                'overall_sentiment': overall_sentiment,
                'retail_sentiment': self._extract_section(response, "RETAIL SENTIMENT:", "INSTITUTIONAL SENTIMENT:"),
                'institutional_sentiment': self._extract_section(response, "INSTITUTIONAL SENTIMENT:", "HEDGE FUND SENTIMENT:"),
                'hedge_fund_sentiment': self._extract_section(response, "HEDGE FUND SENTIMENT:", "KEY TAILWINDS:"),
                'tailwinds': self._extract_list(response, "KEY TAILWINDS:", "KEY HEADWINDS:"),
                'headwinds': self._extract_list(response, "KEY HEADWINDS:", "UNUSUAL ACTIVITY:"),
                'unusual_activity': self._extract_section(response, "UNUSUAL ACTIVITY:", "GUIDANCE HISTORY:"),
                'guidance_history': self._extract_section(response, "GUIDANCE HISTORY:", "MACRO & SECTOR FACTORS:"),
                'macro_sector': self._extract_section(response, "MACRO & SECTOR FACTORS:", None),
                'raw_response': response,  # Keep full response for reference
                'confidence': 'medium'  # Default to medium confidence
            }

            return result

        except Exception as e:
            logger.error(f"Error parsing sentiment response: {e}")
            return self._get_empty_result(ticker)

    def _extract_section(self, text: str, start_marker: str, end_marker: Optional[str]) -> str:
        """Extract text between two markers."""
        try:
            if start_marker not in text:
                return "N/A"

            start = text.find(start_marker) + len(start_marker)
            if end_marker and end_marker in text:
                end = text.find(end_marker, start)
                return text[start:end].strip()
            else:
                return text[start:].strip()
        except:
            return "N/A"

    def _extract_list(self, text: str, start_marker: str, end_marker: str) -> list:
        """Extract bullet point list between markers."""
        try:
            section = self._extract_section(text, start_marker, end_marker)
            if section == "N/A":
                return []

            # Extract lines starting with - or *
            lines = section.split('\n')
            items = []
            for line in lines:
                line = line.strip()
                if line.startswith('-') or line.startswith('*'):
                    items.append(line.lstrip('-*').strip())

            return items
        except:
            return []

    def _get_empty_result(self, ticker: str) -> Dict:
        """Return empty result structure."""
        return {
            'ticker': ticker,
            'overall_sentiment': 'unknown',
            'retail_sentiment': 'N/A',
            'institutional_sentiment': 'N/A',
            'hedge_fund_sentiment': 'N/A',
            'tailwinds': [],
            'headwinds': [],
            'unusual_activity': 'N/A',
            'guidance_history': 'N/A',
            'macro_sector': 'N/A',
            'raw_response': '',
            'confidence': 'low'
        }


# CLI for testing
if __name__ == "__main__":
    import sys

    print()
    print('='*70)
    print('PERPLEXITY SONAR SENTIMENT ANALYZER')
    print('='*70)
    print()

    # Test with a ticker
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else 'NVDA'
    test_date = sys.argv[2] if len(sys.argv) > 2 else None

    analyzer = SentimentAnalyzer(model="sonar-pro")

    print(f"Analyzing sentiment for: {test_ticker}")
    if test_date:
        print(f"Earnings date: {test_date}")
    print()

    result = analyzer.analyze_earnings_sentiment(test_ticker, test_date)

    print("SENTIMENT ANALYSIS RESULTS:")
    print('='*70)
    print(f"\nOverall Sentiment: {result['overall_sentiment'].upper()}")
    print(f"\nRetail Sentiment:\n{result['retail_sentiment']}")
    print(f"\nInstitutional Sentiment:\n{result['institutional_sentiment']}")
    print(f"\nHedge Fund Sentiment:\n{result['hedge_fund_sentiment']}")

    if result['tailwinds']:
        print(f"\nTailwinds:")
        for tw in result['tailwinds']:
            print(f"  + {tw}")

    if result['headwinds']:
        print(f"\nHeadwinds:")
        for hw in result['headwinds']:
            print(f"  - {hw}")

    print(f"\nUnusual Activity:\n{result['unusual_activity']}")
    print(f"\nGuidance History:\n{result['guidance_history']}")
    print(f"\nMacro & Sector:\n{result['macro_sector']}")

    print()
    print('='*70)
    print(f"API calls made: {analyzer.calls_made}")
    print('='*70)
