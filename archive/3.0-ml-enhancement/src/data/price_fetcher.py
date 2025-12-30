"""
Price data fetcher using yfinance (free).

Fetches historical OHLCV data for volatility feature calculation.
"""

import time
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, timedelta
from typing import Optional, Dict
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

__all__ = [
    'VolatilityFeatures',
    'PriceFetcher',
]

# US market holidays (approximate - major ones)
US_MARKET_HOLIDAYS = {
    # 2024
    date(2024, 1, 1),   # New Year's Day
    date(2024, 1, 15),  # MLK Day
    date(2024, 2, 19),  # Presidents Day
    date(2024, 3, 29),  # Good Friday
    date(2024, 5, 27),  # Memorial Day
    date(2024, 6, 19),  # Juneteenth
    date(2024, 7, 4),   # Independence Day
    date(2024, 9, 2),   # Labor Day
    date(2024, 11, 28), # Thanksgiving
    date(2024, 12, 25), # Christmas
    # 2025
    date(2025, 1, 1),   # New Year's Day
    date(2025, 1, 20),  # MLK Day
    date(2025, 2, 17),  # Presidents Day
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 26),  # Memorial Day
    date(2025, 6, 19),  # Juneteenth
    date(2025, 7, 4),   # Independence Day
    date(2025, 9, 1),   # Labor Day
    date(2025, 11, 27), # Thanksgiving
    date(2025, 12, 25), # Christmas
}


def is_trading_day(d: date) -> bool:
    """Check if a date is a trading day (not weekend or holiday)."""
    # Weekend check
    if d.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False
    # Holiday check
    if d in US_MARKET_HOLIDAYS:
        return False
    return True


def get_previous_trading_day(d: date) -> date:
    """Get the most recent trading day on or before the given date."""
    while not is_trading_day(d):
        d = d - timedelta(days=1)
    return d


def trading_days_between(start: date, end: date) -> int:
    """Count trading days between two dates (exclusive of start, inclusive of end)."""
    count = 0
    current = start + timedelta(days=1)
    while current <= end:
        if is_trading_day(current):
            count += 1
        current += timedelta(days=1)
    return count


@dataclass
class VolatilityFeatures:
    """Calculated volatility features for a ticker."""
    ticker: str
    as_of_date: date

    # ATR (Average True Range)
    atr_10d: float
    atr_10d_pct: float
    atr_20d: float
    atr_20d_pct: float
    atr_50d: float
    atr_50d_pct: float

    # Bollinger Band Width
    bb_width_10d: float
    bb_width_20d: float
    bb_width_50d: float

    # Historical Volatility (annualized)
    hv_10d: float
    hv_20d: float
    hv_50d: float

    # Volatility regime
    hv_percentile: float  # Current HV vs last year


