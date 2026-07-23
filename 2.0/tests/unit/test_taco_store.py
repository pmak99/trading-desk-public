"""Unit tests for TACO storage — migration 018 + store CRUD (scripts/taco/store.py)."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.infrastructure.database.migrations.migration_manager import MigrationManager


@pytest.fixture
def db_path(tmp_path):
    """Fresh DB migrated to latest schema."""
    path = tmp_path / "test.db"
    mgr = MigrationManager(str(path))
    mgr.migrate()
    return str(path)


class TestMigration018:
    def test_taco_tables_created(self, db_path):
        con = sqlite3.connect(db_path)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "taco_log" in tables
        assert "taco_positions" in tables

    def test_taco_positions_columns(self, db_path):
        con = sqlite3.connect(db_path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(taco_positions)")}
        assert {"direction", "symbol", "contracts", "strike", "expiration",
                "premium_paid", "entry_date", "event_date", "pre_event_high",
                "panic_low", "euphoria_high", "rip_base", "pre_event_vix",
                "status", "exit_date", "proceeds", "outcome_pnl",
                "rule_compliant"} <= cols

    def test_migration_idempotent(self, db_path):
        # migrate() again applies nothing and doesn't error
        assert MigrationManager(db_path).migrate() == 0


from datetime import date  # noqa: E402

from scripts.taco.market_data import EntryRefs  # noqa: E402
from scripts.taco.store import (  # noqa: E402
    close_position,
    log_run,
    open_positions,
    record_position,
)

CALL_REFS = EntryRefs("CALL", date(2026, 6, 26), 100.0, 90.0, None, None, 15.0)


class TestStore:
    def test_log_run_inserts(self, db_path):
        rowid = log_run(db_path, mode="check", direction="CALL", spot=7546.0,
                        drawdown_pct=3.2, vix=22.0, vix_spike=1.3,
                        vix_term_ratio=1.02, event_date="2026-07-10",
                        event_type="GEOPOLITICAL",
                        event_rationale="test headline", score=61.0,
                        tier="HALF", recommendation="HALF ~$84k SPX calls")
        assert rowid > 0

    def test_log_run_rejects_unknown_fields(self, db_path):
        with pytest.raises(ValueError):
            log_run(db_path, mode="check", bogus_column=1)

    def test_record_close_roundtrip(self, db_path):
        pid = record_position(
            db_path, direction="CALL", symbol="SPX", contracts=5,
            strike=7800.0, expiration="2027-06-18", premium_paid=150_000.0,
            entry_date="2026-07-14", event_date="2026-07-10", refs=CALL_REFS)
        opens = open_positions(db_path)
        assert len(opens) == 1
        assert opens[0]["panic_low"] == 90.0
        assert opens[0]["rule_compliant"] == 1

        pnl = close_position(db_path, pid, "2026-07-20", 180_000.0)
        assert pnl == 30_000.0
        assert open_positions(db_path) == []

    def test_close_missing_position_raises(self, db_path):
        with pytest.raises(ValueError):
            close_position(db_path, 999, "2026-07-20", 1.0)

    def test_rule_compliant_zero_persists(self, db_path):
        record_position(
            db_path, direction="PUT", symbol="SPX", contracts=12,
            strike=7450.0, expiration="2027-04-16", premium_paid=401_650.0,
            entry_date="2026-07-01", event_date="2026-06-25",
            refs=EntryRefs("PUT", date(2026, 6, 25), 7500.0, None, 7550.0,
                           7400.0, 17.0),
            rule_compliant=0)
        assert open_positions(db_path)[0]["rule_compliant"] == 0

    def test_log_run_accepts_cross_asset_fields(self, db_path):
        """migration 019: cross-asset forward-validation columns."""
        rid = log_run(db_path, mode="check", direction="CALL",
                      cross_asset_count=2, cross_asset_available=3,
                      cross_asset_detail='[{"symbol": "TLT"}]')
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM taco_log WHERE id=?", (rid,)).fetchone()
        assert row["cross_asset_count"] == 2
        assert row["cross_asset_available"] == 3
        assert "TLT" in row["cross_asset_detail"]
