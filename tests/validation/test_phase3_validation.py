#!/usr/bin/env python3
"""Comprehensive Phase 3 validation tests."""

import sys
import time
from typing import Optional, List

# Test imports
try:
    from src.core.circuit_breaker import (
        CircuitBreaker,
        CircuitState,
        CircuitBreakerOpenError,
        CircuitBreakerManager
    )
    from src.core.repository import (
        Repository,
        TickerDataRepository,
        OptionsDataRepository
    )
    from src.core.types import TickerData, OptionsData
    print("✅ Phase 3 imports successful")
except Exception as e:
    print(f"❌ Phase 3 import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


def test_circuit_breaker_states():
    """Test circuit breaker state transitions."""
    print("\n=== Testing Circuit Breaker States ===")

    failure_count = 0

    def failing_function():
        nonlocal failure_count
        failure_count += 1
        raise ValueError("Simulated failure")

    def successful_function():
        return "success"

    # Create circuit breaker with low threshold for testing
    cb = CircuitBreaker(
        failure_threshold=3,
        recovery_timeout=1.0,
        name="test_circuit"
    )

    # Initial state should be CLOSED
    if cb.state == CircuitState.CLOSED:
        print("✅ Circuit breaker starts in CLOSED state")
    else:
        print(f"❌ Circuit breaker should start CLOSED, got {cb.state}")
        return False

    # Trigger failures to open circuit
    for i in range(3):
        try:
            cb.call(failing_function)
        except ValueError:
            pass  # Expected

    # Circuit should now be OPEN
    if cb.state == CircuitState.OPEN:
        print(f"✅ Circuit breaker opened after {failure_count} failures")
    else:
        print(f"❌ Circuit breaker should be OPEN, got {cb.state}")
        return False

    # Calls should be rejected while open
    try:
        cb.call(successful_function)
        print("❌ Circuit breaker should reject calls when OPEN")
        return False
    except CircuitBreakerOpenError:
        print("✅ Circuit breaker correctly rejects calls when OPEN")

    # Wait for recovery timeout
    time.sleep(1.1)

    # Next call should transition to HALF_OPEN
    try:
        result = cb.call(successful_function)
        if result == "success" and cb.state == CircuitState.CLOSED:
            print("✅ Circuit breaker recovered: HALF_OPEN → CLOSED on success")
        else:
            print(f"❌ Circuit breaker state unexpected: {cb.state}")
            return False
    except Exception as e:
        print(f"❌ Recovery call failed: {e}")
        return False

    return True


def test_circuit_breaker_with_decorator():
    """Test circuit breaker as a decorator."""
    print("\n=== Testing Circuit Breaker Decorator ===")

    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.5, name="decorator_test")

    call_count = 0
    should_fail = True

    @cb.protect
    def api_call():
        nonlocal call_count
        call_count += 1
        if should_fail:
            raise ConnectionError("API unreachable")
        return "data"

    # Trigger failures
    for _ in range(2):
        try:
            api_call()
        except ConnectionError:
            pass

    if cb.state == CircuitState.OPEN:
        print("✅ Decorator opened circuit after failures")
    else:
        print(f"❌ Circuit should be OPEN, got {cb.state}")
        return False

    # Circuit should reject calls
    try:
        api_call()
        print("❌ Should have raised CircuitBreakerOpenError")
        return False
    except CircuitBreakerOpenError:
        print("✅ Decorator correctly raises CircuitBreakerOpenError")

    # Fix the function and wait for recovery
    should_fail = False
    time.sleep(0.6)

    # Should recover
    try:
        result = api_call()
        if result == "data":
            print("✅ Circuit recovered and function executed successfully")
        else:
            print(f"❌ Unexpected result: {result}")
            return False
    except Exception as e:
        print(f"❌ Recovery failed: {e}")
        return False

    return True


def test_circuit_breaker_manager():
    """Test global circuit breaker manager."""
    print("\n=== Testing Circuit Breaker Manager ===")

    manager = CircuitBreakerManager()

    # Register circuit breakers
    cb1 = manager.add_breaker(
        name="api_service_1",
        failure_threshold=5,
        recovery_timeout=30
    )

    cb2 = manager.add_breaker(
        name="api_service_2",
        failure_threshold=3,
        recovery_timeout=60
    )

    # Get same breaker again
    cb1_again = manager.get_breaker("api_service_1")

    if cb1 is cb1_again:
        print("✅ Manager returns same circuit breaker instance")
    else:
        print("❌ Manager created duplicate circuit breaker")
        return False

    # Check stats
    stats = manager.get_all_stats()
    if len(stats) == 2 and "api_service_1" in stats and "api_service_2" in stats:
        print(f"✅ Manager tracking {len(stats)} circuit breakers")
        print(f"   Service 1: {stats['api_service_1']['state']}")
        print(f"   Service 2: {stats['api_service_2']['state']}")
    else:
        print("❌ Manager stats incorrect")
        return False

    # Reset all
    manager.reset_all()
    if cb1.state == CircuitState.CLOSED and cb2.state == CircuitState.CLOSED:
        print("✅ Manager reset all circuit breakers")
    else:
        print("❌ Manager reset failed")
        return False

    return True


