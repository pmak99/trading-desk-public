"""
Unit tests for Tradier API client.
"""

import pytest
import requests
from datetime import date
from unittest.mock import patch, MagicMock

from src.api.tradier import (
    TradierAPI,
    OptionQuote,
    OptionChain,
    ImpliedMove,
    retry_with_backoff,
)


@pytest.fixture
def mock_env_api_key():
    """Set up mock API key in environment."""
    with patch.dict('os.environ', {'TRADIER_API_KEY': 'test_api_key'}):
        yield


@pytest.fixture
def tradier_api(mock_env_api_key):
    """Create TradierAPI instance with mock key."""
    return TradierAPI()


class TestTradierAPIInit:
    """Tests for TradierAPI initialization."""

    def test_init_with_env_key(self, mock_env_api_key):
        """Test initialization with environment variable."""
        api = TradierAPI()
        assert api.api_key == 'test_api_key'
        assert api.base_url == "https://api.tradier.com/v1"

    def test_init_with_explicit_key(self):
        """Test initialization with explicit API key."""
        api = TradierAPI(api_key='explicit_key')
        assert api.api_key == 'explicit_key'

    def test_init_no_key_raises(self):
        """Test initialization without key raises error."""
        with patch.dict('os.environ', {}, clear=True):
            with pytest.raises(ValueError, match="TRADIER_API_KEY not set"):
                TradierAPI()


class TestRetryWithBackoff:
    """Tests for retry decorator."""

    def test_retry_success_first_try(self):
        """Test function succeeds on first try."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = success_func()
        assert result == "success"
        assert call_count == 1

    def test_retry_success_after_failures(self):
        """Test function succeeds after initial failures."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise requests.exceptions.RequestException("Network error")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count == 3

    def test_retry_exhausted(self):
        """Test all retries exhausted."""
        @retry_with_backoff(max_retries=2, base_delay=0.01)
        def always_fail():
            raise requests.exceptions.RequestException("Always fails")

        with pytest.raises(requests.exceptions.RequestException):
            always_fail()

    def test_retry_non_retryable_exception(self):
        """Test non-retryable exceptions are not retried."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def value_error_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("Not retryable")

        with pytest.raises(ValueError):
            value_error_func()

        assert call_count == 1  # Only called once


class TestGetStockPrice:
    """Tests for get_stock_price method."""

    @patch('src.api.tradier.requests.get')
    def test_get_stock_price_success(self, mock_get, tradier_api):
        """Test successful stock price fetch."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'quotes': {
                'quote': {'symbol': 'AAPL', 'last': 175.50}
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        price = tradier_api.get_stock_price('AAPL')

        assert price == 175.50
        mock_get.assert_called_once()

    @patch('src.api.tradier.requests.get')
    def test_get_stock_price_list_response(self, mock_get, tradier_api):
        """Test stock price with list response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'quotes': {
                'quote': [{'symbol': 'AAPL', 'last': 175.50}]
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        price = tradier_api.get_stock_price('AAPL')

        assert price == 175.50

    @patch('src.api.tradier.requests.get')
    def test_get_stock_price_no_data(self, mock_get, tradier_api):
        """Test stock price with no data."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'quotes': {}}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="No price data"):
            tradier_api.get_stock_price('INVALID')


