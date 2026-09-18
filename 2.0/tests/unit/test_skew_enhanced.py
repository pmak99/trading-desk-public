"""
Tests for the skew classifier (SkewAnalyzerEnhanced).

The classifier reads a 25-delta risk reversal off a smile fitted to OTM quotes:
puts below spot, calls above, each at its own delta. The chains below are built
the way a real one is: one implied volatility per strike, shared by the put and
the call there (put-call parity), with Black-Scholes deltas. A chain factory
that gave the put and the call at one strike different IVs would be testing
quote noise, which is what the pre-Sep-2026 classifier measured.
"""

import math
from datetime import date
from unittest.mock import Mock

import pytest

from src.application.metrics.skew_enhanced import SkewAnalysis, SkewAnalyzerEnhanced
from src.domain.enums import DirectionalBias
from src.domain.errors import ErrorCode, Ok
from src.domain.types import Money, OptionChain, OptionQuote, Percentage, Strike

EXPIRATION = date(2026, 4, 15)
T_YEARS = 7 / 365  # a pre-earnings weekly
SPOT = 100.0


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(spot: float, strike: float, iv_pct: float, call: bool, t: float = T_YEARS) -> float:
    sd = iv_pct / 100.0 * math.sqrt(t)
    d1 = (math.log(spot / strike) + 0.5 * sd * sd) / sd
    return _ncdf(d1) if call else _ncdf(d1) - 1.0


def quote(iv_pct: float, delta, liquid: bool = True) -> OptionQuote:
    return OptionQuote(
        bid=Money(1.00),
        ask=Money(1.10),
        implied_volatility=Percentage(iv_pct),
        open_interest=1000 if liquid else 0,
        volume=100,
        delta=delta,
    )


def build_chain(put_iv, call_iv=None, strikes=range(70, 131), spot=SPOT, ticker="TEST"):
    """Chain from IV-by-strike functions. call_iv defaults to put_iv (parity)."""
    call_iv = call_iv or put_iv
    calls, puts = {}, {}
    for k in strikes:
        strike = Strike(k)
        c_iv, p_iv = call_iv(k), put_iv(k)
        calls[strike] = quote(c_iv, bs_delta(spot, k, c_iv, call=True))
        puts[strike] = quote(p_iv, bs_delta(spot, k, p_iv, call=False))
    return OptionChain(
        ticker=ticker, expiration=EXPIRATION, stock_price=Money(spot),
        calls=calls, puts=puts,
    )


def smile(shape: str):
    """IV% as a function of strike, for one named shape around a 50% ATM vol."""
    def iv(k):
        x = math.log(k / SPOT)
        if shape == "put_skew":      # OTM puts rich, OTM calls cheap
            return 50.0 * (1.0 - 2.5 * x)
        if shape == "call_skew":     # the mirror image
            return 50.0 * (1.0 + 2.5 * x)
        if shape == "smile":         # both wings rich, no tilt
            return 50.0 * (1.0 + 1.5 * abs(x))
        if shape == "flat":
            return 50.0
        raise ValueError(shape)
    return iv


