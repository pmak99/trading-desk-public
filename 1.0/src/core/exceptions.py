"""
Custom exceptions for the trading desk application.

Provides standardized exception hierarchy for better error handling
and clearer error messages across the application.
"""


class TradingDeskError(Exception):
    """Base exception for all trading desk errors."""
    pass


class ValidationError(TradingDeskError):
    """Raised when input validation fails."""
    pass


class DataFetchError(TradingDeskError):
    """Raised when data fetching from external APIs fails."""

    def __init__(self, message: str, ticker: str = None, source: str = None):
        """
        Initialize data fetch error.

        Args:
            message: Error message
            ticker: Ticker symbol that failed (optional)
            source: Data source that failed (e.g., 'yfinance', 'tradier')
        """
        self.ticker = ticker
        self.source = source

        if ticker and source:
            message = f"{ticker} ({source}): {message}"
        elif ticker:
            message = f"{ticker}: {message}"
        elif source:
            message = f"{source}: {message}"

        super().__init__(message)


class APIError(TradingDeskError):
    """Raised when API calls fail (external services)."""

    def __init__(self, message: str, api_name: str = None, status_code: int = None):
        """
        Initialize API error.

        Args:
            message: Error message
            api_name: Name of the API (e.g., 'Perplexity', 'Gemini')
            status_code: HTTP status code (optional)
        """
        self.api_name = api_name
        self.status_code = status_code

        if api_name and status_code:
            message = f"{api_name} API (HTTP {status_code}): {message}"
        elif api_name:
            message = f"{api_name} API: {message}"
        elif status_code:
            message = f"API error (HTTP {status_code}): {message}"

        super().__init__(message)


class BudgetExceededError(TradingDeskError):
    """Raised when budget limits are exceeded."""

    def __init__(self, message: str, limit_type: str = None, current: float = None, limit: float = None):
        """
        Initialize budget exceeded error.

        Args:
            message: Error message
            limit_type: Type of limit ('daily', 'monthly', 'per_call')
            current: Current usage
            limit: Limit value
        """
        self.limit_type = limit_type
        self.current = current
        self.limit = limit

        if limit_type and current is not None and limit is not None:
            message = f"{limit_type.title()} budget exceeded: ${current:.2f} / ${limit:.2f}. {message}"

        super().__init__(message)


class ConfigurationError(TradingDeskError):
    """Raised when configuration is missing or invalid."""

    def __init__(self, message: str, config_key: str = None):
        """
        Initialize configuration error.

        Args:
            message: Error message
            config_key: Configuration key that caused the error
        """
        self.config_key = config_key

        if config_key:
            message = f"Configuration error ({config_key}): {message}"

        super().__init__(message)


class AnalysisError(TradingDeskError):
    """Raised when analysis fails."""

    def __init__(self, message: str, ticker: str = None, step: str = None):
        """
        Initialize analysis error.

        Args:
            message: Error message
            ticker: Ticker being analyzed
            step: Analysis step that failed (e.g., 'sentiment', 'strategy')
        """
        self.ticker = ticker
        self.step = step

        if ticker and step:
            message = f"{ticker} ({step}): {message}"
        elif ticker:
            message = f"{ticker}: {message}"
        elif step:
            message = f"{step}: {message}"

        super().__init__(message)
