"""The shipped baseline schema keeps the properties hand-transcription lost.

Replaces test_generate_pg_ddl.py's live-database tests. Those asserted the
same properties by regenerating DDL from `2.0/data/ivcrush.db` -- which was
deleted when SQLite was retired (Task 6 Step 5), so all 14 of them began
skipping with "live database not present". A suite that silently skips looks
like coverage and is not, which is exactly the failure this file exists to
avoid elsewhere.

So the target moved from the INPUT to the ARTIFACT. These read
`2.0/sql/baseline_schema.sql`, the committed file that production is built
from and that every test fixture applies -- a better subject anyway: it is
what actually ships, it cannot go missing, and it stays true after the
generator can no longer run.

Every assertion below is a real defect the first, hand-written migration plan
shipped. The generator was introduced to end them; this keeps them ended.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "2.0" / "sql" / "baseline_schema.sql"

# Cut deliberately at the port -- recreating dead schema in a fresh database
# is how it becomes permanent.
CUT = (
    "backtest_runs",
    "backtest_trades",
    "cache",
    "iv_log",
    "rate_limits",
    "ticker_metadata",
)


@pytest.fixture(scope="module")
def sql() -> str:
    assert BASELINE.exists(), f"baseline schema missing: {BASELINE}"
    return BASELINE.read_text(encoding="utf-8")


def table_block(sql: str, table: str) -> str:
    """The CREATE TABLE body for one table."""
    m = re.search(rf'CREATE TABLE "{table}" \((.*?)\n\);', sql, re.S)
    assert m, f"no CREATE TABLE for {table}"
    return m.group(1)


def column_type(sql: str, table: str, column: str) -> str:
    body = table_block(sql, table)
    m = re.search(rf'^\s+"{column}"\s+([a-z ]+?(?:\(\d+,\s*\d+\))?)(?:\s+(?:NOT NULL|DEFAULT|GENERATED|PRIMARY|CHECK)|,|$)',
                  body, re.M)
    assert m, f"no column {table}.{column}"
    return m.group(1).strip()


# --- type affinity: SQLite's declared type is a hint, not a fact ------------

def test_trade_journal_quantity_is_numeric_not_integer(sql):
    """9 trade_journal rows store fractional quantities (0.4, 2.4, 0.444) in a
    column SQLite DECLARED integer. bigint aborts COPY with 'invalid input
    syntax for type integer'; rounding silently corrupts real trade legs."""
    assert column_type(sql, "trade_journal", "quantity") == "numeric"


def test_strategies_quantity_is_numeric(sql):
    """strategies.quantity derives from trade_journal legs via
    _quantity_from_legs(), so it inherits that fractional-ness. Production
    briefly shipped this as bigint -- see 2.0/sql/fix_task1_column_types.sql."""
    assert column_type(sql, "strategies", "quantity") == "numeric"


def test_declared_integer_without_hazard_stays_bigint(sql):
    """Contrast case, so the two above are meaningful rather than trivially
    true of every numeric column."""
    assert column_type(sql, "historical_moves", "volume_before") == "bigint"


# --- the columns and tables hand-transcription got wrong --------------------

def test_no_id_column_is_invented_on_earnings_calendar(sql):
    """earnings_calendar has no id column. The hand-written version added one,
    which would have made the composite PK unenforceable."""
    body = table_block(sql, "earnings_calendar")
    assert '"id"' not in body


def test_dropped_columns_are_present(sql):
    """created_at and last_validated_at were both dropped by hand
    transcription, and live code writes them."""
    body = table_block(sql, "earnings_calendar")
    assert '"created_at"' in body
    assert '"last_validated_at"' in body


def test_no_cut_table_is_recreated(sql):
    for t in CUT:
        assert f'CREATE TABLE "{t}"' not in sql, f"{t} was cut; do not recreate it"


# --- constraints that are load-bearing, not decorative ----------------------

def test_composite_primary_keys_survive(sql):
    """Four tables key on a composite. The hand-written DDL replaced one with
    a surrogate id, which silently permits duplicates."""
    composites = re.findall(r"PRIMARY KEY \(([^)]+)\)", sql)
    multi = [c for c in composites if "," in c]
    assert len(multi) >= 4, f"expected >=4 composite PKs, found {multi}"


def test_strategy_type_check_constraint_survives(sql):
    """strategies.strategy_type's CHECK is what keeps banned strategy types
    out of the database at all."""
    body = table_block(sql, "strategies")
    assert "CHECK" in body, "strategies lost its CHECK constraint"