class TestSkewAnalyzerEnhanced:
    """End-to-end classification of whole chains."""

    @pytest.fixture
    def provider(self):
        return Mock()

    @pytest.fixture
    def analyzer(self, provider):
        return SkewAnalyzerEnhanced(provider)

    def create_chain_with_skew(self, ticker="TEST", stock_price=SPOT, skew_type="normal"):
        """normal (= put skew), put_skew, call_skew, smile or flat."""
        shape = "put_skew" if skew_type == "normal" else skew_type
        return build_chain(smile(shape), spot=stock_price, ticker=ticker)

    def _analyze(self, analyzer, provider, chain):
        provider.get_option_chain.return_value = Ok(chain)
        return analyzer.analyze_skew_curve(chain.ticker, chain.expiration)

    def test_normal_skew_detection(self, analyzer, provider):
        """Normal equity skew (OTM puts rich) reads as the bearish side."""
        result = self._analyze(analyzer, provider, self.create_chain_with_skew(skew_type="normal"))

        assert result.is_ok
        analysis = result.value
        assert isinstance(analysis, SkewAnalysis)
        assert analysis.ticker == "TEST"
        assert analysis.num_points >= analyzer.MIN_POINTS
        assert analysis.strength in ["smile", "smirk", "inverse_smile"]
        assert isinstance(analysis.directional_bias, DirectionalBias)
        assert analysis.directional_bias in {
            DirectionalBias.WEAK_BEARISH, DirectionalBias.BEARISH, DirectionalBias.STRONG_BEARISH}
        assert 0.0 <= analysis.confidence <= 1.0

    def test_smile_detection(self, analyzer, provider):
        """Both wings rich with no tilt: a smile, and no directional reading."""
        result = self._analyze(analyzer, provider, self.create_chain_with_skew(skew_type="smile"))

        assert result.is_ok
        analysis = result.value
        assert analysis.strength == "smile"
        assert analysis.curvature > analyzer.SMILE_THRESHOLD
        assert analysis.directional_bias == DirectionalBias.NEUTRAL

    def test_flat_skew(self, analyzer, provider):
        """Flat vol: no curvature, no tilt."""
        result = self._analyze(analyzer, provider, self.create_chain_with_skew(skew_type="flat"))

        assert result.is_ok
        analysis = result.value
        assert analysis.strength == "smirk"  # the label for "neither smile nor inverse"
        assert abs(analysis.curvature) < analyzer.SMILE_THRESHOLD
        assert analysis.directional_bias == DirectionalBias.NEUTRAL
        assert analysis.skew_atm.value == pytest.approx(0.0, abs=1e-6)

    def test_insufficient_data_points(self, analyzer, provider):
        """Test error when insufficient data points for fit."""
        chain = OptionChain(
            ticker="TEST", expiration=EXPIRATION, stock_price=Money(SPOT),
            calls={}, puts={},
        )
        result = self._analyze(analyzer, provider, chain)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_provider_error_propagation(self, analyzer, provider):
        """Test that provider errors are propagated."""
        from src.domain.errors import AppError, Err

        provider.get_option_chain.return_value = Err(
            AppError(ErrorCode.EXTERNAL, "API error")
        )

        result = analyzer.analyze_skew_curve("TEST", EXPIRATION)

        assert result.is_err
        assert result.error.code == ErrorCode.EXTERNAL

    def test_illiquid_options_skipped(self, analyzer, provider):
        """An illiquid OTM quote is left out of the fit."""
        chain = self.create_chain_with_skew(skew_type="normal")
        liquid = self._analyze(analyzer, provider, chain).value

        # Kill the three OTM calls nearest the money.
        otm_calls = sorted(s for s in chain.calls if float(s.price) > SPOT)[:3]
        for strike in otm_calls:
            q = chain.calls[strike]
            chain.calls[strike] = OptionQuote(
                bid=q.bid, ask=q.ask, implied_volatility=q.implied_volatility,
                open_interest=0, volume=0, delta=q.delta,
            )

        result = self._analyze(analyzer, provider, chain)
        assert result.is_ok
        dropped = sum(
            1 for s in otm_calls
            if SkewAnalyzerEnhanced.MIN_ABS_DELTA <= chain.calls[s].delta < SkewAnalyzerEnhanced.MAX_ABS_DELTA
        )
        assert dropped > 0
        assert result.value.num_points == liquid.num_points - dropped

    def test_confidence_score_range(self, analyzer, provider):
        """Test that confidence score is in valid range."""
        result = self._analyze(analyzer, provider, self.create_chain_with_skew(skew_type="normal"))

        assert result.is_ok
        assert 0.0 <= result.value.confidence <= 1.0

    def test_atm_skew_extracted(self, analyzer, provider):
        """skew_atm carries the normalised risk reversal as a Percentage."""
        result = self._analyze(analyzer, provider, self.create_chain_with_skew(skew_type="normal"))

        assert result.is_ok
        analysis = result.value
        assert analysis.skew_atm is not None
        assert isinstance(analysis.skew_atm, Percentage)
        assert analysis.skew_atm.value > 0  # puts richer