def test_repository_pattern():
    """Test repository pattern implementation."""
    print("\n=== Testing Repository Pattern ===")

    # Create a simple test repository
    class TestRepository(Repository[str]):
        def __init__(self):
            self.fetch_count = 0
            self.data_store = {"key1": "value1", "key2": "value2"}

        def get(self, id: str) -> Optional[str]:
            self.fetch_count += 1
            return self.data_store.get(id)

        def get_many(self, ids: List[str]) -> List[str]:
            self.fetch_count += len(ids)
            return [self.data_store.get(id) for id in ids if id in self.data_store]

        def save(self, item: str) -> None:
            # Simple save - just store the item with its value as key
            self.data_store[item] = item

        def delete(self, id: str) -> bool:
            if id in self.data_store:
                del self.data_store[id]
                return True
            return False

        def exists(self, id: str) -> bool:
            return id in self.data_store

    repo = TestRepository()

    # Test get
    val1 = repo.get("key1")
    if val1 == "value1":
        print("✅ Repository get() works")
    else:
        print(f"❌ Repository get() failed: got {val1}")
        return False

    # Test get_many
    vals = repo.get_many(["key1", "key2"])
    if len(vals) == 2 and "value1" in vals and "value2" in vals:
        print("✅ Repository get_many() works")
    else:
        print(f"❌ Repository get_many() failed")
        return False

    # Test exists
    if repo.exists("key1") and not repo.exists("nonexistent"):
        print("✅ Repository exists() works")
    else:
        print("❌ Repository exists() failed")
        return False

    # Test save
    repo.save("key3")
    if repo.exists("key3"):
        print("✅ Repository save() works")
    else:
        print("❌ Repository save() failed")
        return False

    # Test delete
    deleted = repo.delete("key1")
    if deleted and not repo.exists("key1"):
        print("✅ Repository delete() works")
    else:
        print("❌ Repository delete() failed")
        return False

    return True


def test_repository_concrete_implementations():
    """Test concrete repository implementations."""
    print("\n=== Testing Concrete Repository Implementations ===")

    # Test that TickerDataRepository and OptionsDataRepository exist and follow pattern
    # We can't fully test them without dependencies, but we can verify the classes exist

    if TickerDataRepository and OptionsDataRepository:
        print("✅ TickerDataRepository class exists")
        print("✅ OptionsDataRepository class exists")
    else:
        print("❌ Repository classes not found")
        return False

    # Verify they inherit from Repository
    from src.core.repository import Repository as BaseRepo
    if issubclass(TickerDataRepository, BaseRepo) and issubclass(OptionsDataRepository, BaseRepo):
        print("✅ Concrete repositories inherit from Repository base class")
    else:
        print("❌ Repository inheritance failed")
        return False

    return True


if __name__ == '__main__':
    print("=" * 70)
    print("PHASE 3 VALIDATION TEST SUITE")
    print("Testing: Circuit Breaker, Repository Pattern")
    print("=" * 70)

    all_passed = True

    try:
        if not test_circuit_breaker_states():
            all_passed = False
            print("\n❌ Circuit breaker state tests failed")

        if not test_circuit_breaker_with_decorator():
            all_passed = False
            print("\n❌ Circuit breaker decorator tests failed")

        if not test_circuit_breaker_manager():
            all_passed = False
            print("\n❌ Circuit breaker manager tests failed")

        if not test_repository_pattern():
            all_passed = False
            print("\n❌ Repository pattern tests failed")

        if not test_repository_concrete_implementations():
            all_passed = False
            print("\n❌ Repository concrete implementation tests failed")

        print("\n" + "=" * 70)
        if all_passed:
            print("✅ ALL PHASE 3 TESTS PASSED!")
        else:
            print("❌ SOME PHASE 3 TESTS FAILED")
            sys.exit(1)
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ Unexpected error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
