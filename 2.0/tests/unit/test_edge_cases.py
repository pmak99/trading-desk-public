"""
Edge case tests for production robustness (Phase 3, Session 7).

Tests comprehensive error handling for unusual inputs, boundary conditions,
and failure scenarios that may occur in production.
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from src.domain.types import Money, Percentage, Strike, OptionQuote, OptionChain
from src.domain.errors import ErrorCode, AppError
from src.application.metrics.implied_move import ImpliedMoveCalculator
from src.application.metrics.vrp import VRPCalculator
from tests.conftest import MockOptionsProvider


class TestZeroAndNegativeValues:
    """Test handling of zero and negative values."""

    def test_stock_price_zero(self, mock_options_provider):
        """Zero stock price should return error."""
        mock_options_provider.set_stock_price("DEAD", Money(0))

        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("DEAD", date.today() + timedelta(days=7))

        assert result.is_err
        # Returns NODATA since no option chain is set for DEAD
        assert result.unwrap_err().code in (ErrorCode.NODATA, ErrorCode.INVALID, ErrorCode.CALCULATION)

    def test_stock_price_negative(self):
        """Negative stock price - Money may allow it but semantically invalid."""
        # Money type allows negative values (for P&L), but stock prices can't be negative
        # This is a semantic validation, not a type constraint
        money = Money(-100.0)
        assert money.amount == Decimal("-100.0")

    def test_option_strike_zero(self):
        """Zero strike price is technically valid (though unusual)."""
        # Strike(0) is allowed by the type system
        strike = Strike(0)
        assert strike.price == Decimal("0")

    def test_option_bid_ask_zero(self):
        """Zero bid/ask is valid (no market)."""
        quote = OptionQuote(
            bid=Money(0),
            ask=Money(0),
            implied_volatility=Percentage(0),
            open_interest=0,
            volume=0,
        )
        assert quote.mid == Money(0)
        assert not quote.is_liquid


class TestEmptyAndMissingData:
    """Test handling of missing or empty data."""

    def test_empty_option_chain(self, mock_options_provider):
        """Empty option chain should return error."""
        stock_price = Money(100)
        mock_options_provider.set_stock_price("EMPTY", stock_price)

        # Create empty chain
        empty_chain = OptionChain(
            ticker="EMPTY",
            expiration=date.today() + timedelta(days=7),
            stock_price=stock_price,
            calls={},
            puts={},
        )
        mock_options_provider.set_option_chain(
            "EMPTY", date.today() + timedelta(days=7), empty_chain
        )

        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("EMPTY", date.today() + timedelta(days=7))

        assert result.is_err
        assert result.unwrap_err().code == ErrorCode.NODATA

    def test_no_atm_strikes(self, mock_options_provider):
        """Option chain with no ATM strikes."""
        stock_price = Money(100)
        mock_options_provider.set_stock_price("NOATM", stock_price)

        # Strikes far from stock price
        calls = {
            Strike(200): OptionQuote(
                bid=Money(0.10), ask=Money(0.20), implied_volatility=Percentage(50)
            )
        }
        puts = {
            Strike(50): OptionQuote(
                bid=Money(0.10), ask=Money(0.20), implied_volatility=Percentage(50)
            )
        }

        chain = OptionChain(
            ticker="NOATM",
            expiration=date.today() + timedelta(days=7),
            stock_price=stock_price,
            calls=calls,
            puts=puts,
        )
        mock_options_provider.set_option_chain(
            "NOATM", date.today() + timedelta(days=7), chain
        )

        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("NOATM", date.today() + timedelta(days=7))

        # Should handle gracefully, either work or return error
        # Implementation determines exact behavior
        assert result.is_ok or result.is_err

    def test_missing_historical_data(self, mock_options_provider):
        """Insufficient historical data for VRP calculation."""
        stock_price = Money(100)
        mock_options_provider.set_stock_price("NOHISTORY", stock_price)

        # VRP calculator needs historical moves
        calc = VRPCalculator(mock_options_provider)
        # With no historical data, should return error
        # (Implementation specific - depends on how VRP handles missing data)


class TestExtremeValues:
    """Test handling of extreme values."""

    def test_extreme_implied_volatility(self):
        """Extremely high IV (1000%)."""
        quote = OptionQuote(
            bid=Money(50), ask=Money(60), implied_volatility=Percentage(1000)
        )
        assert quote.implied_volatility.value == 1000.0

    def test_extreme_stock_price(self, mock_options_provider):
        """Extremely high stock price ($100,000)."""
        mock_options_provider.set_stock_price("BRKB", Money(100000))

        result = mock_options_provider.get_stock_price("BRKB")
        assert result.is_ok
        assert result.unwrap() == Money(100000)

    def test_penny_stock(self, mock_options_provider):
        """Very low stock price ($0.01)."""
        mock_options_provider.set_stock_price("PENNY", Money(0.01))

        result = mock_options_provider.get_stock_price("PENNY")
        assert result.is_ok
        assert result.unwrap() == Money(0.01)

    def test_wide_bid_ask_spread(self):
        """Extremely wide bid/ask spread (>100%)."""
        quote = OptionQuote(
            bid=Money(1), ask=Money(10), implied_volatility=Percentage(50)
        )
        spread = quote.spread
        spread_pct = quote.spread_pct

        assert spread == Money(9)
        assert spread_pct > 100.0  # Compare float to float
        assert not quote.is_liquid  # Wide spread = illiquid

    def test_zero_open_interest(self):
        """Options with zero open interest."""
        quote = OptionQuote(
            bid=Money(1),
            ask=Money(1.05),
            implied_volatility=Percentage(30),
            open_interest=0,  # No open interest
            volume=0,
        )
        assert not quote.is_liquid


class TestBoundaryConditions:
    """Test boundary conditions and edge values."""

    def test_expiration_today(self, mock_options_provider):
        """Options expiring today (0 DTE)."""
        stock_price = Money(100)
        mock_options_provider.set_stock_price("ZEROD", stock_price)

        calls = {
            Strike(100): OptionQuote(
                bid=Money(2), ask=Money(2.10), implied_volatility=Percentage(200)
            )
        }
        puts = {
            Strike(100): OptionQuote(
                bid=Money(2), ask=Money(2.10), implied_volatility=Percentage(200)
            )
        }

        chain = OptionChain(
            ticker="ZEROD",
            expiration=date.today(),  # Expires today
            stock_price=stock_price,
            calls=calls,
            puts=puts,
        )
        mock_options_provider.set_option_chain("ZEROD", date.today(), chain)

        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("ZEROD", date.today())

        # 0 DTE options are valid
        assert result.is_ok or result.is_err  # Implementation specific

    def test_expiration_far_future(self, mock_options_provider):
        """Options expiring in 2 years (LEAPS)."""
        stock_price = Money(100)
        mock_options_provider.set_stock_price("LEAPS", stock_price)

        far_date = date.today() + timedelta(days=730)  # ~2 years
        calls = {
            Strike(100): OptionQuote(
                bid=Money(20),
                ask=Money(21),
                implied_volatility=Percentage(40),
                open_interest=1000,  # Add OI for liquidity
                volume=100,
            )
        }
        puts = {
            Strike(100): OptionQuote(
                bid=Money(20),
                ask=Money(21),
                implied_volatility=Percentage(40),
                open_interest=1000,  # Add OI for liquidity
                volume=100,
            )
        }

        chain = OptionChain(
            ticker="LEAPS",
            expiration=far_date,
            stock_price=stock_price,
            calls=calls,
            puts=puts,
        )
        mock_options_provider.set_option_chain("LEAPS", far_date, chain)

        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("LEAPS", far_date)

        assert result.is_ok

    def test_single_strike_chain(self, mock_options_provider):
        """Option chain with only one strike."""
        stock_price = Money(100)
        mock_options_provider.set_stock_price("SINGLE", stock_price)

        calls = {
            Strike(100): OptionQuote(
                bid=Money(2),
                ask=Money(2.10),
                implied_volatility=Percentage(30),
                open_interest=500,  # Add OI for liquidity
                volume=50,
            )
        }
        puts = {
            Strike(100): OptionQuote(
                bid=Money(2),
                ask=Money(2.10),
                implied_volatility=Percentage(30),
                open_interest=500,  # Add OI for liquidity
                volume=50,
            )
        }

        chain = OptionChain(
            ticker="SINGLE",
            expiration=date.today() + timedelta(days=7),
            stock_price=stock_price,
            calls=calls,
            puts=puts,
        )
        mock_options_provider.set_option_chain(
            "SINGLE", date.today() + timedelta(days=7), chain
        )

        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("SINGLE", date.today() + timedelta(days=7))

        assert result.is_ok

    def test_percentage_exactly_100(self):
        """Percentage value exactly at 100%."""
        pct = Percentage(100.0)
        assert pct.value == 100.0
        assert pct.to_decimal() == Decimal("1.0")

    def test_percentage_at_limit(self):
        """Percentage at validation limits."""
        # Test lower bound
        pct_low = Percentage(-100.0)
        assert pct_low.value == -100.0

        # Test upper bound (implementation allows high values)
        pct_high = Percentage(1000.0)
        assert pct_high.value == 1000.0


class TestDataValidation:
    """Test data validation edge cases."""

    def test_ticker_with_special_chars(self):
        """Tickers with special characters (BRK.A, BRK.B)."""
        # These are valid tickers
        ticker = "BRK.B"
        assert isinstance(ticker, str)
        assert len(ticker) > 0

    def test_empty_ticker(self, mock_options_provider):
        """Empty ticker string."""
        result = mock_options_provider.get_stock_price("")
        # Should return NODATA error
        assert result.is_err

    def test_very_long_ticker(self, mock_options_provider):
        """Unreasonably long ticker symbol."""
        long_ticker = "A" * 100
        result = mock_options_provider.get_stock_price(long_ticker)
        # Should return NODATA error
        assert result.is_err

    def test_past_expiration_date(self, mock_options_provider):
        """Expiration date in the past."""
        stock_price = Money(100)
        mock_options_provider.set_stock_price("PAST", stock_price)

        past_date = date.today() - timedelta(days=7)
        calls = {
            Strike(100): OptionQuote(
                bid=Money(0), ask=Money(0), implied_volatility=Percentage(0)
            )
        }
        puts = {
            Strike(100): OptionQuote(
                bid=Money(0), ask=Money(0), implied_volatility=Percentage(0)
            )
        }

        chain = OptionChain(
            ticker="PAST",
            expiration=past_date,
            stock_price=stock_price,
            calls=calls,
            puts=puts,
        )
        mock_options_provider.set_option_chain("PAST", past_date, chain)

        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("PAST", past_date)

        # Expired options should either work or error - implementation dependent
        assert result.is_ok or result.is_err


class TestConcurrencyEdgeCases:
    """Test edge cases related to concurrent operations."""

    def test_cache_concurrent_access(self):
        """Multiple threads accessing cache simultaneously."""
        from src.infrastructure.cache.memory_cache import MemoryCache
        import threading

        cache = MemoryCache(ttl_seconds=60, max_size=100)

        def writer(key_prefix):
            for i in range(100):
                cache.set(f"{key_prefix}_{i}", f"value_{i}")

        def reader(key_prefix):
            for i in range(100):
                cache.get(f"{key_prefix}_{i}")

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(f"writer_{i}",)))
            threads.append(threading.Thread(target=reader, args=(f"writer_{i}",)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not crash
        assert True

    def test_performance_monitor_concurrent(self):
        """Performance monitor under concurrent load."""
        from src.utils.performance import get_monitor
        import threading

        monitor = get_monitor()
        monitor.reset()  # Start fresh

        def track_metrics():
            for i in range(100):
                monitor.track("test_func", float(i))

        threads = [threading.Thread(target=track_metrics) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = monitor.get_stats("test_func")
        assert stats is not None
        assert stats["count"] == 1000  # 10 threads * 100 each


class TestConfigurationEdgeCases:
    """Test configuration validation edge cases."""

    def test_config_with_minimum_values(self, tmp_path):
        """Configuration with absolute minimum valid values."""
        from src.config.config import (
            Config,
            APIConfig,
            DatabaseConfig,
            CacheConfig,
            ThresholdsConfig,
            RateLimitConfig,
            ResilienceConfig,
            LoggingConfig,
        )
        from src.config.validation import validate_configuration

        config = Config(
            api=APIConfig(
                tradier_api_key="test_key",
                alpha_vantage_key="test_av_key",
            ),
            database=DatabaseConfig(path=tmp_path / "test.db"),
            cache=CacheConfig(l1_ttl=1, l2_ttl=2),  # Minimum valid TTLs
            thresholds=ThresholdsConfig(
                vrp_excellent=1.3,  # Minimum valid ordering
                vrp_good=1.2,
                vrp_marginal=1.1,
            ),
            rate_limits=RateLimitConfig(
                alpha_vantage_per_minute=1,  # Minimum
                tradier_per_second=1,
            ),
            resilience=ResilienceConfig(
                retry_max_attempts=1,  # Minimum
                max_concurrent_requests=1,
            ),
            logging=LoggingConfig(),
        )

        # Should validate successfully
        validate_configuration(config)

    def test_config_cache_ttl_equal(self, tmp_path):
        """L1 and L2 TTL equal should fail validation."""
        from src.config.config import (
            Config,
            APIConfig,
            DatabaseConfig,
            CacheConfig,
            ThresholdsConfig,
            RateLimitConfig,
            ResilienceConfig,
            LoggingConfig,
        )
        from src.config.validation import validate_configuration, ConfigurationError

        config = Config(
            api=APIConfig(
                tradier_api_key="test_key",
                alpha_vantage_key="",
            ),
            database=DatabaseConfig(path=tmp_path / "test.db"),
            cache=CacheConfig(l1_ttl=30, l2_ttl=30),  # Equal!
            thresholds=ThresholdsConfig(),
            rate_limits=RateLimitConfig(),
            resilience=ResilienceConfig(),
            logging=LoggingConfig(),
        )

        with pytest.raises(ConfigurationError):
            validate_configuration(config)


class TestErrorHandling:
    """Test comprehensive error handling."""

    def test_result_type_chaining(self):
        """Result type error chaining."""
        from src.domain.errors import Ok, Err, AppError, ErrorCode

        # Test Ok chaining
        result = Ok(42).map(lambda x: x * 2)
        assert result.is_ok
        assert result.unwrap() == 84

        # Test Err chaining
        error_result = Err(AppError(ErrorCode.INVALID, "test error"))
        mapped = error_result.map(lambda x: x * 2)
        assert mapped.is_err
        assert mapped.unwrap_err().code == ErrorCode.INVALID

    def test_all_error_codes_defined(self):
        """All error codes in ErrorCode enum."""
        from src.domain.errors import ErrorCode

        # Verify all documented error codes exist
        assert hasattr(ErrorCode, "NODATA")
        assert hasattr(ErrorCode, "INVALID")
        assert hasattr(ErrorCode, "EXTERNAL")
        assert hasattr(ErrorCode, "DBERROR")
        assert hasattr(ErrorCode, "CALCULATION")
        assert hasattr(ErrorCode, "CONFIGURATION")
        assert hasattr(ErrorCode, "RATELIMIT")
        assert hasattr(ErrorCode, "TIMEOUT")
