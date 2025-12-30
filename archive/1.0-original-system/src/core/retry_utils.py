"""
Retry utilities with exponential backoff and circuit breaker for API calls.

Provides decorators and functions to handle transient failures like:
- Rate limiting (429 errors)
- Network timeouts
- Temporary API outages
- Persistent failures (circuit breaker)
"""

import time
import logging
from functools import wraps
from typing import Callable, Optional, Tuple, Type, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def retry_on_rate_limit(
    max_retries: int = 3,
    initial_backoff: float = 1.0,
    backoff_multiplier: float = 2.0,
    max_backoff: float = 60.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Decorator that retries a function with exponential backoff on rate limits.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        initial_backoff: Initial backoff time in seconds (default: 1.0)
        backoff_multiplier: Multiplier for exponential backoff (default: 2.0)
        max_backoff: Maximum backoff time in seconds (default: 60.0)
        exceptions: Tuple of exceptions to catch (default: all Exceptions)

    Returns:
        Decorator function

    Example:
        @retry_on_rate_limit(max_retries=3, initial_backoff=2.0)
        def fetch_data(ticker):
            # API call that might be rate limited
            return api.get(ticker)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            backoff = initial_backoff

            for attempt in range(max_retries + 1):  # +1 for initial attempt
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    error_msg = str(e).lower()

                    # Check if this is a rate limit error
                    is_rate_limit = (
                        'rate limit' in error_msg or
                        '429' in error_msg or
                        'too many requests' in error_msg or
                        'quota' in error_msg
                    )

                    # Check if this is a transient error worth retrying
                    is_transient = (
                        is_rate_limit or
                        'timeout' in error_msg or
                        'connection' in error_msg or
                        'unavailable' in error_msg or
                        '503' in error_msg or
                        '502' in error_msg
                    )

                    # Last attempt or non-retryable error
                    if attempt == max_retries or not is_transient:
                        logger.error(f"Failed after {attempt + 1} attempts: {e}")
                        raise

                    # Calculate backoff with exponential increase
                    sleep_time = min(backoff, max_backoff)

                    if is_rate_limit:
                        logger.warning(
                            f"Rate limited (attempt {attempt + 1}/{max_retries + 1}), "
                            f"retrying in {sleep_time:.1f}s... Error: {e}"
                        )
                    else:
                        logger.warning(
                            f"Transient error (attempt {attempt + 1}/{max_retries + 1}), "
                            f"retrying in {sleep_time:.1f}s... Error: {e}"
                        )

                    time.sleep(sleep_time)
                    backoff *= backoff_multiplier

            # Should never reach here, but just in case
            raise RuntimeError(f"Retry logic failed after {max_retries} attempts")

        return wrapper
    return decorator


def retry_with_backoff(
    func: Callable,
    *args,
    max_retries: int = 3,
    initial_backoff: float = 1.0,
    backoff_multiplier: float = 2.0,
    **kwargs
):
    """
    Retry a function call with exponential backoff (functional interface).

    This is the functional equivalent of the @retry_on_rate_limit decorator.
    Use this when you can't use a decorator (e.g., lambda functions, dynamic calls).

    Args:
        func: Function to call
        *args: Positional arguments to pass to func
        max_retries: Maximum number of retry attempts
        initial_backoff: Initial backoff time in seconds
        backoff_multiplier: Multiplier for exponential backoff
        **kwargs: Keyword arguments to pass to func

    Returns:
        Result of func(*args, **kwargs)

    Example:
        result = retry_with_backoff(
            api.get_quote,
            'AAPL',
            max_retries=3,
            initial_backoff=2.0
        )
    """
    backoff = initial_backoff

    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)

        except Exception as e:
            error_msg = str(e).lower()

            is_rate_limit = (
                'rate limit' in error_msg or
                '429' in error_msg or
                'too many requests' in error_msg
            )

            if attempt == max_retries or not is_rate_limit:
                raise

            sleep_time = min(backoff, 60.0)
            logger.warning(f"Rate limited, retrying in {sleep_time:.1f}s...")
            time.sleep(sleep_time)
            backoff *= backoff_multiplier

    raise RuntimeError(f"Retry logic failed after {max_retries} attempts")


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and prevents function execution."""
    pass


class CircuitBreaker:
    """
    Circuit breaker pattern for API calls.

    Prevents cascading failures by opening the circuit after N consecutive failures.
    When open, calls fail immediately without hitting the API.
    After a timeout period, enters half-open state to test if service recovered.

    States:
        - CLOSED: Normal operation, all calls go through
        - OPEN: Circuit is open, all calls fail immediately
        - HALF_OPEN: Testing if service recovered, single call allowed

    Example:
        breaker = CircuitBreaker(failure_threshold=5, timeout_seconds=60)

        @breaker.call
        def fetch_data():
            return api.get_data()

        # Or use as context manager
        with breaker:
            result = api.get_data()
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_seconds: int = 60,
        expected_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of consecutive failures before opening circuit
            timeout_seconds: Seconds to wait before attempting recovery (half-open)
            expected_exceptions: Exceptions that count as failures
        """
        self.failure_threshold = failure_threshold
        self.timeout = timedelta(seconds=timeout_seconds)
        self.expected_exceptions = expected_exceptions

        # State tracking
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = 'closed'  # closed, open, half_open
        self.success_count = 0  # For half-open state

    def call(self, func: Callable) -> Callable:
        """
        Decorator to wrap function with circuit breaker.

        Args:
            func: Function to protect with circuit breaker

        Returns:
            Wrapped function

        Example:
            breaker = CircuitBreaker()

            @breaker.call
            def api_request():
                return requests.get('https://api.example.com')
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Check circuit state before calling
            if self.state == 'open':
                if self._should_attempt_reset():
                    self.state = 'half_open'
                    logger.info(f"Circuit breaker entering HALF_OPEN state for {func.__name__}")
                else:
                    time_remaining = (
                        self.last_failure_time + self.timeout - datetime.now()
                    ).total_seconds()
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker OPEN for {func.__name__}. "
                        f"Retry in {time_remaining:.0f}s. "
                        f"({self.failure_count} consecutive failures)"
                    )

            # Execute function
            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result

            except self.expected_exceptions as e:
                self._on_failure()
                raise

        return wrapper

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return True
        return datetime.now() >= self.last_failure_time + self.timeout

    def _on_success(self):
        """Handle successful call."""
        if self.state == 'half_open':
            # Success in half-open state, close the circuit
            logger.info("Circuit breaker CLOSED after successful recovery")
            self.state = 'closed'

        # Reset failure tracking
        self.failure_count = 0
        self.last_failure_time = None

    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.failure_count >= self.failure_threshold:
            if self.state != 'open':
                logger.error(
                    f"Circuit breaker OPENED after {self.failure_count} consecutive failures. "
                    f"Will retry in {self.timeout.total_seconds():.0f}s"
                )
            self.state = 'open'
        else:
            logger.warning(
                f"Circuit breaker failure count: {self.failure_count}/{self.failure_threshold}"
            )

    def reset(self):
        """Manually reset the circuit breaker to closed state."""
        logger.info("Circuit breaker manually reset to CLOSED state")
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'closed'

    def get_state(self) -> Dict[str, Any]:
        """
        Get current circuit breaker state.

        Returns:
            Dictionary with state information
        """
        return {
            'state': self.state,
            'failure_count': self.failure_count,
            'failure_threshold': self.failure_threshold,
            'last_failure': self.last_failure_time.isoformat() if self.last_failure_time else None,
            'timeout_seconds': self.timeout.total_seconds()
        }

    # Context manager support
    def __enter__(self):
        """Context manager entry."""
        if self.state == 'open' and not self._should_attempt_reset():
            time_remaining = (
                self.last_failure_time + self.timeout - datetime.now()
            ).total_seconds()
            raise CircuitBreakerOpenError(
                f"Circuit breaker OPEN. Retry in {time_remaining:.0f}s"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if exc_type is None:
            # Success
            self._on_success()
            return False
        elif isinstance(exc_val, self.expected_exceptions):
            # Expected failure
            self._on_failure()
            return False  # Re-raise the exception
        else:
            # Unexpected exception, don't count as failure
            return False


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    @retry_on_rate_limit(max_retries=3, initial_backoff=1.0)
    def example_api_call(fail_count: int = 0):
        """Simulates an API call that fails a certain number of times."""
        if not hasattr(example_api_call, 'attempts'):
            example_api_call.attempts = 0

        example_api_call.attempts += 1

        if example_api_call.attempts <= fail_count:
            raise Exception("429 Too Many Requests")

        return f"Success on attempt {example_api_call.attempts}"

    # Test 1: Success on first try
    print("\nTest 1: Success on first try")
    example_api_call.attempts = 0
    result = example_api_call(fail_count=0)
    print(f"Result: {result}")

    # Test 2: Success after 2 retries
    print("\nTest 2: Success after 2 retries")
    example_api_call.attempts = 0
    result = example_api_call(fail_count=2)
    print(f"Result: {result}")

    # Test 3: Fail after max retries
    print("\nTest 3: Fail after max retries")
    try:
        example_api_call.attempts = 0
        result = example_api_call(fail_count=10)
    except Exception as e:
        print(f"Failed as expected: {e}")

    # Circuit Breaker Tests
    print("\n" + "="*60)
    print("CIRCUIT BREAKER TESTS")
    print("="*60)

    breaker = CircuitBreaker(failure_threshold=3, timeout_seconds=2)

    @breaker.call
    def unstable_api(should_fail: bool = False):
        """Simulates an unstable API."""
        if should_fail:
            raise Exception("API Error")
        return "Success"

    # Test 1: Normal operation (circuit closed)
    print("\nTest 1: Circuit CLOSED - successful calls")
    for i in range(2):
        result = unstable_api(should_fail=False)
        print(f"  Call {i+1}: {result}, State: {breaker.get_state()['state']}")

    # Test 2: Trigger circuit breaker (3 failures)
    print("\nTest 2: Triggering circuit breaker with failures")
    for i in range(3):
        try:
            unstable_api(should_fail=True)
        except Exception as e:
            print(f"  Call {i+1} failed: {e}, State: {breaker.get_state()['state']}")

    # Test 3: Circuit OPEN - calls fail immediately
    print("\nTest 3: Circuit OPEN - immediate failures")
    try:
        unstable_api(should_fail=False)
    except CircuitBreakerOpenError as e:
        print(f"  Circuit open (as expected): {e}")

    # Test 4: Wait for timeout and recovery
    print("\nTest 4: Waiting for timeout (2 seconds)...")
    time.sleep(2.1)
    print("  Attempting call (circuit should enter HALF_OPEN)")
    result = unstable_api(should_fail=False)
    print(f"  Success! {result}, State: {breaker.get_state()['state']}")