class TestMinimumPoints:
    """Defect 1: a degree-2 fit through 3 points is exact interpolation.

    R² is then 1.0 whatever the quotes are, so the noise gate could never fire.
    """

    @pytest.fixture
    def provider(self):
        return Mock()

    @pytest.fixture
    def analyzer(self, provider):
        return SkewAnalyzerEnhanced(provider)

    def _analyze(self, analyzer, provider, chain):
        provider.get_option_chain.return_value = Ok(chain)
        return analyzer.analyze_skew_curve(chain.ticker, chain.expiration)

    def test_min_points_is_five(self, analyzer):
        assert analyzer.MIN_POINTS == 5

    def test_three_point_chain_is_rejected(self, analyzer, provider):
        # Two OTM puts (92, 96) and one OTM call (104), every quote liquid and
        # inside the delta band — both wings reach 25Δ, only the count is short.
        chain = build_chain(smile("put_skew"), strikes=[92, 96, 104])
        assert len(analyzer._collect_skew_points(chain, SPOT)) == 3  # precondition

        result = self._analyze(analyzer, provider, chain)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_four_point_chain_is_rejected(self, analyzer, provider):
        chain = build_chain(smile("put_skew"), strikes=[92, 96, 104, 106])
        assert len(analyzer._collect_skew_points(chain, SPOT)) == 4  # precondition

        result = self._analyze(analyzer, provider, chain)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_one_wing_is_rejected(self, analyzer, provider):
        # Plenty of OTM puts, no OTM call at all: no risk reversal to measure.
        chain = build_chain(smile("put_skew"), strikes=range(85, 101))
        result = self._analyze(analyzer, provider, chain)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_call_wing_short_of_25_delta_is_rejected(self, analyzer, provider):
        # OTM calls only at 101-102 (deltas ~0.4): the 25Δ call would be
        # extrapolated, not measured.
        chain = build_chain(smile("put_skew"), strikes=list(range(85, 100)) + [101, 102])
        calls = [chain.calls[Strike(k)].delta for k in (101, 102)]
        assert min(calls) > 0.25  # precondition: really short of 25Δ

        result = self._analyze(analyzer, provider, chain)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_small_sample_fit_is_judged_on_adjusted_r_squared(self, analyzer):
        # Five points, raw R² 0.49 (clears a 0.30 gate), adjusted R² below
        # zero: the tilt (RR ≈ 6.6% of ATM, WEAK_BEARISH territory) is
        # indistinguishable from noise and must read NEUTRAL.
        points = list(zip([-0.40, -0.25, 0.0, 0.25, 0.40], [54, 48, 52, 49, 46], strict=True))
        analysis = analyzer._fit_and_analyze("TEST", EXPIRATION, Money(SPOT), points)

        assert analysis.skew_atm.value > analyzer.THRESHOLD_NEUTRAL * 100
        assert analysis.confidence < analyzer.MIN_FIT_QUALITY
        assert analysis.directional_bias == DirectionalBias.NEUTRAL

    def test_clean_five_point_fit_keeps_its_reading(self, analyzer):
        points = list(zip([-0.40, -0.25, 0.0, 0.25, 0.40], [55, 49, 50, 48, 46], strict=True))
        analysis = analyzer._fit_and_analyze("TEST", EXPIRATION, Money(SPOT), points)

        assert analysis.confidence >= analyzer.MIN_FIT_QUALITY
        assert analysis.directional_bias == DirectionalBias.WEAK_BEARISH


