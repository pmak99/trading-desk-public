"""scripts/journal/csv_reader.py against Fidelity's Realized Gain/Loss export.

Fidelity writes a wash-sale adjustment as its own row directly under the lot it
adjusts -- no symbol, "Wash Sale" in the Date sold column, the disallowed loss
as a negative cost basis and a positive gain. The reader skipped those rows, so
every imported lot kept Fidelity's tax-lot figure, whose cost basis already
carries disallowed losses rolled in from earlier sales. Against the monthly
statements' cash that overstated taxable 2025 losses materially, one
position accounting for half of it. A lot plus its wash rows equals the statement cash to the
cent, so the reader folds them together and records the adjustment.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from journal.csv_reader import parse_fidelity_csv  # noqa: E402

HEADER = ("Symbol(CUSIP),Security description,Date acquired,Date sold,Quantity,"
          "Cost basis,Proceeds,Short-term gain/loss,Long-term gain/loss\n")


def _write(tmp_path, body):
    path = tmp_path / "export.csv"
    path.write_text("﻿" + HEADER + body, encoding="utf-8")
    return str(path)


def test_a_wash_row_is_folded_into_the_lot_above_it(tmp_path):
    path = _write(tmp_path,
        'AVGO251212P355(8240589GG),PUT (AVGO) BROADCOM INC COM DEC 12 25 $355 (100 SHS),'
        '2025-12-11,2025-12-12,1,$66.01,$8.99, -$57.02, --,\n'
        ',,,Wash Sale,, -$57.02,$0.00,$57.02, --,\n'
        'AVGO251212P355(8240589GG),PUT (AVGO) BROADCOM INC COM DEC 12 25 $355 (100 SHS),'
        '2025-12-11,2025-12-12,199,"$14,103.83","$1,589.19"," -$12,514.64", --,\n')
    trades, skipped, _ = parse_fidelity_csv(path)

    assert len(trades) == 2
    washed, plain = trades
    assert washed.gain_loss == pytest.approx(0.0)
    assert washed.cost_basis == pytest.approx(8.99)
    assert washed.proceeds == pytest.approx(8.99)
    assert washed.wash_sale_amount == pytest.approx(57.02)
    assert plain.gain_loss == pytest.approx(-12514.64)
    assert plain.wash_sale_amount == 0.0
    assert sum(t.gain_loss for t in trades) == pytest.approx(-12514.64)   # the printout's total


def test_several_wash_rows_under_one_lot_all_count(tmp_path):
    path = _write(tmp_path,
        'ORCL250912P215(1),PUT (ORCL) ORACLE CORP SEP 12 25 $215 (100 SHS),'
        '2025-09-10,2025-09-11,10,"$5,000.00",$100.00," -$4,900.00", --,\n'
        ',,,Wash Sale,," -$1,000.00",$0.00,"$1,000.00", --,\n'
        ',,,Wash Sale,, -$500.00,$0.00,$500.00, --,\n')
    (trade,), _, _ = parse_fidelity_csv(path)
    assert trade.gain_loss == pytest.approx(-3400.00)
    assert trade.cost_basis == pytest.approx(3500.00)
    assert trade.wash_sale_amount == pytest.approx(1500.00)


def test_a_wash_row_with_no_lot_above_it_is_reported_not_applied(tmp_path):
    path = _write(tmp_path, ',,,Wash Sale,, -$5.00,$0.00,$5.00, --,\n')
    trades, skipped, _ = parse_fidelity_csv(path)
    assert trades == []
    assert any("wash sale" in reason.lower() for _, reason, _ in skipped)


def test_a_call_on_a_computer_company_is_a_call(tmp_path):
    path = _write(tmp_path,
        'SMCI251107C56(8259979PS),CALL (SMCI) SUPER MICRO COMPUTER INC NOV 07 25 $56 (100 SHS),'
        '2025-11-06,2025-11-05,85,$596.59,"$3,967.91","$3,371.32", --,\n')
    (trade,), _, _ = parse_fidelity_csv(path)
    assert trade.option_type == "CALL"


def test_every_row_reads_its_own_cells(tmp_path):
    """Each lot and wash-sale row reads its own cells. Until 2026-09-14 the two
    cell getters were closures defined inside the row loop over `row` (ruff
    B023): live only if a refactor deferred a call past its iteration, when every
    call would read the last row. csv_reader._row_reader now binds the row it is
    built from; this fails if a getter ever reads a neighbour's cells."""
    path = _write(tmp_path,
        'MU251219P100(1),PUT (MU) MICRON TECHNOLOGY INC DEC 19 25 $100 (100 SHS),'
        '2025-12-10,2025-12-12,10,$1000.00,$100.00, -$900.00, --,\n'
        ',,,Wash Sale,, -$300.00,$0.00,$300.00, --,\n'
        'NVDA251219C200(2),CALL (NVDA) NVIDIA CORP DEC 19 25 $200 (100 SHS),'
        '2025-12-11,2025-12-12,20,$500.00,$50.00, -$450.00, --,\n'
        ',,,Wash Sale,, -$20.00,$0.00,$20.00, --,\n'
        'AAPL251219P250(3),PUT (AAPL) APPLE INC DEC 19 25 $250 (100 SHS),'
        '2025-12-12,2025-12-15,30,$70.00,$770.00,$700.00, --,\n')
    mu, nvda, aapl = parse_fidelity_csv(path)[0]

    assert (mu.symbol, mu.quantity, mu.strike) == ("MU", 10, 100.0)
    assert (mu.gain_loss, mu.wash_sale_amount) == (pytest.approx(-600.0), pytest.approx(300.0))
    assert (nvda.symbol, nvda.quantity, nvda.option_type) == ("NVDA", 20, "CALL")
    assert (nvda.gain_loss, nvda.wash_sale_amount) == (pytest.approx(-430.0), pytest.approx(20.0))
    assert (aapl.symbol, aapl.quantity, aapl.gain_loss) == ("AAPL", 30, pytest.approx(700.0))
    assert aapl.wash_sale_amount == 0.0


