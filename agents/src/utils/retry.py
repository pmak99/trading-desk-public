"""Async retry utility with exponential backoff.

Provides retry logic for transient failures (timeouts, connection errors, 429/500)
while failing fast on permanent errors (ValueError, auth failures, no data).
"""

import asyncio
import logging
from typing import Callable, Awaitable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Errors that should NOT be retried (permanent failures)
_PERMANENT_ERRORS = (
    ValueError,
    TypeError,
    KeyError,
    PermissionError,
    FileNotFoundError,
)

# HTTP status codes that indicate transient failures
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def is_transient_error(error: Exception) -> bool:
    """Classify whether an error is transient (retryable) or permanent.

    Args:
        error: The exception to classify

    Returns:
        True if the error is transient and should be retried
    """
    # Permanent errors - never retry
    if isinstance(error, _PERMANENT_ERRORS):
        return False

    # Auth failures - never retry
    error_str = str(error).lower()
    if any(term in error_str for term in ['unauthorized', '401', '403', 'forbidden', 'invalid api key']):
        return False

    # "No data" type errors - never retry
    if any(term in error_str for term in ['no data', 'not found', 'no earnings', 'invalid ticker']):
        return False

    # Network/timeout errors - always retry
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)):
        return True

    # HTTP status code in error message - check if transient
    for code in _TRANSIENT_STATUS_CODES:
        if str(code) in error_str:
            return True

    # Rate limit indicators
    if 'rate limit' in error_str or 'too many requests' in error_str:
        return True

    # Default: retry on unknown errors (conservative - prefer retry over silent failure)
    return True


async def with_retry(
    coro_factory: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 2.0,
    label: str = ""
) -> T:
    """Execute an async operation with exponential backoff retry.

    Args:
        coro_factory: Callable that returns a new coroutine on each call.
                     Must be a factory (not a coroutine) since coroutines
                     can only be awaited once.
        max_retries: Maximum number of retry attempts (0 = no retries)
        base_delay: Base delay in seconds (doubles each retry: 2s, 4s, 8s)
        label: Label for logging (e.g., "TickerAnalysis(PLTR)")

    Returns:
        The result of the coroutine

    Raises:
        The last exception if all retries are exhausted

    Example:
        result = await with_retry(
            lambda: agent.analyze(ticker, date),
            max_retries=3,
            base_delay=2.0,
            label=f"TickerAnalysis({ticker})"
        )
    """
    last_error = None
    prefix = f"[{label}] " if label else ""

    for attempt in range(1 + max_retries):
        try:
            return await coro_factory()
        except Exception as e:
            last_error = e

            # Don't retry permanent errors
            if not is_transient_error(e):
                logger.debug(f"{prefix}Permanent error (no retry): {type(e).__name__}: {e}")
                raise

            # Don't retry if we've exhausted retries
            if attempt >= max_retries:
                logger.warning(
                    f"{prefix}Failed after {1 + max_retries} attempts: {type(e).__name__}: {e}"
                )
                raise

            # Calculate delay with exponential backoff
            delay = base_delay * (2 ** attempt)
            logger.info(
                f"{prefix}Attempt {attempt + 1}/{1 + max_retries} failed "
                f"({type(e).__name__}: {e}), retrying in {delay:.1f}s"
            )
            await asyncio.sleep(delay)

    # Should never reach here, but just in case
    raise last_error  # type: ignore[misc]
