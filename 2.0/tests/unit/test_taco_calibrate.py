"""Unit tests for scripts/taco/calibrate.py.

The script had no tests, and it stopped working at the Postgres port without
anyone noticing: `strategies.acquired_date` comes back as a `date`, the event
labels were keyed by ISO strings, and so every row fell through to the default
(UNKNOWN, CALL) -- the score's event component silently dropped to its floor
on every historical entry, and every put was scored as a call. The same
string-vs-date mismatch defeated the dedupe of three hand-typed trades, which
were then counted twice once the journal held them, and `f"{date:11}"` printed
the entry column as the literal "11" (a date's format spec is strftime).

These run the real `main()` against a throwaway schema with synthetic market
series patched in, so nothing reaches yfinance and nothing reaches production.
"""

import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402
from scripts.taco import calibrate  # noqa: E402
from scripts.taco.constants import EventType  # noqa: E402

CALIBRATE_SOURCE = Path(calibrate.__file__).read_text()


def _series(start: date, end: date, base: float, amp: float, period: float):
    out, d, i = [], start, 0
    while d <= end:
        out.append((d, base + amp * math.sin(i / period)))
        d += timedelta(days=1)
        i += 1
    return out


SERIES_START, SERIES_END = date(2024, 10, 1), date(2026, 9, 1)
FAKE_SERIES = {
    "^GSPC": _series(SERIES_START, SERIES_END, 6000.0, 150.0, 6.0),
    "^VIX": _series(SERIES_START, SERIES_END, 20.0, 4.0, 3.0),
    "^VIX3M": _series(SERIES_START, SERIES_END, 21.0, 1.0, 9.0),
    "TLT": _series(SERIES_START, SERIES_END, 90.0, 3.0, 11.0),
    "UUP": _series(SERIES_START, SERIES_END, 28.0, 0.5, 13.0),
    "USO": _series(SERIES_START, SERIES_END, 75.0, 5.0, 7.0),
}


@pytest.fixture
def book(pg_schema):
    """strategies + trade_journal + the positions view, built from the real baseline schema."""
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "positions"))
        conn.commit()
    return pg_schema


def add_trade(symbol, acquired, sale, expiration, gain_loss, option_type,
              strategy_type="SINGLE", account="TAXABLE"):
    """One strategy and one journal leg, bound as `date` objects -- the shape
    production returns since the cutover."""
    with connect() as conn:
        sid = conn.execute(
            """INSERT INTO strategies (symbol, strategy_type, acquired_date,
                   sale_date, expiration, quantity, gain_loss, is_winner, account_type)
               VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s) RETURNING id""",
            (symbol, strategy_type, acquired, sale, expiration, gain_loss,
             gain_loss > 0, account)).fetchone()[0]
        conn.execute(
            """INSERT INTO trade_journal (symbol, acquired_date, sale_date,
                   option_type, strike, expiration, quantity, cost_basis,
                   proceeds, gain_loss, is_winner, strategy_id, account_type)
               VALUES (%s, %s, %s, %s, 100, %s, 1, 1000, %s, %s, %s, %s, %s)""",
            (symbol, acquired, sale, option_type, expiration,
             1000 + gain_loss, gain_loss, gain_loss > 0, sid, account))
        conn.commit()
    return sid


@pytest.fixture
def no_network(monkeypatch):
    def fake_fetch(symbol, period="1y"):
        return FAKE_SERIES[symbol]
    monkeypatch.setattr(calibrate, "fetch_series", fake_fetch)
    monkeypatch.delenv("TWELVE_DATA_KEY", raising=False)


def row_lines(output: str, symbol: str, acquired: date):
    prefix = f"{symbol:5} {acquired.isoformat()}"
    return [line for line in output.splitlines() if line.startswith(prefix)]


class TestNoSqlite:
    """The SQLite file (2.0/data/ivcrush.db) was deleted at the cutover; the
    DB constant that pointed at it was removed on 2026-09-13. Keep it out."""

    def test_calibrate_has_no_sqlite_path(self):
        lowered = CALIBRATE_SOURCE.lower()
        assert "sqlite" not in lowered
        assert "ivcrush.db" not in lowered
        assert ".db\"" not in lowered and ".db'" not in lowered
        assert not hasattr(calibrate, "DB")

    def test_trades_come_through_common_db(self):
        assert calibrate.connect.__module__ == "common.db"


pg = pytest.mark.pg


