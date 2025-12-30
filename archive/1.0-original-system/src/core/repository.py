"""
Repository pattern for data access abstraction.

Provides a clean separation between data access logic and business logic,
making it easier to test and swap data sources.
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Optional, List, Dict, Any
import logging

from src.core.types import TickerData, OptionsData

logger = logging.getLogger(__name__)

T = TypeVar('T')


class Repository(ABC, Generic[T]):
    """
    Abstract base repository for data access operations.

    Provides a consistent interface for CRUD operations across different
    data sources (API, database, cache, etc.).
    """

    @abstractmethod
    def get(self, id: str) -> Optional[T]:
        """
        Retrieve a single item by ID.

        Args:
            id: Item identifier

        Returns:
            Item if found, None otherwise
        """
        pass

    @abstractmethod
    def get_many(self, ids: List[str]) -> List[T]:
        """
        Retrieve multiple items by IDs.

        Args:
            ids: List of identifiers

        Returns:
            List of found items (may be fewer than requested)
        """
        pass

    @abstractmethod
    def save(self, item: T) -> None:
        """
        Save an item to the repository.

        Args:
            item: Item to save
        """
        pass

    @abstractmethod
    def delete(self, id: str) -> bool:
        """
        Delete an item from the repository.

        Args:
            id: Item identifier

        Returns:
            True if deleted, False if not found
        """
        pass

    @abstractmethod
    def exists(self, id: str) -> bool:
        """
        Check if an item exists.

        Args:
            id: Item identifier

        Returns:
            True if exists, False otherwise
        """
        pass


class TickerDataRepository(Repository[TickerData]):
    """
    Repository for ticker data with caching support.

    Coordinates between multiple data sources (API, cache, database)
    to provide efficient ticker data access.

    Example:
        repo = TickerDataRepository(
            api_client=tradier_client,
            cache=lru_cache
        )

        data = repo.get('AAPL')
        if data:
            print(f"Price: {data['price']}")
    """

    def __init__(
        self,
        api_client: Any,
        cache: Optional[Any] = None,
        cache_ttl: int = 900  # 15 minutes
    ) -> None:
        """
        Initialize ticker data repository.

        Args:
            api_client: API client for fetching ticker data
            cache: Optional cache instance (LRUCache, Redis, etc.)
            cache_ttl: Cache time-to-live in seconds
        """
        self.api_client = api_client
        self.cache = cache
        self.cache_ttl = cache_ttl

    def get(self, ticker: str) -> Optional[TickerData]:
        """
        Get ticker data with cache support.

        Args:
            ticker: Ticker symbol

        Returns:
            TickerData if found, None otherwise
        """
        # Check cache first
        if self.cache:
            cached_data = self.cache.get(ticker)
            if cached_data is not None:
                logger.debug(f"{ticker}: Cache hit")
                return cached_data

        # Fetch from API
        try:
            data = self.api_client.get_ticker_data(ticker)
            if data and self.cache:
                self.cache.set(ticker, data)
            return data
        except Exception as e:
            logger.error(f"{ticker}: Failed to fetch data: {e}")
            return None

    def get_many(self, tickers: List[str]) -> List[TickerData]:
        """
        Get multiple tickers' data efficiently.

        Uses batch API calls when possible and caches results.

        Args:
            tickers: List of ticker symbols

        Returns:
            List of TickerData for found tickers
        """
        results: List[TickerData] = []
        uncached_tickers: List[str] = []

        # Check cache for each ticker
        if self.cache:
            for ticker in tickers:
                cached_data = self.cache.get(ticker)
                if cached_data is not None:
                    results.append(cached_data)
                else:
                    uncached_tickers.append(ticker)
        else:
            uncached_tickers = tickers

        # Batch fetch uncached tickers
        if uncached_tickers:
            try:
                if hasattr(self.api_client, 'get_many_ticker_data'):
                    # Use batch API if available
                    fetched_data = self.api_client.get_many_ticker_data(uncached_tickers)
                else:
                    # Fall back to individual fetches
                    fetched_data = [
                        self.api_client.get_ticker_data(t)
                        for t in uncached_tickers
                    ]

                # Cache and add to results
                for data in fetched_data:
                    if data:
                        if self.cache:
                            self.cache.set(data['ticker'], data)
                        results.append(data)

            except Exception as e:
                logger.error(f"Batch fetch failed: {e}")

        return results

    def save(self, item: TickerData) -> None:
        """
        Save ticker data to cache.

        Args:
            item: TickerData to save
        """
        if self.cache:
            ticker = item.get('ticker')
            if ticker:
                self.cache.set(ticker, item)
                logger.debug(f"{ticker}: Saved to cache")

    def delete(self, ticker: str) -> bool:
        """
        Delete ticker data from cache.

        Args:
            ticker: Ticker symbol

        Returns:
            True if deleted, False if not found
        """
        if self.cache and hasattr(self.cache, 'delete'):
            return self.cache.delete(ticker)
        return False

    def exists(self, ticker: str) -> bool:
        """
        Check if ticker data exists in cache.

        Args:
            ticker: Ticker symbol

        Returns:
            True if exists in cache
        """
        if self.cache:
            return self.cache.get(ticker) is not None
        return False

    def clear_cache(self) -> None:
        """Clear all cached ticker data."""
        if self.cache and hasattr(self.cache, 'clear'):
            self.cache.clear()
            logger.info("Ticker data cache cleared")


class OptionsDataRepository(Repository[OptionsData]):
    """
    Repository for options data access.

    Provides abstraction over options data sources with support
    for multiple providers (Tradier, yfinance, etc.).
    """

    def __init__(
        self,
        primary_client: Any,
        fallback_client: Optional[Any] = None,
        cache: Optional[Any] = None
    ) -> None:
        """
        Initialize options data repository.

        Args:
            primary_client: Primary options data provider (e.g., Tradier)
            fallback_client: Fallback provider (e.g., yfinance)
            cache: Optional cache instance
        """
        self.primary_client = primary_client
        self.fallback_client = fallback_client
        self.cache = cache

    def get(self, ticker: str) -> Optional[OptionsData]:
        """
        Get options data with fallback support.

        Args:
            ticker: Ticker symbol

        Returns:
            OptionsData if found
        """
        # Check cache
        if self.cache:
            cached_data = self.cache.get(f"options:{ticker}")
            if cached_data is not None:
                return cached_data

        # Try primary client
        try:
            data = self.primary_client.get_options_data(ticker)
            if data:
                if self.cache:
                    self.cache.set(f"options:{ticker}", data)
                return data
        except Exception as e:
            logger.warning(f"{ticker}: Primary options client failed: {e}")

        # Fallback to secondary client
        if self.fallback_client:
            try:
                data = self.fallback_client.get_options_data(ticker)
                if data:
                    if self.cache:
                        self.cache.set(f"options:{ticker}", data)
                    return data
            except Exception as e:
                logger.error(f"{ticker}: Fallback options client failed: {e}")

        return None

    def get_many(self, tickers: List[str]) -> List[OptionsData]:
        """Get options data for multiple tickers."""
        results = []
        for ticker in tickers:
            data = self.get(ticker)
            if data:
                results.append(data)
        return results

    def save(self, item: OptionsData) -> None:
        """Save options data to cache."""
        if self.cache:
            ticker = item.get('ticker', '')  # type: ignore
            if ticker:
                self.cache.set(f"options:{ticker}", item)

    def delete(self, ticker: str) -> bool:
        """Delete options data from cache."""
        if self.cache and hasattr(self.cache, 'delete'):
            return self.cache.delete(f"options:{ticker}")
        return False

    def exists(self, ticker: str) -> bool:
        """Check if options data exists in cache."""
        if self.cache:
            return self.cache.get(f"options:{ticker}") is not None
        return False
