"""scripts/reenrich_journal_earnings.py against real Postgres.

768 of 1,121 option legs in trade_journal had no earnings_date on 2026-09-13,
and only 127 of 282 spread/single strategies did. The matcher today places 527
of those legs. This backfill fills the gaps without overwriting anything: a
stored date the matcher disagrees with is reported for review, never replaced.
--refresh re-derives the move on dated rows and never touches a date.
"""

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl

pytestmark = pytest.mark.pg


@pytest.fixture
def journal(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "historical_moves"))
        for ticker, day in [("NVDA", "2026-08-26"), ("INTC", "2026-01-22"),
                            ("MU", "2026-06-24"), ("MU", "2026-06-30")]:
            conn.execute(
                "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
                " earnings_high, earnings_low, earnings_close)"
                " VALUES (%s, %s, 100, 102, 103, 96, 96.9)", (ticker, day))

        def strategy(symbol, acq, sale, earnings=None):
            return conn.execute(
                "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date,"
                " expiration, gain_loss, is_winner, earnings_date)"
                " VALUES (%s, 'SPREAD', %s, %s, %s, 100, true, %s) RETURNING id",
                (symbol, acq, sale, sale, earnings)).fetchone()[0]

        def leg(sid, symbol, acq, sale, strike, earnings=None):
            conn.execute(
                "INSERT INTO trade_journal (symbol, acquired_date, sale_date, option_type,"
                " strike, cost_basis, proceeds, gain_loss, is_winner, earnings_date, strategy_id)"
                " VALUES (%s, %s, %s, 'PUT', %s, 0, 50, 50, true, %s, %s)",
                (symbol, acq, sale, strike, earnings, sid))

        ids = {}
        # Brackets NVDA's 08-26 event; nothing stored yet.
        ids["nvda"] = strategy("NVDA", "2026-08-25", "2026-08-27")
        leg(ids["nvda"], "NVDA", "2026-08-27", "2026-08-25", 170)
        leg(ids["nvda"], "NVDA", "2026-08-27", "2026-08-25", 165)
        # Stored 01-28, but the only event in the table is 01-22.
        ids["intc"] = strategy("INTC", "2026-01-21", "2026-01-23", earnings="2026-01-28")
        leg(ids["intc"], "INTC", "2026-01-21", "2026-01-23", 40, earnings="2026-01-28")
        # Two legs of one strategy that bracket different events.
        ids["mu"] = strategy("MU", "2026-06-23", "2026-07-01")
        leg(ids["mu"], "MU", "2026-06-23", "2026-06-25", 100)
        leg(ids["mu"], "MU", "2026-06-29", "2026-07-01", 95)
        conn.commit()
    return ids


def _strategy_dates():
    with connect() as conn:
        return {r.symbol: r.earnings_date and r.earnings_date.isoformat()
                for r in conn.execute("SELECT symbol, earnings_date FROM strategies")}


def _leg_dates():
    with connect() as conn:
        return sorted((r.symbol, r.strike, r.earnings_date and r.earnings_date.isoformat())
                      for r in conn.execute("SELECT symbol, strike, earnings_date FROM trade_journal"))


def test_dry_run_writes_nothing(journal):
    from scripts.reenrich_journal_earnings import apply, plan

    p = plan()
    assert len(p.legs) == 4          # two NVDA, two MU
    assert apply(p, execute=False) == (0, 0)
    assert _strategy_dates() == {"NVDA": None, "INTC": "2026-01-28", "MU": None}


def test_execute_fills_legs_and_propagates_only_unambiguous_strategies(journal):
    from scripts.reenrich_journal_earnings import apply, plan

    p = plan()
    assert apply(p, execute=True) == (4, 1)
    assert _leg_dates() == [
        ("INTC", 40.0, "2026-01-28"),
        ("MU", 95.0, "2026-06-30"),
        ("MU", 100.0, "2026-06-24"),
        ("NVDA", 165.0, "2026-08-26"),
        ("NVDA", 170.0, "2026-08-26"),
    ]
    assert _strategy_dates() == {"NVDA": "2026-08-26", "INTC": "2026-01-28", "MU": None}


def test_a_disagreeing_stored_date_is_reported_not_replaced(journal):
    from scripts.reenrich_journal_earnings import plan

    p = plan()
    assert [(d.symbol, d.stored, d.matched) for d in p.disagreements] == [
        ("INTC", "2026-01-28", "2026-01-22")]
    assert [c.strategy_id for c in p.conflicted_strategies] == [journal["mu"]]


def test_a_second_run_is_a_no_op(journal):
    from scripts.reenrich_journal_earnings import apply, plan

    apply(plan(), execute=True)
    assert apply(plan(), execute=True) == (0, 0)


# --refresh: a dated row's actual_move re-derived from its event's move row.
#
# Data repairs re-measure a move row in place, and nothing revisited the copies
# the journal took at import: after the 2026-09-14 repairs 93 of 205 dated
# strategies (3 NULL) and 282 of 941 dated legs disagreed with their event.

COF_MOVE = abs((97.65 - 100.64) / 100.64 * 100)   # 2.97098...: not a one-decimal value


