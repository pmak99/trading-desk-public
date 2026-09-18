"""EarningsRepository.get_timing against real Postgres.

`./trade.sh TICKER DATE` passes no timing, and the implied move is priced at a
timing-dependent expiration (metrics/implied_move_expiration.py), so /analyze
reads the stored tag for the event. Absent means UNKNOWN to the caller, which
prices the next day's expiration: never the report date's, which would miss an
AMC report entirely.
"""

from datetime import date

import pytest

from common.pgtesting import baseline_ddl  # noqa: E402
from src.domain.enums import EarningsTiming  # noqa: E402
from src.domain.errors import ErrorCode  # noqa: E402
from src.infrastructure.database.repositories.earnings_repository import (  # noqa: E402
    EarningsRepository,
)

pytestmark = pytest.mark.pg


@pytest.fixture
def repo(pg_pool):
    with pg_pool.connection() as conn:
        conn.execute(baseline_ddl("earnings_calendar"))
        conn.execute(
            "INSERT INTO earnings_calendar (ticker, earnings_date, timing) VALUES "
            "('JPM', '2026-10-16', 'BMO'), ('ORCL', '2026-12-10', 'AMC'), "
            "('JPM', '2027-01-15', 'UNKNOWN')"
        )
        conn.commit()
    return EarningsRepository(pool=pg_pool)


def test_reads_the_stored_tag_for_that_event(repo):
    assert repo.get_timing("JPM", date(2026, 10, 16)).value == EarningsTiming.BMO
    assert repo.get_timing("ORCL", date(2026, 12, 10)).value == EarningsTiming.AMC
    assert repo.get_timing("JPM", date(2027, 1, 15)).value == EarningsTiming.UNKNOWN


def test_another_date_for_the_ticker_is_not_this_event(repo):
    result = repo.get_timing("JPM", date(2026, 10, 15))
    assert result.is_err
    assert result.error.code == ErrorCode.NODATA
