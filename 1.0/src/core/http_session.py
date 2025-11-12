"""
HTTP session management with connection pooling and retry logic.

Provides optimized HTTP sessions for API clients with:
- Connection pooling (reuses TCP connections)
- Automatic retries with exponential backoff
- Configurable timeouts
- Better performance for multiple requests

Note: yfinance handles sessions internally. This is for future custom API clients.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages HTTP sessions with connection pooling and retry logic.

    Features:
    - Connection pooling (reuses TCP connections for speed)
    - Automatic retries on transient failures
    - Exponential backoff to avoid hammering failing servers
    - Configurable pool size and timeouts

    Usage:
        session = SessionManager.get_session(pool_size=20, retries=3)
        response = session.get('https://api.example.com/data')
    """

    _session_cache = {}

    @classmethod
    def get_session(
        cls,
        pool_connections: int = 10,
        pool_maxsize: int = 20,
        max_retries: int = 3,
        backoff_factor: float = 0.3,
        timeout: int = 10
    ) -> requests.Session:
        """
        Get or create an optimized HTTP session.

        Args:
            pool_connections: Number of connection pools to cache (default: 10)
            pool_maxsize: Maximum connections per pool (default: 20)
            max_retries: Maximum retry attempts on failures (default: 3)
            backoff_factor: Backoff multiplier between retries (default: 0.3)
            timeout: Default timeout in seconds (default: 10)

        Returns:
            Configured requests.Session with pooling and retries

        Example:
            >>> session = SessionManager.get_session(pool_size=20)
            >>> response = session.get('https://api.example.com/data', timeout=15)
        """
        cache_key = f"{pool_connections}_{pool_maxsize}_{max_retries}"

        if cache_key in cls._session_cache:
            return cls._session_cache[cache_key]

        # Create new session
        session = requests.Session()

        # Configure retry strategy
        # Retries on: connection errors, timeouts, 500/502/503/504 errors
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
            raise_on_status=False  # Don't raise, let caller handle
        )

        # Configure HTTP adapter with connection pooling
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize
        )

        # Mount adapter for both HTTP and HTTPS
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Set default timeout (can be overridden per request)
        session.request = lambda *args, **kwargs: requests.Session.request(
            session, *args, timeout=kwargs.get('timeout', timeout), **kwargs
        )

        # Cache for reuse
        cls._session_cache[cache_key] = session

        logger.debug(
            f"Created HTTP session: pool_size={pool_maxsize}, "
            f"retries={max_retries}, timeout={timeout}s"
        )

        return session

    @classmethod
    def close_all(cls):
        """Close all cached sessions (cleanup)."""
        for session in cls._session_cache.values():
            session.close()
        cls._session_cache.clear()
        logger.debug("Closed all HTTP sessions")

    @classmethod
    def get_optimized_session(cls) -> requests.Session:
        """
        Get session optimized for trading APIs.

        Pre-configured with:
        - Large pool size (20 connections)
        - Aggressive retries (5 attempts)
        - Longer timeout (15 seconds)

        Returns:
            Optimized requests.Session
        """
        return cls.get_session(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=5,
            backoff_factor=0.5,
            timeout=15
        )


# Convenience function for simple use cases
def get_session() -> requests.Session:
    """
    Get default optimized HTTP session.

    Returns:
        Configured session with connection pooling and retries
    """
    return SessionManager.get_session()


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    # Get optimized session
    session = SessionManager.get_optimized_session()

    # Make requests (connection will be reused)
    print("Making 3 requests with connection pooling...")
    for i in range(3):
        response = session.get('https://httpbin.org/delay/1')
        print(f"  Request {i+1}: {response.status_code} - {response.elapsed.total_seconds():.2f}s")

    # Cleanup
    SessionManager.close_all()