@pg
class TestLoadTrades:
    def test_reads_only_the_journal(self, book):
        """No hand-typed trades are appended: the three that used to be were
        journaled in Jul 2026, and a string key never matched their date rows,
        so they were counted twice."""
        add_trade("SPX", date(2026, 6, 5), date(2026, 6, 15),
                  date(2027, 5, 21), 28541.78, "CALL")
        trades = calibrate.load_trades()
        assert len(trades) == 1
        assert not hasattr(calibrate, "EXTRA_TRADES")

    def test_rows_carry_dates_and_the_journal_side(self, book):
        add_trade("SPY", date(2025, 2, 18), date(2025, 2, 20),
                  date(2025, 8, 15), 16494.82, "PUT")
        (t,) = calibrate.load_trades()
        assert t["acquired_date"] == date(2025, 2, 18)
        assert t["direction"] == "PUT"

    def test_filters_to_long_dated_index_options(self, book):
        add_trade("SPY", date(2025, 3, 4), date(2025, 5, 13),
                  date(2026, 1, 16), -2265.76, "CALL")            # 318 DTE
        add_trade("SPY", date(2024, 12, 17), date(2024, 12, 31),
                  date(2024, 12, 31), 194.87, "CALL")             # 14 DTE
        add_trade("AAPL", date(2025, 3, 4), date(2025, 5, 13),
                  date(2026, 1, 16), 500.0, "CALL")               # not an index
        add_trade("SPY", date(2025, 6, 2), date(2025, 6, 3),
                  date(2025, 8, 31), 10.0, "CALL")                # exactly 90
        got = [(t["symbol"], t["acquired_date"]) for t in calibrate.load_trades()]
        assert got == [("SPY", date(2025, 3, 4)), ("SPY", date(2025, 6, 2))]

    def test_one_trade_in_both_accounts_is_one_entry(self, book):
        """SPXW 2026-03-13 (strategies 301/333) is one call placed in TAXABLE and
        the IRA. Read per account it was two in-sample winners."""
        add_trade("SPXW", date(2026, 3, 13), date(2026, 4, 8), date(2026, 9, 30),
                  584.96, "CALL", account="TAXABLE")
        add_trade("SPXW", date(2026, 3, 13), date(2026, 4, 8), date(2026, 9, 30),
                  584.96, "CALL", account="IRA")
        (t,) = calibrate.load_trades()
        assert t["gain_loss"] == pytest.approx(1169.92)
        assert (t["direction"], t["is_winner"]) == ("CALL", True)

    def test_mixed_or_legless_positions_have_no_single_side(self, book):
        mixed = add_trade("SPX", date(2025, 8, 1), date(2025, 8, 12),
                          date(2026, 12, 18), 30917.40, "CALL")
        with connect() as conn:
            conn.execute(
                """INSERT INTO trade_journal (symbol, acquired_date, sale_date,
                       option_type, strike, expiration, quantity, cost_basis,
                       proceeds, gain_loss, is_winner, strategy_id)
                   VALUES ('SPX', %s, %s, 'PUT', 100, %s, 1, 1000, 1000, 0,
                           false, %s)""",
                (date(2025, 8, 1), date(2025, 8, 12), date(2026, 12, 18), mixed))
            conn.execute(
                """INSERT INTO strategies (symbol, strategy_type, acquired_date,
                       sale_date, expiration, quantity, gain_loss, is_winner)
                   VALUES ('SPY', 'SINGLE', %s, %s, %s, 1, 10, true)""",
                (date(2025, 9, 5), date(2025, 10, 1), date(2026, 1, 16)))
            conn.commit()
        sides = {t["symbol"]: t["direction"] for t in calibrate.load_trades()}
        assert sides == {"SPX": "CALL/PUT", "SPY": None}


class TestEventLabel:
    def test_date_typed_acquired_date_is_labelled(self):
        assert calibrate.event_label("SPY", date(2025, 3, 4)) == EventType.TARIFF_TACO

    def test_iso_string_still_labels(self):
        assert calibrate.event_label("SPX", "2026-02-23") == EventType.GEOPOLITICAL

    def test_unlabelled_entry_is_unknown(self):
        assert calibrate.event_label("SPY", date(2026, 7, 29)) == EventType.UNKNOWN

    def test_labels_are_keyed_by_date(self):
        for symbol, acquired in calibrate.HISTORICAL_EVENT_LABELS:
            assert type(acquired) is date, (symbol, acquired)


@pg
class TestMain:
    def test_date_row_prints_its_label_not_unknown(self, book, no_network, capsys):
        add_trade("SPY", date(2025, 3, 4), date(2025, 5, 13),
                  date(2026, 1, 16), -2265.76, "CALL")
        assert calibrate.main() == 0
        out = capsys.readouterr().out
        lines = row_lines(out, "SPY", date(2025, 3, 4))
        assert len(lines) == 1, out
        assert "TARIFF_TACO" in lines[0]
        assert "UNKNOWN" not in lines[0]

    def test_put_leg_is_excluded_from_call_fitting(self, book, no_network, capsys):
        """SPY 2025-02-18 was labelled CALL by hand; the journal says PUT."""
        add_trade("SPY", date(2025, 2, 18), date(2025, 2, 20),
                  date(2025, 8, 15), 16494.82, "PUT")
        assert calibrate.main() == 0
        out = capsys.readouterr().out
        (line,) = row_lines(out, "SPY", date(2025, 2, 18))
        assert "PUT-EX" in line
        assert "WINNERS (n=" not in out

    def test_entries_after_the_calibration_are_reported_out_of_sample(
            self, book, no_network, capsys):
        add_trade("SPY", date(2025, 3, 11), date(2025, 3, 14),
                  date(2026, 1, 16), 1653.52, "CALL")
        later = calibrate.CALIBRATED_THROUGH + timedelta(days=7)
        add_trade("SPX", later, later + timedelta(days=1),
                  later + timedelta(days=300), 33791.86, "CALL")
        assert calibrate.main() == 0
        out = capsys.readouterr().out
        assert "WINNERS (n=1)" in out                  # the in-sample call only
        head, sep, tail = out.partition("OUT-OF-SAMPLE")
        assert sep, out
        assert row_lines(tail, "SPX", later), out
        assert not row_lines(head, "SPX", later), out
