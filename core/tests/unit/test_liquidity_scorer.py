"""
Unit tests for the 4-tier liquidity scoring system.

4-Tier System:
- EXCELLENT: OI >= 5x position, spread <= 8%  → 20 points
- GOOD:      OI 2-5x position, spread 8-12%  → 16 points
- WARNING:   OI 1-2x position, spread 12-15% → 12 points
- REJECT:    OI < 1x position, spread > 15%  → 4 points

Final tier = worse of (OI tier, Spread tier)
"""

import pytest
import sys
from pathlib import Path
from datetime import date, timedelta

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.application.metrics.liquidity_scorer import LiquidityScorer, LiquidityScore
from src.domain.types import Money, Percentage, Strike, OptionQuote, OptionChain


@pytest.fixture
def scorer():
    """Default liquidity scorer with standard thresholds."""
    return LiquidityScorer(
        # OI thresholds
        min_oi=50,          # REJECT below this
        warning_oi=100,     # WARNING tier threshold
        good_oi=500,        # GOOD tier threshold
        excellent_oi=1000,  # EXCELLENT tier threshold
        # Volume thresholds
        min_volume=20,
        good_volume=100,
        excellent_volume=250,
        # Spread thresholds (4-tier)
        max_spread_pct=15.0,        # REJECT threshold
        warning_spread_pct=12.0,    # WARNING threshold
        good_spread_pct=8.0,        # GOOD threshold
        excellent_spread_pct=5.0,   # EXCELLENT threshold
    )


def make_option(
    bid: float = 2.00,
    ask: float = 2.10,
    oi: int = 500,
    volume: int = 100,
) -> OptionQuote:
    """Create a test option quote."""
    return OptionQuote(
        bid=Money(bid),
        ask=Money(ask),
        implied_volatility=Percentage(30.0),
        open_interest=oi,
        volume=volume,
    )


class TestSpreadCalculation:
    """Tests for bid-ask spread percentage calculation."""

    def test_normal_spread(self, scorer):
        """Normal bid-ask spread calculation."""
        option = make_option(bid=2.00, ask=2.10)
        spread_pct = scorer.calculate_spread_pct(option)
        # Spread = 0.10, Mid = 2.05, Spread% = 0.10/2.05 * 100 ≈ 4.88%
        assert 4.8 < spread_pct < 5.0

    def test_wide_spread(self, scorer):
        """Wide spread calculation."""
        option = make_option(bid=1.00, ask=1.20)
        spread_pct = scorer.calculate_spread_pct(option)
        # Spread = 0.20, Mid = 1.10, Spread% = 0.20/1.10 * 100 ≈ 18.2%
        assert 18.0 < spread_pct < 19.0

    def test_tight_spread(self, scorer):
        """Tight spread calculation."""
        option = make_option(bid=5.00, ask=5.05)
        spread_pct = scorer.calculate_spread_pct(option)
        # Spread = 0.05, Mid = 5.025, Spread% ≈ 1.0%
        assert 0.9 < spread_pct < 1.1

    def test_no_bid_ask(self, scorer):
        """Missing bid/ask returns 100%."""
        option = OptionQuote(
            bid=None,
            ask=None,
            implied_volatility=Percentage(30.0),
            open_interest=100,
            volume=50,
        )
        spread_pct = scorer.calculate_spread_pct(option)
        assert spread_pct == 100.0


