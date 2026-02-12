# cloud/tests/test_implied_move.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.domain.implied_move import (
    calculate_implied_move,
    find_atm_straddle,
    fetch_real_implied_move,
    get_implied_move_with_fallback,
    IMPLIED_MOVE_FALLBACK_MULTIPLIER,
)

def test_calculate_implied_move_basic():
    """Implied move = straddle_price / stock_price * 100."""
    result = calculate_implied_move(
        stock_price=100.0,
        call_price=5.0,
        put_price=4.5,
    )
    # Straddle = cloud + 4.5 = 9.5
    # Implied move = 9.5 / 100 * 100 = 9.5%
    assert result["implied_move_pct"] == 9.5
    assert result["straddle_price"] == 9.5

def test_calculate_implied_move_with_iv():
    """Include IV in result if provided."""
    result = calculate_implied_move(
        stock_price=100.0,
        call_price=5.0,
        put_price=4.5,
        call_iv=0.45,
        put_iv=0.42,
    )
    assert result["avg_iv"] == pytest.approx(0.435, rel=0.01)

def test_find_atm_straddle():
    """Find ATM options from chain."""
    chain = [
        {"strike": 95.0, "option_type": "call", "bid": 8.0, "ask": 8.5},
        {"strike": 95.0, "option_type": "put", "bid": 2.5, "ask": 3.0},
        {"strike": 100.0, "option_type": "call", "bid": 5.0, "ask": 5.5},
        {"strike": 100.0, "option_type": "put", "bid": 4.0, "ask": 4.5},
        {"strike": 105.0, "option_type": "call", "bid": 2.5, "ask": 3.0},
        {"strike": 105.0, "option_type": "put", "bid": 7.0, "ask": 7.5},
    ]

    call, put = find_atm_straddle(chain, stock_price=101.0)

    assert call["strike"] == 100.0
    assert put["strike"] == 100.0

def test_find_atm_straddle_empty_chain():
    """Empty chain returns None."""
    call, put = find_atm_straddle([], stock_price=100.0)
    assert call is None
    assert put is None


# --- Tests for fetch_real_implied_move ---

@pytest.fixture
def mock_tradier():
    """Create a mock TradierClient."""
    tradier = MagicMock()
    tradier.get_quote = AsyncMock()
    tradier.get_expirations = AsyncMock()
    tradier.get_options_chain = AsyncMock()
    return tradier


@pytest.fixture
def sample_options_chain():
    """Sample options chain with ATM straddle."""
    return [
        {"strike": 95.0, "option_type": "call", "bid": 8.0, "ask": 8.5},
        {"strike": 95.0, "option_type": "put", "bid": 2.5, "ask": 3.0},
        {"strike": 100.0, "option_type": "call", "bid": 5.0, "ask": 5.5},
        {"strike": 100.0, "option_type": "put", "bid": 4.0, "ask": 4.5},
        {"strike": 105.0, "option_type": "call", "bid": 2.5, "ask": 3.0},
        {"strike": 105.0, "option_type": "put", "bid": 7.0, "ask": 7.5},
    ]


@pytest.mark.asyncio
async def test_fetch_real_implied_move_success(mock_tradier, sample_options_chain):
    """Successful fetch with real options data."""
    mock_tradier.get_quote.return_value = {"last": 100.0}
    mock_tradier.get_expirations.return_value = ["2025-01-15", "2025-01-17", "2025-01-24"]
    mock_tradier.get_options_chain.return_value = sample_options_chain

    result = await fetch_real_implied_move(
        mock_tradier, "AAPL", earnings_date="2025-01-16"
    )

    assert result["used_real_data"] is True
    assert result["implied_move_pct"] is not None
    assert result["atm_strike"] == 100.0
    assert result["expiration"] == "2025-01-17"
    assert result["price"] == 100.0
    assert result["error"] is None


@pytest.mark.asyncio
async def test_fetch_real_implied_move_with_price_provided(mock_tradier, sample_options_chain):
    """When price is provided, skip quote fetch."""
    mock_tradier.get_expirations.return_value = ["2025-01-17"]
    mock_tradier.get_options_chain.return_value = sample_options_chain

    result = await fetch_real_implied_move(
        mock_tradier, "AAPL", earnings_date="2025-01-16", price=100.0
    )

    assert result["used_real_data"] is True
    assert result["price"] == 100.0
    mock_tradier.get_quote.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_real_implied_move_no_price(mock_tradier):
    """Handle missing price."""
    mock_tradier.get_quote.return_value = {}

    result = await fetch_real_implied_move(
        mock_tradier, "AAPL", earnings_date="2025-01-16"
    )

    assert result["used_real_data"] is False
    assert result["implied_move_pct"] is None
    assert result["error"] == "No price available"