def test_trade_journal_dedup_is_a_nulls_not_distinct_constraint(sql):
    """The /journal import's dedup guard. Two properties, each with a failure
    behind it:

    - It must be a CONSTRAINT. scripts/import_journal_to_db.py uses ON CONFLICT
      ON CONSTRAINT, which cannot name a plain unique index; from the cutover
      until migration 2 it named one, and every import row failed.
    - It must be NULLS NOT DISTINCT. Stock legs have NULL option_type and
      strike; with ordinary NULL semantics two identical stock legs never
      collide and a re-import duplicates them. (The COALESCE expression index
      this replaced did the same job but could not be a conflict target.)
    """
    body = table_block(sql, "trade_journal")
    assert re.search(
        r"CONSTRAINT trade_journal_dedup UNIQUE NULLS NOT DISTINCT\s*"
        r"\(symbol, acquired_date, sale_date, option_type, strike, cost_basis, account_type\)",
        body,
    ), "trade_journal_dedup constraint missing or changed"
    importer = (ROOT / "scripts" / "import_journal_to_db.py").read_text()
    assert "ON CONFLICT ON CONSTRAINT trade_journal_dedup" in importer


def test_partial_indexes_keep_their_where_clause(sql):
    """A partial index without its WHERE is a different, larger index."""
    partials = [line for line in sql.splitlines()
                if line.startswith("CREATE INDEX") and " WHERE " in line]
    assert len(partials) >= 2, f"expected >=2 partial indexes, found {partials}"


def test_foreign_key_survives(sql):
    """trade_journal declares its FK column-level in SQLite, which a
    table-level-only reader misses entirely."""
    assert 'REFERENCES "strategies" ("id")' in sql


# --- identity ---------------------------------------------------------------

def test_identity_is_by_default_not_always(sql):
    """GENERATED ALWAYS would reject the explicit ids the migration had to
    preserve, and COPY would fail on every table with a surrogate key."""
    assert "GENERATED BY DEFAULT AS IDENTITY" in sql
    assert "GENERATED ALWAYS AS IDENTITY" not in sql


# --- the type overrides that a text column would have broken ----------------

@pytest.mark.parametrize(
    "table,column,expected",
    [
        # The runner's once-per-day claim is an ON CONFLICT (date, job_name)
        # guarded by updated_at < now() - make_interval(...). Against text
        # there is no interval arithmetic at all.
        ("job_status", "date", "date"),
        ("job_status", "updated_at", "timestamptz"),
        # The 3-hour TTL cannot be subtracted from now() if it is text.
        ("sentiment_cache", "cached_at", "timestamptz"),
        ("sentiment_cache", "date", "date"),
        # UTC is the MEASURED convention here, established by Task 1's probe;
        # the original migration plan had it wrong.
        ("sentiment_history", "collected_at", "timestamptz"),
        ("sentiment_history", "updated_at", "timestamptz"),
        ("sentiment_history", "earnings_date", "date"),
    ],
)
def test_type_overrides_hold(sql, table, column, expected):
    """These are the eight columns production shipped as text by mistake --
    the schema was built in Task 1, before Task 4 added TYPE_OVERRIDES, and
    was never rebuilt. Found during the Task 5 cutover by a cross-engine value
    check; every count-based check passed on the wrong types."""
    assert column_type(sql, table, column) == expected


