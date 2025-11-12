"""
Pytest configuration and fixtures for IV Crush 2.0 tests.
"""

import pytest
import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import Config
from src.container import Container
from src.domain.types import Money, Percentage, Strike, OptionQuote, OptionChain
from src.domain.enums import EarningsTiming


@pytest.fixture
def config():
    """Test configuration."""
    return Config.from_env()


@pytest.fixture
def container(config):
    """Test container with real configuration."""
    return Container(config)


@pytest.fixture
def test_db_path(tmp_path):
    """Temporary database path for testing."""
    return tmp_path / "test_iv_crush.db"


@pytest.fixture
def today():
    """Current date."""
    return date.today()


@pytest.fixture
def tomorrow(today):
    """Tomorrow's date."""
    return today + timedelta(days=1)


@pytest.fixture
def next_week(today):
    """Date one week from today."""
    return today + timedelta(days=7)


# ============================================================================
# Mock Data Fixtures
# ============================================================================


@pytest.fixture
def sample_money():
    """Sample Money value."""
    return Money(100.50)


@pytest.fixture
def sample_percentage():
    """Sample Percentage value."""
    return Percentage(5.0)


@pytest.fixture
def sample_strike():
    """Sample Strike value."""
    return Strike(100.0)


@pytest.fixture
def sample_option_quote():
    """Sample OptionQuote."""
    return OptionQuote(
        bid=Money(2.50),
        ask=Money(2.60),
        implied_volatility=Percentage(35.0),
        open_interest=500,
        volume=100,
    )


@pytest.fixture
def sample_option_chain(sample_strike, sample_option_quote):
    """Sample OptionChain with basic data."""
    strikes = [
        Strike(95.0),
        Strike(100.0),
        Strike(105.0),
    ]

    calls = {
        strikes[0]: OptionQuote(
            bid=Money(7.50), ask=Money(7.60), implied_volatility=Percentage(32.0)
        ),
        strikes[1]: sample_option_quote,
        strikes[2]: OptionQuote(
            bid=Money(1.50), ask=Money(1.60), implied_volatility=Percentage(33.0)
        ),
    }

    puts = {
        strikes[0]: OptionQuote(
            bid=Money(1.50), ask=Money(1.60), implied_volatility=Percentage(35.0)
        ),
        strikes[1]: sample_option_quote,
        strikes[2]: OptionQuote(
            bid=Money(7.50), ask=Money(7.60), implied_volatility=Percentage(36.0)
        ),
    }

    return OptionChain(
        ticker="AAPL",
        expiration=date.today() + timedelta(days=7),
        stock_price=Money(100.0),
        calls=calls,
        puts=puts,
    )


# ============================================================================
# Mock Provider Fixtures
# ============================================================================


class MockOptionsProvider:
    """Mock options data provider for testing."""

    def __init__(self):
        self.stock_prices = {}
        self.option_chains = {}

    def set_stock_price(self, ticker: str, price: Money):
        """Set mock stock price."""
        self.stock_prices[ticker] = price

    def set_option_chain(self, ticker: str, expiration: date, chain: OptionChain):
        """Set mock option chain."""
        key = (ticker, expiration)
        self.option_chains[key] = chain

    def get_stock_price(self, ticker: str):
        """Get mock stock price."""
        from src.domain.errors import Ok, Err, AppError, ErrorCode

        if ticker in self.stock_prices:
            return Ok(self.stock_prices[ticker])
        return Err(AppError(ErrorCode.NODATA, f"No price for {ticker}"))

    def get_option_chain(self, ticker: str, expiration: date):
        """Get mock option chain."""
        from src.domain.errors import Ok, Err, AppError, ErrorCode

        key = (ticker, expiration)
        if key in self.option_chains:
            return Ok(self.option_chains[key])
        return Err(AppError(ErrorCode.NODATA, f"No chain for {ticker}"))


@pytest.fixture
def mock_options_provider():
    """Mock options provider."""
    return MockOptionsProvider()


# ============================================================================
# Utility Functions for Tests
# ============================================================================


def assert_money_equal(m1: Money, m2: Money, tolerance: float = 0.01):
    """Assert two Money values are equal within tolerance."""
    assert abs(float(m1.amount) - float(m2.amount)) < tolerance


def assert_percentage_equal(p1: Percentage, p2: Percentage, tolerance: float = 0.01):
    """Assert two Percentage values are equal within tolerance."""
    assert abs(p1.value - p2.value) < tolerance
