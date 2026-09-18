"""Application SQL names its columns.

A `SELECT *` hands every consumer whatever the table holds today: a new column
rides into dicts and payloads unasked, and a dropped one fails in the consumer,
far from the query, instead of at the statement. Audit fix plan 5.7 found five
in live code (taco/store.py, bootstrap_sharpe.py, reconcile_journal.py twice,
and the BaseRepository usage example).

Tests are exempt -- `SELECT *` before and after is how they prove a table
unchanged -- and so is the SQLite -> Postgres copier, which moves every column
of every table by design.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXEMPT = {"2.0/scripts/migrate_to_postgres.py"}


def test_no_select_star_outside_tests():
    offenders = []
    for root in ("2.0/src", "2.0/scripts", "scripts", "common"):
        for path in (REPO / root).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if {"venv", "tests"} & set(path.parts) or rel in EXEMPT:
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                # (?<!`): prose quoting a query in backticks is not a query
                if re.search(r"(?<!`)\bSELECT\s+(\w+\.)?\*", line, re.IGNORECASE):
                    offenders.append(f"{rel}:{i}: {line.strip()}")
    assert offenders == []
