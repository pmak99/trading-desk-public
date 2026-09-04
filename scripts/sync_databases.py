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

sys.path.insert(0, str(PROJECT_ROOT / "2.0" / "scripts"))
from cleanup_duplicate_moves import dedupe_duplicate_moves  # noqa: E402
from sync_earnings_calendar import cleanup_duplicate_earnings  # noqa: E402
GCS_BUCKET = "trading-desk-data"
GCS_BLOB = "ivcrush.db"
# Google Drive backup path - auto-detect or use environment variable
def _find_gdrive_backup_dir() -> Path:
    """Find Google Drive backup directory, handling email-suffixed mount points."""
    if "GDRIVE_BACKUP_PATH" in os.environ:
        return Path(os.environ["GDRIVE_BACKUP_PATH"])

    cloud_storage = Path.home() / "Library/CloudStorage"
    if not cloud_storage.exists():
        return cloud_storage / "GoogleDrive/My Drive/Backups/trading-desk"

    # Find any GoogleDrive* folder (handles GoogleDrive-email@example.com format)
    for item in cloud_storage.iterdir():
        if item.is_dir() and item.name.startswith("GoogleDrive"):
            backup_path = item / "My Drive/Backups/trading-desk"
            if backup_path.exists():
                return backup_path

    # Fallback to generic path
    return cloud_storage / "GoogleDrive/My Drive/Backups/trading-desk"

GDRIVE_BACKUP_DIR = _find_gdrive_backup_dir()


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
                   close_move_pct, volume_before, volume_earnings, created_at,
                   pre_earnings_straddle_pct, ern_iv_effect
            FROM historical_moves
            WHERE (ticker, earnings_date) IN ({placeholders})
        """, params).fetchall()

        local_conn.executemany("""
            INSERT OR IGNORE INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_open, earnings_high,
             earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
             close_move_pct, volume_before, volume_earnings, created_at,
             pre_earnings_straddle_pct, ern_iv_effect)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                   close_move_pct, volume_before, volume_earnings, created_at,
                   pre_earnings_straddle_pct, ern_iv_effect
            FROM historical_moves
            WHERE (ticker, earnings_date) IN ({placeholders})
        """, params).fetchall()

        cloud_conn.executemany("""
            INSERT OR IGNORE INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_open, earnings_high,
             earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
             close_move_pct, volume_before, volume_earnings, created_at,
             pre_earnings_straddle_pct, ern_iv_effect)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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

    # Get unique keys from both — include account_type so IRA and TAXABLE rows
    # with identical option details are treated as distinct records.
    # COALESCE(account_type, 'TAXABLE') handles cloud DBs pre-migration-007.
    local_keys = set(
        local_conn.execute("""
            SELECT symbol, acquired_date, sale_date,
                   COALESCE(option_type, ''), COALESCE(strike, 0), cost_basis,
                   COALESCE(account_type, 'TAXABLE')
            FROM trade_journal
        """).fetchall()
    )

    cloud_keys = set(
        cloud_conn.execute("""
            SELECT symbol, acquired_date, sale_date,
                   COALESCE(option_type, ''), COALESCE(strike, 0), cost_basis,
                   COALESCE(account_type, 'TAXABLE')
            FROM trade_journal
        """).fetchall()
    )

    # Records only in cloud -> add to local
    cloud_only = cloud_keys - local_keys
    if cloud_only:
        for key in cloud_only:
            symbol, acq_date, sale_date, opt_type, strike, cost_basis, account_type = key
            opt_type = opt_type if opt_type else None
            strike = strike if strike else None

            rec = cloud_conn.execute("""
                SELECT symbol, acquired_date, sale_date, days_held, option_type, strike,
                       expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                       term, wash_sale_amount, earnings_date, actual_move, created_at,
                       COALESCE(account_type, 'TAXABLE')
                FROM trade_journal
                WHERE symbol = ? AND (acquired_date = ? OR (acquired_date IS NULL AND ? IS NULL))
                  AND sale_date = ? AND (option_type = ? OR (option_type IS NULL AND ? IS NULL))
                  AND (strike = ? OR (strike IS NULL AND ? IS NULL)) AND cost_basis = ?
                  AND COALESCE(account_type, 'TAXABLE') = ?
            """, (symbol, acq_date, acq_date, sale_date, opt_type, opt_type, strike, strike, cost_basis, account_type)).fetchone()

            if rec:
                local_conn.execute("""
                    INSERT OR IGNORE INTO trade_journal
                    (symbol, acquired_date, sale_date, days_held, option_type, strike,
                     expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                     term, wash_sale_amount, earnings_date, actual_move, created_at,
                     account_type)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, rec)
                stats["local_added"] += 1

    # Records only in local -> add to cloud
    local_only = local_keys - cloud_keys
    if local_only:
        for key in local_only:
            symbol, acq_date, sale_date, opt_type, strike, cost_basis, account_type = key
            opt_type = opt_type if opt_type else None
            strike = strike if strike else None

            rec = local_conn.execute("""
                SELECT symbol, acquired_date, sale_date, days_held, option_type, strike,
                       expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                       term, wash_sale_amount, earnings_date, actual_move, created_at,
                       COALESCE(account_type, 'TAXABLE')
                FROM trade_journal
                WHERE symbol = ? AND (acquired_date = ? OR (acquired_date IS NULL AND ? IS NULL))
                  AND sale_date = ? AND (option_type = ? OR (option_type IS NULL AND ? IS NULL))
                  AND (strike = ? OR (strike IS NULL AND ? IS NULL)) AND cost_basis = ?
                  AND COALESCE(account_type, 'TAXABLE') = ?
            """, (symbol, acq_date, acq_date, sale_date, opt_type, opt_type, strike, strike, cost_basis, account_type)).fetchone()

            if rec:
                cloud_conn.execute("""
                    INSERT OR IGNORE INTO trade_journal
                    (symbol, acquired_date, sale_date, days_held, option_type, strike,
                     expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                     term, wash_sale_amount, earnings_date, actual_move, created_at,
                     account_type)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, rec)
                stats["cloud_added"] += 1

    return stats


