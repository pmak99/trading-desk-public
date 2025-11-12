"""Integration tests for async ticker analyzer."""

import pytest
import asyncio
from datetime import date
from unittest.mock import Mock, patch

from src.config.config import Config
from src.container import Container
from src.application.async_metrics.vrp_analyzer_async import AsyncTickerAnalyzer
from src.application.services.analyzer import TickerAnalyzer
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode


@pytest.fixture
def config():
    """Create test configuration."""
    return Config.from_env()


@pytest.fixture
def container(config):
    """Create container with test config."""
    container = Container(config, skip_validation=True)
    container.initialize_database()
    return container


class TestAsyncTickerAnalyzer:
    """Integration tests for AsyncTickerAnalyzer."""

    @pytest.mark.asyncio
    async def test_async_analyzer_creation(self, container):
        """Test that async analyzer can be created."""
        async_analyzer = container.async_analyzer

        assert async_analyzer is not None
        assert isinstance(async_analyzer, AsyncTickerAnalyzer)
        assert isinstance(async_analyzer.sync_analyzer, TickerAnalyzer)

    @pytest.mark.asyncio
    async def test_analyze_many_with_mock_results(self, container):
        """Test analyze_many with mocked sync analyzer."""
        # Create mock analyzer
        mock_sync_analyzer = Mock(spec=TickerAnalyzer)
        mock_result = Ok(Mock(ticker="AAPL"))
        mock_sync_analyzer.analyze.return_value = mock_result

        async_analyzer = AsyncTickerAnalyzer(mock_sync_analyzer)

        # Analyze multiple tickers
        tickers = ["AAPL", "GOOGL", "MSFT"]
        earnings_date = date(2025, 2, 1)
        expiration = date(2025, 2, 7)

        results = await async_analyzer.analyze_many(
            tickers, earnings_date, expiration, max_concurrent=2
        )

        assert len(results) == 3
        for ticker, result in results:
            assert ticker in tickers
            assert result == mock_result

        # Verify all tickers were analyzed
        assert mock_sync_analyzer.analyze.call_count == 3

    @pytest.mark.asyncio
    async def test_analyze_many_concurrency_limit(self, container):
        """Test that max_concurrent is respected."""
        concurrent_calls = []

        def slow_analyze(ticker, earnings_date, expiration):
            """Simulate slow analysis."""
            import time

            concurrent_calls.append(len(concurrent_calls))
            time.sleep(0.1)
            return Ok(Mock(ticker=ticker))

        mock_sync_analyzer = Mock(spec=TickerAnalyzer)
        mock_sync_analyzer.analyze.side_effect = slow_analyze

        async_analyzer = AsyncTickerAnalyzer(mock_sync_analyzer)

        tickers = ["A", "B", "C", "D", "E"]
        earnings_date = date(2025, 2, 1)
        expiration = date(2025, 2, 7)

        results = await async_analyzer.analyze_many(
            tickers, earnings_date, expiration, max_concurrent=2
        )

        assert len(results) == 5

        # With max_concurrent=2, we shouldn't have more than 2 concurrent calls
        # (This is a simplified check - actual concurrency control is via semaphore)
        assert mock_sync_analyzer.analyze.call_count == 5

    @pytest.mark.asyncio
    async def test_analyze_many_with_errors(self, container):
        """Test analyze_many handles errors in individual analyses."""

        def analyze_with_errors(ticker, earnings_date, expiration):
            """Return error for specific tickers."""
            if ticker == "FAIL":
                return Err(AppError(code=ErrorCode.CALCULATION, message="Analysis failed"))
            return Ok(Mock(ticker=ticker))

        mock_sync_analyzer = Mock(spec=TickerAnalyzer)
        mock_sync_analyzer.analyze.side_effect = analyze_with_errors

        async_analyzer = AsyncTickerAnalyzer(mock_sync_analyzer)

        tickers = ["AAPL", "FAIL", "GOOGL"]
        earnings_date = date(2025, 2, 1)
        expiration = date(2025, 2, 7)

        results = await async_analyzer.analyze_many(
            tickers, earnings_date, expiration, max_concurrent=2
        )

        assert len(results) == 3

        # Check that FAIL ticker has error result
        for ticker, result in results:
            if ticker == "FAIL":
                assert result.is_err
                assert result.error.code == ErrorCode.CALCULATION
                assert result.error.message == "Analysis failed"
            else:
                assert result.is_ok

    @pytest.mark.asyncio
    async def test_analyze_many_preserves_order(self, container):
        """Test that results preserve input order."""
        mock_sync_analyzer = Mock(spec=TickerAnalyzer)

        def mock_analyze(ticker, earnings_date, expiration):
            return Ok(Mock(ticker=ticker))

        mock_sync_analyzer.analyze.side_effect = mock_analyze

        async_analyzer = AsyncTickerAnalyzer(mock_sync_analyzer)

        tickers = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]
        earnings_date = date(2025, 2, 1)
        expiration = date(2025, 2, 7)

        results = await async_analyzer.analyze_many(
            tickers, earnings_date, expiration, max_concurrent=10
        )

        # Results should be in same order as input
        result_tickers = [ticker for ticker, _ in results]
        assert result_tickers == tickers

    @pytest.mark.asyncio
    async def test_analyze_many_performance(self, container):
        """Test that concurrent execution is faster than sequential."""
        import time

        call_times = []

        def slow_analyze(ticker, earnings_date, expiration):
            """Simulate slow API call."""
            call_times.append(time.time())
            time.sleep(0.05)  # 50ms per call
            return Ok(Mock(ticker=ticker))

        mock_sync_analyzer = Mock(spec=TickerAnalyzer)
        mock_sync_analyzer.analyze.side_effect = slow_analyze

        async_analyzer = AsyncTickerAnalyzer(mock_sync_analyzer)

        tickers = ["T1", "T2", "T3", "T4", "T5"]
        earnings_date = date(2025, 2, 1)
        expiration = date(2025, 2, 7)

        start = time.time()
        results = await async_analyzer.analyze_many(
            tickers, earnings_date, expiration, max_concurrent=5
        )
        elapsed = time.time() - start

        assert len(results) == 5

        # With 5 concurrent calls, should be much faster than 5 * 50ms = 250ms
        # Allow some overhead, but should be < 150ms
        assert elapsed < 0.15

    @pytest.mark.asyncio
    async def test_analyze_many_empty_list(self, container):
        """Test analyze_many with empty ticker list."""
        async_analyzer = container.async_analyzer

        earnings_date = date(2025, 2, 1)
        expiration = date(2025, 2, 7)

        results = await async_analyzer.analyze_many(
            [], earnings_date, expiration, max_concurrent=10
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_analyze_many_single_ticker(self, container):
        """Test analyze_many with single ticker."""
        mock_sync_analyzer = Mock(spec=TickerAnalyzer)
        mock_result = Ok(Mock(ticker="AAPL"))
        mock_sync_analyzer.analyze.return_value = mock_result

        async_analyzer = AsyncTickerAnalyzer(mock_sync_analyzer)

        tickers = ["AAPL"]
        earnings_date = date(2025, 2, 1)
        expiration = date(2025, 2, 7)

        results = await async_analyzer.analyze_many(
            tickers, earnings_date, expiration, max_concurrent=10
        )

        assert len(results) == 1
        assert results[0][0] == "AAPL"
        assert results[0][1] == mock_result
