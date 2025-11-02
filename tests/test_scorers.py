"""
Comprehensive tests for scorer classes (Strategy pattern implementation).

Tests all scorer components:
- IVScorer: Primary filter (50% weight)
- IVCrushEdgeScorer: IV crush edge scoring (30% weight)
- LiquidityScorer: Options liquidity scoring (15% weight)
- FundamentalsScorer: Fundamentals scoring (5% weight)
- CompositeScorer: Combined scoring system
"""

import pytest
from src.analysis.scorers import (
    IVScorer,
    IVCrushEdgeScorer,
    LiquidityScorer,
    FundamentalsScorer,
    CompositeScorer
)


class TestIVScorer:
    """Test IV (Implied Volatility) scoring."""

    def test_high_current_iv_premium(self):
        """Test scoring with premium IV (100%+)."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'options_data': {
                'current_iv': 110.5
            }
        }
        score = scorer.score(data)
        assert score == 100.0, "Premium IV (100%+) should score 100"

    def test_high_current_iv_excellent(self):
        """Test scoring with excellent IV (80-100%)."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'options_data': {
                'current_iv': 85.0
            }
        }
        score = scorer.score(data)
        assert score == 85.0, "IV 85% should score 85"

    def test_high_current_iv_good(self):
        """Test scoring with good IV (60-80%)."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'options_data': {
                'current_iv': 70.0
            }
        }
        score = scorer.score(data)
        assert score == 70.0, "IV 70% should score 70"

    def test_low_current_iv_filtered(self):
        """Test that low IV is filtered out (hard filter)."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'options_data': {
                'current_iv': 50.0
            }
        }
        score = scorer.score(data)
        assert score == 0.0, "IV below 60% should be filtered (score 0)"

    def test_iv_rank_excellent(self):
        """Test scoring with excellent IV Rank (75%+)."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'options_data': {
                'iv_rank': 80.0
            }
        }
        score = scorer.score(data)
        assert score == 100.0, "IV Rank 80% should score 100"

    def test_iv_rank_good(self):
        """Test scoring with good IV Rank (60-75%)."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'options_data': {
                'iv_rank': 65.0
            }
        }
        score = scorer.score(data)
        assert score == 80.0, "IV Rank 65% should score 80"

    def test_iv_rank_filtered(self):
        """Test that low IV Rank is filtered out."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'options_data': {
                'iv_rank': 45.0
            }
        }
        score = scorer.score(data)
        assert score == 0.0, "IV Rank below 50% should be filtered"

    def test_yfinance_iv_fallback(self):
        """Test fallback to yfinance IV estimate."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'iv': 0.65  # yfinance format (0.65 = 65%)
        }
        score = scorer.score(data)
        assert score == 80.0, "yfinance IV 0.65 should score 80"

    def test_weighted_score(self):
        """Test weighted score calculation."""
        scorer = IVScorer(weight=0.50, min_iv=60)
        data = {
            'ticker': 'TEST',
            'options_data': {
                'current_iv': 100.0
            }
        }
        weighted = scorer.weighted_score(data)
        assert weighted == 50.0, "100 score * 0.50 weight = 50"


class TestIVCrushEdgeScorer:
    """Test IV crush edge scoring."""

    def test_excellent_edge(self):
        """Test excellent IV crush edge (1.3+ ratio)."""
        scorer = IVCrushEdgeScorer(weight=0.30)
        data = {
            'options_data': {
                'iv_crush_ratio': 1.4  # Implied 40% higher than actual
            }
        }
        score = scorer.score(data)
        assert score == 100.0, "Ratio 1.4 should score 100"

    def test_good_edge(self):
        """Test good IV crush edge (1.2-1.3 ratio)."""
        scorer = IVCrushEdgeScorer(weight=0.30)
        data = {
            'options_data': {
                'iv_crush_ratio': 1.25
            }
        }
        score = scorer.score(data)
        assert score == 80.0, "Ratio 1.25 should score 80"

    def test_moderate_edge(self):
        """Test moderate IV crush edge (1.1-1.2 ratio)."""
        scorer = IVCrushEdgeScorer(weight=0.30)
        data = {
            'options_data': {
                'iv_crush_ratio': 1.15
            }
        }
        score = scorer.score(data)
        assert score == 60.0, "Ratio 1.15 should score 60"

    def test_no_edge(self):
        """Test no IV crush edge (ratio < 1.0)."""
        scorer = IVCrushEdgeScorer(weight=0.30)
        data = {
            'options_data': {
                'iv_crush_ratio': 0.9
            }
        }
        score = scorer.score(data)
        assert score == 0.0, "Ratio < 1.0 should score 0"

    def test_missing_data(self):
        """Test scoring with missing IV crush data."""
        scorer = IVCrushEdgeScorer(weight=0.30)
        data = {'options_data': {}}
        score = scorer.score(data)
        assert score == 50.0, "Missing data should return neutral score (50)"


