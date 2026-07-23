"""Timeout utilities for agent execution.

Provides helper functions for managing timeouts in async agent orchestration.
"""

import asyncio
from typing import List, Any, Optional


async def gather_with_timeout(
    tasks: List[asyncio.Task],
    timeout: int,
    return_exceptions: bool = True
) -> List[Any]:
    """
    Gather all tasks with a global timeout.

    Args:
        tasks: List of asyncio tasks to execute
        timeout: Maximum execution time in seconds
        return_exceptions: If True, return exceptions instead of raising

    Returns:
        List of results or exceptions

    Example:
        tasks = [analyze_ticker("AAPL"), analyze_ticker("NVDA")]
        results = await gather_with_timeout(tasks, timeout=30)
    """
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=return_exceptions),
            timeout=timeout
        )
        return results
    except asyncio.TimeoutError:
        # Cancel all remaining tasks
        for task in tasks:
            if not task.done():
                task.cancel()

        # Return partial results
        results = []
        for task in tasks:
            if task.done():
                try:
                    results.append(task.result())
                except Exception as e:
                    results.append(e if return_exceptions else None)
            else:
                error = TimeoutError(f"Task timed out after {timeout}s")
                results.append(error if return_exceptions else None)

        return results


async def run_with_timeout(
    coro,
    timeout: int,
    default: Optional[Any] = None
) -> Any:
    """
    Run a single coroutine with timeout.

    Args:
        coro: Coroutine to execute
        timeout: Maximum execution time in seconds
        default: Value to return on timeout

    Returns:
        Coroutine result or default value on timeout

    Example:
        result = await run_with_timeout(
            analyze_ticker("AAPL"),
            timeout=30,
            default={"error": "timeout"}
        )
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return default
