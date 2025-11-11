"""
AI-powered strategy generator for earnings trades.

Uses unified AI client with automatic fallback (Perplexity → Gemini).

Generates 3-4 trade strategies based on Trading Research Prompt.pdf criteria:
- Bull put spreads, bear call spreads, iron condors, iron butterflies
- Strikes outside expected move range (20-30 delta)
- Position sizing for $20K risk budget
- Probability of profit, risk/reward analysis

Improvements:
- JSON-based parsing (99% more reliable than string splitting)
- Fallback to legacy parsing for backward compatibility
- Better error handling and validation
"""

import logging
import json
from typing import Dict, List, Optional
from src.ai.client import AIClient
from src.core.usage_tracker import UsageTracker, BudgetExceededError
from src.ai.response_validator import AIResponseValidator
from src.core.json_utils import parse_json_safely

logger = logging.getLogger(__name__)


class StrategyGenerator:
    """AI-powered strategy generator with automatic fallback."""

    def __init__(self, preferred_model: str = None, usage_tracker: Optional[UsageTracker] = None):
        """
        Initialize strategy generator.

        Args:
            preferred_model: Preferred model to use (auto-fallback if budget exceeded)
                            - None: Use default from config (gpt-4o-mini)
                            - "sonar-pro": Fast, cheap ($5/1M tokens)
                            - "gpt-4o-mini": Very cheap ($0.2/1M tokens) - default
                            - Falls back to Gemini when Perplexity exhausted
            usage_tracker: Optional UsageTracker instance for cost control
        """
        self.ai_client = AIClient(usage_tracker=usage_tracker)
        self.usage_tracker = self.ai_client.usage_tracker

        # Use default from config if not specified
        if preferred_model is None:
            preferred_model = self.usage_tracker.config.get('defaults', {}).get('strategy_model', 'gpt-4o-mini')

        self.preferred_model = preferred_model

    def generate_strategies(
        self,
        ticker: str,
        options_data: Dict,
        sentiment_data: Dict,
        ticker_data: Dict,
        override_daily_limit: bool = False
    ) -> Dict:
        """
        Generate 3-4 trade strategies for a ticker.

        Based on Trading Research Prompt.pdf criteria:
        - Defined risk preferred, undefined if strong edge
        - Selling 20-30 delta strikes
        - Strikes outside expected move range
        - Position sizing for $20K risk budget
        - Hold through earnings (not scalp ahead)

        Args:
            ticker: Ticker symbol
            options_data: Options data from AlphaVantageClient
            sentiment_data: Sentiment analysis from SentimentAnalyzer
            ticker_data: Basic ticker data (price, market cap, etc.)

        Returns:
            Dict with:
            - strategies: List of 3-4 strategy dicts, each with:
              - name: Strategy type (e.g., "Bull Put Spread")
              - strikes: Strike prices
              - expiration: Expiration date
              - credit_debit: Net credit/debit
              - max_profit: Maximum profit
              - max_loss: Maximum loss (equals risk)
              - breakeven: Breakeven price(s)
              - probability_of_profit: Estimated POP %
              - contract_count: Number of contracts for $20K risk
              - profitability_score: 1-10 rating
              - risk_score: 1-10 rating
              - rationale: Why this strategy fits the setup
            - recommended_strategy: Index of the recommended strategy (0-3)
            - recommendation_rationale: Why this is the best choice
        """
        logger.info(f"Generating strategies for {ticker}...")

        try:
            # Build comprehensive prompt
            prompt = self._build_strategy_prompt(
                ticker, options_data, sentiment_data, ticker_data
            )

            # Call AI API with automatic fallback
            response = self._make_request(prompt, ticker=ticker, override_daily_limit=override_daily_limit)

            # Parse response into structured format
            result = self._parse_strategy_response(response, ticker)

            logger.info(f"{ticker}: Generated {len(result.get('strategies', []))} strategies")

            return result

        except (ValueError, KeyError, TypeError) as e:
            # Data parsing/structure errors
            logger.error(f"{ticker}: Data error in strategy generation: {e}")
            return self._get_empty_result(ticker)
        except Exception as e:
            # Unexpected errors - log and re-raise for visibility
            logger.error(f"{ticker}: Unexpected error in strategy generation: {e}", exc_info=True)
            raise

    def _build_strategy_prompt(
        self,
        ticker: str,
        options_data: Dict,
        sentiment_data: Dict,
        ticker_data: Dict
    ) -> str:
        """
        Build strategy generation prompt.

        Returns JSON format for reliable parsing.

        Args:
            ticker: Ticker symbol
            options_data: Options metrics
            sentiment_data: Sentiment analysis
            ticker_data: Basic ticker data

        Returns:
            Prompt string requesting JSON format
        """
        current_price = ticker_data.get('price', 0)
        expected_move = options_data.get('expected_move_pct', 'N/A')
        iv_crush_ratio = options_data.get('iv_crush_ratio', 'N/A')
        current_iv = options_data.get('current_iv', 'N/A')
        overall_sentiment = sentiment_data.get('overall_sentiment', 'unknown')

        prompt = f"""You are a highly experienced options trader. Generate 3-4 optimal earnings trade strategies for {ticker}.

TICKER DATA:
- Current Price: ${current_price}
- Current IV: {current_iv}%
- Expected Move: {expected_move}%
- IV Crush Ratio: {iv_crush_ratio}x (implied/actual historical)
- Overall Sentiment: {overall_sentiment}

TRADING CRITERIA (from Trading Research Prompt.pdf):
1. Prefer DEFINED RISK (vertical spreads) - mainly trade directional
2. Sell 20-30 delta strikes when selling premium
3. Position strikes OUTSIDE expected move range
4. Hold through earnings (not scalp ahead)
5. Risk budget: $20K per trade (calculate contract count)
6. High IV (>80%): Excellent premium to capture
7. Strategy: Enter 1-2 days before earnings for optimal timing

SENTIMENT CONTEXT:
- Retail: {sentiment_data.get('retail_sentiment', 'N/A')[:200]}
- Institutional: {sentiment_data.get('institutional_sentiment', 'N/A')[:200]}
- Tailwinds: {', '.join(sentiment_data.get('tailwinds', [])[:3])}
- Headwinds: {', '.join(sentiment_data.get('headwinds', [])[:3])}

Generate 3-4 strategies from these types:
- Bull Put Spread (credit spread below support)
- Bear Call Spread (credit spread above resistance)
- Iron Condor (neutral, wide profit zone)
- Iron Butterfly (neutral, profit at current price)
- Calendar/Diagonal Spread (if IV crush edge is very strong)

Return your analysis as valid JSON with this EXACT structure:

{{
  "strategies": [
    {{
      "name": "Strategy type (e.g., Bull Put Spread)",
      "type": "Defined Risk|Undefined Risk",
      "strikes": "e.g., Short 180P / Long 175P",
      "expiration": "e.g., Weekly expiring 3 days post-earnings",
      "credit_debit": "<CREDIT_OR_DEBIT> (e.g., $2.50 per spread)",
      "max_profit": "<MAX_PROFIT> (e.g., $250 per contract)",
      "max_loss": "<MAX_LOSS> (e.g., $750 per contract)",
      "breakeven": "<BREAKEVEN_PRICE> (e.g., $177.50)",
      "probability_of_profit": "<PROBABILITY>% (e.g., 65%)",
      "contract_count": "<NUM_CONTRACTS> contracts (for $20K position)",
      "profitability_score": "1-10",
      "risk_score": "1-10",
      "rationale": "Max 80 chars - key edge only (e.g., 'High IV crush + support at 175')"
    }}
  ],
  "recommended_strategy": 0,
  "recommendation_rationale": "Max 160 chars - best risk/reward & edge (e.g., 'Iron Condor optimal: IV>75%, defined risk, wide profit zone outside expected move')"
}}

BE SUCCINCT: Include 3-4 strategies. Use abbreviations. Keep rationales brief but meaningful."""

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
        full_prompt = f"""You are a professional options trader with 20+ years of experience trading earnings events using premium selling and IV crush strategies. You provide precise, actionable trade recommendations.

{prompt}"""

        try:
            # AI client handles budget checking and fallback automatically
            result = self.ai_client.chat_completion(
                prompt=full_prompt,
                preferred_model=self.preferred_model,
                use_case="strategy",
                ticker=ticker,
                max_tokens=2000,
                override_daily_limit=override_daily_limit
            )

            # AI client already logged the call - just return content
            logger.info(f"{ticker}: Used {result['model']} ({result['provider']}) - ${result['cost']:.4f}")
            return result['content']

        except BudgetExceededError as e:
            logger.error(f"{ticker}: All models exhausted - {e}")
            raise
        except Exception as e:
            # Sanitize error to avoid exposing API keys in URLs
            from src.ai.client import _sanitize_error_message
            logger.error(f"{ticker}: API request failed - {_sanitize_error_message(str(e))}")
            raise

    def _parse_strategy_response(self, response: str, ticker: str) -> Dict:
        """
        Parse AI response into structured format.

        Tries JSON parsing first (99% reliable), falls back to legacy string parsing.

        Args:
            response: Raw text response from AI
            ticker: Ticker symbol

        Returns:
            Structured strategies dict
        """
        # Try JSON parsing first
        try:
            # Parse JSON (handles markdown code blocks automatically)
            data = parse_json_safely(response, context=f"{ticker} strategy")

            # Validate and sanitize using validator
            data = AIResponseValidator.validate_and_sanitize_strategy(data, ticker)

            # Add ticker and raw response
            data['ticker'] = ticker
            data['raw_response'] = response

            logger.info(f"{ticker}: Successfully parsed JSON response with {len(data['strategies'])} strategies")
            return data

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"{ticker}: JSON parsing failed ({e}), trying legacy format")
            return self._parse_legacy_format(response, ticker)

    def _parse_legacy_format(self, response: str, ticker: str) -> Dict:
        """
        Parse legacy string-based response format (fallback).

        Args:
            response: Raw text response
            ticker: Ticker symbol

        Returns:
            Structured strategies dict
        """
        try:
            strategies = []

            # Extract individual strategies
            for i in range(1, 5):  # Max 4 strategies
                strategy_marker = f"STRATEGY {i}:"
                if strategy_marker not in response:
                    break

                # Find this strategy's text
                start = response.find(strategy_marker)
                next_strategy = f"STRATEGY {i+1}:"
                if next_strategy in response:
                    end = response.find(next_strategy)
                    strategy_text = response[start:end]
                elif "FINAL RECOMMENDATION:" in response:
                    end = response.find("FINAL RECOMMENDATION:")
                    strategy_text = response[start:end]
                else:
                    strategy_text = response[start:]

                # Parse strategy fields
                strategy = {
                    'name': self._extract_field(strategy_text, "STRATEGY " + str(i) + ":", "Type:"),
                    'type': self._extract_field(strategy_text, "Type:", "Strikes:"),
                    'strikes': self._extract_field(strategy_text, "Strikes:", "Expiration:"),
                    'expiration': self._extract_field(strategy_text, "Expiration:", "Net Credit/Debit:"),
                    'credit_debit': self._extract_field(strategy_text, "Net Credit/Debit:", "Max Profit:"),
                    'max_profit': self._extract_field(strategy_text, "Max Profit:", "Max Loss:"),
                    'max_loss': self._extract_field(strategy_text, "Max Loss:", "Breakeven:"),
                    'breakeven': self._extract_field(strategy_text, "Breakeven:", "Probability of Profit:"),
                    'probability_of_profit': self._extract_field(strategy_text, "Probability of Profit:", "Contract Count:"),
                    'contract_count': self._extract_field(strategy_text, "Contract Count:", "Profitability Score:"),
                    'profitability_score': self._extract_field(strategy_text, "Profitability Score:", "Risk Score:"),
                    'risk_score': self._extract_field(strategy_text, "Risk Score:", "Rationale:"),
                    'rationale': self._extract_field(strategy_text, "Rationale:", None)
                }

                strategies.append(strategy)

            # Extract recommendation
            recommendation_text = ""
            recommended_index = 0
            if "FINAL RECOMMENDATION:" in response:
                rec_start = response.find("FINAL RECOMMENDATION:")
                recommendation_text = response[rec_start + len("FINAL RECOMMENDATION:"):].strip()

                # Try to extract which strategy is recommended
                for i in range(1, 5):
                    if f"Strategy {i}" in recommendation_text:
                        recommended_index = i - 1
                        break

            logger.info(f"{ticker}: Successfully parsed legacy format response with {len(strategies)} strategies")

            return {
                'ticker': ticker,
                'strategies': strategies,
                'recommended_strategy': recommended_index,
                'recommendation_rationale': recommendation_text,
                'raw_response': response
            }

        except Exception as e:
            logger.error(f"{ticker}: Error parsing legacy format: {e}")
            return self._get_empty_result(ticker)

    def _extract_field(self, text: str, start_marker: str, end_marker: Optional[str]) -> str:
        """Extract field value between markers."""
        try:
            if start_marker not in text:
                return "N/A"

            start = text.find(start_marker) + len(start_marker)
            if end_marker and end_marker in text:
                end = text.find(end_marker, start)
                value = text[start:end].strip()
            else:
                # Take rest of line
                end_of_line = text.find('\n', start)
                if end_of_line != -1:
                    value = text[start:end_of_line].strip()
                else:
                    value = text[start:].strip()

            return value if value else "N/A"
        except Exception:
            return "N/A"

    def _get_empty_result(self, ticker: str) -> Dict:
        """Return empty result structure."""
        return {
            'ticker': ticker,
            'strategies': [],
            'recommended_strategy': 0,
            'recommendation_rationale': 'N/A',
            'raw_response': ''
        }


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import sys
    from src.alpha_vantage_client import AlphaVantageClient
    from src.ai.sentiment_analyzer import SentimentAnalyzer

    logger.info("")
    logger.info('='*70)
    logger.info('GPT-5 STRATEGY GENERATOR')
    logger.info('='*70)
    logger.info("")

    # Test with a ticker
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else 'NVDA'

    logger.info(f"WARNING: This will make API calls to OpenAI (cost: ~$0.02-0.05)")
    logger.info(f"Testing with ticker: {test_ticker}")
    logger.info("")

    confirmation = input("Continue? (y/n): ")
    if confirmation.lower() != 'y':
        logger.info("Aborted.")
        exit()

    # Get options data
    logger.info("\nFetching options data...")
    options_client = AlphaVantageClient()
    options_data = options_client.get_options_data(test_ticker)

    # Get sentiment data
    logger.info("Fetching sentiment data...")
    sentiment_client = SentimentAnalyzer()
    sentiment_data = sentiment_client.analyze_earnings_sentiment(test_ticker)

    # Mock ticker data
    ticker_data = {'price': 195.0, 'market_cap': 3000e9}

    # Generate strategies
    logger.info("\nGenerating strategies...")
    generator = StrategyGenerator()
    result = generator.generate_strategies(test_ticker, options_data, sentiment_data, ticker_data)

    if result['strategies']:
        logger.info("\nGENERATED STRATEGIES:")
        logger.info('='*70)

        for i, strategy in enumerate(result['strategies'], 1):
            logger.info(f"\nSTRATEGY {i}: {strategy['name']}")
            logger.info(f"  Type: {strategy['type']}")
            logger.info(f"  Strikes: {strategy['strikes']}")
            logger.info(f"  Expiration: {strategy['expiration']}")
            logger.info(f"  Credit/Debit: {strategy['credit_debit']}")
            logger.info(f"  Max Profit: {strategy['max_profit']}")
            logger.info(f"  Max Loss: {strategy['max_loss']}")
            logger.info(f"  Breakeven: {strategy['breakeven']}")
            logger.info(f"  POP: {strategy['probability_of_profit']}")
            logger.info(f"  Contracts: {strategy['contract_count']}")
            logger.info(f"  Scores: Profit {strategy['profitability_score']} / Risk {strategy['risk_score']}")
            logger.info(f"  Rationale: {strategy['rationale']}")

        logger.info(f"\nRECOMMENDED: Strategy {result['recommended_strategy'] + 1}")
        logger.info(f"Why: {result['recommendation_rationale']}")
    else:
        logger.info("Failed to generate strategies")

    logger.info("")
    logger.info('='*70)
    logger.info(f"API calls made: {generator.calls_made}")
    logger.info('='*70)