def sync_position_limits(local_conn: sqlite3.Connection, cloud_conn: sqlite3.Connection) -> dict:
    """Sync position_limits table (union strategy - newest last_updated wins on conflict)."""
    stats = {"local_added": 0, "cloud_added": 0}

    # Check if table exists in both databases
    local_has_table = local_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='position_limits'"
    ).fetchone() is not None

    cloud_has_table = cloud_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='position_limits'"
    ).fetchone() is not None

    if not local_has_table and not cloud_has_table:
        log("position_limits table doesn't exist in either database, skipping")
        return stats

    _POSITION_LIMITS_DDL = """
            CREATE TABLE IF NOT EXISTS position_limits (
                ticker TEXT PRIMARY KEY,
                max_contracts INTEGER DEFAULT 100,
                max_notional REAL DEFAULT 50000,
                tail_risk_ratio REAL,
                tail_risk_level TEXT,
                avg_move REAL,
                max_move REAL,
                num_quarters INTEGER,
                notes TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                iv_rank_1y REAL,
                iv_pct_1y REAL,
                iv30d REAL,
                hv20d REAL,
                abs_avg_ern_mv REAL,
                orats_implied_ern_mv REAL
            )
        """

    # Create table in cloud if missing (copy schema from local)
    if local_has_table and not cloud_has_table:
        cloud_conn.execute(_POSITION_LIMITS_DDL)

    # Create table in local if missing (copy schema from cloud)
    if cloud_has_table and not local_has_table:
        local_conn.execute(_POSITION_LIMITS_DDL)

    # Get all records from both (actual schema columns)
    local_records = {
        row[0]: row  # key by ticker
        for row in local_conn.execute("""
            SELECT ticker, max_contracts, max_notional, tail_risk_ratio, tail_risk_level,
                   avg_move, max_move, num_quarters, notes, last_updated,
                   iv_rank_1y, iv_pct_1y, iv30d, hv20d, abs_avg_ern_mv, orats_implied_ern_mv
            FROM position_limits
        """)
    }

    cloud_records = {
        row[0]: row
        for row in cloud_conn.execute("""
            SELECT ticker, max_contracts, max_notional, tail_risk_ratio, tail_risk_level,
                   avg_move, max_move, num_quarters, notes, last_updated,
                   iv_rank_1y, iv_pct_1y, iv30d, hv20d, abs_avg_ern_mv, orats_implied_ern_mv
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
                (ticker, max_contracts, max_notional, tail_risk_ratio, tail_risk_level,
                 avg_move, max_move, num_quarters, notes, last_updated,
                 iv_rank_1y, iv_pct_1y, iv30d, hv20d, abs_avg_ern_mv, orats_implied_ern_mv)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, local_rec)
            stats["cloud_added"] += 1

        elif cloud_rec and not local_rec:
            # Only in cloud -> add to local
            local_conn.execute("""
                INSERT OR REPLACE INTO position_limits
                (ticker, max_contracts, max_notional, tail_risk_ratio, tail_risk_level,
                 avg_move, max_move, num_quarters, notes, last_updated,
                 iv_rank_1y, iv_pct_1y, iv30d, hv20d, abs_avg_ern_mv, orats_implied_ern_mv)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, cloud_rec)
            stats["local_added"] += 1

        elif local_rec and cloud_rec:
            # Both exist - newest last_updated wins
            local_updated = local_rec[9] or "1970-01-01"
            cloud_updated = cloud_rec[9] or "1970-01-01"

            if local_updated > cloud_updated:
                cloud_conn.execute("""
                    INSERT OR REPLACE INTO position_limits
                    (ticker, max_contracts, max_notional, tail_risk_ratio, tail_risk_level,
                     avg_move, max_move, num_quarters, notes, last_updated,
                     iv_rank_1y, iv_pct_1y, iv30d, hv20d, abs_avg_ern_mv, orats_implied_ern_mv)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, local_rec)
                stats["cloud_added"] += 1
            elif cloud_updated > local_updated:
                local_conn.execute("""
                    INSERT OR REPLACE INTO position_limits
                    (ticker, max_contracts, max_notional, tail_risk_ratio, tail_risk_level,
                     avg_move, max_move, num_quarters, notes, last_updated,
                     iv_rank_1y, iv_pct_1y, iv30d, hv20d, abs_avg_ern_mv, orats_implied_ern_mv)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, cloud_rec)
                stats["local_added"] += 1

    return stats


def dedupe_after_merge(db_path: Path, label: str) -> None:
    """
    Remove adjacent-date duplicate historical_moves rows introduced by the
    union merge above.

    sync_historical_moves() unions (ticker, earnings_date) keys from both
    sides — if a duplicate was cleaned up on only one side since the last
    sync (locally, or via a separate cloud write), the union brings the
    stale row right back into whichever side it was removed from. This
    resurrection was observed live 2026-08-24 (URI). Deduping both the
    local and cloud copies here, before the cloud copy is re-uploaded,
    means the run that reintroduces a duplicate is also the run that
    removes it again — no separate manual cleanup pass required.
    """
    result = dedupe_duplicate_moves(str(db_path), dry_run=False)
    if result["moves_deleted"]:
        log(f"Deduped {label}: removed {result['moves_deleted']} duplicate historical_moves row(s)")
        for item in result["pairs"]:
            log(f"  {item['ticker']}: kept {item['keep_date']}, removed {item['delete_date']}")
    if result["ambiguous"]:
        log(
            f"{label}: {len(result['ambiguous'])} ambiguous duplicate pair(s) left untouched "
            f"— differing values, equal enrichment, needs manual review",
            "warn",
        )


def dedupe_earnings_calendar_after_merge(db_path: Path, label: str) -> None:
    """
    Remove duplicate earnings_calendar rows introduced by the newest-wins
    merge above.

    sync_earnings_calendar() only compares (ticker, earnings_date) as a key
    per side — if a stale duplicate date was cleaned up on only one side
    since the last sync, the merge brings it right back on that side (the
    AXON 2026-08-03/2026-08-05 resurrection root-caused 2026-08-06 — see
    CLAUDE.md Incident History). dedupe_after_merge() above already covers
    this for historical_moves; earnings_calendar needs the same treatment,
    which it never got.

    Called with no Finnhub/Yahoo clients (dry_run=False, finnhub=None,
    yahoo_finance=None) — Rule 1 (confirmed beats unconfirmed) and Rule 3
    (unconfirmed-vs-unconfirmed, keep newest) never need corroboration and
    still run. Rule 2 (confirmed-vs-confirmed within 30 days) requires fresh
    two-source agreement to resolve; with no clients supplied, any such tie
    is left untouched and logged NEEDS REVIEW rather than guessed at —
    consistent with the "earnings-calendar writes require corroboration,
    never single-source" rule (CLAUDE.md Databases known issues).
    """
    removed = cleanup_duplicate_earnings(str(db_path), dry_run=False)
    if removed:
        log(f"Deduped {label}: removed {len(removed)} duplicate earnings_calendar row(s)")
        for item in removed:
            log(f"  {item['ticker']}: kept {item['kept_date']}, removed {item['removed_date']}")


GDRIVE_RCLONE_REMOTE = os.environ.get("GDRIVE_RCLONE_REMOTE", "gdrive:Backups/trading-desk")


def _rclone_backup(db_path: Path, timestamp: str) -> bool:
    """Upload a timestamped copy to Drive via rclone. Returns False if unavailable.

    Used when no Drive folder is mounted locally. Verifies the object exists
    afterwards rather than trusting rclone's exit code, then applies the same
    30-day retention the mounted-folder path uses.
    """
    if not shutil.which("rclone"):
        return False

    remote_file = f"{GDRIVE_RCLONE_REMOTE}/ivcrush_{timestamp}.db"
    try:
        subprocess.run(
            ["rclone", "copyto", str(db_path), remote_file],
            check=True, capture_output=True, timeout=600,
        )
        listed = subprocess.run(
            ["rclone", "lsf", remote_file],
            capture_output=True, text=True, timeout=120,
        )
        if not listed.stdout.strip():
            log(f"rclone reported success but {remote_file} is not present", "error")
            return False

        size = db_path.stat().st_size
        log(f"✓ Backed up to Google Drive via rclone: {remote_file} ({size:,} bytes)", "success")

        subprocess.run(
            ["rclone", "delete", GDRIVE_RCLONE_REMOTE,
             "--include", "ivcrush_*.db", "--min-age", "30d"],
            capture_output=True, timeout=300,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log(f"rclone backup failed: {e}", "warn")
        return False


def backup_to_gdrive(db_path: Path) -> bool:
    """
    Backup database to Google Drive with integrity verification.

    Raises:
        RuntimeError: If backup directory doesn't exist or backup fails
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # No locally mounted Drive folder: fall back to rclone. _find_gdrive_backup_dir
    # only knows how to locate the macOS Google Drive client's mount
    # (~/Library/CloudStorage/GoogleDrive*), so on Linux this raised "CRITICAL:
    # Google Drive backup directory not found" and aborted the whole sync-cloud
    # run. rclone reaches the same Drive over the API and needs no mount.
    if not GDRIVE_BACKUP_DIR.exists():
        if _rclone_backup(db_path, timestamp):
            return True
        error_msg = (
            f"CRITICAL: no Google Drive backup target. Local mount not found at "
            f"{GDRIVE_BACKUP_DIR}, and the rclone fallback did not succeed.\n"
            f"Backups are DISABLED. Either:\n"
            f"  1. Set GDRIVE_BACKUP_PATH to a locally mounted Drive folder\n"
            f"  2. Configure rclone (remote {GDRIVE_RCLONE_REMOTE!r})\n"
            f"  3. Comment out backup_to_gdrive() call if not using GDrive"
        )
        log(error_msg, "error")
        raise RuntimeError(error_msg)

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

            # Wrap all syncs in explicit transactions for atomicity
            local_conn.execute("BEGIN TRANSACTION")
            cloud_conn.execute("BEGIN TRANSACTION")

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

        # Remove any adjacent-date duplicates the union merge above just
        # reintroduced, on both copies, before the cloud copy goes back up.
        log("Checking for duplicate historical_moves rows...")
        dedupe_after_merge(LOCAL_DB, "local")
        dedupe_after_merge(cloud_db_path, "cloud")

        log("Checking for duplicate earnings_calendar rows...")
        dedupe_earnings_calendar_after_merge(LOCAL_DB, "local")
        dedupe_earnings_calendar_after_merge(cloud_db_path, "cloud")

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
