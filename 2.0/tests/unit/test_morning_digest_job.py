"""Morning digest job.

The job's only real responsibility is to run the AUTHORITATIVE 2.0 engine and
render what it returns. The tests below therefore concentrate on the seams
where a divergence could sneak back in: the ticker universe must come from the
same scraper /whisper uses, the week must be the scraper's own Monday, and
quality scores must be precalculated before ranking.
"""

from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from scripts.jobs.morning_digest import (
    FIRST_DAY_LOOKING_AHEAD,
    _week_tickers,
    _weeks_to_try,
    build_digest_text,
    main,
)

MOD = "scripts.jobs.morning_digest"

ROW = {
    "ticker": "AAPL",
    "earnings_date": "2026-10-29",
    "vrp_ratio": 1.85,
    "implied_move_pct": "5.0",
    "historical_mean_pct": "2.7",
    "recommendation": "EXCELLENT",
    "liquidity_tier": "EXCELLENT",
    "is_tradeable": True,
    "status": "SUCCESS",
}


@pytest.fixture(autouse=True)
def _no_migrations_in_job_tests(monkeypatch):
    """The job migrates at start; a test must not migrate whatever PGDATABASE names
    (the session guard in production_guard.py refuses it against production)."""
    monkeypatch.setattr("scripts.jobs.morning_digest.apply_migrations", lambda: 0)


@pytest.fixture
def monday():
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(days=7)


def _run(rows):
    return Mock(results=rows, success_count=len(rows), error_count=0,
                skip_count=0, filtered_count=0, no_earnings_found=not rows)


def test_build_digest_text_uses_collected_results(monday):
    with patch(f"{MOD}.collect_whisper_results", return_value=_run([ROW])):
        text = build_digest_text(Mock(), ["AAPL"], monday)

    assert "AAPL" in text
    assert "1.85" in text


def test_build_digest_text_precalculates_quality_scores(monday):
    """Without this the formatter sorts every row at 0.0 and the ranking is
    arbitrary — the digest would look fine and be silently mis-ordered. MSFT
    (VRP 3.1) arrives second and must be listed first."""
    rows = [dict(ROW), dict(ROW, ticker="MSFT", vrp_ratio=3.1)]

    with patch(f"{MOD}.collect_whisper_results", return_value=_run(rows)):
        text = build_digest_text(Mock(), ["AAPL", "MSFT"], monday)

    assert all(r["_quality_score"] > 0 for r in rows)
    assert text.index("MSFT") < text.index("AAPL")


def test_build_digest_text_handles_empty_week(monday):
    with patch(f"{MOD}.collect_whisper_results", return_value=_run([])):
        text = build_digest_text(Mock(), [], monday)

    assert "No tradeable candidates" in text


def test_week_tickers_returns_the_monday_the_scraper_reports(monday):
    """The scraper is authoritative for which week the tickers belong to.

    We now ask for a SPECIFIC week (see the week-selection tests below), but the
    Monday we hand downstream is still the one the scraper came back with, not
    the one we requested — collect_whisper_results and the digest title must
    agree with the data, not with our intent.
    """
    scraper = Mock()
    scraper.get_most_anticipated_earnings.return_value = Mock(
        is_err=False, value=(["AAPL", "MSFT"], monday))

    with patch(f"{MOD}.EarningsWhisperScraper", return_value=scraper), \
         patch(f"{MOD}.get_shared_cache"), \
         patch(f"{MOD}.ensure_tickers_in_db") as ensure:
        tickers, got_monday = _week_tickers(Mock())

    assert tickers == ["AAPL", "MSFT"]
    assert got_monday == monday
    ensure.assert_called_once()


def test_week_tickers_raises_on_scraper_error():
    """is_err is a PROPERTY, not a method — `if result.is_err()` would be
    truthy always and the error branch would never fire."""
    scraper = Mock()
    scraper.get_most_anticipated_earnings.return_value = Mock(
        is_err=True, error="rate limited")

    with patch(f"{MOD}.EarningsWhisperScraper", return_value=scraper), \
         patch(f"{MOD}.get_shared_cache"), \
         patch(f"{MOD}.ensure_tickers_in_db"):
        with pytest.raises(RuntimeError, match="rate limited"):
            _week_tickers(Mock())