def test_a_credit_trade_keeps_fidelitys_inverted_dates(tmp_path):
    """Fidelity writes a sell-to-open with Date sold (the open) before Date
    acquired (the close). The reader stores both as written -- trade_journal
    holds hundreds such rows by design -- and Trade.close_date reads the later."""
    path = _write(tmp_path,
        'NVDA260828P170(1),PUT (NVDA) NVIDIA CORP AUG 28 26 $170 (100 SHS),'
        '2026-08-27,2026-08-25,50,$1000.00,"$3,500.50","$2,500.50", --,\n')
    (trade,), _, _ = parse_fidelity_csv(path)
    assert (trade.acquired_date, trade.sale_date) == ("2026-08-27", "2026-08-25")
    assert trade.close_date == "2026-08-27"
    assert trade.days_held == -2


def test_preamble_summary_and_unavailable_rows_never_become_trades(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(
        "﻿Realized Gain/Loss\n"
        "Account X12345678 -- 01/01/2025 to 12/31/2025\n"
        "\n" + HEADER +
        'AVGO251212P355(8240589GG),PUT (AVGO) BROADCOM INC COM DEC 12 25 $355 (100 SHS),'
        '2025-12-11,2025-12-12,1,$66.01,$8.99, -$57.02, --,\n'
        'Total,,,,,$66.01,$8.99, -$57.02, --,\n'
        '8261001AB,CALL (ORCL) ORACLE CORP DEC 19 25 $250 (100 SHS),'
        'Unable to provide gain/loss information\n'
        'VOO(922908363),VANGUARD S&P 500 ETF,2025-01-02,,5,$2500.00,$2600.00,$100.00, --,\n',
        encoding="utf-8")
    trades, skipped, unable = parse_fidelity_csv(str(path))

    assert [t.symbol for t in trades] == ["AVGO"]
    reasons = [reason for _, reason, _ in skipped]
    assert "Summary row (total)" in reasons
    assert "No sale date" in reasons            # never imported undated
    assert unable == [{"cusip": "8261001AB",
                       "description": "CALL (ORCL) ORACLE CORP DEC 19 25 $250 (100 SHS)",
                       "symbol": "ORCL", "option_type": "CALL", "strike": 250.0,
                       "expiration": "2025-12-19"}]


def test_symbols_quantities_and_the_combined_gain_loss_format(tmp_path):
    """The older export layout (tests/journal/fixtures): one Gain/Loss column with
    a Term column, MM/DD/YYYY dates, parenthesised negatives, OCC symbols."""
    fixture = Path(__file__).parent / "fixtures" / "sample_fidelity_export.csv"
    aapl, put, call = parse_fidelity_csv(str(fixture))[0]

    assert (aapl.symbol, aapl.option_type, aapl.quantity) == ("AAPL", None, 100)
    assert (aapl.acquired_date, aapl.sale_date, aapl.term) == ("2025-01-15", "2025-03-01", "SHORT")
    assert (put.symbol, put.option_type, put.strike, put.expiration) == ("NVDA", "PUT", 150.0, "2025-01-17")
    assert put.quantity == 2                     # a short's -2 is stored as a size
    assert (put.cost_basis, put.gain_loss) == (-420.0, 230.0)
    assert (call.option_type, call.proceeds, call.gain_loss) == ("CALL", -180.0, -200.0)


def test_a_fractional_share_lot_is_not_truncated(tmp_path):
    path = _write(tmp_path,
        'VOO(922908363),VANGUARD S&P 500 ETF,2024-01-02,2025-03-03,0.444,$200.00,$260.00, --,$60.00,\n')
    (trade,), _, _ = parse_fidelity_csv(path)
    assert (trade.symbol, trade.quantity) == ("VOO", pytest.approx(0.444))
    assert (trade.term, trade.gain_loss) == ("LONG", pytest.approx(60.0))


def test_a_ticker_whose_name_contains_total_is_a_trade_not_a_summary_row(tmp_path):
    """Summary rows start with 'Total'/'Subtotal' in the first cell. The filter used to
    match 'total' anywhere in the row, so every TotalEnergies (TTE) lot was dropped
    as a summary row."""
    path = _write(tmp_path,
        'TTE260918C60(1),CALL (TTE) TOTALENERGIES SE SPON ADS SEP 18 26 $60 (100 SHS),'
        '2026-09-10,2026-09-12,10,$1200.00,$800.00, -$400.00, --,\n'
        'Subtotal,,,,,$1200.00,$800.00, -$400.00, --,\n')
    trades, skipped, _ = parse_fidelity_csv(path)

    assert [t.symbol for t in trades] == ["TTE"]
    assert [reason for _, reason, _ in skipped] == ["Summary row (subtotal)"]