def test_baseline_version_matches_the_latest_migration(sql):
    """baseline_schema.sql is kept equal to the current schema, and a database
    built from it is seeded at BASELINE_VERSION. If a migration file is added
    without bumping it, a fresh database would replay that migration onto a
    schema that already has it and fail -- on a new host, in apply_migrations()."""
    from src.infrastructure.database.migrations.migration_manager import (
        BASELINE_VERSION,
        MIGRATIONS_DIR,
    )
    versions = sorted(int(p.name[:3]) for p in MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    assert versions, "no migration files found"
    assert BASELINE_VERSION == versions[-1], (
        f"BASELINE_VERSION={BASELINE_VERSION} but the latest migration is {versions[-1]}: "
        "apply the migration to baseline_schema.sql and bump BASELINE_VERSION together"
    )
    assert f"version {BASELINE_VERSION}" in sql.splitlines()[0], (
        "the header's first line must name the version the file embodies"
    )


# --- migration 2 (2026-09-13): the schema as it now is ------------------------

@pytest.mark.parametrize("column,expr", [
    ("intraday_move_pct", "(earnings_close - earnings_open) / earnings_open * 100"),
    ("gap_move_pct", "(earnings_open - prev_close) / prev_close * 100"),
    ("close_move_pct", "(earnings_close - prev_close) / prev_close * 100"),
])
def test_move_percentages_are_generated_from_prices(sql, column, expr):
    """The stored-vs-prices invariant as a schema property. The expressions are
    the writers' own formulas, so recomputing changed no correctly stored value
    -- only the near-zero sign errors the Sep 8 repair's tolerance hid."""
    body = table_block(sql, "historical_moves")
    assert re.search(
        rf'"{column}" double precision NOT NULL\s+GENERATED ALWAYS AS \({re.escape(expr)}\) STORED',
        body,
    ), f"historical_moves.{column} is not generated from {expr}"


@pytest.mark.parametrize("table,column", [
    ("strategies", "gain_loss"), ("strategies", "net_credit"), ("strategies", "net_debit"),
    ("trade_journal", "cost_basis"), ("trade_journal", "proceeds"),
    ("trade_journal", "gain_loss"), ("trade_journal", "wash_sale_amount"),
    ("taco_positions", "premium_paid"), ("taco_positions", "proceeds"),
    ("taco_positions", "outcome_pnl"),
])
def test_money_is_exact(sql, table, column):
    """double precision put binary residue into 117 of 316 strategies.gain_loss
    values. Python still reads these as float (common.db.configure_types)."""
    assert column_type(sql, table, column) == "numeric(14,2)"


@pytest.mark.parametrize("table,column,expected", [
    ("sentiment_history", "prediction_correct", "boolean"),
    ("bias_predictions", "compound_risk_active", "boolean"),
    ("bias_predictions", "sizing_alarm", "boolean"),
    ("taco_positions", "rule_compliant", "boolean"),
    ("taco_positions", "contracts", "bigint"),
    ("taco_positions", "expiration", "date"),
    ("taco_positions", "entry_date", "date"),
    ("taco_positions", "event_date", "date"),
    ("taco_positions", "exit_date", "date"),
    ("taco_positions", "created_at", "timestamptz"),
    ("taco_log", "timestamp", "timestamptz"),
    ("taco_log", "event_date", "date"),
    ("iv_history", "expiration", "date"),
    ("schema_migrations", "applied_at", "timestamptz"),
])
def test_no_column_lies_about_its_type(sql, table, column, expected):
    """The type-fix migration's own argument -- text dates break interval
    arithmetic, integers bound to booleans are rejected -- applied to the
    columns it missed."""
    assert column_type(sql, table, column) == expected


def test_dead_analysis_log_columns_are_gone(sql):
    body = table_block(sql, "analysis_log")
    for col in ("strategy_type", "strategy_score", "strategy_pop", "strategy_rr",
                "contracts", "confidence"):
        assert f'"{col}"' not in body, f"analysis_log.{col} was dropped in migration 2"


# --- migration 3 (2026-09-14) ---------------------------------------------------

def test_dead_bias_predictions_columns_are_gone(sql):
    """Never written -- store_bias_prediction bound NULL for all three, the lookup
    meant to fill them calling a method VRPCalculator lacks -- and never read. 0 of
    123 production rows held a value."""
    body = table_block(sql, "bias_predictions")
    for col in ("vrp_ratio", "implied_move_pct", "historical_mean_pct"):
        assert f'"{col}"' not in body, f"bias_predictions.{col} was dropped in migration 3"


def _check_values(sql: str, table: str, constraint: str) -> set:
    body = table_block(sql, table)
    m = re.search(rf"CONSTRAINT {constraint}\s+CHECK \(\w+ IN \(([^)]*)\)\)", body)
    assert m, f"{table}.{constraint} missing"
    return set(re.findall(r"'([^']*)'", m.group(1)))


def test_check_lists_cover_every_enum_value(sql):
    """A CHECK that omits a value the code can write turns a legitimate write
    into an error -- and analysis_log's writer swallows errors, so the row
    would simply vanish from the A/B data. Adding an enum member without
    widening its CHECK must fail HERE."""
    import sys
    sys.path.insert(0, str(ROOT / "2.0"))  # the engine's `scripts` namespace
    from scripts.taco.constants import Tier
    from src.domain.enums import DirectionalBias, Recommendation, VRPConvention
    from src.domain.vix_regime import VIX_REGIMES

    assert _check_values(sql, "analysis_log", "analysis_log_recommendation_check") == {
        r.value for r in Recommendation}
    assert _check_values(sql, "analysis_log", "analysis_log_vix_regime_check") == {
        r.name for r in VIX_REGIMES}
    biases = {b.value for b in DirectionalBias}
    assert _check_values(sql, "bias_predictions", "bias_predictions_directional_bias_check") == biases
    assert _check_values(sql, "bias_predictions", "bias_predictions_fused_bias_check") == biases
    assert _check_values(sql, "taco_log", "taco_log_tier_check") == {t.value for t in Tier}
    conventions = {c.value for c in VRPConvention}
    assert _check_values(sql, "analysis_log", "analysis_log_vrp_convention_check") == conventions
    assert _check_values(sql, "sentiment_history", "sentiment_history_vrp_convention_check") == conventions


def test_sentiment_direction_is_deliberately_unconstrained(sql):
    """/council writes skew levels and an LLM fills this column; a CHECK here
    would reject real writes. Guard against someone "tidying" it."""
    body = table_block(sql, "sentiment_history")
    assert "sentiment_direction_check" not in body