class TestLiquidityScorer:
    """Test liquidity scoring."""

    def test_high_liquidity(self):
        """Test scoring with high liquidity."""
        scorer = LiquidityScorer(weight=0.15)
        data = {
            'options_data': {
                'options_volume': 60000,  # Very high
                'open_interest': 120000,  # Very liquid
                'bid_ask_spread_pct': 0.015  # Excellent spread
            }
        }
        score = scorer.score(data)
        # (100 * 0.4) + (100 * 0.4) + (100 * 0.2) = 100
        assert score == 100.0, "High liquidity should score 100"

    def test_low_liquidity(self):
        """Test scoring with low liquidity."""
        scorer = LiquidityScorer(weight=0.15)
        data = {
            'options_data': {
                'options_volume': 500,
                'open_interest': 2000,
                'bid_ask_spread_pct': 0.15  # Wide spread
            }
        }
        score = scorer.score(data)
        # (20 * 0.4) + (20 * 0.4) + (20 * 0.2) = 20
        assert score == 20.0, "Low liquidity should score 20"

    def test_missing_spread_data(self):
        """Test scoring with missing spread data."""
        scorer = LiquidityScorer(weight=0.15)
        data = {
            'options_data': {
                'options_volume': 10000,
                'open_interest': 50000
            }
        }
        score = scorer.score(data)
        # (80 * 0.4) + (80 * 0.4) + (50 * 0.2) = 74
        assert score == 74.0, "Missing spread should use neutral score (50)"


class TestFundamentalsScorer:
    """Test fundamentals scoring."""

    def test_mega_cap_ideal_price(self):
        """Test mega cap with ideal price."""
        scorer = FundamentalsScorer(weight=0.05)
        data = {
            'market_cap': 250e9,  # $250B mega cap
            'price': 150.0  # Ideal range
        }
        score = scorer.score(data)
        assert score == 100.0, "Mega cap with ideal price should score 100"

    def test_large_cap_acceptable_price(self):
        """Test large cap with acceptable price."""
        scorer = FundamentalsScorer(weight=0.05)
        data = {
            'market_cap': 75e9,  # $75B large cap
            'price': 450.0  # Acceptable
        }
        score = scorer.score(data)
        # (80 + 80) / 2 = 80
        assert score == 80.0, "Large cap acceptable price should score 80"

    def test_mid_cap_low_price(self):
        """Test mid cap with low price."""
        scorer = FundamentalsScorer(weight=0.05)
        data = {
            'market_cap': 15e9,  # $15B mid cap
            'price': 10.0  # Low
        }
        score = scorer.score(data)
        # (60 + 50) / 2 = 55
        assert score == 55.0, "Mid cap low price should score 55"


