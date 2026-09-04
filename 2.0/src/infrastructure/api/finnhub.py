"""
Finnhub API client for earnings calendar.

Rate limits: 60 calls/minute, 30k calls/month (free tier).
"""

import logging
import time
import requests
from datetime import date, timedelta
from typing import List, Tuple, Optional

from src.domain.enums import EarningsTiming
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.utils.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB

# Finnhub silently truncates bulk /calendar/earnings responses at 1,500
# entries, keeping the LATEST dates — a 90-day window in earnings season
# returned only the final ~60 days (found Jul 2026: NFLX reporting same-day
# was absent). Bulk fetches must therefore be chunked.
FINNHUB_BULK_CAP = 1500
BULK_CHUNK_DAYS = 7

_HORIZON_DAYS = {"3month": 90, "6month": 180, "12month": 365}

# A 429 means Finnhub has already rejected us — our own local token bucket
# (which only paces OUR notion of "60 per 60 seconds") has no way to know
# that. Retrying immediately just accumulates more rejections. Found
# 2026-08-24: a calendar sync burned through its local budget in the first
# ~10 seconds (bucket starts full), got 429'd on every remaining call for
# the rest of the run — including calls made a full ~10s apart, well under
# any reasonable per-second rate — with zero backoff in between.
RATE_LIMIT_MAX_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = 5.0

_HOUR_TO_TIMING = {
    "bmo": EarningsTiming.BMO,
    "amc": EarningsTiming.AMC,
}


