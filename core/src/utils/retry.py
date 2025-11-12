"""Retry decorator with exponential backoff for resilient API calls."""

import asyncio
import random
import logging
import time
from functools import wraps
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


def async_retry(max_attempts=3, backoff_base=2.0, max_backoff=60.0, jitter=True, exceptions=None):
    """Async retry with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        backoff_base: Base for exponential backoff calculation
        max_backoff: Maximum backoff time in seconds
        jitter: Add random jitter to backoff time
        exceptions: Tuple of exceptions to catch (default: all exceptions)

    Returns:
        Decorator function that wraps async functions with retry logic
    """
    if exceptions is None:
        exceptions = (Exception,)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts - 1:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise

                    backoff = min(backoff_base ** attempt, max_backoff)
                    if jitter:
                        backoff += random.uniform(0, backoff * 0.1)

                    logger.warning(f"{func.__name__} retry in {backoff:.2f}s (attempt {attempt+1}/{max_attempts}): {e}")
                    await asyncio.sleep(backoff)

        return wrapper
    return decorator


def sync_retry(max_attempts=3, backoff_base=2.0, max_backoff=60.0, jitter=True, exceptions=None):
    """Sync retry with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        backoff_base: Base for exponential backoff calculation
        max_backoff: Maximum backoff time in seconds
        jitter: Add random jitter to backoff time
        exceptions: Tuple of exceptions to catch (default: all exceptions)

    Returns:
        Decorator function that wraps sync functions with retry logic
    """
    if exceptions is None:
        exceptions = (Exception,)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts - 1:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise

                    backoff = min(backoff_base ** attempt, max_backoff)
                    if jitter:
                        backoff += random.uniform(0, backoff * 0.1)

                    logger.warning(f"{func.__name__} retry in {backoff:.2f}s (attempt {attempt+1}/{max_attempts}): {e}")
                    time.sleep(backoff)

        return wrapper
    return decorator
