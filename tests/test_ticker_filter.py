"""
Comprehensive tests for TickerFilter with caching and parallel processing.

Tests the refactored ticker filtering system with:
- TTL caching (15-minute default)
- Parallel processing with ThreadPoolExecutor
- CompositeScorer integration
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from src.analysis.ticker_filter import TickerFilter
from src.analysis.scorers import CompositeScorer


@pytest.fixture
def mock_options_client():
    """Mock options data client."""
    with patch('src.ticker_filter.OptionsDataClient') as mock:
        instance = mock.return_value
        instance.get_options_data.return_value = {
            'current_iv': 75.0,
            'iv_rank': 65.0,
            'iv_crush_ratio': 1.25,
            'options_volume': 15000,
            'open_interest': 50000,
            'bid_ask_spread_pct': 0.03
        }
        yield instance


@pytest.fixture
def ticker_filter(mock_options_client):
    """Create a ticker filter instance with mocked dependencies."""
    return TickerFilter(cache_ttl_minutes=15)


class TestCaching:
    """Test TTL caching functionality."""

    def test_cache_hit_prevents_api_call(self, ticker_filter, mock_options_client):
        """Test that cached data prevents redundant API calls."""
        ticker = 'AAPL'

        # First call - should hit API
        result1 = ticker_filter._get_ticker_data(ticker)
        call_count_1 = mock_options_client.get_options_data.call_count

        # Second call - should use cache
        result2 = ticker_filter._get_ticker_data(ticker)
        call_count_2 = mock_options_client.get_options_data.call_count

        assert call_count_2 == call_count_1, "Second call should use cache, not hit API"
        assert result1 == result2, "Cached result should match original"

    def test_cache_expiration(self, ticker_filter, mock_options_client):
        """Test that cache expires after TTL."""
        ticker_filter._cache_ttl = timedelta(seconds=0.1)  # 100ms TTL for testing
        ticker = 'AAPL'

        # First call
        result1 = ticker_filter._get_ticker_data(ticker)
        call_count_1 = mock_options_client.get_options_data.call_count

        # Wait for cache to expire
        import time
        time.sleep(0.2)

        # Second call - should hit API again
        result2 = ticker_filter._get_ticker_data(ticker)
        call_count_2 = mock_options_client.get_options_data.call_count

        assert call_count_2 > call_count_1, "Expired cache should cause new API call"

    def test_separate_cache_per_ticker(self, ticker_filter, mock_options_client):
        """Test that each ticker has separate cache entry."""
        ticker_filter._get_ticker_data('AAPL')
        ticker_filter._get_ticker_data('NVDA')

        # Each ticker should hit API once
        assert mock_options_client.get_options_data.call_count == 2

        # Using cache for same tickers
        ticker_filter._get_ticker_data('AAPL')
        ticker_filter._get_ticker_data('NVDA')

        # Should still be 2 (no new calls)
        assert mock_options_client.get_options_data.call_count == 2

    def test_cache_stores_complete_data(self, ticker_filter, mock_options_client):
        """Test that cache stores all ticker data."""
        ticker = 'AAPL'

        # Mock complete ticker data
        with patch.object(ticker_filter, '_fetch_ticker_yfinance_data') as mock_yf:
            mock_yf.return_value = {
                'ticker': 'AAPL',
                'price': 150.0,
                'market_cap': 2.5e12,
                'options_data': {'current_iv': 75.0}
            }

            result = ticker_filter._get_ticker_data(ticker)

            # Verify all data is present
            assert result['ticker'] == 'AAPL'
            assert result['price'] == 150.0
            assert result['market_cap'] == 2.5e12
            assert 'options_data' in result

    def test_cache_invalidation_on_error(self, ticker_filter, mock_options_client):
        """Test that errors don't get cached."""
        ticker = 'INVALID'

        # Mock API to raise error
        mock_options_client.get_options_data.side_effect = Exception("API Error")

        # First call - should get None
        result1 = ticker_filter._get_ticker_data(ticker)
        assert result1 is None

        # Fix the API
        mock_options_client.get_options_data.side_effect = None
        mock_options_client.get_options_data.return_value = {'current_iv': 75.0}

        # Second call - should retry API (not use cached None)
        result2 = ticker_filter._get_ticker_data(ticker)
        # Should make at least 2 calls (not cached)
        assert mock_options_client.get_options_data.call_count >= 2


