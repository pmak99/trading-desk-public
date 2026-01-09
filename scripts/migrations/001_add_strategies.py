#!/usr/bin/env python3
"""Migration: Add strategies table for multi-leg tracking."""

import sqlite3
import sys
from pathlib import Path

def migrate(db_path: str, dry_run: bool = False) -> bool:
    """Create strategies table and add strategy_id to trade_journal."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if already migrated
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'")
    if cursor.fetchone():
        print("Migration already applied: strategies table exists")
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

        conn.commit()
        print("Migration successful: created strategies table and added strategy_id column")
        return True

    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "2.0" / "data" / "ivcrush.db"

    dry_run = "--dry-run" in sys.argv
    success = migrate(str(db_path), dry_run=dry_run)
    sys.exit(0 if success else 1)
