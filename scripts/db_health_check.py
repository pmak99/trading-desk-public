#!/usr/bin/env python3
"""
Database Health Check Script

Comprehensive health check for the Trading Desk database including:
- Integrity verification
- Foreign key enforcement
- Orphaned records detection
- Duplicate detection
- Statistics currency
- Performance metrics
"""

import sqlite3
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple


def check_integrity(db_path: Path) -> Tuple[str, str]:
    """Check database integrity."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('PRAGMA foreign_keys=ON')
    cursor = conn.cursor()

    result = cursor.execute('PRAGMA integrity_check').fetchone()
    conn.close()

    status = 'PASS' if result[0] == 'ok' else 'FAIL'
    message = result[0] if status == 'FAIL' else 'Database integrity verified'
    return status, message


def check_foreign_keys(db_path: Path) -> Tuple[str, str]:
    """Check if foreign keys can be enabled and work correctly."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Check default state (should be OFF by default in SQLite)
    default_fk = cursor.execute('PRAGMA foreign_keys').fetchone()[0]

    # Enable foreign keys
    cursor.execute('PRAGMA foreign_keys=ON')
    enabled_fk = cursor.execute('PRAGMA foreign_keys').fetchone()[0]

    # Check for foreign key violations
    violations = list(cursor.execute('PRAGMA foreign_key_check'))

    conn.close()

    if len(violations) > 0:
        return 'FAIL', f'{len(violations)} foreign key violations found'
    elif enabled_fk == 1:
        # Note: SQLite foreign keys are per-connection, not per-database
        return 'PASS', 'Foreign keys work correctly (ensure enabled in all application connections)'
    else:
        return 'FAIL', 'Foreign keys cannot be enabled'


def check_wal_mode(db_path: Path) -> Tuple[str, str]:
    """Check if WAL mode is enabled."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    mode = cursor.execute('PRAGMA journal_mode').fetchone()[0]
    conn.close()

    status = 'PASS' if mode.lower() == 'wal' else 'WARN'
    message = 'WAL mode enabled' if status == 'PASS' else f'Using {mode} mode (consider WAL for concurrency)'
    return status, message


def check_orphaned_legs(db_path: Path) -> Tuple[str, str]:
    """Check for orphaned trade journal legs (pointing to non-existent strategies)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('PRAGMA foreign_keys=ON')
    cursor = conn.cursor()

    orphaned_count = cursor.execute("""
        SELECT COUNT(*) FROM trade_journal
        WHERE strategy_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM strategies WHERE id = trade_journal.strategy_id)
    """).fetchone()[0]

    conn.close()

    status = 'PASS' if orphaned_count == 0 else 'FAIL'
    message = 'No orphaned legs' if status == 'PASS' else f'{orphaned_count} orphaned legs (foreign key violation)'
    return status, message


def check_orphaned_strategies(db_path: Path) -> Tuple[str, str]:
    """Check for orphaned strategies (with no linked legs)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('PRAGMA foreign_keys=ON')
    cursor = conn.cursor()

    # Check if strategies table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'")
    if not cursor.fetchone():
        conn.close()
        return 'INFO', 'Strategies table not found (migration not yet applied)'

    orphaned_count = cursor.execute("""
        SELECT COUNT(*) FROM strategies s
        WHERE NOT EXISTS (SELECT 1 FROM trade_journal WHERE strategy_id = s.id)
    """).fetchone()[0]

    conn.close()

    status = 'WARN' if orphaned_count > 0 else 'PASS'
    message = 'All strategies have legs' if status == 'PASS' else f'{orphaned_count} strategies with no legs (unusual)'
    return status, message


def check_duplicate_moves(db_path: Path) -> Tuple[str, str]:
    """Check for duplicate historical moves."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    duplicate_count = cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT ticker, earnings_date, COUNT(*) as cnt
            FROM historical_moves
            GROUP BY ticker, earnings_date
            HAVING cnt > 1
        )
    """).fetchone()[0]

    conn.close()

    status = 'PASS' if duplicate_count == 0 else 'FAIL'
    message = 'No duplicate moves' if status == 'PASS' else f'{duplicate_count} duplicate move records (UNIQUE constraint violated)'
    return status, message


def check_null_critical_fields(db_path: Path) -> Tuple[str, str]:
    """Check for NULL values in critical NOT NULL fields."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    issues = []

    # Check historical_moves
    null_tickers = cursor.execute("SELECT COUNT(*) FROM historical_moves WHERE ticker IS NULL").fetchone()[0]
    if null_tickers > 0:
        issues.append(f'historical_moves: {null_tickers} NULL tickers')

    # Check strategies (if table exists)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'")
    if cursor.fetchone():
        null_symbols = cursor.execute("SELECT COUNT(*) FROM strategies WHERE symbol IS NULL").fetchone()[0]
        if null_symbols > 0:
            issues.append(f'strategies: {null_symbols} NULL symbols')

    # Check trade_journal
    null_symbols = cursor.execute("SELECT COUNT(*) FROM trade_journal WHERE symbol IS NULL").fetchone()[0]
    if null_symbols > 0:
        issues.append(f'trade_journal: {null_symbols} NULL symbols')

    conn.close()

    status = 'PASS' if len(issues) == 0 else 'FAIL'
    message = 'All critical fields populated' if status == 'PASS' else ', '.join(issues)
    return status, message