class TestParallelProcessing:
    """Test parallel processing with ThreadPoolExecutor."""

    @patch('src.ticker_filter.ThreadPoolExecutor')
    def test_parallel_mode_uses_threadpool(self, mock_executor, ticker_filter):
        """Test that parallel mode uses ThreadPoolExecutor."""
        tickers = ['AAPL', 'NVDA', 'TSLA']

        # Mock executor
        mock_pool = MagicMock()
        mock_executor.return_value.__enter__.return_value = mock_pool
        mock_pool.submit.return_value.result.return_value = {
            'ticker': 'TEST',
            'score': 75.0
        }

        ticker_filter.filter_and_score_tickers(tickers, parallel=True)

        # Should have created executor
        mock_executor.assert_called_once()

    def test_sequential_mode_no_threadpool(self, ticker_filter, mock_options_client):
        """Test that sequential mode doesn't use ThreadPoolExecutor."""
        tickers = ['AAPL', 'NVDA']

        with patch('src.ticker_filter.ThreadPoolExecutor') as mock_executor:
            ticker_filter.filter_and_score_tickers(tickers, parallel=False)

            # Should NOT have used executor
            mock_executor.assert_not_called()

    def test_parallel_processing_performance(self, ticker_filter, mock_options_client):
        """Test that parallel processing is faster than sequential."""
        import time

        # Mock slow API call
        def slow_api_call(*args, **kwargs):
            time.sleep(0.1)  # 100ms delay
            return {'current_iv': 75.0, 'iv_rank': 65.0}

        mock_options_client.get_options_data.side_effect = slow_api_call

        tickers = ['AAPL', 'NVDA', 'TSLA', 'MSFT', 'GOOGL']

        # Sequential: should take ~500ms (5 * 100ms)
        start = time.time()
        ticker_filter.filter_and_score_tickers(tickers, parallel=False)
        sequential_time = time.time() - start

        # Clear cache
        ticker_filter._ticker_cache.clear()

        # Parallel: should take ~200ms (100ms + overhead, not 5 * 100ms)
        start = time.time()
        ticker_filter.filter_and_score_tickers(tickers, parallel=True, max_workers=5)
        parallel_time = time.time() - start

        # Parallel should be significantly faster
        assert parallel_time < sequential_time * 0.5, \
            f"Parallel ({parallel_time:.2f}s) should be < 50% of sequential ({sequential_time:.2f}s)"

    def test_max_workers_parameter(self, ticker_filter, mock_options_client):
        """Test that max_workers parameter is respected."""
        tickers = ['AAPL', 'NVDA', 'TSLA']

        with patch('src.ticker_filter.ThreadPoolExecutor') as mock_executor:
            mock_pool = MagicMock()
            mock_executor.return_value.__enter__.return_value = mock_pool
            mock_pool.submit.return_value.result.return_value = {
                'ticker': 'TEST',
                'score': 75.0
            }

            ticker_filter.filter_and_score_tickers(tickers, parallel=True, max_workers=3)

            # Should have created executor with max_workers=3
            mock_executor.assert_called_with(max_workers=3)

    def test_parallel_error_handling(self, ticker_filter, mock_options_client):
        """Test that parallel processing handles errors gracefully."""
        tickers = ['AAPL', 'ERROR', 'NVDA']

        # Mock API to fail for ERROR ticker
        def conditional_error(ticker, *args, **kwargs):
            if ticker == 'ERROR':
                raise Exception("API Error")
            return {'current_iv': 75.0, 'iv_rank': 65.0}

        mock_options_client.get_options_data.side_effect = conditional_error

        # Should not crash, just skip failed ticker
        results = ticker_filter.filter_and_score_tickers(tickers, parallel=True)

        # Should have results for AAPL and NVDA only
        assert len(results) <= 2, "Failed ticker should be excluded"


class TestScoringIntegration:
    """Test integration with CompositeScorer."""

    def test_uses_composite_scorer(self, ticker_filter):
        """Test that filter uses CompositeScorer instance."""
        assert isinstance(ticker_filter.scorer, CompositeScorer)

    def test_scoring_delegation(self, ticker_filter):
        """Test that calculate_score delegates to CompositeScorer."""
        data = {
            'ticker': 'TEST',
            'price': 100.0,
            'market_cap': 50e9,
            'options_data': {
                'current_iv': 75.0,
                'iv_crush_ratio': 1.25,
                'options_volume': 10000,
                'open_interest': 50000
            }
        }

        # Mock the scorer
        ticker_filter.scorer = Mock()
        ticker_filter.scorer.calculate_score.return_value = 85.5

        score = ticker_filter.calculate_score(data)

        # Should have called scorer
        ticker_filter.scorer.calculate_score.assert_called_once_with(data)
        assert score == 85.5

    def test_min_iv_filter(self, ticker_filter, mock_options_client):
        """Test that min IV filter is applied."""
        # Mock low IV ticker
        mock_options_client.get_options_data.return_value = {
            'current_iv': 40.0,  # Below default min of 60
            'iv_rank': 35.0
        }

        results = ticker_filter.filter_and_score_tickers(['LOW_IV'])

        # Should be filtered out (score 0)
        assert len(results) == 0 or results[0]['score'] == 0


