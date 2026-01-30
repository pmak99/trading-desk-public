#!/usr/bin/env python3
"""
Sync databases between 5.0 cloud (GCS) and 2.0 local.

Bidirectional sync strategy:
- historical_moves: Union (UNIQUE ticker+date prevents dupes)
- earnings_calendar: Newest updated_at wins
- trade_journal: Union (UNIQUE constraint prevents dupes)

Also backs up local DB to Google Drive weekly.
"""

import os
import re
import sys
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
import tempfile

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
LOCAL_DB = PROJECT_ROOT / "2.0" / "data" / "ivcrush.db"
GCS_BUCKET = "your-gcs-bucket"
GCS_BLOB = "ivcrush.db"
GDRIVE_BACKUP_DIR = Path(os.environ.get(
    "GDRIVE_BACKUP_PATH",
    str(Path.home() / "Library/CloudStorage/GoogleDrive-pmakwana99@gmail.com/My Drive/Backups/trading-desk")
))


def log(msg: str, level: str = "info"):
    """Log with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    icons = {"info": "ℹ️", "success": "✅", "warn": "⚠️", "error": "❌"}
    icon = icons.get(level, "•")
    print(f"[{timestamp}] {icon} {msg}")


def _validate_gcs_name(name: str) -> str:
    """Validate GCS bucket/blob names contain only safe characters."""
    if not re.match(r'^[a-zA-Z0-9._/-]+$', name):
        raise ValueError(f"Invalid GCS name (unsafe characters): {name}")
    return name


def run_gsutil(args: list, check: bool = True) -> subprocess.CompletedProcess:
    """Run gsutil command with list args (no shell=True)."""
    # Validate that args are strings (prevent injection)
    sanitized = []
    for arg in args:
        if not isinstance(arg, str):
            raise TypeError(f"gsutil arg must be string, got {type(arg)}")
        sanitized.append(arg)
    cmd = ["gsutil"] + sanitized
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def download_cloud_db(dest: Path, max_retries: int = 3) -> bool:
    """Download cloud DB from GCS with retry logic.

    Args:
        dest: Destination path for downloaded database
        max_retries: Maximum number of retry attempts (default 3)

    Returns:
        True if download succeeded, False otherwise
    """
    import time

    backoff_seconds = [5, 10, 30]  # Exponential backoff: 5s, 10s, 30s

    for attempt in range(max_retries):
        try:
            result = run_gsutil(["cp", f"gs://{GCS_BUCKET}/{GCS_BLOB}", str(dest)])
            if result.returncode == 0:
                log(f"Downloaded cloud DB ({dest.stat().st_size / 1024 / 1024:.2f} MB)")
                return True

            error_msg = result.stderr.strip() if result.stderr else "Unknown error"

            if attempt < max_retries - 1:
                wait_time = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                log(f"Download attempt {attempt + 1} failed: {error_msg}. Retrying in {wait_time}s...", "warn")
                time.sleep(wait_time)
            else:
                log(f"Download failed after {max_retries} attempts: {error_msg}", "error")

        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                log(f"Download attempt {attempt + 1} error: {e}. Retrying in {wait_time}s...", "warn")
                time.sleep(wait_time)
            else:
                log(f"Download error after {max_retries} attempts: {e}", "error")

    return False


def upload_cloud_db(src: Path) -> bool:
    """Upload DB to GCS."""
    try:
        result = run_gsutil(["cp", str(src), f"gs://{GCS_BUCKET}/{GCS_BLOB}"])
        if result.returncode == 0:
            log(f"Uploaded to GCS ({src.stat().st_size / 1024 / 1024:.2f} MB)")
            return True
        log(f"Failed to upload: {result.stderr}", "error")
        return False
    except Exception as e:
        log(f"Upload error: {e}", "error")
        return False


def sync_historical_moves(local_conn: sqlite3.Connection, cloud_conn: sqlite3.Connection) -> dict:
    """Sync historical_moves table (union strategy)."""
    stats = {"local_added": 0, "cloud_added": 0}

    # Get all records from both
    local_cursor = local_conn.execute(
        "SELECT ticker, earnings_date FROM historical_moves"
    )
    local_keys = set((row[0], row[1]) for row in local_cursor)

    cloud_cursor = cloud_conn.execute(
        "SELECT ticker, earnings_date FROM historical_moves"
    )
    cloud_keys = set((row[0], row[1]) for row in cloud_cursor)

    # Records only in cloud -> add to local
    cloud_only = cloud_keys - local_keys
    if not cloud_only and not (local_keys - cloud_keys):
        return stats
    if cloud_only:
        placeholders = ",".join(["(?,?)"] * len(cloud_only))
        params = [item for pair in cloud_only for item in pair]

        cloud_records = cloud_conn.execute(f"""
            SELECT ticker, earnings_date, prev_close, earnings_open, earnings_high,
                   earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
                   close_move_pct, volume_before, volume_earnings, created_at
            FROM historical_moves
            WHERE (ticker, earnings_date) IN ({placeholders})
        """, params).fetchall()

        local_conn.executemany("""
            INSERT OR IGNORE INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_open, earnings_high,
             earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
             close_move_pct, volume_before, volume_earnings, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, cloud_records)
        stats["local_added"] = len(cloud_records)

    # Records only in local -> add to cloud
    local_only = local_keys - cloud_keys
    if not local_only:
        return stats
    if local_only:
        placeholders = ",".join(["(?,?)"] * len(local_only))
        params = [item for pair in local_only for item in pair]

        local_records = local_conn.execute(f"""
            SELECT ticker, earnings_date, prev_close, earnings_open, earnings_high,
                   earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
                   close_move_pct, volume_before, volume_earnings, created_at
            FROM historical_moves
            WHERE (ticker, earnings_date) IN ({placeholders})
        """, params).fetchall()

        cloud_conn.executemany("""
            INSERT OR IGNORE INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_open, earnings_high,
             earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
             close_move_pct, volume_before, volume_earnings, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, local_records)
        stats["cloud_added"] = len(local_records)

    return stats


def sync_earnings_calendar(local_conn: sqlite3.Connection, cloud_conn: sqlite3.Connection) -> dict:
    """Sync earnings_calendar table (newest updated_at wins)."""
    stats = {"local_updated": 0, "cloud_updated": 0}

    # Get all records from both with updated_at
    local_records = {
        (row[0], row[1]): row
        for row in local_conn.execute(
            "SELECT ticker, earnings_date, timing, confirmed, updated_at, last_validated_at FROM earnings_calendar"
        )
    }

    cloud_records = {
        (row[0], row[1]): row
        for row in cloud_conn.execute(
            "SELECT ticker, earnings_date, timing, confirmed, updated_at, last_validated_at FROM earnings_calendar"
        )
    }

    all_keys = set(local_records.keys()) | set(cloud_records.keys())

    for key in all_keys:
        local_rec = local_records.get(key)
        cloud_rec = cloud_records.get(key)

        if local_rec and not cloud_rec:
            # Only in local -> add to cloud
            cloud_conn.execute("""
                INSERT OR REPLACE INTO earnings_calendar
                (ticker, earnings_date, timing, confirmed, updated_at, last_validated_at)
                VALUES (?,?,?,?,?,?)
            """, local_rec)
            stats["cloud_updated"] += 1

        elif cloud_rec and not local_rec:
            # Only in cloud -> add to local
            local_conn.execute("""
                INSERT OR REPLACE INTO earnings_calendar
                (ticker, earnings_date, timing, confirmed, updated_at, last_validated_at)
                VALUES (?,?,?,?,?,?)
            """, cloud_rec)
            stats["local_updated"] += 1

        elif local_rec and cloud_rec:
            # Both exist - newest updated_at wins
            local_updated = local_rec[4] or "1970-01-01"
            cloud_updated = cloud_rec[4] or "1970-01-01"

            if local_updated > cloud_updated:
                cloud_conn.execute("""
                    INSERT OR REPLACE INTO earnings_calendar
                    (ticker, earnings_date, timing, confirmed, updated_at, last_validated_at)
                    VALUES (?,?,?,?,?,?)
                """, local_rec)
                stats["cloud_updated"] += 1
            elif cloud_updated > local_updated:
                local_conn.execute("""
                    INSERT OR REPLACE INTO earnings_calendar
                    (ticker, earnings_date, timing, confirmed, updated_at, last_validated_at)
                    VALUES (?,?,?,?,?,?)
                """, cloud_rec)
                stats["local_updated"] += 1

    return stats


def sync_trade_journal(local_conn: sqlite3.Connection, cloud_conn: sqlite3.Connection) -> dict:
    """Sync trade_journal table (union strategy)."""
    stats = {"local_added": 0, "cloud_added": 0}

    # Get unique keys from both (symbol, acquired_date, sale_date, option_type, strike, cost_basis)
    local_keys = set(
        local_conn.execute("""
            SELECT symbol, acquired_date, sale_date,
                   COALESCE(option_type, ''), COALESCE(strike, 0), cost_basis
            FROM trade_journal
        """).fetchall()
    )

    cloud_keys = set(
        cloud_conn.execute("""
            SELECT symbol, acquired_date, sale_date,
                   COALESCE(option_type, ''), COALESCE(strike, 0), cost_basis
            FROM trade_journal
        """).fetchall()
    )

    # Records only in cloud -> add to local
    cloud_only = cloud_keys - local_keys
    if cloud_only:
        for key in cloud_only:
            symbol, acq_date, sale_date, opt_type, strike, cost_basis = key
            opt_type = opt_type if opt_type else None
            strike = strike if strike else None

            rec = cloud_conn.execute("""
                SELECT symbol, acquired_date, sale_date, days_held, option_type, strike,
                       expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                       term, wash_sale_amount, earnings_date, actual_move, created_at
                FROM trade_journal
                WHERE symbol = ? AND (acquired_date = ? OR (acquired_date IS NULL AND ? IS NULL))
                  AND sale_date = ? AND (option_type = ? OR (option_type IS NULL AND ? IS NULL))
                  AND (strike = ? OR (strike IS NULL AND ? IS NULL)) AND cost_basis = ?
            """, (symbol, acq_date, acq_date, sale_date, opt_type, opt_type, strike, strike, cost_basis)).fetchone()

            if rec:
                local_conn.execute("""
                    INSERT OR IGNORE INTO trade_journal
                    (symbol, acquired_date, sale_date, days_held, option_type, strike,
                     expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                     term, wash_sale_amount, earnings_date, actual_move, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, rec)
                stats["local_added"] += 1

    # Records only in local -> add to cloud
    local_only = local_keys - cloud_keys
    if local_only:
        for key in local_only:
            symbol, acq_date, sale_date, opt_type, strike, cost_basis = key
            opt_type = opt_type if opt_type else None
            strike = strike if strike else None

            rec = local_conn.execute("""
                SELECT symbol, acquired_date, sale_date, days_held, option_type, strike,
                       expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                       term, wash_sale_amount, earnings_date, actual_move, created_at
                FROM trade_journal
                WHERE symbol = ? AND (acquired_date = ? OR (acquired_date IS NULL AND ? IS NULL))
                  AND sale_date = ? AND (option_type = ? OR (option_type IS NULL AND ? IS NULL))
                  AND (strike = ? OR (strike IS NULL AND ? IS NULL)) AND cost_basis = ?
            """, (symbol, acq_date, acq_date, sale_date, opt_type, opt_type, strike, strike, cost_basis)).fetchone()

            if rec:
                cloud_conn.execute("""
                    INSERT OR IGNORE INTO trade_journal
                    (symbol, acquired_date, sale_date, days_held, option_type, strike,
                     expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                     term, wash_sale_amount, earnings_date, actual_move, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, rec)
                stats["cloud_added"] += 1

    return stats


def sync_position_limits(local_conn: sqlite3.Connection, cloud_conn: sqlite3.Connection) -> dict:
    """Sync position_limits table (union strategy - newest updated_at wins on conflict)."""
    stats = {"local_added": 0, "cloud_added": 0}

    # Get all records from both
    local_records = {
        row[0]: row  # key by ticker
        for row in local_conn.execute("""
            SELECT ticker, trr, position_limit, notional_limit, trr_level, updated_at
            FROM position_limits
        """)
    }

    cloud_records = {
        row[0]: row
        for row in cloud_conn.execute("""
            SELECT ticker, trr, position_limit, notional_limit, trr_level, updated_at
            FROM position_limits
        """)
    }

    all_tickers = set(local_records.keys()) | set(cloud_records.keys())

    for ticker in all_tickers:
        local_rec = local_records.get(ticker)
        cloud_rec = cloud_records.get(ticker)

        if local_rec and not cloud_rec:
            # Only in local -> add to cloud
            cloud_conn.execute("""
                INSERT OR REPLACE INTO position_limits
                (ticker, trr, position_limit, notional_limit, trr_level, updated_at)
                VALUES (?,?,?,?,?,?)
            """, local_rec)
            stats["cloud_added"] += 1

        elif cloud_rec and not local_rec:
            # Only in cloud -> add to local
            local_conn.execute("""
                INSERT OR REPLACE INTO position_limits
                (ticker, trr, position_limit, notional_limit, trr_level, updated_at)
                VALUES (?,?,?,?,?,?)
            """, cloud_rec)
            stats["local_added"] += 1

        elif local_rec and cloud_rec:
            # Both exist - newest updated_at wins
            local_updated = local_rec[5] or "1970-01-01"
            cloud_updated = cloud_rec[5] or "1970-01-01"

            if local_updated > cloud_updated:
                cloud_conn.execute("""
                    INSERT OR REPLACE INTO position_limits
                    (ticker, trr, position_limit, notional_limit, trr_level, updated_at)
                    VALUES (?,?,?,?,?,?)
                """, local_rec)
                stats["cloud_added"] += 1
            elif cloud_updated > local_updated:
                local_conn.execute("""
                    INSERT OR REPLACE INTO position_limits
                    (ticker, trr, position_limit, notional_limit, trr_level, updated_at)
                    VALUES (?,?,?,?,?,?)
                """, cloud_rec)
                stats["local_added"] += 1

    return stats


def backup_to_gdrive(db_path: Path) -> bool:
    """
    Backup database to Google Drive with integrity verification.

    Raises:
        RuntimeError: If backup directory doesn't exist or backup fails
    """
    if not GDRIVE_BACKUP_DIR.exists():
        error_msg = (
            f"CRITICAL: Google Drive backup directory not found: {GDRIVE_BACKUP_DIR}\n"
            f"Backups are DISABLED. Either:\n"
            f"  1. Mount Google Drive at the expected location\n"
            f"  2. Update GDRIVE_BACKUP_DIR in sync_databases.py\n"
            f"  3. Comment out backup_to_gdrive() call if not using GDrive"
        )
        log(error_msg, "error")
        raise RuntimeError(error_msg)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = GDRIVE_BACKUP_DIR / f"ivcrush_{timestamp}.db"

    try:
        # Copy database
        shutil.copy2(db_path, backup_file)

        # CRITICAL: Verify backup integrity
        original_size = db_path.stat().st_size
        backup_size = backup_file.stat().st_size

        if backup_size != original_size:
            backup_file.unlink()  # Remove corrupted backup
            raise RuntimeError(
                f"Backup verification failed: size mismatch "
                f"(original: {original_size}, backup: {backup_size})"
            )

        log(f"✓ Backed up to Google Drive: {backup_file.name} ({backup_size:,} bytes)", "success")

        # Cleanup old backups (keep last 30 days)
        cutoff = datetime.now().timestamp() - (30 * 24 * 60 * 60)
        for old_file in GDRIVE_BACKUP_DIR.glob("ivcrush_*.db"):
            if old_file.stat().st_mtime < cutoff:
                old_file.unlink()
                log(f"Removed old backup: {old_file.name}")

        return True
    except Exception as e:
        error_msg = f"Backup to Google Drive FAILED: {e}"
        log(error_msg, "error")
        raise RuntimeError(error_msg)


def main():
    """Main sync function."""
    log("Starting database sync (cloud ↔ local)")

    # Check local DB exists
    if not LOCAL_DB.exists():
        log(f"Local DB not found: {LOCAL_DB}", "error")
        sys.exit(1)

    # Create temp file for cloud DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        cloud_db_path = Path(tmp.name)

    try:
        # Download cloud DB
        log("Downloading cloud DB from GCS...")
        if not download_cloud_db(cloud_db_path):
            # SAFETY: Don't overwrite cloud with local on download failure
            # Cloud may have data we can't download due to transient error
            log("Cannot download cloud DB - aborting to prevent data loss", "error")
            log("To force local → cloud sync, delete cloud DB first:", "info")
            log(f"  gsutil rm gs://{GCS_BUCKET}/{GCS_BLOB}", "info")
            sys.exit(1)

        # Open both databases
        local_conn = sqlite3.connect(str(LOCAL_DB))
        cloud_conn = sqlite3.connect(str(cloud_db_path))

        try:
            # CRITICAL: Enable foreign key constraints
            local_conn.execute("PRAGMA foreign_keys=ON")
            cloud_conn.execute("PRAGMA foreign_keys=ON")

            # Enable WAL mode for better concurrency
            local_conn.execute("PRAGMA journal_mode=WAL")
            cloud_conn.execute("PRAGMA journal_mode=WAL")

            # Sync each table
            log("Syncing historical_moves...")
            hm_stats = sync_historical_moves(local_conn, cloud_conn)
            log(f"  local +{hm_stats['local_added']}, cloud +{hm_stats['cloud_added']}")

            log("Syncing earnings_calendar...")
            ec_stats = sync_earnings_calendar(local_conn, cloud_conn)
            log(f"  local +{ec_stats['local_updated']}, cloud +{ec_stats['cloud_updated']}")

            log("Syncing trade_journal...")
            tj_stats = sync_trade_journal(local_conn, cloud_conn)
            log(f"  local +{tj_stats['local_added']}, cloud +{tj_stats['cloud_added']}")

            log("Syncing position_limits...")
            pl_stats = sync_position_limits(local_conn, cloud_conn)
            log(f"  local +{pl_stats['local_added']}, cloud +{pl_stats['cloud_added']}")

            # Commit changes
            local_conn.commit()
            cloud_conn.commit()
        except Exception as e:
            log(f"Sync failed, rolling back: {e}", "error")
            try:
                local_conn.rollback()
            except Exception:
                pass
            try:
                cloud_conn.rollback()
            except Exception:
                pass
            raise
        finally:
            # Close connections
            local_conn.close()
            cloud_conn.close()

        # Upload synced cloud DB back to GCS
        log("Uploading synced DB to GCS...")
        if upload_cloud_db(cloud_db_path):
            log("Cloud DB updated", "success")

        # Backup local DB to Google Drive
        log("Backing up to Google Drive...")
        try:
            backup_to_gdrive(LOCAL_DB)
        except RuntimeError as e:
            log(f"WARNING: Backup failed but sync completed: {e}", "warn")

        # Summary
        total_changes = (
            hm_stats['local_added'] + hm_stats['cloud_added'] +
            ec_stats['local_updated'] + ec_stats['cloud_updated'] +
            tj_stats['local_added'] + tj_stats['cloud_added'] +
            pl_stats['local_added'] + pl_stats['cloud_added']
        )
        log(f"Sync complete! {total_changes} total changes", "success")

    finally:
        # Cleanup temp file
        if cloud_db_path.exists():
            cloud_db_path.unlink()


if __name__ == "__main__":
    main()
