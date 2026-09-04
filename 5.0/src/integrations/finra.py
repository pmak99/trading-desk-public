"""
FINRA daily short volume client.

Downloads FINRA RegSho daily short volume files to compute short ratio
for a given ticker. High short ratio (>= 50%) before earnings can indicate
elevated bearish pressure or short-squeeze potential.
Free data, no authentication required.
"""

from datetime import date, timedelta
from typing import Optional

import httpx

from src.core.logging import log


FINRA_REGSHO_BASE = "https://cdn.finra.org/equity/regsho/daily"
SHORT_RATIO_THRESHOLD = 0.50
_MARKET_CODES = ("FNSQ", "FNYX")  # NASDAQ then NYSE


class FinraClient:
    """Async FINRA short volume client. No API key required."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self):
        await self._client.aclose()

    async def get_short_ratio(
        self, ticker: str, target_date: Optional[date] = None
    ) -> Optional[float]:
        """Return most recent short volume ratio (0..1) for ticker, or None."""
        check_date = target_date or date.today()

        # Try up to 5 business days back to handle holidays/weekends
        for _ in range(7):
            if check_date.weekday() >= 5:
                check_date -= timedelta(days=1)
                continue
            ratio = await self._fetch_ratio_for_date(ticker, check_date)
            if ratio is not None:
                return ratio
            check_date -= timedelta(days=1)

        return None

    async def _fetch_ratio_for_date(self, ticker: str, target_date: date) -> Optional[float]:
        """Fetch and parse the FINRA regsho file for a specific date."""
        date_str = target_date.strftime("%Y%m%d")

        for market in _MARKET_CODES:
            url = f"{FINRA_REGSHO_BASE}/{market}shvol{date_str}.txt"
            try:
                response = await self._client.get(url)
                if response.status_code != 200:
                    continue
                ratio = self._parse_ratio(ticker, response.text)
                if ratio is not None:
                    return ratio
            except Exception as e:
                log("debug", "FINRA file fetch failed", market=market, date=date_str, error=str(e)[:80])
                continue

        return None

    def _parse_ratio(self, ticker: str, content: str) -> Optional[float]:
        """Parse pipe-delimited regsho file and return short ratio for ticker."""
        for line in content.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 5:
                continue
            if parts[1] != ticker:
                continue
            try:
                short_vol = int(parts[2])
                total_vol = int(parts[4])
                if total_vol > 0:
                    return short_vol / total_vol
            except (ValueError, IndexError):
                continue
        return None

    def build_risk_flag(self, ratio: Optional[float]) -> Optional[str]:
        """Build a risk flag string for high short ratio, or None if below threshold."""
        if ratio is None or ratio < SHORT_RATIO_THRESHOLD:
            return None
        pct = int(ratio * 100)
        return f"⚠️ FINRA: High short volume ratio {pct}% (bearish pressure / potential squeeze)"
