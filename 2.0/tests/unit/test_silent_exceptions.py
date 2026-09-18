"""Failures that used to vanish must reach the log, with their traceback.

- The analysis_log write in the scan workflow was `except Exception: pass`.
  That row is the entire dataset /ab-status evaluates, so a broken write
  would starve the A/B without a single log line.
- The VIX-regime warning in scan and /analyze was `except Exception: pass`,
  so a broken market-conditions fetch silently dropped the elevated-VIX
  warning.
- The one-position-per-event check logged its failure at DEBUG, invisible at
  the default level, so a broken guard looked like "no prior position".
"""

import logging
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock


def _warned_with_traceback(caplog, needle):
    hits = [r for r in caplog.records
            if r.levelno >= logging.WARNING and needle in r.getMessage()]
    assert hits, f"no WARNING containing {needle!r}: {[r.getMessage() for r in caplog.records]}"
    assert hits[0].exc_info is not None, "logged without the traceback"


def test_a_failed_analysis_log_write_is_a_warning(monkeypatch, caplog):
    from scripts.scan.workflows import ticker_analysis as ta
    from src.domain.errors import AppError, Err, ErrorCode

    monkeypatch.setattr(ta, "TickerAnalysis", lambda **kw: kw)
    monkeypatch.setattr(ta, "logged_vrp_columns", lambda vrp, moves: {
        "vrp_intraday_ratio": 1.5, "historical_intraday_mean_pct": 5.0,
        "vrp_close_ratio": 1.0, "historical_close_mean_pct": 7.5})
    container = MagicMock()
    container.cached_options_provider.get_expirations.return_value = Err(
        AppError(ErrorCode.NODATA, "no chain in this test"))    # term slope: None
    container.analysis_repository.log_analysis.side_effect = RuntimeError("boom")
    implied = MagicMock()
    implied.implied_move_pct.value = 7.5

    with caplog.at_level(logging.INFO):
        ta._log_analysis_row(container, "MU", date(2026, 9, 23), "AMC", date(2026, 9, 25),
                             implied, MagicMock(), [], "scan")

    container.analysis_repository.log_analysis.assert_called_once()
    _warned_with_traceback(caplog, "MU")


def test_a_failed_vix_fetch_in_scan_is_a_warning(caplog):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "scan" / "workflows"))
    from vix import _log_vix_context

    container = MagicMock()
    container.market_conditions_analyzer.get_current_conditions.side_effect = RuntimeError("boom")
    with caplog.at_level(logging.INFO):
        _log_vix_context(container)
    _warned_with_traceback(caplog, "VIX")


def test_analyze_uses_the_shared_vix_context_instead_of_a_silent_copy():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "analyze.py").read_text()
    assert "_log_vix_context(container)" in src
    assert "except Exception:\n            pass" not in src


def test_a_failed_existing_position_check_is_a_warning(caplog):
    from src.application.services.existing_position import existing_position_warning

    pool = MagicMock()
    pool.connection.side_effect = RuntimeError("pool down")
    with caplog.at_level(logging.INFO):
        assert existing_position_warning(pool, "MU", date(2025, 12, 19)) is None
    _warned_with_traceback(caplog, "MU")
