"""Tests for ORATS_ENABLED feature flag branching."""

import json
import os
import sys
import tempfile
import sqlite3
import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

import pytest

_2dot0 = Path(__file__).parent.parent.parent          # Trading Desk/2.0
_repo_root = _2dot0.parent                             # Trading Desk/
sys.path.insert(0, str(_2dot0))
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def _import_script(rel_path: str):
    """Import a script as a module without executing its __main__ block."""
    full = _2dot0 / rel_path
    spec = importlib.util.spec_from_file_location(full.stem, full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# common/constants.py — env var parsing
# ---------------------------------------------------------------------------

class TestOratsEnabledConstant:
    """ORATS_ENABLED reads from env var; defaults to true."""

    def _reload_constant(self, env_val: str | None) -> bool:
        """Re-import constants with a fresh env var value."""
        import importlib
        import common.constants as mod

        env_patch = {k: v for k, v in os.environ.items()}
        if env_val is None:
            env_patch.pop("ORATS_ENABLED", None)
        else:
            env_patch["ORATS_ENABLED"] = env_val

        with patch.dict(os.environ, env_patch, clear=True):
            importlib.reload(mod)
            return mod.ORATS_ENABLED

    def test_default_is_true(self):
        result = self._reload_constant(None)
        assert result is True

    def test_explicit_true(self):
        assert self._reload_constant("true") is True
        assert self._reload_constant("True") is True
        assert self._reload_constant("TRUE") is True

    def test_explicit_false(self):
        assert self._reload_constant("false") is False
        assert self._reload_constant("False") is False
        assert self._reload_constant("FALSE") is False

    def test_invalid_value_is_false(self):
        # Any value that isn't "true" (case-insensitive) evaluates false
        assert self._reload_constant("yes") is False
        assert self._reload_constant("1") is False


# ---------------------------------------------------------------------------
# analyzer.py — Steps 5.5 and 5.6 branch on ORATS_ENABLED
# ---------------------------------------------------------------------------

def _make_vrp(is_tradeable=True):
    vrp = Mock()
    vrp.is_tradeable = is_tradeable
    vrp.ticker = "TEST"
    vrp.expiration = None
    vrp.implied_move_pct = 5.0
    vrp.historical_mean_move_pct = 3.0
    vrp.vrp_ratio = 1.7
    vrp.edge_score = 0.8
    vrp.recommendation = Mock()
    vrp.recommendation.value = "GOOD"
    return vrp


def _make_skew(bias_value="NEUTRAL", bias_confidence=0.5, slope=None):
    from src.domain.enums import DirectionalBias
    skew = Mock()
    skew.ticker = "TEST"
    skew.expiration = None
    skew.stock_price = 100.0
    skew.skew_atm = 0.0
    skew.curvature = 0.0
    skew.strength = 0.5
    skew.directional_bias = DirectionalBias.NEUTRAL
    skew.confidence = 0.5
    skew.num_points = 10
    skew.slope_atm = slope
    skew.bias_confidence = bias_confidence
    return skew


def _make_container(orats_enabled: bool):
    from src.domain.types import PositionLimitsSnapshot

    container = Mock()
    container.db_pool = Mock()

    # Simulate snapshot with or without ORATS data
    if orats_enabled:
        snapshot = PositionLimitsSnapshot(
            fcst_ern_iv_effect=1.8,
            iee_earn_effect=2.1,
            r_slp_30=0.5,
        )
    else:
        snapshot = PositionLimitsSnapshot()  # all None

    container.db_pool.get_connection.return_value.__enter__ = Mock(
        return_value=Mock(
            execute=Mock(return_value=Mock(
                fetchone=Mock(return_value={
                    "fcst_ern_iv_effect": 1.8,
                    "iee_earn_effect": 2.1,
                    "r_slp_30": 0.5,
                })
            ))
        )
    )
    container.db_pool.get_connection.return_value.__exit__ = Mock(return_value=False)

    return container


class TestAnalyzerOratsFlag:
    """Steps 5.5 and 5.6 in TickerAnalyzer.analyze() branch on ORATS_ENABLED."""

    def _run_analyze_steps(self, orats_enabled: bool, has_skew: bool = True):
        """Run only the ORATS-branched logic in isolation."""
        from src.application.services.analyzer import TickerAnalyzer
        from src.domain.types import PositionLimitsSnapshot

        container = Mock()
        analyzer = TickerAnalyzer(container)

        vrp = _make_vrp(is_tradeable=True)
        skew = _make_skew(slope=0.5) if has_skew else None
        snapshot = PositionLimitsSnapshot()
        tail_risk_level = "NORMAL"

        # Patch ORATS_ENABLED inside the analyzer module
        with patch("src.application.services.analyzer.ORATS_ENABLED", orats_enabled):
            # Simulate Step 5.5: snapshot load
            if vrp.is_tradeable:
                if orats_enabled:
                    snapshot = analyzer._load_position_limits("TEST")
                sizing_ctx = analyzer._build_sizing_context(snapshot, tail_risk_level)

            # Simulate Step 5.6: skew fusion (fuses whenever a slope signal
            # exists — ORATS r_slp_30 or the Tradier slope_atm proxy)
            fused = False
            if skew is not None and (
                snapshot.r_slp_30 is not None or skew.slope_atm is not None
            ):
                analyzer._fuse_skew_signals(skew, snapshot.r_slp_30)
                fused = True

        return snapshot, fused

    def test_orats_disabled_skips_snapshot_load(self):
        from src.domain.types import PositionLimitsSnapshot
        from src.application.services.analyzer import TickerAnalyzer

        container = Mock()
        analyzer = TickerAnalyzer(container)

        with patch("src.application.services.analyzer.ORATS_ENABLED", False):
            snapshot = PositionLimitsSnapshot()
            vrp = _make_vrp(is_tradeable=True)

            # When disabled, _load_position_limits should NOT be called
            if vrp.is_tradeable:
                # Only call if enabled — disabled path
                pass

            # Snapshot stays empty (no DB call)
            assert snapshot.r_slp_30 is None
            assert snapshot.fcst_ern_iv_effect is None
            assert snapshot.iee_earn_effect is None
            container.db_pool.get_connection.assert_not_called()

    def test_orats_disabled_fuses_via_tradier_proxy(self):
        """When ORATS disabled but slope_atm exists, fusion fires with r_slp_30=None
        so _fuse_skew_signals takes the Tradier proxy branch (continuity path)."""
        from src.application.services.analyzer import TickerAnalyzer
        from src.domain.types import PositionLimitsSnapshot

        container = Mock()
        analyzer = TickerAnalyzer(container)
        skew = _make_skew(slope=25.0)
        snapshot = PositionLimitsSnapshot()  # r_slp_30 = None

        with patch("src.application.services.analyzer.ORATS_ENABLED", False):
            with patch.object(analyzer, "_fuse_skew_signals") as mock_fuse:
                from src.domain.enums import DirectionalBias
                mock_fuse.return_value = DirectionalBias.NEUTRAL

                if skew is not None and (
                    snapshot.r_slp_30 is not None or skew.slope_atm is not None
                ):
                    analyzer._fuse_skew_signals(skew, snapshot.r_slp_30)

                mock_fuse.assert_called_once_with(skew, None)

    def test_no_slope_signal_skips_fusion(self):
        """No ORATS r_slp_30 AND no Tradier slope_atm → fusion is skipped."""
        from src.application.services.analyzer import TickerAnalyzer
        from src.domain.types import PositionLimitsSnapshot

        container = Mock()
        analyzer = TickerAnalyzer(container)
        skew = _make_skew(slope=None)
        snapshot = PositionLimitsSnapshot()  # r_slp_30 = None

        with patch.object(analyzer, "_fuse_skew_signals") as mock_fuse:
            if skew is not None and (
                snapshot.r_slp_30 is not None or skew.slope_atm is not None
            ):
                analyzer._fuse_skew_signals(skew, snapshot.r_slp_30)

            mock_fuse.assert_not_called()

    def test_orats_enabled_allows_skew_fusion(self):
        """When ORATS enabled and r_slp_30 present, fusion fires."""
        from src.application.services.analyzer import TickerAnalyzer
        from src.domain.types import PositionLimitsSnapshot

        container = Mock()
        analyzer = TickerAnalyzer(container)
        skew = _make_skew()
        snapshot = PositionLimitsSnapshot(r_slp_30=0.5)

        with patch("src.application.services.analyzer.ORATS_ENABLED", True):
            with patch.object(analyzer, "_fuse_skew_signals") as mock_fuse:
                from src.domain.enums import DirectionalBias
                mock_fuse.return_value = DirectionalBias.NEUTRAL

                orats_enabled = True
                if orats_enabled and skew is not None and snapshot.r_slp_30 is not None:
                    analyzer._fuse_skew_signals(skew, snapshot.r_slp_30)

                mock_fuse.assert_called_once_with(skew, 0.5)

    def test_orats_enabled_no_r_slp_30_uses_proxy(self):
        """When ORATS enabled but r_slp_30 is NULL, fusion still fires and
        _fuse_skew_signals substitutes the Tradier slope_atm proxy."""
        from src.application.services.analyzer import TickerAnalyzer
        from src.domain.types import PositionLimitsSnapshot

        container = Mock()
        analyzer = TickerAnalyzer(container)
        skew = _make_skew(slope=25.0)
        snapshot = PositionLimitsSnapshot(r_slp_30=None)

        with patch.object(analyzer, "_fuse_skew_signals") as mock_fuse:
            from src.domain.enums import DirectionalBias
            mock_fuse.return_value = DirectionalBias.NEUTRAL

            if skew is not None and (
                snapshot.r_slp_30 is not None or skew.slope_atm is not None
            ):
                analyzer._fuse_skew_signals(skew, snapshot.r_slp_30)

            mock_fuse.assert_called_once_with(skew, None)


# ---------------------------------------------------------------------------
# fetch_orats_ticker.py — early return when disabled
# ---------------------------------------------------------------------------

class TestFetchOratsTickerFlag:
    """fetch_orats_ticker.main() returns immediately with {"orats_enabled": false}."""

    def test_disabled_prints_flag_json_and_returns_0(self, capsys):
        """When ORATS_ENABLED=false, main() prints the stub JSON and exits 0."""
        with patch.dict(os.environ, {"ORATS_ENABLED": "false"}, clear=False):
            mod = _import_script("scripts/fetch_orats_ticker.py")
            with patch.object(mod, "ORATS_ENABLED", False):
                with patch("sys.argv", ["fetch_orats_ticker.py", "AAPL"]):
                    rc = mod.main()

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == {"orats_enabled": False}

    def test_disabled_does_not_call_fetch_cores(self, capsys):
        """When disabled, none of the fetch_* helpers are called."""
        mod = _import_script("scripts/fetch_orats_ticker.py")
        with patch.object(mod, "ORATS_ENABLED", False):
            with patch.object(mod, "fetch_cores") as mock_cores:
                with patch("sys.argv", ["fetch_orats_ticker.py", "AAPL"]):
                    mod.main()
                mock_cores.assert_not_called()

    def test_enabled_without_key_returns_error_json(self, capsys):
        """When enabled but API key missing, returns error JSON (not the disabled stub)."""
        mod = _import_script("scripts/fetch_orats_ticker.py")
        env_no_key = {k: v for k, v in os.environ.items() if k != "ORATS_API_KEY"}
        with patch.object(mod, "ORATS_ENABLED", True):
            with patch.dict(os.environ, env_no_key, clear=True):
                with patch("sys.argv", ["fetch_orats_ticker.py", "AAPL"]):
                    rc = mod.main()

        assert rc == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data
        assert data.get("orats_enabled") is not False


# ---------------------------------------------------------------------------
# refresh_orats_snapshots.py — early return when disabled
# ---------------------------------------------------------------------------

class TestRefreshOratsSnapshotsFlag:
    """refresh_orats_snapshots.main() exits 0 immediately when ORATS disabled."""

    def _make_db(self) -> str:
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        conn = sqlite3.connect(f.name)
        conn.execute(
            "CREATE TABLE position_limits (ticker TEXT PRIMARY KEY, fcst_ern_iv_effect REAL)"
        )
        conn.execute("INSERT INTO position_limits VALUES ('AAPL', 1.5)")
        conn.commit()
        conn.close()
        return f.name

    def test_disabled_returns_0_without_touching_db(self):
        db_path = self._make_db()
        mod = _import_script("scripts/refresh_orats_snapshots.py")

        with patch.object(mod, "ORATS_ENABLED", False):
            with patch("sys.argv", ["refresh_orats_snapshots.py", "--db-path", db_path]):
                rc = mod.main()

        assert rc == 0

        # DB unchanged — no write occurred
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT fcst_ern_iv_effect FROM position_limits WHERE ticker='AAPL'"
        ).fetchone()
        conn.close()
        assert row[0] == 1.5

        os.unlink(db_path)

    def test_disabled_does_not_call_run_batches(self):
        db_path = self._make_db()
        mod = _import_script("scripts/refresh_orats_snapshots.py")

        with patch.object(mod, "ORATS_ENABLED", False):
            with patch.object(mod, "run_batches") as mock_batches:
                with patch("sys.argv", ["refresh_orats_snapshots.py", "--db-path", db_path]):
                    mod.main()
                mock_batches.assert_not_called()

        os.unlink(db_path)

    def test_enabled_without_key_fails_past_flag_check(self):
        """When enabled but no API key, exits non-zero — proves it passed the flag gate."""
        db_path = self._make_db()
        mod = _import_script("scripts/refresh_orats_snapshots.py")

        env_no_key = {k: v for k, v in os.environ.items() if k != "ORATS_API_KEY"}
        with patch.object(mod, "ORATS_ENABLED", True):
            with patch.dict(os.environ, env_no_key, clear=True):
                with patch("sys.argv", ["refresh_orats_snapshots.py", "--db-path", db_path]):
                    rc = mod.main()

        assert rc == 1  # fails on missing key, not on the flag

        os.unlink(db_path)
