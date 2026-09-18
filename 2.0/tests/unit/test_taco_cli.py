"""scripts/taco/cli.py: record, positions and close.

The DTE floor lives in store.require_min_dte; the CLI calls the same rule
before it spends a market-data download, rather than keeping its own copy.

The lifecycle below runs the three subcommands against a real schema. Rows come
back from Postgres with `date` objects and numerics, not the strings the CLI
was first written against -- `positions` formatting a row it read back is where
that shows (strptime on a date raised TypeError until _parse_date took both).
"""

from datetime import date, timedelta

import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402
from scripts.taco import cli  # noqa: E402

RECORD = ["record", "--direction", "call", "--symbol", "SPX", "--contracts", "5",
          "--strike", "7800", "--premium", "150000", "--entry-date", "2026-07-14",
          "--event-date", "2026-07-10"]


def test_short_dated_record_is_refused_before_any_fetch(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("reached market data or the database for a trade it must refuse")

    monkeypatch.setattr(cli, "fetch_series", boom)
    monkeypatch.setattr(cli, "record_position", boom)
    assert cli.main(RECORD + ["--expiration", "2026-08-21"]) == 1
    out = capsys.readouterr().out
    assert "DTE 38" in out
    assert "minimum 90" in out


def _closes(start, values):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


# SPX held 7,500 into the 2026-07-10 event, fell to a 7,200 panic low on 07-12,
# and has retraced to 7,300 since the 07-14 entry: a third of the 300-point dip,
# short of the 50% target. VIX sat at 15 before the event and 25 after it, so the
# VIX-normal exits cannot fire.
SPX = (_closes(date(2026, 6, 1), [7500.0] * 39)
       + _closes(date(2026, 7, 10), [7300.0, 7250.0, 7200.0, 7220.0, 7250.0, 7280.0, 7300.0]))
VIX = _closes(date(2026, 6, 1), [15.0] * 39) + _closes(date(2026, 7, 10), [25.0] * 7)


@pytest.fixture
def taco_db(pg_schema, monkeypatch):
    with connect() as conn:
        conn.execute(baseline_ddl("taco_log", "taco_positions"))
        conn.commit()
    series = {cli.SPX_SYMBOL: SPX, cli.VIX_SYMBOL: VIX}
    monkeypatch.setattr(cli, "fetch_series", lambda symbol, period="1y": series[symbol])
    monkeypatch.setattr(cli, "get_market_status", lambda: (False, "Weekend"))
    return pg_schema


@pytest.mark.pg
def test_record_positions_close_lifecycle(taco_db, capsys):
    assert cli.main(RECORD + ["--expiration", "2026-12-18"]) == 0
    out = capsys.readouterr().out
    assert "refs frozen — pre_event_high 7,500, panic_low 7,200, pre_event_vix 15.0" in out
    with connect() as conn:
        (pid,) = conn.execute("SELECT id FROM taco_positions").fetchone()

    assert cli.main(["positions"]) == 0
    out = capsys.readouterr().out
    assert "[data: prior close (Weekend)]" in out
    assert f"[{pid}] CALL SPX 5x 7800 exp 2026-12-18 ($150,000)" in out
    assert "HOLD: retrace 33% of target 50%" in out
    with connect() as conn:
        logged = conn.execute(
            "SELECT mode, direction, spot, event_date, recommendation FROM taco_log").fetchall()
    assert [tuple(r) for r in logged] == [
        ("positions", "CALL", 7300.0, date(2026, 7, 10),
         f"pos {pid} SPX HOLD: retrace 33% of target 50%")]

    assert cli.main(["close", "--id", str(pid), "--proceeds", "180000",
                     "--exit-date", "2026-09-11"]) == 0
    assert f"Closed position {pid}: P&L $30,000" in capsys.readouterr().out
    with connect() as conn:
        row = conn.execute(
            "SELECT status, exit_date, proceeds, outcome_pnl FROM taco_positions").fetchone()
    assert tuple(row) == ("CLOSED", date(2026, 9, 11), 180000.0, 30000.0)

    assert cli.main(["positions"]) == 0
    assert "No open TACO positions." in capsys.readouterr().out


@pytest.mark.pg
def test_closing_a_position_twice_is_an_error_not_a_second_pnl(taco_db, capsys):
    assert cli.main(RECORD + ["--expiration", "2026-12-18"]) == 0
    with connect() as conn:
        (pid,) = conn.execute("SELECT id FROM taco_positions").fetchone()
    close = ["close", "--id", str(pid), "--proceeds", "90000", "--exit-date", "2026-09-11"]
    assert cli.main(close) == 0
    assert cli.main(close) == 1
    assert f"ERROR: no OPEN taco position with id {pid}" in capsys.readouterr().out
    with connect() as conn:
        assert conn.execute("SELECT outcome_pnl FROM taco_positions").fetchone()[0] == -60000.0


@pytest.mark.parametrize("argv", [
    RECORD + ["--expiration", "2026-12-18", "--contracts", "5.5"],   # the column is an integer
    ["record", "--direction", "call", "--symbol", "QQQ", "--contracts", "1", "--strike", "1",
     "--expiration", "2026-12-18", "--premium", "1", "--entry-date", "2026-07-14",
     "--event-date", "2026-07-10"],                                 # SPX or SPY only
    ["check", "--direction", "call", "--event-type", "NOT_A_TYPE", "--rationale", "x"],
    ["close", "--id", "1"],                                         # proceeds required
])
def test_invalid_arguments_exit_before_any_fetch_or_write(monkeypatch, argv):
    def boom(*a, **k):
        raise AssertionError("reached market data or the database")

    for name in ("fetch_series", "record_position", "close_position", "log_run"):
        monkeypatch.setattr(cli, name, boom)
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
