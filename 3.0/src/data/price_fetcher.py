"""
Price data fetcher using yfinance (free).

Fetches historical OHLCV data for volatility feature calculation.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, timedelta
from typing import Optional, Dict
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


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
    """

    def __init__(self, cache_days: int = 1):
        self._cache: Dict[str, pd.DataFrame] = {}
        self._cache_dates: Dict[str, date] = {}
        self.cache_days = cache_days

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

        # Check cache
        if cache_key in self._cache:
            cached_date = self._cache_dates.get(cache_key)
            if cached_date and (as_of_date - cached_date).days <= self.cache_days:
                return self._cache[cache_key]

        try:
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
                df[f'sma_{window}'] = df['close'].rolling(window).mean()
                df[f'std_{window}'] = df['close'].rolling(window).std()
                df[f'bb_width_{window}'] = (2 * df[f'std_{window}']) / df[f'sma_{window}']

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
                bb_width_10d=df['bb_width_10'].iloc[-1],
                bb_width_20d=df['bb_width_20'].iloc[-1],
                bb_width_50d=df['bb_width_50'].iloc[-1],
                hv_10d=hv_10d,
                hv_20d=hv_20d,
                hv_50d=hv_50d,
                hv_percentile=hv_percentile,
            )

        except Exception as e:
            logger.error(f"{ticker}: Failed to calculate volatility features - {e}")
            return None