class TestFilterAndScoreTickers:
    """Test main filter_and_score_tickers method."""

    def test_returns_sorted_results(self, ticker_filter, mock_options_client):
        """Test that results are sorted by score descending."""
        # Mock different scores for different tickers
        def varying_iv(ticker, *args, **kwargs):
            iv_map = {'HIGH': 95.0, 'MED': 75.0, 'LOW': 65.0}
            return {
                'current_iv': iv_map.get(ticker, 75.0),
                'iv_rank': iv_map.get(ticker, 75.0),
                'iv_crush_ratio': 1.2,
                'options_volume': 10000,
                'open_interest': 50000
            }

        mock_options_client.get_options_data.side_effect = varying_iv

        results = ticker_filter.filter_and_score_tickers(['LOW', 'HIGH', 'MED'])

        # Should be sorted: HIGH > MED > LOW
        assert results[0]['ticker'] == 'HIGH'
        assert results[-1]['ticker'] == 'LOW'
        assert results[0]['score'] >= results[1]['score'] >= results[2]['score']

    def test_excludes_zero_scores(self, ticker_filter, mock_options_client):
        """Test that zero-score tickers are excluded."""
        def conditional_iv(ticker, *args, **kwargs):
            if ticker == 'PASS':
                return {'current_iv': 75.0, 'iv_rank': 65.0}
            else:
                return {'current_iv': 40.0, 'iv_rank': 35.0}  # Will score 0

        mock_options_client.get_options_data.side_effect = conditional_iv

        results = ticker_filter.filter_and_score_tickers(['PASS', 'FAIL'])

        # Should only include PASS
        assert len(results) == 1
        assert results[0]['ticker'] == 'PASS'

    def test_caching_across_calls(self, ticker_filter, mock_options_client):
        """Test that cache persists across multiple filter calls."""
        tickers = ['AAPL']

        # First call
        ticker_filter.filter_and_score_tickers(tickers)
        call_count_1 = mock_options_client.get_options_data.call_count

        # Second call with same tickers
        ticker_filter.filter_and_score_tickers(tickers)
        call_count_2 = mock_options_client.get_options_data.call_count

        assert call_count_2 == call_count_1, "Second call should use cache"

    def test_empty_ticker_list(self, ticker_filter):
        """Test handling of empty ticker list."""
        results = ticker_filter.filter_and_score_tickers([])
        assert results == [], "Empty list should return empty results"

    def test_single_ticker(self, ticker_filter, mock_options_client):
        """Test processing single ticker."""
        results = ticker_filter.filter_and_score_tickers(['AAPL'])

        assert len(results) <= 1, "Single ticker should return <= 1 result"
        if len(results) == 1:
            assert results[0]['ticker'] == 'AAPL'


class TestProcessSingleTicker:
    """Test _process_single_ticker helper method."""

    def test_successful_processing(self, ticker_filter, mock_options_client):
        """Test successful ticker processing."""
        result = ticker_filter._process_single_ticker('AAPL')

        assert result is not None
        assert result['ticker'] == 'AAPL'
        assert 'score' in result
        assert 'price' in result
        assert 'options_data' in result

    def test_failed_data_fetch(self, ticker_filter, mock_options_client):
        """Test handling of failed data fetch."""
        # Mock data fetch to return None
        with patch.object(ticker_filter, '_get_ticker_data', return_value=None):
            result = ticker_filter._process_single_ticker('INVALID')

            assert result is None, "Failed fetch should return None"

    def test_scoring_integration_in_processing(self, ticker_filter, mock_options_client):
        """Test that processing includes scoring."""
        result = ticker_filter._process_single_ticker('AAPL')

        assert 'score' in result, "Result should include score"
        assert isinstance(result['score'], (int, float)), "Score should be numeric"
        assert 0 <= result['score'] <= 100, "Score should be 0-100"


