#!/usr/bin/env python3
"""
Migrate budget tracking schema to support token-aware tracking.

Adds token tracking columns to existing api_budget tables:
- output_tokens: Number of output tokens (sonar/sonar-pro)
- reasoning_tokens: Number of reasoning tokens (reasoning-pro)
- search_requests: Number of search API requests

Usage:
    python scripts/migrate_budget_schema.py

The script is idempotent - safe to run multiple times.
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime


# Database paths
DB_PATHS = [
    Path(__file__).parent.parent / "4.0" / "data" / "sentiment_cache.db",
    Path(__file__).parent.parent / "5.0" / "data" / "ivcrush.db",
    Path(__file__).parent.parent / "2.0" / "data" / "ivcrush.db",
]

# New columns to add
NEW_COLUMNS = [
    ("output_tokens", "INTEGER DEFAULT 0"),
    ("reasoning_tokens", "INTEGER DEFAULT 0"),
    ("search_requests", "INTEGER DEFAULT 0"),
]


def migrate_database(db_path: Path) -> bool:
    """
    Migrate a single database to add token tracking columns.

    Args:
        db_path: Path to the SQLite database

    Returns:
        True if migration was successful or not needed
    """
    if not db_path.exists():
        print(f"  Skipping {db_path} (does not exist)")
        return True

    print(f"  Migrating {db_path}...")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if api_budget table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='api_budget'
        """)
        if not cursor.fetchone():
            print(f"    No api_budget table found, skipping")
            conn.close()
            return True

        # Get existing columns
        cursor.execute("PRAGMA table_info(api_budget)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        print(f"    Existing columns: {existing_columns}")

        # Add new columns if they don't exist
        columns_added = []
        for column_name, column_def in NEW_COLUMNS:
            if column_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE api_budget ADD COLUMN {column_name} {column_def}")
                    columns_added.append(column_name)
                except sqlite3.OperationalError as e:
                    if "duplicate column" in str(e).lower():
                        print(f"    Column {column_name} already exists")
                    else:
                        raise

        if columns_added:
            conn.commit()
            print(f"    Added columns: {columns_added}")
        else:
            print(f"    All columns already present")

        # Verify schema
        cursor.execute("PRAGMA table_info(api_budget)")
        final_columns = {row[1] for row in cursor.fetchall()}
        print(f"    Final columns: {final_columns}")

        conn.close()
        return True

    except Exception as e:
        print(f"    ERROR: {e}")
        return False


def estimate_tokens_from_cost(cost: float, model: str = "sonar") -> dict:
    """
    Estimate token counts from historical cost data.

    This is a rough estimation for backfilling historical data.
    New tracking will capture actual token counts.

    Args:
        cost: Cost in dollars
        model: Model used (sonar, sonar-pro, reasoning-pro)

    Returns:
        Estimated token breakdown
    """
    # Pricing rates
    PRICING = {
        "sonar_output": 0.000001,      # $1/1M tokens
        "sonar_pro_output": 0.000015,  # $15/1M tokens
        "reasoning_pro": 0.000003,     # $3/1M tokens
        "search_request": 0.005,       # $5/1000 requests
    }

    # Estimate: Assume 80% of cost is from output tokens, 20% from search
    # This is rough but better than 0
    if model == "sonar":
        # Typical: $0.005 search + $0.001 tokens = $0.006
        search_cost = min(cost * 0.8, 0.005)
        token_cost = cost - search_cost
        output_tokens = int(token_cost / PRICING["sonar_output"])
        search_requests = 1 if search_cost >= 0.005 else 0
    elif model == "sonar-pro":
        output_tokens = int(cost / PRICING["sonar_pro_output"])
        search_requests = 0
    else:
        output_tokens = 0
        search_requests = 0

    return {
        "output_tokens": output_tokens,
        "reasoning_tokens": 0,  # Can't estimate without model info
        "search_requests": search_requests,
    }


def backfill_token_estimates(db_path: Path) -> bool:
    """
    Backfill estimated token counts from historical cost data.

    Only fills in rows where all token columns are 0.

    Args:
        db_path: Path to the SQLite database

    Returns:
        True if successful
    """
    if not db_path.exists():
        return True

    print(f"  Backfilling estimates for {db_path}...")

    # Check if api_budget table exists
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='api_budget'
        """)
        if not cursor.fetchone():
            print(f"    No api_budget table found, skipping")
            conn.close()
            return True
        conn.close()
    except Exception as e:
        print(f"    ERROR checking table: {e}")
        return False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if this is a multi-service schema (5.0 style with service column)
        cursor.execute("PRAGMA table_info(api_budget)")
        columns = {row[1] for row in cursor.fetchall()}
        has_service_column = 'service' in columns

        # Get rows with no token data
        if has_service_column:
            cursor.execute("""
                SELECT date, service, cost FROM api_budget
                WHERE (output_tokens = 0 OR output_tokens IS NULL)
                  AND (reasoning_tokens = 0 OR reasoning_tokens IS NULL)
                  AND (search_requests = 0 OR search_requests IS NULL)
                  AND cost > 0
            """)
        else:
            cursor.execute("""
                SELECT date, NULL as service, cost FROM api_budget
                WHERE (output_tokens = 0 OR output_tokens IS NULL)
                  AND (reasoning_tokens = 0 OR reasoning_tokens IS NULL)
                  AND (search_requests = 0 OR search_requests IS NULL)
                  AND cost > 0
            """)
        rows = cursor.fetchall()

        if not rows:
            print(f"    No rows need backfilling")
            conn.close()
            return True

        print(f"    Found {len(rows)} rows to backfill")

        for date_str, service, cost in rows:
            estimates = estimate_tokens_from_cost(cost)
            if has_service_column and service:
                cursor.execute("""
                    UPDATE api_budget
                    SET output_tokens = ?, reasoning_tokens = ?, search_requests = ?
                    WHERE date = ? AND service = ?
                """, (
                    estimates["output_tokens"],
                    estimates["reasoning_tokens"],
                    estimates["search_requests"],
                    date_str,
                    service
                ))
            else:
                cursor.execute("""
                    UPDATE api_budget
                    SET output_tokens = ?, reasoning_tokens = ?, search_requests = ?
                    WHERE date = ?
                """, (
                    estimates["output_tokens"],
                    estimates["reasoning_tokens"],
                    estimates["search_requests"],
                    date_str
                ))

        conn.commit()
        print(f"    Backfilled {len(rows)} rows")
        conn.close()
        return True

    except Exception as e:
        print(f"    ERROR during backfill: {e}")
        return False


def main():
    """Run migrations on all known databases."""
    print("Budget Schema Migration")
    print("=" * 50)
    print(f"Started at: {datetime.now().isoformat()}")
    print()

    # Run migrations
    print("Phase 1: Adding token columns...")
    all_success = True
    for db_path in DB_PATHS:
        if not migrate_database(db_path):
            all_success = False

    print()
    print("Phase 2: Backfilling token estimates...")
    for db_path in DB_PATHS:
        if not backfill_token_estimates(db_path):
            all_success = False

    print()
    if all_success:
        print("Migration completed successfully!")
        return 0
    else:
        print("Migration completed with errors!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
