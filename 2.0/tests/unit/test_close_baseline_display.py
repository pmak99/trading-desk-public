"""
Tests for gap-inclusive (close-to-close) VRP display — vrp_close_ratio field on
TickerAnalysis and the divergence helper in scripts/analyze.py.
"""

import sys
from datetime import date, datetime
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.domain.types import (
    TickerAnalysis,
    ImpliedMove,
    VRPResult,
    Money,
    Percentage,
    Strike,
)
from src.domain.enums import EarningsTiming, Recommendation

# Import helpers from analyze script
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from analyze import vrp_tier, _log_vrp_close_divergence


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

TICKER = "TEST"
EARNINGS_DATE = date(2026, 6, 20)
EXPIRATION = date(2026, 6, 26)


def _minimal_analysis(vrp_ratio=2.0, vrp_close=None, close_mean=None):
    """Build a minimal TickerAnalysis with the requested VRP values."""
    implied_move = ImpliedMove(
        ticker=TICKER,
        expiration=EXPIRATION,
        stock_price=Money(100.0),
        atm_strike=Strike(100.0),
        straddle_cost=Money(5.0),
        implied_move_pct=Percentage(5.0),
        upper_bound=Money(105.0),
        lower_bound=Money(95.0),
    )
    vrp = VRPResult(
        ticker=TICKER,
        expiration=EXPIRATION,
        implied_move_pct=Percentage(5.0),
        historical_mean_move_pct=Percentage(5.0 / vrp_ratio),
        vrp_ratio=vrp_ratio,
        edge_score=0.5,
        recommendation=Recommendation.EXCELLENT if vrp_ratio >= 1.8 else Recommendation.GOOD,
    )
    return TickerAnalysis(
        ticker=TICKER,
        earnings_date=EARNINGS_DATE,
        earnings_timing=EarningsTiming.AMC,
        entry_time=datetime(2026, 6, 19, 12, 0),
        expiration=EXPIRATION,
        implied_move=implied_move,
        vrp=vrp,
        vrp_close_ratio=vrp_close,
        historical_close_mean_pct=close_mean,
    )


# ---------------------------------------------------------------------------
# TickerAnalysis field tests
# ---------------------------------------------------------------------------


class TestTickerAnalysisCloseFields:
    def test_stores_vrp_close_ratio(self):
        analysis = _minimal_analysis(vrp_ratio=2.0, vrp_close=1.5, close_mean=3.0)
        assert analysis.vrp_close_ratio == pytest.approx(1.5)
        assert analysis.historical_close_mean_pct == pytest.approx(3.0)

    def test_defaults_to_none_for_backward_compat(self):
        analysis = _minimal_analysis(vrp_ratio=2.0)
        assert analysis.vrp_close_ratio is None
        assert analysis.historical_close_mean_pct is None

    def test_stores_zero_close_ratio(self):
        # Edge: explicitly set to 0 (should not be confused with None)
        analysis = _minimal_analysis(vrp_close=0.0, close_mean=0.0)
        assert analysis.vrp_close_ratio == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# vrp_tier helper
# ---------------------------------------------------------------------------


class TestVrpTier:
    @pytest.mark.parametrize("ratio,expected", [
        (1.8, "EXCELLENT"),
        (2.5, "EXCELLENT"),
        (1.4, "GOOD"),
        (1.79, "GOOD"),
        (1.2, "MARGINAL"),
        (1.39, "MARGINAL"),
        (1.19, "SKIP"),
        (0.9, "SKIP"),
        (0.0, "SKIP"),
    ])
    def test_tier_boundaries(self, ratio, expected):
        assert vrp_tier(ratio) == expected


# ---------------------------------------------------------------------------
# _log_vrp_close_divergence helper
# ---------------------------------------------------------------------------


class TestVrpDivergenceLogger:
    def _collect_logs(self, intraday_ratio, close_ratio, close_mean):
        """Capture log lines emitted by _log_vrp_close_divergence."""
        lines = []
        _log_vrp_close_divergence(lines.append, intraday_ratio, close_ratio, close_mean)
        return lines

    def test_same_tier_no_divergence_warning(self):
        # Both GOOD — no divergence message expected
        lines = self._collect_logs(1.5, 1.6, 3.0)
        assert any("gap-incl." in l for l in lines), "Should show gap-incl. line"
        assert not any("DIVERGENCE" in l or "NOTE:" in l for l in lines), \
            "Same tier should produce no warning"

    def test_intraday_inflated_fires_warning(self):
        # Intraday EXCELLENT (2.6x), gap-inclusive SKIP (1.04x)
        lines = self._collect_logs(2.6, 1.04, 4.7)
        assert any("DIVERGENCE" in l for l in lines), \
            "Should warn when intraday tier > gap-incl. tier"
        assert any("inflated" in l.lower() for l in lines)

    def test_gap_incl_stronger_fires_note(self):
        # Intraday SKIP (1.1x), gap-inclusive GOOD (1.5x) — reversal ticker
        lines = self._collect_logs(1.1, 1.5, 2.0)
        assert any("NOTE:" in l for l in lines), \
            "Should note when gap-incl. tier > intraday tier"
        assert any("reversal" in l.lower() for l in lines)

    def test_shows_close_ratio_and_mean(self):
        lines = self._collect_logs(2.0, 1.5, 3.2)
        assert any("1.50x" in l for l in lines)
        assert any("3.2%" in l for l in lines)

    def test_orcl_scenario(self):
        # ORCL: intraday 2.59x EXCELLENT vs gap-inclusive 1.04x SKIP
        lines = self._collect_logs(2.59, 1.04, 4.7)
        assert any("DIVERGENCE" in l for l in lines)
        assert any("gap-dominant" in l.lower() for l in lines)
