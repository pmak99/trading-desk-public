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

Improvements:
- JSON-based parsing (99% more reliable than string splitting)
- Fallback to legacy parsing for backward compatibility
- Better error handling and validation
"""

import logging
import json
from typing import Dict, Optional
from src.ai.client import AIClient
from src.core.usage_tracker import UsageTracker, BudgetExceededError
from src.data.reddit_scraper import RedditScraper
from src.ai.response_validator import AIResponseValidator
from src.core.json_utils import parse_json_safely

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
            # Get ENHANCED Reddit sentiment with AI content analysis (uses free Gemini)
            logger.info(f"{ticker}: Fetching Reddit sentiment with AI content analysis...")
            reddit_data = self.reddit_scraper.get_ticker_sentiment(
                ticker,
                limit=20,
                analyze_content=True,
                ai_client=self.ai_client
            )
            logger.info(f"{ticker}: Found {reddit_data['posts_found']} Reddit posts, "
                       f"sentiment: {reddit_data.get('sentiment_score', 0):.2f}")

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

        except (ValueError, KeyError, TypeError) as e:
            # Data parsing/structure errors
            logger.error(f"{ticker}: Data error in sentiment analysis: {e}")
            return self._get_empty_result(ticker)
        except Exception as e:
            # Unexpected errors - log and re-raise for visibility
            logger.error(f"{ticker}: Unexpected error in sentiment analysis: {e}", exc_info=True)
            raise

    def _build_sentiment_prompt(self, ticker: str, earnings_date: Optional[str], reddit_data: Dict) -> str:
        """
        Build sentiment analysis prompt based on user's criteria with Reddit data.

        Returns JSON format for reliable parsing.

        Args:
            ticker: Ticker symbol
            earnings_date: Optional earnings date
            reddit_data: Reddit sentiment data from RedditScraper

        Returns:
            Prompt string requesting JSON format
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

IMPORTANT: For unusual options activity, you MUST search recent data from these sources:
1. Barchart unusual options activity
2. CBOE published volume data
3. Financial news about institutional positioning (Bloomberg, CNBC, Reuters)
4. Options flow discussions on financial sites (past 3 days only)

For EACH unusual activity finding, you MUST provide:
- Specific data point (e.g., "15,000 contracts at $180 call strike")
- Source name (e.g., "Barchart Unusual Activity", "CBOE Data")
- Date observed (e.g., "November 8, 2025")

If NO reliable sources found, set detected=false and summary="No unusual activity detected from verified sources."
DO NOT speculate or infer unusual activity without citing a specific, named source with a date.

Return your analysis as valid JSON with this EXACT structure:

{{
  "overall_sentiment": "bullish|neutral|bearish",
  "sentiment_summary": "1 sentence max",
  "retail_sentiment": "Max 100 chars - only key positioning",
  "institutional_sentiment": "Max 100 chars - only key positioning",
  "hedge_fund_sentiment": "Max 100 chars - only key positioning",
  "tailwinds": ["max 50 chars each", "..."],
  "headwinds": ["max 50 chars each", "..."],
  "unusual_activity": {{
    "detected": true|false,
    "sources": ["source 1", "source 2"],
    "findings": ["finding 1 with date", "finding 2 with date"],
    "summary": "Max 80 chars OR 'No unusual activity detected from verified sources'"
  }},
  "guidance_history": "Max 120 chars - only recent beat/miss pattern",
  "macro_sector": "Max 120 chars - only most relevant trend",
  "confidence": "low|medium|high"
}}

BE SUCCINCT: Focus on actionable intelligence for IV crush strategy. Use abbreviations (e.g., "inst" for institutional, "bullish on tech" vs "bullish sentiment on technology sector"). Each sentiment field must fit character limits - prioritize key info only."""

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
        Parse AI response into structured format.

        Tries JSON parsing first (99% reliable), falls back to legacy string parsing.

        Args:
            response: Raw text response from AI
            ticker: Ticker symbol

        Returns:
            Structured sentiment dict
        """
        # Try JSON parsing first
        try:
            # Parse JSON (handles markdown code blocks automatically)
            data = parse_json_safely(response, context=f"{ticker} sentiment")

            # Validate and sanitize using validator
            data = AIResponseValidator.validate_and_sanitize_sentiment(data, ticker)

            # Add ticker and raw response
            data['ticker'] = ticker
            data['raw_response'] = response

            logger.info(f"{ticker}: Successfully parsed JSON response")
            return data

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"{ticker}: JSON parsing failed ({e}), trying legacy format")
            return self._parse_legacy_format(response, ticker)

    def _parse_legacy_format(self, response: str, ticker: str) -> Dict:
        """
        Parse legacy string-based response format (fallback).

        Args:
            response: Raw text response
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

            logger.info(f"{ticker}: Successfully parsed legacy format response")
            return result

        except Exception as e:
            logger.error(f"{ticker}: Error parsing legacy format: {e}")
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
        except (AttributeError, TypeError, ValueError) as e:
            logger.debug(f"Failed to extract section with markers '{start_marker}'...'{end_marker}': {e}")
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
        except (AttributeError, TypeError, ValueError) as e:
            logger.debug(f"Failed to extract list with markers '{start_marker}'...'{end_marker}': {e}")
            return []

    def _get_empty_result(self, ticker: str) -> Dict:
        """Return empty result structure."""
        return {
            'ticker': ticker,
            'overall_sentiment': 'unknown',
            'sentiment_summary': '',
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
