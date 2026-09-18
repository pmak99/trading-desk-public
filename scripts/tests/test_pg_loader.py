"""The one-time SQLite -> Postgres loader.

Every test here maps to a specific proven failure, not to caution. See the
docstrings; each names what breaks and how it was verified.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from migrate_to_postgres import (  # noqa: E402
    SKIP_TABLES,
    coerce_row,
    tables_to_migrate,
)


def test_skips_tables_that_must_not_be_migrated():
    """schema_migrations' 21 rows describe migrations the baseline replaces --
    loading them makes MigrationManager believe v1-21 are applied, which is a
    repeat of the migration-021-sat-unapplied incident. The TTL caches refill
    themselves within a day. job_status is loaded from job_status.db in Task 6
    instead: BOTH sources hold ids 3 and 4 with different rows, so loading
    both is a guaranteed primary-key collision.
    """
    for t in ("schema_migrations", "sentiment_cache", "whisper_cache",
              "job_status"):
        assert t in SKIP_TABLES, t


def test_skips_every_cut_table():
    """These exist in SQLite and deliberately do not exist in Postgres."""
    for t in ("backtest_runs", "backtest_trades", "rate_limits",
              "ticker_metadata", "cache", "iv_log"):
        assert t in SKIP_TABLES, t


def test_strategies_is_loaded_before_trade_journal():
    """The only FK is trade_journal.strategy_id -> strategies.id. Alphabetical
    order happens to work; relying on that is luck, so it is asserted."""
    order = tables_to_migrate(["trade_journal", "strategies", "analysis_log"])
    assert order.index("strategies") < order.index("trade_journal")


def test_et_local_timestamp_keeps_its_wall_clock():
    """analysis_log.timestamp is datetime.now().isoformat() -- ET wall clock,
    no offset. Postgres parses a naive literal in the session TimeZone (UTC in
    the image), landing all 1,258 rows 4 hours early with matching row counts.
    """
    out = coerce_row("analysis_log", {"timestamp": "2026-09-04T08:30:00.123456"},
                     frozenset({"timestamp"}))
    ts = out["timestamp"]
    assert ts.tzinfo is not None, "must be tz-aware before insert"
    assert ts.hour == 8, "wall-clock hour must survive"
    assert ts.utcoffset().total_seconds() == -4 * 3600, "EDT"


def test_utc_timestamp_is_not_shifted():
    """historical_moves.created_at is SQLite CURRENT_TIMESTAMP: already UTC.
    Localising it to ET would break it the other way -- the mistake the
    original per-column list would have made for bias_predictions."""
    out = coerce_row("historical_moves", {"created_at": "2026-09-04 12:30:00"},
                     frozenset({"created_at"}))
    ts = out["created_at"]
    assert ts.tzinfo is not None
    assert ts.hour == 12
    assert ts.utcoffset().total_seconds() == 0


def test_offset_bearing_timestamp_is_left_alone():
    """job_status.updated_at already carries -04:00 and needs neither."""
    out = coerce_row("job_status", {"updated_at": "2026-09-08T14:40:53.770554-04:00"},
                     frozenset({"updated_at"}))
    assert out["updated_at"] == datetime.fromisoformat(
        "2026-09-08T14:40:53.770554-04:00")


def test_mixed_column_resolves_per_row():
    """earnings_calendar.updated_at holds BOTH conventions: 30,075 UTC rows
    and 24 ET-local ones from a single 2026-01-16 script run. A per-column
    rule cannot express this; that is why detection is per value."""
    utc = coerce_row("earnings_calendar", {"updated_at": "2026-01-16 16:23:22"},
                     frozenset({"updated_at"}))
    et = coerce_row("earnings_calendar", {"updated_at": "2026-01-16T16:23:22.117556"},
                    frozenset({"updated_at"}))
    assert utc["updated_at"].utcoffset().total_seconds() == 0
    assert et["updated_at"].utcoffset().total_seconds() == -5 * 3600  # EST in Jan


def test_fractional_quantity_survives():
    """9 trade_journal rows store 0.4 / 2.4 / 0.444 in a column SQLite
    declares INTEGER. Rounding would corrupt real trade legs."""
    out = coerce_row("trade_journal", {"quantity": 0.444})
    assert float(out["quantity"]) == pytest.approx(0.444)


def test_non_timestamp_strings_are_untouched():
    """A ticker is not a timestamp. The detector must not maul ordinary text."""
    out = coerce_row("strategies", {"ticker": "NVDA", "notes": "T is in here"})
    assert out["ticker"] == "NVDA"
    assert out["notes"] == "T is in here"


def test_timestamp_shaped_string_into_a_TEXT_column_is_left_verbatim():
    """The destination type decides, not the value's shape.

    taco_log.timestamp and taco_positions.created_at are text in Postgres by
    design. Coercing them to datetime made psycopg render them back in
    Postgres's own spelling -- "2026-07-14T11:44:34.282950" was stored as
    "2026-07-14 11:44:34.282950-04:00".

    That passes every count, id-range, referential and timezone check, because
    the wall clock is intact. What it breaks is ORDER BY: the live writer
    (scripts/taco/store.py) still emits isoformat() with a "T", so the column
    ends up holding both spellings, and " " (0x20) sorts before "T" (0x54).
    The Grafana taco panels sort on these columns.
    """
    val = "2026-07-14T11:44:34.282950"
    out = coerce_row("taco_log", {"timestamp": val}, ts_cols=frozenset())
    assert out["timestamp"] == val, "a text destination must round-trip verbatim"
    assert isinstance(out["timestamp"], str)

    # Same value, same table, but declared as a timestamp: now it must convert.
    out = coerce_row("taco_log", {"timestamp": val}, ts_cols=frozenset({"timestamp"}))
    assert out["timestamp"].tzinfo is not None
    assert out["timestamp"].hour == 11


def test_ts_cols_defaults_to_converting_nothing():
    """Fail closed. A caller that forgets to pass the column set should leave
    values alone rather than silently rewrite every timestamp-shaped string --
    the previous default was the opposite and that is what shipped the bug."""
    out = coerce_row("taco_log", {"timestamp": "2026-07-14T11:44:34.282950"})
    assert out["timestamp"] == "2026-07-14T11:44:34.282950"


def test_identity_columns_come_from_the_catalog():
    """pg_get_serial_sequence(tbl, 'id') RAISES when the column does not exist
    -- it does not return NULL. Verified on Postgres 18.6:
        ERROR: column "id" of relation "iv_history" does not exist
    Five carried tables have no id column, so a guard keyed on a NULL return
    can never fire; the loop dies partway with data committed and sequences
    unset, which is exactly the duplicate-key bug it exists to prevent.
    """
    src = (ROOT / "2.0" / "scripts" / "migrate_to_postgres.py").read_text()
    assert "attidentity" in src, "resolve identity columns from pg_attribute"
    assert "pg_get_serial_sequence(%s, 'id')" not in src
