"""
Unit tests for liquidity tier boundary conditions.

Tests exact boundary values for the 4-tier liquidity system:
- Spread at exact boundaries: 8.0%, 12.0%, 15.0%
- OI at exact boundaries: min_oi (50), warning_oi (100), good_oi (500), excellent_oi (1000)
- Combined tier = worse of (OI tier, spread tier)
- Zero spread, zero OI edge cases
"""

import pytest
from datetime import date, timedelta

from src.application.metrics.liquidity_scorer import LiquidityScorer, LiquidityScore
from src.domain.types import Money, Percentage, Strike, OptionQuote


# ============================================================================
# Helpers
# ============================================================================


def make_option(
    bid: float = 2.00,
    ask: float = 2.10,
    oi: int = 500,
    volume: int = 100,
    iv: float = 30.0,
) -> OptionQuote:
    """Create a test option quote."""
    return OptionQuote(
        bid=Money(bid),
        ask=Money(ask),
        implied_volatility=Percentage(iv),
        open_interest=oi,
        volume=volume,
    )


def make_option_with_spread_pct(
    target_spread_pct: float,
    mid_price: float = 5.0,
    oi: int = 1500,
    volume: int = 300,
) -> OptionQuote:
    """Create option with a specific spread percentage.

    spread_pct = (ask - bid) / mid * 100
    spread = target_spread_pct * mid / 100
    bid = mid - spread/2, ask = mid + spread/2
    """
    spread = target_spread_pct * mid_price / 100.0
    bid = mid_price - spread / 2.0
    ask = mid_price + spread / 2.0
    return make_option(bid=bid, ask=ask, oi=oi, volume=volume)


@pytest.fixture
def scorer():
    """Default liquidity scorer with standard 4-tier thresholds."""
    return LiquidityScorer(
        min_oi=50,
        warning_oi=100,
        good_oi=500,
        excellent_oi=1000,
        min_volume=20,
        good_volume=100,
        excellent_volume=250,
        max_spread_pct=15.0,
        warning_spread_pct=12.0,
        good_spread_pct=8.0,
        excellent_spread_pct=5.0,
    )


# ============================================================================
# Spread Boundary Tests
# ============================================================================


class TestSpreadBoundaries:
    """Tests for spread-based tier classification at exact boundaries.

    Spread tiers:
    - EXCELLENT: < 8.0%
    - GOOD: >= 8.0% and < 12.0%
    - WARNING: >= 12.0% and <= 15.0%
    - REJECT: > 15.0%
    """

    def test_spread_below_excellent_threshold(self, scorer):
        """Spread < 8.0% should give EXCELLENT spread tier."""
        # OI/volume high enough to not interfere
        tier = scorer._classify_tier(oi=1500, volume=300, spread_pct=7.99)
        assert tier == "EXCELLENT"

    def test_spread_at_good_boundary(self, scorer):
        """Spread = 8.0% should give GOOD spread tier (>= 8%)."""
        tier = scorer._classify_tier(oi=1500, volume=300, spread_pct=8.0)
        assert tier == "GOOD"

    def test_spread_between_good_and_warning(self, scorer):
        """Spread between 8% and 12% should give GOOD spread tier."""
        tier = scorer._classify_tier(oi=1500, volume=300, spread_pct=10.0)
        assert tier == "GOOD"

    def test_spread_at_warning_boundary(self, scorer):
        """Spread = 12.0% should give WARNING spread tier (>= 12%)."""
        tier = scorer._classify_tier(oi=1500, volume=300, spread_pct=12.0)
        assert tier == "WARNING"

    def test_spread_between_warning_and_reject(self, scorer):
        """Spread between 12% and 15% should give WARNING spread tier."""
        tier = scorer._classify_tier(oi=1500, volume=300, spread_pct=14.0)
        assert tier == "WARNING"

    def test_spread_at_max_boundary(self, scorer):
        """Spread = 15.0% should still be WARNING (> 15% is REJECT)."""
        tier = scorer._classify_tier(oi=1500, volume=300, spread_pct=15.0)
        assert tier == "WARNING"

    def test_spread_just_above_reject_threshold(self, scorer):
        """Spread = 15.01% should be REJECT (> 15%)."""
        tier = scorer._classify_tier(oi=1500, volume=300, spread_pct=15.01)
        assert tier == "REJECT"

    def test_spread_well_above_reject(self, scorer):
        """Spread = 50% should definitely be REJECT."""
        tier = scorer._classify_tier(oi=1500, volume=300, spread_pct=50.0)
        assert tier == "REJECT"


# ============================================================================
# OI Boundary Tests
# ============================================================================


