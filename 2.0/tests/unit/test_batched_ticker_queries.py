"""Four per-ticker query loops became one query each; the output did not change.

Audit fix plan 5.7. Each loop sent one statement per ticker (or per row) inside
a Python loop: live TRR (compute_live_trr), /harvest's database inputs
(harvest_live), the whisper/digest check for tickers with no future earnings
row (earnings_fetcher), and the HV20 repair (track_iv_weekly). Each test keeps
the old per-ticker statement as the reference and checks the batched version
returns the same thing on tables built from the real baseline, including a
ticker with no rows at all.
"""

from datetime import date, timedelta

import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402

pytestmark = pytest.mark.pg

TODAY = date.today()


@pytest.fixture
def tables(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("historical_moves", "earnings_calendar",
                                  "position_limits", "iv_history"))
        conn.commit()
    return pg_schema


def _move(conn, ticker, earnings_date, gap_pct):
    conn.execute(
        "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
        " earnings_high, earnings_low, earnings_close) VALUES (%s, %s, 100, %s, %s, %s, 100)",
        (ticker, earnings_date, 100 + gap_pct, 100 + abs(gap_pct) + 1, 100 - abs(gap_pct) - 1),
    )


def _seed_moves(conn):
    """AAPL 14 quarters (the window keeps 12), MSFT 3 (unknown TRR), NVDA 12 with a tail."""
    for q in range(14):
        _move(conn, "AAPL", date(2023, 1, 15) + timedelta(days=91 * q), (-1) ** q * (2.0 + q * 0.3))
    for q in range(3):
        _move(conn, "MSFT", date(2025, 1, 20) + timedelta(days=91 * q), 3.0 + q)
    for q in range(12):
        _move(conn, "NVDA", date(2023, 5, 1) + timedelta(days=91 * q), 12.0 if q == 11 else -2.5)


# --- compute_live_trr ----------------------------------------------------------

def _trr_per_ticker(conn, ticker, limit=12):
    """The statement compute_trr ran once per ticker until 2026-09-14."""
    rows = conn.execute(
        """
        SELECT gap_move_pct FROM (
            SELECT gap_move_pct, earnings_date
            FROM historical_moves
            WHERE ticker = %s AND gap_move_pct IS NOT NULL
            ORDER BY earnings_date DESC
            LIMIT %s
        )
        """,
        (ticker, limit),
    ).fetchall()
    return sorted(abs(r[0]) for r in rows)


def test_live_trr_batched_matches_per_ticker(tables, monkeypatch):
    import scripts.compute_live_trr as trr

    with connect() as conn:
        _seed_moves(conn)
        conn.commit()
        expected = {t: _trr_per_ticker(conn, t) for t in ("AAPL", "MSFT", "NVDA", "NOPE")}

    statements = []
    real_connect = trr.connect

    def counting_connect():
        conn = real_connect()
        execute = conn.execute
        conn.execute = lambda *a, **kw: statements.append(a[0]) or execute(*a, **kw)
        return conn

    monkeypatch.setattr(trr, "connect", counting_connect)
    result = trr.compute_trr(["AAPL", "MSFT", "NVDA", "NOPE"])

    assert len(statements) == 1
    from src.domain.tail_risk import tail_risk_ratio, trr_level
    for ticker, moves in expected.items():
        ratio = tail_risk_ratio(moves)
        assert result[ticker] == {
            "quarters": len(moves),
            "max_move": max(moves) if ratio is not None else None,
            "avg_move": sum(moves) / len(moves) if ratio is not None else None,
            "trr": ratio,
            "trr_level": trr_level(ratio),
        }, ticker
    assert result["AAPL"]["quarters"] == 12 and result["NOPE"]["quarters"] == 0
    assert result["NVDA"]["trr_level"] == "HIGH" and result["MSFT"]["trr_level"] is None


# --- harvest_live -----------------------------------------------------------------

def _harvest_per_ticker(conn, ticker):
    """The three statements get_live_harvest_candidates ran per ticker until 2026-09-14."""
    gap_moves = [row[0] for row in conn.execute(
        "SELECT gap_move_pct FROM historical_moves "
        "WHERE ticker = %s AND gap_move_pct IS NOT NULL "
        "ORDER BY earnings_date DESC LIMIT 12", (ticker,))]
    ern_row = conn.execute(
        "SELECT MIN(earnings_date) FROM earnings_calendar "
        "WHERE ticker = %s AND earnings_date >= CURRENT_DATE",
        (ticker,)).fetchone()
    mc_row = conn.execute(
        "SELECT max_contracts FROM position_limits WHERE ticker = %s",
        (ticker,)).fetchone()
    return gap_moves, (ern_row[0] if ern_row else None), (mc_row[0] if mc_row else None)


