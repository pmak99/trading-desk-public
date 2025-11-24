#!/usr/bin/env python3
"""
Database migration CLI tool.

Usage:
    python scripts/migrate.py status           # Show migration status
    python scripts/migrate.py migrate          # Apply all pending migrations
    python scripts/migrate.py rollback <version>  # Rollback to version
    python scripts/migrate.py create <name>    # Create new migration template
"""

import sys
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import Config
from src.infrastructure.database.migrations import MigrationManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def cmd_status(db_path: Path):
    """Show migration status."""
    manager = MigrationManager(db_path)
    status = manager.status()

    print(f"\n📊 Migration Status for {db_path.name}")
    print(f"   Database: {db_path}")
    print(f"   Current version: {status['current_version']}")
    print(f"   Latest version: {status['latest_version']}")
    print(f"   Pending migrations: {status['pending_count']}")

    if status['applied_migrations']:
        print(f"\n✅ Applied Migrations:")
        for version, name, applied_at in status['applied_migrations']:
            print(f"   {version:3d}. {name:40s} (applied: {applied_at})")

    if status['pending_migrations']:
        print(f"\n⏳ Pending Migrations:")
        for version, name in status['pending_migrations']:
            print(f"   {version:3d}. {name}")
    else:
        print(f"\n✨ Database is up to date!")


def cmd_migrate(db_path: Path, target_version: int | None = None):
    """Apply pending migrations."""
    manager = MigrationManager(db_path)

    pending = manager.get_pending_migrations()
    if not pending:
        print(f"✨ Database is up to date (version {manager.get_current_version()})")
        return

    print(f"\n🚀 Migrating {db_path.name}...")
    if target_version:
        print(f"   Target version: {target_version}")
    else:
        print(f"   Target version: {max(m.version for m in pending)} (latest)")

    try:
        count = manager.migrate(target_version)
        print(f"\n✅ Successfully applied {count} migration(s)")
        print(f"   New version: {manager.get_current_version()}")
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)


def cmd_rollback(db_path: Path, target_version: int):
    """Rollback to target version."""
    manager = MigrationManager(db_path)
    current = manager.get_current_version()

    if target_version >= current:
        print(f"⚠️  Already at or below version {target_version} (current: {current})")
        return

    print(f"\n⚠️  Rolling back {db_path.name}...")
    print(f"   From version: {current}")
    print(f"   To version: {target_version}")
    print(f"\n⚠️  WARNING: This will undo migrations and may result in data loss!")

    confirm = input("   Continue? (yes/no): ")
    if confirm.lower() != 'yes':
        print("   Rollback cancelled")
        return

    try:
        count = manager.rollback(target_version)
        print(f"\n✅ Successfully rolled back {count} migration(s)")
        print(f"   New version: {manager.get_current_version()}")
    except Exception as e:
        print(f"\n❌ Rollback failed: {e}")
        sys.exit(1)


def cmd_create(name: str):
    """Create new migration template."""
    # Get next version number by finding max version in migration_manager.py
    from src.infrastructure.database.migrations.migration_manager import MigrationManager

    # Instantiate with dummy path to get migrations
    manager = MigrationManager(":memory:")
    next_version = max(m.version for m in manager.migrations) + 1

    template = f'''"""
Migration {next_version:03d}: {name}

Description:
    TODO: Describe what this migration does

Author: TODO
Date: TODO
"""

# Add to MigrationManager._load_migrations():

self.migrations.append(Migration(
    version={next_version},
    name="{name}",
    sql_up="""
        -- TODO: Add SQL for applying migration
        -- Example:
        -- ALTER TABLE foo ADD COLUMN bar TEXT;
    """,
    sql_down="""
        -- TODO: Add SQL for rolling back migration (optional)
        -- Example:
        -- ALTER TABLE foo DROP COLUMN bar;
    """
))
'''

    print(f"\n📝 Migration template for version {next_version}:")
    print(template)
    print(f"\n💡 Add this migration to:")
    print(f"   src/infrastructure/database/migrations/migration_manager.py")
    print(f"   in the _load_migrations() method")


def main():
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    # Load config
    config = Config.from_env()
    db_path = config.database.path

    if command == "status":
        cmd_status(db_path)

    elif command == "migrate":
        target_version = int(sys.argv[2]) if len(sys.argv) > 2 else None
        cmd_migrate(db_path, target_version)

    elif command == "rollback":
        if len(sys.argv) < 3:
            print("Error: rollback requires target version")
            print("Usage: python scripts/migrate.py rollback <version>")
            sys.exit(1)
        target_version = int(sys.argv[2])
        cmd_rollback(db_path, target_version)

    elif command == "create":
        if len(sys.argv) < 3:
            print("Error: create requires migration name")
            print("Usage: python scripts/migrate.py create <name>")
            sys.exit(1)
        name = "_".join(sys.argv[2:])
        cmd_create(name)

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
