"""Load named panel queries from a .sql file.

Queries are delimited by a `-- name: <identifier>` comment. Keeping them in one
file rather than embedded only in dashboard JSON is what lets CI execute all of
them against a fixture — a panel cannot then reference a column that is always
NULL in production.
"""

import re
from pathlib import Path

_NAME_RE = re.compile(r"^--\s*name:\s*([A-Za-z0-9_]+)\s*$", re.MULTILINE)


def load_queries(path: Path) -> dict:
    """Return {name: sql} for every `-- name:`-delimited block in `path`."""
    text = Path(path).read_text(encoding="utf-8")
    matches = list(_NAME_RE.finditer(text))
    out = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sql = text[start:end].strip()
        if sql:
            out[m.group(1)] = sql
    return out