def test_harvest_database_inputs_batched_match_per_ticker(tables):
    from scripts.scan import harvest_live

    with connect() as conn:
        _seed_moves(conn)
        for ticker, days in (("AAPL", -3), ("AAPL", 40), ("AAPL", 130), ("MSFT", -10), ("NVDA", 0)):
            conn.execute(
                "INSERT INTO earnings_calendar (ticker, earnings_date, timing) VALUES (%s, %s, 'AMC')",
                (ticker, TODAY + timedelta(days=days)))
        conn.execute("INSERT INTO position_limits (ticker, max_contracts) VALUES ('AAPL', 50), ('NVDA', NULL)")
        conn.commit()

        universe = ["SPY", "AAPL", "MSFT", "NVDA"]
        expected = {t: _harvest_per_ticker(conn, t) for t in universe}
        got = harvest_live._database_inputs(conn, universe)

    assert got == expected
    assert got["SPY"] == ([], None, None)
    assert got["AAPL"][1] == TODAY + timedelta(days=40) and len(got["AAPL"][0]) == 12
    assert got["NVDA"][1] == TODAY


def test_harvest_fetch_holds_no_connection_across_vendor_calls(monkeypatch):
    """The database inputs are read before the Tradier and Yahoo calls, and the
    connection is closed before the first of them."""
    from scripts.scan import harvest_live

    events = []

    class Conn:
        def execute(self, *args):
            events.append("query")
            return []

        def close(self):
            events.append("close")

    monkeypatch.setattr(harvest_live, "connect", Conn)
    monkeypatch.setattr(harvest_live, "_fetch_closes_yf",
                        lambda symbol, period: events.append("yahoo") or [])
    monkeypatch.setattr(harvest_live, "_fetch_live_iv30",
                        lambda api, ticker, today: events.append("tradier"))

    class Container:
        cached_options_provider = object()

    harvest_live.get_live_harvest_candidates(Container())

    assert events.count("query") == 3
    assert events.index("close") < events.index("tradier")


# --- earnings_fetcher -------------------------------------------------------------

def test_tickers_without_a_future_earnings_row_batched_match_per_ticker(tables):
    from scripts.scan import earnings_fetcher

    with connect() as conn:
        for ticker, days in (("AAPL", 10), ("AAPL", 100), ("MSFT", -1), ("NVDA", 0)):
            conn.execute(
                "INSERT INTO earnings_calendar (ticker, earnings_date, timing) VALUES (%s, %s, 'BMO')",
                (ticker, TODAY + timedelta(days=days)))
        conn.commit()

        tickers = ["NVDA", "NOPE", "AAPL", "MSFT"]
        expected = [
            t for t in tickers
            if conn.execute(
                "SELECT COUNT(*) FROM earnings_calendar WHERE ticker = %s AND earnings_date >= CURRENT_DATE",
                (t,)).fetchone()[0] == 0
        ]
        got = earnings_fetcher._tickers_without_future_earnings(conn, tickers)

    assert got == expected == ["NOPE", "MSFT"]


# --- track_iv_weekly --------------------------------------------------------------

def test_hv20_repair_is_one_update_with_the_per_row_result(tables, monkeypatch):
    import scripts.track_iv_weekly as tiv
    from scripts.scan.harvest_live import compute_hv20

    start = date(2026, 6, 1)
    closes = [(start + timedelta(days=i), 100 * (1.01 if i % 2 else 0.99) ** (i % 7))
              for i in range(90)]
    snaps = [start + timedelta(days=d) for d in (10, 40, 60, 80)]   # the first has too few closes
    with connect() as conn:
        for ticker in ("SPY", "QQQ"):
            for d in snaps:
                conn.execute(
                    "INSERT INTO iv_history (ticker, snapshot_date, iv30d, hv20d) VALUES (%s, %s, 20, 22.0)",
                    (ticker, d))
        conn.commit()

    monkeypatch.setattr(tiv, "_dated_closes",
                        lambda ticker, s, e: closes if ticker == "SPY" else [])

    def expected_hv(d):
        hv = compute_hv20([c for day, c in closes if day <= d])
        return round(hv, 2) if hv is not None else None

    with connect() as conn:
        statements = []
        execute = conn.execute
        conn.execute = lambda *a, **kw: statements.append(a[0]) or execute(*a, **kw)

        assert tiv.recompute_hv20(conn, ["SPY", "QQQ", "IWM"], dry_run=True) == 4
        assert not any(s.lstrip().startswith("UPDATE") for s in statements)
        assert tiv.recompute_hv20(conn, ["SPY", "QQQ", "IWM"], dry_run=False) == 4
        assert sum(s.lstrip().startswith("UPDATE") for s in statements) == 1

        rows = {(r.ticker, r.snapshot_date): r.hv20d
                for r in execute("SELECT ticker, snapshot_date, hv20d FROM iv_history")}

    assert expected_hv(snaps[0]) is None
    assert {d: rows[("SPY", d)] for d in snaps} == {d: expected_hv(d) for d in snaps}
    assert {d: rows[("QQQ", d)] for d in snaps} == dict.fromkeys(snaps, 22.0), "no closes: unchanged"
    with connect() as conn:
        assert tiv.recompute_hv20(conn, ["SPY"], dry_run=True) == 0, "a repaired row stays repaired"
