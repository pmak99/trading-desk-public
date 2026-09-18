"""Every strategy a writer creates is born with a trade_type.

Until 2026-09-13 nothing set it: every IRA option row and every stock row the
journal created had NULL (93 of 310), so /backtest, /pnl and /risk grouped them
as an unlabelled bucket or COALESCEd them into NEW. A new position is NEW; ROLL
and REPAIR come only from a parent link, which is set by hand when an
adjustment is identified, never inferred at import.
"""

import re
from pathlib import Path

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def db(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal"))
        conn.commit()
    return pg_schema


def _trade_types():
    with connect() as conn:
        return [r.trade_type for r in conn.execute("SELECT trade_type FROM strategies ORDER BY id")]


@pytest.mark.pg
def test_regroup_creates_new_positions_as_new(db):
    from scripts.regroup_strategies import LegRecord, create_strategy_and_link

    with connect() as conn:
        ids = [conn.execute(
            "INSERT INTO trade_journal (symbol, acquired_date, sale_date, option_type, strike,"
            " expiration, quantity, cost_basis, proceeds, gain_loss, is_winner, account_type)"
            " VALUES ('NVDA', '2026-08-26', '2026-08-27', 'CALL', %s, '2026-08-28', 10, %s, %s,"
            " %s, %s, 'IRA') RETURNING id", (strike, cost, proceeds, proceeds - cost,
                                              proceeds > cost)).fetchone()[0]
            for strike, cost, proceeds in ((235, 100.0, 900.0), (240, 500.0, 100.0))]
        legs = [LegRecord(id=i, symbol="NVDA", acquired_date="2026-08-26", sale_date="2026-08-27",
                          option_type="CALL", strike=s, expiration="2026-08-28", quantity=10,
                          cost_basis=0.0, proceeds=0.0, gain_loss=g, is_winner=g > 0,
                          earnings_date="2026-08-26", actual_move=None)
                for i, s, g in zip(ids, (235, 240), (800.0, -400.0), strict=True)]
        create_strategy_and_link(conn, legs, "SPREAD", account_type="IRA")
        conn.commit()
    assert _trade_types() == ["NEW"]


def test_every_strategy_insert_names_trade_type():
    """A writer added later that forgets the column would bring the NULLs back."""
    offenders = []
    for path in list((REPO / "scripts").rglob("*.py")) + list((REPO / "2.0").rglob("*.py")):
        if {"venv", "tests"} & set(path.parts):
            continue
        text = path.read_text()
        for m in re.finditer(r"INSERT INTO strategies\s*\((.*?)\)", text, re.S):
            if "trade_type" not in m.group(1):
                offenders.append(f"{path.relative_to(REPO)}:{text[:m.start()].count(chr(10)) + 1}")
    assert offenders == []
