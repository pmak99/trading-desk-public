"""Unit tests for cleanup_duplicate_moves.py (Jul 2026 audit).

historical_moves contains adjacent-date duplicate events (same ticker,
dates within a few days) written by backfills that consumed a calendar
holding both the announce and reaction date. Each pair double-counts a
quarter in VRP/TRR baselines. A company cannot report twice within the
window, so any same-ticker in-window pair is one event.

Two resolvable classes are auto-deleted:
  * identical close_move_pct (any enrichment), OR
  * differing close_move_pct but ONE row is ORATS-enriched (the enriched
    row is the real event; the bare row is a no-ORATS backfill artifact).
Keep-rule: more ORATS enrichment wins; tie -> earlier date (announce-date
convention). The loser's earnings_calendar row for that ticker is removed
so re-running backfills cannot recreate it.

Differing close_move_pct with EQUAL enrichment (e.g. both bare) cannot be
resolved from stored data alone — the script flags these as `ambiguous`
for manual price verification and never deletes them.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.cleanup_duplicate_moves import dedupe_duplicate_moves


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "moves.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE historical_moves ("
        "id INTEGER PRIMARY KEY, ticker TEXT, earnings_date DATE, "
        "close_move_pct REAL, pre_earnings_straddle_pct REAL, ern_iv_effect REAL)"
    )
    conn.execute(
        "CREATE TABLE earnings_calendar ("
        "id INTEGER PRIMARY KEY, ticker TEXT, earnings_date DATE)"
    )
    conn.commit()
    conn.close()
    return str(path)


def _insert(db_path, table, rows):
    conn = sqlite3.connect(db_path)
    for r in rows:
        cols = ",".join(r.keys())
        ph = ",".join("?" * len(r))
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", list(r.values()))
    conn.commit()
    conn.close()


def _dates(db_path, table, ticker):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        f"SELECT earnings_date FROM {table} WHERE ticker=? ORDER BY earnings_date",
        (ticker,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


class TestDedupe:
    def test_bare_pair_keeps_earlier(self, db):
        _insert(db, "historical_moves", [
            dict(ticker="FDX", earnings_date="2025-12-18", close_move_pct=0.578),
            dict(ticker="FDX", earnings_date="2025-12-19", close_move_pct=0.578),
        ])
        _insert(db, "earnings_calendar", [
            dict(ticker="FDX", earnings_date="2025-12-18"),
            dict(ticker="FDX", earnings_date="2025-12-19"),
        ])
        result = dedupe_duplicate_moves(db, dry_run=False)
        assert result["moves_deleted"] == 1
        assert _dates(db, "historical_moves", "FDX") == ["2025-12-18"]
        assert _dates(db, "earnings_calendar", "FDX") == ["2025-12-18"]

    def test_enriched_later_row_wins(self, db):
        # SBUX case: only the later date carries ORATS enrichment
        _insert(db, "historical_moves", [
            dict(ticker="SBUX", earnings_date="2026-01-27", close_move_pct=-0.585),
            dict(ticker="SBUX", earnings_date="2026-01-28", close_move_pct=-0.585,
                 pre_earnings_straddle_pct=7.31, ern_iv_effect=2.37),
        ])
        _insert(db, "earnings_calendar", [
            dict(ticker="SBUX", earnings_date="2026-01-27"),
            dict(ticker="SBUX", earnings_date="2026-01-28"),
        ])
        dedupe_duplicate_moves(db, dry_run=False)
        assert _dates(db, "historical_moves", "SBUX") == ["2026-01-28"]
        assert _dates(db, "earnings_calendar", "SBUX") == ["2026-01-28"]

    def test_both_enriched_keeps_earlier(self, db):
        _insert(db, "historical_moves", [
            dict(ticker="AES", earnings_date="2026-02-26", close_move_pct=6.338,
                 pre_earnings_straddle_pct=3.78, ern_iv_effect=1.68),
            dict(ticker="AES", earnings_date="2026-02-27", close_move_pct=6.338,
                 pre_earnings_straddle_pct=5.60, ern_iv_effect=6.52),
        ])
        dedupe_duplicate_moves(db, dry_run=False)
        assert _dates(db, "historical_moves", "AES") == ["2026-02-26"]

    def test_differing_moves_equal_enrichment_flagged_ambiguous(self, db):
        # Adjacent dates, different close moves, neither enriched: cannot pick
        # the keeper from stored data - flag, never delete (MU/OKTA case).
        _insert(db, "historical_moves", [
            dict(ticker="XYZ", earnings_date="2026-02-26", close_move_pct=1.0),
            dict(ticker="XYZ", earnings_date="2026-02-27", close_move_pct=5.0),
        ])
        result = dedupe_duplicate_moves(db, dry_run=False)
        assert result["moves_deleted"] == 0
        assert len(_dates(db, "historical_moves", "XYZ")) == 2
        assert [p["ticker"] for p in result["ambiguous"]] == ["XYZ"]

    def test_two_day_identical_value_caught(self, db):
        # 2-day gap (not 1) with identical values - the class the <=1 query
        # silently missed and that resurrected after the Jul 3 cleanup.
        _insert(db, "historical_moves", [
            dict(ticker="COF", earnings_date="2026-01-22", close_move_pct=-7.56,
                 pre_earnings_straddle_pct=4.1, ern_iv_effect=1.5),
            dict(ticker="COF", earnings_date="2026-01-24", close_move_pct=-7.56),
        ])
        _insert(db, "earnings_calendar", [
            dict(ticker="COF", earnings_date="2026-01-22"),
            dict(ticker="COF", earnings_date="2026-01-24"),
        ])
        result = dedupe_duplicate_moves(db, dry_run=False)
        assert result["moves_deleted"] == 1
        assert _dates(db, "historical_moves", "COF") == ["2026-01-22"]
        assert _dates(db, "earnings_calendar", "COF") == ["2026-01-22"]

    def test_two_day_differing_value_enriched_wins(self, db):
        # 2-day gap, DIFFERENT values, only the earlier row enriched: the bare
        # later row is the resurrected artifact (ABM/EPAC/STLD case).
        _insert(db, "historical_moves", [
            dict(ticker="ABM", earnings_date="2025-12-17", close_move_pct=5.49,
                 pre_earnings_straddle_pct=6.0, ern_iv_effect=2.1),
            dict(ticker="ABM", earnings_date="2025-12-19", close_move_pct=-2.99),
        ])
        result = dedupe_duplicate_moves(db, dry_run=False)
        assert result["moves_deleted"] == 1
        assert _dates(db, "historical_moves", "ABM") == ["2025-12-17"]
        assert result["ambiguous"] == []

    def test_two_day_differing_value_enriched_later_wins(self, db):
        # SLP inversion: enrichment is on the LATER date (its true report day),
        # so the earlier row is the artifact and gets deleted.
        _insert(db, "historical_moves", [
            dict(ticker="SLP", earnings_date="2026-01-06", close_move_pct=-1.45),
            dict(ticker="SLP", earnings_date="2026-01-08", close_move_pct=8.59,
                 pre_earnings_straddle_pct=5.2, ern_iv_effect=1.9),
        ])
        result = dedupe_duplicate_moves(db, dry_run=False)
        assert result["moves_deleted"] == 1
        assert _dates(db, "historical_moves", "SLP") == ["2026-01-08"]

    def test_three_days_apart_untouched(self, db):
        # Beyond the window: distinct real quarters must never be joined.
        _insert(db, "historical_moves", [
            dict(ticker="ZZZ", earnings_date="2026-02-20", close_move_pct=1.0),
            dict(ticker="ZZZ", earnings_date="2026-02-24", close_move_pct=1.0),
        ])
        result = dedupe_duplicate_moves(db, dry_run=False)
        assert result["moves_deleted"] == 0
        assert result["ambiguous"] == []
        assert len(_dates(db, "historical_moves", "ZZZ")) == 2

    def test_dry_run_deletes_nothing(self, db):
        _insert(db, "historical_moves", [
            dict(ticker="FDX", earnings_date="2025-12-18", close_move_pct=0.578),
            dict(ticker="FDX", earnings_date="2025-12-19", close_move_pct=0.578),
        ])
        result = dedupe_duplicate_moves(db, dry_run=True)
        assert result["moves_deleted"] == 1  # planned
        assert len(_dates(db, "historical_moves", "FDX")) == 2  # not executed
        assert result["dry_run"] is True

    def test_calendar_only_loser_date_removed_for_that_ticker(self, db):
        # Another ticker legitimately reporting on the loser date is untouched
        _insert(db, "historical_moves", [
            dict(ticker="FDX", earnings_date="2025-12-18", close_move_pct=0.578),
            dict(ticker="FDX", earnings_date="2025-12-19", close_move_pct=0.578),
        ])
        _insert(db, "earnings_calendar", [
            dict(ticker="FDX", earnings_date="2025-12-19"),
            dict(ticker="OTHER", earnings_date="2025-12-19"),
        ])
        dedupe_duplicate_moves(db, dry_run=False)
        assert _dates(db, "earnings_calendar", "OTHER") == ["2025-12-19"]
        assert _dates(db, "earnings_calendar", "FDX") == []
