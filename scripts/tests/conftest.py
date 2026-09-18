"""Fixtures for scripts/tests/.

The Postgres fixtures are defined in common/pgtesting.py and shared with
2.0/tests/conftest.py. The two suites run with different rootdirs, so neither
sees the other's conftest -- one definition, two re-exports.

`common` is an installed package (the repo root's pyproject.toml), so nothing
here puts the repo root on sys.path.
"""

from pathlib import Path

import pytest

from common.pgtesting import pg_pool, pg_schema  # noqa: F401
from src.infrastructure.database.migrations.production_guard import (  # noqa: F401,E402
    no_migration_reaches_production,
)

# ⚠️ Several guards in this suite read the Claude command definitions in
# `.claude/commands/`, which are operator tooling and are not carried here.
# Where that directory is absent those guards have nothing to check, so they
# skip instead of failing on the missing file.
COMMANDS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "commands"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    outcome = yield
    exc = outcome.excinfo
    if exc and issubclass(exc[0], FileNotFoundError) and ".claude/commands" in str(exc[1]):
        outcome.force_exception(
            pytest.skip.Exception(
                "%s: %s" % ("the operator's .claude/commands/ definitions are not "
                            "part of this repository", exc[1].filename)
            )
        )