@pytest.mark.asyncio
async def test_fetch_real_implied_move_no_expiration(mock_tradier):
    """Handle no expiration after earnings."""
    mock_tradier.get_quote.return_value = {"last": 100.0}
    mock_tradier.get_expirations.return_value = ["2025-01-10", "2025-01-12"]

    result = await fetch_real_implied_move(
        mock_tradier, "AAPL", earnings_date="2025-01-16"
    )

    assert result["used_real_data"] is False
    assert result["error"] == "No expiration after earnings date"


@pytest.mark.asyncio
async def test_fetch_real_implied_move_empty_chain(mock_tradier):
    """Handle empty options chain."""
    mock_tradier.get_quote.return_value = {"last": 100.0}
    mock_tradier.get_expirations.return_value = ["2025-01-17"]
    mock_tradier.get_options_chain.return_value = []

    result = await fetch_real_implied_move(
        mock_tradier, "AAPL", earnings_date="2025-01-16"
    )

    assert result["used_real_data"] is False
    assert result["error"] == "Empty or invalid options chain"


@pytest.mark.asyncio
async def test_fetch_real_implied_move_invalid_chain(mock_tradier):
    """Handle invalid chain (not a list)."""
    mock_tradier.get_quote.return_value = {"last": 100.0}
    mock_tradier.get_expirations.return_value = ["2025-01-17"]
    mock_tradier.get_options_chain.return_value = {"error": "no data"}

    result = await fetch_real_implied_move(
        mock_tradier, "AAPL", earnings_date="2025-01-16"
    )

    assert result["used_real_data"] is False
    assert result["error"] == "Empty or invalid options chain"


@pytest.mark.asyncio
async def test_fetch_real_implied_move_api_exception(mock_tradier):
    """Handle API exception gracefully."""
    mock_tradier.get_quote.side_effect = Exception("Network error")

    result = await fetch_real_implied_move(
        mock_tradier, "AAPL", earnings_date="2025-01-16"
    )

    assert result["used_real_data"] is False
    assert "Network error" in result["error"]


@pytest.mark.asyncio
async def test_fetch_real_implied_move_no_atm_straddle(mock_tradier):
    """Handle chain without ATM straddle (calls only)."""
    chain_calls_only = [
        {"strike": 100.0, "option_type": "call", "bid": 5.0, "ask": 5.5},
    ]
    mock_tradier.get_quote.return_value = {"last": 100.0}
    mock_tradier.get_expirations.return_value = ["2025-01-17"]
    mock_tradier.get_options_chain.return_value = chain_calls_only

    result = await fetch_real_implied_move(
        mock_tradier, "AAPL", earnings_date="2025-01-16"
    )

    assert result["used_real_data"] is False
    assert result["error"] == "Could not calculate implied move from chain"


# --- Tests for get_implied_move_with_fallback ---

def test_get_implied_move_with_fallback_real_data():
    """Return real data when available."""
    real_result = {
        "implied_move_pct": 8.5,
        "used_real_data": True,
    }
    historical_avg = 5.0

    implied_move, used_real = get_implied_move_with_fallback(real_result, historical_avg)

    assert implied_move == 8.5
    assert used_real is True


def test_get_implied_move_with_fallback_no_real_data():
    """Fall back to estimate when used_real_data is False."""
    real_result = {
        "implied_move_pct": None,
        "used_real_data": False,
        "error": "No price available",
    }
    historical_avg = 5.0

    implied_move, used_real = get_implied_move_with_fallback(real_result, historical_avg)

    assert implied_move == historical_avg * IMPLIED_MOVE_FALLBACK_MULTIPLIER
    assert implied_move == 7.5  # cloud * 1.5
    assert used_real is False


def test_get_implied_move_with_fallback_none_implied_move():
    """Fall back when implied_move_pct is None even if used_real_data True."""
    real_result = {
        "implied_move_pct": None,
        "used_real_data": True,  # Anomalous state
    }
    historical_avg = 6.0

    implied_move, used_real = get_implied_move_with_fallback(real_result, historical_avg)

    assert implied_move == 9.0  # agents * 1.5
    assert used_real is False


def test_fallback_multiplier_value():
    """Verify fallback multiplier constant."""
    assert IMPLIED_MOVE_FALLBACK_MULTIPLIER == 1.5
