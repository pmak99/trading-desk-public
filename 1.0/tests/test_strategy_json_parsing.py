"""
Unit tests for strategy generator JSON parsing.

Tests the new JSON-based parsing with fallback to legacy format.
"""

import json
import pytest
from src.ai.strategy_generator import StrategyGenerator


class TestStrategyJSONParsing:
    """Test JSON parsing functionality in strategy generator."""

    @pytest.fixture
    def generator(self):
        """Create strategy generator instance."""
        return StrategyGenerator()

    @pytest.fixture
    def sample_strategies(self):
        """Sample strategies for testing."""
        return [
            {
                "name": "Bull Put Spread",
                "type": "Defined Risk",
                "strikes": "Short 180P / Long 175P",
                "expiration": "Weekly expiring 3 days post-earnings",
                "credit_debit": "$3.50 per spread",
                "max_profit": "$350",
                "max_loss": "$150",
                "breakeven": "$176.50",
                "probability_of_profit": "75%",
                "contract_count": "4 contracts",
                "profitability_score": "8",
                "risk_score": "6",
                "rationale": "Strong support at 175 level with high IV crush expected"
            },
            {
                "name": "Bear Call Spread",
                "type": "Defined Risk",
                "strikes": "Short 200C / Long 205C",
                "expiration": "Weekly expiring 3 days post-earnings",
                "credit_debit": "$2.75 per spread",
                "max_profit": "$275",
                "max_loss": "$225",
                "breakeven": "$202.75",
                "probability_of_profit": "70%",
                "contract_count": "5 contracts",
                "profitability_score": "7",
                "risk_score": "7",
                "rationale": "Resistance at 200 with bearish sentiment"
            }
        ]

    def test_parse_valid_json_response(self, generator, sample_strategies):
        """Test parsing a valid JSON response."""
        json_response = json.dumps({
            'strategies': sample_strategies,
            'recommended_strategy': 0,
            'recommendation_rationale': 'Bull put spread offers best risk/reward'
        })

        result = generator._parse_strategy_response(json_response, 'NVDA')

        assert result['ticker'] == 'NVDA'
        assert len(result['strategies']) == 2
        assert result['recommended_strategy'] == 0
        assert result['strategies'][0]['name'] == 'Bull Put Spread'
        assert result['strategies'][0]['profitability_score'] == '8'
        assert 'raw_response' in result

    def test_parse_json_with_markdown_code_blocks(self, generator, sample_strategies):
        """Test parsing JSON wrapped in markdown code blocks."""
        json_data = {
            'strategies': sample_strategies[:1],
            'recommended_strategy': 0,
            'recommendation_rationale': 'Single strategy recommendation'
        }

        markdown_response = f"```json\n{json.dumps(json_data, indent=2)}\n```"

        result = generator._parse_strategy_response(markdown_response, 'AAPL')

        assert result['ticker'] == 'AAPL'
        assert len(result['strategies']) == 1
        assert result['strategies'][0]['name'] == 'Bull Put Spread'

    def test_parse_json_with_multiple_strategies(self, generator, sample_strategies):
        """Test parsing JSON with 3-4 strategies."""
        # Add 2 more strategies
        all_strategies = sample_strategies + [
            {
                "name": "Iron Condor",
                "type": "Defined Risk",
                "strikes": "Short 175P/180P / Short 200C/205C",
                "expiration": "Weekly expiring 3 days post-earnings",
                "credit_debit": "$4.25 per spread",
                "max_profit": "$425",
                "max_loss": "$575",
                "breakeven": "$175.75 / $199.25",
                "probability_of_profit": "65%",
                "contract_count": "3 contracts",
                "profitability_score": "9",
                "risk_score": "5",
                "rationale": "Wide profit zone with neutral outlook"
            },
            {
                "name": "Iron Butterfly",
                "type": "Defined Risk",
                "strikes": "Short 190P/C / Long 185P / Long 195C",
                "expiration": "Weekly expiring 3 days post-earnings",
                "credit_debit": "$3.00 per spread",
                "max_profit": "$300",
                "max_loss": "$200",
                "breakeven": "$187 / $193",
                "probability_of_profit": "60%",
                "contract_count": "6 contracts",
                "profitability_score": "7",
                "risk_score": "8",
                "rationale": "Max profit at current price with IV crush"
            }
        ]

        json_response = json.dumps({
            'strategies': all_strategies,
            'recommended_strategy': 2,
            'recommendation_rationale': 'Iron condor provides best probability-adjusted return'
        })

        result = generator._parse_strategy_response(json_response, 'TSLA')

        assert result['ticker'] == 'TSLA'
        assert len(result['strategies']) == 4
        assert result['recommended_strategy'] == 2
        assert result['strategies'][2]['name'] == 'Iron Condor'

    def test_parse_json_with_invalid_recommended_index(self, generator, sample_strategies):
        """Test that invalid recommended_strategy index defaults to 0."""
        json_response = json.dumps({
            'strategies': sample_strategies,
            'recommended_strategy': 10,  # Invalid index (out of bounds)
            'recommendation_rationale': 'Test'
        })

        result = generator._parse_strategy_response(json_response, 'TEST')
        assert result['recommended_strategy'] == 0  # Should default to 0

    def test_parse_json_with_missing_strategies_field(self, generator):
        """Test that missing 'strategies' field triggers fallback."""
        json_response = json.dumps({
            'recommended_strategy': 0,
            'recommendation_rationale': 'Test'
        })

        # Should fallback to legacy parser
        result = generator._parse_strategy_response(json_response, 'TEST')
        assert result['ticker'] == 'TEST'
        # Legacy parser won't find anything, returns empty
        assert len(result['strategies']) == 0

    def test_parse_json_with_empty_strategies_array(self, generator):
        """Test that empty strategies array triggers fallback."""
        json_response = json.dumps({
            'strategies': [],  # Empty array
            'recommended_strategy': 0,
            'recommendation_rationale': 'Test'
        })

        # Should fallback to legacy parser
        result = generator._parse_strategy_response(json_response, 'TEST')
        assert result['ticker'] == 'TEST'

    def test_parse_json_with_incomplete_strategy_fields(self, generator):
        """Test that incomplete strategy fields trigger fallback."""
        incomplete_strategy = {
            "name": "Bull Put Spread",
            "type": "Defined Risk",
            # Missing required fields: strikes, expiration, etc.
        }

        json_response = json.dumps({
            'strategies': [incomplete_strategy],
            'recommended_strategy': 0,
            'recommendation_rationale': 'Test'
        })

        # Should fallback to legacy parser
        result = generator._parse_strategy_response(json_response, 'TEST')
        assert result['ticker'] == 'TEST'

    def test_fallback_to_legacy_format(self, generator):
        """Test fallback to legacy string-based parsing."""
        legacy_response = """
STRATEGY 1: Bull Put Spread
Type: Defined Risk
Strikes: Short 180P / Long 175P
Expiration: Weekly expiring 3 days post-earnings
Net Credit/Debit: $3.50 per spread
Max Profit: $350
Max Loss: $150
Breakeven: $176.50
Probability of Profit: 75%
Contract Count: 4 contracts
Profitability Score: 8
Risk Score: 6
Rationale: Strong support at 175 level

STRATEGY 2: Bear Call Spread
Type: Defined Risk
Strikes: Short 200C / Long 205C
Expiration: Weekly expiring 3 days post-earnings
Net Credit/Debit: $2.75 per spread
Max Profit: $275
Max Loss: $225
Breakeven: $202.75
Probability of Profit: 70%
Contract Count: 5 contracts
Profitability Score: 7
Risk Score: 7
Rationale: Resistance at 200

FINAL RECOMMENDATION:
I recommend Strategy 1 because it has the best risk/reward profile.
"""

        result = generator._parse_strategy_response(legacy_response, 'NVDA')

        assert result['ticker'] == 'NVDA'
        assert len(result['strategies']) == 2
        assert result['strategies'][0]['name'] == 'Bull Put Spread'
        assert result['strategies'][0]['profitability_score'] == '8'
        assert result['strategies'][1]['name'] == 'Bear Call Spread'
        assert result['recommended_strategy'] == 0
        assert 'best risk/reward' in result['recommendation_rationale']

    def test_legacy_format_extracts_all_fields(self, generator):
        """Test that legacy format extracts all strategy fields correctly."""
        legacy_response = """
STRATEGY 1: Iron Condor
Type: Defined Risk
Strikes: Short 175P/180P / Short 200C/205C
Expiration: Weekly
Net Credit/Debit: $4.25
Max Profit: $425
Max Loss: $575
Breakeven: $175.75 / $199.25
Probability of Profit: 65%
Contract Count: 3
Profitability Score: 9
Risk Score: 5
Rationale: Wide profit zone

FINAL RECOMMENDATION:
Strategy 1 is the best choice.
"""

        result = generator._parse_legacy_format(legacy_response, 'TEST')

        assert len(result['strategies']) == 1
        strategy = result['strategies'][0]
        assert strategy['name'] == 'Iron Condor'
        assert strategy['type'] == 'Defined Risk'
        assert strategy['strikes'] == 'Short 175P/180P / Short 200C/205C'
        assert strategy['expiration'] == 'Weekly'
        assert strategy['credit_debit'] == '$4.25'
        assert strategy['max_profit'] == '$425'
        assert strategy['max_loss'] == '$575'
        assert strategy['breakeven'] == '$175.75 / $199.25'
        assert strategy['probability_of_profit'] == '65%'
        assert strategy['contract_count'] == '3'
        assert strategy['profitability_score'] == '9'
        assert strategy['risk_score'] == '5'
        assert 'Wide profit zone' in strategy['rationale']

    def test_legacy_format_finds_recommended_strategy(self, generator):
        """Test that legacy format correctly identifies recommended strategy."""
        legacy_response = """
STRATEGY 1: Strategy A
Type: Defined Risk
Strikes: Test
Expiration: Test
Net Credit/Debit: $1
Max Profit: $100
Max Loss: $50
Breakeven: $180
Probability of Profit: 70%
Contract Count: 1
Profitability Score: 7
Risk Score: 5
Rationale: Test A

STRATEGY 2: Strategy B
Type: Defined Risk
Strikes: Test
Expiration: Test
Net Credit/Debit: $1
Max Profit: $100
Max Loss: $50
Breakeven: $180
Probability of Profit: 70%
Contract Count: 1
Profitability Score: 8
Risk Score: 4
Rationale: Test B

FINAL RECOMMENDATION:
I recommend Strategy 2 because it has better scores.
"""

        result = generator._parse_legacy_format(legacy_response, 'TEST')

        assert len(result['strategies']) == 2
        assert result['recommended_strategy'] == 1  # 0-indexed, so Strategy 2 = index 1
        assert 'Strategy 2' in result['recommendation_rationale']

    def test_empty_result_has_all_required_fields(self, generator):
        """Test that empty result contains all expected fields."""
        result = generator._get_empty_result('EMPTY')

        required_fields = [
            'ticker', 'strategies', 'recommended_strategy',
            'recommendation_rationale', 'raw_response'
        ]

        for field in required_fields:
            assert field in result, f"Missing field: {field}"

        assert result['ticker'] == 'EMPTY'
        assert result['strategies'] == []
        assert result['recommended_strategy'] == 0
        assert result['recommendation_rationale'] == 'N/A'
        assert result['raw_response'] == ''
