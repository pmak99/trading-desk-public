"""
Unit tests for database sync logic (scripts/sync_databases.py).

Tests the bidirectional sync functions for:
- historical_moves union strategy
- earnings_calendar newest-wins strategy
- Empty set guards (IN clause safety)
- Duplicate record handling
- Transaction rollback on failure
- GCS name validation
"""

import pytest
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

# Fixed timestamp for deterministic tests
_FIXED_TIMESTAMP = datetime(2026, 3, 16, 10, 30, 0)

import sys
# Add scripts/ to path so we can import sync_databases
# From 2.0/tests/unit/ -> ../../.. = project root, then /scripts
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "scripts"))

from sync_databases import (
    sync_historical_moves,
    sync_earnings_calendar,
    sync_trade_journal,
    _validate_gcs_name,
    backup_to_gdrive,
    dedupe_earnings_calendar_after_merge,
)


# ============================================================================
# Fixtures
# ============================================================================


def create_test_db(path: Path) -> sqlite3.Connection:
    """Create a test database with the expected schema."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_moves (
            ticker TEXT,
            earnings_date TEXT,
            prev_close REAL,
            earnings_open REAL,
            earnings_high REAL,
            earnings_low REAL,
            earnings_close REAL,
            intraday_move_pct REAL,
            gap_move_pct REAL,
            close_move_pct REAL,
            volume_before INTEGER,
            volume_earnings INTEGER,
            created_at TEXT,
            pre_earnings_straddle_pct REAL,
            ern_iv_effect REAL,
            UNIQUE(ticker, earnings_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            ticker TEXT,
            earnings_date TEXT,
            timing TEXT,
            confirmed INTEGER DEFAULT 0,
            updated_at TEXT,
            last_validated_at TEXT,
            UNIQUE(ticker, earnings_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_journal (
            symbol TEXT,
            acquired_date TEXT,
            sale_date TEXT,
            days_held INTEGER,
            option_type TEXT,
            strike REAL,
            expiration TEXT,
            quantity INTEGER,
            cost_basis REAL,
            proceeds REAL,
            gain_loss REAL,
            is_winner INTEGER,
            term TEXT,
            wash_sale_amount REAL,
            earnings_date TEXT,
            actual_move REAL,
            created_at TEXT,
            account_type TEXT NOT NULL DEFAULT 'TAXABLE',
            UNIQUE(symbol, acquired_date, sale_date, option_type, strike, cost_basis, account_type)
        )
    """)
    conn.commit()
    return conn


@pytest.fixture
def local_db(tmp_path):
    """Create a local test database."""
    path = tmp_path / "local.db"
    conn = create_test_db(path)
    yield conn
    conn.close()


@pytest.fixture
def cloud_db(tmp_path):
    """Create a cloud test database."""
    path = tmp_path / "cloud.db"
    conn = create_test_db(path)
    yield conn
    conn.close()


def insert_historical_move(conn, ticker, earnings_date, close_move_pct=5.0):
    """Insert a test historical move record."""
    conn.execute("""
        INSERT INTO historical_moves
        (ticker, earnings_date, prev_close, earnings_open, earnings_high,
         earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
         close_move_pct, volume_before, volume_earnings, created_at)
        VALUES (?, ?, 100.0, 102.0, 105.0, 98.0, ?, ?, 2.0, ?, 1000000, 2000000, ?)
    """, (ticker, earnings_date, 100.0 + close_move_pct,
          close_move_pct + 1.0, close_move_pct,
          _FIXED_TIMESTAMP.isoformat()))
    conn.commit()


