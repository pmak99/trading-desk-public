"""Every dashboard panel query must run against a representative fixture.

A panel that references an always-NULL column renders an empty graph with no
error. This suite makes that a test failure instead. It exists because two
such defects were caught by hand during design: panels proposed against
analysis_log.strategy_* (100% NULL in production) and a systemd watch on a
-wal file that does not exist between runs.
"""

import re
from pathlib import Path

import psycopg
import pytest

from common.db import connect
from scripts.dashboard_queries import load_queries
from scripts.tests.fixtures.make_dashboard_fixture import build_fixture

QUERIES_PATH = Path(__file__).resolve().parents[2] / "2.0" / "dashboards" / "queries.sql"

# analysis_log.strategy_* was 100% NULL in production (0 of 1,304, May-Sep
# 2026) and migration 2 dropped those columns. Sizing panels read `positions`,
# where quantity is fully populated (both accounts' contracts for a trade in both). Kept as a list so a panel that reaches for
# them fails here by name, not only as a generic "column does not exist".
ALWAYS_NULL_IN_ANALYSIS_LOG = (
    "strategy_type", "strategy_score", "strategy_pop", "strategy_rr", "contracts",
)


@pytest.fixture
def fixture_db(pg_schema):
    """A populated fixture in a throwaway schema.

    Function-scoped, not module-scoped: `pg_schema` uses monkeypatch, which is
    function-scoped. Building it per test costs a fraction of a second.
    """
    build_fixture(pg_schema)
    return pg_schema


def test_queries_file_exists():
    assert QUERIES_PATH.exists(), f"missing {QUERIES_PATH}"


def test_queries_are_named_and_parsed():
    queries = load_queries(QUERIES_PATH)
    assert queries, "no named queries found — check the '-- name:' delimiters"


@pytest.mark.pg
def test_all_queries_execute(fixture_db):
    with connect() as conn:
        for name, sql in queries_or_fail().items():
            try:
                conn.execute(sql).fetchall()
            except psycopg.Error as e:
                conn.rollback()
                pytest.fail(f"query '{name}' failed: {e}\n{sql}")


@pytest.mark.pg
def test_all_queries_return_at_least_one_row(fixture_db):
    """An empty result against a populated fixture means the panel would
    render blank in production — usually an always-NULL column."""
    empty = []
    with connect() as conn:
        for name, sql in queries_or_fail().items():
            if not conn.execute(sql).fetchall():
                empty.append(name)
    assert not empty, f"queries returned no rows against a populated fixture: {empty}"


def queries_or_fail():
    queries = load_queries(QUERIES_PATH)
    assert queries, "no named queries parsed"
    return queries


# Panel -> (its trade-count column, how many of the fixture's two trades placed in
# both accounts its filter admits: MIR, a put spread with legs and a move, enters
# every panel; MIS, a strangle opened after the ban, enters the general ones and
# the tripwire).
COUNTS_A_TRADE_IN_BOTH_ACCOUNTS_ONCE = {
    "trading_journal_freshness": ("total_trades", 2),
    "trading_win_rate_by_month": ("trades", 2),
    "trading_trade_type": ("trades", 2),
    "trading_banned_strategy_tripwire": ("banned_after_ban", 1),
    # The two tripwires below invert in opposite directions, which is the point
    # of both reading positions:
    #   one_position  +1 -- over strategies MIR's IRA and TAXABLE rows share a
    #                       (symbol, earnings_date), so the table FALSELY trips a
    #                       tripwire the view leaves clean.
    #   contract_cap  -1 -- positions sums both accounts' contracts (60+50=110,
    #                       a breach); the table sees 60 and 50 and MISSES it.
    #                       §6 sizes a trade on both accounts together.
    "trading_one_position_tripwire": ("multi_position_events", 1),
    "trading_contract_cap_tripwire": ("over_contract_cap", -1),
    "trading_stance_accuracy": ("trades", 1),
    "trading_direction_accuracy": ("trades", 1),
    "trading_strike_accuracy": ("trades", 1),
    "trading_direction_strike_crosstab": ("trades", 1),
}


@pytest.mark.pg
def test_a_trade_in_both_accounts_counts_once_on_every_panel(fixture_db):
    """strategies holds a row per account, so over the table each mirrored trade
    counts twice; every panel that counts trades reads positions and counts it once.
    The same SQL with the view swapped for the table is the double-counting control."""
    queries = queries_or_fail()
    reading_positions = {n for n, sql in queries.items() if re.search(r"\bFROM\s+positions\b", sql)}
    assert reading_positions == set(COUNTS_A_TRADE_IN_BOTH_ACCOUNTS_ONCE), (
        "a panel reading positions needs its count column here")

    def total(conn, sql, column):
        cur = conn.execute(sql)
        index = [d.name for d in cur.description].index(column)
        return sum(row[index] for row in cur.fetchall())

    with connect() as conn:
        for name, (column, mirrored) in COUNTS_A_TRADE_IN_BOTH_ACCOUNTS_ONCE.items():
            over_view = total(conn, queries[name], column)
            over_table = total(conn, over_strategies(queries[name]), column)
            assert over_table - over_view == mirrored, (
                f"{name}: {over_view} over positions, {over_table} over strategies")


