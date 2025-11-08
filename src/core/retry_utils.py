"""
Retry utilities with exponential backoff for API calls.

Provides decorators and functions to handle transient failures like:
- Rate limiting (429 errors)
- Network timeouts
- Temporary API outages
"""

import time
import logging
from functools import wraps
from typing import Callable, Optional, Tuple, Type

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