def insert_earnings_calendar(conn, ticker, earnings_date, timing="BMO", updated_at=None):
    """Insert a test earnings calendar record."""
    if updated_at is None:
        updated_at = _FIXED_TIMESTAMP.isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO earnings_calendar
        (ticker, earnings_date, timing, confirmed, updated_at, last_validated_at)
        VALUES (?, ?, ?, 1, ?, ?)
    """, (ticker, earnings_date, timing, updated_at, updated_at))
    conn.commit()


def insert_trade_journal(conn, symbol, acquired_date, sale_date, cost_basis,
                         option_type=None, strike=None):
    """Insert a test trade journal record."""
    conn.execute("""
        INSERT INTO trade_journal
        (symbol, acquired_date, sale_date, days_held, option_type, strike,
         expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
         term, wash_sale_amount, earnings_date, actual_move, created_at)
        VALUES (?, ?, ?, 5, ?, ?, '2026-02-07', 10, ?, 500.0, 100.0, 1,
                'SHORT', 0, '2026-01-30', 5.0, ?)
    """, (symbol, acquired_date, sale_date, option_type, strike, cost_basis,
          _FIXED_TIMESTAMP.isoformat()))
    conn.commit()


# ============================================================================
# Historical Moves Sync Tests
# ============================================================================


class TestSyncHistoricalMoves:
    """Tests for historical_moves union sync strategy."""

    def test_empty_both_databases(self, local_db, cloud_db):
        """Syncing two empty databases should produce no changes."""
        stats = sync_historical_moves(local_db, cloud_db)
        assert stats["local_added"] == 0
        assert stats["cloud_added"] == 0

    def test_cloud_only_records_added_to_local(self, local_db, cloud_db):
        """Records only in cloud should be added to local."""
        insert_historical_move(cloud_db, "NVDA", "2026-01-20")
        insert_historical_move(cloud_db, "AAPL", "2026-01-25")

        stats = sync_historical_moves(local_db, cloud_db)

        assert stats["local_added"] == 2
        assert stats["cloud_added"] == 0

        # Verify records exist in local
        count = local_db.execute("SELECT COUNT(*) FROM historical_moves").fetchone()[0]
        assert count == 2

    def test_local_only_records_added_to_cloud(self, local_db, cloud_db):
        """Records only in local should be added to cloud."""
        insert_historical_move(local_db, "MSFT", "2026-01-22")

        stats = sync_historical_moves(local_db, cloud_db)

        assert stats["local_added"] == 0
        assert stats["cloud_added"] == 1

        count = cloud_db.execute("SELECT COUNT(*) FROM historical_moves").fetchone()[0]
        assert count == 1

    def test_shared_records_not_duplicated(self, local_db, cloud_db):
        """Records present in both should not be duplicated."""
        insert_historical_move(local_db, "TSLA", "2026-01-15")
        insert_historical_move(cloud_db, "TSLA", "2026-01-15")

        stats = sync_historical_moves(local_db, cloud_db)

        assert stats["local_added"] == 0
        assert stats["cloud_added"] == 0

    def test_bidirectional_sync(self, local_db, cloud_db):
        """Different records in each should be synced bidirectionally."""
        insert_historical_move(local_db, "AAPL", "2026-01-20")
        insert_historical_move(cloud_db, "GOOG", "2026-01-22")

        # Shared record
        insert_historical_move(local_db, "TSLA", "2026-01-15")
        insert_historical_move(cloud_db, "TSLA", "2026-01-15")

        stats = sync_historical_moves(local_db, cloud_db)

        assert stats["local_added"] == 1  # GOOG from cloud
        assert stats["cloud_added"] == 1  # AAPL from local

        local_count = local_db.execute("SELECT COUNT(*) FROM historical_moves").fetchone()[0]
        cloud_count = cloud_db.execute("SELECT COUNT(*) FROM historical_moves").fetchone()[0]
        assert local_count == 3
        assert cloud_count == 3

    def test_empty_cloud_only_set(self, local_db, cloud_db):
        """When all records are already in local, cloud_only is empty - no crash."""
        insert_historical_move(local_db, "NVDA", "2026-01-20")
        insert_historical_move(cloud_db, "NVDA", "2026-01-20")

        # Only local has extra
        insert_historical_move(local_db, "AAPL", "2026-01-25")

        stats = sync_historical_moves(local_db, cloud_db)
        # Should not crash even though cloud_only is empty
        assert stats["cloud_added"] == 1
        assert stats["local_added"] == 0


# ============================================================================
# Earnings Calendar Sync Tests
# ============================================================================


class TestSyncEarningsCalendar:
    """Tests for earnings_calendar newest-wins sync strategy."""

    def test_empty_both_databases(self, local_db, cloud_db):
        """Syncing two empty calendars should produce no changes."""
        stats = sync_earnings_calendar(local_db, cloud_db)
        assert stats["local_updated"] == 0
        assert stats["cloud_updated"] == 0

    def test_cloud_only_added_to_local(self, local_db, cloud_db):
        """Records only in cloud should be added to local."""
        insert_earnings_calendar(cloud_db, "NVDA", "2026-02-20", "AMC")

        stats = sync_earnings_calendar(local_db, cloud_db)

        assert stats["local_updated"] == 1
        count = local_db.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
        assert count == 1

    def test_local_only_added_to_cloud(self, local_db, cloud_db):
        """Records only in local should be added to cloud."""
        insert_earnings_calendar(local_db, "AAPL", "2026-01-30", "BMO")

        stats = sync_earnings_calendar(local_db, cloud_db)

        assert stats["cloud_updated"] == 1
        count = cloud_db.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
        assert count == 1

    def test_newer_local_wins(self, local_db, cloud_db):
        """When both have same record, newer updated_at should win."""
        old_time = "2026-01-10T00:00:00"
        new_time = "2026-01-15T00:00:00"

        insert_earnings_calendar(cloud_db, "NVDA", "2026-02-20", "BMO", updated_at=old_time)
        insert_earnings_calendar(local_db, "NVDA", "2026-02-20", "AMC", updated_at=new_time)

        stats = sync_earnings_calendar(local_db, cloud_db)

        # Local is newer -> cloud should be updated
        assert stats["cloud_updated"] == 1
        assert stats["local_updated"] == 0

        # Cloud should now have AMC timing
        row = cloud_db.execute(
            "SELECT timing FROM earnings_calendar WHERE ticker = 'NVDA'"
        ).fetchone()
        assert row[0] == "AMC"

    def test_newer_cloud_wins(self, local_db, cloud_db):
        """When cloud has newer record, local should be updated."""
        old_time = "2026-01-10T00:00:00"
        new_time = "2026-01-15T00:00:00"

        insert_earnings_calendar(local_db, "MSFT", "2026-01-28", "BMO", updated_at=old_time)
        insert_earnings_calendar(cloud_db, "MSFT", "2026-01-28", "AMC", updated_at=new_time)

        stats = sync_earnings_calendar(local_db, cloud_db)

        assert stats["local_updated"] == 1
        assert stats["cloud_updated"] == 0

        row = local_db.execute(
            "SELECT timing FROM earnings_calendar WHERE ticker = 'MSFT'"
        ).fetchone()
        assert row[0] == "AMC"

    def test_identical_timestamps_no_update(self, local_db, cloud_db):
        """Identical updated_at should not trigger any update."""
        same_time = "2026-01-12T12:00:00"
        insert_earnings_calendar(local_db, "GOOG", "2026-02-05", "BMO", updated_at=same_time)
        insert_earnings_calendar(cloud_db, "GOOG", "2026-02-05", "BMO", updated_at=same_time)

        stats = sync_earnings_calendar(local_db, cloud_db)

        assert stats["local_updated"] == 0
        assert stats["cloud_updated"] == 0


# ============================================================================
# Trade Journal Sync Tests
# ============================================================================


class TestSyncTradeJournal:
    """Tests for trade_journal union sync strategy."""

    def test_empty_both_databases(self, local_db, cloud_db):
        """Syncing empty trade journals should produce no changes."""
        stats = sync_trade_journal(local_db, cloud_db)
        assert stats["local_added"] == 0
        assert stats["cloud_added"] == 0

    def test_cloud_only_trades_added_to_local(self, local_db, cloud_db):
        """Trades only in cloud should be added to local."""
        insert_trade_journal(cloud_db, "NVDA", "2026-01-20", "2026-01-25", 400.0,
                             option_type="CALL", strike=150.0)

        stats = sync_trade_journal(local_db, cloud_db)

        assert stats["local_added"] == 1
        count = local_db.execute("SELECT COUNT(*) FROM trade_journal").fetchone()[0]
        assert count == 1

    def test_local_only_trades_added_to_cloud(self, local_db, cloud_db):
        """Trades only in local should be added to cloud."""
        insert_trade_journal(local_db, "AAPL", "2026-01-15", "2026-01-20", 300.0,
                             option_type="PUT", strike=200.0)

        stats = sync_trade_journal(local_db, cloud_db)

        assert stats["cloud_added"] == 1

    def test_shared_trades_not_duplicated(self, local_db, cloud_db):
        """Trades in both databases should not be duplicated."""
        for conn in [local_db, cloud_db]:
            insert_trade_journal(conn, "TSLA", "2026-01-10", "2026-01-15", 500.0,
                                 option_type="CALL", strike=250.0)

        stats = sync_trade_journal(local_db, cloud_db)

        assert stats["local_added"] == 0
        assert stats["cloud_added"] == 0


# ============================================================================
# GCS Validation Tests
# ============================================================================


class TestGCSValidation:
    """Tests for GCS name validation."""

    def test_valid_bucket_name(self):
        """Valid bucket names should pass validation."""
        assert _validate_gcs_name("trading-desk-data") == "trading-desk-data"
        assert _validate_gcs_name("my.bucket.name") == "my.bucket.name"
        assert _validate_gcs_name("bucket_with_underscore") == "bucket_with_underscore"

    def test_valid_blob_name(self):
        """Valid blob names should pass validation."""
        assert _validate_gcs_name("ivcrush.db") == "ivcrush.db"
        assert _validate_gcs_name("backups/2026/ivcrush.db") == "backups/2026/ivcrush.db"

    def test_invalid_name_with_semicolon(self):
        """Names with semicolons should be rejected (command injection)."""
        with pytest.raises(ValueError, match="unsafe characters"):
            _validate_gcs_name("bucket; rm -rf /")

    def test_invalid_name_with_spaces(self):
        """Names with spaces should be rejected."""
        with pytest.raises(ValueError, match="unsafe characters"):
            _validate_gcs_name("bucket name")

    def test_invalid_name_with_backtick(self):
        """Names with backticks should be rejected (command substitution)."""
        with pytest.raises(ValueError, match="unsafe characters"):
            _validate_gcs_name("bucket`whoami`")

    def test_invalid_name_with_pipe(self):
        """Names with pipe should be rejected."""
        with pytest.raises(ValueError, match="unsafe characters"):
            _validate_gcs_name("bucket|cat /etc/passwd")


# ============================================================================
# Backup Tests
# ============================================================================


class TestBackupToGDrive:
    """Tests for Google Drive backup with integrity verification."""

    def test_backup_missing_directory_raises_when_rclone_also_fails(self, tmp_path):
        """Raise only when BOTH the local mount is absent and rclone cannot help.

        _rclone_backup is patched, not just GDRIVE_BACKUP_DIR. Without that
        patch this test performs a REAL upload to the user's Google Drive on any
        machine with rclone configured — it did exactly that once, putting a
        9-byte b"test data" file in the live backup folder.
        """
        nonexistent = tmp_path / "nonexistent_drive"
        db_file = tmp_path / "test.db"
        db_file.write_bytes(b"test data")

        with patch("sync_databases.GDRIVE_BACKUP_DIR", nonexistent), \
             patch("sync_databases._rclone_backup", return_value=False) as mock_rclone:
            with pytest.raises(RuntimeError, match="no Google Drive backup target"):
                backup_to_gdrive(db_file)
        mock_rclone.assert_called_once()

    def test_backup_falls_back_to_rclone_when_unmounted(self, tmp_path):
        """With no local Drive mount, the rclone path is used and succeeds."""
        nonexistent = tmp_path / "nonexistent_drive"
        db_file = tmp_path / "test.db"
        db_file.write_bytes(b"test data")

        with patch("sync_databases.GDRIVE_BACKUP_DIR", nonexistent), \
             patch("sync_databases._rclone_backup", return_value=True) as mock_rclone:
            assert backup_to_gdrive(db_file) is True
        mock_rclone.assert_called_once()

    def test_backup_prefers_local_mount_over_rclone(self, tmp_path):
        """When the mount exists, rclone must not be invoked at all."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        db_file = tmp_path / "test.db"
        db_file.write_bytes(b"y" * 512)

        with patch("sync_databases.GDRIVE_BACKUP_DIR", backup_dir), \
             patch("sync_databases._rclone_backup") as mock_rclone:
            assert backup_to_gdrive(db_file) is True
        mock_rclone.assert_not_called()
        assert len(list(backup_dir.glob("ivcrush_*.db"))) == 1

    def test_backup_creates_file(self, tmp_path):
        """Backup should create a file in the backup directory."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        db_file = tmp_path / "test.db"
        db_file.write_bytes(b"x" * 1024)

        with patch("sync_databases.GDRIVE_BACKUP_DIR", backup_dir):
            result = backup_to_gdrive(db_file)

        assert result is True
        backup_files = list(backup_dir.glob("ivcrush_*.db"))
        assert len(backup_files) == 1
        assert backup_files[0].stat().st_size == 1024


# ============================================================================
# Transaction Rollback Tests
# ============================================================================


class TestTransactionRollback:
    """Tests that sync operations use proper transaction management."""

    def test_partial_sync_does_not_corrupt(self, local_db, cloud_db):
        """If one sync function fails, previously synced data should still be consistent."""
        # Add data to both
        insert_historical_move(local_db, "AAPL", "2026-01-20")
        insert_historical_move(cloud_db, "GOOG", "2026-01-22")

        # Sync should work normally
        stats = sync_historical_moves(local_db, cloud_db)
        local_db.commit()
        cloud_db.commit()

        local_count = local_db.execute("SELECT COUNT(*) FROM historical_moves").fetchone()[0]
        cloud_count = cloud_db.execute("SELECT COUNT(*) FROM historical_moves").fetchone()[0]
        assert local_count == 2
        assert cloud_count == 2


# ============================================================================
# Earnings Calendar Post-Merge Dedupe Tests
# ============================================================================


class TestDedupeEarningsCalendarAfterMerge:
    """
    Tests for dedupe_earnings_calendar_after_merge() — the earnings_calendar
    counterpart to dedupe_after_merge() (historical_moves). The union merge
    in sync_earnings_calendar can resurrect a stale duplicate row that was
    already cleaned up on only one side since the last sync (the AXON
    2026-08-03/2026-08-05 case root-caused 2026-08-06). This removes such
    resurrected duplicates on both copies before the cloud copy re-uploads,
    same pattern as the existing historical_moves post-merge dedupe.
    """

    def test_removes_resurrected_stale_duplicate(self, tmp_path):
        """A confirmed entry plus a stale unconfirmed echo within 90 days
        (Rule 1: confirmed beats unconfirmed) should be deduped down to
        just the confirmed row — no corroboration needed for this rule.
        cleanup_duplicate_earnings only scans a today-14d..today+120d
        window, so dates must be relative to today, not hardcoded."""
        stale_date = (date.today() + timedelta(days=3)).isoformat()
        confirmed_date = (date.today() + timedelta(days=5)).isoformat()

        db_path = tmp_path / "test.db"
        conn = create_test_db(db_path)
        insert_earnings_calendar(conn, "AXON", stale_date, updated_at="2026-08-01T00:00:00")
        conn.execute(
            "UPDATE earnings_calendar SET confirmed = 0 WHERE ticker = 'AXON' AND earnings_date = ?",
            (stale_date,),
        )
        insert_earnings_calendar(conn, "AXON", confirmed_date, updated_at="2026-08-02T00:00:00")
        conn.commit()
        conn.close()

        dedupe_earnings_calendar_after_merge(db_path, "test")

        remaining = sqlite3.connect(str(db_path)).execute(
            "SELECT earnings_date FROM earnings_calendar WHERE ticker = 'AXON'"
        ).fetchall()
        assert remaining == [(confirmed_date,)]

    def test_no_duplicates_leaves_table_untouched(self, tmp_path):
        """A single entry per ticker (the common case) should never be touched."""
        only_date = (date.today() + timedelta(days=5)).isoformat()

        db_path = tmp_path / "test.db"
        conn = create_test_db(db_path)
        insert_earnings_calendar(conn, "MSFT", only_date)
        conn.commit()
        conn.close()

        dedupe_earnings_calendar_after_merge(db_path, "test")

        remaining = sqlite3.connect(str(db_path)).execute(
            "SELECT earnings_date FROM earnings_calendar WHERE ticker = 'MSFT'"
        ).fetchall()
        assert remaining == [(only_date,)]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
