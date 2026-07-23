"""Tests for calculate_harvest_score() and _rslp30_to_skew_label()."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.scan.quality_scorer import calculate_harvest_score, _rslp30_to_skew_label


class TestRslp30ToSkewLabel:
    def test_strong_bearish(self):
        assert _rslp30_to_skew_label(-1.5) == 'STRONG_BEARISH'

    def test_bearish(self):
        assert _rslp30_to_skew_label(0.0) == 'BEARISH'

    def test_weak_bearish(self):
        assert _rslp30_to_skew_label(0.5) == 'WEAK_BEARISH'

    def test_neutral(self):
        assert _rslp30_to_skew_label(1.0) == 'NEUTRAL'

    def test_weak_bullish(self):
        assert _rslp30_to_skew_label(1.5) == 'WEAK_BULLISH'

    def test_bullish(self):
        assert _rslp30_to_skew_label(2.5) == 'BULLISH'

    def test_strong_bullish(self):
        assert _rslp30_to_skew_label(3.5) == 'STRONG_BULLISH'

    def test_none_returns_none(self):
        assert _rslp30_to_skew_label(None) is None

    def test_boundary_strong_bearish(self):
        # -0.905 is the boundary — exactly at boundary is BEARISH (not >=)
        assert _rslp30_to_skew_label(-0.905) == 'BEARISH'
        assert _rslp30_to_skew_label(-0.906) == 'STRONG_BEARISH'


class TestCalculateHarvestScore:

    def test_perfect_candidate_max_score(self):
        # iv_rank=100 → 40pts, iv_hv=2.0 → 25pts, STRONG_BULLISH → 15pts, max_contracts=100 → 20pts
        candidate = {
            'iv_rank_1y': 100.0,
            'iv_hv_ratio': 2.0,
            'r_slp_30': 3.5,      # STRONG_BULLISH
            'max_contracts': 100,
        }
        assert calculate_harvest_score(candidate) == 100.0

    def test_minimum_gate_candidate(self):
        # iv_rank=60 → 0pts, iv_hv=1.0 → 0pts, NEUTRAL → 10pts, max_contracts=100 → 20pts
        candidate = {
            'iv_rank_1y': 60.0,
            'iv_hv_ratio': 1.0,
            'r_slp_30': 1.0,      # NEUTRAL
            'max_contracts': 100,
        }
        score = calculate_harvest_score(candidate)
        assert score == 30.0

    def test_iv_hv_capped_at_2x(self):
        base = {'iv_rank_1y': 80.0, 'r_slp_30': 1.0, 'max_contracts': 100}
        score_2x = calculate_harvest_score({**base, 'iv_hv_ratio': 2.0})
        score_3x = calculate_harvest_score({**base, 'iv_hv_ratio': 3.5})
        assert score_2x == score_3x

    def test_bearish_skew_penalised_vs_neutral(self):
        base = {'iv_rank_1y': 80.0, 'iv_hv_ratio': 1.5, 'max_contracts': 100}
        bearish = calculate_harvest_score({**base, 'r_slp_30': 0.0})   # BEARISH
        neutral = calculate_harvest_score({**base, 'r_slp_30': 1.0})   # NEUTRAL
        assert bearish < neutral

    def test_null_r_slp30_uses_middle_default(self):
        # NULL should get 8pts on skew (middle default), not 0 or 15
        candidate = {'iv_rank_1y': 80.0, 'iv_hv_ratio': 1.3, 'r_slp_30': None, 'max_contracts': 100}
        score_null = calculate_harvest_score(candidate)
        score_bullish = calculate_harvest_score({**candidate, 'r_slp_30': 2.5})   # BULLISH = 14pts
        score_bearish = calculate_harvest_score({**candidate, 'r_slp_30': 0.0})   # BEARISH = 3pts
        assert score_bearish < score_null < score_bullish

    def test_reduced_max_contracts_penalised(self):
        base = {'iv_rank_1y': 80.0, 'iv_hv_ratio': 1.3, 'r_slp_30': 1.0}
        full = calculate_harvest_score({**base, 'max_contracts': 100})
        reduced = calculate_harvest_score({**base, 'max_contracts': 50})
        very_low = calculate_harvest_score({**base, 'max_contracts': 25})
        assert full > reduced > very_low

    def test_empty_dict_returns_valid_float(self):
        score = calculate_harvest_score({})
        assert isinstance(score, float)
        assert 0.0 <= score <= 100.0

    def test_deterministic(self):
        candidate = {'iv_rank_1y': 85.0, 'iv_hv_ratio': 1.6, 'r_slp_30': 1.2, 'max_contracts': 100}
        assert calculate_harvest_score(candidate) == calculate_harvest_score(candidate)

    def test_raises_type_error_on_non_dict(self):
        with pytest.raises(TypeError):
            calculate_harvest_score("not a dict")

    def test_score_in_valid_range(self):
        candidates = [
            {'iv_rank_1y': 75.0, 'iv_hv_ratio': 1.3, 'r_slp_30': 0.8, 'max_contracts': 100},
            {'iv_rank_1y': 95.0, 'iv_hv_ratio': 1.8, 'r_slp_30': 2.0, 'max_contracts': 50},
            {'iv_rank_1y': 62.0, 'iv_hv_ratio': 1.05, 'r_slp_30': -0.5, 'max_contracts': 25},
        ]
        for c in candidates:
            score = calculate_harvest_score(c)
            assert 0.0 <= score <= 100.0, f"Score {score} out of range for {c}"