class PriceFetcher:
    """
    Fetch price data from Yahoo Finance (free).

    Includes rate limiting and trading-day-aware caching.
    """

    def __init__(
        self,
        cache_days: int = 1,  # Backward compatible name
        min_request_interval: float = 0.2,
    ):
        """
        Initialize price fetcher.

        Args:
            cache_days: Number of trading days before cache expires
            min_request_interval: Minimum seconds between API requests (rate limiting)
        """
        self._cache: Dict[str, pd.DataFrame] = {}
        self._cache_dates: Dict[str, date] = {}
        self.cache_days = cache_days  # Keep backward compatible name
        self.min_request_interval = min_request_interval
        self._last_request_time: float = 0.0

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_request_interval:
            sleep_time = self.min_request_interval - elapsed
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _is_cache_valid(self, cache_key: str, as_of_date: date) -> bool:
        """Check if cached data is still valid using trading days."""
        if cache_key not in self._cache:
            return False

        cached_date = self._cache_dates.get(cache_key)
        if not cached_date:
            return False

        # Count trading days between cached date and current date
        trading_days = trading_days_between(cached_date, as_of_date)
        return trading_days <= self.cache_days

    def get_price_history(
        self,
        ticker: str,
        days: int = 100,
        as_of_date: Optional[date] = None
    ) -> Optional[pd.DataFrame]:
        """
        Get OHLCV price history for a ticker.

        Args:
            ticker: Stock symbol
            days: Number of trading days to fetch
            as_of_date: End date (default: today)

        Returns:
            DataFrame with columns: open, high, low, close, volume
        """
        as_of_date = as_of_date or date.today()
        cache_key = f"{ticker}_{days}"

        # Check cache with trading day logic
        if self._is_cache_valid(cache_key, as_of_date):
            return self._cache[cache_key]

        try:
            # Rate limit before making request
            self._rate_limit()

            # Fetch from yfinance
            # Add buffer days for weekends/holidays
            start_date = as_of_date - timedelta(days=int(days * 1.5) + 10)

            stock = yf.Ticker(ticker)
            df = stock.history(start=start_date, end=as_of_date + timedelta(days=1))

            if df.empty:
                logger.warning(f"{ticker}: No price data from yfinance")
                return None

            # Standardize column names
            df.columns = df.columns.str.lower()
            df = df[['open', 'high', 'low', 'close', 'volume']].copy()

            # Cache result
            self._cache[cache_key] = df
            self._cache_dates[cache_key] = as_of_date

            return df

        except Exception as e:
            logger.error(f"{ticker}: Failed to fetch price data - {e}")
            return None

    def calculate_volatility_features(
        self,
        ticker: str,
        as_of_date: Optional[date] = None
    ) -> Optional[VolatilityFeatures]:
        """
        Calculate volatility features from price data.

        Args:
            ticker: Stock symbol
            as_of_date: Date to calculate features as of

        Returns:
            VolatilityFeatures or None if insufficient data
        """
        as_of_date = as_of_date or date.today()

        # Need 100 days for 50-day calculations + buffer
        df = self.get_price_history(ticker, days=100, as_of_date=as_of_date)

        if df is None or len(df) < 50:
            logger.warning(f"{ticker}: Insufficient price data for volatility features")
            return None

        try:
            # Current price
            current_price = df['close'].iloc[-1]

            # Calculate True Range
            df['prev_close'] = df['close'].shift(1)
            df['tr'] = np.maximum(
                df['high'] - df['low'],
                np.maximum(
                    abs(df['high'] - df['prev_close']),
                    abs(df['low'] - df['prev_close'])
                )
            )

            # ATR calculations
            atr_10d = df['tr'].rolling(10).mean().iloc[-1]
            atr_20d = df['tr'].rolling(20).mean().iloc[-1]
            atr_50d = df['tr'].rolling(50).mean().iloc[-1]

            # Bollinger Band Width (width / middle band)
            for window in [10, 20, 50]:
                df[f'sma_{window}d'] = df['close'].rolling(window).mean()
                df[f'std_{window}d'] = df['close'].rolling(window).std()
                df[f'bb_width_{window}d'] = (2 * df[f'std_{window}d']) / df[f'sma_{window}d']

            # Historical Volatility (annualized)
            df['log_return'] = np.log(df['close'] / df['close'].shift(1))
            hv_10d = df['log_return'].rolling(10).std().iloc[-1] * np.sqrt(252) * 100
            hv_20d = df['log_return'].rolling(20).std().iloc[-1] * np.sqrt(252) * 100
            hv_50d = df['log_return'].rolling(50).std().iloc[-1] * np.sqrt(252) * 100

            # HV percentile (current 20d HV vs last year)
            hv_series = df['log_return'].rolling(20).std() * np.sqrt(252) * 100
            if len(hv_series.dropna()) > 50:
                current_hv = hv_series.iloc[-1]
                hv_percentile = (hv_series.dropna() < current_hv).mean() * 100
            else:
                hv_percentile = 50.0  # Default to median

            return VolatilityFeatures(
                ticker=ticker,
                as_of_date=as_of_date,
                atr_10d=atr_10d,
                atr_10d_pct=(atr_10d / current_price) * 100,
                atr_20d=atr_20d,
                atr_20d_pct=(atr_20d / current_price) * 100,
                atr_50d=atr_50d,
                atr_50d_pct=(atr_50d / current_price) * 100,
                bb_width_10d=df['bb_width_10d'].iloc[-1],
                bb_width_20d=df['bb_width_20d'].iloc[-1],
                bb_width_50d=df['bb_width_50d'].iloc[-1],
                hv_10d=hv_10d,
                hv_20d=hv_20d,
                hv_50d=hv_50d,
                hv_percentile=hv_percentile,
            )

        except Exception as e:
            logger.error(f"{ticker}: Failed to calculate volatility features - {e}")
            return None
