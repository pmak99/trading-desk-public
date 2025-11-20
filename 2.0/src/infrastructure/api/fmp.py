"""
Financial Modeling Prep (FMP) API client.

Provides earnings data with EPS estimates and historical surprises.
Complements Alpha Vantage with richer earnings context for VRP analysis.

Free tier: 500MB/month bandwidth
"""

import requests
import logging
import time
from datetime import date, datetime
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from decimal import Decimal

from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.infrastructure.cache.hybrid_cache import HybridCache

logger = logging.getLogger(__name__)

# Rate limiting: Conservative 2 calls/second for free tier
MIN_CALL_INTERVAL = 0.5  # seconds between calls


@dataclass
class EarningsSurprise:
    """Historical earnings surprise data."""
    date: date
    actual_eps: Optional[Decimal]
    estimated_eps: Optional[Decimal]
    surprise: Optional[Decimal]  # actual - estimated
    surprise_pct: Optional[float]  # percentage surprise

    @property
    def beat(self) -> Optional[bool]:
        """True if beat estimates, False if missed, None if no data."""
        if self.surprise is None:
            return None
        return self.surprise > 0


@dataclass
class EarningsEstimate:
    """Upcoming earnings estimate data."""
    ticker: str
    earnings_date: date
    eps_estimated: Optional[Decimal]
    revenue_estimated: Optional[Decimal]
    fiscal_quarter: Optional[str]