class TestBasicTierClassification:
    """Tests for 4-tier classification (EXCELLENT/GOOD/WARNING/REJECT)."""

    def test_excellent_all_metrics(self, scorer):
        """All metrics excellent → EXCELLENT."""
        # OI >= 1000 (excellent), spread <= 8% (excellent)
        tier = scorer._classify_tier(oi=1000, volume=250, spread_pct=3.0)
        assert tier == "EXCELLENT"

    def test_good_tier_oi_and_spread(self, scorer):
        """Good OI (500-1000) + good spread (<=8%) → GOOD."""
        tier = scorer._classify_tier(oi=700, volume=150, spread_pct=6.0)
        assert tier == "GOOD"

    def test_good_tier_spread_demotes(self, scorer):
        """Excellent OI + good spread (8-12%) → GOOD (spread demotes)."""
        tier = scorer._classify_tier(oi=1000, volume=250, spread_pct=10.0)
        assert tier == "GOOD"

    def test_warning_tier_oi(self, scorer):
        """Warning OI (100-500) + tight spread → WARNING."""
        tier = scorer._classify_tier(oi=200, volume=50, spread_pct=5.0)
        assert tier == "WARNING"

    def test_warning_tier_spread(self, scorer):
        """Excellent OI + warning spread (12-15%) → WARNING."""
        tier = scorer._classify_tier(oi=1000, volume=250, spread_pct=14.0)
        assert tier == "WARNING"

    def test_reject_low_oi(self, scorer):
        """OI below minimum → REJECT."""
        tier = scorer._classify_tier(oi=30, volume=100, spread_pct=5.0)
        assert tier == "REJECT"

    def test_reject_low_volume(self, scorer):
        """Volume below minimum → REJECT."""
        tier = scorer._classify_tier(oi=1000, volume=10, spread_pct=5.0)
        assert tier == "REJECT"

    def test_reject_wide_spread(self, scorer):
        """Spread above maximum (>15%) → REJECT."""
        tier = scorer._classify_tier(oi=1000, volume=250, spread_pct=20.0)
        assert tier == "REJECT"

    def test_tier_is_worse_of_two(self, scorer):
        """Final tier is worse of OI tier and spread tier."""
        # Excellent OI, Warning spread → WARNING
        tier = scorer._classify_tier(oi=1000, volume=250, spread_pct=13.0)
        assert tier == "WARNING"

        # Warning OI, Excellent spread → WARNING
        tier = scorer._classify_tier(oi=200, volume=50, spread_pct=5.0)
        assert tier == "WARNING"


class TestHybridTierClassification:
    """Tests for 4-tier hybrid classification (EXCELLENT/GOOD/WARNING/REJECT)."""

    @pytest.fixture
    def sample_chain(self):
        """Create a sample option chain for testing."""
        stock_price = Money(100.0)
        strikes = [Strike(90), Strike(95), Strike(100), Strike(105), Strike(110)]

        # Create calls with varying liquidity
        calls = {
            Strike(90): make_option(bid=11.00, ask=11.20, oi=2000, volume=300),
            Strike(95): make_option(bid=6.50, ask=6.70, oi=1500, volume=200),
            Strike(100): make_option(bid=3.00, ask=3.15, oi=1000, volume=150),
            Strike(105): make_option(bid=1.00, ask=1.10, oi=800, volume=100),
            Strike(110): make_option(bid=0.30, ask=0.40, oi=500, volume=50),
        }

        # Create puts with varying liquidity
        puts = {
            Strike(90): make_option(bid=0.30, ask=0.40, oi=500, volume=50),
            Strike(95): make_option(bid=1.00, ask=1.10, oi=800, volume=100),
            Strike(100): make_option(bid=3.00, ask=3.15, oi=1000, volume=150),
            Strike(105): make_option(bid=6.50, ask=6.70, oi=1500, volume=200),
            Strike(110): make_option(bid=11.00, ask=11.20, oi=2000, volume=300),
        }

        return OptionChain(
            ticker="TEST",
            expiration=date.today() + timedelta(days=7),
            stock_price=stock_price,
            calls=calls,
            puts=puts,
        )

    def test_hybrid_excellent_tier(self, scorer, sample_chain):
        """High OI + tight spread → EXCELLENT."""
        tier, details = scorer.classify_hybrid_tier(
            chain=sample_chain,
            implied_move_pct=5.0,  # Small move, ATM options
            stock_price=100.0,
            use_dynamic_thresholds=False,
        )
        # With good OI and tight spreads, should be EXCELLENT or GOOD
        assert tier in ("EXCELLENT", "GOOD")
        assert details['method'] is not None

    def test_dynamic_thresholds_calculation(self, scorer):
        """Test dynamic threshold calculation based on stock price."""
        # Low price stock
        thresholds_20 = scorer.calculate_dynamic_thresholds(stock_price=20.0)
        assert thresholds_20['spread_width'] == 5.0
        assert thresholds_20['price_tier'] == "$20-100"

        # Mid price stock
        thresholds_150 = scorer.calculate_dynamic_thresholds(stock_price=150.0)
        assert thresholds_150['spread_width'] == 10.0
        assert thresholds_150['price_tier'] == "$100-200"

        # High price stock
        thresholds_500 = scorer.calculate_dynamic_thresholds(stock_price=500.0)
        assert thresholds_500['spread_width'] == 50.0
        assert thresholds_500['price_tier'] == "$500-1000"

        # Very high price stock
        thresholds_1500 = scorer.calculate_dynamic_thresholds(stock_price=1500.0)
        assert thresholds_1500['spread_width'] == 100.0
        assert thresholds_1500['price_tier'] == "$1000+"


