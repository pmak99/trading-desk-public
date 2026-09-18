#!/usr/bin/env python3
"""Generate Postgres DDL from the live SQLite schema.

Transcribing 14 tables by hand is how the first version of the migration plan
invented an `id` column that does not exist and dropped two that do. This reads
sqlite_master -- NOT PRAGMA, which cannot see expression indexes -- and probes
every column's ACTUAL stored types, because SQLite's declared type is a hint,
not a constraint.

⚠️ **SPENT — this cannot run any more, and is kept for provenance.** Its input
was `2.0/data/ivcrush.db`, deleted when SQLite was retired (Task 6 Step 5). The
output it produced, `2.0/sql/baseline_schema.sql`, is now the authority: schema
changes go through MigrationManager migrations on top of it, never through a
regenerate.

It is kept because TYPE_OVERRIDES below is the written record of WHY seven
columns are timestamptz/date and not text, and why two quantity columns are
numeric and not bigint -- reasoning that `scripts/jobs/runner.py` and
`2.0/sql/fix_task1_column_types.sql` both cite. The properties it used to
guarantee are now asserted directly against the shipped schema by
`scripts/tests/test_baseline_schema.py`.

Usage (historical):
    python 2.0/scripts/generate_pg_ddl.py [--db PATH] > 2.0/sql/baseline_schema.sql
    python 2.0/scripts/generate_pg_ddl.py --probe      # report type hazards only
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# Cut 2026-09-12. Each verified to have no live *query* reference; the three
# that init_schema.py still CREATE-ed stop being created when that file is
# gutted (migration plan, Task 3 Step 6). Recreating dead schema in a fresh
# database is how it becomes permanent.
CUT = {
    "backtest_runs",     # deleted backtest subsystem
    "backtest_trades",   # deleted backtest subsystem
    "rate_limits",       # no reader, no writer
    "ticker_metadata",   # no reader, no writer
    "cache",             # vestigial; HybridCache uses cache.db/scan_cache.db
    "iv_log",            # 16 rows, superseded by analysis_log since Dec 2025
}

# Timestamp convention is detected PER VALUE, not from a hand-maintained
# column list. The migration plan carried such a list and it was wrong for
# three columns and blind to two more (measured 2026-09-12):
#
#   analysis_log.timestamp        1258 T-sep      -> ET-local   (plan: correct)
#   taco_log.timestamp              24 T-sep      -> ET-local   (plan: correct)
#   taco_positions.created_at       10 T-sep      -> ET-local   (plan: correct)
#   bias_predictions.predicted_at  122 space      -> UTC        (plan: WRONG)
#   bias_predictions.validated_at  120 space      -> UTC        (plan: WRONG)
#   sentiment_history.collected_at  95 space      -> UTC        (plan: WRONG)
#   earnings_calendar.updated_at    24 T / 30075 space -> MIXED  (plan: blind)
#   earnings_calendar.last_validated_at 24 T / 835 space -> MIXED (plan: blind)
#
# Localising a UTC value would shift it +4h -- the same error the list was
# written to prevent, in the opposite direction. A list cannot express the two
# mixed columns at all.
#
# The rule below is derived from how the values were written, which is
# observable in the string itself:
#   "2026-09-04T08:30:00.123456"   datetime.now().isoformat()  -> ET-local
#   "2026-09-04 12:30:00"          SQLite CURRENT_TIMESTAMP    -> UTC
#   "2026-09-08T14:40:53.77-04:00" already offset-bearing      -> as-is
#
# Verified: every timestamp column in the source is cleanly one of these, and
# the two mixed columns resolve correctly per row.
ISO_T_SEPARATOR = "T"


def timestamp_convention(value: str) -> str:
    """'offset' | 'et-local' | 'utc' for a naive-or-aware timestamp string."""
    if re.search(r"[+-]\d{2}:\d{2}$", value):
        return "offset"
    return "et-local" if ISO_T_SEPARATOR in value else "utc"

BASE_TYPES = {
    "INTEGER": "bigint",
    "INT": "bigint",
    "REAL": "double precision",
    "FLOAT": "double precision",
    "DOUBLE": "double precision",
    "NUMERIC": "numeric",
    "DECIMAL": "numeric",
    "TEXT": "text",
    "VARCHAR": "text",
    "CHAR": "text",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "DATETIME": "timestamptz",
    "TIMESTAMP": "timestamptz",
}


# --------------------------------------------------------------------------
# Probing: what is ACTUALLY stored, regardless of what was declared
# --------------------------------------------------------------------------

def carried_tables(conn) -> list[str]:
    """Source tables that survive the migration, in dependency order."""
    names = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return [n for n in names if n not in CUT]


def probe_column_types(conn, table: str, column: str) -> Counter:
    """Storage-class histogram for a column: {'integer': 1285, 'real': 9}."""
    rows = conn.execute(
        f'SELECT typeof("{column}") AS t, COUNT(*) FROM "{table}" GROUP BY t'
    ).fetchall()
    return Counter(dict(rows))


def declared_type(conn, table: str, column: str) -> str:
    for _cid, name, decl, _nn, _df, _pk in conn.execute(
        f'PRAGMA table_info("{table}")'
    ):
        if name == column:
            return decl or ""
    raise KeyError(f"{table}.{column} does not exist")


def find_affinity_hazards(conn) -> list[tuple]:
    """Columns whose stored types contradict their declared type.

    These are the values that abort a text-mode COPY into a strict column.
    """
    hazards = []
    for table in carried_tables(conn):
        for _cid, name, decl, _nn, _df, _pk in conn.execute(
            f'PRAGMA table_info("{table}")'
        ):
            seen = probe_column_types(conn, table, name)
            stored = {t for t in seen if t != "null"}
            d = (decl or "").upper()
            if d.startswith("INT") and "real" in stored:
                hazards.append((table, name, decl, dict(seen)))
            elif d.startswith("BOOL") and stored - {"integer"}:
                hazards.append((table, name, decl, dict(seen)))
            elif "text" in stored and d.startswith(("REAL", "NUM", "INT", "DOUBLE", "FLOAT")):
                hazards.append((table, name, decl, dict(seen)))
    return hazards


# Columns where SQLite's declared TEXT is a lie the DATA cannot expose, because
# an ISO string looks like text no matter how it is probed. Kept to the cases
# where the wrong type is a CORRECTNESS bug, not merely untidy -- every other
# text-stored timestamp here is only ever compared for equality or sorted, and
# a fixed-format ISO string sorts correctly as text.
#
# job_status is the exception. `runner.py`'s once-per-day claim turns on
# `updated_at < now() - interval '1 hour'` -- real timestamp arithmetic, and
# the entire correctness argument for never stealing a live claim. As text that
# comparison is lexicographic, which silently disagrees with real time whenever
# the UTC offset changes: '...01:30:00-04:00' and '...01:30:00-05:00' are an
# hour apart but sort adjacent. A DST boundary would be enough to reclaim a
# live claim or pin a dead one open.
#
# Safe to retype precisely because job_status rows are NOT migrated from
# ivcrush.db (they are vestigial there); the real ones are loaded from
# job_status.db at cutover.
TYPE_OVERRIDES = {
    ("job_status", "date"): "date",
    ("job_status", "updated_at"): "timestamptz",
    # sentiment_cache.cached_at drives the 3-hour TTL check
    # (`cached_at > now() - interval '3 hours'`, in six slash commands). As
    # text that comparison does not type-check at all. Rows are not migrated
    # -- a TTL cache refills itself -- so retyping costs nothing.
    ("sentiment_cache", "cached_at"): "timestamptz",
    ("sentiment_cache", "date"): "date",
    # sentiment_history.earnings_date is compared against real dates by
    # /backfill. collected_at was measured UTC during Task 1's probe.
    ("sentiment_history", "earnings_date"): "date",
    ("sentiment_history", "collected_at"): "timestamptz",
    ("sentiment_history", "updated_at"): "timestamptz",
    # strategies.quantity is DERIVED from trade_journal.quantity, which the
    # probe correctly widened to numeric (9 rows store 0.4 / 2.4 / 0.444).
    # The probe cannot see that: strategies holds only integers today, so it
    # inferred bigint -- narrower than its own source. A spread regrouped from
    # fractional legs would silently ROUND, on the column CLAUDE.md's
    # contract-count sizing rules read. Not reachable right now (all 9
    # fractional legs are already linked), which is exactly why it would go
    # unnoticed later.
    ("strategies", "quantity"): "numeric",
}


def pg_type(conn, table: str, column: str) -> str:
    """Declared type, CORRECTED by what is actually stored."""
    override = TYPE_OVERRIDES.get((table, column))
    if override:
        return override
    decl = declared_type(conn, table, column)
    key = (decl or "TEXT").upper().split("(")[0].strip()
    base = BASE_TYPES.get(key, "text")
    seen = probe_column_types(conn, table, column)
    # An INTEGER column holding REALs must widen, or COPY aborts with
    # 'invalid input syntax for type integer'. Rounding would corrupt data.
    if base == "bigint" and "real" in seen:
        return "numeric"
    return base


# --------------------------------------------------------------------------
# DDL generation
# --------------------------------------------------------------------------

def _table_sql(conn, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row and row[0] else ""


def _column_checks(sql: str, column: str) -> str:
    """Column-level CHECK(...) for `column`, verbatim, or ''."""
    # Match the column's own definition line up to the next top-level comma.
    m = re.search(
        rf'[(,]\s*"?{re.escape(column)}"?\s[^,]*?(CHECK\s*\((?:[^()]|\([^()]*\))*\))',
        sql, re.I | re.S,
    )
    return m.group(1) if m else ""


def _foreign_keys(conn, table: str) -> list[str]:
    """FK clauses from PRAGMA, not regex.

    trade_journal declares its FK column-level (`strategy_id INTEGER
    REFERENCES strategies(id)`), which a table-level `FOREIGN KEY (...)`
    regex misses entirely -- caught by test_foreign_key_survives. PRAGMA
    reports both styles identically.
    """
    out = []
    for _id, _seq, ref_table, from_col, to_col, on_upd, on_del, _m in conn.execute(
        f'PRAGMA foreign_key_list("{table}")'
    ):
        clause = (f'FOREIGN KEY ("{from_col}") '
                  f'REFERENCES "{ref_table}" ("{to_col}")')
        if on_del and on_del != "NO ACTION":
            clause += f" ON DELETE {on_del}"
        if on_upd and on_upd != "NO ACTION":
            clause += f" ON UPDATE {on_upd}"
        out.append(clause)
    return out


def _table_level_constraints(sql: str) -> list[str]:
    """Table-level PRIMARY KEY / UNIQUE clauses lifted verbatim."""
    out = []
    for pat in (
        r'PRIMARY KEY\s*\([^)]*,[^)]*\)',            # composite only
        r'UNIQUE\s*\([^)]*\)',
    ):
        for m in re.finditer(pat, sql, re.I):
            clause = " ".join(m.group(0).split())
            if clause not in out:
                out.append(clause)
    return out


def _default_clause(dflt: str | None, typ: str = "") -> str:
    """SQLite default -> Postgres default.

    `boolean DEFAULT 0` is a hard CREATE TABLE error in Postgres -- there is
    no implicit int->boolean cast. Found by applying the generated DDL to a
    real server; the unit tests all passed with it present.
    """
    if dflt is None:
        return ""
    d = dflt.strip()
    if d.upper() in ("CURRENT_TIMESTAMP", "(CURRENT_TIMESTAMP)"):
        return " DEFAULT now()"
    if typ == "boolean":
        if d in ("0", "1"):
            return f" DEFAULT {'true' if d == '1' else 'false'}"
    return f" DEFAULT {d}"


def generate_table_ddl(conn, table: str) -> str:
    raw = _table_sql(conn, table)
    info = list(conn.execute(f'PRAGMA table_info("{table}")'))
    pk_cols = [r[1] for r in info if r[5]]
    composite_pk = len(pk_cols) > 1
    autoinc = "AUTOINCREMENT" in raw.upper()

    lines = []
    for _cid, name, _decl, notnull, dflt, pk in info:
        typ = pg_type(conn, table, name)
        if pk and not composite_pk and autoinc and typ == "bigint":
            # INTEGER PRIMARY KEY AUTOINCREMENT. BY DEFAULT (not ALWAYS):
            # the migration carries ids across and ALWAYS would reject them.
            col = f'    "{name}" bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY'
        else:
            col = f'    "{name}" {typ}'
            if pk and not composite_pk:
                col += " PRIMARY KEY"
            if notnull and not pk:
                col += " NOT NULL"
            col += _default_clause(dflt, typ)
        chk = _column_checks(raw, name)
        if chk:
            col += f" {' '.join(chk.split())}"
        lines.append(col)

    if composite_pk:
        lines.append(f'    PRIMARY KEY ({", ".join(pk_cols)})')
    for clause in _table_level_constraints(raw):
        if clause.upper().startswith("PRIMARY KEY") and composite_pk:
            continue                      # already emitted from table_info
        lines.append(f"    {clause}")
    for clause in _foreign_keys(conn, table):
        lines.append(f"    {clause}")

    return f'CREATE TABLE "{table}" (\n' + ",\n".join(lines) + "\n);"


def generate_index_ddl(conn, table: str) -> list[str]:
    """Indexes verbatim from sqlite_master -- PRAGMA cannot see expression
    indexes, and idx_trade_journal_dedup is one (the /journal dedup guard)."""
    out = []
    for (sql,) in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
        "AND sql IS NOT NULL ORDER BY name", (table,)
    ):
        stmt = " ".join(sql.split())
        # SQLite accepts [bracket] identifier quoting; Postgres does not.
        stmt = re.sub(r"\[([^\]]+)\]", r'"\1"', stmt)
        out.append(stmt + ";")
    return out


EXTRA_TABLES = '''
-- Folds in scan_cache.db (11 rows, 6-day TTL). Rows are NOT migrated -- a TTL
-- cache refills itself within a day and copying it is pure ceremony.
CREATE TABLE "whisper_cache" (
    "cache_key"  text PRIMARY KEY,
    "payload"    jsonb NOT NULL,
    "expires_at" timestamptz NOT NULL
);
CREATE INDEX idx_whisper_cache_expires ON whisper_cache (expires_at);

-- Derived, recomputed weekly by refresh_risk_metrics.py; never migrated.
-- Sharpe/Sortino need net_campaigns()'s netting rule, which is not
-- expressible as a SQL aggregate (docs/SHARPE_ANALYSIS_2026-09.md section 2).
CREATE TABLE "risk_metrics_snapshot" (
    "window_months" int PRIMARY KEY,
    "sharpe"        double precision,
    "sortino"       double precision,
    "n_months"      int
);
'''


def generate_ddl(conn) -> str:
    parts = [
        "-- Postgres baseline schema for ivcrush.",
        "-- GENERATED by 2.0/scripts/generate_pg_ddl.py -- do not hand-edit.",
        "-- Regenerate with:",
        "--   2.0/venv/bin/python 2.0/scripts/generate_pg_ddl.py > 2.0/sql/baseline_schema.sql",
        "--",
        "-- Types are chosen from the DECLARED type corrected by the ACTUALLY",
        "-- STORED types, because SQLite's declared type is a hint. See",
        "-- find_affinity_hazards().",
        f"-- Cut and deliberately absent: {', '.join(sorted(CUT))}.",
        "",
    ]
    for table in carried_tables(conn):
        parts.append(generate_table_ddl(conn, table))
        parts.extend(generate_index_ddl(conn, table))
        parts.append("")
    parts.append(EXTRA_TABLES.strip())
    return "\n".join(parts) + "\n"


def main_probe(db: Path) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    hazards = find_affinity_hazards(conn)
    if not hazards:
        print("no type-affinity hazards found", file=sys.stderr)
        return 0
    print(f"{len(hazards)} type-affinity hazard(s):", file=sys.stderr)
    for table, col, decl, seen in hazards:
        print(f"  {table}.{col:24s} declared {decl:10s} stored {seen}",
              file=sys.stderr)
        print(f"    -> pg_type resolves to: {pg_type(conn, table, col)}",
              file=sys.stderr)
    return 0


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[2]
    ap.add_argument("--db", type=Path,
                    default=root / "2.0" / "data" / "ivcrush.db")
    ap.add_argument("--probe", action="store_true",
                    help="report type hazards to stderr and exit")
    return ap.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    if args.probe:
        sys.exit(main_probe(args.db))
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    hazards = find_affinity_hazards(conn)
    for table, col, decl, seen in hazards:
        print(f"-- HAZARD {table}.{col}: declared {decl}, stored {seen} "
              f"-> {pg_type(conn, table, col)}", file=sys.stderr)
    sys.stdout.write(generate_ddl(conn))
