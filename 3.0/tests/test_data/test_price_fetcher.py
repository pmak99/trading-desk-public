"""
Unit tests for Price Fetcher.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from src.data.price_fetcher import PriceFetcher, VolatilityFeatures


@pytest.fixture
def mock_price_data():
    """Create mock price data DataFrame."""
    dates = pd.date_range(end=date.today(), periods=100, freq='D')
    np.random.seed(42)

    # Generate realistic price data
    base_price = 100.0
    returns = np.random.randn(100) * 0.02  # 2% daily volatility
    prices = base_price * np.exp(np.cumsum(returns))

    df = pd.DataFrame({
        'Open': prices * (1 + np.random.randn(100) * 0.005),
        'High': prices * (1 + abs(np.random.randn(100) * 0.01)),
        'Low': prices * (1 - abs(np.random.randn(100) * 0.01)),
        'Close': prices,
        'Volume': np.random.randint(1000000, 10000000, 100),
    }, index=dates)

    return df


@pytest.fixture
def price_fetcher():
    """Create a PriceFetcher instance."""
    return PriceFetcher(cache_days=1)


class TestPriceFetcher:
    """Tests for PriceFetcher class."""

    def test_init_default_cache(self):
        """Test default cache days."""
        fetcher = PriceFetcher()
        assert fetcher.cache_days == 1

    def test_init_custom_cache(self):
        """Test custom cache days."""
        fetcher = PriceFetcher(cache_days=7)
        assert fetcher.cache_days == 7

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_get_price_history_success(self, mock_ticker, mock_price_data, price_fetcher):
        """Test successful price history fetch."""
        mock_stock = MagicMock()
        mock_stock.history.return_value = mock_price_data
        mock_ticker.return_value = mock_stock

        df = price_fetcher.get_price_history('AAPL', days=100)

        assert df is not None
        assert len(df) == 100
        assert list(df.columns) == ['open', 'high', 'low', 'close', 'volume']

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_get_price_history_caching(self, mock_ticker, mock_price_data, price_fetcher):
        """Test price history caching."""
        mock_stock = MagicMock()
        mock_stock.history.return_value = mock_price_data
        mock_ticker.return_value = mock_stock

        # First call
        df1 = price_fetcher.get_price_history('AAPL', days=100)
        # Second call should use cache
        df2 = price_fetcher.get_price_history('AAPL', days=100)

        # yfinance should only be called once
        assert mock_ticker.call_count == 1
        assert df1 is df2  # Same object from cache

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_get_price_history_empty(self, mock_ticker, price_fetcher):
        """Test handling empty data."""
        mock_stock = MagicMock()
        mock_stock.history.return_value = pd.DataFrame()
        mock_ticker.return_value = mock_stock

        df = price_fetcher.get_price_history('INVALID', days=100)

        assert df is None

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_get_price_history_exception(self, mock_ticker, price_fetcher):
        """Test handling exceptions."""
        mock_ticker.side_effect = Exception("API error")

        df = price_fetcher.get_price_history('AAPL', days=100)

        assert df is None

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_calculate_volatility_features_success(self, mock_ticker, mock_price_data, price_fetcher):
        """Test volatility feature calculation."""
        mock_stock = MagicMock()
        mock_stock.history.return_value = mock_price_data
        mock_ticker.return_value = mock_stock

        features = price_fetcher.calculate_volatility_features('AAPL')

        assert features is not None
        assert isinstance(features, VolatilityFeatures)
        assert features.ticker == 'AAPL'

        # Check ATR values
        assert features.atr_10d > 0
        assert features.atr_20d > 0
        assert features.atr_50d > 0
        assert features.atr_10d_pct > 0

        # Check Bollinger Band widths
        assert features.bb_width_10d > 0
        assert features.bb_width_20d > 0
        assert features.bb_width_50d > 0

        # Check Historical Volatility
        assert features.hv_10d > 0
        assert features.hv_20d > 0
        assert features.hv_50d > 0

        # Check percentile
        assert 0 <= features.hv_percentile <= 100

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_calculate_volatility_features_insufficient_data(self, mock_ticker, price_fetcher):
        """Test volatility features with insufficient data."""
        # Only 30 days of data (need 50+)
        dates = pd.date_range(end=date.today(), periods=30, freq='D')
        df = pd.DataFrame({
            'Open': [100] * 30,
            'High': [101] * 30,
            'Low': [99] * 30,
            'Close': [100] * 30,
            'Volume': [1000000] * 30,
        }, index=dates)

        mock_stock = MagicMock()
        mock_stock.history.return_value = df
        mock_ticker.return_value = mock_stock

        features = price_fetcher.calculate_volatility_features('AAPL')

        assert features is None

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_calculate_volatility_features_no_data(self, mock_ticker, price_fetcher):
        """Test volatility features with no data."""
        mock_stock = MagicMock()
        mock_stock.history.return_value = pd.DataFrame()
        mock_ticker.return_value = mock_stock

        features = price_fetcher.calculate_volatility_features('INVALID')

        assert features is None

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_atr_calculation(self, mock_ticker, price_fetcher):
        """Test ATR calculation accuracy."""
        # Create simple test data with known ATR
        dates = pd.date_range(end=date.today(), periods=60, freq='D')
        df = pd.DataFrame({
            'Open': [100] * 60,
            'High': [102] * 60,  # High is always +2
            'Low': [98] * 60,    # Low is always -2
            'Close': [100] * 60,
            'Volume': [1000000] * 60,
        }, index=dates)

        mock_stock = MagicMock()
        mock_stock.history.return_value = df
        mock_ticker.return_value = mock_stock

        features = price_fetcher.calculate_volatility_features('TEST')

        assert features is not None
        # True range = High - Low = 4 for each day
        # ATR should be ~4
        assert 3.9 <= features.atr_10d <= 4.1
        assert 3.9 <= features.atr_20d <= 4.1
        assert 3.9 <= features.atr_50d <= 4.1

    @patch('src.data.price_fetcher.yf.Ticker')
    def test_atr_pct_calculation(self, mock_ticker, price_fetcher):
        """Test ATR percentage calculation."""
        dates = pd.date_range(end=date.today(), periods=60, freq='D')
        df = pd.DataFrame({
            'Open': [100] * 60,
            'High': [102] * 60,
            'Low': [98] * 60,
            'Close': [100] * 60,
            'Volume': [1000000] * 60,
        }, index=dates)

        mock_stock = MagicMock()
        mock_stock.history.return_value = df
        mock_ticker.return_value = mock_stock

        features = price_fetcher.calculate_volatility_features('TEST')

        assert features is not None
        # ATR is ~4, price is 100, so ATR% should be ~4%
        assert 3.9 <= features.atr_10d_pct <= 4.1


class TestVolatilityFeatures:
    """Tests for VolatilityFeatures dataclass."""

    def test_volatility_features_creation(self):
        """Test creating VolatilityFeatures instance."""
        features = VolatilityFeatures(
            ticker='AAPL',
            as_of_date=date(2024, 12, 6),
            atr_10d=2.5,
            atr_10d_pct=1.5,
            atr_20d=2.8,
            atr_20d_pct=1.6,
            atr_50d=3.0,
            atr_50d_pct=1.8,
            bb_width_10d=0.04,
            bb_width_20d=0.05,
            bb_width_50d=0.06,
            hv_10d=25.0,
            hv_20d=28.0,
            hv_50d=30.0,
            hv_percentile=65.0,
        )

        assert features.ticker == 'AAPL'
        assert features.atr_10d == 2.5
        assert features.hv_percentile == 65.0

    def test_all_fields_required(self):
        """Test all fields are required."""
        with pytest.raises(TypeError):
            VolatilityFeatures(ticker='AAPL')
