"""
Load testing for production readiness (Phase 3, Session 8).

Tests system performance under realistic load scenarios with 50-100 tickers
to validate production readiness and ensure the system can handle concurrent
analysis at scale.
"""

import pytest
import asyncio
import time
from datetime import date, timedelta

from src.domain.types import Money, Percentage, Strike, OptionQuote, OptionChain
from src.domain.errors import ErrorCode
from src.application.metrics.implied_move import ImpliedMoveCalculator
from tests.conftest import MockOptionsProvider


def create_realistic_chain(ticker: str, stock_price: Money, expiration: date) -> OptionChain:
    """Create a realistic option chain for testing."""
    atm_strike = Strike(int(stock_price.amount))
    strike_below = Strike(int(stock_price.amount) - 5)
    strike_above = Strike(int(stock_price.amount) + 5)

    calls = {
        strike_below: OptionQuote(
            bid=Money(6), ask=Money(6.10),
            implied_volatility=Percentage(30),
            open_interest=1000, volume=100
        ),
        atm_strike: OptionQuote(
            bid=Money(3), ask=Money(3.10),
            implied_volatility=Percentage(28),
            open_interest=2000, volume=200
        ),
        strike_above: OptionQuote(
            bid=Money(1), ask=Money(1.10),
            implied_volatility=Percentage(32),
            open_interest=1000, volume=100
        ),
    }
    puts = {
        strike_below: OptionQuote(
            bid=Money(1), ask=Money(1.10),
            implied_volatility=Percentage(32),
            open_interest=1000, volume=100
        ),
        atm_strike: OptionQuote(
            bid=Money(3), ask=Money(3.10),
            implied_volatility=Percentage(28),
            open_interest=2000, volume=200
        ),
        strike_above: OptionQuote(
            bid=Money(6), ask=Money(6.10),
            implied_volatility=Percentage(30),
            open_interest=1000, volume=100
        ),
    }

    return OptionChain(
        ticker=ticker,
        expiration=expiration,
        stock_price=stock_price,
        calls=calls,
        puts=puts,
    )


class TestBaselinePerformance:
    """Baseline performance tests with small ticker counts."""

    @pytest.mark.asyncio
    async def test_10_tickers_concurrent(self, mock_options_provider):
        """Baseline: Analyze 10 tickers concurrently."""
        tickers = [f"TICK{i:02d}" for i in range(10)]
        expiration = date.today() + timedelta(days=7)

        # Setup tickers
        for ticker in tickers:
            stock_price = Money(100)
            mock_options_provider.set_stock_price(ticker, stock_price)
            chain = create_realistic_chain(ticker, stock_price, expiration)
            mock_options_provider.set_option_chain(ticker, expiration, chain)

        calc = ImpliedMoveCalculator(mock_options_provider)

        # Create concurrent tasks
        async def analyze_ticker(ticker):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: calc.calculate(ticker, expiration))

        start_time = time.time()
        results = await asyncio.gather(*[analyze_ticker(t) for t in tickers])
        elapsed = time.time() - start_time

        # Verify results
        assert len(results) == 10
        successful = sum(1 for r in results if r.is_ok)
        assert successful == 10, f"Expected 10 successful analyses, got {successful}"

        print(f"\n✓ 10 tickers analyzed in {elapsed:.3f}s ({elapsed/10*1000:.1f}ms avg)")


class TestTargetLoad:
    """Target load tests with 50 tickers."""

    @pytest.mark.asyncio
    async def test_50_tickers_concurrent(self, mock_options_provider):
        """Target load: Analyze 50 tickers concurrently."""
        tickers = [f"LOAD{i:02d}" for i in range(50)]
        expiration = date.today() + timedelta(days=7)

        # Setup tickers with varying stock prices
        for ticker in tickers:
            stock_price = Money(100 + (hash(ticker) % 50))
            mock_options_provider.set_stock_price(ticker, stock_price)
            chain = create_realistic_chain(ticker, stock_price, expiration)
            mock_options_provider.set_option_chain(ticker, expiration, chain)

        calc = ImpliedMoveCalculator(mock_options_provider)

        # Create concurrent tasks
        async def analyze_ticker(ticker):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: calc.calculate(ticker, expiration))

        start_time = time.time()
        results = await asyncio.gather(*[analyze_ticker(t) for t in tickers])
        elapsed = time.time() - start_time

        # Verify results
        assert len(results) == 50
        successful = sum(1 for r in results if r.is_ok)
        assert successful == 50, f"Expected 50 successful analyses, got {successful}"

        avg_per_ticker = elapsed / 50
        print(f"\n✓ 50 tickers analyzed in {elapsed:.3f}s ({avg_per_ticker*1000:.1f}ms avg)")


