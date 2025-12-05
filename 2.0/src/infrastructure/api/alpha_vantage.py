"""
Alpha Vantage API client for earnings calendar and historical prices.

Rate limits: 5 calls/minute, 500 calls/day (free tier).
"""

import requests
import logging
from datetime import date, datetime, timedelta
from typing import List, Tuple, Optional
from src.domain.types import Money, Percentage, HistoricalMove
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.enums import EarningsTiming
from src.utils.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

# Maximum response size to prevent OOM attacks (10MB)
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB


class AlphaVantageAPI:
    """
    Alpha Vantage API client.

    Provides:
    - Earnings calendar
    - Historical daily prices
    - Company fundamentals
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://www.alphavantage.co/query",
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = 30
        self.rate_limiter = rate_limiter

    def __repr__(self):
        """Mask API key in repr to prevent leaking in logs."""
        return f"AlphaVantageAPI(base_url={self.base_url}, key=***)"

    def get_earnings_calendar(
        self, symbol: Optional[str] = None, horizon: str = "3month"
    ) -> Result[List[Tuple[str, date, EarningsTiming]], AppError]:
        """
        Get earnings calendar.

        Args:
            symbol: Optional ticker to filter (None = all upcoming)
            horizon: Time horizon (3month, 6month, 12month)

        Returns:
            Result with list of (ticker, date, timing) tuples
        """
        # Rate limit check (blocking mode - wait for token)
        if self.rate_limiter and not self.rate_limiter.acquire(blocking=True):
            return Err(
                AppError(
                    ErrorCode.RATELIMIT,
                    "Alpha Vantage rate limit exceeded",
                )
            )

        try:
            params = {
                "function": "EARNINGS_CALENDAR",
                "horizon": horizon,
                "apikey": self.api_key,
            }

            if symbol:
                params["symbol"] = symbol

            response = requests.get(
                self.base_url, params=params, timeout=self.timeout
            )
            response.raise_for_status()

            # Check response size to prevent OOM
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {content_length} bytes",
                    )
                )

            # Also check actual response size
            if len(response.content) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            # Parse CSV response
            lines = response.text.strip().split('\n')
            if not lines or len(lines) < 2:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        "Empty earnings calendar response",
                    )
                )

            # Parse header and data
            # CSV format: symbol,name,reportDate,fiscalDateEnding,estimate,currency
            header = lines[0].split(',')
            results = []

            # Find column indices from header (robust against column order changes)
            try:
                symbol_idx = header.index('symbol')
                report_date_idx = header.index('reportDate')
            except ValueError:
                # Fallback to hardcoded indices if header doesn't match
                logger.warning("CSV header doesn't match expected format, using fallback indices")
                symbol_idx = 0
                report_date_idx = 2

            for line in lines[1:]:
                try:
                    fields = line.split(',')
                    if len(fields) < 3:
                        continue

                    ticker = fields[symbol_idx].strip()
                    report_date = date.fromisoformat(fields[report_date_idx].strip())

                    # Alpha Vantage CSV doesn't include timing (BMO/AMC) info
                    # Default to UNKNOWN - timing should be fetched from another source if needed
                    timing = EarningsTiming.UNKNOWN

                    results.append((ticker, report_date, timing))

                except (ValueError, IndexError) as e:
                    logger.debug(f"Skipping malformed earnings line: {e}")
                    continue

            if not results:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No earnings found for {symbol or 'all'}",
                    )
                )

            logger.info(f"Fetched {len(results)} earnings events")
            return Ok(results)

        except requests.exceptions.Timeout:
            return Err(
                AppError(ErrorCode.TIMEOUT, "Alpha Vantage request timeout")
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"Alpha Vantage request error: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def get_daily_prices(
        self,
        symbol: str,
        outputsize: str = "compact",
    ) -> Result[List[Tuple[date, Money, Money, Money, Money, int]], AppError]:
        """
        Get daily price history.

        Args:
            symbol: Stock ticker
            outputsize: "compact" (100 days) or "full" (20+ years)

        Returns:
            Result with list of (date, open, high, low, close, volume) tuples
        """
        # Rate limit check (blocking mode - wait for token)
        if self.rate_limiter and not self.rate_limiter.acquire(blocking=True):
            return Err(
                AppError(
                    ErrorCode.RATELIMIT,
                    "Alpha Vantage rate limit exceeded",
                )
            )

        try:
            params = {
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": outputsize,
                "apikey": self.api_key,
            }

            response = requests.get(
                self.base_url, params=params, timeout=self.timeout
            )
            response.raise_for_status()

            # Check response size to prevent OOM
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {content_length} bytes",
                    )
                )

            # Also check actual response size
            if len(response.content) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            data = response.json()

            # Check for API error messages
            if "Error Message" in data:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"Alpha Vantage error: {data['Error Message']}",
                    )
                )

            if "Note" in data:
                # Rate limit message
                return Err(
                    AppError(
                        ErrorCode.RATELIMIT,
                        "Alpha Vantage rate limit (API message)",
                    )
                )

            time_series = data.get("Time Series (Daily)", {})
            if not time_series:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No price data for {symbol}",
                    )
                )

            results = []
            for date_str, values in time_series.items():
                try:
                    price_date = date.fromisoformat(date_str)
                    open_price = Money(float(values["1. open"]))
                    high_price = Money(float(values["2. high"]))
                    low_price = Money(float(values["3. low"]))
                    close_price = Money(float(values["4. close"]))
                    volume = int(values["6. volume"])

                    results.append(
                        (
                            price_date,
                            open_price,
                            high_price,
                            low_price,
                            close_price,
                            volume,
                        )
                    )

                except (KeyError, ValueError) as e:
                    logger.debug(f"Skipping malformed price data: {e}")
                    continue

            # Sort by date descending
            results.sort(key=lambda x: x[0], reverse=True)

            logger.info(f"Fetched {len(results)} daily prices for {symbol}")
            return Ok(results)

        except requests.exceptions.Timeout:
            return Err(
                AppError(ErrorCode.TIMEOUT, "Alpha Vantage request timeout")
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"Alpha Vantage request error: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def calculate_earnings_move(
        self,
        symbol: str,
        earnings_date: date,
        daily_prices: Optional[
            List[Tuple[date, Money, Money, Money, Money, int]]
        ] = None,
    ) -> Result[HistoricalMove, AppError]:
        """
        Calculate historical move for earnings date.

        Args:
            symbol: Stock ticker
            earnings_date: Date of earnings announcement
            daily_prices: Optional pre-fetched prices (to avoid rate limits)

        Returns:
            Result with HistoricalMove
        """
        # Get prices if not provided
        if daily_prices is None:
            prices_result = self.get_daily_prices(symbol, outputsize="full")
            if prices_result.is_err:
                return Err(prices_result.error)
            daily_prices = prices_result.value

        # Find earnings day and previous day
        earnings_data = None
        prev_data = None

        for i, (price_date, open_px, high, low, close, volume) in enumerate(
            daily_prices
        ):
            if price_date == earnings_date:
                earnings_data = (open_px, high, low, close, volume)

                # Find previous trading day
                if i + 1 < len(daily_prices):
                    prev_data = daily_prices[i + 1]
                break

        if not earnings_data or not prev_data:
            return Err(
                AppError(
                    ErrorCode.NODATA,
                    f"No price data for {symbol} on {earnings_date}",
                )
            )

        # Unpack data
        earnings_open, earnings_high, earnings_low, earnings_close, volume_earnings = (
            earnings_data
        )
        (
            prev_date,
            prev_open,
            prev_high,
            prev_low,
            prev_close,
            volume_before,
        ) = prev_data

        # Calculate moves
        if prev_close.amount == 0:
            return Err(
                AppError(
                    ErrorCode.INVALID,
                    f"Previous close is zero for {symbol}",
                )
            )

        # Intraday move: high-low range as % of prev close
        intraday_range = earnings_high.amount - earnings_low.amount
        intraday_move_pct = Percentage(
            float(intraday_range / prev_close.amount * 100)
        )

        # Gap move: open vs prev close
        gap_move = earnings_open.amount - prev_close.amount
        gap_move_pct = Percentage(
            float(gap_move / prev_close.amount * 100)
        )

        # Close move: close vs prev close
        close_move = earnings_close.amount - prev_close.amount
        close_move_pct = Percentage(
            float(close_move / prev_close.amount * 100)
        )

        move = HistoricalMove(
            ticker=symbol,
            earnings_date=earnings_date,
            prev_close=prev_close,
            earnings_open=earnings_open,
            earnings_high=earnings_high,
            earnings_low=earnings_low,
            earnings_close=earnings_close,
            intraday_move_pct=intraday_move_pct,
            gap_move_pct=gap_move_pct,
            close_move_pct=close_move_pct,
            volume_before=volume_before,
            volume_earnings=volume_earnings,
        )

        logger.debug(
            f"{symbol} {earnings_date}: {intraday_move_pct} intraday move"
        )
        return Ok(move)
