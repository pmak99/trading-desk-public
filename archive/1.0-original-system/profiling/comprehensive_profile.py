"""
Comprehensive profiling and benchmarking script.

Profiles key operations and identifies optimization opportunities:
- API call patterns and timing
- Memory usage patterns
- Cache effectiveness
- Parallel vs sequential performance
- Function-level hotspots

Usage:
    python profiling/comprehensive_profile.py
"""

import cProfile
import pstats
import io
import time
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ComprehensiveProfiler:
    """
    Comprehensive profiler that analyzes multiple dimensions of performance.
    """

    def __init__(self):
        self.results = {}
        self.profilers = {}

    def profile_data_fetching(self):
        """Profile ticker data fetching operations."""
        logger.info("\n" + "=" * 80)
        logger.info("PROFILING: Data Fetching Operations")
        logger.info("=" * 80)

        from src.analysis.ticker_filter import TickerFilter
        from src.analysis.ticker_data_fetcher import TickerDataFetcher

        # Initialize
        ticker_filter = TickerFilter()
        fetcher = TickerDataFetcher(ticker_filter)

        # Test tickers with varying IV levels
        test_tickers = ['AAPL', 'MSFT', 'GOOGL']

        profiler = cProfile.Profile()
        profiler.enable()

        start_time = time.time()

        try:
            tickers_data, failed = fetcher.fetch_tickers_data(
                test_tickers,
                '2025-11-15'
            )
            elapsed = time.time() - start_time

            profiler.disable()

            # Analyze results
            s = io.StringIO()
            ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
            ps.print_stats(20)

            logger.info(f"\nData Fetching Results:")
            logger.info(f"  Tickers processed: {len(tickers_data)}")
            logger.info(f"  Failed: {len(failed)}")
            logger.info(f"  Total time: {elapsed:.2f}s")
            logger.info(f"  Time per ticker: {elapsed/len(test_tickers):.2f}s")

            logger.info(f"\nTop Functions:")
            print(s.getvalue())

            return {
                'elapsed': elapsed,
                'tickers_processed': len(tickers_data),
                'failed': len(failed),
                'time_per_ticker': elapsed / len(test_tickers)
            }

        except Exception as e:
            logger.error(f"Data fetching profile failed: {e}")
            return None

    def profile_api_calls(self):
        """Profile API call patterns and timing."""
        logger.info("\n" + "=" * 80)
        logger.info("PROFILING: API Call Patterns")
        logger.info("=" * 80)

        from src.options.tradier_client import TradierOptionsClient

        client = TradierOptionsClient()
        if not client.is_available():
            logger.warning("Tradier client not available - skipping API profiling")
            return None

        # Profile single API call
        ticker = 'AAPL'
        profiler = cProfile.Profile()
        profiler.enable()

        start_time = time.time()
        result = client.get_options_data(ticker, earnings_date='2025-11-15')
        elapsed = time.time() - start_time

        profiler.disable()

        logger.info(f"\nAPI Call Results:")
        logger.info(f"  Ticker: {ticker}")
        logger.info(f"  Time: {elapsed:.2f}s")
        logger.info(f"  Success: {result is not None}")

        # Show top API-related functions
        s = io.StringIO()
        ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
        ps.print_stats(15)

        logger.info(f"\nTop API Functions:")
        print(s.getvalue())

        return {
            'elapsed': elapsed,
            'success': result is not None
        }

    def profile_caching(self):
        """Profile cache effectiveness."""
        logger.info("\n" + "=" * 80)
        logger.info("PROFILING: Cache Effectiveness")
        logger.info("=" * 80)

        from src.core.lru_cache import LRUCache

        # Test cache performance
        cache = LRUCache(max_size=100, ttl_minutes=15)

        # Warm up cache
        for i in range(50):
            cache.set(f"key_{i}", f"value_{i}")

        # Test cache hits
        start = time.time()
        for i in range(50):
            _ = cache.get(f"key_{i}")
        hit_time = time.time() - start

        # Test cache misses
        start = time.time()
        for i in range(50, 100):
            _ = cache.get(f"key_{i}")
        miss_time = time.time() - start

        stats = cache.stats()

        logger.info(f"\nCache Statistics:")
        logger.info(f"  Size: {stats['size']}/{stats['max_size']}")
        logger.info(f"  Hits: {stats['hits']}")
        logger.info(f"  Misses: {stats['misses']}")
        logger.info(f"  Hit rate: {stats['hit_rate']}%")
        logger.info(f"  Hit time: {hit_time*1000:.2f}ms for 50 lookups")
        logger.info(f"  Miss time: {miss_time*1000:.2f}ms for 50 lookups")
        logger.info(f"  Avg hit time: {hit_time/50*1000000:.2f}μs")
        logger.info(f"  Avg miss time: {miss_time/50*1000000:.2f}μs")

        return {
            'cache_stats': stats,
            'hit_time_us': hit_time / 50 * 1000000,
            'miss_time_us': miss_time / 50 * 1000000
        }

    def analyze_memory_usage(self):
        """Analyze memory usage patterns."""
        logger.info("\n" + "=" * 80)
        logger.info("ANALYZING: Memory Usage Patterns")
        logger.info("=" * 80)

        import psutil
        import os

        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()

        logger.info(f"\nMemory Usage:")
        logger.info(f"  RSS: {mem_info.rss / 1024 / 1024:.2f} MB")
        logger.info(f"  VMS: {mem_info.vms / 1024 / 1024:.2f} MB")

        # Test memory growth with cache
        from src.core.lru_cache import LRUCache

        initial_mem = process.memory_info().rss / 1024 / 1024
        cache = LRUCache(max_size=1000)

        # Fill cache
        for i in range(1000):
            cache.set(f"key_{i}", f"value_{i}" * 100)  # ~1KB per entry

        final_mem = process.memory_info().rss / 1024 / 1024
        delta = final_mem - initial_mem

        logger.info(f"\nCache Memory Growth Test:")
        logger.info(f"  Initial: {initial_mem:.2f} MB")
        logger.info(f"  After 1000 entries: {final_mem:.2f} MB")
        logger.info(f"  Delta: {delta:.2f} MB")
        logger.info(f"  Per entry: {delta/1000*1024:.2f} KB")

        return {
            'rss_mb': mem_info.rss / 1024 / 1024,
            'cache_growth_mb': delta
        }

    def run_all_profiles(self):
        """Run all profiling analyses."""
        logger.info("\n" + "=" * 80)
        logger.info("COMPREHENSIVE PROFILING SUITE")
        logger.info("=" * 80)

        start_time = time.time()

        # Run each profiling analysis
        results = {}

        # 1. Data fetching
        data_fetch_result = self.profile_data_fetching()
        if data_fetch_result:
            results['data_fetching'] = data_fetch_result

        # 2. API calls
        api_result = self.profile_api_calls()
        if api_result:
            results['api_calls'] = api_result

        # 3. Caching
        cache_result = self.profile_caching()
        if cache_result:
            results['caching'] = cache_result

        # 4. Memory usage
        memory_result = self.analyze_memory_usage()
        if memory_result:
            results['memory'] = memory_result

        total_time = time.time() - start_time

        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("PROFILING SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total profiling time: {total_time:.2f}s")
        logger.info(f"Analyses completed: {len(results)}")

        # Save results
        self._save_results(results)

        return results

    def _save_results(self, results: Dict):
        """Save profiling results to JSON."""
        import json

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_dir = Path('profiling/results')
        results_dir.mkdir(parents=True, exist_ok=True)

        filename = results_dir / f'comprehensive_profile_{timestamp}.json'

        with open(filename, 'w') as f:
            json.dump(results, f, indent=2)

        logger.info(f"\n📊 Results saved to: {filename}")


if __name__ == "__main__":
    profiler = ComprehensiveProfiler()
    profiler.run_all_profiles()