def check_statistics_currency(db_path: Path) -> Tuple[str, str]:
    """Check if ANALYZE statistics are current."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Check if stat1 table exists (created by ANALYZE)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'")
    has_stats = cursor.fetchone() is not None

    if not has_stats:
        conn.close()
        return 'WARN', 'No statistics found (run ANALYZE to optimize queries)'

    stat_count = cursor.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()[0]
    conn.close()

    status = 'PASS' if stat_count > 0 else 'WARN'
    message = f'{stat_count} stat entries' if status == 'PASS' else 'Run ANALYZE to generate statistics'
    return status, message


def get_database_stats(db_path: Path) -> Dict[str, Any]:
    """Get comprehensive database statistics."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('PRAGMA foreign_keys=ON')
    cursor = conn.cursor()

    stats = {
        'file_size_mb': round(db_path.stat().st_size / 1024 / 1024, 2),
        'page_count': cursor.execute('PRAGMA page_count').fetchone()[0],
        'page_size': cursor.execute('PRAGMA page_size').fetchone()[0],
        'freelist_count': cursor.execute('PRAGMA freelist_count').fetchone()[0],
        'tables': {}
    }

    # Get row counts for main tables
    for table in ['historical_moves', 'trade_journal', 'strategies', 'earnings_calendar']:
        try:
            count = cursor.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            stats['tables'][table] = count
        except sqlite3.OperationalError:
            stats['tables'][table] = None  # Table doesn't exist

    # Calculate fragmentation percentage
    if stats['page_count'] > 0:
        stats['fragmentation_pct'] = round((stats['freelist_count'] / stats['page_count']) * 100, 1)
    else:
        stats['fragmentation_pct'] = 0

    conn.close()
    return stats


def print_results(checks: List[Tuple[str, Tuple[str, str]]], stats: Dict[str, Any]):
    """Print formatted health check results."""
    print("\n" + "=" * 70)
    print("DATABASE HEALTH CHECK")
    print("=" * 70)

    # Categorize results
    failures = []
    warnings = []
    passes = []

    for name, (status, message) in checks:
        if status == 'FAIL':
            failures.append((name, message))
        elif status == 'WARN':
            warnings.append((name, message))
        else:
            passes.append((name, message))

    # Print failures first (most critical)
    if failures:
        print("\n🚫 FAILURES (Must Fix):")
        for name, message in failures:
            print(f"  ❌ {name}: {message}")

    # Print warnings
    if warnings:
        print("\n⚠️  WARNINGS (Should Fix):")
        for name, message in warnings:
            print(f"  ⚠️  {name}: {message}")

    # Print passes
    if passes:
        print("\n✅ PASSED:")
        for name, message in passes:
            print(f"  ✓ {name}: {message}")

    # Print statistics
    print("\n" + "-" * 70)
    print("DATABASE STATISTICS")
    print("-" * 70)
    print(f"File Size: {stats['file_size_mb']} MB")
    print(f"Page Count: {stats['page_count']:,}")
    print(f"Page Size: {stats['page_size']:,} bytes")
    print(f"Free Pages: {stats['freelist_count']:,} ({stats['fragmentation_pct']}% fragmentation)")

    if stats['fragmentation_pct'] > 10:
        print(f"  ⚠️  Consider running VACUUM (fragmentation > 10%)")

    print("\nTable Row Counts:")
    for table, count in stats['tables'].items():
        if count is not None:
            print(f"  {table}: {count:,}")
        else:
            print(f"  {table}: <not found>")

    # Overall verdict
    print("\n" + "=" * 70)
    if failures:
        print("VERDICT: ❌ CRITICAL ISSUES FOUND")
        print("Database has data integrity issues that must be fixed immediately.")
        return 1
    elif warnings:
        print("VERDICT: ⚠️  WARNINGS FOUND")
        print("Database is functional but should be optimized.")
        return 0
    else:
        print("VERDICT: ✅ HEALTHY")
        print("Database is in excellent condition.")
        return 0


def main():
    """Run comprehensive database health check."""
    project_root = Path(__file__).parent.parent
    db_path = project_root / "2.0" / "data" / "ivcrush.db"

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    print(f"Checking database: {db_path}")

    # Run all checks
    checks = [
        ('Integrity Check', check_integrity(db_path)),
        ('Foreign Keys', check_foreign_keys(db_path)),
        ('WAL Mode', check_wal_mode(db_path)),
        ('Orphaned Legs', check_orphaned_legs(db_path)),
        ('Orphaned Strategies', check_orphaned_strategies(db_path)),
        ('Duplicate Moves', check_duplicate_moves(db_path)),
        ('NULL Critical Fields', check_null_critical_fields(db_path)),
        ('Query Statistics', check_statistics_currency(db_path)),
    ]

    # Get database statistics
    stats = get_database_stats(db_path)

    # Print results
    exit_code = print_results(checks, stats)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