class FinnhubAPI:
    """
    Finnhub API client (sync).

    Returns Result[List[Tuple[str, date, EarningsTiming]], AppError] for earnings calendar.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = 30
        self.rate_limiter = rate_limiter

    def __repr__(self):
        return f"FinnhubAPI(base_url={self.base_url}, key=***)"

    def get_earnings_calendar(
        self,
        symbol: Optional[str] = None,
        horizon: Optional[str] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> "Result[List[Tuple[str, date, EarningsTiming]], AppError]":
        """
        Get earnings calendar from Finnhub.

        Bulk fetches (no symbol) are chunked into BULK_CHUNK_DAYS windows:
        Finnhub silently caps the response at FINNHUB_BULK_CAP entries and
        keeps the LATEST dates, so a single wide window loses the nearest
        weeks entirely.

        Args:
            symbol: Optional ticker to filter (None = all upcoming)
            horizon: Time horizon ("3month", "6month", "12month"); default 30 days.
                Ignored if from_date/to_date are given.
            from_date: Explicit window start. Default is today — pass this to
                query a window that includes past dates (e.g. to corroborate a
                disputed earnings_calendar entry that has already occurred;
                the default today-forward window can never return it).
            to_date: Explicit window end. Default is from_date + horizon days.

        Returns:
            Result with list of (ticker, date, timing) tuples, deduped on
            (ticker, date)
        """
        try:
            days = _HORIZON_DAYS.get(horizon, 30) if horizon else 30
            start = from_date if from_date is not None else date.today()
            end = to_date if to_date is not None else start + timedelta(days=days)

            if symbol:
                entries_result = self._fetch_calendar_window(start, end, symbol)
            else:
                entries_result = self._fetch_calendar_chunked(start, end)
            if entries_result.is_err:
                return entries_result

            entries = entries_result.value
            if not entries:
                return Err(AppError(ErrorCode.NODATA, f"No earnings found for {symbol or 'all'}"))

            results = []
            seen = set()
            for entry in entries:
                try:
                    ticker = entry["symbol"].strip()
                    earn_date = date.fromisoformat(entry["date"])
                    timing = _HOUR_TO_TIMING.get((entry.get("hour") or "").lower(), EarningsTiming.UNKNOWN)
                except (KeyError, ValueError) as e:
                    logger.debug(f"Skipping malformed earnings entry: {e}")
                    continue
                if (ticker, earn_date) in seen:
                    continue
                seen.add((ticker, earn_date))
                results.append((ticker, earn_date, timing))

            if not results:
                return Err(AppError(ErrorCode.NODATA, "No valid earnings entries parsed"))

            logger.info(f"Fetched {len(results)} earnings events from Finnhub")
            return Ok(results)

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def _fetch_calendar_window(
        self,
        from_date: date,
        to_date: date,
        symbol: Optional[str] = None,
    ) -> "Result[List[dict], AppError]":
        """Single calendar request. Returns the raw entry dicts (may be empty).

        Retries on a real 429 from Finnhub's server (distinct from our own
        local rate_limiter, which paces outbound calls but has no way to know
        the server already rejected us) — honoring Retry-After when present,
        else a fixed backoff. Without this, a 429 anywhere in a multi-ticker
        run just fails that ticker and immediately fires the next one, which
        keeps getting rejected too (found 2026-08-24).
        """
        if self.rate_limiter and not self.rate_limiter.acquire(blocking=True):
            return Err(AppError(ErrorCode.RATELIMIT, "Finnhub rate limit exceeded"))

        params = {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "token": self.api_key,
        }
        if symbol:
            params["symbol"] = symbol

        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            try:
                response = requests.get(
                    f"{self.base_url}/calendar/earnings",
                    params=params,
                    timeout=self.timeout,
                )

                if response.status_code == 429:
                    if attempt < RATE_LIMIT_MAX_RETRIES:
                        retry_after = response.headers.get("Retry-After")
                        try:
                            wait_seconds = float(retry_after) if retry_after else RATE_LIMIT_BACKOFF_SECONDS
                        except ValueError:
                            wait_seconds = RATE_LIMIT_BACKOFF_SECONDS
                        logger.warning(
                            f"Finnhub 429 for {symbol or 'bulk'} — backing off "
                            f"{wait_seconds:.0f}s before retry {attempt + 1}/{RATE_LIMIT_MAX_RETRIES}"
                        )
                        time.sleep(wait_seconds)
                        continue
                    return Err(AppError(ErrorCode.RATELIMIT, "Finnhub rate limit exceeded (429, retries exhausted)"))

                response.raise_for_status()

                if len(response.content) > MAX_RESPONSE_SIZE:
                    return Err(AppError(ErrorCode.EXTERNAL, f"Response too large: {len(response.content)} bytes"))

                return Ok(response.json().get("earningsCalendar") or [])

            except requests.exceptions.Timeout:
                return Err(AppError(ErrorCode.TIMEOUT, "Finnhub request timeout"))

            except requests.exceptions.RequestException as e:
                logger.error(f"Finnhub request error: {e}")
                return Err(AppError(ErrorCode.EXTERNAL, str(e)))

        # Unreachable (loop always returns or retries), but keeps type-checkers happy.
        return Err(AppError(ErrorCode.RATELIMIT, "Finnhub rate limit exceeded"))

    def _fetch_calendar_chunked(
        self,
        from_date: date,
        to_date: date,
    ) -> "Result[List[dict], AppError]":
        """
        Bulk fetch in BULK_CHUNK_DAYS windows, splitting any window that comes
        back at FINNHUB_BULK_CAP entries (truncated) until it fits or is a
        single day.
        """
        entries: List[dict] = []
        start = from_date
        while start <= to_date:
            chunk_end = min(start + timedelta(days=BULK_CHUNK_DAYS - 1), to_date)
            result = self._fetch_calendar_window(start, chunk_end)
            if result.is_err:
                return result
            chunk = result.value

            if len(chunk) >= FINNHUB_BULK_CAP and chunk_end > start:
                # Truncated: halve the window and refetch both sides
                mid = start + timedelta(days=(chunk_end - start).days // 2)
                left = self._fetch_calendar_chunked(start, mid)
                if left.is_err:
                    return left
                right = self._fetch_calendar_chunked(mid + timedelta(days=1), chunk_end)
                if right.is_err:
                    return right
                entries.extend(left.value)
                entries.extend(right.value)
            else:
                if len(chunk) >= FINNHUB_BULK_CAP:
                    logger.warning(
                        f"Finnhub calendar {start}: single-day response at cap "
                        f"({len(chunk)} entries) — may still be truncated"
                    )
                entries.extend(chunk)

            start = chunk_end + timedelta(days=1)
        return Ok(entries)
