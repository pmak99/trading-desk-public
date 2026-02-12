#!/usr/bin/env python3
"""Migration 001: Add strategies table for multi-leg tracking."""

import sqlite3
import sys
from pathlib import Path

VERSION = 4
NAME = "add_strategies_table"
DESCRIPTION = "Create strategies table and add strategy_id foreign key to trade_journal"

def migrate(db_path: str, dry_run: bool = False) -> bool:
    """Create strategies table and add strategy_id to trade_journal."""
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys=ON')
    cursor = conn.cursor()

    # Ensure schema_migrations table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Check if already migrated via version table
    cursor.execute("SELECT version FROM schema_migrations WHERE version = ?", (VERSION,))
    if cursor.fetchone():
        print(f"Migration {VERSION} ({NAME}) already applied")
        conn.close()
        return True

    if dry_run:
        print("DRY RUN - Would create strategies table and add strategy_id column")
        conn.close()
        return True

    try:
        # Create strategies table
        cursor.execute("""
            CREATE TABLE strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy_type TEXT NOT NULL CHECK(strategy_type IN ('SINGLE', 'SPREAD', 'IRON_CONDOR')),
                acquired_date DATE NOT NULL,
                sale_date DATE NOT NULL,
                days_held INTEGER,
                expiration DATE,
                quantity INTEGER,
                net_credit REAL,
                net_debit REAL,
                gain_loss REAL NOT NULL,
                is_winner BOOLEAN NOT NULL,
                earnings_date DATE,
                actual_move REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add strategy_id to trade_journal
        cursor.execute("""
            ALTER TABLE trade_journal ADD COLUMN strategy_id INTEGER REFERENCES strategies(id)
        """)

        # Create index for faster lookups
        cursor.execute("CREATE INDEX idx_trade_journal_strategy_id ON trade_journal(strategy_id)")
        cursor.execute("CREATE INDEX idx_strategies_symbol ON strategies(symbol)")
        cursor.execute("CREATE INDEX idx_strategies_sale_date ON strategies(sale_date)")

        # Record migration in schema_migrations table
        cursor.execute("""
            INSERT INTO schema_migrations (version, name, description)
            VALUES (?, ?, ?)
        """, (VERSION, NAME, DESCRIPTION))

        conn.commit()
        print(f"Migration {VERSION} ({NAME}) applied successfully")
        print(f"  ✓ Created strategies table")
        print(f"  ✓ Added strategy_id column to trade_journal")
        print(f"  ✓ Created 3 indexes")
        return True

    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "core" / "data" / "ivcrush.db"

    dry_run = "--dry-run" in sys.argv
    success = migrate(str(db_path), dry_run=dry_run)
    sys.exit(0 if success else 1)