class TestStraddleTierClassification:
    """Tests for straddle liquidity tier (uses worse of two legs)."""

    def test_both_excellent(self, scorer):
        """Both legs excellent → EXCELLENT."""
        call = make_option(bid=3.00, ask=3.05, oi=1000, volume=250)
        put = make_option(bid=3.00, ask=3.05, oi=1000, volume=250)
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "EXCELLENT"

    def test_both_good(self, scorer):
        """Both legs good → GOOD."""
        call = make_option(bid=3.00, ask=3.05, oi=700, volume=150)   # Good OI
        put = make_option(bid=3.00, ask=3.05, oi=600, volume=150)    # Good OI
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "GOOD"

    def test_excellent_and_good(self, scorer):
        """One excellent, one good → GOOD (worse of two)."""
        call = make_option(bid=3.00, ask=3.05, oi=1000, volume=250)  # Excellent
        put = make_option(bid=3.00, ask=3.05, oi=600, volume=150)    # Good
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "GOOD"

    def test_one_warning(self, scorer):
        """One leg warning → WARNING."""
        call = make_option(bid=3.00, ask=3.05, oi=1000, volume=250)  # Excellent
        put = make_option(bid=3.00, ask=3.05, oi=200, volume=50)     # Warning (OI 100-500)
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "WARNING"

    def test_one_reject(self, scorer):
        """One leg reject → REJECT."""
        call = make_option(bid=3.00, ask=3.05, oi=1000, volume=250)  # Excellent
        put = make_option(bid=3.00, ask=3.05, oi=30, volume=50)      # Reject (OI < 50)
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "REJECT"

    def test_both_reject(self, scorer):
        """Both legs reject → REJECT."""
        call = make_option(bid=3.00, ask=4.00, oi=30, volume=10)     # Reject
        put = make_option(bid=3.00, ask=4.00, oi=30, volume=10)      # Reject
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "REJECT"


class TestOIOnlyMode:
    """Tests for OI-only mode (used when markets are closed)."""

    def test_oi_only_excellent(self, scorer):
        """High OI + tight spread in OI-only mode → EXCELLENT."""
        tier = scorer._classify_tier_oi_only(oi=1000, spread_pct=3.0)
        assert tier == "EXCELLENT"

    def test_oi_only_good(self, scorer):
        """Good OI + good spread in OI-only mode → GOOD."""
        tier = scorer._classify_tier_oi_only(oi=700, spread_pct=6.0)
        assert tier == "GOOD"

    def test_oi_only_good_spread_demotes(self, scorer):
        """Excellent OI + good spread (8-12%) → GOOD."""
        tier = scorer._classify_tier_oi_only(oi=1000, spread_pct=10.0)
        assert tier == "GOOD"

    def test_oi_only_warning_oi(self, scorer):
        """Warning OI (100-500) + tight spread → WARNING."""
        tier = scorer._classify_tier_oi_only(oi=200, spread_pct=5.0)
        assert tier == "WARNING"

    def test_oi_only_warning_spread(self, scorer):
        """Excellent OI + warning spread (12-15%) → WARNING."""
        tier = scorer._classify_tier_oi_only(oi=1000, spread_pct=13.0)
        assert tier == "WARNING"

    def test_oi_only_reject_low_oi(self, scorer):
        """Low OI in OI-only mode → REJECT."""
        tier = scorer._classify_tier_oi_only(oi=30, spread_pct=5.0)
        assert tier == "REJECT"

    def test_oi_only_reject_wide_spread(self, scorer):
        """Wide spread in OI-only mode → REJECT."""
        tier = scorer._classify_tier_oi_only(oi=1000, spread_pct=20.0)
        assert tier == "REJECT"


