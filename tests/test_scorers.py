"""Tests for scorer classes using Strategy pattern."""

import pytest
from src.analysis.scorers import (
    IVScorer, IVCrushEdgeScorer, LiquidityScorer,
    FundamentalsScorer, CompositeScorer, IVExpansionScorer
)


class TestIVScorer:
    """Test IV scoring."""

    @pytest.mark.parametrize("current_iv,expected_score", [
        (110.5, 100.0),  # Premium IV
        (85.0, 100.0),   # Excellent IV
        (70.0, 70.0),    # Good IV
        (50.0, 0.0),     # Filtered out
    ])
    def test_iv_scoring(self, current_iv, expected_score):
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {'ticker': 'TEST', 'options_data': {'current_iv': current_iv}}
        assert scorer.score(data) == expected_score

    def test_yfinance_fallback(self):
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {'ticker': 'TEST', 'iv': 0.65}
        assert scorer.score(data) == 80.0

    def test_weighted_score(self):
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {'ticker': 'TEST', 'options_data': {'current_iv': 100.0}}
        assert scorer.weighted_score(data) == 50.0


class TestIVCrushEdgeScorer:
    """Test IV crush edge scoring."""

    @pytest.mark.parametrize("ratio,expected_score", [
        (1.4, 100.0),   # Excellent
        (1.25, 80.0),   # Good
        (1.15, 60.0),   # Moderate
        (1.05, 40.0),   # Slight
        (0.9, 0.0),     # No edge
        (None, 50.0),   # Missing data
    ])
    def test_crush_edge_scoring(self, ratio, expected_score):
        scorer = IVCrushEdgeScorer(weight=0.30)
        options_data = {'iv_crush_ratio': ratio} if ratio is not None else {}
        data = {'options_data': options_data}
        assert scorer.score(data) == expected_score


class TestLiquidityScorer:
    """Test liquidity scoring."""

    def test_high_liquidity(self):
        scorer = LiquidityScorer(weight=0.15)
        data = {'options_data': {
            'options_volume': 60000,
            'open_interest': 120000,
            'bid_ask_spread_pct': 0.015
        }}
        assert scorer.score(data) == 100.0

    def test_low_liquidity(self):
        scorer = LiquidityScorer(weight=0.15)
        data = {'options_data': {
            'options_volume': 1000,
            'open_interest': 5500,
            'bid_ask_spread_pct': 0.15
        }}
        assert scorer.score(data) == 36.0

    def test_missing_spread(self):
        scorer = LiquidityScorer(weight=0.15)
        data = {'options_data': {
            'options_volume': 10000,
            'open_interest': 50000
        }}
        assert scorer.score(data) == 74.0


class TestFundamentalsScorer:
    """Test fundamentals scoring."""

    @pytest.mark.parametrize("market_cap,price,expected_score", [
        (250e9, 150.0, 100.0),  # Mega cap, ideal price
        (75e9, 450.0, 80.0),    # Large cap, acceptable price
        (15e9, 10.0, 55.0),     # Mid cap, low price
    ])
    def test_fundamentals_scoring(self, market_cap, price, expected_score):
        scorer = FundamentalsScorer(weight=0.05)
        data = {'market_cap': market_cap, 'price': price}
        assert scorer.score(data) == expected_score


class TestCompositeScorer:
    """Test composite scoring."""

    def test_default_scorers_initialized(self):
        scorer = CompositeScorer()
        assert len(scorer.scorers) == 5
        assert isinstance(scorer.scorers[0], IVExpansionScorer)
        assert isinstance(scorer.scorers[1], LiquidityScorer)
        assert isinstance(scorer.scorers[2], IVCrushEdgeScorer)
        assert isinstance(scorer.scorers[3], IVScorer)
        assert isinstance(scorer.scorers[4], FundamentalsScorer)

    def test_custom_scorers(self):
        custom_scorers = [IVScorer(weight=1.0)]
        scorer = CompositeScorer(scorers=custom_scorers)
        assert len(scorer.scorers) == 1

    def test_high_quality_ticker(self):
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'NVDA',
            'price': 150.0,
            'market_cap': 500e9,
            'options_data': {
                'current_iv': 95.0,
                'iv_crush_ratio': 1.35,
                'options_volume': 80000,
                'open_interest': 150000,
                'bid_ask_spread_pct': 0.01
            }
        }
        assert scorer.calculate_score(data) >= 90.0

    def test_low_iv_filtered(self):
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'TEST',
            'price': 100.0,
            'market_cap': 50e9,
            'options_data': {'current_iv': 40.0}
        }
        assert scorer.calculate_score(data) == 0.0

    def test_medium_quality_ticker(self):
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'TEST',
            'price': 75.0,
            'market_cap': 20e9,
            'options_data': {
                'current_iv': 65.0,
                'iv_crush_ratio': 1.05,
                'options_volume': 5000,
                'open_interest': 10000,
                'bid_ask_spread_pct': 0.08
            }
        }
        score = scorer.calculate_score(data)
        assert 42.0 <= score <= 45.0

    def test_weight_distribution(self):
        scorer = CompositeScorer()
        total_weight = sum(s.weight for s in scorer.scorers)
        assert total_weight == 1.20

    def test_missing_options_data(self):
        scorer = CompositeScorer(min_iv=60)
        data = {'ticker': 'TEST', 'price': 100.0, 'market_cap': 50e9}
        assert scorer.calculate_score(data) == 0.0

    def test_score_rounding(self):
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'TEST',
            'price': 100.0,
            'market_cap': 50e9,
            'options_data': {
                'current_iv': 75.5,
                'iv_crush_ratio': 1.25,
                'options_volume': 15000,
                'open_interest': 60000,
                'bid_ask_spread_pct': 0.03
            }
        }
        score = scorer.calculate_score(data)
        assert score == round(score, 2)


class TestScorerIntegration:
    """Integration tests."""

    def test_nvda_like_ticker(self):
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'NVDA',
            'price': 450.0,
            'market_cap': 1.1e12,
            'options_data': {
                'current_iv': 88.5,
                'iv_rank': 72.0,
                'iv_crush_ratio': 1.28,
                'options_volume': 125000,
                'open_interest': 450000,
                'bid_ask_spread_pct': 0.012,
                'expected_move_pct': 7.5
            }
        }
        assert scorer.calculate_score(data) > 85.0

    def test_low_iv_filtered(self):
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'KO',
            'price': 58.0,
            'market_cap': 250e9,
            'options_data': {
                'current_iv': 18.5,
                'options_volume': 35000,
                'open_interest': 120000
            }
        }
        assert scorer.calculate_score(data) == 0.0

    def test_performance(self):
        import time
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'TEST',
            'price': 100.0,
            'market_cap': 50e9,
            'options_data': {
                'current_iv': 75.0,
                'iv_crush_ratio': 1.2,
                'options_volume': 10000,
                'open_interest': 50000,
                'bid_ask_spread_pct': 0.05
            }
        }
        start = time.time()
        for _ in range(1000):
            scorer.calculate_score(data)
        assert time.time() - start < 1.0
