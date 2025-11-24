"""
Database migration system.

Manages schema migrations across all databases in a versioned, repeatable way.
"""

from src.infrastructure.database.migrations.migration_manager import MigrationManager

__all__ = ['MigrationManager']
