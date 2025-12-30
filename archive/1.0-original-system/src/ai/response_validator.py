"""
AI Response Validator

Validates AI-generated responses for sentiment analysis and strategy generation.
Provides comprehensive validation with detailed error messages.
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AIResponseValidator:
    """Validator for AI-generated JSON responses."""

    @staticmethod
    def validate_sentiment_response(data: Dict) -> Tuple[bool, Optional[str]]:
        """
        Validate sentiment analysis response.

        Args:
            data: Parsed JSON response

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if valid, False otherwise
            - error_message: None if valid, error description if invalid
        """
        # Check required fields
        required_fields = [
            'overall_sentiment',
            'retail_sentiment',
            'institutional_sentiment',
            'hedge_fund_sentiment',
            'tailwinds',
            'headwinds'
        ]

        for field in required_fields:
            if field not in data:
                return False, f"Missing required field: '{field}'"

        # Validate overall_sentiment value
        valid_sentiments = ['bullish', 'neutral', 'bearish']
        sentiment = data['overall_sentiment'].lower()
        if sentiment not in valid_sentiments:
            return False, f"Invalid overall_sentiment '{data['overall_sentiment']}', must be one of: {valid_sentiments}"

        # Validate list fields
        list_fields = ['tailwinds', 'headwinds']
        for field in list_fields:
            if not isinstance(data[field], list):
                return False, f"Field '{field}' must be a list, got {type(data[field]).__name__}"

        # Validate string fields are not empty
        string_fields = [
            'retail_sentiment',
            'institutional_sentiment',
            'hedge_fund_sentiment'
        ]

        for field in string_fields:
            if not isinstance(data[field], str) or not data[field].strip():
                return False, f"Field '{field}' must be a non-empty string"

        # Validate optional fields if present
        if 'confidence' in data:
            valid_confidence = ['low', 'medium', 'high']
            if data['confidence'].lower() not in valid_confidence:
                return False, f"Invalid confidence '{data['confidence']}', must be one of: {valid_confidence}"

        return True, None

    @staticmethod
    def validate_strategy_response(data: Dict) -> Tuple[bool, Optional[str]]:
        """
        Validate strategy generation response.

        Args:
            data: Parsed JSON response

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if valid, False otherwise
            - error_message: None if valid, error description if invalid
        """
        # Check required top-level fields
        if 'strategies' not in data:
            return False, "Missing required field: 'strategies'"

        if not isinstance(data['strategies'], list):
            return False, f"Field 'strategies' must be a list, got {type(data['strategies']).__name__}"

        if len(data['strategies']) == 0:
            return False, "Field 'strategies' must contain at least one strategy"

        if len(data['strategies']) > 4:
            return False, f"Field 'strategies' must contain at most 4 strategies, got {len(data['strategies'])}"

        # Validate each strategy
        required_strategy_fields = [
            'name',
            'type',
            'strikes',
            'expiration',
            'credit_debit',
            'max_profit',
            'max_loss',
            'breakeven',
            'probability_of_profit',
            'contract_count',
            'profitability_score',
            'risk_score',
            'rationale'
        ]

        for i, strategy in enumerate(data['strategies']):
            if not isinstance(strategy, dict):
                return False, f"Strategy {i} must be a dict, got {type(strategy).__name__}"

            for field in required_strategy_fields:
                if field not in strategy:
                    return False, f"Strategy {i} missing required field: '{field}'"

            # Validate strategy type
            valid_types = ['defined risk', 'undefined risk']
            if strategy['type'].lower() not in valid_types:
                return False, f"Strategy {i} has invalid type '{strategy['type']}', must be one of: {valid_types}"

            # Validate string fields are not empty
            string_fields = ['name', 'strikes', 'expiration', 'credit_debit',
                           'max_profit', 'max_loss', 'breakeven',
                           'probability_of_profit', 'contract_count',
                           'profitability_score', 'risk_score', 'rationale']

            for field in string_fields:
                if not isinstance(strategy[field], str) or not strategy[field].strip():
                    return False, f"Strategy {i} field '{field}' must be a non-empty string"

        # Validate recommended_strategy if present
        if 'recommended_strategy' in data:
            rec_idx = data['recommended_strategy']
            if not isinstance(rec_idx, int):
                return False, f"Field 'recommended_strategy' must be an integer, got {type(rec_idx).__name__}"

            if rec_idx < 0 or rec_idx >= len(data['strategies']):
                return False, f"Field 'recommended_strategy' index {rec_idx} out of range (0-{len(data['strategies'])-1})"

        return True, None

    @staticmethod
    def validate_and_sanitize_sentiment(data: Dict, ticker: str) -> Dict:
        """
        Validate and sanitize sentiment response.

        Logs warnings for issues and applies fixes where possible.

        Args:
            data: Parsed JSON response
            ticker: Ticker symbol (for logging)

        Returns:
            Sanitized data dict
        """
        is_valid, error = AIResponseValidator.validate_sentiment_response(data)

        if not is_valid:
            logger.warning(f"{ticker}: Sentiment validation failed: {error}")
            # Apply defaults for invalid data
            data.setdefault('overall_sentiment', 'neutral')
            data.setdefault('retail_sentiment', 'N/A')
            data.setdefault('institutional_sentiment', 'N/A')
            data.setdefault('hedge_fund_sentiment', 'N/A')
            data.setdefault('tailwinds', [])
            data.setdefault('headwinds', [])

        # Normalize overall_sentiment to lowercase
        if 'overall_sentiment' in data:
            sentiment = data['overall_sentiment'].lower()
            if sentiment not in ['bullish', 'neutral', 'bearish']:
                logger.warning(f"{ticker}: Invalid overall_sentiment '{data['overall_sentiment']}', defaulting to 'neutral'")
                data['overall_sentiment'] = 'neutral'
            else:
                data['overall_sentiment'] = sentiment

        # Ensure optional fields have defaults
        # unusual_activity now expects nested structure with sources
        if 'unusual_activity' not in data or not isinstance(data['unusual_activity'], dict):
            data['unusual_activity'] = {
                'detected': False,
                'sources': [],
                'findings': [],
                'summary': 'No unusual activity data available'
            }
        else:
            # Validate nested structure
            activity = data['unusual_activity']
            activity.setdefault('detected', False)
            activity.setdefault('sources', [])
            activity.setdefault('findings', [])
            activity.setdefault('summary', 'No unusual activity detected from verified sources')

        data.setdefault('guidance_history', 'N/A')
        data.setdefault('macro_sector', 'N/A')
        data.setdefault('confidence', 'medium')
        data.setdefault('sentiment_summary', '')

        return data

    @staticmethod
    def validate_and_sanitize_strategy(data: Dict, ticker: str) -> Dict:
        """
        Validate and sanitize strategy response.

        Logs warnings for issues and applies fixes where possible.

        Args:
            data: Parsed JSON response
            ticker: Ticker symbol (for logging)

        Returns:
            Sanitized data dict
        """
        is_valid, error = AIResponseValidator.validate_strategy_response(data)

        if not is_valid:
            logger.warning(f"{ticker}: Strategy validation failed: {error}")
            # Cannot recover from missing strategies
            if 'strategies' not in data or not isinstance(data['strategies'], list):
                data['strategies'] = []
                data['recommended_strategy'] = 0
                data['recommendation_rationale'] = 'N/A'
                return data

        # Ensure recommendation fields exist with defaults
        data.setdefault('recommended_strategy', 0)
        data.setdefault('recommendation_rationale', 'See strategy rationales above')

        # Validate recommended_strategy index
        if data['strategies']:
            rec_idx = data['recommended_strategy']
            if not isinstance(rec_idx, int) or rec_idx < 0 or rec_idx >= len(data['strategies']):
                logger.warning(f"{ticker}: Invalid recommended_strategy index {rec_idx}, defaulting to 0")
                data['recommended_strategy'] = 0

        return data
