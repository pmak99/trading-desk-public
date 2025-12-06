#!/usr/bin/env python3
"""
Performance Benchmarking for 3.0 ML Earnings Scanner.

Tests sync vs async performance, API latencies, and database operations.
"""

import asyncio
import os
import sys
import time
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import dataclass, field

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.tradier import TradierAPI
from src.api.tradier_async import AsyncTradierAPI
from src.analysis.vrp import VRPCalculator
from src.analysis.scanner_core import get_earnings_calendar, get_default_db_path
from src.data.price_fetcher import PriceFetcher


@dataclass
class BenchmarkResult:
    """Single benchmark result."""
    name: str
    iterations: int
    total_time: float
    times: List[float] = field(default_factory=list)
    
    @property
    def avg_time(self) -> float:
        return self.total_time / self.iterations if self.iterations > 0 else 0
    
    @property
    def min_time(self) -> float:
        return min(self.times) if self.times else 0
    
    @property
    def max_time(self) -> float:
        return max(self.times) if self.times else 0
    
    @property
    def std_dev(self) -> float:
        return statistics.stdev(self.times) if len(self.times) > 1 else 0
    
    @property
    def throughput(self) -> float:
        return self.iterations / self.total_time if self.total_time > 0 else 0


def print_result(result: BenchmarkResult):
    """Print benchmark result."""
    print(f"\n{result.name}:")
    print(f"  Iterations: {result.iterations}")
    print(f"  Total time: {result.total_time:.2f}s")
    print(f"  Avg time: {result.avg_time*1000:.1f}ms")
    print(f"  Min/Max: {result.min_time*1000:.1f}ms / {result.max_time*1000:.1f}ms")
    print(f"  Std dev: {result.std_dev*1000:.1f}ms")
    print(f"  Throughput: {result.throughput:.2f} ops/sec")


def benchmark_sync_api(tickers: List[str], iterations: int = 1) -> Dict[str, BenchmarkResult]:
    """Benchmark sync Tradier API operations."""
    api = TradierAPI()
    results = {}
    
    # Benchmark get_stock_price
    times = []
    start = time.time()
    for _ in range(iterations):
        for ticker in tickers:
            t0 = time.time()
            try:
                api.get_stock_price(ticker)
            except Exception:
                pass
            times.append(time.time() - t0)
    total = time.time() - start
    results['sync_get_stock_price'] = BenchmarkResult(
        name="Sync get_stock_price",
        iterations=len(tickers) * iterations,
        total_time=total,
        times=times
    )
    
    # Benchmark get_expirations
    times = []
    start = time.time()
    for _ in range(iterations):
        for ticker in tickers:
            t0 = time.time()
            try:
                api.get_expirations(ticker)
            except Exception:
                pass
            times.append(time.time() - t0)
    total = time.time() - start
    results['sync_get_expirations'] = BenchmarkResult(
        name="Sync get_expirations",
        iterations=len(tickers) * iterations,
        total_time=total,
        times=times
    )
    
    # Benchmark calculate_implied_move (requires expiration)
    times = []
    start = time.time()
    for ticker in tickers[:3]:  # Limit to 3 for this expensive op
        t0 = time.time()
        try:
            exps = api.get_expirations(ticker)
            if exps:
                api.calculate_implied_move(ticker, exps[0])
        except Exception:
            pass
        times.append(time.time() - t0)
    total = time.time() - start
    results['sync_calculate_implied_move'] = BenchmarkResult(
        name="Sync calculate_implied_move",
        iterations=min(3, len(tickers)),
        total_time=total,
        times=times
    )
    
    return results


