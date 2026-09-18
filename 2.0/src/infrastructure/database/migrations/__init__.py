"""
Database migration system.

`apply_migrations()` is what an entry point calls; `MigrationManager` takes a
connection, for the maintenance step and tests.
"""

from src.infrastructure.database.migrations.migration_manager import (
    MigrationManager,
    apply_migrations,
)

__all__ = ['MigrationManager', 'apply_migrations']
