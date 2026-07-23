"""
Tests for 2.0 FinnhubAPI (sync earnings calendar client).

TDD: tests written before implementation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("/Users/prashant/PycharmProjects/Trading Desk/2.0")))

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


def test_get_earnings_calendar_symbol_path_single_request(client):
    """Symbol-filtered fetches are small — they must stay a single request."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = [{"symbol": "NVDA", "date": tomorrow, "hour": "amc"}]
    mock_get = MagicMock(return_value=_make_response(payload))

    with patch("requests.get", mock_get):
        result = client.get_earnings_calendar(symbol="NVDA", horizon="3month")

    assert result.is_ok
    assert mock_get.call_count == 1