class TestScoreCalculation:
    """Tests for individual score components."""

    def test_oi_score_excellent(self, scorer):
        """Excellent OI → 100 points."""
        score = scorer._score_open_interest(1000)
        assert score == 100.0

    def test_oi_score_good(self, scorer):
        """Good OI → 80-100 points."""
        score = scorer._score_open_interest(500)
        assert score == 80.0

    def test_oi_score_marginal(self, scorer):
        """Marginal OI → 50-80 points."""
        score = scorer._score_open_interest(200)
        assert 50.0 < score < 80.0

    def test_oi_score_poor(self, scorer):
        """Poor OI → 0-50 points."""
        score = scorer._score_open_interest(25)
        assert 0.0 < score < 50.0

    def test_spread_score_excellent(self, scorer):
        """Tight spread → 100 points."""
        score = scorer._score_spread(3.0)
        assert score == 100.0

    def test_spread_score_good(self, scorer):
        """Good spread → 80-100 points."""
        score = scorer._score_spread(7.0)
        assert 80.0 < score < 100.0

    def test_spread_score_marginal(self, scorer):
        """Marginal spread → 50-80 points."""
        score = scorer._score_spread(12.0)
        assert 50.0 <= score < 80.0

    def test_spread_score_poor(self, scorer):
        """Wide spread → 0-50 points."""
        score = scorer._score_spread(20.0)
        assert score < 50.0


class TestCompositeScoring:
    """Tests for full option scoring."""

    def test_excellent_option(self, scorer):
        """Excellent option gets high composite score."""
        option = make_option(
            bid=3.00, ask=3.05,  # Tight spread ~1.6%
            oi=1500,            # Excellent OI
            volume=300,         # Excellent volume
        )
        score = scorer.score_option(option)

        assert score.overall_score >= 90.0
        assert score.liquidity_tier == "EXCELLENT"
        assert score.is_liquid is True

    def test_good_option(self, scorer):
        """Good-level option gets good score."""
        option = make_option(
            bid=3.00, ask=3.08,  # ~2.6% spread (excellent)
            oi=700,             # Good OI (500-1000)
            volume=150,         # Good volume
        )
        score = scorer.score_option(option)

        assert 70.0 <= score.overall_score < 95.0
        assert score.liquidity_tier == "GOOD"
        assert score.is_liquid is True

    def test_warning_option(self, scorer):
        """Warning-level option gets moderate score."""
        option = make_option(
            bid=3.00, ask=3.10,  # ~3.3% spread (excellent)
            oi=200,             # Warning OI (100-500)
            volume=50,          # Warning volume
        )
        score = scorer.score_option(option)

        assert 50.0 <= score.overall_score < 80.0
        assert score.liquidity_tier == "WARNING"
        assert score.is_liquid is True

    def test_reject_option(self, scorer):
        """Reject-level option gets low score."""
        option = make_option(
            bid=1.00, ask=1.30,  # ~26% spread
            oi=30,              # Below min OI
            volume=10,          # Below min volume
        )
        score = scorer.score_option(option)

        assert score.overall_score < 50.0
        assert score.liquidity_tier == "REJECT"
        assert score.is_liquid is False