class TestRiskReversalMeasure:
    """Defect 2: skew was put IV − call IV at the SAME strike.

    Put-call parity makes those two IVs equal up to quote noise, so that
    difference measures noise, not the smile. The smile is OTM puts against
    OTM calls: the 25-delta risk reversal, normalised by ATM IV.
    """

    @pytest.fixture
    def provider(self):
        return Mock()

    @pytest.fixture
    def analyzer(self, provider):
        return SkewAnalyzerEnhanced(provider)

    def _analyze(self, analyzer, provider, chain):
        provider.get_option_chain.return_value = Ok(chain)
        return analyzer.analyze_skew_curve(chain.ticker, chain.expiration)

    def test_put_skew_with_equal_same_strike_ivs_is_bearish(self, analyzer, provider):
        chain = build_chain(smile("put_skew"))
        # Precondition: parity holds everywhere, so same-strike put − call is 0.
        for strike in chain.strikes:
            assert chain.puts[strike].implied_volatility == chain.calls[strike].implied_volatility

        result = self._analyze(analyzer, provider, chain)

        assert result.is_ok
        analysis = result.value
        assert analysis.directional_bias in (
            DirectionalBias.BEARISH, DirectionalBias.STRONG_BEARISH
        )
        assert analysis.skew_atm.value > analyzer.THRESHOLD_WEAK * 100
        assert analysis.slope_atm < 0  # sign convention: negative = put skew

    def test_call_skew_with_equal_same_strike_ivs_is_bullish(self, analyzer, provider):
        result = self._analyze(analyzer, provider, build_chain(smile("call_skew")))

        assert result.is_ok
        analysis = result.value
        assert analysis.directional_bias in (
            DirectionalBias.BULLISH, DirectionalBias.STRONG_BULLISH
        )
        assert analysis.skew_atm.value < -analyzer.THRESHOLD_WEAK * 100
        assert analysis.slope_atm > 0

    def test_same_strike_iv_differences_alone_are_not_skew(self, analyzer, provider):
        # Every OTM quote at 40 vol on both wings: no smile, no tilt. The ITM
        # quotes disagree with their OTM partners by up to 15 vol points, in a
        # pattern that makes same-strike put − call fall steeply with strike.
        def put_iv(k):
            return 40.0 if k < SPOT else 40.0 - 100.0 * (k - SPOT) / SPOT

        def call_iv(k):
            return 40.0 if k > SPOT else 40.0 + 100.0 * (k - SPOT) / SPOT

        chain = build_chain(put_iv, call_iv, strikes=range(85, 116))
        result = self._analyze(analyzer, provider, chain)

        assert result.is_ok
        analysis = result.value
        assert analysis.directional_bias == DirectionalBias.NEUTRAL
        assert analysis.skew_atm.value == pytest.approx(0.0, abs=1e-6)

    def test_far_wing_quotes_do_not_drive_the_reading(self, analyzer, provider):
        # A symmetric smile whose far wings blow out (209% vol at the 70 put).
        # Its local 25Δ vols are 52.8 put / 53.5 call. Fitted from 5Δ, the far
        # put wing dragged the reading to RR +29.5% — STRONG_BEARISH, a
        # compound-risk leg out of nothing. The 10Δ floor keeps them out.
        def blown_out(k):
            return 50.0 * (1.0 + 25.0 * math.log(k / SPOT) ** 2)

        result = self._analyze(analyzer, provider, build_chain(blown_out))

        assert result.is_ok
        analysis = result.value
        assert abs(analysis.skew_atm.value) < analyzer.THRESHOLD_WEAK * 100
        assert analysis.directional_bias.strength() <= 1

    def test_risk_reversal_is_read_at_25_delta_and_normalised(self, analyzer):
        # IV(v) = 50 − 20v, v = signed distance from 50Δ (puts negative).
        # 25Δ put = 55, 25Δ call = 45, ATM = 50 → RR = 10 / 50 = 20%.
        vs = [-0.45, -0.30, -0.10, 0.10, 0.30, 0.45]
        points = [(v, 50.0 - 20.0 * v) for v in vs]
        analysis = analyzer._fit_and_analyze("TEST", EXPIRATION, Money(SPOT), points)

        assert analysis.skew_atm.value == pytest.approx(20.0)
        assert analysis.slope_atm == pytest.approx(-0.4)
        assert analysis.curvature == pytest.approx(0.0, abs=1e-9)
        assert analysis.num_points == 6

    def test_normalisation_makes_the_reading_scale_free(self, analyzer):
        # Pre-earnings IV inflation scales every strike; the classification
        # must not change with the level of vol, only with its tilt.
        vs = [-0.45, -0.30, -0.10, 0.10, 0.30, 0.45]
        shape = [1.0 - 0.3 * v + 0.2 * v * v for v in vs]
        low = analyzer._fit_and_analyze(
            "TEST", EXPIRATION, Money(SPOT), [(v, 30.0 * s) for v, s in zip(vs, shape, strict=True)])
        high = analyzer._fit_and_analyze(
            "TEST", EXPIRATION, Money(SPOT), [(v, 90.0 * s) for v, s in zip(vs, shape, strict=True)])

        assert low.skew_atm.value == pytest.approx(high.skew_atm.value)
        assert low.curvature == pytest.approx(high.curvature)
        assert low.directional_bias == high.directional_bias

    @pytest.mark.parametrize("rr, expected", [
        (0.0, DirectionalBias.NEUTRAL),
        (0.05, DirectionalBias.NEUTRAL),         # each upper bound is inclusive
        (0.0501, DirectionalBias.WEAK_BEARISH),
        (0.10, DirectionalBias.WEAK_BEARISH),
        (0.1001, DirectionalBias.BEARISH),
        (0.20, DirectionalBias.BEARISH),
        (0.2001, DirectionalBias.STRONG_BEARISH),
        (-0.05, DirectionalBias.NEUTRAL),
        (-0.10, DirectionalBias.WEAK_BULLISH),
        (-0.20, DirectionalBias.BULLISH),
        (-0.2001, DirectionalBias.STRONG_BULLISH),
    ])
    def test_seven_level_boundaries(self, analyzer, rr, expected):
        assert analyzer._classify(rr) == expected

    @pytest.mark.parametrize("rr_pct, expected", [
        (7.5, DirectionalBias.WEAK_BEARISH),
        (15.0, DirectionalBias.BEARISH),
        (25.0, DirectionalBias.STRONG_BEARISH),
        (-7.5, DirectionalBias.WEAK_BULLISH),
        (-15.0, DirectionalBias.BULLISH),
        (-25.0, DirectionalBias.STRONG_BULLISH),
    ])
    def test_fitted_tilt_reaches_its_level(self, analyzer, rr_pct, expected):
        # A clean linear tilt with the requested RR (R² = 1 on 6 points),
        # kept clear of the boundaries a least-squares fit could straddle.
        vs = [-0.45, -0.30, -0.10, 0.10, 0.30, 0.45]
        slope = -rr_pct / 100.0 * 2.0 * 50.0  # RR = −slope / 2 / ATM
        points = [(v, 50.0 + slope * v) for v in vs]
        analysis = analyzer._fit_and_analyze("TEST", EXPIRATION, Money(SPOT), points)

        assert analysis.skew_atm.value == pytest.approx(rr_pct, abs=1e-9)
        assert analysis.directional_bias == expected


