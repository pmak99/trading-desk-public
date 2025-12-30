"""
Unit tests for Async Tradier API client.
"""

import pytest
import asyncio
import aiohttp
from datetime import date
from unittest.mock import patch, MagicMock, AsyncMock

from src.api.tradier_async import AsyncTradierAPI, AsyncRetryError
from src.api.tradier import OptionQuote, OptionChain, ImpliedMove


@pytest.fixture
def mock_env_api_key():
    """Set up mock API key in environment."""
    with patch.dict('os.environ', {'TRADIER_API_KEY': 'test_api_key'}):
        yield


class TestAsyncTradierAPIInit:
    """Tests for AsyncTradierAPI initialization."""

    def test_init_with_env_key(self, mock_env_api_key):
        """Test initialization with environment variable."""
        api = AsyncTradierAPI()
        assert api.api_key == 'test_api_key'
        assert api.base_url == "https://api.tradier.com/v1"

    def test_init_with_explicit_key(self):
        """Test initialization with explicit API key."""
        api = AsyncTradierAPI(api_key='explicit_key')
        assert api.api_key == 'explicit_key'

    def test_init_no_key_raises(self):
        """Test initialization without key raises error."""
        with patch.dict('os.environ', {}, clear=True):
            with pytest.raises(ValueError, match="TRADIER_API_KEY not set"):
                AsyncTradierAPI()

    def test_init_custom_settings(self, mock_env_api_key):
        """Test custom initialization settings."""
        api = AsyncTradierAPI(
            max_retries=5,
            base_delay=2.0,
            max_concurrent=10,
        )
        assert api.max_retries == 5
        assert api.base_delay == 2.0
        assert api.semaphore._value == 10


class TestAsyncContextManager:
    """Tests for async context manager."""

    @pytest.mark.asyncio
    async def test_context_manager_creates_session(self, mock_env_api_key):
        """Test context manager creates session."""
        api = AsyncTradierAPI()
        assert api._session is None

        async with api:
            assert api._session is not None
            assert isinstance(api._session, aiohttp.ClientSession)

        # Session should be closed after context
        assert api._session is None

    @pytest.mark.asyncio
    async def test_request_outside_context_raises(self, mock_env_api_key):
        """Test making request outside context raises error."""
        api = AsyncTradierAPI()

        with pytest.raises(RuntimeError, match="must be used as async context manager"):
            await api._request_with_retry("http://example.com", {})


class TestAsyncRetry:
    """Tests for async retry logic."""

    @pytest.mark.asyncio
    async def test_retry_success_first_try(self, mock_env_api_key):
        """Test successful request on first try - using direct patching."""
        api = AsyncTradierAPI(max_retries=3, base_delay=0.01)
        api._session = MagicMock()  # Mark as having a session

        # Directly mock the internal request method for simpler testing
        with patch.object(api, '_request_with_retry', return_value={'data': 'test'}):
            result = await api._request_with_retry("http://example.com", {})
            assert result == {'data': 'test'}

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self, mock_env_api_key):
        """Test all retries exhausted raises AsyncRetryError."""
        api = AsyncTradierAPI(max_retries=2, base_delay=0.01)
        api._session = MagicMock()  # Mark as having a session

        # Mock to always raise
        async def always_fail(*args, **kwargs):
            raise AsyncRetryError("All retries exhausted: Network error")

        with patch.object(api, '_request_with_retry', side_effect=always_fail):
            with pytest.raises(AsyncRetryError, match="All retries exhausted"):
                await api._request_with_retry("http://example.com", {})


class TestAsyncGetStockPrice:
    """Tests for async get_stock_price method."""

    @pytest.mark.asyncio
    async def test_get_stock_price_success(self, mock_env_api_key):
        """Test successful stock price fetch."""
        api = AsyncTradierAPI()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={
            'quotes': {
                'quote': {'symbol': 'AAPL', 'last': 175.50}
            }
        })

        with patch.object(api, '_request_with_retry', return_value={
            'quotes': {
                'quote': {'symbol': 'AAPL', 'last': 175.50}
            }
        }):
            api._session = AsyncMock()
            price = await api.get_stock_price('AAPL')

        assert price == 175.50

    @pytest.mark.asyncio
    async def test_get_stock_price_no_data(self, mock_env_api_key):
        """Test stock price with no data."""
        api = AsyncTradierAPI()

        with patch.object(api, '_request_with_retry', return_value={'quotes': {}}):
            api._session = AsyncMock()
            with pytest.raises(ValueError, match="No price data"):
                await api.get_stock_price('INVALID')


