"""
Tests for 2.0 FinnhubAPI (sync earnings calendar client).

TDD: tests written before implementation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from src.domain.errors import ErrorCode
from src.domain.enums import EarningsTiming


@pytest.fixture
def client():
    from src.infrastructure.api.finnhub import FinnhubAPI
    return FinnhubAPI(api_key="test_key")


def _make_response(entries):
    """Build a mock requests.Response with JSON."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"earningsCalendar": entries}
    mock.raise_for_status = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# get_earnings_calendar
# ---------------------------------------------------------------------------

def test_get_earnings_calendar_returns_symbol_date_timing_tuples(client):
    """Should return list of (symbol, date, timing) tuples."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "AAPL", "date": tomorrow, "hour": "amc"}]

    with patch("requests.get", return_value=_make_response(payload)):
        result = client.get_earnings_calendar()

    assert result.is_ok
    data = result.value
    assert len(data) == 1
    ticker, earn_date, timing = data[0]
    assert ticker == "AAPL"
    assert earn_date == date.fromisoformat(tomorrow)
    assert timing == EarningsTiming.AMC


def test_get_earnings_calendar_maps_bmo_timing(client):
    """'bmo' hour should map to EarningsTiming.BMO."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "MSFT", "date": tomorrow, "hour": "bmo"}]

    with patch("requests.get", return_value=_make_response(payload)):
        result = client.get_earnings_calendar()

    assert result.is_ok
    _, _, timing = result.value[0]
    assert timing == EarningsTiming.BMO


def test_get_earnings_calendar_unknown_hour_maps_to_unknown(client):
    """Unknown or empty hour should map to EarningsTiming.UNKNOWN."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "GOOG", "date": tomorrow, "hour": ""}]

    with patch("requests.get", return_value=_make_response(payload)):
        result = client.get_earnings_calendar()

    assert result.is_ok
    _, _, timing = result.value[0]
    assert timing == EarningsTiming.UNKNOWN


def test_get_earnings_calendar_empty_response_returns_err(client):
    """Empty earningsCalendar list should return Err(NODATA)."""
    with patch("requests.get", return_value=_make_response([])):
        result = client.get_earnings_calendar()

    assert result.is_err
    assert result.error.code == ErrorCode.NODATA


def test_get_earnings_calendar_api_error_returns_err(client):
    """Network error should return Err(EXTERNAL)."""
    import requests as req
    with patch("requests.get", side_effect=req.exceptions.RequestException("timeout")):
        result = client.get_earnings_calendar()

    assert result.is_err
    assert result.error.code in (ErrorCode.EXTERNAL, ErrorCode.TIMEOUT)


def test_get_earnings_calendar_symbol_filter_sent_in_params(client):
    """symbol parameter should be forwarded as ?symbol= query param."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "NVDA", "date": tomorrow, "hour": "amc"}]
    mock_get = MagicMock(return_value=_make_response(payload))

    with patch("requests.get", mock_get):
        client.get_earnings_calendar(symbol="NVDA")

    call_kwargs = mock_get.call_args
    params = call_kwargs[1].get("params") or call_kwargs[0][1]
    assert params.get("symbol") == "NVDA"


def test_get_earnings_calendar_horizon_chunks_cover_full_range(client):
    """Bulk fetches must be chunked (Finnhub caps responses at 1,500 entries,
    keeping the LATEST dates): contiguous windows from today to ~90 days out,
    each no wider than the chunk size."""
    from src.infrastructure.api.finnhub import BULK_CHUNK_DAYS

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "TSLA", "date": tomorrow, "hour": "bmo"}]
    mock_get = MagicMock(return_value=_make_response(payload))

    with patch("requests.get", mock_get):
        result = client.get_earnings_calendar(horizon="3month")

    assert result.is_ok
    windows = []
    for call in mock_get.call_args_list:
        params = call[1].get("params") or call[0][1]
        windows.append((date.fromisoformat(params["from"]), date.fromisoformat(params["to"])))

    assert windows[0][0] == date.today()
    assert 85 <= (windows[-1][1] - date.today()).days <= 95
    for from_d, to_d in windows:
        assert (to_d - from_d).days < BULK_CHUNK_DAYS
    for (_, prev_to), (next_from, _) in zip(windows, windows[1:]):
        assert next_from == prev_to + timedelta(days=1)


