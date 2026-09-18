"""ab_evaluate_live.py's SQL and report against a real Postgres.

Tables come from the baseline schema, so the loader is tested against the
columns production has. The pure selection rules are in test_ab_evaluate_live.py.
"""

import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402

pytestmark = pytest.mark.pg

ET = ZoneInfo("America/New_York")


def _log(conn, ticker, logged, ts, impl, vi, vc, slope=None):
    conn.execute(
        "INSERT INTO analysis_log (timestamp, ticker, earnings_date, expiration,"
        " implied_move_pct, historical_intraday_mean_pct, vrp_intraday_ratio, recommendation,"
        " vrp_close_ratio, term_slope_ratio, vrp_convention)"
        " VALUES (%s, %s, %s, %s, %s, 1.0, %s, 'good', %s, %s, 'intraday')",
        (ts, ticker, logged, logged, impl, vi, vc, slope))


def _move(conn, ticker, day, close_pct, intraday_pct=2.0, timing="BMO"):
    # prev_close 100 -> close = 100 + close_pct; open chosen so (close-open)/open = intraday.
    close = 100.0 + close_pct
    open_ = close / (1 + intraday_pct / 100)
    conn.execute(
        "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
        " earnings_high, earnings_low, earnings_close) VALUES (%s, %s, 100, %s, %s, %s, %s)",
        (ticker, day, open_, max(open_, close) + 1, min(open_, close) - 1, close))
    if timing is not None:
        conn.execute(
            "INSERT INTO earnings_calendar (ticker, earnings_date, timing) VALUES (%s, %s, %s)"
            " ON CONFLICT DO NOTHING", (ticker, day, timing))


@pytest.fixture
def book(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("analysis_log", "historical_moves", "earnings_calendar"))
        # Four prior quarters per ticker for the point-in-time diagnostic.
        tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GOOG", "GOOGL"]
        for t in tickers:
            for day in ("2025-07-28", "2025-10-28", "2026-01-28", "2026-04-28"):
                _move(conn, t, day, 3.0, intraday_pct=2.0)
        for i, t in enumerate(tickers):
            _move(conn, t, "2026-07-28", close_pct=4.0 + i, timing="AMC")

        # AAA: pre-open first log crushes; the post-open latest log would MISS.
        _log(conn, "AAA", "2026-07-28", datetime(2026, 7, 20, 11, 0, tzinfo=ET), 9.0, 4.5, 2.0)
        _log(conn, "AAA", "2026-07-28", datetime(2026, 7, 29, 11, 0, tzinfo=ET), 2.0, 1.0, 0.5)
        # BBB: logged under a stale date and again under the real one.
        _log(conn, "BBB", "2026-08-05", datetime(2026, 7, 21, 11, 0, tzinfo=ET), 8.0, 4.0, 1.8)
        _log(conn, "BBB", "2026-07-28", datetime(2026, 7, 27, 11, 0, tzinfo=ET), 8.5, 4.2, 1.9)
        # CCC: only a post-open log.
        _log(conn, "CCC", "2026-07-28", datetime(2026, 7, 29, 12, 0, tzinfo=ET), 3.0, 1.5, 0.7)
        # GOOG/GOOGL: one issuer.
        _log(conn, "GOOGL", "2026-07-28", datetime(2026, 7, 22, 11, 0, tzinfo=ET), 7.0, 3.5, 1.6)
        _log(conn, "GOOG", "2026-07-28", datetime(2026, 7, 22, 12, 0, tzinfo=ET), 7.1, 3.6, 1.6)
        for t, impl in (("DDD", 12.0), ("EEE", 5.0), ("FFF", 11.0)):
            _log(conn, t, "2026-07-28", datetime(2026, 7, 27, 15, 0, tzinfo=ET), impl, impl / 2, impl / 4)
        # Excluded by the loader: no close VRP, and before the sample start.
        conn.execute(
            "INSERT INTO analysis_log (timestamp, ticker, earnings_date, expiration,"
            " implied_move_pct, historical_intraday_mean_pct, vrp_intraday_ratio,"
            " recommendation, vrp_convention)"
            " VALUES ('2026-07-20 11:00-04', 'EEE', '2026-07-28', '2026-07-31', 1, 1, 1, 'skip', 'intraday')")
        _log(conn, "EEE", "2026-07-28", datetime(2026, 6, 1, 11, 0, tzinfo=ET), 1.0, 0.5, 0.2)
        # A pending event.
        _log(conn, "AAA", "2026-10-28", datetime(2026, 9, 10, 11, 0, tzinfo=ET), 6.0, 3.0, 1.5)
        conn.commit()


def test_loader_reads_timing_and_filters_the_sample(book):
    from scripts.ab_evaluate_live import load_rows

    with connect() as conn:
        analyses, reactions = load_rows(conn)
    assert len(analyses) == 11                       # the two excluded rows are gone
    assert all(a["timestamp"].tzinfo is not None for a in analyses)
    assert all(isinstance(a["expiration"], date) for a in analyses)
    assert {a["analysis_timing"] for a in analyses if a["earnings_date"] == date(2026, 7, 28)} == {"AMC"}
    assert sorted(reactions) == ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GOOG", "GOOGL"]
    aaa = {r["earnings_date"]: r for r in reactions["AAA"]}
    assert aaa[date(2026, 7, 28)]["timing"] == "AMC"
    assert aaa[date(2026, 7, 28)]["cm"] == pytest.approx(4.0)
    assert aaa[date(2025, 7, 28)]["im"] == pytest.approx(2.0)


def test_load_selects_one_pre_open_row_per_issuer_reaction(book):
    from scripts.ab_evaluate_live import load

    samples = load()
    sel = samples["vrp"]
    assert samples["term"].graded == []                  # no fixture row logged a slope
    graded = {e["ticker"]: e for e in sel.graded}
    assert sorted(graded) == ["AAA", "BBB", "DDD", "EEE", "FFF", "GOOGL"]
    assert graded["AAA"]["impl"] == 9.0 and graded["AAA"]["crush"] == 1
    assert graded["BBB"]["logged_date"] == date(2026, 8, 5)
    assert sel.stats["stale_date_duplicates"] == 1
    assert sel.stats["share_class_duplicates"] == 1
    assert sel.stats["no_pre_open"] == 1
    assert sel.stats["post_open_rows"] == 2
    assert sel.stats["pending"] == 1


def test_report_labels_primary_secondary_and_holm(book, capsys, monkeypatch):
    from scripts import ab_evaluate_live as ev

    monkeypatch.setattr(sys, "argv", ["ab_evaluate_live.py", "--full"])
    assert ev.main() == 0
    out = capsys.readouterr().out
    assert "PRIMARY — VRP CONVENTION VERDICT" in out
    assert "SECONDARY — TERM-STRUCTURE VERDICT" in out
    assert "Holm" in out
    assert "duplicate reactions removed: 2" in out
    assert "post-open log rows excluded: 2" in out
    assert "no pre-open analysis: 1" in out
    assert "DIAGNOSTIC" in out
    assert "11s" not in out
    assert "2026-07-28" in out
