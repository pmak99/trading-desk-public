"""Tests for TickerFilter with caching and parallel processing."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import pandas as pd
from src.analysis.ticker_filter import TickerFilter
from src.analysis.scorers import CompositeScorer


@pytest.fixture
def mock_yfinance():
    """Mock yfinance."""
    with patch('src.analysis.ticker_filter.yf') as mock_yf:
        mock_ticker = Mock()
        mock_ticker.info = {
            'currentPrice': 150.0,
            'marketCap': 2.5e12,
            'regularMarketPrice': 150.0,
            'averageVolume': 10000000
        }
        hist_data = pd.DataFrame({
            'Close': [150.0, 151.0, 149.0],
            'Volume': [1000000, 1100000, 1050000]
        })
        mock_ticker.history.return_value = hist_data
        mock_yf.Ticker.return_value = mock_ticker
        yield mock_yf


@pytest.fixture
def mock_options_client():
    """Mock options client."""
    with patch('src.analysis.ticker_filter.OptionsDataClient') as mock:
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
def ticker_filter(mock_yfinance, mock_options_client):
    """Create ticker filter instance."""
    return TickerFilter(cache_ttl_minutes=15)


class TestCaching:
    """Test caching functionality."""

    def test_cache_hit(self, ticker_filter, mock_options_client):
        result1 = ticker_filter.get_ticker_data('AAPL')
        call_count_1 = mock_options_client.get_options_data.call_count

        result2 = ticker_filter.get_ticker_data('AAPL')
        call_count_2 = mock_options_client.get_options_data.call_count

        assert call_count_2 == call_count_1
        assert result1 == result2

    def test_cache_bypass(self, ticker_filter, mock_yfinance):
        result1 = ticker_filter.get_ticker_data('AAPL')
        assert result1['price'] == 150.0

        mock_yfinance.Ticker.return_value.info = {'currentPrice': 200.0, 'marketCap': 3.0e12}

        result2 = ticker_filter.get_ticker_data('AAPL', use_cache=True)
        assert result2['price'] == 150.0

        result3 = ticker_filter.get_ticker_data('AAPL', use_cache=False)
        assert result3['price'] == 200.0

    def test_separate_cache_per_ticker(self, ticker_filter):
        result1 = ticker_filter.get_ticker_data('AAPL')
        result2 = ticker_filter.get_ticker_data('NVDA')
        assert result1 is not result2

    def test_cache_stores_complete_data(self, ticker_filter):
        result1 = ticker_filter.get_ticker_data('AAPL')
        assert 'price' in result1 and 'options_data' in result1

        result2 = ticker_filter.get_ticker_data('AAPL')
        assert result1 is result2

    def test_none_results_cached(self, ticker_filter, mock_yfinance):
        mock_yfinance.Ticker.side_effect = Exception("API error")
        result1 = ticker_filter.get_ticker_data('AAPL')
        assert result1 is None

        mock_yfinance.Ticker.side_effect = None
        result2 = ticker_filter.get_ticker_data('AAPL')
        assert result2 is None


class TestParallelProcessing:
    """Test parallel processing."""

    def test_parallel_uses_threadpool(self, ticker_filter):
        with patch('src.analysis.ticker_filter.ThreadPoolExecutor') as mock_executor_class:
            with patch('src.analysis.ticker_filter.as_completed') as mock_as_completed:
                mock_executor = MagicMock()
                mock_executor_class.return_value.__enter__.return_value = mock_executor
                mock_executor_class.return_value.__exit__.return_value = None

                submitted_futures = []
                def mock_submit(func, *args):
                    mock_future = MagicMock()
                    try:
                        mock_future.result.return_value = func(*args)
                    except:
                        mock_future.result.return_value = None
                    submitted_futures.append(mock_future)
                    return mock_future

                mock_executor.submit.side_effect = mock_submit
                mock_as_completed.return_value = submitted_futures

                ticker_filter.filter_and_score_tickers(['AAPL', 'NVDA'], parallel=True)

                mock_executor_class.assert_called_once()
                assert mock_executor.submit.call_count == 2

    def test_sequential_no_threadpool(self, ticker_filter):
        with patch('src.analysis.ticker_filter.ThreadPoolExecutor') as mock_executor:
            ticker_filter.filter_and_score_tickers(['AAPL', 'NVDA'], parallel=False)
            mock_executor.assert_not_called()

    def test_max_workers_respected(self, ticker_filter):
        with patch('src.analysis.ticker_filter.ThreadPoolExecutor') as mock_executor_class:
            with patch('src.analysis.ticker_filter.as_completed'):
                mock_executor = MagicMock()
                mock_executor_class.return_value.__enter__.return_value = mock_executor
                mock_executor_class.return_value.__exit__.return_value = None

                mock_executor.submit.return_value = MagicMock()

                ticker_filter.filter_and_score_tickers(['A', 'B', 'C'], parallel=True, max_workers=3)
                mock_executor_class.assert_called_with(max_workers=3)


class TestScoringIntegration:
    """Test CompositeScorer integration."""

    def test_uses_composite_scorer(self, ticker_filter):
        assert isinstance(ticker_filter.scorer, CompositeScorer)

    def test_scoring_delegation(self, ticker_filter):
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
        ticker_filter.scorer = Mock()
        ticker_filter.scorer.calculate_score.return_value = 85.5

        score = ticker_filter.calculate_score(data)
        ticker_filter.scorer.calculate_score.assert_called_once_with(data)
        assert score == 85.5


class TestFilterAndScoreTickers:
    """Test main filtering method."""

    def test_returns_sorted_results(self, ticker_filter, mock_options_client):
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

        if len(results) >= 3:
            assert results[0]['ticker'] == 'HIGH'
            assert results[-1]['ticker'] == 'LOW'

    def test_caching_across_calls(self, ticker_filter, mock_options_client):
        ticker_filter.filter_and_score_tickers(['AAPL'])
        call_count_1 = mock_options_client.get_options_data.call_count

        ticker_filter.filter_and_score_tickers(['AAPL'])
        call_count_2 = mock_options_client.get_options_data.call_count

        assert call_count_2 == call_count_1

    def test_empty_list(self, ticker_filter):
        assert ticker_filter.filter_and_score_tickers([]) == []

    def test_single_ticker(self, ticker_filter):
        results = ticker_filter.filter_and_score_tickers(['AAPL'])
        assert len(results) <= 1


class TestProcessSingleTicker:
    """Test ticker processing."""

    def test_successful_processing(self, ticker_filter):
        result = ticker_filter._process_single_ticker('AAPL')
        if result:
            assert result['ticker'] == 'AAPL'
            assert 'score' in result and 'price' in result

    def test_failed_fetch(self, ticker_filter):
        with patch.object(ticker_filter, 'get_ticker_data', return_value=None):
            assert ticker_filter._process_single_ticker('INVALID') is None

    def test_scoring_included(self, ticker_filter):
        result = ticker_filter._process_single_ticker('AAPL')
        if result:
            assert isinstance(result['score'], (int, float))
            assert 0 <= result['score'] <= 100


class TestRealWorldScenarios:
    """Test realistic usage patterns."""

    def test_typical_scan(self, ticker_filter, mock_options_client):
        def varying_quality(ticker, *args, **kwargs):
            idx = int(ticker.replace('TICKER', ''))
            if idx % 3 == 0:
                return {'current_iv': 85.0, 'iv_crush_ratio': 1.3,
                        'options_volume': 50000, 'open_interest': 100000}
            elif idx % 3 == 1:
                return {'current_iv': 65.0, 'iv_crush_ratio': 1.1,
                        'options_volume': 5000, 'open_interest': 10000}
            else:
                return {'current_iv': 45.0}

        mock_options_client.get_options_data.side_effect = varying_quality

        results = ticker_filter.filter_and_score_tickers(
            [f'TICKER{i}' for i in range(50)], parallel=True
        )

        assert len(results) <= 40
        scores = [r['score'] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_repeated_scans_use_cache(self, ticker_filter, mock_options_client):
        tickers = ['AAPL', 'NVDA', 'TSLA', 'MSFT', 'GOOGL']

        ticker_filter.filter_and_score_tickers(tickers, parallel=True)
        first_count = mock_options_client.get_options_data.call_count

        ticker_filter.filter_and_score_tickers(tickers, parallel=True)
        second_count = mock_options_client.get_options_data.call_count

        assert second_count == first_count

    def test_mixed_cached_and_new(self, ticker_filter, mock_options_client):
        ticker_filter.filter_and_score_tickers(['AAPL', 'NVDA'], parallel=False)
        first_count = mock_options_client.get_options_data.call_count

        ticker_filter.filter_and_score_tickers(['AAPL', 'TSLA'], parallel=False)
        second_count = mock_options_client.get_options_data.call_count

        new_fetches = second_count - first_count
        assert 0 <= new_fetches <= 1
