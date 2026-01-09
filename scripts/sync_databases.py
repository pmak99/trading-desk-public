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
GDRIVE_BACKUP_DIR = Path.home() / "Library/CloudStorage/GoogleDrive-pmakwana99@gmail.com/My Drive/Trading/Database Backups"


def log(msg: str, level: str = "info"):
    """Log with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    icons = {"info": "ℹ️", "success": "✅", "warn": "⚠️", "error": "❌"}
    icon = icons.get(level, "•")
    print(f"[{timestamp}] {icon} {msg}")


def run_gsutil(args: list, check: bool = True) -> subprocess.CompletedProcess:
    """Run gsutil command."""
    cmd = ["gsutil"] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def download_cloud_db(dest: Path) -> bool:
    """Download cloud DB from GCS."""
    try:
        result = run_gsutil(["cp", f"gs://{GCS_BUCKET}/{GCS_BLOB}", str(dest)])
        if result.returncode == 0:
            log(f"Downloaded cloud DB ({dest.stat().st_size / 1024 / 1024:.2f} MB)")
            return True
        log(f"Failed to download: {result.stderr}", "error")
        return False
    except Exception as e:
        log(f"Download error: {e}", "error")
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


def backup_to_gdrive(db_path: Path) -> bool:
    """Backup database to Google Drive."""
    if not GDRIVE_BACKUP_DIR.exists():
        log(f"Google Drive backup dir not found: {GDRIVE_BACKUP_DIR}", "warn")
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = GDRIVE_BACKUP_DIR / f"ivcrush_{timestamp}.db"

    try:
        shutil.copy2(db_path, backup_file)
        log(f"Backed up to Google Drive: {backup_file.name}")

        # Cleanup old backups (keep last 30 days)
        cutoff = datetime.now().timestamp() - (30 * 24 * 60 * 60)
        for old_file in GDRIVE_BACKUP_DIR.glob("ivcrush_*.db"):
            if old_file.stat().st_mtime < cutoff:
                old_file.unlink()
                log(f"Removed old backup: {old_file.name}")

        return True
    except Exception as e:
        log(f"Backup to Google Drive failed: {e}", "error")
        return False


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
            log("Cannot download cloud DB, syncing local → cloud only", "warn")
            # Just upload local to cloud
            if upload_cloud_db(LOCAL_DB):
                log("Uploaded local DB to cloud", "success")
            backup_to_gdrive(LOCAL_DB)
            return

        # Open both databases
        local_conn = sqlite3.connect(str(LOCAL_DB))
        cloud_conn = sqlite3.connect(str(cloud_db_path))

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

        # Commit changes
        local_conn.commit()
        cloud_conn.commit()

        # Close connections
        local_conn.close()
        cloud_conn.close()

        # Upload synced cloud DB back to GCS
        log("Uploading synced DB to GCS...")
        if upload_cloud_db(cloud_db_path):
            log("Cloud DB updated", "success")

        # Backup local DB to Google Drive
        log("Backing up to Google Drive...")
        backup_to_gdrive(LOCAL_DB)

        # Summary
        total_changes = (
            hm_stats['local_added'] + hm_stats['cloud_added'] +
            ec_stats['local_updated'] + ec_stats['cloud_updated'] +
            tj_stats['local_added'] + tj_stats['cloud_added']
        )
        log(f"Sync complete! {total_changes} total changes", "success")

    finally:
        # Cleanup temp file
        if cloud_db_path.exists():
            cloud_db_path.unlink()


if __name__ == "__main__":
    main()
