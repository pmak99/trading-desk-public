"""
Module-level helpers shared by multiple handlers.

Extracted from handlers.py — these two functions are called by
_pre_market_prep, _outcome_recorder, and _weekly_backfill.
Kept in a separate file to avoid circular imports once handlers/
becomes a package.
"""
import asyncio
from typing import Dict, Any, List

from src.core.config import today_et
from src.core.logging import log
from src.integrations import FinnhubClient
from src.domain import HistoricalMovesRepository

async def fetch_earnings_with_db_fallback(
    finnhub: FinnhubClient,
    repo: HistoricalMovesRepository,
    days: int = 5
) -> List[Dict[str, Any]]:
    """
    Fetch earnings calendar with database fallback.

    Tries Finnhub first. If it returns empty (API issues, rate limits),
    falls back to local database earnings_calendar table.

    Args:
        finnhub: Finnhub API client
        repo: Repository with get_upcoming_earnings() method
        days: Number of days to look ahead (default 5)

    Returns:
        List of earnings dicts with 'symbol', 'report_date', 'timing' keys
    """
    try:
        earnings = await finnhub.get_earnings_calendar()
        if earnings:
            return earnings

        # Finnhub returned empty - fall back to DB
        log("warn", "Finnhub returned empty, using DB fallback", days=days)
        return repo.get_upcoming_earnings(today_et(), days)

    except Exception as e:
        # API error - fall back to DB
        log("error", "Finnhub failed, using DB fallback", error=str(e), days=days)
        return repo.get_upcoming_earnings(today_et(), days)


def _parse_price_history(closes: dict) -> List[tuple]:
    """
    Parse timestamp->price dict into sorted (date_str, price) list.

    Handles various timestamp formats from Yahoo Finance API:
    - datetime objects with strftime
    - string timestamps
    - other types (converted via str)

    Args:
        closes: Dict mapping timestamps to prices

    Returns:
        List of (date_str, price) tuples sorted by date ascending
    """
    price_data = []
    for timestamp, price in closes.items():
        if price is None:
            continue
        try:
            if hasattr(timestamp, 'strftime'):
                date_str = timestamp.strftime("%Y-%m-%d")
            elif isinstance(timestamp, str):
                date_str = timestamp[:10]
            else:
                date_str = str(timestamp)[:10]
            price_data.append((date_str, float(price)))
        except (ValueError, TypeError):
            continue
    price_data.sort(key=lambda x: x[0])
    return price_data

