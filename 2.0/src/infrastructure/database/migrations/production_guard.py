"""A pytest fixture: no test applies a migration to the production database.

Entry points that write (the bot, the digest, record_moves --execute, scan,
analyze, store_bias_prediction) migrate at start, and a test that runs one of
them unpatched migrates whatever PGDATABASE names -- on 2026-09-14 that applied
migration 3 to production mid-suite. The fixture wraps the apply step itself, so
whichever path reaches it, a test database passes and production raises.
"""

import pytest

from common.pgtesting import refuse_production_migration
from src.infrastructure.database.migrations.migration_manager import MigrationManager


@pytest.fixture(autouse=True, scope="session")
def no_migration_reaches_production():
    original = MigrationManager._apply_migration

    def guarded(self, migration):
        refuse_production_migration(self.conn)
        return original(self, migration)

    MigrationManager._apply_migration = guarded
    try:
        yield
    finally:
        MigrationManager._apply_migration = original
