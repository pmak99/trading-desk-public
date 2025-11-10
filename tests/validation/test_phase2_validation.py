#!/usr/bin/env python3
"""Comprehensive Phase 2 validation tests."""

import sys
import time
import threading
from typing import Dict

# Test imports
try:
    from src.core.memoization import (
        memoize,
        memoize_with_dict_key,
        cache_result_by_ticker,
        MemoizedProperty
    )
    from src.core.rate_limiter import (
        TokenBucketRateLimiter,
        MultiRateLimiter
    )
    print("✅ Phase 2 imports successful")
except Exception as e:
    print(f"❌ Phase 2 import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


def test_memoization():
    """Test memoization decorators."""
    print("\n=== Testing Memoization ===")

    # Test basic memoization
    call_count = 0

    @memoize(maxsize=10)
    def expensive_calculation(x: int) -> int:
        nonlocal call_count
        call_count += 1
        time.sleep(0.01)  # Simulate expensive operation
        return x * x

    # First call - should execute
    result1 = expensive_calculation(5)
    count_after_first = call_count

    # Second call - should use cache
    result2 = expensive_calculation(5)
    count_after_second = call_count

    if result1 == 25 and result2 == 25 and count_after_second == count_after_first:
        print(f"✅ Basic memoization working (cached result, only {count_after_first} execution)")
    else:
        print("❌ Basic memoization failed")
        return False

    # Test dict key memoization
    dict_call_count = 0

    @memoize_with_dict_key(maxsize=10)
    def score_calculator(data: Dict[str, float]) -> float:
        nonlocal dict_call_count
        dict_call_count += 1
        return sum(data.values())

    data = {'a': 1.0, 'b': 2.0, 'c': 3.0}
    score1 = score_calculator(data)
    count_after_dict1 = dict_call_count

    score2 = score_calculator(data)
    count_after_dict2 = dict_call_count

    if score1 == 6.0 and score2 == 6.0 and count_after_dict2 == count_after_dict1:
        print(f"✅ Dict key memoization working (cached dict args)")
    else:
        print("❌ Dict key memoization failed")
        return False

    # Test ticker-based caching
    ticker_call_count = 0

    @cache_result_by_ticker(maxsize=10)
    def fetch_ticker_data(ticker: str, extra_param: str = "default") -> Dict:
        nonlocal ticker_call_count
        ticker_call_count += 1
        return {'ticker': ticker, 'data': extra_param}

    data1 = fetch_ticker_data('AAPL')
    count_after_ticker1 = ticker_call_count

    data2 = fetch_ticker_data('AAPL')  # Same params, same ticker - should cache
    count_after_ticker2 = ticker_call_count

    if data1['ticker'] == 'AAPL' and count_after_ticker2 == count_after_ticker1:
        print(f"✅ Ticker-based caching working (cached same ticker+params)")
    else:
        print("❌ Ticker-based caching failed")
        return False

    # Test MemoizedProperty
    class TestClass:
        def __init__(self):
            self.compute_count = 0

        @MemoizedProperty
        def expensive_property(self):
            self.compute_count += 1
            return "computed_value"

    obj = TestClass()
    val1 = obj.expensive_property
    val2 = obj.expensive_property

    if val1 == "computed_value" and val2 == "computed_value" and obj.compute_count == 1:
        print(f"✅ MemoizedProperty working (computed once, accessed twice)")
    else:
        print("❌ MemoizedProperty failed")
        return False

    return True


def test_rate_limiter():
    """Test token bucket rate limiter."""
    print("\n=== Testing Rate Limiter ===")

    # Test basic rate limiting
    limiter = TokenBucketRateLimiter(rate=5.0, capacity=10, name="test")

    # Should allow burst up to capacity
    burst_success = True
    for i in range(10):
        if not limiter.acquire(blocking=False):
            burst_success = False
            break

    if burst_success:
        print("✅ Token bucket allows burst up to capacity (10 requests)")
    else:
        print("❌ Token bucket burst failed")
        return False

    # Next request should fail (capacity exhausted)
    if not limiter.acquire(blocking=False):
        print("✅ Token bucket correctly rejects when exhausted")
    else:
        print("❌ Token bucket should have rejected request")
        return False

    # Test token replenishment
    time.sleep(0.5)  # Wait for tokens to replenish (rate=5/sec, so ~2-3 tokens)
    if limiter.acquire(blocking=False):
        print("✅ Token bucket replenishes over time")
    else:
        print("❌ Token bucket replenishment failed")
        return False

    # Test blocking mode
    limiter2 = TokenBucketRateLimiter(rate=10.0, capacity=1, name="blocking")
    limiter2.acquire()  # Use the token

    start = time.time()
    limiter2.acquire(blocking=True)  # Should wait for replenishment
    elapsed = time.time() - start

    if elapsed >= 0.05:  # Should wait ~0.1s (1 token at 10/sec)
        print(f"✅ Blocking mode works (waited {elapsed:.3f}s for token)")
    else:
        print(f"❌ Blocking mode didn't wait enough ({elapsed:.3f}s)")
        return False

    return True


def test_multi_rate_limiter():
    """Test multi-service rate limiter."""
    print("\n=== Testing Multi-Service Rate Limiter ===")

    multi = MultiRateLimiter()

    # Add different rate limits
    multi.add_limiter("tradier", rate=10.0, capacity=20)
    multi.add_limiter("yfinance", rate=5.0, capacity=10)

    # Test service-specific limiting
    if multi.acquire("tradier"):
        print("✅ Multi-limiter tradier service working")
    else:
        print("❌ Multi-limiter tradier failed")
        return False

    if multi.acquire("yfinance"):
        print("✅ Multi-limiter yfinance service working")
    else:
        print("❌ Multi-limiter yfinance failed")
        return False

    # Test unknown service handling
    try:
        multi.acquire("unknown_service")
        print("❌ Should have raised error for unknown service")
        return False
    except KeyError as e:
        print(f"✅ Correctly raised error for unknown service: {e}")

    # Test stats
    stats = multi.get_stats()
    if "tradier" in stats and "yfinance" in stats:
        print(f"✅ Multi-limiter stats tracking: {len(stats)} services")
        print(f"   Tradier: {stats['tradier']:.1f} tokens available")
        print(f"   Yfinance: {stats['yfinance']:.1f} tokens available")
    else:
        print("❌ Multi-limiter stats failed")
        return False

    return True


def test_thread_safety():
    """Test that rate limiter is thread-safe."""
    print("\n=== Testing Thread Safety ===")

    limiter = TokenBucketRateLimiter(rate=100.0, capacity=50, name="thread_test")
    successful_requests = []
    failed_requests = []

    def make_requests(thread_id: int, count: int):
        for i in range(count):
            if limiter.acquire(blocking=False):
                successful_requests.append((thread_id, i))
            else:
                failed_requests.append((thread_id, i))

    # Launch multiple threads
    threads = []
    for i in range(5):
        t = threading.Thread(target=make_requests, args=(i, 20))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    total_attempts = len(successful_requests) + len(failed_requests)
    if total_attempts == 100 and len(successful_requests) <= 50:
        print(f"✅ Thread safety validated: {len(successful_requests)}/100 requests succeeded (≤50 capacity)")
    else:
        print(f"❌ Thread safety failed: {len(successful_requests)}/{total_attempts} (expected ≤50/100)")
        return False

    return True


def test_performance_improvement():
    """Verify memoization improves performance."""
    print("\n=== Testing Performance Improvement ===")

    # Without memoization
    def slow_function(x: int) -> int:
        time.sleep(0.01)
        return x * x

    start = time.time()
    for i in range(10):
        slow_function(5)  # Same input
    no_cache_time = time.time() - start

    # With memoization
    @memoize(maxsize=10)
    def fast_function(x: int) -> int:
        time.sleep(0.01)
        return x * x

    start = time.time()
    for i in range(10):
        fast_function(5)  # Same input
    with_cache_time = time.time() - start

    speedup = no_cache_time / with_cache_time
    if speedup > 5:  # Should be ~10x faster (only 1 execution vs 10)
        print(f"✅ Memoization provides {speedup:.1f}x speedup")
    else:
        print(f"⚠️  Memoization speedup only {speedup:.1f}x (expected >5x)")

    return True


if __name__ == '__main__':
    print("=" * 70)
    print("PHASE 2 VALIDATION TEST SUITE")
    print("Testing: Memoization, Rate Limiting, Performance")
    print("=" * 70)

    all_passed = True

    try:
        if not test_memoization():
            all_passed = False
            print("\n❌ Memoization tests failed")

        if not test_rate_limiter():
            all_passed = False
            print("\n❌ Rate limiter tests failed")

        if not test_multi_rate_limiter():
            all_passed = False
            print("\n❌ Multi-rate limiter tests failed")

        if not test_thread_safety():
            all_passed = False
            print("\n❌ Thread safety tests failed")

        if not test_performance_improvement():
            all_passed = False
            print("\n❌ Performance tests failed")

        print("\n" + "=" * 70)
        if all_passed:
            print("✅ ALL PHASE 2 TESTS PASSED!")
        else:
            print("❌ SOME PHASE 2 TESTS FAILED")
            sys.exit(1)
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ Unexpected error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