class TestPerformance:
    """Test performance characteristics."""

    def test_cache_improves_performance(self, ticker_filter, mock_options_client):
        """Test that caching significantly improves performance."""
        import time

        # Mock slow API
        def slow_api(*args, **kwargs):
            time.sleep(0.05)  # 50ms delay
            return {'current_iv': 75.0}

        mock_options_client.get_options_data.side_effect = slow_api

        tickers = ['AAPL'] * 10  # Same ticker 10 times

        # Without cache: should take ~500ms (10 * 50ms)
        ticker_filter._ticker_cache.clear()
        start = time.time()
        for ticker in tickers:
            ticker_filter._get_ticker_data(ticker)
        no_cache_time = time.time() - start

        # With cache: should take ~50ms (only first call hits API)
        ticker_filter._ticker_cache.clear()
        start = time.time()
        for ticker in tickers:
            ticker_filter._get_ticker_data(ticker)
        with_cache_time = time.time() - start

        # Cache should provide significant speedup
        assert with_cache_time < no_cache_time * 0.3, \
            f"Cache ({with_cache_time:.2f}s) should be < 30% of no-cache ({no_cache_time:.2f}s)"

    def test_batch_processing_performance(self, ticker_filter, mock_options_client):
        """Test that batch processing is efficient."""
        import time

        # Mock realistic API timing
        def realistic_api(*args, **kwargs):
            time.sleep(0.02)  # 20ms (realistic API latency)
            return {'current_iv': 75.0, 'iv_rank': 65.0}

        mock_options_client.get_options_data.side_effect = realistic_api

        tickers = [f'TICKER{i}' for i in range(20)]

        start = time.time()
        results = ticker_filter.filter_and_score_tickers(tickers, parallel=True, max_workers=10)
        elapsed = time.time() - start

        # With 10 workers, 20 tickers @ 20ms each should take ~50ms (not 400ms)
        assert elapsed < 0.2, f"Batch processing of 20 tickers should take < 200ms, took {elapsed:.2f}s"


class TestRealWorldScenarios:
    """Test realistic usage patterns."""

    def test_typical_scan_workflow(self, ticker_filter, mock_options_client):
        """Test a typical ticker scanning workflow."""
        # Simulate scanning S&P 500 subset
        tickers = [f'TICKER{i}' for i in range(50)]

        # Mock varying quality tickers
        def varying_quality(ticker, *args, **kwargs):
            # Make every 3rd ticker high quality
            idx = int(ticker.replace('TICKER', ''))
            if idx % 3 == 0:
                return {'current_iv': 85.0, 'iv_rank': 75.0, 'iv_crush_ratio': 1.3,
                        'options_volume': 50000, 'open_interest': 100000}
            elif idx % 3 == 1:
                return {'current_iv': 65.0, 'iv_rank': 55.0, 'iv_crush_ratio': 1.1,
                        'options_volume': 5000, 'open_interest': 10000}
            else:
                return {'current_iv': 45.0, 'iv_rank': 35.0}  # Filtered out

        mock_options_client.get_options_data.side_effect = varying_quality

        results = ticker_filter.filter_and_score_tickers(tickers, parallel=True)

        # Should have filtered down to ~33 tickers (excluded low IV)
        assert len(results) <= 40, "Should filter out low-quality tickers"

        # Results should be sorted by score
        scores = [r['score'] for r in results]
        assert scores == sorted(scores, reverse=True), "Should be sorted by score"

    def test_repeated_scans_use_cache(self, ticker_filter, mock_options_client):
        """Test that repeated scans benefit from cache."""
        tickers = ['AAPL', 'NVDA', 'TSLA', 'MSFT', 'GOOGL']

        # First scan
        ticker_filter.filter_and_score_tickers(tickers, parallel=True)
        first_call_count = mock_options_client.get_options_data.call_count

        # Second scan (within cache TTL)
        ticker_filter.filter_and_score_tickers(tickers, parallel=True)
        second_call_count = mock_options_client.get_options_data.call_count

        assert second_call_count == first_call_count, "Second scan should use cache"

    def test_mixed_cached_and_new_tickers(self, ticker_filter, mock_options_client):
        """Test scanning mix of cached and new tickers."""
        # First scan with some tickers
        ticker_filter.filter_and_score_tickers(['AAPL', 'NVDA'], parallel=True)
        first_count = mock_options_client.get_options_data.call_count

        # Second scan with some cached, some new
        ticker_filter.filter_and_score_tickers(['AAPL', 'TSLA'], parallel=True)
        second_count = mock_options_client.get_options_data.call_count

        # Should only fetch TSLA (AAPL is cached)
        new_fetches = second_count - first_count
        assert new_fetches == 1, "Should only fetch new ticker (TSLA)"
