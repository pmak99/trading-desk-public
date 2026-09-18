"""API keys travel in request headers, never in the URL.

On 2026-09-13 a Twelve Data 429 made `backfill_historical.py` log
`requests`' error text, which quotes the full URL — `apikey=` included — so the
key landed in a terminal transcript, and would have landed in the server's
maintenance logs on any failed backfill. Finnhub's client, its MCP server and
TACO's cross-asset fetch passed keys the same way. Both vendors accept header
auth (verified live that day: Twelve Data `Authorization: apikey <key>`,
Finnhub `X-Finnhub-Token`), and a URL with no key cannot leak one.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[3]
KEY = "secret-key-must-not-appear-in-urls"


def _captured_get(payload):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None, **_):
        calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"{}"
        resp.json.return_value = payload
        resp.raise_for_status = MagicMock()
        return resp

    return fake_get, calls


def _assert_key_only_in_headers(calls, header, value):
    assert calls, "no request was made"
    for call in calls:
        assert KEY not in call["url"]
        assert KEY not in "".join(str(v) for v in call["params"].values())
        assert call["headers"].get(header) == value


def test_no_request_puts_a_key_in_its_query_parameters():
    offenders = []
    for root in ("2.0/src", "2.0/scripts", "scripts"):
        for path in (REPO / root).rglob("*.py"):
            if {"venv", "tests"} & set(path.parts):
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"""["'](apikey|api_key|token)["']\s*:""", line):
                    offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert offenders == []


def test_backfill_twelve_data_sends_the_key_as_a_header():
    # The fetch moved to scripts/earnings_moves.py (shared with jobs/record_moves.py);
    # backfill_historical re-exports it and resolves the key at call time.
    from scripts import earnings_moves as em

    fake_get, calls = _captured_get({"status": "ok", "values": []})
    with patch.dict("os.environ", {"TWELVE_DATA_KEY": KEY}), \
            patch.object(em.requests, "get", fake_get):
        em.get_twelve_data_prices("AAPL")
    _assert_key_only_in_headers(calls, "Authorization", f"apikey {KEY}")


def test_finnhub_client_sends_the_token_as_a_header():
    from src.infrastructure.api.finnhub import FinnhubAPI

    fake_get, calls = _captured_get({"earningsCalendar": []})
    with patch("requests.get", fake_get):
        FinnhubAPI(api_key=KEY).get_earnings_calendar()
    _assert_key_only_in_headers(calls, "X-Finnhub-Token", KEY)


def test_taco_cross_asset_sends_the_key_as_a_header():
    from scripts.taco import cross_asset
    from src.infrastructure.api import twelve_data

    fake_get, calls = _captured_get({"status": "ok", "values": []})
    with patch.object(twelve_data.requests, "get", fake_get):
        cross_asset.fetch_twelvedata("TLT", KEY)
    _assert_key_only_in_headers(calls, "Authorization", f"apikey {KEY}")
