"""
Tests for EarningsWhisperScraper — earningswhispers.com direct scraping.

TDD: tests written before implementation.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.domain.errors import ErrorCode


@pytest.fixture
def scraper():
    from src.infrastructure.data_sources.earnings_whisper_scraper import (
        EarningsWhisperScraper,
    )
    return EarningsWhisperScraper()


def _mock_response(html: str, status_code: int = 200):
    """Build a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.text = html
    if status_code >= 400:
        import requests
        mock.raise_for_status.side_effect = requests.HTTPError(
            response=mock
        )
    else:
        mock.raise_for_status = MagicMock()
    return mock


SAMPLE_HTML = """<div id="showweekcal"><div class="row"><div class="col-1 weekdayticks wdbmo" style="background-image: url(/api/verticallogos/NVDA,WMT,CSCO,)" id="20260525-BMO"></div><div class="col-1 weekdayticks wdamc" style="background-image: url(/api/verticallogos/AMAT,PANW,SNOW,)" id="20260525-AMC"></div><div class="col-1 weekdayticks wdbmo" style="background-image: url(/api/verticallogos/UBER,MELI,JD,AFRM,)" id="20260526-BMO"></div></div></div>"""

SAMPLE_HTML_NO_TICKERS = """
<div id="showweekcal"><div class="row"><div class="col-1 weekdaynoticks wdbmo"></div></div></div>
"""


# ---------------------------------------------------------------------------
# _parse_earningswhispers_weekview
# ---------------------------------------------------------------------------

def test_parse_earningswhispers_weekview_extracts_verticallogos_tickers(scraper):
    """Should extract ticker symbols from /api/verticallogos/TICKER,TICKER, patterns."""
    tickers = scraper._parse_earningswhispers_weekview(SAMPLE_HTML)
    assert "NVDA" in tickers
    assert "WMT" in tickers
    assert "CSCO" in tickers


def test_parse_earningswhispers_weekview_returns_list_in_order(scraper):
    """Should return tickers in document order, deduplicated."""
    tickers = scraper._parse_earningswhispers_weekview(SAMPLE_HTML)
    assert tickers[0] == "NVDA"
    assert tickers[1] == "WMT"
    assert len(tickers) == 10


def test_parse_earningswhispers_weekview_returns_empty_when_no_logos(scraper):
    """Should return empty list when no verticallogos URLs found."""
    tickers = scraper._parse_earningswhispers_weekview(SAMPLE_HTML_NO_TICKERS)
    assert tickers == []


def test_parse_earningswhispers_weekview_deduplicates_tickers(scraper):
    """Should deduplicate if same ticker appears in multiple day cells."""
    html = "background-image: url(/api/verticallogos/AAPL,)" * 3
    tickers = scraper._parse_earningswhispers_weekview(html)
    assert tickers.count("AAPL") == 1


def test_parse_earningswhispers_weekview_keeps_word_shaped_tickers(scraper):
    """Word-shaped symbols are real tickers and must not be filtered out.

    This replaces a test that asserted "THE" got dropped from
    /api/verticallogos/THE,NVDA — synthetic HTML that cannot occur, since every
    value in that path segment is already a ticker. The word-list filter it
    guarded was silently dropping genuine reporters: NOW is ServiceNow, ALL is
    Allstate, ARE is Alexandria Real Estate, FOUR is Shift4, HAS is Hasbro,
    WELL is Welltower. SHOE (Shoe Carnival) was being dropped from live
    results the week this was found.
    """
    html = "background-image: url(/api/verticallogos/NOW,ALL,ARE,FOUR,HAS,WELL,JACK,SHOE,NVDA,)"
    tickers = scraper._parse_earningswhispers_weekview(html)
    for symbol in ("NOW", "ALL", "ARE", "FOUR", "HAS", "WELL", "JACK", "SHOE", "NVDA"):
        assert symbol in tickers, f"{symbol} is a real ticker and must survive parsing"


# ---------------------------------------------------------------------------
# _fetch_from_earningswhispers
# ---------------------------------------------------------------------------

def test_fetch_from_earningswhispers_returns_tickers_on_success(scraper):
    """Should return Ok with ticker list when the API returns valid data.

    The fetch makes two calls in sequence (page load for session cookie, then
    the calweekview API) — side_effect supplies a response for each in order.
    """
    monday = datetime(2026, 5, 26)
    with patch("requests.Session.get", side_effect=[_mock_response(""), _mock_response(SAMPLE_HTML)]):
        result = scraper._fetch_from_earningswhispers(monday)
    assert result.is_ok
    assert "NVDA" in result.value
    assert len(result.value) >= 5


def test_fetch_from_earningswhispers_returns_nodata_when_no_tickers(scraper):
    """Should return Err(NODATA) when the API response has no tickers."""
    monday = datetime(2026, 5, 26)
    with patch("requests.Session.get", side_effect=[_mock_response(""), _mock_response(SAMPLE_HTML_NO_TICKERS)]):
        result = scraper._fetch_from_earningswhispers(monday)
    assert not result.is_ok
    assert result.error.code == ErrorCode.NODATA


