"""
Protocol definitions for dependency injection and testability.

Protocols define interfaces without inheritance, enabling duck typing
and easier mocking in tests.
"""

from typing import Protocol, List, Optional, Any
from datetime import date, datetime
from src.domain.types import (
    Money,
    OptionChain,
    ImpliedMove,
    HistoricalMove,
    VRPResult,
    ConsistencyResult,
    SkewResult,
    TermStructureResult,
)
from src.domain.errors import Result, AppError


# ============================================================================
# Data Provider Interfaces
# ============================================================================


class OptionsDataProvider(Protocol):
    """
    Interface for fetching options market data.
    Implementation: TradierAPI
    """

    def get_stock_price(self, ticker: str) -> Result[Money, AppError]:
        """Get current stock price for ticker."""
        ...

    def get_option_chain(
        self, ticker: str, expiration: date
    ) -> Result[OptionChain, AppError]:
        """Get option chain for ticker and expiration."""
        ...


class EarningsDataProvider(Protocol):
    """
    Interface for fetching earnings calendar data.
    Implementation: AlphaVantageAPI
    """

    def get_earnings_date(self, ticker: str) -> Result[date, AppError]:
        """Get next earnings date for ticker."""
        ...

    def get_earnings_calendar(
        self, start_date: date, end_date: date
    ) -> Result[List[tuple[str, date]], AppError]:
        """Get all earnings between start and end dates."""
        ...


class PriceHistoryProvider(Protocol):
    """
    Interface for fetching historical price data.
    Implementation: AlphaVantageAPI or database
    """

    def get_daily_prices(
        self, ticker: str, start_date: date, end_date: date
    ) -> Result[List[tuple[date, Money]], AppError]:
        """Get daily closing prices for date range."""
        ...

    def get_intraday_prices(
        self, ticker: str, target_date: date
    ) -> Result[List[tuple[datetime, Money]], AppError]:
        """Get intraday prices (1-minute bars) for a specific day."""
        ...


# ============================================================================
# Repository Interfaces
# ============================================================================


class EarningsRepository(Protocol):
    """
    Interface for persisting and retrieving earnings data.
    Implementation: SQLite repository
    """

    def save_earnings_event(
        self, ticker: str, earnings_date: date, timing: str
    ) -> Result[None, AppError]:
        """Save earnings event to database."""
        ...

    def get_earnings_history(
        self, ticker: str, limit: int = 12
    ) -> Result[List[tuple[date, str]], AppError]:
        """Get past earnings dates for ticker."""
        ...

    def get_upcoming_earnings(
        self, days_ahead: int = 7
    ) -> Result[List[tuple[str, date]], AppError]:
        """Get all earnings in next N days."""
        ...


class PriceRepository(Protocol):
    """
    Interface for persisting and retrieving price history.
    Implementation: SQLite repository
    """

    def save_historical_move(
        self, ticker: str, move: HistoricalMove
    ) -> Result[None, AppError]:
        """Save historical earnings move to database."""
        ...

    def get_historical_moves(
        self, ticker: str, limit: int = 12
    ) -> Result[List[HistoricalMove], AppError]:
        """Get past earnings moves for ticker."""
        ...


class MetadataRepository(Protocol):
    """
    Interface for ticker metadata (sector, market cap, etc.).
    Implementation: SQLite repository
    """

    def get_market_cap(self, ticker: str) -> Result[Money, AppError]:
        """Get market capitalization for ticker."""
        ...

    def get_sector(self, ticker: str) -> Result[str, AppError]:
        """Get sector for ticker."""
        ...


# ============================================================================
# Calculator Interfaces
# ============================================================================


class ImpliedMoveCalculator(Protocol):
    """
    Interface for calculating implied move from options.
    Implementation: Application layer
    """

    def calculate(
        self, ticker: str, expiration: date
    ) -> Result[ImpliedMove, AppError]:
        """Calculate implied move from ATM straddle."""
        ...


class VRPCalculator(Protocol):
    """
    Interface for calculating VRP ratio and recommendation.
    Implementation: Application layer
    """

    def calculate(
        self,
        ticker: str,
        expiration: date,
        implied_move: ImpliedMove,
        historical_moves: List[HistoricalMove],
    ) -> Result[VRPResult, AppError]:
        """Calculate VRP ratio and generate recommendation."""
        ...


class ConsistencyAnalyzer(Protocol):
    """
    Interface for analyzing historical move consistency.
    Implementation: Application layer
    """

    def analyze(
        self, historical_moves: List[HistoricalMove]
    ) -> Result[ConsistencyResult, AppError]:
        """Analyze consistency of historical moves."""
        ...


class SkewAnalyzer(Protocol):
    """
    Interface for analyzing IV skew.
    Implementation: Application layer
    """

    def analyze(
        self, ticker: str, expiration: date
    ) -> Result[SkewResult, AppError]:
        """Analyze put-call IV skew."""
        ...


class TermStructureAnalyzer(Protocol):
    """
    Interface for analyzing IV term structure.
    Implementation: Application layer
    """

    def analyze(
        self, ticker: str, expirations: List[date]
    ) -> Result[TermStructureResult, AppError]:
        """Analyze IV across multiple expirations."""
        ...


# ============================================================================
# Cache Interface
# ============================================================================


class CacheProvider(Protocol):
    """
    Interface for caching layer.
    Implementations: MemoryCache, HybridCache (Phase 2)
    """

    def get(self, key: str) -> Optional[Any]:
        """Get cached value by key."""
        ...

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set cached value with optional TTL."""
        ...

    def delete(self, key: str) -> None:
        """Delete cached value."""
        ...

    def clear(self) -> None:
        """Clear all cached values."""
        ...


# ============================================================================
# Service Interfaces (Phase 1+)
# ============================================================================


class HealthCheckService(Protocol):
    """
    Interface for health checking all dependencies.
    Implementation: Phase 1
    """

    def check_all(self) -> dict:
        """Check health of all services."""
        ...


class RateLimiter(Protocol):
    """
    Interface for rate limiting API calls.
    Implementation: Token bucket algorithm
    """

    def acquire(self, tokens: int = 1) -> bool:
        """Attempt to acquire tokens. Returns True if successful."""
        ...

    def wait_for_token(self, timeout: Optional[float] = None) -> bool:
        """Wait until token is available or timeout."""
        ...
