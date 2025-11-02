"""
Unit tests for sentiment analyzer JSON parsing.

Tests the new JSON-based parsing with fallback to legacy format.
"""

import json
import pytest
from src.ai.sentiment_analyzer import SentimentAnalyzer


class TestSentimentJSONParsing:
    """Test JSON parsing functionality in sentiment analyzer."""

    @pytest.fixture
    def analyzer(self):
        """Create sentiment analyzer instance."""
        return SentimentAnalyzer()

    def test_parse_valid_json_response(self, analyzer):
        """Test parsing a valid JSON response."""
        json_response = json.dumps({
            'overall_sentiment': 'bullish',
            'sentiment_summary': 'Strong momentum heading into earnings',
            'retail_sentiment': 'Very bullish positioning',
            'institutional_sentiment': 'Increasing positions',
            'hedge_fund_sentiment': 'Mixed sentiment',
            'tailwinds': ['Strong revenue growth', 'AI leadership'],
            'headwinds': ['High valuation', 'Market volatility'],
            'unusual_activity': 'Heavy call buying',
            'guidance_history': 'Beat last 3 quarters',
            'macro_sector': 'Tech sector strength',
            'confidence': 'high'
        })

        result = analyzer._parse_sentiment_response(json_response, 'NVDA')

        assert result['ticker'] == 'NVDA'
        assert result['overall_sentiment'] == 'bullish'
        assert result['sentiment_summary'] == 'Strong momentum heading into earnings'
        assert len(result['tailwinds']) == 2
        assert len(result['headwinds']) == 2
        assert result['confidence'] == 'high'
        assert 'raw_response' in result

    def test_parse_json_with_markdown_code_blocks(self, analyzer):
        """Test parsing JSON wrapped in markdown code blocks."""
        json_data = {
            'overall_sentiment': 'neutral',
            'sentiment_summary': 'Mixed signals',
            'retail_sentiment': 'Neutral',
            'institutional_sentiment': 'Cautious',
            'hedge_fund_sentiment': 'Waiting',
            'tailwinds': ['Revenue growth'],
            'headwinds': ['Competition'],
            'unusual_activity': 'Normal flow',
            'guidance_history': 'In-line',
            'macro_sector': 'Stable',
            'confidence': 'medium'
        }

        markdown_response = f"```json\n{json.dumps(json_data, indent=2)}\n```"

        result = analyzer._parse_sentiment_response(markdown_response, 'AAPL')

        assert result['ticker'] == 'AAPL'
        assert result['overall_sentiment'] == 'neutral'
        assert len(result['tailwinds']) == 1
        assert len(result['headwinds']) == 1

    def test_parse_json_normalizes_sentiment_case(self, analyzer):
        """Test that overall_sentiment is normalized to lowercase."""
        json_response = json.dumps({
            'overall_sentiment': 'BULLISH',  # Uppercase
            'sentiment_summary': 'Test',
            'retail_sentiment': 'Test',
            'institutional_sentiment': 'Test',
            'hedge_fund_sentiment': 'Test',
            'tailwinds': [],
            'headwinds': []
        })

        result = analyzer._parse_sentiment_response(json_response, 'TEST')
        assert result['overall_sentiment'] == 'bullish'  # Normalized to lowercase

    def test_parse_json_with_invalid_sentiment_defaults_to_neutral(self, analyzer):
        """Test that invalid sentiment values default to neutral."""
        json_response = json.dumps({
            'overall_sentiment': 'super-mega-bullish',  # Invalid value
            'sentiment_summary': 'Test',
            'retail_sentiment': 'Test',
            'institutional_sentiment': 'Test',
            'hedge_fund_sentiment': 'Test',
            'tailwinds': [],
            'headwinds': []
        })

        result = analyzer._parse_sentiment_response(json_response, 'TEST')
        assert result['overall_sentiment'] == 'neutral'

    def test_parse_json_with_missing_fields_raises_error_and_falls_back(self, analyzer):
        """Test that missing required fields triggers fallback to legacy format."""
        # Missing 'retail_sentiment' field
        json_response = json.dumps({
            'overall_sentiment': 'bullish',
            'institutional_sentiment': 'Test',
            'hedge_fund_sentiment': 'Test',
            'tailwinds': [],
            'headwinds': []
        })

        # Should fallback to legacy parser which will return N/A for missing fields
        result = analyzer._parse_sentiment_response(json_response, 'TEST')

        # Since JSON parse fails, it should use legacy format
        # Legacy format won't find the markers, so it returns N/A
        assert result['retail_sentiment'] == 'N/A'

    def test_fallback_to_legacy_format(self, analyzer):
        """Test fallback to legacy string-based parsing."""
        legacy_response = """
OVERALL SENTIMENT: Bearish - Weak fundamentals

RETAIL SENTIMENT:
Bearish positioning with high put volume

INSTITUTIONAL SENTIMENT:
Reducing positions ahead of earnings

HEDGE FUND SENTIMENT:
Net short positioning increasing

KEY TAILWINDS:
- Cost cutting measures
- New product launch

KEY HEADWINDS:
- Revenue decline
- Market share loss
- Regulatory headwinds

UNUSUAL ACTIVITY:
Heavy put buying, dark pool sales

GUIDANCE HISTORY:
Missed last 2 quarters, lowered guidance

MACRO & SECTOR FACTORS:
Sector headwinds, rising rates
"""

        result = analyzer._parse_sentiment_response(legacy_response, 'TSLA')

        assert result['ticker'] == 'TSLA'
        assert result['overall_sentiment'] == 'bearish'
        assert 'Bearish positioning' in result['retail_sentiment']
        assert len(result['tailwinds']) == 2
        assert len(result['headwinds']) == 3
        assert 'Heavy put buying' in result['unusual_activity']

    def test_legacy_format_extracts_sections_correctly(self, analyzer):
        """Test that legacy format extracts sections between markers."""
        legacy_response = """
RETAIL SENTIMENT:
This is retail sentiment text

INSTITUTIONAL SENTIMENT:
This is institutional sentiment text

HEDGE FUND SENTIMENT:
This is hedge fund text

KEY TAILWINDS:
- Tailwind 1
- Tailwind 2
"""

        result = analyzer._parse_legacy_format(legacy_response, 'TEST')

        assert 'retail sentiment text' in result['retail_sentiment'].lower()
        assert 'institutional sentiment text' in result['institutional_sentiment'].lower()
        assert 'hedge fund text' in result['hedge_fund_sentiment'].lower()
        assert len(result['tailwinds']) == 2

    def test_empty_result_has_all_required_fields(self, analyzer):
        """Test that empty result contains all expected fields."""
        result = analyzer._get_empty_result('EMPTY')

        required_fields = [
            'ticker', 'overall_sentiment', 'sentiment_summary',
            'retail_sentiment', 'institutional_sentiment', 'hedge_fund_sentiment',
            'tailwinds', 'headwinds', 'unusual_activity',
            'guidance_history', 'macro_sector', 'reddit_data',
            'raw_response', 'confidence'
        ]

        for field in required_fields:
            assert field in result, f"Missing field: {field}"

        assert result['ticker'] == 'EMPTY'
        assert result['overall_sentiment'] == 'unknown'
        assert result['confidence'] == 'low'
