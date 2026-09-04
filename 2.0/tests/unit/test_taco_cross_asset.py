"""Unit tests for scripts/taco/cross_asset.py — confirmation logic."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.taco.constants import EventType
from scripts.taco.cross_asset import evaluate_cross_asset

EVENT = date(2026, 7, 1)
NOW = date(2026, 7, 20)


def _series(base: float, last: float):
    """Two-point series: baseline close on EVENT, last close on NOW."""
    return [(EVENT, base), (NOW, last)]


def _assets(tlt_move=0.0, uup_move=0.0, uso_move=0.0):
    """Build the assets dict from % moves since event."""
    return {
        "TLT": (_series(100.0, 100.0 * (1 + tlt_move / 100)), "yfinance"),
        "UUP": (_series(28.0, 28.0 * (1 + uup_move / 100)), "yfinance"),
        "USO": (_series(120.0, 120.0 * (1 + uso_move / 100)), "yfinance"),
    }


class TestCallConfirmation:
    def test_all_confirm_geopolitical(self):
        # flight to safety + supply-shock oil spike
        xa = evaluate_cross_asset("CALL", EventType.GEOPOLITICAL,
                                  _assets(2.0, 1.0, 5.0), EVENT, NOW)
        assert xa.count == 3 and xa.available == 3

    def test_none_confirm_when_flat(self):
        xa = evaluate_cross_asset("CALL", EventType.GEOPOLITICAL,
                                  _assets(0.2, 0.1, 0.5), EVENT, NOW)
        assert xa.count == 0 and xa.available == 3

    def test_threshold_is_inclusive(self):
        xa = evaluate_cross_asset("CALL", EventType.GEOPOLITICAL,
                                  _assets(1.0, 0.5, 3.0), EVENT, NOW)
        assert xa.count == 3

    def test_oil_down_confirms_tariff(self):
        # demand-destruction panic: oil falls
        xa = evaluate_cross_asset("CALL", EventType.TARIFF_TACO,
                                  _assets(2.0, 1.0, -5.0), EVENT, NOW)
        assert xa.count == 3

    def test_oil_up_does_not_confirm_tariff(self):
        xa = evaluate_cross_asset("CALL", EventType.TARIFF_TACO,
                                  _assets(2.0, 1.0, 5.0), EVENT, NOW)
        uso = next(r for r in xa.reads if r.symbol == "USO")
        assert not uso.confirmed and xa.count == 2

    def test_oil_down_does_not_confirm_geopolitical(self):
        # oil crashing during a supply-shock war contradicts the narrative
        xa = evaluate_cross_asset("CALL", EventType.GEOPOLITICAL,
                                  _assets(2.0, 1.0, -5.0), EVENT, NOW)
        assert xa.count == 2

    def test_oil_either_direction_confirms_unknown(self):
        up = evaluate_cross_asset("CALL", EventType.UNKNOWN,
                                  _assets(0.0, 0.0, 5.0), EVENT, NOW)
        down = evaluate_cross_asset("CALL", EventType.UNKNOWN,
                                    _assets(0.0, 0.0, -5.0), EVENT, NOW)
        assert up.count == 1 and down.count == 1

    def test_fed_and_econ_use_oil_down(self):
        for et in (EventType.FED_REGIME_SHIFT, EventType.ECON_DATA):
            xa = evaluate_cross_asset("CALL", et,
                                      _assets(0.0, 0.0, -5.0), EVENT, NOW)
            assert xa.count == 1, et

    def test_negative_thresholds_inclusive(self):
        # exactly -3.0% oil on TARIFF (down-confirms) must confirm
        xa = evaluate_cross_asset("CALL", EventType.TARIFF_TACO,
                                  _assets(0.0, 0.0, -3.0), EVENT, NOW)
        assert next(r for r in xa.reads if r.symbol == "USO").confirmed


class TestPutMirror:
    def test_put_mirrors_safety_directions(self):
        # euphoria: money leaves safety — TLT down, UUP down confirm
        xa = evaluate_cross_asset("PUT", EventType.UNKNOWN,
                                  _assets(-2.0, -1.0, 0.0), EVENT, NOW)
        assert xa.count == 2

    def test_put_oil_is_direction_blind(self):
        up = evaluate_cross_asset("PUT", EventType.UNKNOWN,
                                  _assets(0.0, 0.0, 5.0), EVENT, NOW)
        down = evaluate_cross_asset("PUT", EventType.UNKNOWN,
                                    _assets(0.0, 0.0, -5.0), EVENT, NOW)
        assert up.count == 1 and down.count == 1

    def test_put_flight_to_safety_does_not_confirm(self):
        xa = evaluate_cross_asset("PUT", EventType.UNKNOWN,
                                  _assets(2.0, 1.0, 0.0), EVENT, NOW)
        assert xa.count == 0

    def test_put_mirror_thresholds_inclusive(self):
        xa = evaluate_cross_asset("PUT", EventType.UNKNOWN,
                                  _assets(-1.0, -0.5, 0.0), EVENT, NOW)
        assert xa.count == 2


class TestDegradation:
    def test_missing_asset_reduces_available(self):
        assets = _assets(2.0, 1.0, 5.0)
        assets["USO"] = ([], "")
        xa = evaluate_cross_asset("CALL", EventType.GEOPOLITICAL,
                                  assets, EVENT, NOW)
        assert xa.available == 2 and xa.count == 2
        assert xa.unavailable == ["USO"]

    def test_all_missing(self):
        assets = {s: ([], "") for s in ("TLT", "UUP", "USO")}
        xa = evaluate_cross_asset("CALL", EventType.GEOPOLITICAL,
                                  assets, EVENT, NOW)
        assert xa.available == 0 and xa.count == 0
        assert sorted(xa.unavailable) == ["TLT", "USO", "UUP"]

    def test_series_without_baseline_is_unavailable(self):
        # data starts after the event date — no baseline close
        assets = _assets(2.0, 1.0, 5.0)
        assets["TLT"] = ([(NOW, 102.0)], "yfinance")
        xa = evaluate_cross_asset("CALL", EventType.GEOPOLITICAL,
                                  assets, EVENT, NOW)
        assert xa.available == 2 and "TLT" in xa.unavailable

    def test_baseline_uses_last_close_at_or_before_event(self):
        # event on a weekend: baseline = prior trading day's close
        assets = _assets(0.0, 0.0, 0.0)
        assets["TLT"] = ([(date(2026, 6, 30), 100.0), (NOW, 102.0)], "yfinance")
        xa = evaluate_cross_asset("CALL", EventType.GEOPOLITICAL,
                                  assets, EVENT, NOW)
        tlt = next(r for r in xa.reads if r.symbol == "TLT")
        assert tlt.move_pct == pytest.approx(2.0)
        assert tlt.confirmed


class TestFetch:
    def test_twelvedata_parses_and_sorts_ascending(self, monkeypatch):
        from scripts.taco import cross_asset

        # Twelve Data returns values NEWEST first — must sort ascending.
        payload = {"status": "ok", "values": [
            {"datetime": "2026-07-22", "close": "83.42000"},
            {"datetime": "2026-07-21", "close": "83.66000"},
        ]}

        class FakeResp:
            def json(self):
                return payload
        monkeypatch.setattr(cross_asset.requests, "get",
                            lambda *a, **k: FakeResp())
        series = cross_asset.fetch_twelvedata("TLT", "dummy-key")
        assert series == [(date(2026, 7, 21), 83.66), (date(2026, 7, 22), 83.42)]

    def test_twelvedata_error_payload_returns_empty(self, monkeypatch):
        from scripts.taco import cross_asset

        class FakeResp:
            def json(self):
                return {"code": 404, "status": "error", "message": "nope"}
        monkeypatch.setattr(cross_asset.requests, "get",
                            lambda *a, **k: FakeResp())
        assert cross_asset.fetch_twelvedata("TLT", "dummy-key") == []

    def test_twelvedata_network_error_returns_empty(self, monkeypatch):
        from scripts.taco import cross_asset

        def boom(*a, **k):
            raise OSError("connection refused")
        monkeypatch.setattr(cross_asset.requests, "get", boom)
        assert cross_asset.fetch_twelvedata("TLT", "dummy-key") == []

    def test_fetch_asset_series_prefers_yfinance(self, monkeypatch):
        from scripts.taco import cross_asset
        yf_series = [(date(2026, 7, 21), 100.0)]
        monkeypatch.setattr(cross_asset, "fetch_series",
                            lambda sym, period="1y": yf_series)
        series, source = cross_asset.fetch_asset_series("TLT")
        assert series == yf_series and source == "yfinance"

    def test_fetch_asset_series_falls_back_to_twelvedata(self, monkeypatch):
        from scripts.taco import cross_asset
        td_series = [(date(2026, 7, 21), 100.0)]
        monkeypatch.setattr(cross_asset, "fetch_series",
                            lambda sym, period="1y": [])
        monkeypatch.setattr(cross_asset, "fetch_twelvedata",
                            lambda sym, key, outputsize=400: td_series)
        monkeypatch.setenv("TWELVE_DATA_KEY", "dummy-key")
        series, source = cross_asset.fetch_asset_series("TLT")
        assert series == td_series and source == "twelvedata"

    def test_fetch_asset_series_both_fail(self, monkeypatch):
        from scripts.taco import cross_asset
        monkeypatch.setattr(cross_asset, "fetch_series",
                            lambda sym, period="1y": [])
        monkeypatch.setattr(cross_asset, "fetch_twelvedata",
                            lambda sym, key, outputsize=400: [])
        monkeypatch.setenv("TWELVE_DATA_KEY", "dummy-key")
        assert cross_asset.fetch_asset_series("TLT") == ([], "")

    def test_fetch_asset_series_no_key_skips_fallback(self, monkeypatch):
        from scripts.taco import cross_asset
        monkeypatch.setattr(cross_asset, "fetch_series",
                            lambda sym, period="1y": [])
        monkeypatch.delenv("TWELVE_DATA_KEY", raising=False)
        assert cross_asset.fetch_asset_series("TLT") == ([], "")
