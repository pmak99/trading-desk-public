"""
SEC EDGAR API client for pre-earnings risk detection.

Checks for NT 10-K/10-Q late filing notices — a strong negative signal
when a company cannot meet its SEC reporting deadline before earnings.
Free API, no authentication required.
"""

from datetime import date, timedelta
from typing import List, Optional, Dict, Any

import httpx

from src.core.logging import log


EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
SHORT_RATIO_THRESHOLD = 0.50


class SecEdgarClient:
    """Async SEC EDGAR client. No API key required — public EDGAR API."""

    def __init__(self):
        # EDGAR requires contact info in User-Agent (per their terms)
        self._client = httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "ivcrush-trading-research/2.0 pmakwana99@gmail.com"},
        )

    async def close(self):
        await self._client.aclose()

    async def get_nt_filings(self, ticker: str, days: int = 30) -> List[Dict[str, Any]]:
        """Return list of NT 10-K/10-Q filings for ticker in the last N days."""
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        try:
            response = await self._client.get(
                EDGAR_SEARCH_URL,
                params={
                    "q": f'"{ticker}"',
                    "dateRange": "custom",
                    "startdt": start_date.isoformat(),
                    "enddt": end_date.isoformat(),
                    "forms": "NT 10-K,NT 10-Q",
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("hits", {}).get("hits", [])
        except Exception as e:
            log("warn", "SEC EDGAR NT check failed", ticker=ticker, error=str(e)[:100])
            return []

    def build_risk_flag(self, filings: List[Dict[str, Any]]) -> Optional[str]:
        """Build a risk flag string from NT filings, or None if no filings."""
        if not filings:
            return None
        source = filings[0].get("_source", {})
        form = source.get("form_type", "NT filing")
        filed = source.get("file_date", "")
        date_str = f" ({filed})" if filed else ""
        return f"⚠️ SEC EDGAR: {form} filed{date_str} — company notified SEC it cannot meet reporting deadline"
