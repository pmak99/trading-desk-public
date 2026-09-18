"""scripts/journal/vrp_correlation.py against real Postgres.

From the 2026-09-12 cutover until this test existed the module could not match a
single trade: it returned early unless `2.0/data/ivcrush.db` existed, and that
file was deleted at the cutover. Every import since then wrote NULL
earnings_date and actual_move -- 768 of 1,121 option rows by 2026-09-13.

A match that did get through would then have crashed the parser's JSON export:
Postgres returns a `date`, and `Trade.earnings_date` is a string.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from journal.models import Trade  # noqa: E402

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402

pytestmark = pytest.mark.pg


@pytest.fixture
def moves(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("historical_moves"))
        # NVDA reported 2026-08-26: gap +2%, intraday -5%.
        conn.execute(
            "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
            " earnings_high, earnings_low, earnings_close)"
            " VALUES ('NVDA', '2026-08-26', 100, 102, 103, 96, 96.9)")
        conn.commit()
    return pg_schema


def _option(symbol, acquired, sale):
    return Trade(symbol=symbol, description="", quantity=50, acquired_date=acquired,
                 sale_date=sale, cost_basis=0.0, proceeds=100.0, gain_loss=100.0,
                 term="SHORT", option_type="PUT", strike=170.0, expiration="2026-08-28")


def test_a_trade_bracketing_earnings_acquires_its_event(moves):
    from journal.vrp_correlation import correlate_with_vrp

    # Credit trade: Fidelity inverts the dates (acquired = close).
    trade = _option("NVDA", acquired="2026-08-27", sale="2026-08-25")
    trades, matched = correlate_with_vrp([trade])

    assert matched == 1
    assert trades[0].earnings_date == "2026-08-26"
    assert trades[0].actual_move == pytest.approx(5.0)
    json.dumps({"earnings_date": trades[0].earnings_date})   # the parser's export


def test_a_trade_with_no_event_stays_unmatched(moves):
    from journal.vrp_correlation import correlate_with_vrp

    trades, matched = correlate_with_vrp([_option("AAPL", "2026-08-25", "2026-08-27")])
    assert matched == 0
    assert trades[0].earnings_date is None


def test_a_position_held_well_past_the_report_still_acquires_its_event(moves):
    """Opened before the report and held through it: an earnings trade, however
    long it stayed open. Until 2026-09-13 the matcher also required a close within
    7 days of the report, which left four such positions undated (MNDY, UPWK,
    INTC, CRWV in Jan-Mar 2026, a combined loss) and so kept them out of every
    held-2+-days figure CLAUDE.md quotes."""
    from journal.vrp_correlation import correlate_with_vrp

    trades, matched = correlate_with_vrp([_option("NVDA", "2026-08-24", "2026-09-08")])
    assert matched == 1
    assert trades[0].earnings_date == "2026-08-26"


def test_a_position_opened_more_than_a_week_before_is_not_an_earnings_trade(moves):
    from journal.vrp_correlation import correlate_with_vrp

    _, matched = correlate_with_vrp([_option("NVDA", "2026-08-18", "2026-08-27")])
    assert matched == 0


def test_a_position_opened_after_the_report_is_not_matched_to_it(moves):
    from journal.vrp_correlation import correlate_with_vrp

    _, matched = correlate_with_vrp([_option("NVDA", "2026-08-27", "2026-08-28")])
    assert matched == 0