def test_get_earnings_calendar_dedupes_across_chunks(client):
    """Identical (symbol, date) rows returned by multiple chunk requests must
    collapse to a single tuple."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "AAPL", "date": tomorrow, "hour": "amc"}]

    with patch("requests.get", return_value=_make_response(payload)):
        result = client.get_earnings_calendar(horizon="3month")

    assert result.is_ok
    assert len(result.value) == 1


def test_get_earnings_calendar_splits_chunk_at_bulk_cap(client):
    """A chunk that comes back at the 1,500-entry cap is truncated — the client
    must split the window and refetch so near-term entries are not lost."""
    from src.infrastructure.api.finnhub import FINNHUB_BULK_CAP

    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    def fake_get(url, params=None, timeout=None):
        from_d = date.fromisoformat(params["from"])
        to_d = date.fromisoformat(params["to"])
        if (to_d - from_d).days >= 1:
            # Multi-day window: simulate Finnhub's silent truncation
            capped = [
                {"symbol": f"T{i}", "date": to_d.isoformat(), "hour": "amc"}
                for i in range(FINNHUB_BULK_CAP)
            ]
            return _make_response(capped)
        return _make_response([{"symbol": "NFLX", "date": from_d.isoformat(), "hour": "amc"}])

    with patch("requests.get", side_effect=fake_get):
        result = client.get_earnings_calendar()  # default 30-day bulk

    assert result.is_ok
    tickers = {t for t, _, _ in result.value}
    # Every single-day window was reached, including day 0
    assert "NFLX" in tickers
    assert date.today() in {d for _, d, _t in result.value}


def test_get_earnings_calendar_from_date_overrides_today_default(client):
    """from_date/to_date must be used verbatim as the request window instead
    of the default today-forward range — needed so a caller can query a
    window that includes past dates (e.g. sync_earnings_calendar's
    _corroborate, which otherwise can never confirm an already-past
    disputed earnings date: the today-forward default would just skip
    past it to the next quarter)."""
    past_from = date.today() - timedelta(days=10)
    past_to = date.today() - timedelta(days=5)
    payload = [{"symbol": "AXON", "date": past_to.isoformat(), "hour": "bmo"}]
    mock_get = MagicMock(return_value=_make_response(payload))

    with patch("requests.get", mock_get):
        result = client.get_earnings_calendar(
            symbol="AXON", from_date=past_from, to_date=past_to
        )

    assert result.is_ok
    call_kwargs = mock_get.call_args
    params = call_kwargs[1].get("params") or call_kwargs[0][1]
    assert date.fromisoformat(params["from"]) == past_from
    assert date.fromisoformat(params["to"]) == past_to


def test_get_earnings_calendar_from_date_without_to_date_uses_horizon(client):
    """from_date alone still respects horizon for the window end (to_date
    defaults to from_date + horizon days, not today + horizon days)."""
    past_from = date.today() - timedelta(days=20)
    payload = [{"symbol": "SKYT", "date": past_from.isoformat(), "hour": "amc"}]
    mock_get = MagicMock(return_value=_make_response(payload))

    with patch("requests.get", mock_get):
        client.get_earnings_calendar(symbol="SKYT", horizon="3month", from_date=past_from)

    call_kwargs = mock_get.call_args
    params = call_kwargs[1].get("params") or call_kwargs[0][1]
    assert date.fromisoformat(params["from"]) == past_from
    assert date.fromisoformat(params["to"]) == past_from + timedelta(days=90)


def test_get_earnings_calendar_symbol_path_single_request(client):
    """Symbol-filtered fetches are small — they must stay a single request."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "NVDA", "date": tomorrow, "hour": "amc"}]
    mock_get = MagicMock(return_value=_make_response(payload))

    with patch("requests.get", mock_get):
        result = client.get_earnings_calendar(symbol="NVDA", horizon="3month")

    assert result.is_ok
    assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# 429 backoff/retry — a real server-side rejection is not the same as our own
# local rate_limiter running dry, and must not be treated as an immediate,
# un-retried failure (root-caused 2026-08-24: this drove a silent single-
# source consensus bug and left duplicate-cleanup conflicts stuck forever).
# ---------------------------------------------------------------------------

def _make_429_response(retry_after=None):
    mock = MagicMock()
    mock.status_code = 429
    mock.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return mock


def test_get_earnings_calendar_retries_after_429(client):
    """A 429 should back off and retry, not fail immediately."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "NVDA", "date": tomorrow, "hour": "amc"}]
    mock_get = MagicMock(side_effect=[_make_429_response(), _make_response(payload)])

    with patch("requests.get", mock_get), patch("time.sleep") as mock_sleep:
        result = client.get_earnings_calendar(symbol="NVDA", horizon="3month")

    assert result.is_ok
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once()


def test_get_earnings_calendar_honors_retry_after_header(client):
    """Retry-After header value should be used as the backoff duration."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "NVDA", "date": tomorrow, "hour": "amc"}]
    mock_get = MagicMock(side_effect=[_make_429_response(retry_after=12), _make_response(payload)])

    with patch("requests.get", mock_get), patch("time.sleep") as mock_sleep:
        result = client.get_earnings_calendar(symbol="NVDA", horizon="3month")

    assert result.is_ok
    mock_sleep.assert_called_once_with(12.0)


def test_get_earnings_calendar_429_exhausted_returns_ratelimit_err(client):
    """Persistent 429s across all retries should return Err(RATELIMIT), not hang forever."""
    mock_get = MagicMock(return_value=_make_429_response())

    with patch("requests.get", mock_get), patch("time.sleep"):
        result = client.get_earnings_calendar(symbol="NVDA", horizon="3month")

    assert result.is_err
    assert result.error.code == ErrorCode.RATELIMIT