def test_fetch_from_earningswhispers_returns_err_on_http_error(scraper):
    """Should return Err(EXTERNAL) on HTTP error."""
    monday = datetime(2026, 5, 26)
    with patch("requests.Session.get", return_value=_mock_response("", status_code=403)):
        result = scraper._fetch_from_earningswhispers(monday)
    assert not result.is_ok
    assert result.error.code == ErrorCode.EXTERNAL


def test_fetch_from_earningswhispers_returns_err_on_connection_error(scraper):
    """Should return Err(EXTERNAL) when connection fails."""
    import requests as req_lib
    monday = datetime(2026, 5, 26)
    with patch("requests.Session.get", side_effect=req_lib.ConnectionError("refused")):
        result = scraper._fetch_from_earningswhispers(monday)
    assert not result.is_ok
    assert result.error.code == ErrorCode.EXTERNAL


def test_fetch_from_earningswhispers_returns_err_on_timeout(scraper):
    """Should return Err(EXTERNAL) on request timeout."""
    import requests as req_lib
    monday = datetime(2026, 5, 26)
    with patch("requests.Session.get", side_effect=req_lib.Timeout("timed out")):
        result = scraper._fetch_from_earningswhispers(monday)
    assert not result.is_ok
    assert result.error.code == ErrorCode.EXTERNAL


# ---------------------------------------------------------------------------
# fallback chain
# ---------------------------------------------------------------------------

def test_try_fetch_week_falls_back_to_database_when_ew_returns_nodata(scraper):
    """When earningswhispers returns NODATA, should fall through to DB fallback."""
    monday = datetime(2026, 5, 26)

    with patch("requests.Session.get", side_effect=[_mock_response(""), _mock_response(SAMPLE_HTML_NO_TICKERS)]):
        with patch.object(
            scraper, "_fetch_from_database",
            return_value=MagicMock(is_ok=True, value=["AAPL", "MSFT"])
        ) as mock_db:
            result = scraper._try_fetch_week(monday)

    mock_db.assert_called_once()
    assert result.is_ok
    assert "AAPL" in result.value


def test_ew_circuit_breaker_trips_after_repeated_failures(scraper):
    """Circuit breaker should open after 3 network failures via _try_fetch_week."""
    import requests as req_lib
    monday = datetime(2026, 5, 26)

    with patch("requests.Session.get", side_effect=req_lib.ConnectionError("refused")):
        with patch.object(
            scraper, "_fetch_from_database",
            return_value=MagicMock(is_ok=False, error=MagicMock(code=ErrorCode.NODATA))
        ):
            for _ in range(3):
                scraper._try_fetch_week(monday)

    assert scraper._ew_breaker.is_open()


# ---------------------------------------------------------------------------
# the shared week cache
# ---------------------------------------------------------------------------

class _RecordingCache:
    def __init__(self):
        self.rows, self.sets = {}, []

    def get(self, key):
        return self.rows.get(key)

    def set(self, key, value, ttl=None):
        self.sets.append((key, value, ttl))
        self.rows[key] = value


def test_an_earningswhispers_list_is_cached_for_the_week():
    """The shared cache's own L2 TTL (6 days, scan/constants.py) applies to the real list."""
    from src.infrastructure.data_sources.earnings_whisper_scraper import (
        EarningsWhisperScraper,
    )

    cache = _RecordingCache()
    scraper = EarningsWhisperScraper(cache=cache)
    with patch("requests.Session.get", side_effect=[_mock_response(""), _mock_response(SAMPLE_HTML)]):
        result = scraper._try_fetch_week(datetime(2026, 5, 25))

    assert result.is_ok
    assert [(key, ttl) for key, _, ttl in cache.sets] == [("whisper_tickers:2026-05-25", None)]
    assert scraper._try_fetch_week(datetime(2026, 5, 25)).value == result.value


def test_a_database_fallback_list_is_cached_for_an_hour_not_the_week():
    """The fallback is the week's reporters, not the "most anticipated" list.
    Cached for the week in the shared table, one failed EarningsWhispers call on
    either host would serve it to every /whisper and morning digest until the
    week ended; an hour spares the endpoint without hiding its recovery."""
    from src.infrastructure.data_sources.earnings_whisper_scraper import (
        WHISPER_FALLBACK_TTL_SECONDS,
        EarningsWhisperScraper,
    )

    cache = _RecordingCache()
    scraper = EarningsWhisperScraper(cache=cache)
    with patch("requests.Session.get",
               side_effect=[_mock_response(""), _mock_response(SAMPLE_HTML_NO_TICKERS)]):
        with patch.object(scraper, "_fetch_from_database",
                          return_value=MagicMock(is_ok=True, value=["AAPL", "MSFT"])):
            result = scraper._try_fetch_week(datetime(2026, 5, 25))

    assert result.is_ok
    assert WHISPER_FALLBACK_TTL_SECONDS == 3600
    assert cache.sets == [("whisper_tickers:2026-05-25", ["AAPL", "MSFT"], 3600)]