class TestOIBoundaries:
    """Tests for OI-based tier classification at exact boundaries.

    OI tiers (with default thresholds):
    - REJECT: OI < min_oi (50) or volume < min_volume (20)
    - REJECT: OI >= min_oi but < warning_oi (100) (still too low)
    - WARNING: OI >= warning_oi (100) and < good_oi (500)
    - GOOD: OI >= good_oi (500) and < excellent_oi (1000)
    - EXCELLENT: OI >= excellent_oi (1000)
    """

    def test_oi_zero(self, scorer):
        """OI = 0 should be REJECT."""
        tier = scorer._classify_tier(oi=0, volume=100, spread_pct=3.0)
        assert tier == "REJECT"

    def test_oi_below_min(self, scorer):
        """OI < min_oi (50) should be REJECT."""
        tier = scorer._classify_tier(oi=49, volume=100, spread_pct=3.0)
        assert tier == "REJECT"

    def test_oi_at_min(self, scorer):
        """OI = min_oi (50) but < warning_oi should still be REJECT."""
        tier = scorer._classify_tier(oi=50, volume=100, spread_pct=3.0)
        assert tier == "REJECT"

    def test_oi_between_min_and_warning(self, scorer):
        """OI between min_oi (50) and warning_oi (100) should be REJECT."""
        tier = scorer._classify_tier(oi=75, volume=100, spread_pct=3.0)
        assert tier == "REJECT"

    def test_oi_at_warning_threshold(self, scorer):
        """OI = warning_oi (100) should be WARNING."""
        tier = scorer._classify_tier(oi=100, volume=100, spread_pct=3.0)
        assert tier == "WARNING"

    def test_oi_between_warning_and_good(self, scorer):
        """OI between warning_oi (100) and good_oi (500) should be WARNING."""
        tier = scorer._classify_tier(oi=300, volume=100, spread_pct=3.0)
        assert tier == "WARNING"

    def test_oi_at_good_threshold(self, scorer):
        """OI = good_oi (500) should be GOOD."""
        tier = scorer._classify_tier(oi=500, volume=100, spread_pct=3.0)
        assert tier == "GOOD"

    def test_oi_between_good_and_excellent(self, scorer):
        """OI between good_oi (500) and excellent_oi (1000) should be GOOD."""
        tier = scorer._classify_tier(oi=750, volume=100, spread_pct=3.0)
        assert tier == "GOOD"

    def test_oi_at_excellent_threshold(self, scorer):
        """OI = excellent_oi (1000) should be EXCELLENT."""
        tier = scorer._classify_tier(oi=1000, volume=100, spread_pct=3.0)
        assert tier == "EXCELLENT"

    def test_oi_well_above_excellent(self, scorer):
        """OI >> excellent_oi should be EXCELLENT."""
        tier = scorer._classify_tier(oi=10000, volume=100, spread_pct=3.0)
        assert tier == "EXCELLENT"


# ============================================================================
# Volume Boundary Tests
# ============================================================================


class TestVolumeBoundaries:
    """Tests for volume impact on tier classification."""

    def test_zero_volume_is_reject(self, scorer):
        """Volume = 0 should force REJECT regardless of OI."""
        tier = scorer._classify_tier(oi=5000, volume=0, spread_pct=3.0)
        assert tier == "REJECT"

    def test_below_min_volume_is_reject(self, scorer):
        """Volume < min_volume (20) should force REJECT."""
        tier = scorer._classify_tier(oi=5000, volume=19, spread_pct=3.0)
        assert tier == "REJECT"

    def test_at_min_volume_not_reject(self, scorer):
        """Volume = min_volume (20) should not be REJECT from volume alone."""
        tier = scorer._classify_tier(oi=1500, volume=20, spread_pct=3.0)
        assert tier != "REJECT"


# ============================================================================
# Combined Tier Tests (worse of OI tier, spread tier)
# ============================================================================


class TestCombinedTierLogic:
    """Tests that combined tier = worse of (OI tier, spread tier)."""

    def test_excellent_oi_reject_spread(self, scorer):
        """EXCELLENT OI + REJECT spread -> REJECT."""
        tier = scorer._classify_tier(oi=5000, volume=300, spread_pct=20.0)
        assert tier == "REJECT"

    def test_reject_oi_excellent_spread(self, scorer):
        """REJECT OI + EXCELLENT spread -> REJECT."""
        tier = scorer._classify_tier(oi=10, volume=300, spread_pct=3.0)
        assert tier == "REJECT"

    def test_excellent_oi_warning_spread(self, scorer):
        """EXCELLENT OI + WARNING spread -> WARNING."""
        tier = scorer._classify_tier(oi=5000, volume=300, spread_pct=13.0)
        assert tier == "WARNING"

    def test_warning_oi_excellent_spread(self, scorer):
        """WARNING OI + EXCELLENT spread -> WARNING."""
        tier = scorer._classify_tier(oi=150, volume=300, spread_pct=3.0)
        assert tier == "WARNING"

    def test_good_oi_good_spread(self, scorer):
        """GOOD OI + GOOD spread -> GOOD."""
        tier = scorer._classify_tier(oi=600, volume=300, spread_pct=10.0)
        assert tier == "GOOD"

    def test_excellent_oi_good_spread(self, scorer):
        """EXCELLENT OI + GOOD spread -> GOOD (worse of the two)."""
        tier = scorer._classify_tier(oi=5000, volume=300, spread_pct=10.0)
        assert tier == "GOOD"

    def test_good_oi_excellent_spread(self, scorer):
        """GOOD OI + EXCELLENT spread -> GOOD (worse of the two)."""
        tier = scorer._classify_tier(oi=600, volume=300, spread_pct=3.0)
        assert tier == "GOOD"

    def test_excellent_both(self, scorer):
        """EXCELLENT OI + EXCELLENT spread -> EXCELLENT."""
        tier = scorer._classify_tier(oi=5000, volume=300, spread_pct=3.0)
        assert tier == "EXCELLENT"

    @pytest.mark.parametrize(
        "oi,spread,expected",
        [
            (5000, 3.0, "EXCELLENT"),   # Both EXCELLENT
            (5000, 10.0, "GOOD"),       # OI EXCELLENT, spread GOOD
            (5000, 13.0, "WARNING"),    # OI EXCELLENT, spread WARNING
            (5000, 20.0, "REJECT"),     # OI EXCELLENT, spread REJECT
            (600, 3.0, "GOOD"),         # OI GOOD, spread EXCELLENT
            (150, 3.0, "WARNING"),      # OI WARNING, spread EXCELLENT
            (10, 3.0, "REJECT"),        # OI REJECT, spread EXCELLENT
            (150, 13.0, "WARNING"),     # Both WARNING
            (10, 20.0, "REJECT"),       # Both REJECT
        ],
    )
    def test_combined_tier_matrix(self, scorer, oi, spread, expected):
        """Parametrized test for combined tier logic."""
        tier = scorer._classify_tier(oi=oi, volume=300, spread_pct=spread)
        assert tier == expected