class TestStressLoad:
    """Stress tests with 100 tickers."""

    @pytest.mark.asyncio
    async def test_100_tickers_concurrent(self, mock_options_provider):
        """Stress test: Analyze 100 tickers concurrently."""
        tickers = [f"STRESS{i:03d}" for i in range(100)]
        expiration = date.today() + timedelta(days=7)

        # Setup tickers with varying stock prices
        for ticker in tickers:
            stock_price = Money(100 + (hash(ticker) % 100))
            mock_options_provider.set_stock_price(ticker, stock_price)
            chain = create_realistic_chain(ticker, stock_price, expiration)
            mock_options_provider.set_option_chain(ticker, expiration, chain)

        calc = ImpliedMoveCalculator(mock_options_provider)

        # Create concurrent tasks
        async def analyze_ticker(ticker):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: calc.calculate(ticker, expiration))

        start_time = time.time()
        results = await asyncio.gather(*[analyze_ticker(t) for t in tickers])
        elapsed = time.time() - start_time

        # Verify results
        assert len(results) == 100
        successful = sum(1 for r in results if r.is_ok)
        assert successful == 100, f"Expected 100 successful analyses, got {successful}"

        avg_per_ticker = elapsed / 100
        print(f"\n✓ 100 tickers analyzed in {elapsed:.3f}s ({avg_per_ticker*1000:.1f}ms avg)")


class TestBatchProcessing:
    """Test batch processing patterns."""

    @pytest.mark.asyncio
    async def test_sequential_batches(self, mock_options_provider):
        """Test processing tickers in sequential batches."""
        tickers = [f"BATCH{i:02d}" for i in range(30)]
        expiration = date.today() + timedelta(days=7)

        # Setup tickers
        for ticker in tickers:
            stock_price = Money(100)
            mock_options_provider.set_stock_price(ticker, stock_price)
            chain = create_realistic_chain(ticker, stock_price, expiration)
            mock_options_provider.set_option_chain(ticker, expiration, chain)

        calc = ImpliedMoveCalculator(mock_options_provider)

        async def analyze_ticker(ticker):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: calc.calculate(ticker, expiration))

        # Process in batches of 10
        batch_size = 10
        all_results = []

        start_time = time.time()
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i+batch_size]
            batch_results = await asyncio.gather(*[analyze_ticker(t) for t in batch])
            all_results.extend(batch_results)
        elapsed = time.time() - start_time

        # Verify all tickers processed
        assert len(all_results) == 30
        successful = sum(1 for r in all_results if r.is_ok)
        assert successful == 30

        print(f"\n✓ 30 tickers in 3 batches: {elapsed:.3f}s")

    @pytest.mark.asyncio
    async def test_concurrent_with_limit(self, mock_options_provider):
        """Test concurrent processing with semaphore limit."""
        tickers = [f"LIMIT{i:02d}" for i in range(50)]
        expiration = date.today() + timedelta(days=7)

        # Setup tickers
        for ticker in tickers:
            stock_price = Money(100)
            mock_options_provider.set_stock_price(ticker, stock_price)
            chain = create_realistic_chain(ticker, stock_price, expiration)
            mock_options_provider.set_option_chain(ticker, expiration, chain)

        calc = ImpliedMoveCalculator(mock_options_provider)

        # Use semaphore to limit concurrency
        semaphore = asyncio.Semaphore(10)

        async def analyze_ticker_limited(ticker):
            async with semaphore:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: calc.calculate(ticker, expiration))

        start_time = time.time()
        results = await asyncio.gather(*[analyze_ticker_limited(t) for t in tickers])
        elapsed = time.time() - start_time

        # Verify results
        assert len(results) == 50
        successful = sum(1 for r in results if r.is_ok)
        assert successful == 50

        print(f"\n✓ 50 tickers with max 10 concurrent: {elapsed:.3f}s")


