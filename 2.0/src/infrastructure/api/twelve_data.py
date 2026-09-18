"""
Twelve Data's time_series endpoint -- the one request every Twelve Data fetch makes.

Two callers: scripts/earnings_moves.py (daily bars for historical_moves, shared by
backfill_historical.py and jobs/record_moves.py) and scripts/taco/cross_asset.py
(ETF closes when Yahoo has none). Every request waits on the process's Twelve Data
gate (src/utils/rate_limiter.py: at most 8 in any 60 s) and is retried there:

  - a minute limit (HTTP 429, or an error body with code 429 or "run out of API
    credits" that is not the day's) waits for Retry-After when one is sent, else
    into the next minute, when the credits reset;
  - a 5xx backs off 2 s, then 4 s;
  - the day's credits running out is not retried -- nothing changes before
    00:00 UTC -- and neither is any other error;
  - this host's calls for the UTC day reaching TWELVE_DATA_DAILY_CEILING (780)
    is the same daily limit, raised before a request is sent.

Until 2026-09-14 each caller paced itself (record_moves 7.5 s a call,
backfill_historical 8 s a ticker, TACO's fallback not at all), nothing was
retried, and record_moves ended its run at the first 429 of either kind.
"""

import time
from typing import Optional

import requests

from src.utils.rate_limiter import (
    TWELVE_DATA,
    VendorDailyLimit,
    VendorRateLimited,
    vendor_gate,
)

TIME_SERIES_URL = "https://api.twelvedata.com/time_series"

_CREDITS_GONE = "run out of api credits"


class TwelveDataError(Exception):
    """Twelve Data answered with an error: unknown symbol, plan restriction,
    malformed body. Retrying tomorrow will not change the answer."""


class TwelveDataRateLimited(TwelveDataError):
    """HTTP 429 or "run out of API credits" that the gate's retries did not clear,
    the day's credits (`daily`: Twelve Data's answer, or this host's ceiling), or
    no call slot in time. Every further call fails the same way for a while, so a
    batch caller must stop, not skip ahead."""

    def __init__(self, message: str, *, daily: bool = False,
                 retry_after: Optional[float] = None):
        super().__init__(message)
        self.daily = daily
        self.retry_after = retry_after


def seconds_to_next_minute(now: float) -> float:
    """Seconds until one second past the next wall-clock minute, when Twelve Data
    resets the minute's credits."""
    return 61.0 - now % 60.0


def time_series(params: dict, api_key: str, timeout: float) -> dict:
    """The JSON body of a time_series answer that is not an error.

    Raises:
        TwelveDataRateLimited: a rate limit the gate's retries did not clear, the
            day's credits or this host's daily ceiling, or no call slot within the
            gate's budget.
        TwelveDataError: any other error body.
        requests.RequestException: network failure, or an HTTP error status (a
            5xx only after its retries).
    """
    try:
        return vendor_gate(TWELVE_DATA).call(
            lambda: _request(params, api_key, timeout), _retry_after)
    except VendorRateLimited as e:
        raise TwelveDataRateLimited(str(e), daily=isinstance(e, VendorDailyLimit)) from e


def _request(params: dict, api_key: str, timeout: float) -> dict:
    # The key goes in a header, never the URL: requests' error text quotes the
    # URL, and a 429 once logged the key that way (2026-09-13).
    response = requests.get(TIME_SERIES_URL, params=params,
                            headers={"Authorization": f"apikey {api_key}"}, timeout=timeout)
    if response.status_code == 429:
        # The body says whether the minute or the day ran out; keep it.
        try:
            detail = (response.json() or {}).get("message") or ""
        except ValueError:
            detail = ""
        message = f"HTTP 429 for {params.get('symbol')}" + (f": {detail}" if detail else "")
        raise _rate_limited(message, detail, response)
    response.raise_for_status()

    data = response.json()
    if data.get("status") == "error":
        message = data.get("message", "Unknown error")
        if data.get("code") == 429 or _CREDITS_GONE in (message or "").lower():
            raise _rate_limited(message, message, response)
        raise TwelveDataError(message)
    return data


def _rate_limited(message: str, detail: str, response) -> TwelveDataRateLimited:
    daily = "for the day" in (detail or "").lower()
    retry_after = None
    header = response.headers.get("Retry-After")
    if isinstance(header, str):
        try:
            retry_after = max(float(header), 0.0)
        except ValueError:
            pass            # an HTTP-date: wait for the next minute instead
    return TwelveDataRateLimited(message, daily=daily, retry_after=retry_after)


def _retry_after(error: Exception) -> Optional[float]:
    """The gate's retry rule: seconds to wait, 0 for exponential, None for never."""
    if isinstance(error, TwelveDataRateLimited):
        if error.daily:
            return None
        if error.retry_after is not None:
            return error.retry_after
        return seconds_to_next_minute(time.time())
    if isinstance(error, requests.HTTPError):
        status = getattr(error.response, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return 0.0
    return None
