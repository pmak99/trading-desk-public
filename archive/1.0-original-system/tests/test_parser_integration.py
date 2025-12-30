"""
Integration tests for sentiment and strategy parsers.

Tests complete parsing pipeline with realistic AI responses.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.ai.sentiment_analyzer import SentimentAnalyzer
from src.ai.strategy_generator import StrategyGenerator


class TestSentimentAnalyzerIntegration:
    """Integration tests for sentiment analyzer."""

    @pytest.fixture
    def analyzer(self):
        """Create sentiment analyzer with mocked dependencies."""
        with patch('src.ai.sentiment_analyzer.RedditScraper'):
            analyzer = SentimentAnalyzer()
            # Mock Reddit scraper
            analyzer.reddit_scraper.get_ticker_sentiment = Mock(return_value={
                'posts_found': 15,
                'avg_score': 125.3,
                'total_comments': 450,
                'sentiment_score': 0.65,
                'top_posts': [
                    {'title': 'NVDA earnings play', 'score': 250, 'num_comments': 85},
                    {'title': 'Bullish on NVDA', 'score': 180, 'num_comments': 62}
                ]
            })
            return analyzer

    def test_analyze_with_json_response(self, analyzer):
        """Test full analysis pipeline with JSON response."""
        # Mock AI response (JSON format)
        mock_response = {
            'content': json.dumps({
                'overall_sentiment': 'bullish',
                'sentiment_summary': 'Strong momentum ahead of earnings',
                'retail_sentiment': 'Very bullish with heavy call buying',
                'institutional_sentiment': 'Increasing positions, bullish outlook',
                'hedge_fund_sentiment': 'Mixed, some taking profits',
                'tailwinds': [
                    'AI chip demand accelerating',
                    'Data center growth strong',
                    'New product launches'
                ],
                'headwinds': [
                    'High valuation concerns',
                    'Competition from AMD',
                    'Export restrictions to China'
                ],
                'unusual_activity': 'Heavy call volume at 200 strike',
                'guidance_history': 'Beat last 4 quarters, raised guidance',
                'macro_sector': 'Tech sector strength, AI tailwinds',
                'confidence': 'high'
            }),
            'model': 'sonar-pro',
            'provider': 'perplexity',
            'cost': 0.0025
        }

        with patch.object(analyzer.ai_client, 'chat_completion', return_value=mock_response):
            result = analyzer.analyze_earnings_sentiment('NVDA', '2024-11-20')

        # Verify structure
        assert result['ticker'] == 'NVDA'
        assert result['overall_sentiment'] == 'bullish'
        assert result['sentiment_summary'] == 'Strong momentum ahead of earnings'
        assert len(result['tailwinds']) == 3
        assert len(result['headwinds']) == 3
        assert result['confidence'] == 'high'
        assert 'reddit_data' in result
        assert result['reddit_data']['posts_found'] == 15

    def test_analyze_with_legacy_response(self, analyzer):
        """Test full analysis pipeline with legacy string response."""
        # Mock AI response (legacy string format)
        mock_response = {
            'content': """
OVERALL SENTIMENT: Bearish - Weak fundamentals ahead of earnings

RETAIL SENTIMENT:
Bearish positioning with increasing put buying activity

INSTITUTIONAL SENTIMENT:
Reducing positions, defensive stance ahead of earnings

HEDGE FUND SENTIMENT:
Net short positioning, expecting downside

KEY TAILWINDS:
- New product line showing promise
- Cost cutting measures underway

KEY HEADWINDS:
- Revenue declining YoY
- Market share loss to competitors
- Macro headwinds in key markets
- Regulatory pressures

UNUSUAL ACTIVITY:
Heavy put buying at 150 strike, dark pool selling

GUIDANCE HISTORY:
Missed last 2 quarters, lowered guidance twice