class FMPAPI:
    """
    Financial Modeling Prep API client.

    Provides:
    - Earnings calendar with EPS estimates
    - Historical earnings surprises (beat/miss)
    - Analyst ratings
    """

    def __init__(
        self,
        api_key: str,
        cache: Optional[HybridCache] = None,
        base_url: str = "https://financialmodelingprep.com/api/v3"
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.cache = cache
        self.timeout = 10
        self._last_call_time = 0.0

    def __repr__(self):
        return f"FMPAPI(base_url={self.base_url}, key=***)"

    def _rate_limit(self):
        """Enforce minimum interval between API calls."""
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < MIN_CALL_INTERVAL:
            time.sleep(MIN_CALL_INTERVAL - elapsed)
        self._last_call_time = time.time()

    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Result[Any, AppError]:
        """Make rate-limited API request."""
        self._rate_limit()

        url = f"{self.base_url}/{endpoint}"
        request_params = {"apikey": self.api_key}
        if params:
            request_params.update(params)

        try:
            response = requests.get(url, params=request_params, timeout=self.timeout)
            response.raise_for_status()

            data = response.json()

            # FMP returns error messages in response body
            if isinstance(data, dict) and "Error Message" in data:
                return Err(AppError(ErrorCode.EXTERNAL, data["Error Message"]))

            return Ok(data)

        except requests.exceptions.Timeout:
            return Err(AppError(ErrorCode.TIMEOUT, f"FMP timeout: {endpoint}"))
        except requests.exceptions.RequestException as e:
            return Err(AppError(ErrorCode.EXTERNAL, f"FMP request error: {e}"))
        except Exception as e:
            return Err(AppError(ErrorCode.EXTERNAL, f"FMP error: {e}"))

    def get_earnings_surprises(
        self,
        ticker: str,
        limit: int = 8
    ) -> Result[List[EarningsSurprise], AppError]:
        """
        Get historical earnings surprises for a ticker.

        Shows beat/miss history which adds context to VRP analysis.
        A stock that consistently beats may have understated implied moves.

        Args:
            ticker: Stock symbol
            limit: Number of quarters to return

        Returns:
            List of EarningsSurprise objects (most recent first)
        """
        # Check cache first
        cache_key = f"fmp:surprises:{ticker}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                logger.debug(f"FMP cache hit: {ticker} surprises")
                return Ok(cached[:limit])

        # Use historical earning calendar endpoint (free tier)
        result = self._request(f"historical/earning_calendar/{ticker}", {"limit": limit * 2})
        if result.is_err:
            return Err(result.error)

        data = result.value
        if not data:
            return Err(AppError(ErrorCode.NODATA, f"No earnings surprises for {ticker}"))

        surprises = []
        for item in data[:limit]:
            try:
                # Parse date
                date_str = item.get("date", "")
                if date_str:
                    surprise_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                else:
                    continue

                # Parse EPS values (field names differ between endpoints)
                actual = item.get("eps") or item.get("actualEarningResult")
                estimated = item.get("epsEstimated") or item.get("estimatedEarning")

                actual_eps = Decimal(str(actual)) if actual is not None else None
                estimated_eps = Decimal(str(estimated)) if estimated is not None else None

                # Calculate surprise
                surprise = None
                surprise_pct = None
                if actual_eps is not None and estimated_eps is not None:
                    surprise = actual_eps - estimated_eps
                    if estimated_eps != 0:
                        surprise_pct = float(surprise / abs(estimated_eps) * 100)

                surprises.append(EarningsSurprise(
                    date=surprise_date,
                    actual_eps=actual_eps,
                    estimated_eps=estimated_eps,
                    surprise=surprise,
                    surprise_pct=surprise_pct
                ))

            except (ValueError, KeyError) as e:
                logger.debug(f"Skipping malformed surprise data: {e}")
                continue

        # Cache for 6 days (earnings data doesn't change often)
        if self.cache and surprises:
            self.cache.set(cache_key, surprises)

        logger.info(f"FMP: Fetched {len(surprises)} earnings surprises for {ticker}")
        return Ok(surprises)

    def get_earnings_calendar(
        self,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None
    ) -> Result[List[EarningsEstimate], AppError]:
        """
        Get earnings calendar with EPS estimates.

        Args:
            from_date: Start date (default: today)
            to_date: End date (default: 3 months out)

        Returns:
            List of EarningsEstimate objects
        """
        if from_date is None:
            from_date = date.today()
        if to_date is None:
            to_date = date.today().replace(month=date.today().month + 3)

        # Check cache
        cache_key = f"fmp:calendar:{from_date}:{to_date}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                logger.debug(f"FMP cache hit: earnings calendar")
                return Ok(cached)

        params = {
            "from": from_date.isoformat(),
            "to": to_date.isoformat()
        }

        result = self._request("earning_calendar", params)
        if result.is_err:
            return Err(result.error)

        data = result.value
        if not data:
            return Ok([])

        estimates = []
        for item in data:
            try:
                earnings_date = datetime.strptime(item["date"], "%Y-%m-%d").date()

                eps_est = item.get("epsEstimated")
                rev_est = item.get("revenueEstimated")

                estimates.append(EarningsEstimate(
                    ticker=item["symbol"],
                    earnings_date=earnings_date,
                    eps_estimated=Decimal(str(eps_est)) if eps_est else None,
                    revenue_estimated=Decimal(str(rev_est)) if rev_est else None,
                    fiscal_quarter=item.get("fiscalDateEnding")
                ))

            except (ValueError, KeyError) as e:
                logger.debug(f"Skipping malformed calendar entry: {e}")
                continue

        # Cache for 1 day (calendar updates daily)
        if self.cache and estimates:
            self.cache.set(cache_key, estimates)

        logger.info(f"FMP: Fetched {len(estimates)} earnings events")
        return Ok(estimates)

    def get_analyst_ratings(
        self,
        ticker: str
    ) -> Result[Dict[str, Any], AppError]:
        """
        Get analyst ratings summary for a ticker.

        Args:
            ticker: Stock symbol

        Returns:
            Dict with rating counts and consensus
        """
        cache_key = f"fmp:ratings:{ticker}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                return Ok(cached)

        result = self._request(f"grade/{ticker}")
        if result.is_err:
            return Err(result.error)

        data = result.value
        if not data:
            return Err(AppError(ErrorCode.NODATA, f"No analyst ratings for {ticker}"))

        # Aggregate recent ratings (last 90 days)
        cutoff = date.today().replace(day=date.today().day - 90) if date.today().day > 90 else date.today()

        ratings = {"buy": 0, "hold": 0, "sell": 0, "total": 0, "recent": []}

        for item in data[:20]:  # Last 20 ratings
            try:
                grade = item.get("newGrade", "").lower()
                if "buy" in grade or "outperform" in grade or "overweight" in grade:
                    ratings["buy"] += 1
                elif "hold" in grade or "neutral" in grade or "equal" in grade:
                    ratings["hold"] += 1
                elif "sell" in grade or "underperform" in grade or "underweight" in grade:
                    ratings["sell"] += 1
                ratings["total"] += 1

                ratings["recent"].append({
                    "firm": item.get("gradingCompany"),
                    "grade": item.get("newGrade"),
                    "date": item.get("date")
                })

            except (ValueError, KeyError):
                continue

        if self.cache:
            self.cache.set(cache_key, ratings)

        return Ok(ratings)

    def format_surprises_summary(self, surprises: List[EarningsSurprise]) -> str:
        """
        Format earnings surprises for display in analysis output.

        Args:
            surprises: List of EarningsSurprise objects

        Returns:
            Formatted string for display
        """
        if not surprises:
            return "No historical earnings data"

        # Beat/miss pattern
        pattern = []
        for s in surprises[:4]:
            if s.beat is True:
                pattern.append("BEAT")
            elif s.beat is False:
                pattern.append("MISS")
            else:
                pattern.append("N/A")

        # Average surprise percentage
        valid_surprises = [s.surprise_pct for s in surprises if s.surprise_pct is not None]
        avg_surprise = sum(valid_surprises) / len(valid_surprises) if valid_surprises else 0

        return f"Last {len(pattern)}Q: {', '.join(pattern)} | Avg Surprise: {avg_surprise:+.1f}%"