def test_dry_run_prints_and_does_not_send(capsys, monday):
    sender = Mock()

    with patch(f"{MOD}.collect_whisper_results", return_value=_run([ROW])), \
         patch(f"{MOD}.Container", return_value=Mock()), \
         patch(f"{MOD}._week_tickers", return_value=(["AAPL"], monday)), \
         patch(f"{MOD}.run_job", side_effect=lambda name, fn, **kw: (fn(), 0)[1]):
        rc = main(["--dry-run"], sender=sender)

    assert rc == 0
    sender.send.assert_not_called()
    assert "AAPL" in capsys.readouterr().out


def test_send_failure_is_raised_so_the_runner_records_an_error(monday):
    """A Telegram send that returns False must fail the job, not pass silently —
    otherwise a broken digest looks like a successful run forever."""
    sender = Mock()
    sender.send.return_value = False

    with patch(f"{MOD}.collect_whisper_results", return_value=_run([ROW])), \
         patch(f"{MOD}.Container", return_value=Mock()), \
         patch(f"{MOD}._week_tickers", return_value=(["AAPL"], monday)):
        with pytest.raises(RuntimeError, match="Telegram"):
            from scripts.jobs import morning_digest
            morning_digest._work_once(sender)


def test_digest_title_has_no_datetime_suffix(monday):
    """The scraper returns a datetime, not a date; a bare isoformat() rendered
    "week of 2026-09-14T00:00:00" in the first live dry-run."""
    as_datetime = datetime.combine(monday, datetime.min.time())

    with patch(f"{MOD}.collect_whisper_results", return_value=_run([ROW])):
        text = build_digest_text(Mock(), ["AAPL"], as_datetime)

    assert "T00:00:00" not in text
    assert f"week of {monday.isoformat()}" in text


# ---------------------------------------------------------------------------
# Added after code review.
# ---------------------------------------------------------------------------

def test_dry_run_does_not_consume_the_daily_claim(monday, tmp_path):
    """Previewing at 07:00 must not make the 07:30 timer a no-op.

    Routing --dry-run through run_job marked the day 'success', so the real
    trigger got LOCK_HELD and no digest was ever sent — while job_status read
    'success', hiding it completely.
    """
    with patch(f"{MOD}.collect_whisper_results", return_value=_run([ROW])), \
         patch(f"{MOD}.Container", return_value=Mock()), \
         patch(f"{MOD}._week_tickers", return_value=(["AAPL"], monday)), \
         patch(f"{MOD}.run_job") as run_job_mock:
        rc = main(["--dry-run"], sender=Mock())

    assert rc == 0
    run_job_mock.assert_not_called()


def test_week_tickers_raises_when_the_scraper_returns_no_tickers(monday):
    """An outage must look like a failure, not a quiet week.

    EarningsWhisperScraper can return Ok([]) — a DB fallback with no rows, or a
    cached empty list (`if cached_tickers is not None` accepts []). whisper_mode
    guards this with `if not tickers: return 1`; without the same guard the
    digest renders 'No tradeable candidates' and records success, so every
    morning of a silent outage looks legitimately quiet.
    """
    scraper = Mock()
    scraper.get_most_anticipated_earnings.return_value = Mock(
        is_err=False, value=([], monday))

    with patch(f"{MOD}.EarningsWhisperScraper", return_value=scraper), \
         patch(f"{MOD}.get_shared_cache"), \
         patch(f"{MOD}.ensure_tickers_in_db"):
        with pytest.raises(RuntimeError, match="no tickers"):
            _week_tickers(Mock())


def test_digest_title_has_no_parallel_run_prefix(monday):
    """5.0 was decommissioned 2026-09-12 (plan Task 13) -- there is no second
    engine posting to the same bot/chat anymore, so the [2.0] disambiguation
    prefix (removed the same day) must not reappear."""
    with patch(f"{MOD}.collect_whisper_results", return_value=_run([ROW])):
        text = build_digest_text(Mock(), ["AAPL"], monday)

    assert text.startswith("<b>Morning Digest")
    assert "[2.0]" not in text


# ---------------------------------------------------------------------------
# Week selection. The digest deliberately DIVERGES from /whisper here.
#
# /whisper is unconditional next-week-first every day of the week
# (earnings_whisper_scraper.get_most_anticipated_earnings: weeks_to_try =
# [next_monday, current_monday]). That suits a planning command you invoke on
# purpose, but made the 07:40 push useless: on Wed 2026-09-09 it showed one
# REJECT-liquidity name for the week of Sep 14 while ADBE at 4.9x VRP reported
# the next morning and never appeared.
#
# The digest now shows the CURRENT week Mon-Thu and NEXT week from Friday on,
# because by Friday the current week's earnings are done.
# ---------------------------------------------------------------------------