MACRO & SECTOR FACTORS:
Sector headwinds, rising interest rates impact
""",
            'model': 'gemini-1.5-flash',
            'provider': 'google',
            'cost': 0.0001
        }

        with patch.object(analyzer.ai_client, 'chat_completion', return_value=mock_response):
            result = analyzer.analyze_earnings_sentiment('TSLA')

        # Verify legacy parsing works
        assert result['ticker'] == 'TSLA'
        assert result['overall_sentiment'] == 'bearish'
        assert 'Bearish positioning' in result['retail_sentiment']
        assert len(result['tailwinds']) == 2
        assert len(result['headwinds']) == 4
        assert 'Heavy put buying' in result['unusual_activity']

    def test_analyze_with_malformed_json_falls_back(self, analyzer):
        """Test that malformed JSON triggers fallback to legacy parsing."""
        # Mock AI response (invalid JSON)
        mock_response = {
            'content': '{"overall_sentiment": "bullish", invalid json here',
            'model': 'sonar-pro',
            'provider': 'perplexity',
            'cost': 0.0025
        }

        with patch.object(analyzer.ai_client, 'chat_completion', return_value=mock_response):
            # Should not crash, should return empty result or fallback
            result = analyzer.analyze_earnings_sentiment('TEST')

        assert result['ticker'] == 'TEST'
        # Empty result since no valid markers found
        assert result['overall_sentiment'] in ['unknown', 'neutral']

    def test_analyze_handles_api_error(self, analyzer):
        """Test that API errors are handled gracefully."""
        with patch.object(analyzer.ai_client, 'chat_completion', side_effect=Exception('API Error')):
            with pytest.raises(Exception) as exc_info:
                analyzer.analyze_earnings_sentiment('TEST')

            assert 'API Error' in str(exc_info.value)


class TestStrategyGeneratorIntegration:
    """Integration tests for strategy generator."""

    @pytest.fixture
    def generator(self):
        """Create strategy generator."""
        return StrategyGenerator()

    @pytest.fixture
    def sample_options_data(self):
        """Sample options data for testing."""
        return {
            'iv_rank': 85.5,
            'expected_move_pct': 8.5,
            'iv_crush_ratio': 2.3,
            'current_iv': 65.2,
            'average_iv': 42.1
        }

    @pytest.fixture
    def sample_sentiment_data(self):
        """Sample sentiment data for testing."""
        return {
            'overall_sentiment': 'neutral',
            'retail_sentiment': 'Mixed sentiment across retail',
            'institutional_sentiment': 'Cautious positioning',
            'hedge_fund_sentiment': 'Waiting for catalyst',
            'tailwinds': ['Product launch', 'Revenue growth'],
            'headwinds': ['Competition', 'Valuation']
        }

    @pytest.fixture
    def sample_ticker_data(self):
        """Sample ticker data for testing."""
        return {
            'price': 195.50,
            'market_cap': 3000e9
        }

    def test_generate_with_json_response(self, generator, sample_options_data,
                                        sample_sentiment_data, sample_ticker_data):
        """Test full strategy generation with JSON response."""
        # Mock AI response (JSON format)
        mock_response = {
            'content': json.dumps({
                'strategies': [
                    {
                        'name': 'Bull Put Spread',
                        'type': 'Defined Risk',
                        'strikes': 'Short 190P / Long 185P',
                        'expiration': 'Weekly expiring 3 days post-earnings',
                        'credit_debit': '$3.50 per spread',
                        'max_profit': '$350',
                        'max_loss': '$150',
                        'breakeven': '$186.50',
                        'probability_of_profit': '75%',
                        'contract_count': '4 contracts',
                        'profitability_score': '8',
                        'risk_score': '6',
                        'rationale': 'Strong support at 185 with high IV crush expected'
                    },
                    {
                        'name': 'Iron Condor',
                        'type': 'Defined Risk',
                        'strikes': 'Short 185P/190P / Short 205C/210C',
                        'expiration': 'Weekly expiring 3 days post-earnings',
                        'credit_debit': '$5.25 per spread',
                        'max_profit': '$525',
                        'max_loss': '$475',
                        'breakeven': '$184.75 / $210.25',
                        'probability_of_profit': '65%',
                        'contract_count': '3 contracts',
                        'profitability_score': '9',
                        'risk_score': '5',
                        'rationale': 'Wide profit zone captures expected move with IV crush'
                    },
                    {
                        'name': 'Bear Call Spread',
                        'type': 'Defined Risk',
                        'strikes': 'Short 205C / Long 210C',
                        'expiration': 'Weekly expiring 3 days post-earnings',
                        'credit_debit': '$2.75 per spread',
                        'max_profit': '$275',
                        'max_loss': '$225',
                        'breakeven': '$207.75',
                        'probability_of_profit': '70%',
                        'contract_count': '5 contracts',
                        'profitability_score': '7',
                        'risk_score': '7',
                        'rationale': 'Resistance at 205, bearish sentiment above current price'
                    }
                ],
                'recommended_strategy': 1,
                'recommendation_rationale': 'Iron condor provides best probability-adjusted return with IV rank >75% favoring defined-risk spreads'
            }),
            'model': 'gpt-4o-mini',
            'provider': 'openai',
            'cost': 0.0015
        }

        with patch.object(generator.ai_client, 'chat_completion', return_value=mock_response):
            result = generator.generate_strategies(
                'NVDA',
                sample_options_data,
                sample_sentiment_data,
                sample_ticker_data
            )

        # Verify structure
        assert result['ticker'] == 'NVDA'
        assert len(result['strategies']) == 3
        assert result['recommended_strategy'] == 1
        assert result['strategies'][0]['name'] == 'Bull Put Spread'
        assert result['strategies'][1]['name'] == 'Iron Condor'
        assert result['strategies'][2]['name'] == 'Bear Call Spread'
        assert 'probability-adjusted' in result['recommendation_rationale']

    def test_generate_with_legacy_response(self, generator, sample_options_data,
                                          sample_sentiment_data, sample_ticker_data):
        """Test full strategy generation with legacy string response."""
        # Mock AI response (legacy format)
        mock_response = {
            'content': """
