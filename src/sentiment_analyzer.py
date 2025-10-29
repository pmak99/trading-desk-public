"""
AI-powered sentiment analyzer for earnings trades.

Uses unified AI client with automatic fallback (Perplexity → Gemini).
Uses Perplexity Sonar Pro for sentiment analysis with Reddit data integration.

Analyzes sentiment based on Trading Research Prompt.pdf criteria:
- Overall sentiment: Retail vs Institutional vs Hedge Fund
- Key headwinds and tailwinds
- Recent news, guidance history, unusual options flow
- Dark pool activity and positioning
- Reddit sentiment from r/wallstreetbets, r/stocks, r/options
"""

import logging
from typing import Dict, Optional
from src.ai_client import AIClient
from src.usage_tracker import UsageTracker, BudgetExceededError
from src.reddit_scraper import RedditScraper

logger = logging.getLogger(__name__)


class SentimentAnalyzer:
    """AI-powered sentiment analyzer with Reddit integration and automatic fallback."""

    def __init__(self, preferred_model: str = None, usage_tracker: Optional[UsageTracker] = None):
        """
        Initialize sentiment analyzer.

        Args:
            preferred_model: Preferred model to use (auto-fallback if budget exceeded)
                            - None: Use default from config (sonar-pro)
                            - "sonar-pro": Perplexity Sonar Pro for Reddit sentiment ($5/1M tokens) - default
                            - Falls back to Gemini when Perplexity exhausted
            usage_tracker: Optional UsageTracker instance for cost control
        """
        self.ai_client = AIClient(usage_tracker=usage_tracker)
        self.usage_tracker = self.ai_client.usage_tracker
        self.reddit_scraper = RedditScraper()

        # Use default from config if not specified
        if preferred_model is None:
            preferred_model = self.usage_tracker.config.get('defaults', {}).get('sentiment_model', 'sonar-pro')

        self.preferred_model = preferred_model

    def analyze_earnings_sentiment(self, ticker: str, earnings_date: Optional[str] = None, override_daily_limit: bool = False) -> Dict:
        """
        Analyze earnings sentiment for a ticker with Reddit data integration.

        Based on Trading Research Prompt.pdf criteria:
        1. Overall sentiment: Retail vs Institutional vs Hedge Fund
        2. Key headwinds and tailwinds
        3. Recent news, guidance history, macro factors
        4. Unusual options flow and dark pool activity
        5. Reddit sentiment from r/wallstreetbets, r/stocks, r/options

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
            - reddit_data: Reddit sentiment data
            - confidence: Low/Medium/High based on data quality
        """
        logger.info(f"Analyzing sentiment for {ticker}...")

        try:
            # Get Reddit sentiment first
            logger.info(f"{ticker}: Fetching Reddit sentiment...")
            reddit_data = self.reddit_scraper.get_ticker_sentiment(ticker, limit=20)
            logger.info(f"{ticker}: Found {reddit_data['posts_found']} Reddit posts")

            # Construct prompt with Reddit data
            prompt = self._build_sentiment_prompt(ticker, earnings_date, reddit_data)

            # Call AI API with budget tracking
            response = self._make_request(prompt, ticker=ticker, override_daily_limit=override_daily_limit)

            # Parse response into structured format
            result = self._parse_sentiment_response(response, ticker)

            # Add Reddit data to result
            result['reddit_data'] = reddit_data

            logger.info(f"{ticker}: Overall sentiment = {result.get('overall_sentiment', 'unknown')}")

            return result

        except Exception as e:
            logger.error(f"Error analyzing sentiment for {ticker}: {e}")
            return self._get_empty_result(ticker)

    def _build_sentiment_prompt(self, ticker: str, earnings_date: Optional[str], reddit_data: Dict) -> str:
        """
        Build sentiment analysis prompt based on user's criteria with Reddit data.

        Args:
            ticker: Ticker symbol
            earnings_date: Optional earnings date
            reddit_data: Reddit sentiment data from RedditScraper

        Returns:
            Prompt string for Grok
        """
        earnings_context = f" with earnings on {earnings_date}" if earnings_date else " heading into upcoming earnings"

        # Format Reddit data for prompt
        reddit_summary = f"""
REDDIT SENTIMENT DATA (r/wallstreetbets, r/stocks, r/options):
- Posts Found: {reddit_data['posts_found']}
- Average Score: {reddit_data['avg_score']:.1f}
- Total Comments: {reddit_data['total_comments']}
- Sentiment Score: {reddit_data['sentiment_score']:.2f} (-1.0 to 1.0)"""

        if reddit_data.get('top_posts'):
            reddit_summary += "\n\nTop Reddit Posts:"
            for i, post in enumerate(reddit_data['top_posts'][:3], 1):
                reddit_summary += f"\n  {i}. {post['title']} (Score: {post['score']}, Comments: {post['num_comments']})"

        prompt = f"""Analyze the earnings sentiment for {ticker}{earnings_context}.

{reddit_summary}


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

    def _make_request(self, prompt: str, ticker: Optional[str] = None, override_daily_limit: bool = False) -> str:
        """
        Make AI API request with automatic fallback.

        Uses unified AI client that automatically falls back from:
        Perplexity → Gemini when budget limits are reached.

        Args:
            prompt: Prompt string
            ticker: Ticker symbol (for logging)
            override_daily_limit: If True, bypass daily limits (but still respect hard caps)

        Returns:
            Response text

        Raises:
            BudgetExceededError: If all models exhausted
            Exception: If request fails
        """
        # Add system context to prompt
        full_prompt = f"""You are a professional options trading analyst specializing in earnings plays and sentiment analysis.

