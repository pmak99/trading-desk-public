#!/usr/bin/env python
"""Live test for maintenance tasks.

Tests:
1. Health check
2. Data quality scanning
3. Cache cleanup
"""

import sys
from pathlib import Path
import subprocess

# Add 6.0/ to path (parent of src/)
_6_0_dir = Path(__file__).parent.parent
sys.path.insert(0, str(_6_0_dir))


def test_health_check():
    """Test health check maintenance task."""
    print("=" * 60)
    print("LIVE TEST: Health Check")
    print("=" * 60)
    print()

    result = subprocess.run(
        [sys.executable, '-m', 'src.cli.maintenance', 'health'],
        cwd=_6_0_dir,
        capture_output=True,
        text=True
    )

    print(result.stdout)

    if result.returncode == 0:
        print("✓ Health check passed")
        return True
    else:
        print("✗ Health check failed")
        print(result.stderr)
        return False


def test_data_quality():
    """Test data quality scanning."""
    print()
    print("=" * 60)
    print("LIVE TEST: Data Quality Scan")
    print("=" * 60)
    print()

    result = subprocess.run(
        [sys.executable, '-m', 'src.cli.maintenance', 'data-quality'],
        cwd=_6_0_dir,
        capture_output=True,
        text=True
    )

    print(result.stdout)

    # Exit code 0 = healthy or warnings, 1 = critical issues
    if result.returncode in [0, 1]:
        print("✓ Data quality scan completed")
        return True
    else:
        print("✗ Data quality scan failed")
        print(result.stderr)
        return False


def test_cache_cleanup():
    """Test cache cleanup."""
    print()
    print("=" * 60)
    print("LIVE TEST: Cache Cleanup")
    print("=" * 60)
    print()

    result = subprocess.run(
        [sys.executable, '-m', 'src.cli.maintenance', 'cache-cleanup'],
        cwd=_6_0_dir,
        capture_output=True,
        text=True
    )

    print(result.stdout)

    if result.returncode == 0:
        print("✓ Cache cleanup passed")
        return True
    else:
        print("✗ Cache cleanup failed")
        print(result.stderr)
        return False


if __name__ == '__main__':
    print()
    print("Running maintenance task tests...")
    print()

    success1 = test_health_check()
    success2 = test_data_quality()
    success3 = test_cache_cleanup()

    print()
    if success1 and success2 and success3:
        print("ALL TESTS PASSED ✓✓✓")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED ✗")
        sys.exit(1)