STRATEGY 1: Bull Put Spread
Type: Defined Risk
Strikes: Short 190P / Long 185P
Expiration: Weekly
Net Credit/Debit: $3.50
Max Profit: $350
Max Loss: $150
Breakeven: $186.50
Probability of Profit: 75%
Contract Count: 4
Profitability Score: 8
Risk Score: 6
Rationale: Strong support at 185

STRATEGY 2: Iron Condor
Type: Defined Risk
Strikes: Short 185P/190P / Short 205C/210C
Expiration: Weekly
Net Credit/Debit: $5.25
Max Profit: $525
Max Loss: $475
Breakeven: $184.75 / $210.25
Probability of Profit: 65%
Contract Count: 3
Profitability Score: 9
Risk Score: 5
Rationale: Wide profit zone

FINAL RECOMMENDATION:
I recommend Strategy 2 because it provides the best risk/reward profile for this IV environment.
""",
            'model': 'gemini-1.5-flash',
            'provider': 'google',
            'cost': 0.0001
        }

        with patch.object(generator.ai_client, 'chat_completion', return_value=mock_response):
            result = generator.generate_strategies(
                'AAPL',
                sample_options_data,
                sample_sentiment_data,
                sample_ticker_data
            )

        # Verify legacy parsing works
        assert result['ticker'] == 'AAPL'
        assert len(result['strategies']) == 2
        assert result['recommended_strategy'] == 1  # Strategy 2 = index 1
        assert result['strategies'][0]['profitability_score'] == '8'
        assert result['strategies'][1]['profitability_score'] == '9'

    def test_generate_with_single_strategy(self, generator, sample_options_data,
                                          sample_sentiment_data, sample_ticker_data):
        """Test generation with single strategy returned."""
        mock_response = {
            'content': json.dumps({
                'strategies': [
                    {
                        'name': 'Bull Put Spread',
                        'type': 'Defined Risk',
                        'strikes': 'Short 190P / Long 185P',
                        'expiration': 'Weekly',
                        'credit_debit': '$3.50',
                        'max_profit': '$350',
                        'max_loss': '$150',
                        'breakeven': '$186.50',
                        'probability_of_profit': '75%',
                        'contract_count': '4',
                        'profitability_score': '8',
                        'risk_score': '6',
                        'rationale': 'Only viable strategy in current market'
                    }
                ],
                'recommended_strategy': 0,
                'recommendation_rationale': 'Only recommended strategy'
            }),
            'model': 'gpt-4o-mini',
            'provider': 'openai',
            'cost': 0.0015
        }

        with patch.object(generator.ai_client, 'chat_completion', return_value=mock_response):
            result = generator.generate_strategies(
                'TEST',
                sample_options_data,
                sample_sentiment_data,
                sample_ticker_data
            )

        assert len(result['strategies']) == 1
        assert result['recommended_strategy'] == 0

    def test_generate_handles_api_error(self, generator, sample_options_data,
                                       sample_sentiment_data, sample_ticker_data):
        """Test that API errors are handled gracefully."""
        with patch.object(generator.ai_client, 'chat_completion', side_effect=Exception('API Error')):
            with pytest.raises(Exception) as exc_info:
                generator.generate_strategies(
                    'TEST',
                    sample_options_data,
                    sample_sentiment_data,
                    sample_ticker_data
                )

            assert 'API Error' in str(exc_info.value)
