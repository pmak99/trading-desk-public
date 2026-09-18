#!/usr/bin/env python3
"""One-time SQLite -> Postgres load for the trading database.

Four things here exist because of specific proven failures, not caution:

1. Identity columns are resolved from pg_attribute. pg_get_serial_sequence()
   RAISES on a table with no such column (verified, PG 18.6) rather than
   returning NULL, so the obvious `if not seq: continue` guard never fires
   and the loop dies partway with data committed and sequences unset.
2. Timestamp convention is detected PER VALUE. Postgres parses a naive
   literal in the session TimeZone, which would put 1,258 analysis_log rows
   4 hours early -- with every row count still matching.
3. There is no ON CONFLICT. Target tables are empty; a conflict is a real
   duplicate-key problem that must fail loudly. --truncate-first is the
   supported way to retry.
4. Verification is value-level. Counts cannot see a timezone shift, a column
   silently taking a DDL default, or a dangling foreign key.

Usage:
    python 2.0/scripts/migrate_to_postgres.py [--truncate-first] [SQLITE_DB]

Reads PG* from the environment (source 2.0/.env first).
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# "2026-09-04 12:30:00" or "2026-09-04T08:30:00.123456[-04:00]"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
OFFSET_RE = re.compile(r"[+-]\d{2}:\d{2}$")

# Not migrated. Each has a test in scripts/tests/test_pg_loader.py saying why.
SKIP_TABLES = frozenset({
    # Present in Postgres, but their rows must not come from ivcrush.db:
    "schema_migrations",   # the baseline replaces v1-21
    "sentiment_cache",     # 3-hour TTL, refills itself
    "whisper_cache",       # 6-day TTL, no SQLite source anyway
    "job_status",          # vestigial here; real rows come from job_status.db
    # Cut entirely -- present in SQLite, absent from Postgres:
    "backtest_runs", "backtest_trades", "rate_limits",
    "ticker_metadata", "cache", "iv_log",
})

# The only declared FK is trade_journal.strategy_id -> strategies.id.
# Alphabetical order happens to satisfy it; asserting it beats relying on luck.
LOAD_FIRST = ("strategies",)


def timestamp_convention(value: str) -> str:
    """'offset' | 'et-local' | 'utc' -- derived from the value, not a list.

    The migration plan originally carried a per-column list. Measured against
    the real data it was wrong for bias_predictions (x2) and
    sentiment_history (UTC, not ET), and could not express
    earnings_calendar.updated_at at all, which holds 30,075 UTC rows and 24
    ET-local ones from a single 2026-01-16 script run.

        "2026-09-04T08:30:00.123456"    datetime.now().isoformat() -> ET
        "2026-09-04 12:30:00"           SQLite CURRENT_TIMESTAMP   -> UTC
        "2026-09-08T14:40:53.77-04:00"  already unambiguous        -> as-is
    """
    if OFFSET_RE.search(value):
        return "offset"
    return "et-local" if "T" in value else "utc"


def coerce_row(table: str, row: dict, ts_cols: frozenset[str] = frozenset()) -> dict:
    """Fix values SQLite stores loosely before they reach a typed column.

    `ts_cols` is the set of columns on this table that Postgres actually
    declares as a timestamp type. Only those are converted.

    ⚠️ **Converting on value shape alone corrupts text columns.** An earlier
    version keyed solely off TS_RE, so any timestamp-shaped string became a
    datetime -- and where the destination column is text, psycopg renders that
    datetime straight back to a string in *Postgres's* format. The stored value
    silently changed spelling: taco_log.timestamp went from
    "2026-07-14T11:44:34.282950" to "2026-07-14 11:44:34.282950-04:00".

    The wall clock survived, so every count, id-range, referential and
    timezone-probe check passed. What it broke is ordering. The live writer
    (scripts/taco/store.py) still emits isoformat() with a "T", so the column
    would have held both spellings at once -- and " " (0x20) sorts before
    "T" (0x54), so a lexical ORDER BY on a text column interleaves the two
    eras wrongly. The Grafana taco panels order by exactly these columns.

    Caught by a cross-engine value digest during the Task 5 cutover; no
    count-based check can see it.
    """
    out = dict(row)
    for col, val in out.items():
        if col not in ts_cols:
            continue
        if not isinstance(val, str) or not TS_RE.match(val):
            continue
        kind = timestamp_convention(val)
        if kind == "offset":
            out[col] = datetime.fromisoformat(val)
            continue
        dt = datetime.fromisoformat(val)
        # Attach the zone the value was WRITTEN in. Do not convert the clock:
        # 08:30 ET stays 08:30 ET, which Postgres then stores as 12:30Z.
        out[col] = dt.replace(tzinfo=ET if kind == "et-local" else UTC)
    return out


def timestamp_columns(pg, table: str) -> frozenset[str]:
    """Columns Postgres declares as a date/timestamp type, from the catalog.

    Asking the destination rather than guessing from the value is the whole
    point -- see coerce_row.
    """
    return frozenset(
        r[0]
        for r in pg.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "AND data_type IN ('date', 'timestamp with time zone', "
            "                  'timestamp without time zone')",
            (table,),
        )
    )


def tables_to_migrate(all_tables) -> list[str]:
    """Migratable tables, dependency-ordered."""
    keep = [t for t in all_tables if t not in SKIP_TABLES]
    first = [t for t in LOAD_FIRST if t in keep]
    return first + sorted(t for t in keep if t not in first)


def sqlite_tables(sq) -> list[str]:
    return [r[0] for r in sq.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def identity_columns(pg) -> dict[str, str]:
    """{table: column} for every GENERATED ... AS IDENTITY column.

    From the catalog, because pg_get_serial_sequence(t, 'id') raises
    UndefinedColumn on the five carried tables that have no id column.
    """
    rows = pg.execute("""
        SELECT c.relname, a.attname
          FROM pg_attribute a
          JOIN pg_class     c ON c.oid = a.attrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND a.attidentity <> '' AND a.attnum > 0
    """).fetchall()
    return dict(rows)


def reset_identity_sequences(pg) -> list[tuple[str, int]]:
    """Advance each identity sequence past the highest migrated id."""
    out = []
    for table, col in sorted(identity_columns(pg).items()):
        mx = pg.execute(
            f'SELECT COALESCE(MAX("{col}"), 0) FROM "{table}"').fetchone()[0]
        # is_called=false when the table is empty, so id 1 is still issued.
        pg.execute(
            "SELECT setval(pg_get_serial_sequence(%s, %s), GREATEST(%s, 1), %s)",
            (table, col, mx, mx > 0))
        out.append((table, mx))
    return out


def row_count(conn, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def migrate_table(sq, pg, table: str) -> int:
    rows = sq.execute(f'SELECT * FROM "{table}"').fetchall()
    if not rows:
        return 0
    cols = list(rows[0].keys())
    collist = ", ".join(f'"{c}"' for c in cols)
    ts_cols = timestamp_columns(pg, table)
    with pg.cursor() as cur:
        with cur.copy(f'COPY "{table}" ({collist}) FROM STDIN') as copy:
            for r in rows:
                v = coerce_row(table, dict(r), ts_cols)
                copy.write_row([v[c] for c in cols])
    return len(rows)


def truncate_all(pg, tables) -> None:
    """Retry path. The loader has no ON CONFLICT by design, so a partial run
    must be cleared rather than re-attempted on top."""
    quoted = ", ".join(f'"{t}"' for t in tables)
    pg.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")


def verify(sq, pg, tables) -> dict:
    """Row counts AND identity ranges. Counts alone are not the gate --
    see referential_checks() for what they cannot see."""
    report = {}
    ident = identity_columns(pg)
    for t in tables:
        s_n, p_n = row_count(sq, t), row_count(pg, t)
        entry = {"sqlite": s_n, "postgres": p_n, "match": s_n == p_n}
        if t in ident and p_n:
            col = ident[t]
            entry["id_range"] = pg.execute(
                f'SELECT MIN("{col}"), MAX("{col}") FROM "{t}"').fetchone()
        report[t] = entry
    return report


REFERENTIAL_CHECKS = {
    "trade_journal -> strategies (1,294 rows, 0 orphans expected)":
        "SELECT COUNT(*) FROM trade_journal j LEFT JOIN strategies s "
        "ON j.strategy_id = s.id WHERE j.strategy_id IS NOT NULL AND s.id IS NULL",
    "strategies.parent_strategy_id self-ref (7 refs, 0 orphans expected)":
        "SELECT COUNT(*) FROM strategies a LEFT JOIN strategies b "
        "ON a.parent_strategy_id = b.id "
        "WHERE a.parent_strategy_id IS NOT NULL AND b.id IS NULL",
    "stored-vs-prices invariant (CLAUDE.md; 0 expected)":
        "SELECT COUNT(*) FROM historical_moves WHERE prev_close > 0 AND "
        "ABS(gap_move_pct - (earnings_open-prev_close)/prev_close*100) > 0.02",
}


def referential_checks(pg) -> list[tuple[str, int]]:
    return [(label, pg.execute(sql).fetchone()[0])
            for label, sql in REFERENTIAL_CHECKS.items()]


def timezone_check(sq, pg) -> tuple[str, str]:
    """The wall-clock check no row count could catch."""
    s = sq.execute("SELECT timestamp FROM analysis_log "
                   "ORDER BY timestamp LIMIT 1").fetchone()[0]
    p = pg.execute("SELECT to_char(timestamp AT TIME ZONE 'America/New_York', "
                   "'YYYY-MM-DD HH24:MI:SS') FROM analysis_log "
                   "ORDER BY timestamp LIMIT 1").fetchone()[0]
    return s, p


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sqlite_db", nargs="?",
                    default=str(root / "2.0" / "data" / "ivcrush.db"))
    ap.add_argument("--truncate-first", action="store_true",
                    help="clear target tables before loading (retry path)")
    args = ap.parse_args(argv)

    sq = sqlite3.connect(f"file:{args.sqlite_db}?mode=ro", uri=True)
    sq.row_factory = sqlite3.Row

    print(f"source : {args.sqlite_db}")
    print(f"target : {os.getenv('PGDATABASE')} on {os.getenv('PGHOST')}\n")

    with psycopg.connect() as pg:
        pg_tables = {r[0] for r in pg.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
        tables = [t for t in tables_to_migrate(sqlite_tables(sq))
                  if t in pg_tables]

        if args.truncate_first:
            truncate_all(pg, tables)
            print(f"truncated {len(tables)} tables\n")

        for t in tables:
            n = migrate_table(sq, pg, t)
            print(f"  loaded {t:24s} {n:>7,}")
        pg.commit()

        print("\nidentity sequences:")
        for table, mx in reset_identity_sequences(pg):
            print(f"  {table:24s} max id {mx:>7,}")
        pg.commit()

        print("\nverification (counts + id ranges):")
        failures = 0
        for t, r in sorted(verify(sq, pg, tables).items()):
            flag = "OK  " if r["match"] else "MISMATCH"
            failures += 0 if r["match"] else 1
            extra = f"  ids {r['id_range']}" if "id_range" in r else ""
            print(f"  {flag} {t:24s} sqlite={r['sqlite']:>7,} "
                  f"pg={r['postgres']:>7,}{extra}")

        print("\nreferential checks (all must be 0):")
        for label, n in referential_checks(pg):
            failures += 0 if n == 0 else 1
            print(f"  {'OK  ' if n == 0 else 'FAIL'} {n:>6}  {label}")

        s, p = timezone_check(sq, pg)
        ok = s.replace("T", " ")[:19] == p[:19]
        failures += 0 if ok else 1
        print(f"\ntimezone wall-clock check:\n  {'OK  ' if ok else 'FAIL'} "
              f"sqlite={s}  postgres(ET)={p}")

    print(f"\n{'PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
