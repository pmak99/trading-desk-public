"""
Unified AI client with automatic fallback cascade.

Handles Perplexity → Claude → Gemini fallback when budget limits are reached.
Supports thread-safe budget tracking for concurrent execution.
"""

import os
import requests
import logging
from typing import Dict, Optional
from dotenv import load_dotenv
from src.usage_tracker import UsageTracker, BudgetExceededError

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class AIClient:
    """
    Unified AI client with fallback cascade.

    Automatically falls back from Perplexity → Claude → Gemini when budget limits reached.
    """

    def __init__(self, usage_tracker: Optional[UsageTracker] = None):
        """
        Initialize AI client.

        Args:
            usage_tracker: Optional usage tracker instance (creates new if not provided)
        """
        self.usage_tracker = usage_tracker or UsageTracker()

        # API keys for different providers
        self.perplexity_key = os.getenv('PERPLEXITY_API_KEY')
        self.anthropic_key = os.getenv('ANTHROPIC_API_KEY')
        self.google_key = os.getenv('GOOGLE_API_KEY')

        # API endpoints
        self.endpoints = {
            'perplexity': 'https://api.perplexity.ai/chat/completions',
            'anthropic': 'https://api.anthropic.com/v1/messages',
            'google': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        }

    def chat_completion(
        self,
        prompt: str,
        preferred_model: str = "sonar-pro",
        use_case: str = "sentiment",
        ticker: Optional[str] = None,
        max_tokens: int = 2000
    ) -> Dict:
        """
        Get chat completion with automatic fallback.

        Args:
            prompt: The prompt to send
            preferred_model: Preferred model name
            use_case: Use case ('sentiment' or 'strategy')
            ticker: Ticker symbol (for logging)
            max_tokens: Maximum tokens in response

        Returns:
            Dict with 'content', 'model', 'provider', 'tokens_used', 'cost'

        Raises:
            BudgetExceededError: If all models are exhausted
            Exception: If API call fails
        """
        # Get best available model
        try:
            model, provider = self.usage_tracker.get_available_model(preferred_model, use_case)
        except BudgetExceededError as e:
            logger.error(f"❌ All models exhausted: {e}")
            raise

        logger.info(f"📡 Using {model} ({provider}) for {use_case}")

        # Make API call based on provider
        if provider == 'perplexity':
            return self._call_perplexity(model, prompt, ticker, max_tokens)
        elif provider == 'anthropic':
            return self._call_anthropic(model, prompt, ticker, max_tokens)
        elif provider == 'google':
            return self._call_google(model, prompt, ticker, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def _call_perplexity(
        self,
        model: str,
        prompt: str,
        ticker: Optional[str],
        max_tokens: int
    ) -> Dict:
        """Call Perplexity API."""
        if not self.perplexity_key:
            raise ValueError("PERPLEXITY_API_KEY not set in environment")

        headers = {
            'Authorization': f'Bearer {self.perplexity_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens
        }

        try:
            response = requests.post(
                self.endpoints['perplexity'],
                headers=headers,
                json=data,
                timeout=30
            )
            response.raise_for_status()

            result = response.json()

            # Extract content and usage
            content = result['choices'][0]['message']['content']
            tokens_used = result['usage']['total_tokens']

            # Calculate cost
            config = self.usage_tracker.config
            cost_per_1k = config['models'][model]['cost_per_1k_tokens']
            cost = (tokens_used / 1000) * cost_per_1k

            # Log usage
            self.usage_tracker.log_api_call(model, tokens_used, cost, ticker, success=True)

            return {
                'content': content,
                'model': model,
                'provider': 'perplexity',
                'tokens_used': tokens_used,
                'cost': cost
            }

        except Exception as e:
            logger.error(f"Perplexity API error: {e}")
            # Log failed call
            self.usage_tracker.log_api_call(model, 0, 0, ticker, success=False)
            raise

    def _call_anthropic(
        self,
        model: str,
        prompt: str,
        ticker: Optional[str],
        max_tokens: int
    ) -> Dict:
        """Call Anthropic Claude API."""
        if not self.anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY not set in environment")

        headers = {
            'x-api-key': self.anthropic_key,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json'
        }

        data = {
            'model': 'claude-sonnet-4-20250514',  # Latest Claude Sonnet 4.5
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': prompt}]
        }

        try:
            response = requests.post(
                self.endpoints['anthropic'],
                headers=headers,
                json=data,
                timeout=60
            )
            response.raise_for_status()

            result = response.json()

            # Extract content and usage
            content = result['content'][0]['text']
            input_tokens = result['usage']['input_tokens']
            output_tokens = result['usage']['output_tokens']
            tokens_used = input_tokens + output_tokens

            # Calculate cost (input and output priced differently)
            config = self.usage_tracker.config
            input_cost_per_1k = config['models'][model]['cost_per_1k_tokens']
            output_cost_per_1k = config['models'][model]['cost_per_1k_output_tokens']

            cost = (input_tokens / 1000) * input_cost_per_1k + (output_tokens / 1000) * output_cost_per_1k

            # Log usage
            self.usage_tracker.log_api_call(model, tokens_used, cost, ticker, success=True)

            logger.info(f"✓ Claude response: {len(content)} chars, ${cost:.4f}")

            return {
                'content': content,
                'model': model,
                'provider': 'anthropic',
                'tokens_used': tokens_used,
                'cost': cost
            }

        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            self.usage_tracker.log_api_call(model, 0, 0, ticker, success=False)
            raise

    def _call_google(
        self,
        model: str,
        prompt: str,
        ticker: Optional[str],
        max_tokens: int
    ) -> Dict:
        """Call Google Gemini API."""
        if not self.google_key:
            raise ValueError("GOOGLE_API_KEY not set in environment")

        # Gemini 2.0 Flash model name
        gemini_model = 'gemini-2.0-flash-exp'

        url = self.endpoints['google'].format(model=gemini_model)

        params = {'key': self.google_key}

        data = {
            'contents': [{
                'parts': [{'text': prompt}]
            }],
            'generationConfig': {
                'maxOutputTokens': max_tokens
            }
        }

        try:
            response = requests.post(
                url,
                params=params,
                json=data,
                timeout=60
            )
            response.raise_for_status()

            result = response.json()

            # Extract content
            content = result['candidates'][0]['content']['parts'][0]['text']

            # Gemini free tier doesn't charge, estimate tokens for logging
            tokens_used = len(prompt.split()) * 1.3 + len(content.split()) * 1.3
            cost = 0.0  # Free tier

            # Log usage
            self.usage_tracker.log_api_call(model, int(tokens_used), cost, ticker, success=True)

            logger.info(f"✓ Gemini response: {len(content)} chars (FREE)")

            return {
                'content': content,
                'model': model,
                'provider': 'google',
                'tokens_used': int(tokens_used),
                'cost': cost
            }

        except Exception as e:
            logger.error(f"Google Gemini API error: {e}")
            self.usage_tracker.log_api_call(model, 0, 0, ticker, success=False)
            raise


# CLI for testing
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    print()
    print('='*70)
    print('AI CLIENT - TESTING FALLBACK CASCADE')
    print('='*70)
    print()

    test_prompt = sys.argv[1] if len(sys.argv) > 1 else "Explain IV crush in options trading in 50 words."

    client = AIClient()

    try:
        result = client.chat_completion(
            prompt=test_prompt,
            preferred_model="sonar-pro",
            use_case="sentiment"
        )

        print(f"Model: {result['model']} ({result['provider']})")
        print(f"Cost: ${result['cost']:.4f}")
        print(f"Tokens: {result['tokens_used']}")
        print()
        print("Response:")
        print(result['content'])

    except BudgetExceededError as e:
        print(f"❌ Budget exceeded: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")

    print()
    print('='*70)
