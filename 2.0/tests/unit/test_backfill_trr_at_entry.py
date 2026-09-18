"""backfill_trr_at_entry.py against real Postgres.

`/journal` Step 7 used to tag new strategies with an inline block that took the
last 8 *close* moves at import time -- after the trade had closed, so the event
being traded was one of them. On 2026-09-13, 83 of 181 earnings trades sat in a
different TRR tier than the engine would have shown at entry. The backfill now
uses the engine's own function, as of the entry date.
"""

from datetime import date

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl

pytestmark = pytest.mark.pg


@pytest.fixture
def book(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "historical_moves"))
        # Four quiet quarters then a 20% gap: the 4-quarter TRR floor is met as of
        # the report day, excluding that report.
        for day, gap in [("2024-10-10", 2), ("2025-01-10", 2), ("2025-04-10", 2),
                         ("2025-07-10", 2), ("2025-10-10", 20)]:
            conn.execute(
                "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
                " earnings_high, earnings_low, earnings_close) VALUES ('AAA', %s, 100, %s, 125,"
                " 95, 100)", (day, 100 + gap))

        def strategy(symbol, acquired, trr):
            return conn.execute(
                "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date,"
                " gain_loss, is_winner, trr_at_entry)"
                " VALUES (%s, 'SPREAD', %s, %s, 100, true, %s) RETURNING id",
                (symbol, acquired, acquired, trr)).fetchone()[0]

        ids = {
            # AMC entry on the report day: the 20% gap is its outcome.
            "report_day": strategy("AAA", "2025-10-10", None),
            # Opened after that report, which it could see.
            "after": strategy("AAA", "2025-10-13", None),
            # What the old Step 7 stored: TRR including the traded event.
            "stale": strategy("AAA", "2025-10-10", 3.077),
            # No history at all: unknown, not LOW.
            "unknown_stored": strategy("BBB", "2025-10-10", 1.9),
            "unknown_null": strategy("BBB", "2025-10-10", None),
        }
        conn.commit()
    return ids


def _stored():
    with connect() as conn:
        return {r.id: r.trr_at_entry for r in conn.execute("SELECT id, trr_at_entry FROM strategies")}


def test_report_only_by_default(book, capsys):
    from scripts.backfill_trr_at_entry import main

    before = _stored()
    assert main([]) == 0
    assert _stored() == before
    assert "report only" in capsys.readouterr().out


def test_execute_fills_missing_values_as_of_entry(book):
    from scripts.backfill_trr_at_entry import main

    assert main(["--execute"]) == 0
    got = _stored()
    assert got[book["report_day"]] == 1.0
    assert got[book["after"]] == pytest.approx(20 / 5.6, abs=0.0005)
    # Without --all a stored value is left alone, right or wrong.
    assert got[book["stale"]] == 3.077
    assert got[book["unknown_stored"]] == 1.9
    assert got[book["unknown_null"]] is None


def test_all_recomputes_stored_values_and_clears_unknowns(book):
    from scripts.backfill_trr_at_entry import main

    assert main(["--all", "--execute"]) == 0
    got = _stored()
    assert got[book["stale"]] == 1.0
    assert got[book["unknown_stored"]] is None


def test_second_run_changes_nothing(book):
    from scripts.backfill_trr_at_entry import main, plan

    main(["--all", "--execute"])
    with connect() as conn:
        assert plan(conn, recompute_all=True) == []


def test_as_of_is_strictly_before_the_entry_date():
    from scripts.backfill_trr_at_entry import computed_trr

    history = [(date(2025, 7, 10), 2.0), (date(2025, 10, 10), 20.0), (date(2025, 4, 10), 2.0),
               (date(2025, 1, 10), 2.0), (date(2024, 10, 10), 2.0)]
    assert computed_trr(history, date(2025, 10, 10)) == 1.0
    assert computed_trr(history, date(2025, 10, 11)) == round(20 / 5.6, 3)
    # Three prior quarters is below the floor: unknown, not a tier.
    assert computed_trr(history, date(2025, 7, 10)) is None