async def benchmark_async_api(tickers: List[str], workers: int = 5) -> Dict[str, BenchmarkResult]:
    """Benchmark async Tradier API operations."""
    results = {}
    
    async with AsyncTradierAPI(max_concurrent=workers) as api:
        # Benchmark parallel get_stock_price
        start = time.time()
        tasks = [api.get_stock_price(t) for t in tickers]
        await asyncio.gather(*tasks, return_exceptions=True)
        total = time.time() - start
        results[f'async_get_stock_price_{workers}w'] = BenchmarkResult(
            name=f"Async get_stock_price ({workers} workers)",
            iterations=len(tickers),
            total_time=total,
            times=[total/len(tickers)] * len(tickers)  # Approximate per-item
        )
        
        # Benchmark parallel get_expirations
        start = time.time()
        tasks = [api.get_expirations(t) for t in tickers]
        await asyncio.gather(*tasks, return_exceptions=True)
        total = time.time() - start
        results[f'async_get_expirations_{workers}w'] = BenchmarkResult(
            name=f"Async get_expirations ({workers} workers)",
            iterations=len(tickers),
            total_time=total,
            times=[total/len(tickers)] * len(tickers)
        )
        
        # Benchmark parallel calculate_implied_move
        start = time.time()
        async def get_implied(ticker):
            try:
                exps = await api.get_expirations(ticker)
                if exps:
                    return await api.calculate_implied_move(ticker, exps[0])
            except Exception:
                pass
            return None
        
        tasks = [get_implied(t) for t in tickers[:10]]  # Limit to 10
        await asyncio.gather(*tasks, return_exceptions=True)
        total = time.time() - start
        results[f'async_calculate_implied_move_{workers}w'] = BenchmarkResult(
            name=f"Async calculate_implied_move ({workers} workers)",
            iterations=min(10, len(tickers)),
            total_time=total,
            times=[total/min(10, len(tickers))] * min(10, len(tickers))
        )
    
    return results


def benchmark_vrp_calculator(tickers: List[str]) -> BenchmarkResult:
    """Benchmark VRP calculator database operations."""
    db_path = get_default_db_path()
    vrp_calc = VRPCalculator(db_path=db_path)
    
    times = []
    start = time.time()
    for ticker in tickers:
        t0 = time.time()
        vrp_calc.get_historical_moves(ticker, limit=12)
        times.append(time.time() - t0)
    total = time.time() - start
    
    return BenchmarkResult(
        name="VRP get_historical_moves",
        iterations=len(tickers),
        total_time=total,
        times=times
    )


def benchmark_price_fetcher(tickers: List[str]) -> Dict[str, BenchmarkResult]:
    """Benchmark yfinance price fetcher."""
    fetcher = PriceFetcher(cache_days=0, min_request_interval=0.0)  # Disable cache/rate limit
    results = {}
    
    # Benchmark get_price_history (cold cache)
    times = []
    start = time.time()
    for ticker in tickers[:5]:  # Limit due to yfinance rate limits
        t0 = time.time()
        fetcher.get_price_history(ticker, days=100)
        times.append(time.time() - t0)
    total = time.time() - start
    results['yfinance_cold'] = BenchmarkResult(
        name="yfinance get_price_history (cold)",
        iterations=min(5, len(tickers)),
        total_time=total,
        times=times
    )
    
    # Benchmark get_price_history (warm cache)
    fetcher2 = PriceFetcher(cache_days=1, min_request_interval=0.0)
    # Warm the cache
    for ticker in tickers[:5]:
        fetcher2.get_price_history(ticker, days=100)
    
    times = []
    start = time.time()
    for ticker in tickers[:5]:
        t0 = time.time()
        fetcher2.get_price_history(ticker, days=100)
        times.append(time.time() - t0)
    total = time.time() - start
    results['yfinance_warm'] = BenchmarkResult(
        name="yfinance get_price_history (warm cache)",
        iterations=min(5, len(tickers)),
        total_time=total,
        times=times
    )
    
    return results


async def benchmark_worker_scaling(tickers: List[str]) -> Dict[int, float]:
    """Benchmark async performance with different worker counts."""
    worker_counts = [1, 2, 3, 5, 10, 15, 20]
    results = {}
    
    for workers in worker_counts:
        async with AsyncTradierAPI(max_concurrent=workers) as api:
            start = time.time()
            tasks = [api.get_stock_price(t) for t in tickers]
            await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.time() - start
            results[workers] = len(tickers) / elapsed
            print(f"  {workers} workers: {results[workers]:.1f} tickers/sec")
    
    return results


