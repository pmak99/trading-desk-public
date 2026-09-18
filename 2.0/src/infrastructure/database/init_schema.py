"""Create the baseline Postgres schema.

This file used to be 366 lines of hand-written `CREATE TABLE IF NOT EXISTS`
that duplicated the real schema and drifted from it. It is now a loader for
`2.0/sql/baseline_schema.sql`, which is GENERATED from the source database by
`2.0/scripts/generate_pg_ddl.py` — one definition, tested by
`scripts/tests/test_baseline_schema.py`, which asserts against the shipped
`baseline_schema.sql` rather than regenerating it (the generator's SQLite
input was deleted when SQLite was retired, so it can no longer run).

⚠️ **Gutting this file is what actually retires `rate_limits`,
`ticker_metadata` and `cache`.** Those three tables have no readers and no
writers, but the old version of this file CREATE-ed all three on every
database it touched, which is why "cut" and "deleted from the DDL" were not
the same thing. Deleting them from the generator's output while this file
still created them would have quietly recreated them in the new database.

Also deleted, not ported: `verify_database`, `drop_all_tables`,
`optimize_database` and `_ensure_wal_mode`. All four were SQLite-specific
(VACUUM/ANALYZE tuning, WAL journal mode) with zero callers outside this
module.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASELINE_SQL = Path(__file__).resolve().parents[3] / "sql" / "baseline_schema.sql"


def init_database(conn) -> None:
    """Apply the baseline schema to an empty database. No-op otherwise.

    Args:
        conn: An open psycopg connection. Takes a connection, not a path —
            there is no path any more, and a caller that still passes one
            fails immediately on `.execute` rather than creating something.
    """
    already = conn.execute(
        "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public'"
    ).fetchone()[0]
    if already:
        logger.debug(f"Schema present ({already} tables), skipping baseline")
        return

    conn.execute(BASELINE_SQL.read_text())
    conn.commit()
    logger.info(f"Applied baseline schema from {BASELINE_SQL.name}")
