"""scripts/scan/filters.py: what can drop a ticker from a scan, and what cannot.

Market cap is the only filter, at ThresholdsConfig.min_market_cap_millions
(MIN_MARKET_CAP_MILLIONS, default $1,000M). Liquidity is looked up for display
and never filters: CLAUDE.md §2 sends a REJECT to the broker's ATM chain, which
needs the row to be shown. A market cap Yahoo cannot supply must not filter
either, or a Yahoo outage would empty every scan.
"""

from datetime import date
from unittest.mock import Mock

import pytest

from scripts.scan import filters
from src.config.config import ThresholdsConfig

EXPIRATION = date(2026, 9, 18)


@pytest.fixture
def container():
    c = Mock()
    c.config.thresholds = ThresholdsConfig()
    return c


@pytest.fixture
def market(monkeypatch):
    """Set the market cap and liquidity tier the two vendor lookups return."""
    seen = {"liquidity_checked": 0}

    def set_(cap_millions, tier="EXCELLENT"):
        monkeypatch.setattr(filters, "get_market_cap_millions", lambda ticker: cap_millions)

        def liquidity(ticker, expiration, container):
            seen["liquidity_checked"] += 1
            return tier != "REJECT", tier
        monkeypatch.setattr(filters, "check_liquidity_with_tier", liquidity)
        return seen
    return set_


def test_the_threshold_is_the_configured_one_billion(container):
    assert container.config.thresholds.min_market_cap_millions == 1000.0


def test_below_the_threshold_is_filtered_with_the_reason(container, market):
    market(999.0)
    should_filter, reason, tier, details = filters.should_filter_ticker("TINY", EXPIRATION, container)
    assert should_filter is True
    assert reason == "Market cap $999M < $1000M"
    assert (tier, details) == (None, None)


def test_exactly_at_the_threshold_passes(container, market):
    market(1000.0)
    assert filters.should_filter_ticker("EDGE", EXPIRATION, container)[0] is False


def test_the_threshold_follows_config_not_a_literal(container, market):
    container.config.thresholds = ThresholdsConfig(min_market_cap_millions=5000.0)
    market(4000.0)
    should_filter, reason, _, _ = filters.should_filter_ticker("MID", EXPIRATION, container)
    assert should_filter is True and "$5000M" in reason


def test_an_unknown_market_cap_does_not_filter(container, market):
    market(None)
    assert filters.should_filter_ticker("NOCAP", EXPIRATION, container)[0] is False


@pytest.mark.parametrize("tier", ["EXCELLENT", "GOOD", "WARNING", "REJECT", "REJECT (market closed)"])
def test_no_liquidity_tier_filters_and_the_tier_is_returned_for_display(container, market, tier):
    market(250_000.0, tier)
    should_filter, reason, got_tier, details = filters.should_filter_ticker("MU", EXPIRATION, container)
    assert (should_filter, reason, got_tier, details) == (False, None, tier, None)


def test_a_filtered_ticker_spends_no_chain_fetch_on_liquidity(container, market):
    seen = market(10.0)
    filters.should_filter_ticker("TINY", EXPIRATION, container)
    assert seen["liquidity_checked"] == 0


def test_the_concurrent_scanner_adapter_checks_both_and_returns_two_fields(container, market):
    seen = market(250_000.0, "REJECT")
    assert filters.filter_ticker_concurrent("MU", EXPIRATION, container) == (False, None)
    assert seen["liquidity_checked"] == 1
    market(12.0)
    assert filters.filter_ticker_concurrent("TINY", EXPIRATION, container) == (
        True, "Market cap $12M < $1000M")
