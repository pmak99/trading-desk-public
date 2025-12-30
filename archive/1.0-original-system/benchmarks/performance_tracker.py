"""
Performance benchmarking system to track improvements over time.

Tracks key metrics:
- Execution time
- API call counts
- Memory usage
- Cache hit rates

Usage:
    python benchmarks/performance_tracker.py --tickers "AAPL,MSFT,GOOGL" --benchmark
"""

import time
import psutil
import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import argparse

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class PerformanceBenchmark:
    """
    Track and compare performance metrics across runs.

    Stores results in benchmarks/results/ for historical tracking.
    """

    def __init__(self, results_dir: str = "benchmarks/results"):
        """Initialize benchmark tracker."""
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

        self.start_time = None
        self.end_time = None
        self.start_memory = None
        self.end_memory = None
        self.process = psutil.Process(os.getpid())

    def start(self):
        """Start benchmark timer and memory tracking."""
        self.start_time = time.time()
        self.start_memory = self.process.memory_info().rss / 1024 / 1024  # MB
        logger.info("🏁 Benchmark started")

    def stop(self) -> Dict:
        """
        Stop benchmark and calculate metrics.

        Returns:
            Dict with performance metrics
        """
        self.end_time = time.time()
        self.end_memory = self.process.memory_info().rss / 1024 / 1024  # MB

        elapsed = self.end_time - self.start_time
        memory_delta = self.end_memory - self.start_memory

        metrics = {
            'elapsed_seconds': round(elapsed, 2),
            'start_memory_mb': round(self.start_memory, 2),
            'end_memory_mb': round(self.end_memory, 2),
            'memory_delta_mb': round(memory_delta, 2),
            'timestamp': datetime.now().isoformat()
        }

        logger.info(f"⏱️  Elapsed: {metrics['elapsed_seconds']}s")
        logger.info(f"💾 Memory: {metrics['start_memory_mb']}MB → {metrics['end_memory_mb']}MB (Δ{metrics['memory_delta_mb']}MB)")

        return metrics

    def save_results(self, metrics: Dict, label: str = "benchmark"):
        """
        Save benchmark results to JSON file.

        Args:
            metrics: Performance metrics dict
            label: Label for this benchmark run
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = self.results_dir / f"{label}_{timestamp}.json"

        with open(filename, 'w') as f:
            json.dump(metrics, f, indent=2)

        logger.info(f"📊 Results saved to: {filename}")

    def compare_with_baseline(self, metrics: Dict, baseline_file: Optional[str] = None) -> Dict:
        """
        Compare current metrics with baseline.

        Args:
            metrics: Current performance metrics
            baseline_file: Path to baseline JSON (defaults to latest)

        Returns:
            Dict with comparison results
        """
        if baseline_file is None:
            # Find most recent baseline
            baseline_files = sorted(self.results_dir.glob("baseline_*.json"))
            if not baseline_files:
                logger.warning("No baseline found for comparison")
                return {}
            baseline_file = baseline_files[-1]

        with open(baseline_file, 'r') as f:
            baseline = json.load(f)

        comparison = {
            'time_improvement': self._calculate_improvement(
                baseline.get('elapsed_seconds'),
                metrics.get('elapsed_seconds')
            ),
            'memory_improvement': self._calculate_improvement(
                baseline.get('memory_delta_mb'),
                metrics.get('memory_delta_mb')
            ),
            'baseline_file': str(baseline_file),
            'baseline_time': baseline.get('elapsed_seconds'),
            'current_time': metrics.get('elapsed_seconds')
        }

        logger.info(f"\n📈 COMPARISON vs {baseline_file.name}:")
        logger.info(f"   Time: {comparison['time_improvement']:.1f}% improvement")
        logger.info(f"   Memory: {comparison['memory_improvement']:.1f}% improvement")

        return comparison

    def _calculate_improvement(self, baseline: Optional[float], current: Optional[float]) -> float:
        """Calculate percentage improvement (negative = regression)."""
        if baseline is None or current is None or baseline == 0:
            return 0.0
        return ((baseline - current) / baseline) * 100


def run_benchmark(tickers: List[str], earnings_date: str, with_profiling: bool = False) -> Dict:
    """
    Run earnings analyzer and track performance.

    Args:
        tickers: List of ticker symbols
        earnings_date: Earnings date (YYYY-MM-DD)
        with_profiling: If True, run with cProfile for detailed analysis

    Returns:
        Performance metrics dict
    """
    from src.analysis.earnings_analyzer import EarningsAnalyzer

    benchmark = PerformanceBenchmark()
    benchmark.start()

    # Track CPU usage
    import psutil
    process = psutil.Process(os.getpid())
    cpu_start = process.cpu_percent()

    # Run analyzer (with optional profiling)
    if with_profiling:
        import cProfile
        import pstats
        import io

        profiler = cProfile.Profile()
        profiler.enable()

    analyzer = EarningsAnalyzer()
    result = analyzer.analyze_specific_tickers(
        tickers=tickers,
        earnings_date=earnings_date,
        override_daily_limit=False
    )

    if with_profiling:
        profiler.disable()

        # Save profile stats
        stats_file = Path('profiling/results') / f'profile_benchmark_{datetime.now().strftime("%Y%m%d_%H%M%S")}.prof'
        stats_file.parent.mkdir(parents=True, exist_ok=True)
        profiler.dump_stats(str(stats_file))
        logger.info(f"📊 Profile saved to: {stats_file}")

        # Get top functions
        s = io.StringIO()
        ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
        ps.print_stats(10)
        top_functions = s.getvalue()

    # Stop benchmark
    metrics = benchmark.stop()

    # CPU usage
    cpu_end = process.cpu_percent()

    # Add analyzer-specific metrics
    metrics.update({
        'tickers_count': len(tickers),
        'tickers': tickers,
        'earnings_date': earnings_date,
        'analyzed_count': result.get('analyzed_count', 0),
        'failed_count': result.get('failed_count', 0),
        'time_per_ticker': round(metrics['elapsed_seconds'] / len(tickers), 2),
        'cpu_usage_pct': round((cpu_start + cpu_end) / 2, 1),
        'profiled': with_profiling
    })

    logger.info(f"📈 Performance: {metrics['time_per_ticker']}s per ticker")
    logger.info(f"💻 CPU Usage: {metrics['cpu_usage_pct']}%")

    return metrics


def create_baseline(tickers: List[str], earnings_date: str):
    """Create a baseline benchmark for future comparisons."""
    logger.info("🎯 Creating baseline benchmark...")
    metrics = run_benchmark(tickers, earnings_date)

    benchmark = PerformanceBenchmark()
    benchmark.save_results(metrics, label="baseline")

    logger.info("\n✅ Baseline created successfully!")
    logger.info("   Use this as reference for future performance comparisons")


def run_comparison(tickers: List[str], earnings_date: str):
    """Run benchmark and compare with baseline."""
    logger.info("🏃 Running performance benchmark...")
    metrics = run_benchmark(tickers, earnings_date)

    benchmark = PerformanceBenchmark()
    benchmark.save_results(metrics, label="benchmark")

    # Compare with baseline
    comparison = benchmark.compare_with_baseline(metrics)

    if comparison:
        # Check for regressions
        if comparison['time_improvement'] < -5:  # >5% slower
            logger.warning(f"⚠️  PERFORMANCE REGRESSION: {abs(comparison['time_improvement']):.1f}% slower!")
        elif comparison['time_improvement'] > 5:  # >5% faster
            logger.info(f"🎉 PERFORMANCE IMPROVEMENT: {comparison['time_improvement']:.1f}% faster!")


def show_history():
    """Show benchmark history."""
    results_dir = Path("benchmarks/results")

    if not results_dir.exists():
        logger.warning("No benchmark results found")
        return

    benchmark_files = sorted(results_dir.glob("benchmark_*.json"))
    baseline_files = sorted(results_dir.glob("baseline_*.json"))

    logger.info("\n📊 BENCHMARK HISTORY\n")

    if baseline_files:
        logger.info("📍 BASELINES:")
        for f in baseline_files:
            with open(f, 'r') as file:
                data = json.load(file)
                logger.info(f"   {f.name}: {data.get('elapsed_seconds')}s, {data.get('tickers_count')} tickers")

    if benchmark_files:
        logger.info("\n🏃 BENCHMARKS:")
        for f in benchmark_files[-10:]:  # Last 10
            with open(f, 'r') as file:
                data = json.load(file)
                logger.info(f"   {f.name}: {data.get('elapsed_seconds')}s, {data.get('tickers_count')} tickers")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Performance Benchmark Tool")
    parser.add_argument('--tickers', type=str, help='Comma-separated ticker list')
    parser.add_argument('--date', type=str, default='2025-11-15', help='Earnings date (YYYY-MM-DD)')
    parser.add_argument('--baseline', action='store_true', help='Create baseline benchmark')
    parser.add_argument('--compare', action='store_true', help='Run and compare with baseline')
    parser.add_argument('--history', action='store_true', help='Show benchmark history')
    parser.add_argument('--profile', action='store_true', help='Run with detailed profiling')

    args = parser.parse_args()

    if args.history:
        show_history()
    elif args.tickers:
        tickers = [t.strip() for t in args.tickers.split(',')]

        if args.baseline:
            logger.info("🎯 Creating baseline benchmark...")
            metrics = run_benchmark(tickers, args.date, with_profiling=args.profile)
            benchmark = PerformanceBenchmark()
            benchmark.save_results(metrics, label="baseline")
            logger.info("\n✅ Baseline created successfully!")
        else:
            logger.info("🏃 Running performance benchmark...")
            metrics = run_benchmark(tickers, args.date, with_profiling=args.profile)
            benchmark = PerformanceBenchmark()
            benchmark.save_results(metrics, label="benchmark")

            # Compare with baseline
            comparison = benchmark.compare_with_baseline(metrics)

            if comparison:
                if comparison['time_improvement'] < -5:
                    logger.warning(f"⚠️  PERFORMANCE REGRESSION: {abs(comparison['time_improvement']):.1f}% slower!")
                elif comparison['time_improvement'] > 5:
                    logger.info(f"🎉 PERFORMANCE IMPROVEMENT: {comparison['time_improvement']:.1f}% faster!")
    else:
        parser.print_help()
        print("\nExamples:")
        print("  # Create baseline:")
        print("  python benchmarks/performance_tracker.py --tickers 'AAPL,MSFT,GOOGL' --baseline")
        print("\n  # Run benchmark with profiling:")
        print("  python benchmarks/performance_tracker.py --tickers 'AAPL,MSFT,GOOGL' --compare --profile")
        print("\n  # View history:")
        print("  python benchmarks/performance_tracker.py --history")