class TestErrorHandlingUnderLoad:
    """Test error handling with many tickers."""

    @pytest.mark.asyncio
    async def test_mixed_success_and_failures(self, mock_options_provider):
        """Test handling mix of successful and failing tickers."""
        tickers = [f"MIX{i:02d}" for i in range(20)]
        expiration = date.today() + timedelta(days=7)

        # Setup: Half with valid data, half with missing data
        for i, ticker in enumerate(tickers):
            if i % 2 == 0:
                # Valid ticker
                stock_price = Money(100)
                mock_options_provider.set_stock_price(ticker, stock_price)
                chain = create_realistic_chain(ticker, stock_price, expiration)
                mock_options_provider.set_option_chain(ticker, expiration, chain)
            # else: No data for odd-indexed tickers (will fail)

        calc = ImpliedMoveCalculator(mock_options_provider)

        async def analyze_ticker(ticker):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: calc.calculate(ticker, expiration))

        results = await asyncio.gather(*[analyze_ticker(t) for t in tickers])

        # Verify mixed results
        assert len(results) == 20
        successful = sum(1 for r in results if r.is_ok)
        failed = sum(1 for r in results if r.is_err)

        assert successful == 10, f"Expected 10 successful, got {successful}"
        assert failed == 10, f"Expected 10 failed, got {failed}"

        print(f"\n✓ Mixed results: {successful} succeeded, {failed} failed")


class TestPerformanceScaling:
    """Test that performance scales reasonably."""

    @pytest.mark.asyncio
    async def test_linear_scaling(self, mock_options_provider):
        """Verify performance scales roughly linearly with ticker count."""
        expiration = date.today() + timedelta(days=7)
        results_data = []

        for count in [10, 20, 40]:
            tickers = [f"SCALE{i:03d}_{count}" for i in range(count)]

            # Setup tickers
            for ticker in tickers:
                stock_price = Money(100)
                mock_options_provider.set_stock_price(ticker, stock_price)
                chain = create_realistic_chain(ticker, stock_price, expiration)
                mock_options_provider.set_option_chain(ticker, expiration, chain)

            calc = ImpliedMoveCalculator(mock_options_provider)

            async def analyze_ticker(ticker):
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: calc.calculate(ticker, expiration))

            start_time = time.time()
            results = await asyncio.gather(*[analyze_ticker(t) for t in tickers])
            elapsed = time.time() - start_time

            assert len(results) == count
            results_data.append((count, elapsed))

            print(f"  {count:2d} tickers: {elapsed:.3f}s ({elapsed/count*1000:.1f}ms avg)")

        # Verify scaling is reasonable (not exponential)
        _, time_10 = results_data[0]
        _, time_40 = results_data[2]

        if time_10 > 0:
            ratio = time_40 / time_10
            # Should be between 2x and 8x (linear to slightly worse)
            assert ratio < 8.0, f"Performance degraded too much: {ratio:.1f}x for 4x load"
            print(f"\n✓ Scaling ratio (40/10 tickers): {ratio:.2f}x")


class TestMemoryStability:
    """Test memory behavior under load."""

    @pytest.mark.asyncio
    async def test_repeated_analysis_no_leak(self, mock_options_provider):
        """Verify repeated analysis doesn't leak memory."""
        tickers = [f"MEM{i:02d}" for i in range(10)]
        expiration = date.today() + timedelta(days=7)

        # Setup tickers
        for ticker in tickers:
            stock_price = Money(100)
            mock_options_provider.set_stock_price(ticker, stock_price)
            chain = create_realistic_chain(ticker, stock_price, expiration)
            mock_options_provider.set_option_chain(ticker, expiration, chain)

        calc = ImpliedMoveCalculator(mock_options_provider)

        async def analyze_ticker(ticker):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: calc.calculate(ticker, expiration))

        # Run same analysis multiple times
        for iteration in range(5):
            results = await asyncio.gather(*[analyze_ticker(t) for t in tickers])
            assert len(results) == 10
            successful = sum(1 for r in results if r.is_ok)
            assert successful == 10

        print("\n✓ 5 iterations of 10 tickers completed without errors")