class TestGetExpirations:
    """Tests for get_expirations method."""

    @patch('src.api.tradier.requests.get')
    def test_get_expirations_success(self, mock_get, tradier_api):
        """Test successful expiration fetch."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'expirations': {
                'date': ['2024-12-20', '2024-12-27', '2025-01-17']
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        expirations = tradier_api.get_expirations('AAPL')

        assert len(expirations) == 3
        assert expirations[0] == date(2024, 12, 20)
        assert expirations[2] == date(2025, 1, 17)

    @patch('src.api.tradier.requests.get')
    def test_get_expirations_single_date(self, mock_get, tradier_api):
        """Test expiration with single date."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'expirations': {
                'date': '2024-12-20'
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        expirations = tradier_api.get_expirations('AAPL')

        assert len(expirations) == 1
        assert expirations[0] == date(2024, 12, 20)

    @patch('src.api.tradier.requests.get')
    def test_get_expirations_empty(self, mock_get, tradier_api):
        """Test expirations with no data."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'expirations': {}}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        expirations = tradier_api.get_expirations('INVALID')

        assert expirations == []


class TestGetOptionChain:
    """Tests for get_option_chain method."""

    @patch('src.api.tradier.requests.get')
    def test_get_option_chain_success(self, mock_get, tradier_api):
        """Test successful option chain fetch."""
        # First call for stock price, second for options
        stock_response = MagicMock()
        stock_response.json.return_value = {
            'quotes': {'quote': {'symbol': 'AAPL', 'last': 175.0}}
        }
        stock_response.raise_for_status = MagicMock()

        options_response = MagicMock()
        options_response.json.return_value = {
            'options': {
                'option': [
                    {
                        'strike': 175.0,
                        'option_type': 'call',
                        'bid': 3.50,
                        'ask': 3.70,
                        'last': 3.60,
                        'volume': 1000,
                        'open_interest': 5000,
                        'greeks': {
                            'mid_iv': 0.35,
                            'delta': 0.52,
                            'gamma': 0.05,
                            'theta': -0.15,
                            'vega': 0.25,
                        }
                    },
                    {
                        'strike': 175.0,
                        'option_type': 'put',
                        'bid': 3.40,
                        'ask': 3.60,
                        'last': 3.50,
                        'volume': 800,
                        'open_interest': 4000,
                        'greeks': {
                            'mid_iv': 0.35,
                            'delta': -0.48,
                            'gamma': 0.05,
                            'theta': -0.15,
                            'vega': 0.25,
                        }
                    },
                ]
            }
        }
        options_response.raise_for_status = MagicMock()

        mock_get.side_effect = [stock_response, options_response]

        chain = tradier_api.get_option_chain('AAPL', date(2024, 12, 20))

        assert isinstance(chain, OptionChain)
        assert chain.ticker == 'AAPL'
        assert chain.stock_price == 175.0
        assert len(chain.calls) == 1
        assert len(chain.puts) == 1

        # Check call details
        call = chain.calls[0]
        assert call.strike == 175.0
        assert call.bid == 3.50
        assert call.ask == 3.70
        assert call.iv == 0.35
        assert call.delta == 0.52

    @patch('src.api.tradier.requests.get')
    def test_get_option_chain_sorted_by_strike(self, mock_get, tradier_api):
        """Test option chain is sorted by strike."""
        stock_response = MagicMock()
        stock_response.json.return_value = {
            'quotes': {'quote': {'symbol': 'AAPL', 'last': 175.0}}
        }
        stock_response.raise_for_status = MagicMock()

        options_response = MagicMock()
        options_response.json.return_value = {
            'options': {
                'option': [
                    {'strike': 180.0, 'option_type': 'call', 'bid': 1.0, 'ask': 1.2, 'last': 1.1, 'volume': 100, 'open_interest': 500},
                    {'strike': 170.0, 'option_type': 'call', 'bid': 6.0, 'ask': 6.2, 'last': 6.1, 'volume': 200, 'open_interest': 600},
                    {'strike': 175.0, 'option_type': 'call', 'bid': 3.0, 'ask': 3.2, 'last': 3.1, 'volume': 150, 'open_interest': 550},
                ]
            }
        }
        options_response.raise_for_status = MagicMock()

        mock_get.side_effect = [stock_response, options_response]

        chain = tradier_api.get_option_chain('AAPL', date(2024, 12, 20))

        # Calls should be sorted by strike
        assert chain.calls[0].strike == 170.0
        assert chain.calls[1].strike == 175.0
        assert chain.calls[2].strike == 180.0


class TestCalculateImpliedMove:
    """Tests for calculate_implied_move method."""

    @patch.object(TradierAPI, 'get_option_chain')
    def test_calculate_implied_move_success(self, mock_chain, tradier_api):
        """Test implied move calculation."""
        mock_chain.return_value = OptionChain(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            stock_price=175.0,
            calls=[
                OptionQuote(strike=175.0, option_type='call', bid=3.50, ask=3.70, last=3.60, volume=1000, open_interest=5000),
            ],
            puts=[
                OptionQuote(strike=175.0, option_type='put', bid=3.40, ask=3.60, last=3.50, volume=800, open_interest=4000),
            ],
        )

        result = tradier_api.calculate_implied_move('AAPL', date(2024, 12, 20))

        assert isinstance(result, ImpliedMove)
        assert result.ticker == 'AAPL'
        assert result.stock_price == 175.0
        assert result.atm_strike == 175.0

        # Call mid = (3.50 + 3.70) / 2 = 3.60
        # Put mid = (3.40 + 3.60) / 2 = 3.50
        # Straddle = 3.60 + 3.50 = 7.10
        assert abs(result.straddle_cost - 7.10) < 0.01

        # Implied move % = 7.10 / 175.0 * 100 = 4.057%
        assert abs(result.implied_move_pct - 4.057) < 0.01

        # Bounds
        assert result.upper_bound == 175.0 + 7.10
        assert result.lower_bound == 175.0 - 7.10

    @patch.object(TradierAPI, 'get_option_chain')
    def test_calculate_implied_move_finds_atm(self, mock_chain, tradier_api):
        """Test ATM strike selection."""
        mock_chain.return_value = OptionChain(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            stock_price=173.0,  # Between 170 and 175
            calls=[
                OptionQuote(strike=170.0, option_type='call', bid=5.0, ask=5.2, last=5.1, volume=100, open_interest=500),
                OptionQuote(strike=175.0, option_type='call', bid=2.0, ask=2.2, last=2.1, volume=100, open_interest=500),
            ],
            puts=[
                OptionQuote(strike=170.0, option_type='put', bid=2.0, ask=2.2, last=2.1, volume=100, open_interest=500),
                OptionQuote(strike=175.0, option_type='put', bid=4.0, ask=4.2, last=4.1, volume=100, open_interest=500),
            ],
        )

        result = tradier_api.calculate_implied_move('AAPL', date(2024, 12, 20))

        # 175 is closer to 173 than 170
        assert result.atm_strike == 175.0

    @patch.object(TradierAPI, 'get_option_chain')
    def test_calculate_implied_move_no_atm(self, mock_chain, tradier_api):
        """Test error when no ATM options found."""
        mock_chain.return_value = OptionChain(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            stock_price=175.0,
            calls=[
                OptionQuote(strike=175.0, option_type='call', bid=3.0, ask=3.2, last=3.1, volume=100, open_interest=500),
            ],
            puts=[],  # No puts at ATM strike
        )

        with pytest.raises(ValueError, match="No ATM options found"):
            tradier_api.calculate_implied_move('AAPL', date(2024, 12, 20))


class TestOptionQuote:
    """Tests for OptionQuote dataclass."""

    def test_option_quote_creation(self):
        """Test creating OptionQuote instance."""
        quote = OptionQuote(
            strike=175.0,
            option_type='call',
            bid=3.50,
            ask=3.70,
            last=3.60,
            volume=1000,
            open_interest=5000,
            iv=0.35,
            delta=0.52,
        )

        assert quote.strike == 175.0
        assert quote.option_type == 'call'
        assert quote.iv == 0.35

    def test_option_quote_optional_greeks(self):
        """Test OptionQuote with optional greeks."""
        quote = OptionQuote(
            strike=175.0,
            option_type='put',
            bid=3.40,
            ask=3.60,
            last=3.50,
            volume=800,
            open_interest=4000,
        )

        assert quote.iv is None
        assert quote.delta is None
        assert quote.gamma is None
        assert quote.theta is None
        assert quote.vega is None


class TestImpliedMove:
    """Tests for ImpliedMove dataclass."""

    def test_implied_move_creation(self):
        """Test creating ImpliedMove instance."""
        move = ImpliedMove(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            stock_price=175.0,
            atm_strike=175.0,
            call_mid=3.60,
            put_mid=3.50,
            straddle_cost=7.10,
            implied_move_pct=4.057,
            upper_bound=182.10,
            lower_bound=167.90,
        )

        assert move.ticker == 'AAPL'
        assert move.implied_move_pct == 4.057
        assert move.upper_bound == 182.10
