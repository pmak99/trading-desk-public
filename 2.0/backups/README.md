# Database Backups

This directory contains automated backups of the ivcrush.db database.

## Auto-Backup Settings

- **Frequency**: Every 6 hours (when trade.sh is run)
- **Retention**: 30 days (automatic cleanup)
- **Format**: SQLite database files with UTC timestamps
- **Verification**: Size check + database integrity test

## Google Drive Sync

Backups are automatically synced to Google Drive:

**Location**: `Google Drive/My Drive/Trading/Database Backups/`

The sync happens asynchronously (non-blocking) after each backup:
1. Database is backed up locally first
2. Copy is initiated to Google Drive in background
3. Old backups (>30 days) are cleaned up from both locations

## Backup Files

Format: `ivcrush_YYYYMMDD_HHMMSS_UTC.db`

Example: `ivcrush_20251124_142214_UTC.db`

## Security Features

- Atomic file locking (prevents race conditions)
- WAL checkpoint before backup
- Backup verification before commit
- Stale lock detection (8+ hour old locks are removed)
- Symlink protection

## Manual Backup Restore

To restore from a backup:

```bash
# 1. Stop any running processes
# 2. Backup current database
cp data/ivcrush.db data/ivcrush.db.before-restore

# 3. Restore from backup
cp backups/ivcrush_YYYYMMDD_HHMMSS_UTC.db data/ivcrush.db

# 4. Verify integrity
sqlite3 data/ivcrush.db "PRAGMA integrity_check;"
```

## Troubleshooting

### No backups being created?
- Check for stale lock: `ls -la backups/.backup.lock`
- Remove if old: `rmdir backups/.backup.lock`
- Check database exists: `ls -lh data/ivcrush.db`

### Google Drive sync not working?
- Verify Google Drive is running: `ps aux | grep "Google Drive"`
- Check folder exists: `ls -la ~/Library/CloudStorage/GoogleDrive-*/My\ Drive/Trading/Database\ Backups/`
- Check permissions: Should be writable (drwx------)

## Notes

- Backups are created during normal trade.sh operations
- First backup happens immediately, then every 6 hours
- Local backups in this directory are excluded from git
- Google Drive handles cloud backup redundancy
