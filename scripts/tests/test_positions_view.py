"""The `positions` view: one trade placed in both accounts is one position.

`strategies` holds a row per account, so the same spread opened in the IRA and in
TAXABLE counted twice in every trade count and win rate that read it -- ten such
pairs on 2026-09-15 (PLTR 297/322, DDOG 342/472, SPXW 301/333 and seven more).
rule_evidence.py merged them in Python; the commands and Grafana did not. The view
is the one definition every reader shares (migration 5).
"""

from datetime import date

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl

pytestmark = pytest.mark.pg


@pytest.fixture
def db(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "positions"))
        conn.commit()

    def strategy(account, *, symbol="AAA", strategy_type="SPREAD", acquired=date(2026, 5, 4),
                 sold=date(2026, 5, 5), expiration=date(2026, 5, 8), quantity=100,
                 gain_loss=1000.0, earnings=date(2026, 5, 4), parent=None,
                 legs=(("PUT", 95.0), ("PUT", 90.0))):
        with connect() as conn:
            sid = conn.execute(
                "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date,"
                " expiration, quantity, gain_loss, is_winner, earnings_date, parent_strategy_id,"
                " account_type, trade_type, trr_at_entry)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 2.1) RETURNING id",
                (symbol, strategy_type, acquired, sold, expiration, quantity, gain_loss,
                 gain_loss > 0, earnings, parent, account,
                 "ROLL" if parent else "NEW")).fetchone()[0]
            for option_type, strike in legs:
                conn.execute(
                    "INSERT INTO trade_journal (symbol, acquired_date, sale_date, option_type,"
                    " strike, expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,"
                    " strategy_id, account_type)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, true, %s, %s)",
                    (symbol, acquired, sold, option_type, strike, expiration, quantity,
                     float(sid), sid, account))
            conn.commit()
        return sid

    return strategy


def _positions():
    with connect() as conn:
        return conn.execute(
            "SELECT strategy_ids, account_type, quantity, gain_loss, is_winner, trr_at_entry"
            " FROM positions ORDER BY id").fetchall()


def test_one_trade_in_both_accounts_is_one_position_with_summed_size_and_pnl(db):
    ira = db("IRA", quantity=200, gain_loss=3000.0)
    taxable = db("TAXABLE", quantity=100, gain_loss=1500.0)
    [p] = _positions()
    assert p.strategy_ids == [ira, taxable]
    assert p.account_type == "IRA+TAXABLE"
    assert (float(p.quantity), float(p.gain_loss), p.is_winner) == (300, 4500, True)
    assert p.trr_at_entry == 2.1


def test_the_win_is_decided_on_the_summed_pnl(db):
    db("IRA", gain_loss=500.0)
    db("TAXABLE", gain_loss=-800.0)
    [p] = _positions()
    assert (float(p.gain_loss), p.is_winner) == (-300, False)


def test_breakeven_is_not_a_win(db):
    db("IRA", gain_loss=500.0)
    db("TAXABLE", gain_loss=-500.0)
    [p] = _positions()
    assert p.is_winner is False


@pytest.mark.parametrize("change", [
    {"symbol": "BBB"},
    {"strategy_type": "SINGLE"},
    {"acquired": date(2026, 5, 1)},
    {"sold": date(2026, 5, 6)},
    {"expiration": date(2026, 5, 15)},
    {"legs": (("PUT", 95.0), ("PUT", 85.0))},
    {"legs": (("CALL", 95.0), ("CALL", 90.0))},
    {"earnings": date(2026, 5, 6)},
])
def test_a_different_trade_in_the_other_account_stays_its_own_position(db, change):
    db("IRA")
    db("TAXABLE", **change)
    assert [len(p.strategy_ids) for p in _positions()] == [1, 1]


def test_rows_with_different_parents_are_not_merged(db):
    first = db("IRA", acquired=date(2026, 4, 1), sold=date(2026, 4, 2), legs=(("PUT", 80.0),))
    other = db("TAXABLE", acquired=date(2026, 4, 1), sold=date(2026, 4, 3), legs=(("PUT", 80.0),))
    db("IRA", parent=first)
    db("TAXABLE", parent=other)
    assert sorted(len(p.strategy_ids) for p in _positions()) == [1, 1, 1, 1]


def test_the_same_position_twice_in_one_account_is_not_merged(db):
    db("IRA")
    db("IRA")
    assert [len(p.strategy_ids) for p in _positions()] == [1, 1]


def test_a_second_copy_in_one_account_pairs_with_the_other_accounts_second_copy(db):
    ira = [db("IRA"), db("IRA")]
    taxable = [db("TAXABLE"), db("TAXABLE")]
    assert [p.strategy_ids for p in _positions()] == [[ira[0], taxable[0]], [ira[1], taxable[1]]]


def test_stock_rows_have_no_legs_and_merge_on_dates(db):
    db("IRA", strategy_type="STOCK", expiration=None, legs=(), earnings=None)
    db("TAXABLE", strategy_type="STOCK", expiration=None, legs=(), earnings=None)
    [p] = _positions()
    assert len(p.strategy_ids) == 2


def test_a_single_account_position_keeps_its_row(db):
    sid = db("TAXABLE", gain_loss=-250.0)
    [p] = _positions()
    assert (p.strategy_ids, p.account_type, float(p.gain_loss), p.is_winner) == (
        [sid], "TAXABLE", -250, False)


def test_the_view_totals_equal_the_table(db):
    db("IRA", gain_loss=1000.0)
    db("TAXABLE", gain_loss=400.0)
    db("TAXABLE", symbol="CCC", gain_loss=-90.0)
    with connect() as conn:
        view = conn.execute("SELECT sum(gain_loss) AS pnl, sum(cardinality(strategy_ids)) AS n"
                            " FROM positions").fetchone()
        table = conn.execute("SELECT sum(gain_loss) AS pnl, count(*) AS n FROM strategies").fetchone()
    assert (float(view.pnl), view.n) == (float(table.pnl), table.n)