@pytest.mark.pg
def test_a_trade_in_both_accounts_is_sized_on_both_accounts_contracts(fixture_db):
    """MIR is 60 contracts in the IRA and 50 in TAXABLE: one 110-contract trade in
    101-200, not a 60 and a 50 in the bands below. The panel reads
    rule_evidence_snapshot now, and rule_evidence loads the positions view, so the
    snapshot inherits the merge -- this asserts that it survives the indirection."""
    sql = queries_or_fail()["trading_contract_buckets"]
    with connect() as conn:
        bands = {r.bucket: r.trades for r in conn.execute(sql).fetchall()}
    assert bands.get("101\u2013200", 0) >= 1, (
        f"a 110-contract mirrored trade must land in 101-200, got {bands}")


def over_strategies(sql):
    """The same panel over the table: the double-counting control."""
    return re.sub(r"\bpositions\b", "strategies", sql)


def test_no_query_reads_always_null_analysis_log_columns():
    queries = load_queries(QUERIES_PATH)
    for name, sql in queries.items():
        if "analysis_log" not in sql:
            continue
        for col in ALWAYS_NULL_IN_ANALYSIS_LOG:
            assert col not in sql, (
                f"query '{name}' references analysis_log.{col}, which is always "
                f"NULL in production. Sizing panels must read positions instead."
            )


@pytest.mark.pg
def test_fixture_mirrors_production_nullness(pg_schema):
    """The fixture is only useful if it reproduces production's null-ness."""
    if True:
        build_fixture(pg_schema)
        conn = connect()
        try:
            # strategy_* do not exist, like production (dropped in migration 2)
            present = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'analysis_log' AND table_schema = current_schema()"
            ).fetchall()}
            assert present, "fixture has no analysis_log table"
            leaked = present & set(ALWAYS_NULL_IN_ANALYSIS_LOG)
            assert not leaked, f"analysis_log still has dropped columns: {sorted(leaked)}"
            # term_slope_ratio sparse, not absent
            total = conn.execute("SELECT COUNT(*) FROM analysis_log").fetchone()[0]
            slope = conn.execute(
                "SELECT COUNT(term_slope_ratio) FROM analysis_log").fetchone()[0]
            assert 0 < slope < total, "term_slope_ratio must be sparse, like production"
            # strategies.trade_type set on every row
            unset = conn.execute(
                "SELECT COUNT(*) FROM strategies WHERE trade_type IS NULL").fetchone()[0]
            assert unset == 0, "every writer stores trade_type in production since 2026-09-13"
            # strategies.earnings_date only partly populated
            ed = conn.execute(
                "SELECT COUNT(earnings_date) FROM strategies").fetchone()[0]
            st = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
            assert 0 < ed < st, "earnings_date must be partly populated (~43%)"
        finally:
            conn.close()


# --- dashboard JSON must stay in sync with queries.sql -----------------------

DASHBOARD_DIR = QUERIES_PATH.parent


def _dashboard_files():
    return sorted(p for p in DASHBOARD_DIR.glob("*.json"))


def test_at_least_one_dashboard_exists():
    assert _dashboard_files(), "no dashboard JSON generated"


def test_every_panel_query_matches_queries_sql():
    """Panels carry SQL verbatim from queries.sql, because that file is what CI
    executes against the fixture. A panel with drifted SQL is untested SQL."""
    import json

    queries = load_queries(QUERIES_PATH)
    known = set(queries.values())
    for path in _dashboard_files():
        doc = json.loads(path.read_text(encoding="utf-8"))
        for panel in doc.get("panels", []):
            for target in panel.get("targets", []):
                # rawSql is Grafana's first-party Postgres datasource; queryText
                # was frser-sqlite's. Both are accepted so this assertion is
                # about SQL drift, not about which plugin is configured -- but
                # a panel carrying NEITHER is the real hazard: the datasource
                # silently renders it as an empty result rather than erroring.
                sql = target.get("rawSql") or target.get("queryText")
                assert sql, (
                    f"{path.name}: panel '{panel.get('title')}' has no rawSql "
                    f"(Postgres) or queryText (sqlite) — it will render blank"
                )
                assert sql in known, (
                    f"{path.name}: panel '{panel.get('title')}' carries SQL not "
                    f"present in queries.sql — regenerate with "
                    f"`python -m scripts.build_dashboards`"
                )