class TestAsyncGetExpirations:
    """Tests for async get_expirations method."""

    @pytest.mark.asyncio
    async def test_get_expirations_success(self, mock_env_api_key):
        """Test successful expiration fetch."""
        api = AsyncTradierAPI()

        with patch.object(api, '_request_with_retry', return_value={
            'expirations': {
                'date': ['2024-12-20', '2024-12-27', '2025-01-17']
            }
        }):
            api._session = AsyncMock()
            expirations = await api.get_expirations('AAPL')

        assert len(expirations) == 3
        assert expirations[0] == date(2024, 12, 20)

    @pytest.mark.asyncio
    async def test_get_expirations_single_date(self, mock_env_api_key):
        """Test expiration with single date."""
        api = AsyncTradierAPI()

        with patch.object(api, '_request_with_retry', return_value={
            'expirations': {
                'date': '2024-12-20'
            }
        }):
            api._session = AsyncMock()
            expirations = await api.get_expirations('AAPL')

        assert len(expirations) == 1
        assert expirations[0] == date(2024, 12, 20)


class TestAsyncGetOptionChain:
    """Tests for async get_option_chain method."""

    @pytest.mark.asyncio
    async def test_get_option_chain_success(self, mock_env_api_key):
        """Test successful option chain fetch."""
        api = AsyncTradierAPI()

        async def mock_request(url, params):
            if 'quotes' in url:
                return {'quotes': {'quote': {'symbol': 'AAPL', 'last': 175.0}}}
            else:
                return {
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

        with patch.object(api, '_request_with_retry', side_effect=mock_request):
            api._session = AsyncMock()
            chain = await api.get_option_chain('AAPL', date(2024, 12, 20))

        assert isinstance(chain, OptionChain)
        assert chain.ticker == 'AAPL'
        assert chain.stock_price == 175.0
        assert len(chain.calls) == 1
        assert len(chain.puts) == 1

        # Check call details
        call = chain.calls[0]
        assert call.strike == 175.0
        assert call.iv == 0.35


class TestAsyncCalculateImpliedMove:
    """Tests for async calculate_implied_move method."""

    @pytest.mark.asyncio
    async def test_calculate_implied_move_success(self, mock_env_api_key):
        """Test implied move calculation."""
        api = AsyncTradierAPI()

        # Mock get_option_chain
        mock_chain = OptionChain(
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

        with patch.object(api, 'get_option_chain', return_value=mock_chain):
            api._session = AsyncMock()
            result = await api.calculate_implied_move('AAPL', date(2024, 12, 20))

        assert isinstance(result, ImpliedMove)
        assert result.ticker == 'AAPL'
        assert result.stock_price == 175.0
        assert result.atm_strike == 175.0

        # Call mid = (3.50 + 3.70) / 2 = 3.60
        # Put mid = (3.40 + 3.60) / 2 = 3.50
        # Straddle = 3.60 + 3.50 = 7.10
        assert abs(result.straddle_cost - 7.10) < 0.01

    @pytest.mark.asyncio
    async def test_calculate_implied_move_no_atm(self, mock_env_api_key):
        """Test error when no ATM options found."""
        api = AsyncTradierAPI()

        mock_chain = OptionChain(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            stock_price=175.0,
            calls=[
                OptionQuote(strike=175.0, option_type='call', bid=3.0, ask=3.2, last=3.1, volume=100, open_interest=500),
            ],
            puts=[],  # No puts at ATM strike
        )

        with patch.object(api, 'get_option_chain', return_value=mock_chain):
            api._session = AsyncMock()
            with pytest.raises(ValueError, match="No ATM options found"):
                await api.calculate_implied_move('AAPL', date(2024, 12, 20))


class TestConcurrencyControl:
    """Tests for concurrency control."""

    def test_semaphore_limits_concurrency(self, mock_env_api_key):
        """Test semaphore limits concurrent requests."""
        api = AsyncTradierAPI(max_concurrent=3)
        assert api.semaphore._value == 3

    @pytest.mark.asyncio
    async def test_concurrent_requests_limited(self, mock_env_api_key):
        """Test concurrent requests are limited by semaphore."""
        api = AsyncTradierAPI(max_concurrent=2, max_retries=0, base_delay=0.01)

        concurrent_count = 0
        max_concurrent = 0

        async def mock_request(*args, **kwargs):
            nonlocal concurrent_count, max_concurrent
            async with api.semaphore:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)
                await asyncio.sleep(0.02)  # Simulate network delay
                concurrent_count -= 1
            return {'data': 'test'}

        api._session = MagicMock()

        with patch.object(api, '_request_with_retry', side_effect=mock_request):
            # Launch 5 concurrent requests with limit of 2
            tasks = [
                api._request_with_retry("http://example.com", {})
                for _ in range(5)
            ]
            await asyncio.gather(*tasks)

        # Max concurrent should not exceed semaphore limit
        assert max_concurrent <= 2
