"""
Performance profiling utilities using cProfile and custom analysis.

Helps identify bottlenecks and optimization opportunities.

Usage:
    # Method 1: Decorator
    @profile_function
    def my_function():
        ...

    # Method 2: Context manager
    with Profiler("my_operation"):
        ...

    # Method 3: Full program profiling
    python profiling/profiler.py --run "python -m src.analysis.earnings_analyzer --tickers AAPL"
"""

import cProfile
import pstats
import io
import time
import functools
import logging
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional
import argparse
import subprocess
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Profiler:
    """
    Context manager for profiling code blocks.

    Usage:
        with Profiler("data_processing"):
            process_large_dataset()
    """

    def __init__(self, name: str = "operation", save_stats: bool = True):
        """
        Initialize profiler.

        Args:
            name: Name for this profiling session
            save_stats: Whether to save detailed stats to file
        """
        self.name = name
        self.save_stats = save_stats
        self.profiler = cProfile.Profile()
        self.start_time = None

    def __enter__(self):
        """Start profiling."""
        logger.info(f"🔍 Profiling started: {self.name}")
        self.start_time = time.time()
        self.profiler.enable()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop profiling and display results."""
        self.profiler.disable()
        elapsed = time.time() - self.start_time

        logger.info(f"✅ Profiling completed: {self.name} ({elapsed:.2f}s)")

        # Print top functions
        self._print_top_functions()

        # Save detailed stats
        if self.save_stats:
            self._save_stats()

    def _print_top_functions(self, n: int = 20):
        """Print top N time-consuming functions."""
        s = io.StringIO()
        ps = pstats.Stats(self.profiler, stream=s).sort_stats('cumulative')
        ps.print_stats(n)

        logger.info(f"\n{'='*70}")
        logger.info(f"TOP {n} FUNCTIONS BY CUMULATIVE TIME - {self.name}")
        logger.info(f"{'='*70}")
        print(s.getvalue())

    def _save_stats(self):
        """Save detailed profiling stats to file."""
        stats_dir = Path("profiling/results")
        stats_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = stats_dir / f"{self.name}_{timestamp}.prof"

        self.profiler.dump_stats(str(filename))
        logger.info(f"📊 Detailed stats saved to: {filename}")
        logger.info(f"   Analyze with: python -m pstats {filename}")


def profile_function(func: Callable) -> Callable:
    """
    Decorator to profile a function.

    Usage:
        @profile_function
        def expensive_operation():
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with Profiler(func.__name__):
            return func(*args, **kwargs)

    return wrapper


def analyze_stats_file(stats_file: str, top_n: int = 30):
    """
    Analyze a saved .prof file and show insights.

    Args:
        stats_file: Path to .prof file
        top_n: Number of top functions to show
    """
    logger.info(f"\n{'='*70}")
    logger.info(f"ANALYZING: {stats_file}")
    logger.info(f"{'='*70}\n")

    stats = pstats.Stats(stats_file)

    # 1. Top functions by cumulative time
    logger.info(f"\n📊 TOP {top_n} BY CUMULATIVE TIME:")
    logger.info("-" * 70)
    stats.sort_stats('cumulative').print_stats(top_n)

    # 2. Top functions by total time
    logger.info(f"\n⏱️  TOP {top_n} BY TOTAL TIME:")
    logger.info("-" * 70)
    stats.sort_stats('tottime').print_stats(top_n)

    # 3. Callers (who called expensive functions)
    logger.info(f"\n📞 CALLERS OF TOP 10 FUNCTIONS:")
    logger.info("-" * 70)
    stats.sort_stats('cumulative').print_callers(10)


def compare_profiles(file1: str, file2: str):
    """
    Compare two profiling runs.

    Args:
        file1: Baseline profile
        file2: New profile to compare
    """
    logger.info(f"\n{'='*70}")
    logger.info(f"COMPARING PROFILES")
    logger.info(f"{'='*70}")
    logger.info(f"Baseline: {file1}")
    logger.info(f"New:      {file2}\n")

    stats1 = pstats.Stats(file1)
    stats2 = pstats.Stats(file2)

    # Get top functions from each
    s1 = io.StringIO()
    s2 = io.StringIO()

    stats1.stream = s1
    stats2.stream = s2

    stats1.sort_stats('cumulative').print_stats(20)
    stats2.sort_stats('cumulative').print_stats(20)

    logger.info("📊 Both profiles analyzed")
    logger.info("   Manual comparison recommended using pstats")