def _monday_of(d):
    return d - timedelta(days=d.weekday())


@pytest.mark.parametrize("weekday,name", [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu")])
def test_early_week_prefers_the_current_week(weekday, name):
    now = datetime(2026, 9, 7) + timedelta(days=weekday)  # w/c Mon 2026-09-07
    weeks = _weeks_to_try(now)
    assert weeks[0].date() == date(2026, 9, 7), f"{name} should lead with this week"
    assert weeks[1].date() == date(2026, 9, 14), "next week is the fallback"


@pytest.mark.parametrize("weekday,name", [(4, "Fri"), (5, "Sat"), (6, "Sun")])
def test_friday_onward_prefers_next_week(weekday, name):
    now = datetime(2026, 9, 7) + timedelta(days=weekday)
    weeks = _weeks_to_try(now)
    assert weeks[0].date() == date(2026, 9, 14), f"{name} should lead with next week"
    assert weeks[1].date() == date(2026, 9, 7), "this week is the fallback"


def test_thursday_evening_on_a_utc_host_still_leads_with_this_week(monkeypatch):
    """21:00 ET Thu 2026-09-10 is 01:00 Fri in UTC, the server's clock. Read from the
    host clock (until 2026-09-14), the bot's evening /whisper jumped to next week
    and dropped Friday's reports. The weekday is New York's."""
    from datetime import timezone

    import common.timezone as tz

    class UtcHost(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(tz, "datetime", UtcHost)
    assert [w.date() for w in _weeks_to_try()] == [date(2026, 9, 7), date(2026, 9, 14)]


def test_friday_boundary_constant_is_friday():
    assert FIRST_DAY_LOOKING_AHEAD == 4  # datetime.weekday(): Mon=0 .. Fri=4


def test_week_tickers_requests_an_explicit_week_not_none(monday):
    """/whisper passes week_monday=None and lets the scraper choose. The digest
    must choose for itself, or it inherits next-week-first."""
    scraper = Mock()
    scraper.get_most_anticipated_earnings.return_value = Mock(
        is_err=False, value=(["AAPL"], datetime(2026, 9, 7)))

    with patch(f"{MOD}.EarningsWhisperScraper", return_value=scraper), \
         patch(f"{MOD}.get_shared_cache"), \
         patch(f"{MOD}.ensure_tickers_in_db"):
        _week_tickers(Mock())

    sent = scraper.get_most_anticipated_earnings.call_args[1]["week_monday"]
    requested = datetime.strptime(sent, "%Y-%m-%d").date()
    this_monday = _monday_of(date.today())
    assert requested.weekday() == 0
    assert requested in (this_monday, this_monday + timedelta(days=7))


def _scraper_by_week(tickers_for):
    """A scraper that answers each requested week from `tickers_for(week_index)`,
    where 0 is the first week asked for -- so no test pins the clock."""
    asked = []

    def answer(week_monday):
        asked.append(week_monday)
        return Mock(is_err=False, value=(tickers_for(len(asked) - 1),
                                         datetime.strptime(week_monday, "%Y-%m-%d")))

    scraper = Mock()
    scraper.get_most_anticipated_earnings.side_effect = answer
    return scraper, asked


def test_week_tickers_falls_back_to_the_other_week_when_the_first_is_empty():
    """A genuinely quiet week must not error — it should show the other week
    rather than nothing."""
    scraper, asked = _scraper_by_week(lambda i: [] if i == 0 else ["ADBE"])

    with patch(f"{MOD}.EarningsWhisperScraper", return_value=scraper), \
         patch(f"{MOD}.get_shared_cache"), \
         patch(f"{MOD}.ensure_tickers_in_db"):
        tickers, monday = _week_tickers(Mock())

    first, second = (datetime.strptime(w, "%Y-%m-%d") for w in asked)
    assert abs((second - first).days) == 7
    assert tickers == ["ADBE"]
    assert monday == second


def test_week_tickers_raises_only_when_every_week_is_empty():
    """Both weeks empty is an outage signal, not a quiet fortnight."""
    scraper, asked = _scraper_by_week(lambda i: [])

    with patch(f"{MOD}.EarningsWhisperScraper", return_value=scraper), \
         patch(f"{MOD}.get_shared_cache"), \
         patch(f"{MOD}.ensure_tickers_in_db"):
        with pytest.raises(RuntimeError, match="no tickers"):
            _week_tickers(Mock())
    assert len(asked) == 2