@pytest.fixture
def dated_journal(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "historical_moves"))
        # (prev_close, open, close); the move is max(|gap|, |intraday|).
        for ticker, day, prev, open_, close in [
            ("AMD", "2026-02-03", 100, 88.8, 88.8),      # gap -11.2 -> 11.2
            ("PYPL", "2026-02-03", 100, 80, 80),        # gap -20 -> 20
            ("COF", "2026-07-21", 100, 100.64, 97.65),  # intraday -2.97098... -> 2.97098...
        ]:
            conn.execute(
                "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
                " earnings_high, earnings_low, earnings_close) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (ticker, day, prev, open_, max(open_, close), min(open_, close), close))

        def strategy(symbol, day, move):
            return conn.execute(
                "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date,"
                " expiration, gain_loss, is_winner, earnings_date, actual_move)"
                " VALUES (%s, 'SPREAD', %s, %s, %s, 100, true, %s, %s) RETURNING id",
                (symbol, day, day, day, day, move)).fetchone()[0]

        def leg(sid, symbol, day, strike, move, dated=True):
            return conn.execute(
                "INSERT INTO trade_journal (symbol, acquired_date, sale_date, option_type,"
                " strike, cost_basis, proceeds, gain_loss, is_winner, earnings_date,"
                " actual_move, strategy_id)"
                " VALUES (%s, %s, %s, 'PUT', %s, 0, 50, 50, true, %s, %s, %s) RETURNING id",
                (symbol, day, day, strike, day if dated else None, move, sid)).fetchone()[0]

        ids = {}
        # Re-measured since import: the leg holds the old full value, the strategy
        # a one-decimal value a tenth away. Both stale.
        ids["amd"] = strategy("AMD", "2026-02-03", 11.3)
        ids["amd_leg"] = leg(ids["amd"], "AMD", "2026-02-03", 100, 16.6)
        # Dated, but no move was ever copied.
        ids["pypl"] = strategy("PYPL", "2026-02-03", None)
        ids["pypl_leg"] = leg(ids["pypl"], "PYPL", "2026-02-03", 60, None)
        # Current: the strategy holds the full value, the leg the /journal CSV's
        # one-decimal rendering of it.
        ids["cof"] = strategy("COF", "2026-07-21", COF_MOVE)
        ids["cof_leg"] = leg(ids["cof"], "COF", "2026-07-21", 90, 3.0)
        # Dated to an event with no move row: nothing to derive from.
        ids["zs"] = strategy("ZS", "2026-05-28", 7.0)
        ids["zs_leg"] = leg(ids["zs"], "ZS", "2026-05-28", 200, 7.0)
        # Undated: filling it is the default mode's job, not the refresh's.
        ids["undated_leg"] = leg(None, "AMD", "2026-03-10", 95, None, dated=False)
        conn.commit()
    return ids


def _moves(table):
    with connect() as conn:
        return {r.id: r.actual_move for r in conn.execute(f"SELECT id, actual_move FROM {table}")}


def test_refresh_plan_lists_only_moves_that_differ_from_their_event(dated_journal):
    from scripts.reenrich_journal_earnings import refresh_plan

    p = refresh_plan()
    assert sorted((c.table, c.row_id, c.stored, round(c.current, 4)) for c in p.changes) == sorted([
        ("strategies", dated_journal["amd"], 11.3, 11.2),
        ("strategies", dated_journal["pypl"], None, 20.0),
        ("trade_journal", dated_journal["amd_leg"], 16.6, 11.2),
        ("trade_journal", dated_journal["pypl_leg"], None, 20.0),
    ])
    assert sorted((m.table, m.row_id) for m in p.no_move_row) == sorted([
        ("strategies", dated_journal["zs"]), ("trade_journal", dated_journal["zs_leg"])])


def test_refresh_report_only_writes_nothing(dated_journal):
    from scripts.reenrich_journal_earnings import apply_refresh, refresh_plan

    before = (_moves("strategies"), _moves("trade_journal"))
    assert apply_refresh(refresh_plan(), execute=False) == (0, 0)
    assert (_moves("strategies"), _moves("trade_journal")) == before


def test_refresh_execute_writes_the_event_move_and_nothing_else(dated_journal):
    from scripts.reenrich_journal_earnings import apply_refresh, refresh_plan

    assert apply_refresh(refresh_plan(), execute=True) == (2, 2)
    strategies, legs = _moves("strategies"), _moves("trade_journal")
    assert strategies[dated_journal["amd"]] == pytest.approx(11.2)
    assert strategies[dated_journal["pypl"]] == pytest.approx(20.0)
    assert strategies[dated_journal["cof"]] == COF_MOVE
    assert strategies[dated_journal["zs"]] == 7.0
    assert legs[dated_journal["amd_leg"]] == pytest.approx(11.2)
    assert legs[dated_journal["pypl_leg"]] == pytest.approx(20.0)
    assert legs[dated_journal["cof_leg"]] == 3.0
    assert legs[dated_journal["zs_leg"]] == 7.0
    assert legs[dated_journal["undated_leg"]] is None
    assert refresh_plan().changes == []


def test_refresh_writes_nothing_when_a_row_changed_after_the_plan(dated_journal):
    from scripts.reenrich_journal_earnings import apply_refresh, refresh_plan

    p = refresh_plan()
    with connect() as conn:
        conn.execute("UPDATE trade_journal SET actual_move = 1.0 WHERE id = %s",
                     (dated_journal["amd_leg"],))
        conn.commit()
    with pytest.raises(RuntimeError, match="changed since the plan"):
        apply_refresh(p, execute=True)
    assert _moves("strategies")[dated_journal["amd"]] == 11.3
    assert _moves("trade_journal")[dated_journal["pypl_leg"]] is None


def test_refresh_cli_is_report_only_without_execute(dated_journal, capsys):
    from scripts.reenrich_journal_earnings import main

    assert main(["--refresh"]) == 0
    out = capsys.readouterr().out
    assert "REPORT ONLY" in out
    assert "Moves to refresh: 4 (2 strategies, 2 legs)" in out
    assert _moves("strategies")[dated_journal["amd"]] == 11.3

    assert main(["--refresh", "--execute"]) == 0
    assert "Refreshed 2 legs and 2 strategies." in capsys.readouterr().out
    assert _moves("strategies")[dated_journal["amd"]] == pytest.approx(11.2)