def test_dashboards_are_regenerable_byte_for_byte():
    """Guards against hand-edits landing in the committed JSON."""
    import json

    import scripts.build_dashboards as bd

    queries = load_queries(QUERIES_PATH)
    for fname, spec in bd.DASHBOARDS.items():
        expected = json.dumps(bd.build(fname, spec, queries), indent=2) + "\n"
        actual = (DASHBOARD_DIR / fname).read_text(encoding="utf-8")
        assert actual == expected, (
            f"{fname} is out of date — run `python -m scripts.build_dashboards`"
        )


def test_every_dashboard_row_is_full_and_uniform_height():
    """A row that does not fill 24 columns leaves dead space; a row mixing
    heights leaves vertical voids. Both were live defects on 2026-09-17:
    trading.json had three short rows and signal.json one mixed-height row.
    build() asserts this at generation time; this pins the committed JSON."""
    import collections
    import json

    for path in _dashboard_files():
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = collections.defaultdict(list)
        for panel in doc["panels"]:
            g = panel["gridPos"]
            rows[g["y"]].append((g["w"], g["h"], panel["title"]))
        for y, row in sorted(rows.items()):
            used = sum(w for w, _, _ in row)
            heights = {h for _, h, _ in row}
            titles = ", ".join(t for _, _, t in row)
            assert used == 24, (
                f"{path.name} y={y}: widths sum to {used}, not 24 ({titles})")
            assert len(heights) == 1, (
                f"{path.name} y={y}: mixed heights {sorted(heights)} ({titles})")


def test_panel_uids_and_titles_are_unique():
    import json

    seen_uids = set()
    for path in _dashboard_files():
        doc = json.loads(path.read_text(encoding="utf-8"))
        uid = doc["uid"]
        assert uid not in seen_uids, f"duplicate dashboard uid: {uid}"
        seen_uids.add(uid)
        titles = [p["title"] for p in doc["panels"]]
        assert len(titles) == len(set(titles)), f"{path.name}: duplicate panel titles"


@pytest.mark.pg
def test_implied_vs_actual_joins_the_latest_analysis_of_the_nearest_event(pg_schema):
    """The rule the correlated subquery encoded, pinned for the ranked join that
    replaced it (2026-09-14): per move, the logged event nearest in date within
    30 days, its latest analysis; two equally near events both join; none
    within 30 days joins nothing."""
    from common.pgtesting import baseline_ddl

    def move(conn, ticker, day, close_pct):
        conn.execute(
            "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
            " earnings_high, earnings_low, earnings_close) VALUES (%s, %s, 100, 100, 110, 90, %s)",
            (ticker, day, 100 + close_pct))

    def analysis(conn, ticker, day, logged_at, implied):
        conn.execute(
            "INSERT INTO analysis_log (timestamp, ticker, earnings_date, expiration,"
            " implied_move_pct, historical_intraday_mean_pct, vrp_intraday_ratio,"
            " recommendation, vrp_convention)"
            " VALUES (%s, %s, %s, %s, %s, 3, 2, 'good', 'intraday')",
            (logged_at, ticker, day, day, implied))

    with connect() as conn:
        conn.execute(baseline_ddl("historical_moves", "analysis_log"))
        move(conn, "NEAR", "2026-05-10", 4.0)
        analysis(conn, "NEAR", "2026-05-08", "2026-05-01T10:00:00", 9.0)
        analysis(conn, "NEAR", "2026-05-08", "2026-05-07T10:00:00", 6.0)   # latest of the nearest
        analysis(conn, "NEAR", "2026-05-20", "2026-05-19T10:00:00", 12.0)  # further away
        move(conn, "TIED", "2026-06-10", -5.0)
        analysis(conn, "TIED", "2026-06-05", "2026-06-04T10:00:00", 8.0)
        analysis(conn, "TIED", "2026-06-15", "2026-06-14T10:00:00", 10.0)
        move(conn, "FAR", "2026-07-10", 3.0)
        analysis(conn, "FAR", "2026-08-25", "2026-08-24T10:00:00", 7.0)    # 46 days: no match
        conn.commit()

        rows = conn.execute(
            load_queries(QUERIES_PATH)["signal_implied_vs_actual_recent"]).fetchall()

    got = sorted((r[1], float(r[2]), float(r[4])) for r in rows)
    assert got == [("NEAR", 6.0, 1.5), ("TIED", 8.0, 1.6), ("TIED", 10.0, 2.0)]
