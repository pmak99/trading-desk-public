"""
Perplexity Sonar strategy generator for earnings trades.

Generates 3-4 trade strategies based on Trading Research Prompt.pdf criteria:
- Bull put spreads, bear call spreads, iron condors, iron butterflies
- Strikes outside expected move range (20-30 delta)
- Position sizing for $20K risk budget
- Probability of profit, risk/reward analysis

Uses Perplexity API instead of OpenAI (unified API key management).
"""

import os
import requests
import logging
from typing import Dict, List, Optional
from dotenv import load_dotenv
from src.usage_tracker import UsageTracker

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when API budget is exceeded."""
    pass


class StrategyGenerator:
    """Perplexity Sonar client for options strategy generation."""

    def __init__(self, model: str = "sonar-pro", usage_tracker: Optional[UsageTracker] = None):
        """
        Initialize strategy generator using Perplexity API.

        Args:
            model: Perplexity model to use
                   - "sonar-pro": Perplexity's fast model ($0.005/1k tokens) - recommended
                   - "sonar": Alternative Perplexity model
            usage_tracker: Optional UsageTracker instance for cost control
        """
        self.api_key = os.getenv('PERPLEXITY_API_KEY')
        if not self.api_key:
            logger.warning("PERPLEXITY_API_KEY not found - strategy generator unavailable")
            self.api_key = None

        self.model = model
        self.api_url = "https://api.perplexity.ai/chat/completions"
        self.calls_made = 0

        # Initialize usage tracker for cost control
        self.usage_tracker = usage_tracker or UsageTracker()

    def generate_strategies(
        self,
        ticker: str,
        options_data: Dict,
        sentiment_data: Dict,
        ticker_data: Dict
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
        if not self.api_key:
            logger.error("Strategy generator unavailable - missing OpenAI API key")
            return self._get_empty_result(ticker)

        logger.info(f"Generating strategies for {ticker}...")

        try:
            # Build comprehensive prompt
            prompt = self._build_strategy_prompt(
                ticker, options_data, sentiment_data, ticker_data
            )

            # Call OpenAI API with budget tracking
            response = self._make_request(prompt, ticker=ticker)

            # Parse response into structured format
            result = self._parse_strategy_response(response, ticker)

            logger.info(f"{ticker}: Generated {len(result.get('strategies', []))} strategies")

            return result

        except Exception as e:
            logger.error(f"Error generating strategies for {ticker}: {e}")
            return self._get_empty_result(ticker)

    def _build_strategy_prompt(
        self,
        ticker: str,
        options_data: Dict,
        sentiment_data: Dict,
        ticker_data: Dict
    ) -> str:
        """
        Build strategy generation prompt.

        Args:
            ticker: Ticker symbol
            options_data: Options metrics
            sentiment_data: Sentiment analysis
            ticker_data: Basic ticker data

        Returns:
            Prompt string for OpenAI
        """
        current_price = ticker_data.get('price', 0)
        iv_rank = options_data.get('iv_rank', 'N/A')
        expected_move = options_data.get('expected_move_pct', 'N/A')
        iv_crush_ratio = options_data.get('iv_crush_ratio', 'N/A')
        overall_sentiment = sentiment_data.get('overall_sentiment', 'unknown')

        prompt = f"""You are a highly experienced options trader. Generate 3-4 optimal earnings trade strategies for {ticker}.

TICKER DATA:
- Current Price: ${current_price}
- IV Rank: {iv_rank}%
- Expected Move: {expected_move}%
- IV Crush Ratio: {iv_crush_ratio}x (implied/actual historical)
- Overall Sentiment: {overall_sentiment}

TRADING CRITERIA (from Trading Research Prompt.pdf):
1. Prefer DEFINED RISK (spreads), but consider undefined risk if edge is strong
2. Sell 20-30 delta strikes when selling premium
3. Position strikes OUTSIDE expected move range
4. Hold through earnings (not scalp ahead)
5. Risk budget: $20K per trade (calculate contract count)
6. IV Rank >75%: Prefer defined-risk spreads over iron condors
7. IV Rank 50-75%: Iron condors attractive for broader profit zones

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

For EACH strategy, provide EXACTLY this format:

STRATEGY 1: [Strategy Name]
Type: [Defined Risk / Undefined Risk]
Strikes: [e.g., Short 180P / Long 175P]
Expiration: [e.g., Weekly expiring 3 days post-earnings]
Net Credit/Debit: $[X.XX] per spread
Max Profit: $[XXX]
Max Loss: $[XXX]
Breakeven: $[XXX.XX]
Probability of Profit: [XX]%
Contract Count: [X] contracts (for $20K max risk)
Profitability Score: [1-10]
Risk Score: [1-10]
Rationale: [2-3 sentences on why this fits the setup]

[Repeat for Strategy 2, 3, and optionally 4]

