"""
Pytest configuration and fixtures for IV Crush 2.0 tests.
"""

from datetime import date, timedelta
from hashlib import sha1

import pytest

from src.config.config import Config
from src.container import Container
from src.domain.types import Money, OptionChain, OptionQuote, Percentage, Strike


class VendorClock:
    """Virtual time for the Yahoo and Twelve Data gates: sleeping advances it."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.0)


@pytest.fixture(scope="session")
def _vendor_state(tmp_path_factory):
    return tmp_path_factory.mktemp("vendor-state")


@pytest.fixture(autouse=True)
def vendor_clock(monkeypatch, request, _vendor_state):
    """Every test gets fresh Yahoo and Twelve Data gates on a fake clock.

    The real gates are process-wide and sleep: a test that reaches a yfinance
    or Twelve Data call site would wait a second per Yahoo call, or a minute
    for a Twelve Data retry, and would inherit another test's backoff. The
    Twelve Data gate's daily count goes to a file of the test's own, never the
    host's real one, which the bot and record_moves spend from.
    """
    from src.utils import rate_limiter

    monkeypatch.setenv("TWELVE_DATA_USAGE_FILE",
                       str(_vendor_state / sha1(request.node.nodeid.encode()).hexdigest()))
    clock = VendorClock()
    monkeypatch.setattr(rate_limiter, "_gates", {
        vendor: factory(clock=clock.time, sleep=clock.sleep)
        for vendor, factory in rate_limiter._GATE_FACTORIES.items()
    })
    return clock


@pytest.fixture
def config():
    """Test configuration."""
    return Config.from_env()


@pytest.fixture
def container(config):
    """Test container with real configuration (validation skipped for tests)."""
    return Container(config, skip_validation=True)


@pytest.fixture
def test_db_path(tmp_path):
    """Temporary database path for testing."""
    return tmp_path / "test_iv_crush.db"


# Postgres fixtures live in common/pgtesting.py so scripts/tests/ can share
# the identical definitions -- the two suites have different rootdirs and
# cannot see each other's conftest.
from common.pgtesting import pg_pool, pg_schema  # noqa: F401,E402
from src.infrastructure.database.migrations.production_guard import (  # noqa: F401,E402
    no_migration_reaches_production,
)


@pytest.fixture
def today():
    """Fixed date for deterministic tests (Monday, non-holiday)."""
    return date(2026, 6, 16)


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
        expiration=date(2026, 6, 23),
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
        from src.domain.errors import AppError, Err, ErrorCode, Ok

        if ticker in self.stock_prices:
            return Ok(self.stock_prices[ticker])
        return Err(AppError(ErrorCode.NODATA, f"No price for {ticker}"))

    def get_option_chain(self, ticker: str, expiration: date):
        """Get mock option chain."""
        from src.domain.errors import AppError, Err, ErrorCode, Ok

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