def main():
    print("=" * 80)
    print("3.0 ML EARNINGS SCANNER - PERFORMANCE BENCHMARK")
    print("=" * 80)
    
    # Get test tickers from earnings calendar
    db_path = get_default_db_path()
    earnings = get_earnings_calendar(db_path, date.today(), date.today() + timedelta(days=14))
    
    if not earnings:
        # Fallback tickers
        test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'INTC', 'CRM']
        print(f"\nNo earnings found, using fallback tickers: {test_tickers}")
    else:
        test_tickers = [e[0] for e in earnings[:20]]
        print(f"\nUsing {len(test_tickers)} tickers from earnings calendar")
    
    print(f"Test tickers: {', '.join(test_tickers[:10])}{'...' if len(test_tickers) > 10 else ''}")
    
    # 1. Sync API Benchmarks
    print("\n" + "-" * 40)
    print("1. SYNC API BENCHMARKS")
    print("-" * 40)
    
    sync_results = benchmark_sync_api(test_tickers[:5], iterations=1)
    for result in sync_results.values():
        print_result(result)
    
    # 2. Async API Benchmarks
    print("\n" + "-" * 40)
    print("2. ASYNC API BENCHMARKS")
    print("-" * 40)
    
    async_results = asyncio.run(benchmark_async_api(test_tickers, workers=5))
    for result in async_results.values():
        print_result(result)
    
    # 3. Worker Scaling
    print("\n" + "-" * 40)
    print("3. WORKER SCALING ANALYSIS")
    print("-" * 40)
    print(f"\nTesting with {len(test_tickers)} tickers...")
    
    scaling_results = asyncio.run(benchmark_worker_scaling(test_tickers))
    
    optimal_workers = max(scaling_results, key=scaling_results.get)
    print(f"\nOptimal worker count: {optimal_workers} ({scaling_results[optimal_workers]:.1f} tickers/sec)")
    
    # 4. VRP Calculator
    print("\n" + "-" * 40)
    print("4. VRP CALCULATOR (DATABASE)")
    print("-" * 40)
    
    vrp_result = benchmark_vrp_calculator(test_tickers)
    print_result(vrp_result)
    
    # 5. yfinance Price Fetcher
    print("\n" + "-" * 40)
    print("5. YFINANCE PRICE FETCHER")
    print("-" * 40)
    
    yf_results = benchmark_price_fetcher(test_tickers)
    for result in yf_results.values():
        print_result(result)
    
    # 6. Summary
    print("\n" + "=" * 80)
    print("PERFORMANCE SUMMARY")
    print("=" * 80)
    
    sync_throughput = sync_results['sync_get_stock_price'].throughput
    async_throughput = async_results['async_get_stock_price_5w'].throughput
    speedup = async_throughput / sync_throughput if sync_throughput > 0 else 0
    
    print(f"\nSync throughput: {sync_throughput:.1f} tickers/sec")
    print(f"Async throughput (5 workers): {async_throughput:.1f} tickers/sec")
    print(f"Speedup: {speedup:.1f}x")
    print(f"\nOptimal async workers: {optimal_workers}")
    print(f"Peak throughput: {scaling_results[optimal_workers]:.1f} tickers/sec")
    
    print(f"\nVRP DB query avg: {vrp_result.avg_time*1000:.2f}ms")
    print(f"yfinance cold avg: {yf_results['yfinance_cold'].avg_time*1000:.0f}ms")
    print(f"yfinance warm avg: {yf_results['yfinance_warm'].avg_time*1000:.3f}ms")
    
    # Recommendations
    print("\n" + "-" * 40)
    print("OPTIMIZATION RECOMMENDATIONS")
    print("-" * 40)
    
    recommendations = []
    
    if optimal_workers > 5:
        recommendations.append(f"- Increase default workers from 5 to {optimal_workers}")
    
    if yf_results['yfinance_cold'].avg_time > 1.0:
        recommendations.append("- yfinance is slow, consider pre-warming cache")
    
    if vrp_result.avg_time > 0.01:
        recommendations.append("- Add database indexes for historical_moves table")
    
    if async_throughput < 10:
        recommendations.append("- Consider connection pooling for higher throughput")
    
    if not recommendations:
        recommendations.append("- System is performing optimally!")
    
    for rec in recommendations:
        print(rec)
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
