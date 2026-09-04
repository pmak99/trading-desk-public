"""Unit tests for compute_live_trr.py.

Ten `.claude/commands/*.md` files each embedded ad hoc SQL against the
frozen (2026-06-24) `position_limits.tail_risk_ratio`/`tail_risk_level`
columns, which the live engine (`analyzer._compute_tail_risk_level`,
`prices_repository.get_historical_moves`) never reads — an Aug 25 2026
audit found 6 of 14 sampled tickers had the wrong TRR *tier* between the
frozen snapshot and a live recompute. This module is the single
engine-matching TRR computation every command should call instead of
re-deriving its own SQL: same source table (`historical_moves`), same
column (`gap_move_pct`, not intraday), same window (12 most recent
quarters by `earnings_date DESC`), same thresholds (>2.5 HIGH, >=1.5
NORMAL, else LOW), and — unlike the ad hoc SQL versions — the same
`quarters < 2` "unknown" guard the engine applies, instead of silently
returning LOW.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.compute_live_trr import compute_trr


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "moves.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE historical_moves ("
        "id INTEGER PRIMARY KEY, ticker TEXT, earnings_date DATE, gap_move_pct REAL)"
    )
    conn.commit()
    yield path
    conn.close()


def _insert(db_path, ticker, earnings_date, gap_move_pct):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO historical_moves (ticker, earnings_date, gap_move_pct) VALUES (?, ?, ?)",
        (ticker, earnings_date, gap_move_pct),
    )
    conn.commit()
    conn.close()


class TestComputeTrr:
    def test_high_tier_above_2_5(self, db):
        """max/avg > 2.5 -> HIGH, matching analyzer._compute_tail_risk_level."""
        _insert(db, "ABBV", "2026-01-01", 1.0)
        _insert(db, "ABBV", "2026-04-01", 1.0)
        _insert(db, "ABBV", "2026-07-01", 20.0)
        # max=20.0, avg=22/3=7.333, trr=2.727 -> HIGH
        result = compute_trr(str(db), ["ABBV"])
        assert result["ABBV"]["trr_level"] == "HIGH"

    def test_boundary_at_2_5_is_normal_not_high(self, db):
        """analyzer uses `trr > 2.5` (strict), so exactly 2.5 is NORMAL."""
        _insert(db, "BA", "2026-01-01", 1.0)
        _insert(db, "BA", "2026-04-01", 1.0)
        _insert(db, "BA", "2026-07-01", 8.0)
        # max=8.0, avg=10/3=3.3333, trr=2.4 -> NORMAL
        result = compute_trr(str(db), ["BA"])
        assert result["BA"]["trr_level"] == "NORMAL"

    def test_normal_tier_between_1_5_and_2_5(self, db):
        _insert(db, "MSFT", "2026-01-01", 2.0)
        _insert(db, "MSFT", "2026-04-01", 2.0)
        _insert(db, "MSFT", "2026-07-01", 4.0)
        # max=4.0, avg=8/3=2.667, trr=1.5 exactly -> NORMAL (boundary is inclusive >=1.5)
        result = compute_trr(str(db), ["MSFT"])
        assert result["MSFT"]["trr_level"] == "NORMAL"
        assert result["MSFT"]["quarters"] == 3
        assert round(result["MSFT"]["trr"], 2) == 1.5

    def test_low_tier_below_1_5(self, db):
        _insert(db, "AAPL", "2026-01-01", 2.0)
        _insert(db, "AAPL", "2026-04-01", 2.0)
        _insert(db, "AAPL", "2026-07-01", 2.4)
        # max=2.4, avg=6.4/3=2.133, trr=1.125 -> LOW
        result = compute_trr(str(db), ["AAPL"])
        assert result["AAPL"]["trr_level"] == "LOW"

    def test_fewer_than_2_quarters_is_unknown_not_low(self, db):
        """Matches analyzer._compute_tail_risk_level: len(gap_moves) < 2 -> None (unknown)."""
        _insert(db, "AACTF", "2026-01-01", 5.0)
        result = compute_trr(str(db), ["AACTF"])
        assert result["AACTF"]["trr_level"] is None
        assert result["AACTF"]["quarters"] == 1

    def test_zero_quarters_ticker_still_present_not_dropped(self, db):
        """A ticker with no historical_moves rows must appear in the result
        (as unknown), not vanish silently — the ad hoc SQL versions this
        replaces dropped absent tickers entirely from their output."""
        result = compute_trr(str(db), ["NOHISTORY"])
        assert "NOHISTORY" in result
        assert result["NOHISTORY"]["trr_level"] is None
        assert result["NOHISTORY"]["quarters"] == 0

    def test_only_most_recent_12_quarters_used(self, db):
        """Matches prices_repository.get_historical_moves: ORDER BY
        earnings_date DESC LIMIT 12 — an older 13th quarter must not
        affect the computation."""
        # 12 recent quarters, all gap_move_pct = 1.0 (trr would be 1.0 -> LOW)
        for i in range(12):
            _insert(db, "NVDA", f"2026-{(i % 12) + 1:02d}-01", 1.0)
        # A 13th, much older, huge outlier quarter that must be excluded
        _insert(db, "NVDA", "2015-01-01", 50.0)
        result = compute_trr(str(db), ["NVDA"])
        assert result["NVDA"]["quarters"] == 12
        assert result["NVDA"]["max_move"] == 1.0
        assert result["NVDA"]["trr_level"] == "LOW"

    def test_batch_multiple_tickers(self, db):
        _insert(db, "AAPL", "2026-01-01", 2.0)
        _insert(db, "AAPL", "2026-04-01", 2.0)
        _insert(db, "MSFT", "2026-01-01", 1.0)
        _insert(db, "MSFT", "2026-04-01", 1.0)
        result = compute_trr(str(db), ["AAPL", "MSFT"])
        assert set(result.keys()) == {"AAPL", "MSFT"}
