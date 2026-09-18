"""/journal's first two steps end to end: a Fidelity export through
scripts/parse_fidelity_csv.py into trade_journal.

The parser writes trading_journal_enhanced.csv (journal/reporting.py's
export_journal_csv) and import_journal_to_db.py reads it back. Nothing tested
that hand-off: each side was tested against a CSV the test wrote by hand, so a
renamed column or a money format the importer cannot parse would pass both
suites and write wrong rows. This runs the real parser's main() on an export
carrying the cases CLAUDE.md's data caveats name -- a wash-sale row, a credit
trade's inverted dates, a multi-fill lot, a fractional share lot, a total row and
a position Fidelity could not price -- plus a lot opened and closed on a report
day that moved 0.0%, and imports what it wrote.
"""

import importlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402
from scripts.import_journal_to_db import import_csv_to_db  # noqa: E402

pytestmark = pytest.mark.pg

HEADER = ("Symbol(CUSIP),Security description,Date acquired,Date sold,Quantity,"
          "Cost basis,Proceeds,Short-term gain/loss,Long-term gain/loss\n")

EXPORT = HEADER + (
    # NVDA bull put spread over the 2026-08-26 report. The short leg is a credit
    # trade (Date sold = the open, before Date acquired = the close) filled in two
    # lots Fidelity lists separately; the long leg expired worthless.
    'NVDA260828P170(1),PUT (NVDA) NVIDIA CORP AUG 28 26 $170 (100 SHS),'
    '2026-08-27,2026-08-25,30,$0.00,"$3,000.00","$3,000.00", --,\n'
    'NVDA260828P170(1),PUT (NVDA) NVIDIA CORP AUG 28 26 $170 (100 SHS),'
    '2026-08-27,2026-08-25,20,$0.00,"$2,000.00","$2,000.00", --,\n'
    'NVDA260828P165(2),PUT (NVDA) NVIDIA CORP AUG 28 26 $165 (100 SHS),'
    '2026-08-25,2026-08-27,50,"$1,500.00",$0.00," -$1,500.00", --,\n'
    # A lot whose loss was disallowed: lot + wash row is the cash, $0.00.
    'AVGO251212P355(8240589GG),PUT (AVGO) BROADCOM INC COM DEC 12 25 $355 (100 SHS),'
    '2025-12-11,2025-12-12,1,$66.01,$8.99, -$57.02, --,\n'
    ',,,Wash Sale,, -$57.02,$0.00,$57.02, --,\n'
    # Bought and sold on KO's 2026-07-21 report day, which moved 0.0%: zero days
    # held and a zero move are values, not gaps.
    'KO260724C70(3),CALL (KO) COCA COLA CO JUL 24 26 $70 (100 SHS),'
    '2026-07-21,2026-07-21,10,$500.00,$620.00,$120.00, --,\n'
    'VOO(922908363),VANGUARD S&P 500 ETF,2024-01-02,2025-03-03,0.444,$200.00,$260.00, --,$60.00,\n'
    'Total,,,,,"$2,266.01","$5,888.99","$3,562.98",$60.00,\n'
    '8261001AB,CALL (ORCL) ORACLE CORP DEC 19 25 $250 (100 SHS),'
    'Unable to provide gain/loss information\n'
)


@pytest.fixture
def journal(pg_schema, tmp_path, monkeypatch, capsys):
    with connect() as conn:
        conn.execute(baseline_ddl("historical_moves", "strategies", "trade_journal"))
        # NVDA reported 2026-08-26: gap +2%, intraday -5%.
        conn.execute(
            "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
            " earnings_high, earnings_low, earnings_close)"
            " VALUES ('NVDA', '2026-08-26', 100, 102, 103, 96, 96.9)")
        # KO reported 2026-07-21 and opened and closed at the prior close: gap and
        # intraday both 0.0%.
        conn.execute(
            "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
            " earnings_high, earnings_low, earnings_close)"
            " VALUES ('KO', '2026-07-21', 70, 70, 70.6, 69.4, 70)")
        conn.commit()

    export = tmp_path / "Realized_Gain_Loss.csv"
    export.write_text("﻿" + EXPORT, encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["parse_fidelity_csv.py", str(export), "--output", str(out)])
    importlib.import_module("parse_fidelity_csv").main()
    return out, capsys.readouterr().out


def test_the_parser_reports_what_it_could_not_import(journal):
    _, printed = journal
    assert "Found 6 trades" in printed
    assert "Matched 4 trades to earnings events" in printed
    assert "DATA GAP — FIDELITY MISSING G/L FOR 1 POSITIONS" in printed
    assert "ORCL    CALL  strike 250.0  exp 2025-12-19" in printed


def test_the_summary_reports_the_wash_sale(journal):
    """calculate_strategy_statistics returned a wash-sale count of 0 whatever the
    trades held, so print_summary's WASH SALES block never printed."""
    _, printed = journal
    lines = [line.strip() for line in printed.splitlines()]
    block = lines[lines.index("WASH SALES"):]
    assert block[1:3] == ["Count:           1", "Disallowed:      $57.02"]


