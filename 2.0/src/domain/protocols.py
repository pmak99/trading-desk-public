"""
Protocol definitions for dependency injection and testability.

Protocols define interfaces without inheritance, enabling duck typing
and easier mocking in tests.
"""

from datetime import date, datetime
from typing import (
    Any,
    ContextManager,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from src.domain.errors import AppError, Result
from src.domain.types import (
    ConsistencyResult,
    HistoricalMove,
    ImpliedMove,
    Money,
    OptionChain,
    SkewResult,
    TermStructureResult,
    VRPResult,
)

# ============================================================================
# Data Provider Interfaces
# ============================================================================


class OptionsDataProvider(Protocol):
    """
    Interface for fetching options market data.
    Implementations: TradierAPI (container.tradier) and CachedOptionsDataProvider
    (container.cached_options_provider), which wraps it.
    """

    def get_stock_price(self, ticker: str) -> Result[Money, AppError]:
        """Get current stock price for ticker."""
        ...

    def get_option_chain(
        self, ticker: str, expiration: date,
        stock_price: Optional[Money] = None,
    ) -> Result[OptionChain, AppError]:
        """Get option chain for ticker and expiration.

        stock_price: optional known price — implementations may use it to
        skip an internal price fetch.
        """
        ...

    def get_expirations(self, ticker: str) -> Result[List[date], AppError]:
        """Listed option expirations for ticker."""
        ...

    def find_nearest_expiration(
        self, ticker: str, target_date: date
    ) -> Result[date, AppError]:
        """The nearest listed expiration on or after target_date (else the last)."""
        ...


class CachedOptionsProvider(OptionsDataProvider, Protocol):
    """
    An OptionsDataProvider behind a cache that can be seeded in bulk.
    Implementation: CachedOptionsDataProvider (container.cached_options_provider),
    the provider scan code must fetch through.
    """

    def keep_stock_prices_warm(self, tickers: List[str]) -> ContextManager[int]:
        """Batch-quote many tickers now and whenever their quotes expire, until the
        block exits; yields how many were quoted on entry."""
        ...


@runtime_checkable
class RateLimitedOptionsProvider(OptionsDataProvider, Protocol):
    """
    An OptionsDataProvider whose calls wait on a shared rate limiter, where a caller
    can take a call's slot first and decide afterwards what to spend it on.
    Implementation: TradierAPI. CachedOptionsDataProvider takes a chain's slot before
    it reads the quote the chain is priced on, so the wait cannot age the quote.
    """

    def admit(self) -> bool:
        """Wait for one slot; False when none comes within the wait budget. The
        caller issues exactly one call with admitted=True next."""
        ...

    def get_stock_price(self, ticker: str, admitted: bool = False) -> Result[Money, AppError]:
        """Current stock price; admitted=True spends a slot taken with admit()."""
        ...

    def get_stock_prices_batch(
        self, tickers: List[str], admitted: bool = False
    ) -> Result[Dict[str, Money], AppError]:
        """Prices for up to 100 tickers in one call; admitted as for get_stock_price."""
        ...

    def get_option_chain(
        self, ticker: str, expiration: date,
        stock_price: Optional[Money] = None,
        admitted: bool = False,
    ) -> Result[OptionChain, AppError]:
        """Option chain; admitted=True spends a slot taken with admit(), and then
        needs stock_price (one slot cannot pay for a quote and a chain). Sent once:
        a send that gets no usable answer returns an error marked retryable, for
        the caller to resend on a new slot and a fresh price."""
        ...


class EarningsDataProvider(Protocol):
    """
    Interface for fetching earnings calendar data.
    Implementation: FinnhubAPI
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
    Implementation: FinnhubAPI or database
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
    Implementation: PricesRepository (container.prices_repository)
    """

    def save_historical_move(
        self, move: HistoricalMove
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
    Implementations: ImpliedMoveCalculator and ImpliedMoveCalculatorInterpolated
    (src/application/metrics), handed out as container.implied_move_calculator.
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
    Implementations: MemoryCache (container.cache), HybridCache
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