# ============================================================================
# Zero / Edge Case Tests
# ============================================================================


class TestZeroEdgeCases:
    """Tests for zero values in spread and OI calculations."""

    def test_zero_spread(self, scorer):
        """Zero spread (bid == ask) should calculate 0% spread -> EXCELLENT."""
        option = make_option(bid=5.00, ask=5.00, oi=1500, volume=300)
        spread_pct = scorer.calculate_spread_pct(option)
        assert spread_pct == 0.0

    def test_zero_oi_option_score(self, scorer):
        """Option with zero OI should get low OI score."""
        option = make_option(oi=0, volume=100)
        score = scorer.score_option(option)
        assert score.oi_score == 0.0
        assert score.liquidity_tier == "REJECT"

    def test_zero_bid_zero_ask(self, scorer):
        """Zero bid and zero ask should return 100% spread (worst case)."""
        option = make_option(bid=0.0, ask=0.0, oi=1500, volume=300)
        spread_pct = scorer.calculate_spread_pct(option)
        # Mid = 0, so division by zero guard should return 100.0
        assert spread_pct == 100.0

    def test_zero_bid_nonzero_ask(self, scorer):
        """Zero bid with nonzero ask - spread should still be calculable."""
        option = make_option(bid=0.0, ask=1.00, oi=1500, volume=300)
        spread_pct = scorer.calculate_spread_pct(option)
        # Mid = 0.50, spread = 1.00, spread_pct = 200%
        assert spread_pct == 200.0

    def test_very_small_spread(self, scorer):
        """Very small spread should score well."""
        option = make_option(bid=5.00, ask=5.01, oi=1500, volume=300)
        spread_pct = scorer.calculate_spread_pct(option)
        assert spread_pct < 1.0


# ============================================================================
# Straddle Tier Tests
# ============================================================================


class TestStraddleTier:
    """Tests for straddle tier classification (call + put)."""

    def test_straddle_worse_of_two_legs(self, scorer):
        """Straddle tier should be worse of the two legs."""
        call = make_option(bid=3.00, ask=3.10, oi=1500, volume=300)  # Excellent
        put = make_option(bid=1.00, ask=1.50, oi=150, volume=100)   # WARNING (OI) or worse

        tier = scorer.classify_straddle_tier(call, put)

        # put has OI=150 -> WARNING; spread = 0.50/1.25 = 40% -> REJECT
        assert tier == "REJECT"

    def test_straddle_both_excellent(self, scorer):
        """Both legs excellent should give EXCELLENT tier."""
        call = make_option(bid=3.00, ask=3.05, oi=1500, volume=300)
        put = make_option(bid=3.00, ask=3.05, oi=1500, volume=300)

        tier = scorer.classify_straddle_tier(call, put)
        assert tier == "EXCELLENT"


# ============================================================================
# OI-Only Mode Tests
# ============================================================================


class TestOIOnlyMode:
    """Tests for OI-only tier classification (market closed mode)."""

    def test_oi_only_ignores_volume(self, scorer):
        """OI-only mode should not penalize for zero volume."""
        # With normal classify_tier, volume=0 would be REJECT
        tier_normal = scorer._classify_tier(oi=1500, volume=0, spread_pct=3.0)
        assert tier_normal == "REJECT"

        # OI-only mode should give EXCELLENT
        tier_oi_only = scorer._classify_tier_oi_only(oi=1500, spread_pct=3.0)
        assert tier_oi_only == "EXCELLENT"

    def test_oi_only_still_checks_spread(self, scorer):
        """OI-only mode should still check spread thresholds."""
        tier = scorer._classify_tier_oi_only(oi=1500, spread_pct=20.0)
        assert tier == "REJECT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
