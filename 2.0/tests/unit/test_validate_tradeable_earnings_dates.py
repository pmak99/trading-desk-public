"""validate_tradeable_earnings_dates skips tickers fetch_earnings_for_ticker
validated in the last five minutes.

Until 2026-09-14 the skip never happened. Its query mixed `%s` placeholders
with a SQLite-era `?`, so psycopg refused it (N placeholders, N+1 parameters),
the `except Exception` turned that into "nothing recently validated", and every
tradeable ticker went back to Yahoo a second time. Behind that sat a second
defect: the cutoff was a naive `datetime.now()` string, which Postgres reads in
the session TimeZone (UTC), so on an ET-local Mac "five minutes" meant four
hours and five minutes. Neither surfaced: a failed lookup is now a warning.
"""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl
from common.timezone import now_et
from scripts.scan import earnings_fetcher
from src.domain.errors import AppError, Err, ErrorCode

pytestmark = pytest.mark.pg


def _calendar(rows):
    with connect() as conn:
        conn.execute(baseline_ddl("earnings_calendar"))
        for ticker, validated_ago in rows:
            conn.execute(
                "INSERT INTO earnings_calendar (ticker, earnings_date, timing, last_validated_at)"
                " VALUES (%s, %s, 'AMC', %s)",
                (ticker, now_et().date() + timedelta(days=3),
                 datetime.now(timezone.utc) - validated_ago),
            )
        conn.commit()


def _validated(tickers):
    validator = MagicMock()
    validator.validate_earnings_date.return_value = Err(AppError(ErrorCode.NODATA, "stub"))
    with patch.object(earnings_fetcher, "EarningsDateValidator", return_value=validator), \
         patch.object(earnings_fetcher, "YahooFinanceEarnings"):
        earnings_fetcher.validate_tradeable_earnings_dates(
            [{"ticker": t} for t in tickers], container=None)
    return [c.args[0] for c in validator.validate_earnings_date.call_args_list]


def test_a_ticker_validated_a_minute_ago_is_not_validated_again(pg_schema):
    _calendar([("AAPL", timedelta(minutes=1)), ("MSFT", timedelta(hours=2))])
    assert _validated(["AAPL", "MSFT"]) == ["MSFT"]


def test_every_ticker_validated_recently_skips_the_pass(pg_schema):
    _calendar([("AAPL", timedelta(minutes=1)), ("MSFT", timedelta(minutes=2))])
    assert _validated(["AAPL", "MSFT"]) == []


def test_a_failed_lookup_is_a_warning_and_validates_everything(monkeypatch, caplog):
    def refuse():
        raise RuntimeError("database down")
    monkeypatch.setattr(earnings_fetcher, "connect", refuse)
    with caplog.at_level(logging.WARNING):
        assert sorted(_validated(["AAPL", "MSFT"])) == ["AAPL", "MSFT"]
    hits = [r for r in caplog.records if "recently validated" in r.getMessage().lower()]
    assert hits and hits[0].levelno == logging.WARNING and hits[0].exc_info is not None