def profile_command(command: str, name: str = "command"):
    """
    Profile an external command.

    Args:
        command: Shell command to profile
        name: Name for this profiling session
    """
    logger.info(f"🚀 Running profiled command: {command}")

    start_time = time.time()

    # Run command with profiling
    result = subprocess.run(
        f"python -m cProfile -o profiling/results/{name}.prof {command}",
        shell=True,
        capture_output=True,
        text=True
    )

    elapsed = time.time() - start_time

    logger.info(f"✅ Command completed in {elapsed:.2f}s")
    logger.info(f"   Exit code: {result.returncode}")

    if result.returncode == 0:
        logger.info(f"📊 Profile saved to: profiling/results/{name}.prof")
        logger.info(f"   Analyze with: python profiling/profiler.py --analyze profiling/results/{name}.prof")
    else:
        logger.error(f"Command failed!")
        logger.error(f"STDOUT:\n{result.stdout}")
        logger.error(f"STDERR:\n{result.stderr}")

    return result.returncode


def find_hotspots(stats_file: str, min_cumtime: float = 0.1):
    """
    Find performance hotspots (functions taking significant time).

    Args:
        stats_file: Path to .prof file
        min_cumtime: Minimum cumulative time (seconds) to be considered a hotspot
    """
    logger.info(f"\n🔥 FINDING HOTSPOTS (functions > {min_cumtime}s)")
    logger.info("-" * 70)

    stats = pstats.Stats(stats_file)
    stats.sort_stats('cumulative')

    hotspots = []

    for func, (cc, nc, tt, ct, callers) in stats.stats.items():
        if ct >= min_cumtime:
            filename, line, func_name = func
            hotspots.append({
                'function': func_name,
                'file': filename,
                'line': line,
                'calls': nc,
                'cumtime': ct,
                'tottime': tt
            })

    # Sort by cumulative time
    hotspots.sort(key=lambda x: x['cumtime'], reverse=True)

    logger.info(f"\nFound {len(hotspots)} hotspots:\n")

    for i, hotspot in enumerate(hotspots[:20], 1):
        logger.info(
            f"{i:2d}. {hotspot['function']:40s} "
            f"({hotspot['cumtime']:6.2f}s, {hotspot['calls']:5d} calls) "
            f"- {hotspot['file']}:{hotspot['line']}"
        )


# Example profiling targets
@profile_function
def example_slow_function():
    """Example function to demonstrate profiling."""
    import time
    total = 0
    for i in range(1000000):
        total += i
    time.sleep(0.1)
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Performance Profiling Tool")
    parser.add_argument('--run', type=str, help='Command to profile')
    parser.add_argument('--analyze', type=str, help='Analyze a .prof file')
    parser.add_argument('--compare', nargs=2, help='Compare two .prof files')
    parser.add_argument('--hotspots', type=str, help='Find hotspots in .prof file')
    parser.add_argument('--top', type=int, default=30, help='Number of top functions to show')
    parser.add_argument('--example', action='store_true', help='Run example')

    args = parser.parse_args()

    if args.run:
        # Profile a command
        profile_command(args.run, name=f"profile_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    elif args.analyze:
        # Analyze existing profile
        analyze_stats_file(args.analyze, top_n=args.top)

    elif args.compare:
        # Compare two profiles
        compare_profiles(args.compare[0], args.compare[1])

    elif args.hotspots:
        # Find hotspots
        find_hotspots(args.hotspots)

    elif args.example:
        # Run example
        logger.info("Running example profiling demo...")
        result = example_slow_function()
        logger.info(f"Result: {result}")

    else:
        parser.print_help()
        print("\n" + "="*70)
        print("EXAMPLES:")
        print("="*70)
        print("\n1. Profile a specific command:")
        print("   python profiling/profiler.py --run 'python -m src.analysis.earnings_analyzer --tickers AAPL --yes'")
        print("\n2. Analyze a profile file:")
        print("   python profiling/profiler.py --analyze profiling/results/my_profile.prof")
        print("\n3. Find hotspots:")
        print("   python profiling/profiler.py --hotspots profiling/results/my_profile.prof")
        print("\n4. Compare two profiles:")
        print("   python profiling/profiler.py --compare baseline.prof new.prof")
        print("\n5. Run example:")
        print("   python profiling/profiler.py --example")
        print("\n" + "="*70)
