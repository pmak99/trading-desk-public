"""The /journal import, end to end against real Postgres.

It had no test, and from the Postgres cutover until migration 2 it could not
insert a single row: its conflict clause named a unique INDEX (ON CONFLICT ON
CONSTRAINT accepts only constraints), it bound 1/0 to the boolean is_winner,
and with no savepoint the first failure aborted the transaction so every later
row failed too -- all swallowed by a per-row except, reported as "skipped".
"""

import csv

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl
from scripts.import_journal_to_db import import_csv_to_db

pytestmark = pytest.mark.pg

FIELDS = ["Symbol", "Acquired Date", "Sale Date", "Days Held", "Option Type", "Strike",
          "Expiration", "Quantity", "Cost Basis", "Proceeds", "Gain/Loss", "Winner", "Term",
          "Wash Sale", "Earnings Date", "Actual Move %"]


def _row(**kw):
    base = dict.fromkeys(FIELDS, "")
    base.update({"Days Held": "3", "Term": "SHORT", "Winner": "YES"})
    base.update(kw)
    return base


@pytest.fixture
def journal_db(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal"))
        conn.commit()
    return pg_schema


def _write(tmp_path, rows):
    path = tmp_path / "journal.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return str(path)


ROWS = [
    _row(Symbol="NVDA", **{"Acquired Date": "2026-08-27", "Sale Date": "2026-08-28",
         "Option Type": "PUT", "Strike": "170", "Expiration": "2026-08-29", "Quantity": "50",
         "Cost Basis": "$1,000.00", "Proceeds": "$3,500.50", "Gain/Loss": "$2,500.50"}),
    # A fractional stock lot: NULL option_type/strike, quantity 0.4.
    _row(Symbol="VOO", **{"Acquired Date": "2026-01-02", "Sale Date": "2026-03-02",
         "Quantity": "0.4", "Cost Basis": "$200.00", "Proceeds": "$210.00",
         "Gain/Loss": "$10.00", "Term": "LONG"}),
    # A row the database must reject (not a real option type).
    _row(Symbol="AAPL", **{"Acquired Date": "2026-08-27", "Sale Date": "2026-08-28",
         "Option Type": "STRADDLE", "Strike": "200", "Quantity": "1",
         "Cost Basis": "$1.00", "Proceeds": "$2.00", "Gain/Loss": "$1.00"}),
    # Written after the rejected row: must still be inserted.
    _row(Symbol="MSFT", **{"Acquired Date": "2026-08-27", "Sale Date": "2026-08-28",
         "Option Type": "CALL", "Strike": "450", "Quantity": "10",
         "Cost Basis": "$0.00", "Proceeds": "$900.00", "Gain/Loss": "$900.00"}),
]


def test_rows_insert_and_a_bad_row_does_not_poison_the_rest(journal_db, tmp_path):
    result = import_csv_to_db(_write(tmp_path, ROWS), account_type="TAXABLE")
    assert result["inserted"] == 3, result
    assert len(result["errors"]) == 1 and "AAPL" in result["errors"][0]
    with connect() as conn:
        rows = {r.symbol: r for r in conn.execute(
            "SELECT symbol, quantity, is_winner, gain_loss FROM trade_journal")}
    assert set(rows) == {"NVDA", "VOO", "MSFT"}
    assert rows["VOO"].quantity == 0.4, "fractional lot must not be truncated"
    assert rows["NVDA"].is_winner is True
    assert rows["NVDA"].gain_loss == 2500.50


def test_reimport_is_a_no_op_including_null_option_type_stock_legs(journal_db, tmp_path):
    path = _write(tmp_path, [r for r in ROWS if r["Symbol"] != "AAPL"])
    first = import_csv_to_db(path, account_type="TAXABLE")
    second = import_csv_to_db(path, account_type="TAXABLE")
    assert first["inserted"] == 3
    assert second["inserted"] == 0 and second["skipped"] == 3, second
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM trade_journal").fetchone()[0] == 3


def test_the_cli_inserts_only_with_execute(journal_db, tmp_path, monkeypatch):
    import sys

    from scripts import import_journal_to_db

    path = _write(tmp_path, [r for r in ROWS if r["Symbol"] != "AAPL"])
    monkeypatch.setattr(sys, "argv", ["import_journal_to_db.py", "--csv", path])
    import_journal_to_db.main()
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM trade_journal").fetchone()[0] == 0

    monkeypatch.setattr(sys, "argv", ["import_journal_to_db.py", "--csv", path, "--execute"])
    import_journal_to_db.main()
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM trade_journal").fetchone()[0] == 3