{prompt}"""

        try:
            # AI client handles budget checking and fallback automatically
            result = self.ai_client.chat_completion(
                prompt=full_prompt,
                preferred_model=self.preferred_model,
                use_case="sentiment",
                ticker=ticker,
                max_tokens=1500,
                override_daily_limit=override_daily_limit
            )

            # AI client already logged the call - just return content
            logger.info(f"{ticker}: Used {result['model']} ({result['provider']}) - ${result['cost']:.4f}")
            return result['content']

        except BudgetExceededError as e:
            logger.error(f"{ticker}: All models exhausted - {e}")
            raise
        except Exception as e:
            logger.error(f"{ticker}: API request failed - {e}")
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
            'reddit_data': {'posts_found': 0, 'sentiment_score': 0.0},
            'raw_response': '',
            'confidence': 'low'
        }


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import sys

    logger.info("")
    logger.info('='*70)
    logger.info('SONAR PRO + REDDIT SENTIMENT ANALYZER')
    logger.info('='*70)
    logger.info("")

    # Test with a ticker
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else 'NVDA'
    test_date = sys.argv[2] if len(sys.argv) > 2 else None

    analyzer = SentimentAnalyzer(preferred_model="sonar-pro")

    logger.info(f"Analyzing sentiment for: {test_ticker}")
    if test_date:
        logger.info(f"Earnings date: {test_date}")
    logger.info("")

    result = analyzer.analyze_earnings_sentiment(test_ticker, test_date)

    logger.info("SENTIMENT ANALYSIS RESULTS:")
    logger.info('='*70)
    logger.info(f"\nOverall Sentiment: {result['overall_sentiment'].upper()}")
    logger.info(f"\nRetail Sentiment:\n{result['retail_sentiment']}")
    logger.info(f"\nInstitutional Sentiment:\n{result['institutional_sentiment']}")
    logger.info(f"\nHedge Fund Sentiment:\n{result['hedge_fund_sentiment']}")

    if result['tailwinds']:
        logger.info(f"\nTailwinds:")
        for tw in result['tailwinds']:
            logger.info(f"  + {tw}")

    if result['headwinds']:
        logger.info(f"\nHeadwinds:")
        for hw in result['headwinds']:
            logger.info(f"  - {hw}")

    logger.info(f"\nUnusual Activity:\n{result['unusual_activity']}")
    logger.info(f"\nGuidance History:\n{result['guidance_history']}")
    logger.info(f"\nMacro & Sector:\n{result['macro_sector']}")

    # Display Reddit data
    reddit = result.get('reddit_data', {})
    if reddit.get('posts_found', 0) > 0:
        logger.info(f"\n\nREDDIT SENTIMENT:")
        logger.info(f"  Posts Found: {reddit['posts_found']}")
        logger.info(f"  Sentiment Score: {reddit['sentiment_score']:.2f} (-1.0 to 1.0)")
        logger.info(f"  Avg Post Score: {reddit['avg_score']:.1f}")
        logger.info(f"  Total Comments: {reddit['total_comments']}")

    logger.info("")
    logger.info('='*70)