class TestScanScoringConstants:
    """Tests for scan.py scoring constants (4-tier system)."""

    def test_import_constants(self):
        """Verify all 4-tier constants are defined."""
        from scripts.scan import (
            SCORE_LIQUIDITY_MAX_POINTS,
            SCORE_LIQUIDITY_EXCELLENT_POINTS,
            SCORE_LIQUIDITY_GOOD_POINTS,
            SCORE_LIQUIDITY_WARNING_POINTS,
            SCORE_LIQUIDITY_REJECT_POINTS,
        )

        assert SCORE_LIQUIDITY_MAX_POINTS == 20
        assert SCORE_LIQUIDITY_EXCELLENT_POINTS == 20
        assert SCORE_LIQUIDITY_GOOD_POINTS == 16
        assert SCORE_LIQUIDITY_WARNING_POINTS == 12
        assert SCORE_LIQUIDITY_REJECT_POINTS == 4

    def test_tier_ordering(self):
        """Verify tier scores decrease properly."""
        from scripts.scan import (
            SCORE_LIQUIDITY_EXCELLENT_POINTS,
            SCORE_LIQUIDITY_GOOD_POINTS,
            SCORE_LIQUIDITY_WARNING_POINTS,
            SCORE_LIQUIDITY_REJECT_POINTS,
        )

        assert SCORE_LIQUIDITY_EXCELLENT_POINTS > SCORE_LIQUIDITY_GOOD_POINTS
        assert SCORE_LIQUIDITY_GOOD_POINTS > SCORE_LIQUIDITY_WARNING_POINTS
        assert SCORE_LIQUIDITY_WARNING_POINTS > SCORE_LIQUIDITY_REJECT_POINTS
        assert SCORE_LIQUIDITY_REJECT_POINTS > 0  # Not zero - some REJECT trades win

    def test_quality_score_with_good_tier(self):
        """Test calculate_scan_quality_score with GOOD liquidity tier."""
        from scripts.scan import calculate_scan_quality_score, SCORE_LIQUIDITY_GOOD_POINTS

        result = {
            'vrp_ratio': 5.0,
            'edge_score': 3.0,
            'liquidity_tier': 'GOOD',
            'implied_move_pct': '10%',
        }

        score = calculate_scan_quality_score(result)

        # Score should include GOOD tier points (16)
        # Manually verify GOOD tier is scored correctly
        assert score > 0

        # Compare with WARNING to ensure GOOD gets higher score
        result_warning = result.copy()
        result_warning['liquidity_tier'] = 'WARNING'
        score_warning = calculate_scan_quality_score(result_warning)

        assert score > score_warning, "GOOD tier should score higher than WARNING"
        assert score - score_warning == SCORE_LIQUIDITY_GOOD_POINTS - 12  # 16 - 12 = 4


class TestRealWorldScenarios:
    """Tests based on real production scenarios."""

    def test_nvda_excellent_liquidity(self, scorer):
        """NVDA typically has excellent liquidity."""
        # Based on real NVDA data: high OI, tight spreads
        call = make_option(bid=15.00, ask=15.10, oi=5000, volume=800)
        put = make_option(bid=14.50, ask=14.60, oi=4500, volume=700)
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "EXCELLENT"

    def test_mid_cap_good_liquidity(self, scorer):
        """Mid-cap stocks often have good liquidity."""
        # Moderate OI (500-1000), tight spreads
        call = make_option(bid=8.00, ask=8.10, oi=700, volume=150)
        put = make_option(bid=7.50, ask=7.60, oi=650, volume=140)
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "GOOD"

    def test_small_cap_warning_liquidity(self, scorer):
        """Small cap stocks often have warning-level liquidity."""
        # Low OI (100-500), moderate spread
        call = make_option(bid=2.00, ask=2.10, oi=250, volume=40)
        put = make_option(bid=1.80, ask=1.90, oi=200, volume=35)
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "WARNING"

    def test_micro_cap_reject_liquidity(self, scorer):
        """Micro cap stocks often have reject-level liquidity."""
        # Very low OI, wide spreads
        call = make_option(bid=1.00, ask=1.40, oi=30, volume=5)
        put = make_option(bid=0.80, ask=1.20, oi=25, volume=8)
        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "REJECT"


class TestEdgeCases:
    """Edge case tests."""

    def test_zero_oi(self, scorer):
        """Zero OI should score poorly."""
        score = scorer._score_open_interest(0)
        assert score == 0.0

    def test_zero_volume(self, scorer):
        """Zero volume should score poorly."""
        score = scorer._score_volume(0)
        assert score == 0.0

    def test_very_wide_spread(self, scorer):
        """Very wide spread (>30%) should score near zero."""
        score = scorer._score_spread(35.0)
        assert score < 25.0

    def test_extreme_oi(self, scorer):
        """Extremely high OI should still cap at 100."""
        score = scorer._score_open_interest(100000)
        assert score == 100.0

    def test_negative_spread(self, scorer):
        """Negative spread (shouldn't happen) handled gracefully."""
        score = scorer._score_spread(-5.0)
        # Score function should handle this without crashing
        assert score == 100.0  # Negative spread = best case
