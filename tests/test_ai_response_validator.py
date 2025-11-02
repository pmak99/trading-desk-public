"""
Unit tests for AI response validator.

Tests validation logic for sentiment and strategy responses.
"""

import pytest
from src.ai.response_validator import AIResponseValidator


class TestAIResponseValidator:
    """Test AI response validation."""

    # Sentiment validation tests

    def test_validate_sentiment_valid_response(self):
        """Test validation of a valid sentiment response."""
        valid_data = {
            'overall_sentiment': 'bullish',
            'retail_sentiment': 'Very bullish',
            'institutional_sentiment': 'Increasing positions',
            'hedge_fund_sentiment': 'Mixed',
            'tailwinds': ['Growth', 'Innovation'],
            'headwinds': ['Valuation', 'Competition']
        }

        is_valid, error = AIResponseValidator.validate_sentiment_response(valid_data)
        assert is_valid is True
        assert error is None

    def test_validate_sentiment_missing_required_field(self):
        """Test validation fails when required field is missing."""
        invalid_data = {
            'overall_sentiment': 'bullish',
            'retail_sentiment': 'Very bullish',
            # Missing institutional_sentiment
            'hedge_fund_sentiment': 'Mixed',
            'tailwinds': ['Growth'],
            'headwinds': ['Valuation']
        }

        is_valid, error = AIResponseValidator.validate_sentiment_response(invalid_data)
        assert is_valid is False
        assert 'institutional_sentiment' in error

    def test_validate_sentiment_invalid_overall_sentiment(self):
        """Test validation fails for invalid overall_sentiment value."""
        invalid_data = {
            'overall_sentiment': 'super-bullish',  # Invalid
            'retail_sentiment': 'Very bullish',
            'institutional_sentiment': 'Increasing',
            'hedge_fund_sentiment': 'Mixed',
            'tailwinds': ['Growth'],
            'headwinds': ['Valuation']
        }

        is_valid, error = AIResponseValidator.validate_sentiment_response(invalid_data)
        assert is_valid is False
        assert 'overall_sentiment' in error

    def test_validate_sentiment_tailwinds_not_list(self):
        """Test validation fails when tailwinds is not a list."""
        invalid_data = {
            'overall_sentiment': 'bullish',
            'retail_sentiment': 'Very bullish',
            'institutional_sentiment': 'Increasing',
            'hedge_fund_sentiment': 'Mixed',
            'tailwinds': 'Growth and Innovation',  # Should be list
            'headwinds': ['Valuation']
        }

        is_valid, error = AIResponseValidator.validate_sentiment_response(invalid_data)
        assert is_valid is False
        assert 'tailwinds' in error
        assert 'list' in error

    def test_validate_sentiment_empty_string_field(self):
        """Test validation fails when string field is empty."""
        invalid_data = {
            'overall_sentiment': 'bullish',
            'retail_sentiment': '',  # Empty
            'institutional_sentiment': 'Increasing',
            'hedge_fund_sentiment': 'Mixed',
            'tailwinds': ['Growth'],
            'headwinds': ['Valuation']
        }

        is_valid, error = AIResponseValidator.validate_sentiment_response(invalid_data)
        assert is_valid is False
        assert 'retail_sentiment' in error

    def test_validate_sentiment_invalid_confidence(self):
        """Test validation fails for invalid confidence value."""
        invalid_data = {
            'overall_sentiment': 'bullish',
            'retail_sentiment': 'Very bullish',
            'institutional_sentiment': 'Increasing',
            'hedge_fund_sentiment': 'Mixed',
            'tailwinds': ['Growth'],
            'headwinds': ['Valuation'],
            'confidence': 'very-high'  # Invalid
        }

        is_valid, error = AIResponseValidator.validate_sentiment_response(invalid_data)
        assert is_valid is False
        assert 'confidence' in error

    def test_validate_sentiment_valid_with_optional_fields(self):
        """Test validation passes with valid optional fields."""
        valid_data = {
            'overall_sentiment': 'neutral',
            'retail_sentiment': 'Mixed',
            'institutional_sentiment': 'Cautious',
            'hedge_fund_sentiment': 'Waiting',
            'tailwinds': ['Revenue'],
            'headwinds': ['Competition'],
            'confidence': 'high',
            'unusual_activity': 'Heavy call buying',
            'guidance_history': 'Beat last quarter'
        }

        is_valid, error = AIResponseValidator.validate_sentiment_response(valid_data)
        assert is_valid is True
        assert error is None

    # Strategy validation tests

    def test_validate_strategy_valid_response(self):
        """Test validation of a valid strategy response."""
        valid_data = {
            'strategies': [
                {
                    'name': 'Bull Put Spread',
                    'type': 'Defined Risk',
                    'strikes': 'Short 180P / Long 175P',
                    'expiration': 'Weekly',
                    'credit_debit': '$3.50',
                    'max_profit': '$350',
                    'max_loss': '$150',
                    'breakeven': '$176.50',
                    'probability_of_profit': '75%',
                    'contract_count': '4',
                    'profitability_score': '8',
                    'risk_score': '6',
                    'rationale': 'Strong support'
                }
            ],
            'recommended_strategy': 0,
            'recommendation_rationale': 'Best risk/reward'
        }

        is_valid, error = AIResponseValidator.validate_strategy_response(valid_data)
        assert is_valid is True
        assert error is None

    def test_validate_strategy_missing_strategies_field(self):
        """Test validation fails when strategies field is missing."""
        invalid_data = {
            'recommended_strategy': 0,
            'recommendation_rationale': 'Test'
        }

        is_valid, error = AIResponseValidator.validate_strategy_response(invalid_data)
        assert is_valid is False
        assert 'strategies' in error

    def test_validate_strategy_strategies_not_list(self):
        """Test validation fails when strategies is not a list."""
        invalid_data = {
            'strategies': 'Bull Put Spread',  # Should be list
            'recommended_strategy': 0
        }

        is_valid, error = AIResponseValidator.validate_strategy_response(invalid_data)
        assert is_valid is False
        assert 'list' in error

    def test_validate_strategy_empty_strategies_array(self):
        """Test validation fails when strategies array is empty."""
        invalid_data = {
            'strategies': [],
            'recommended_strategy': 0
        }

        is_valid, error = AIResponseValidator.validate_strategy_response(invalid_data)
        assert is_valid is False
        assert 'at least one' in error

    def test_validate_strategy_too_many_strategies(self):
        """Test validation fails when more than 4 strategies provided."""
        invalid_data = {
            'strategies': [
                {'name': 'Strategy 1', 'type': 'Defined Risk', 'strikes': 'Test',
                 'expiration': 'Test', 'credit_debit': '$1', 'max_profit': '$100',
                 'max_loss': '$50', 'breakeven': '$180', 'probability_of_profit': '70%',
                 'contract_count': '1', 'profitability_score': '7', 'risk_score': '5',
                 'rationale': 'Test'},
                {'name': 'Strategy 2', 'type': 'Defined Risk', 'strikes': 'Test',
                 'expiration': 'Test', 'credit_debit': '$1', 'max_profit': '$100',
                 'max_loss': '$50', 'breakeven': '$180', 'probability_of_profit': '70%',
                 'contract_count': '1', 'profitability_score': '7', 'risk_score': '5',
                 'rationale': 'Test'},
                {'name': 'Strategy 3', 'type': 'Defined Risk', 'strikes': 'Test',
                 'expiration': 'Test', 'credit_debit': '$1', 'max_profit': '$100',
                 'max_loss': '$50', 'breakeven': '$180', 'probability_of_profit': '70%',
                 'contract_count': '1', 'profitability_score': '7', 'risk_score': '5',
                 'rationale': 'Test'},
                {'name': 'Strategy 4', 'type': 'Defined Risk', 'strikes': 'Test',
                 'expiration': 'Test', 'credit_debit': '$1', 'max_profit': '$100',
                 'max_loss': '$50', 'breakeven': '$180', 'probability_of_profit': '70%',
                 'contract_count': '1', 'profitability_score': '7', 'risk_score': '5',
                 'rationale': 'Test'},
                {'name': 'Strategy 5', 'type': 'Defined Risk', 'strikes': 'Test',
                 'expiration': 'Test', 'credit_debit': '$1', 'max_profit': '$100',
                 'max_loss': '$50', 'breakeven': '$180', 'probability_of_profit': '70%',
                 'contract_count': '1', 'profitability_score': '7', 'risk_score': '5',
                 'rationale': 'Test'}
            ],
            'recommended_strategy': 0
        }

        is_valid, error = AIResponseValidator.validate_strategy_response(invalid_data)
        assert is_valid is False
        assert 'at most 4' in error

    def test_validate_strategy_missing_strategy_field(self):
        """Test validation fails when strategy is missing required field."""
        invalid_data = {
            'strategies': [
                {
                    'name': 'Bull Put Spread',
                    'type': 'Defined Risk',
                    # Missing 'strikes' and other fields
                }
            ]
        }

        is_valid, error = AIResponseValidator.validate_strategy_response(invalid_data)
        assert is_valid is False
        assert 'Strategy 0' in error
        assert 'strikes' in error

    def test_validate_strategy_invalid_type(self):
        """Test validation fails for invalid strategy type."""
        invalid_data = {
            'strategies': [
                {
                    'name': 'Bull Put Spread',
                    'type': 'Super Safe',  # Invalid
                    'strikes': 'Short 180P / Long 175P',
                    'expiration': 'Weekly',
                    'credit_debit': '$3.50',
                    'max_profit': '$350',
                    'max_loss': '$150',
                    'breakeven': '$176.50',
                    'probability_of_profit': '75%',
                    'contract_count': '4',
                    'profitability_score': '8',
                    'risk_score': '6',
                    'rationale': 'Test'
                }
            ]
        }

        is_valid, error = AIResponseValidator.validate_strategy_response(invalid_data)
        assert is_valid is False
        assert 'type' in error

    def test_validate_strategy_invalid_recommended_index(self):
        """Test validation fails for out-of-bounds recommended_strategy index."""
        invalid_data = {
            'strategies': [
                {
                    'name': 'Bull Put Spread',
                    'type': 'Defined Risk',
                    'strikes': 'Short 180P / Long 175P',
                    'expiration': 'Weekly',
                    'credit_debit': '$3.50',
                    'max_profit': '$350',
                    'max_loss': '$150',
                    'breakeven': '$176.50',
                    'probability_of_profit': '75%',
                    'contract_count': '4',
                    'profitability_score': '8',
                    'risk_score': '6',
                    'rationale': 'Test'
                }
            ],
            'recommended_strategy': 5  # Out of bounds
        }

        is_valid, error = AIResponseValidator.validate_strategy_response(invalid_data)
        assert is_valid is False
        assert 'recommended_strategy' in error
        assert 'out of range' in error

    # Sanitization tests

    def test_sanitize_sentiment_adds_defaults_for_invalid(self):
        """Test sanitization adds defaults for invalid sentiment data."""
        invalid_data = {
            'overall_sentiment': 'super-bullish'
        }

        result = AIResponseValidator.validate_and_sanitize_sentiment(invalid_data, 'TEST')

        assert result['overall_sentiment'] == 'neutral'  # Fixed invalid value
        assert result['retail_sentiment'] == 'N/A'
        assert result['institutional_sentiment'] == 'N/A'
        assert result['hedge_fund_sentiment'] == 'N/A'
        assert result['tailwinds'] == []
        assert result['headwinds'] == []

    def test_sanitize_sentiment_normalizes_case(self):
        """Test sanitization normalizes sentiment to lowercase."""
        data = {
            'overall_sentiment': 'BULLISH',
            'retail_sentiment': 'Test',
            'institutional_sentiment': 'Test',
            'hedge_fund_sentiment': 'Test',
            'tailwinds': [],
            'headwinds': []
        }

        result = AIResponseValidator.validate_and_sanitize_sentiment(data, 'TEST')
        assert result['overall_sentiment'] == 'bullish'

    def test_sanitize_strategy_adds_defaults_for_invalid(self):
        """Test sanitization adds defaults for invalid strategy data."""
        invalid_data = {}

        result = AIResponseValidator.validate_and_sanitize_strategy(invalid_data, 'TEST')

        assert result['strategies'] == []
        assert result['recommended_strategy'] == 0
        assert result['recommendation_rationale'] == 'N/A'

    def test_sanitize_strategy_fixes_invalid_index(self):
        """Test sanitization fixes invalid recommended_strategy index."""
        data = {
            'strategies': [
                {
                    'name': 'Test',
                    'type': 'Defined Risk',
                    'strikes': 'Test',
                    'expiration': 'Test',
                    'credit_debit': '$1',
                    'max_profit': '$100',
                    'max_loss': '$50',
                    'breakeven': '$180',
                    'probability_of_profit': '70%',
                    'contract_count': '1',
                    'profitability_score': '7',
                    'risk_score': '5',
                    'rationale': 'Test'
                }
            ],
            'recommended_strategy': 10  # Invalid
        }

        result = AIResponseValidator.validate_and_sanitize_strategy(data, 'TEST')
        assert result['recommended_strategy'] == 0  # Fixed