class TestBiasToNumeric:
    """bias_to_numeric / numeric_to_bias round-trip."""

    def test_strong_bearish_maps_to_minus_3(self):
        from src.application.metrics.skew_enhanced import bias_to_numeric
        assert bias_to_numeric(DirectionalBias.STRONG_BEARISH) == -3

    def test_strong_bullish_maps_to_plus_3(self):
        from src.application.metrics.skew_enhanced import bias_to_numeric
        assert bias_to_numeric(DirectionalBias.STRONG_BULLISH) == 3

    def test_neutral_maps_to_zero(self):
        from src.application.metrics.skew_enhanced import bias_to_numeric
        assert bias_to_numeric(DirectionalBias.NEUTRAL) == 0

    def test_round_trip_all_levels(self):
        from src.application.metrics.skew_enhanced import (
            bias_to_numeric,
            numeric_to_bias,
        )
        for bias in DirectionalBias:
            assert numeric_to_bias(bias_to_numeric(bias)) == bias


class TestRSlp30ToNumeric:
    """r_slp_30_to_numeric threshold coverage."""

    def test_below_lower_bound_is_strong_bearish(self):
        from src.application.metrics.skew_enhanced import r_slp_30_to_numeric
        assert r_slp_30_to_numeric(-2.0) == -3  # < -0.905

    def test_near_mean_is_neutral(self):
        from src.application.metrics.skew_enhanced import r_slp_30_to_numeric
        assert r_slp_30_to_numeric(1.036) == 0   # mean → NEUTRAL

    def test_above_upper_bound_is_strong_bullish(self):
        from src.application.metrics.skew_enhanced import r_slp_30_to_numeric
        assert r_slp_30_to_numeric(5.0) == 3   # > 2.976

    def test_known_spot_check_aapl(self):
        from src.application.metrics.skew_enhanced import r_slp_30_to_numeric
        # AAPL rSlp30 ≈ 1.268 → NEUTRAL (between 0.712 and 1.359)
        assert r_slp_30_to_numeric(1.268) == 0

    def test_known_spot_check_jpm(self):
        from src.application.metrics.skew_enhanced import r_slp_30_to_numeric
        # JPM rSlp30 ≈ 2.083 → BULLISH (between 2.006 and 2.976)
        assert r_slp_30_to_numeric(2.083) == 2


class TestSkewRSquaredGating:
    """A fit below MIN_FIT_QUALITY forces NEUTRAL regardless of tilt."""

    @pytest.fixture
    def provider(self):
        return Mock()

    @pytest.fixture
    def analyzer(self, provider):
        return SkewAnalyzerEnhanced(provider)

    def test_bias_confidence_equals_r_squared(self, analyzer, provider):
        """bias_confidence must equal confidence (the fit quality), not R²*slope_strength."""
        chain = TestSkewAnalyzerEnhanced().create_chain_with_skew(skew_type="normal")
        provider.get_option_chain.return_value = Ok(chain)
        result = analyzer.analyze_skew_curve("TEST", chain.expiration)
        assert result.is_ok
        analysis = result.value
        assert abs(analysis.bias_confidence - analysis.confidence) < 0.001