FINAL RECOMMENDATION:
I recommend Strategy [#] because [2-3 sentence rationale covering risk/reward, edge, and fit with user's criteria].

Keep response under 800 words total. Be specific with numbers."""

        return prompt

    def _make_request(self, prompt: str, ticker: Optional[str] = None) -> str:
        """
        Make API request to OpenAI with budget checking.

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
        estimated_tokens = len(prompt) / 4 + 2000  # prompt + max_tokens response

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
                    "content": "You are a professional options trader with 20+ years of experience trading earnings events using premium selling and IV crush strategies. You provide precise, actionable trade recommendations."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,  # Low temperature for consistent, precise recommendations
            "max_tokens": 2000
        }

        success = False
        tokens_used = 0
        cost = 0.0

        try:
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()

            data = response.json()

            # Get actual token usage from response
            if 'usage' in data:
                tokens_used = data['usage'].get('total_tokens', estimated_tokens)
            else:
                tokens_used = estimated_tokens  # Fallback to estimate

            # Calculate cost
            if self.model in self.usage_tracker.config['models']:
                cost_per_1k = self.usage_tracker.config['models'][self.model]['cost_per_1k_tokens']
                cost = (tokens_used / 1000) * cost_per_1k
            else:
                # Fallback cost estimate if model not in config
                cost = (tokens_used / 1000) * 0.0025  # Use gpt-4o pricing as default

            success = True
            self.calls_made += 1

            # Log usage AFTER successful call
            self.usage_tracker.log_api_call(self.model, tokens_used, cost, ticker, success=True)

            return data['choices'][0]['message']['content']

        except requests.exceptions.RequestException as e:
            logger.error(f"OpenAI API request failed: {e}")
            # Log failed call with estimated cost
            if estimated_tokens > 0:
                if self.model in self.usage_tracker.config['models']:
                    cost_per_1k = self.usage_tracker.config['models'][self.model]['cost_per_1k_tokens']
                else:
                    cost_per_1k = 0.0025
                cost = (estimated_tokens / 1000) * cost_per_1k
                self.usage_tracker.log_api_call(self.model, estimated_tokens, cost, ticker, success=False)
            raise

    def _parse_strategy_response(self, response: str, ticker: str) -> Dict:
        """
        Parse OpenAI response into structured format.

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

            return {
                'ticker': ticker,
                'strategies': strategies,
                'recommended_strategy': recommended_index,
                'recommendation_rationale': recommendation_text,
                'raw_response': response
            }

        except Exception as e:
            logger.error(f"Error parsing strategy response: {e}")
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
        except:
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
    import sys
    from src.alpha_vantage_client import AlphaVantageClient
    from src.sentiment_analyzer import SentimentAnalyzer

    print()
    print('='*70)
    print('GPT-5 STRATEGY GENERATOR')
    print('='*70)
    print()

    # Test with a ticker
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else 'NVDA'

    print(f"WARNING: This will make API calls to OpenAI (cost: ~$0.02-0.05)")
    print(f"Testing with ticker: {test_ticker}")
    print()

    confirmation = input("Continue? (y/n): ")
    if confirmation.lower() != 'y':
        print("Aborted.")
        exit()

    # Get options data
    print("\nFetching options data...")
    options_client = AlphaVantageClient()
    options_data = options_client.get_options_data(test_ticker)

    # Get sentiment data
    print("Fetching sentiment data...")
    sentiment_client = SentimentAnalyzer()
    sentiment_data = sentiment_client.analyze_earnings_sentiment(test_ticker)

    # Mock ticker data
    ticker_data = {'price': 195.0, 'market_cap': 3000e9}

    # Generate strategies
    print("\nGenerating strategies...")
    generator = StrategyGenerator()
    result = generator.generate_strategies(test_ticker, options_data, sentiment_data, ticker_data)

    if result['strategies']:
        print("\nGENERATED STRATEGIES:")
        print('='*70)

        for i, strategy in enumerate(result['strategies'], 1):
            print(f"\nSTRATEGY {i}: {strategy['name']}")
            print(f"  Type: {strategy['type']}")
            print(f"  Strikes: {strategy['strikes']}")
            print(f"  Expiration: {strategy['expiration']}")
            print(f"  Credit/Debit: {strategy['credit_debit']}")
            print(f"  Max Profit: {strategy['max_profit']}")
            print(f"  Max Loss: {strategy['max_loss']}")
            print(f"  Breakeven: {strategy['breakeven']}")
            print(f"  POP: {strategy['probability_of_profit']}")
            print(f"  Contracts: {strategy['contract_count']}")
            print(f"  Scores: Profit {strategy['profitability_score']} / Risk {strategy['risk_score']}")
            print(f"  Rationale: {strategy['rationale']}")

        print(f"\nRECOMMENDED: Strategy {result['recommended_strategy'] + 1}")
        print(f"Why: {result['recommendation_rationale']}")
    else:
        print("Failed to generate strategies")

    print()
    print('='*70)
    print(f"API calls made: {generator.calls_made}")
    print('='*70)
