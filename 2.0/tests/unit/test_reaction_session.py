"""src/domain/reaction_session.py -- which session holds a report's reaction, and
when that session has closed.

The move writers read a reaction session's daily bar. A bar fetched before the
session closes is a partial one, and it becomes a stored move that is never
rewritten (record_moves inserts only; the backfill upserts, but only when rerun).
"""

from datetime import date, datetime

import pytest

from common.timezone import MARKET_TZ as ET
from src.domain.reaction_session import (
    is_reaction_complete,
    reaction_close,
    reaction_session,
)


def et(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


@pytest.mark.parametrize("report, timing, session", [
    (date(2026, 9, 10), "BMO", date(2026, 9, 10)),
    (date(2026, 9, 10), "AMC", date(2026, 9, 11)),
    (date(2026, 9, 11), "AMC", date(2026, 9, 14)),       # Friday -> Monday
    (date(2026, 9, 4), "AMC", date(2026, 9, 8)),         # Labor Day Monday closed
    (date(2026, 9, 10), "DMH", date(2026, 9, 11)),       # uncertain: the later session
    (date(2026, 9, 10), "UNKNOWN", date(2026, 9, 11)),
    (date(2026, 9, 10), None, date(2026, 9, 11)),
])
def test_the_reaction_session(report, timing, session):
    assert reaction_session(report, timing) == session


def test_a_regular_session_closes_at_1600_et():
    assert reaction_close(date(2026, 9, 10), "BMO") == et(2026, 9, 10, 16, 0)
    assert not is_reaction_complete(et(2026, 9, 10, 15, 59), date(2026, 9, 10), "BMO")
    assert is_reaction_complete(et(2026, 9, 10, 16, 0), date(2026, 9, 10), "BMO")


def test_an_early_close_ends_the_session_at_1300_et():
    """The day after Thanksgiving; an AMC report the Wednesday before reacts on it."""
    assert reaction_close(date(2026, 11, 25), "AMC") == et(2026, 11, 27, 13, 0)
    assert not is_reaction_complete(et(2026, 11, 27, 12, 59), date(2026, 11, 25), "AMC")
    assert is_reaction_complete(et(2026, 11, 27, 13, 1), date(2026, 11, 25), "AMC")


def test_a_utc_clock_is_read_in_new_york():
    """the server's clock is UTC: 20:30 UTC is 16:30 ET in September."""
    from datetime import timezone
    assert is_reaction_complete(datetime(2026, 9, 10, 20, 30, tzinfo=timezone.utc),
                                date(2026, 9, 10), "BMO")