def test_the_csv_export_keeps_a_zero_apart_from_a_blank(journal):
    """`t.days_held or ''` wrote a same-day lot's 0 days as blank, and a 0.0% move
    the same way, so the importer stored NULL: indistinguishable from a lot with no
    dates or no matched report."""
    import csv

    out, _ = journal
    with open(out / "trading_journal_enhanced.csv", newline="") as f:
        rows = {r["Symbol"]: r for r in csv.DictReader(f)}
    assert (rows["KO"]["Days Held"], rows["KO"]["Earnings Date"], rows["KO"]["Actual Move %"]) \
        == ("0", "2026-07-21", "0.0%")
    assert (rows["VOO"]["Earnings Date"], rows["VOO"]["Actual Move %"]) == ("", "")


def test_the_csv_export_leaves_missing_values_blank(tmp_path):
    import csv

    from journal.models import Trade
    from journal.reporting import export_journal_csv

    lot = Trade(symbol="ASAN", description="ASANA INC", quantity=100, acquired_date=None,
                sale_date="2025-06-02", cost_basis=1500.0, proceeds=1400.0,
                gain_loss=-100.0, term="SHORT")
    export_journal_csv([lot], str(tmp_path / "out.csv"))
    with open(tmp_path / "out.csv", newline="") as f:
        (row,) = csv.DictReader(f)
    assert (row["Acquired Date"], row["Days Held"], row["Actual Move %"], row["Wash Sale"]) \
        == ("", "", "", "")


def test_the_json_export_carries_the_folded_wash_sale(journal):
    out, _ = journal
    data = json.loads((out / "trading_journal_enhanced.json").read_text())
    avgo = [t for t in data["trades"] if t["symbol"] == "AVGO"]
    assert [(t["gain_loss"], t["wash_sale_amount"]) for t in avgo] == [
        (pytest.approx(0.0), pytest.approx(57.02))]


def test_what_the_parser_writes_is_what_the_importer_stores(journal):
    out, _ = journal
    result = import_csv_to_db(str(out / "trading_journal_enhanced.csv"), account_type="TAXABLE")
    assert result["errors"] == []
    assert result["inserted"] == 5          # the two NVDA 170P lots are one fill group

    with connect() as conn:
        rows = {(r.symbol, r.strike): r for r in conn.execute(
            "SELECT symbol, strike, option_type, acquired_date, sale_date, days_held, quantity,"
            " cost_basis, proceeds, gain_loss, is_winner, term, wash_sale_amount,"
            " earnings_date, actual_move FROM trade_journal")}

    short = rows[("NVDA", 170.0)]
    assert (short.option_type, short.quantity, short.gain_loss) == ("PUT", 50, 5000.0)
    assert (short.acquired_date, short.sale_date) == (date(2026, 8, 27), date(2026, 8, 25))
    assert short.days_held == -2
    assert (short.earnings_date, short.actual_move) == (date(2026, 8, 26), 5.0)
    assert short.is_winner is True

    long_ = rows[("NVDA", 165.0)]
    assert (long_.gain_loss, long_.is_winner, long_.earnings_date) == (-1500.0, False, date(2026, 8, 26))

    avgo = rows[("AVGO", 355.0)]
    assert (avgo.cost_basis, avgo.proceeds, avgo.gain_loss) == (8.99, 8.99, 0.0)
    assert (avgo.wash_sale_amount, avgo.is_winner) == (57.02, False)

    ko = rows[("KO", 70.0)]
    assert (ko.days_held, ko.earnings_date, ko.actual_move) == (0, date(2026, 7, 21), 0.0)

    voo = rows[("VOO", None)]
    assert (voo.option_type, voo.quantity, voo.term, voo.gain_loss) == (None, 0.444, "LONG", 60.0)
    assert (voo.earnings_date, voo.actual_move) == (None, None)

    # The journal holds cash P&L: every lot plus its wash rows, the total row not
    # counted again, the unpriced ORCL position absent.
    assert sum(r.gain_loss for r in rows.values()) == pytest.approx(3000 + 2000 - 1500 + 0 + 120 + 60)


def test_print_summary_restarts_ytd_each_calendar_year(capsys):
    from journal.reporting import print_summary

    month = {"count": 1, "win_rate": 100.0}
    stats = {
        "total_trades": 3, "win_rate": 66.7, "winners": 2, "losers": 1,
        "total_pnl": 250.0, "winner_pnl": 400.0, "loser_pnl": -150.0,
        "avg_win": 200.0, "avg_loss": -150.0, "profit_factor": 2.67,
        "options_count": 3, "stocks_count": 0, "by_option_type": {},
        "by_ticker": {"NVDA": {"count": 3, "win_rate": 66.7, "pnl": 250.0}},
        "by_month": {"2025-11": dict(month, pnl=100.0), "2025-12": dict(month, pnl=300.0),
                     "2026-01": dict(month, pnl=-150.0)},
        "earnings_correlated": 0, "wash_sales": {"count": 0, "total": 0.0},
    }
    print_summary(stats)
    ytd = {line.split()[0]: line.split("(YTD: $")[1].rstrip(")").strip()
           for line in capsys.readouterr().out.splitlines() if "(YTD:" in line}
    assert ytd == {"2025-11": "100.00", "2025-12": "400.00", "2026-01": "-150.00"}
