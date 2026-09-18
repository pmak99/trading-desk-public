"""Fixtures for tests/journal/.

The Postgres fixtures are defined once in common/pgtesting.py and re-exported
by each test root's conftest, as 2.0/tests/ and scripts/tests/ already do.
`common` is an installed package, so nothing here edits sys.path.
"""

from common.pgtesting import pg_pool, pg_schema  # noqa: F401
from src.infrastructure.database.migrations.production_guard import (  # noqa: F401,E402
    no_migration_reaches_production,
)
