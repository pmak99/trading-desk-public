"""Pytest configuration and shared fixtures."""

import pytest
from datetime import datetime, timedelta


@pytest.fixture
def sample_earnings_date():
    """Return a sample earnings date in the future."""
    return datetime.now() + timedelta(days=7)


@pytest.fixture
def sample_ticker():
    """Return a sample ticker symbol."""
    return "NVDA"


@pytest.fixture
def sample_tickers():
    """Return a list of sample ticker symbols."""
    return ["NVDA", "TSLA", "AAPL"]