class TestCompositeScorer:
    """Test composite scoring system."""

    def test_default_scorers(self):
        """Test that default scorers are initialized correctly."""
        scorer = CompositeScorer()
        assert len(scorer.scorers) == 4, "Should have 4 default scorers"
        assert isinstance(scorer.scorers[0], IVScorer)
        assert isinstance(scorer.scorers[1], IVCrushEdgeScorer)
        assert isinstance(scorer.scorers[2], LiquidityScorer)
        assert isinstance(scorer.scorers[3], FundamentalsScorer)

    def test_custom_scorers(self):
        """Test initialization with custom scorers."""
        custom_scorers = [IVScorer(weight=1.0)]
        scorer = CompositeScorer(scorers=custom_scorers)
        assert len(scorer.scorers) == 1, "Should have 1 custom scorer"

    def test_high_score_ticker(self):
        """Test scoring for high-quality ticker."""
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'NVDA',
            'price': 150.0,
            'market_cap': 500e9,
            'options_data': {
                'current_iv': 95.0,  # Excellent IV
                'iv_crush_ratio': 1.35,  # Excellent edge
                'options_volume': 80000,  # High liquidity
                'open_interest': 150000,
                'bid_ask_spread_pct': 0.01
            }
        }
        score = scorer.calculate_score(data)
        assert score >= 90.0, f"High-quality ticker should score >= 90, got {score}"

    def test_filtered_ticker(self):
        """Test that low IV ticker is filtered out."""
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'TEST',
            'price': 100.0,
            'market_cap': 50e9,
            'options_data': {
                'current_iv': 40.0  # Below minimum
            }
        }
        score = scorer.calculate_score(data)
        assert score == 0.0, "Ticker with IV < 60% should be filtered (score 0)"

    def test_medium_score_ticker(self):
        """Test scoring for medium-quality ticker."""
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'TEST',
            'price': 75.0,
            'market_cap': 20e9,
            'options_data': {
                'current_iv': 65.0,  # Barely above minimum
                'iv_crush_ratio': 1.05,  # Slight edge
                'options_volume': 5000,  # Moderate liquidity
                'open_interest': 10000,
                'bid_ask_spread_pct': 0.08
            }
        }
        score = scorer.calculate_score(data)
        assert 40.0 <= score <= 60.0, f"Medium ticker should score 40-60, got {score}"

    def test_weight_distribution(self):
        """Test that weights are properly distributed."""
        scorer = CompositeScorer()
        total_weight = sum(s.weight for s in scorer.scorers)
        assert total_weight == 1.0, "Weights should sum to 1.0"

    def test_missing_options_data(self):
        """Test scoring with missing options data."""
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'TEST',
            'price': 100.0,
            'market_cap': 50e9
        }
        score = scorer.calculate_score(data)
        # Missing options data falls back to yfinance IV estimate (30 score)
        # Combined with fundamentals (5% weight), gives low but non-zero score
        # This is a low-confidence score indicating ticker needs real options data
        assert 0 < score < 50, f"Missing options_data should give low score, got {score}"

    def test_score_rounding(self):
        """Test that scores are properly rounded to 2 decimal places."""
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
        # Check that it's rounded to 2 decimal places
        assert score == round(score, 2), "Score should be rounded to 2 decimals"


class TestScorerIntegration:
    """Integration tests for scorer system."""

    def test_real_world_scenario_nvda(self):
        """Test with realistic NVDA-like data."""
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'NVDA',
            'price': 450.0,
            'market_cap': 1.1e12,  # $1.1T
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
        score = scorer.calculate_score(data)
        assert score > 85.0, f"NVDA-like ticker should score > 85, got {score}"

    def test_real_world_scenario_low_iv(self):
        """Test with realistic low-IV ticker."""
        scorer = CompositeScorer(min_iv=60)
        data = {
            'ticker': 'KO',  # Coca-Cola - typically low IV
            'price': 58.0,
            'market_cap': 250e9,
            'options_data': {
                'current_iv': 18.5,  # Low IV
                'options_volume': 35000,
                'open_interest': 120000
            }
        }
        score = scorer.calculate_score(data)
        assert score == 0.0, "Low IV ticker should be filtered"

    def test_performance_with_many_tickers(self):
        """Test that scoring is fast enough for batch processing."""
        import time

        scorer = CompositeScorer(min_iv=60)
        sample_data = {
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
            scorer.calculate_score(sample_data)
        elapsed = time.time() - start

        assert elapsed < 1.0, f"Scoring 1000 tickers should take < 1s, took {elapsed:.2f}s"
