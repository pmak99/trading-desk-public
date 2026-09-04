"""Unit tests for scan/harvest_live.py — live (post-ORATS) harvest Phase 1.

Replaces the frozen position_limits snapshot ranking with live data:
  - IV30 from Tradier ATM chain (same method as track_iv_weekly)
  - HV20 computed from live daily closes
  - Index IVR from 52w vol-index history (SPY->^VIX, QQQ->^VXN, IWM->^RVX)
  - Stock IVR: unavailable until iv_history matures (~Jun 2027) -> no hard
    gate, flagged for broker verification
  - TRR from historical_moves gap moves (live table)
"""

import math
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.scan.harvest_live import (
    build_live_candidates,
    classify_trr,
    compute_hv20,
    compute_ivr,
)


class TestComputeHv20:
    def test_constant_prices_zero_vol(self):
        assert compute_hv20([100.0] * 25) == pytest.approx(0.0)

    def test_insufficient_closes_returns_none(self):
        assert compute_hv20([100.0] * 20) is None

    def test_known_volatility(self):
        # Alternating +1%/-1% daily returns: log-return std is ~0.01,
        # annualized ~ 0.01 * sqrt(252) * 100 ~ 15.9%
        closes = [100.0]
        for i in range(24):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
        hv = compute_hv20(closes)
        assert hv == pytest.approx(0.01 * math.sqrt(252) * 100, rel=0.05)

    def test_uses_most_recent_window(self):
        # Volatile early history then flat 21 closes -> HV20 ~ 0
        closes = [100.0, 150.0, 80.0, 120.0] + [100.0] * 21
        assert compute_hv20(closes) == pytest.approx(0.0)


class TestComputeIvr:
    def test_midpoint_is_50(self):
        assert compute_ivr(20.0, [10.0, 30.0] + [20.0] * 20) == pytest.approx(50.0)

    def test_at_max_is_100(self):
        assert compute_ivr(30.0, [10.0, 30.0] + [15.0] * 20) == pytest.approx(100.0)

    def test_at_min_is_0(self):
        assert compute_ivr(10.0, [10.0, 30.0] + [15.0] * 20) == pytest.approx(0.0)

    def test_degenerate_range_returns_none(self):
        assert compute_ivr(20.0, [20.0] * 30) is None

    def test_insufficient_history_returns_none(self):
        assert compute_ivr(20.0, [10.0, 30.0]) is None


class TestClassifyTrr:
    def test_low(self):
        assert classify_trr([2.0, 2.0, 2.0, 2.5]) == "LOW"

    def test_normal(self):
        # max 10 / avg 4.67 = 2.14
        assert classify_trr([2.0, 2.0, 10.0]) == "NORMAL"

    def test_high(self):
        # max 20 / avg 7.33 = 2.73
        assert classify_trr([1.0, 1.0, 20.0]) == "HIGH"

    def test_signed_moves_use_abs(self):
        assert classify_trr([-1.0, 1.0, -20.0]) == "HIGH"

    def test_insufficient_moves_returns_none(self):
        assert classify_trr([5.0]) is None


def _raw(ticker="AAPL", is_index=False, iv30d=30.0, hv20d=20.0, ivr=None,
         gap_moves=None, next_earnings=None, max_contracts=100):
    return {
        "ticker": ticker,
        "is_index": is_index,
        "iv30d": iv30d,
        "hv20d": hv20d,
        "ivr": ivr,
        "gap_moves": gap_moves if gap_moves is not None else [2.0, 2.0, 2.0],
        "next_earnings": next_earnings,
        "max_contracts": max_contracts,
    }


class TestBuildLiveCandidates:
    TODAY = date(2026, 7, 3)

    def test_low_iv_hv_filtered(self):
        cands, skipped = build_live_candidates(
            [_raw(iv30d=20.0, hv20d=20.0)], today=self.TODAY)
        assert cands == []
        assert skipped[0][0] == "AAPL" and "IV/HV" in skipped[0][1]

    def test_stock_trr_high_filtered(self):
        cands, skipped = build_live_candidates(
            [_raw(gap_moves=[1.0, 1.0, 20.0])], today=self.TODAY)
        assert cands == []
        assert "TRR" in skipped[0][1]

    def test_stock_earnings_within_window_filtered(self):
        cands, skipped = build_live_candidates(
            [_raw(next_earnings="2026-07-20")], today=self.TODAY)
        assert cands == []
        assert "earnings" in skipped[0][1].lower()

    def test_stock_earnings_beyond_window_passes(self):
        cands, _ = build_live_candidates(
            [_raw(next_earnings="2026-08-20")], today=self.TODAY)
        assert len(cands) == 1
        assert cands[0]["next_earnings"] == "2026-08-20"

    def test_index_without_vol_index_degrades_to_flagged_pass(self):
        # ^RVX is discontinued on Yahoo — an index with no vol-index history
        # must degrade to the stock-style ungated/flagged path, NOT be
        # permanently excluded.
        cands, skipped = build_live_candidates(
            [_raw(ticker="IWM", is_index=True, ivr=None)], today=self.TODAY)
        assert skipped == []
        assert len(cands) == 1
        assert cands[0]["iv_rank_1y"] is None
        assert cands[0]["ivr_source"] == "unavailable"

    def test_index_below_ivr_gate_filtered(self):
        cands, skipped = build_live_candidates(
            [_raw(ticker="SPY", is_index=True, ivr=40.0)],
            iv_rank_min=60.0, today=self.TODAY)
        assert cands == []
        assert "IVR" in skipped[0][1]

    def test_index_above_ivr_gate_passes(self):
        cands, _ = build_live_candidates(
            [_raw(ticker="SPY", is_index=True, ivr=75.0)],
            iv_rank_min=60.0, today=self.TODAY)
        assert len(cands) == 1
        assert cands[0]["iv_rank_1y"] == 75.0
        assert cands[0]["ivr_source"] == "vol-index"

    def test_stock_without_ivr_passes_flagged(self):
        # No IVR gate possible for single names until iv_history matures —
        # they pass on IV/HV + TRR + earnings, flagged for broker check.
        cands, _ = build_live_candidates([_raw(ivr=None)], today=self.TODAY)
        assert len(cands) == 1
        assert cands[0]["iv_rank_1y"] is None
        assert cands[0]["ivr_source"] == "unavailable"

    def test_candidate_shape_for_scorer_and_display(self):
        cands, _ = build_live_candidates(
            [_raw(ticker="SPY", is_index=True, ivr=80.0, iv30d=30.0, hv20d=20.0)],
            today=self.TODAY)
        c = cands[0]
        for key in ("ticker", "iv_rank_1y", "iv_hv_ratio", "iv30d", "hv20d",
                    "r_slp_30", "tail_risk_level", "max_contracts", "next_earnings"):
            assert key in c, f"missing {key}"
        assert c["iv_hv_ratio"] == pytest.approx(1.5)
        assert c["r_slp_30"] is None  # no live skew source at Phase 1

    def test_missing_iv_or_hv_skipped(self):
        cands, skipped = build_live_candidates(
            [_raw(iv30d=None), _raw(ticker="MSFT", hv20d=None)], today=self.TODAY)
        assert cands == []
        assert len(skipped) == 2
