"""
Input validation for earnings analyzer.

Provides reusable validation functions for tickers, dates, and other inputs.
Extracted from EarningsAnalyzer to improve modularity and testability.
"""

# Standard library imports
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class InputValidator:
    """
    Validates user inputs for earnings analysis.

    Provides static methods for validating:
    - Ticker symbols (format, length)
    - Date strings (YYYY-MM-DD format)
    - Numeric parameters (max_analyze, lookback days)

    Example:
        ticker = InputValidator.validate_ticker("aapl")  # Returns "AAPL"
        date = InputValidator.validate_date("2025-11-15")  # Returns "2025-11-15"
        max_val = InputValidator.validate_max_analyze(10)  # Returns 10
    """

    @staticmethod
    def validate_ticker(ticker: str) -> str:
        """
        Validate ticker symbol format.

        Args:
            ticker: Ticker symbol to validate

        Returns:
            Uppercase, stripped ticker

        Raises:
            ValueError: If ticker format is invalid

        Examples:
            >>> InputValidator.validate_ticker("aapl")
            'AAPL'
            >>> InputValidator.validate_ticker("  MSFT  ")
            'MSFT'
            >>> InputValidator.validate_ticker("")
            Traceback (most recent call last):
                ...
            ValueError: Ticker cannot be empty
        """
        ticker = ticker.upper().strip()

        if not ticker:
            raise ValueError("Ticker cannot be empty")

        if not ticker.isalpha():
            raise ValueError(
                f"Invalid ticker format: '{ticker}'. "
                f"Tickers must contain only letters (e.g., AAPL, MSFT, GOOGL)"
            )

        if len(ticker) > 5:
            raise ValueError(
                f"Invalid ticker format: '{ticker}'. "
                f"Tickers must be 1-5 characters (got {len(ticker)})"
            )

        return ticker

    @staticmethod
    def validate_date(date_str: Optional[str]) -> Optional[str]:
        """
        Validate date format (YYYY-MM-DD).

        Args:
            date_str: Date string to validate, or None

        Returns:
            Valid date string or None

        Raises:
            ValueError: If date format is invalid

        Examples:
            >>> InputValidator.validate_date("2025-11-15")
            '2025-11-15'
            >>> InputValidator.validate_date(None)
            >>> InputValidator.validate_date("11/15/2025")
            Traceback (most recent call last):
                ...
            ValueError: Invalid date format: '11/15/2025'. Expected format: YYYY-MM-DD (e.g., 2025-11-08)
        """
        if date_str is None:
            return None

        try:
            # Attempt to parse
            datetime.strptime(date_str, '%Y-%m-%d')
            return date_str
        except ValueError:
            raise ValueError(
                f"Invalid date format: '{date_str}'. "
                f"Expected format: YYYY-MM-DD (e.g., 2025-11-08)"
            )

    @staticmethod
    def validate_max_analyze(max_analyze: int) -> int:
        """
        Validate max_analyze parameter.

        Args:
            max_analyze: Maximum number of tickers to analyze

        Returns:
            Validated max_analyze value

        Raises:
            ValueError: If value is invalid

        Examples:
            >>> InputValidator.validate_max_analyze(10)
            10
            >>> InputValidator.validate_max_analyze(0)
            Traceback (most recent call last):
                ...
            ValueError: max_analyze must be >= 1 (got 0)
        """
        if max_analyze < 1:
            raise ValueError(
                f"max_analyze must be >= 1 (got {max_analyze})"
            )

        if max_analyze > 50:
            logger.warning(
                f"max_analyze={max_analyze} is very high. "
                f"This may be slow and expensive (~${0.05 * max_analyze:.2f})"
            )

        return max_analyze

    @staticmethod
    def validate_lookback_days(lookback_days: int, max_days: int = 60) -> int:
        """
        Validate lookback days parameter.

        Args:
            lookback_days: Number of days to look back
            max_days: Maximum allowed lookback (default: 60)

        Returns:
            Validated lookback_days value

        Raises:
            ValueError: If value is invalid

        Examples:
            >>> InputValidator.validate_lookback_days(7)
            7
            >>> InputValidator.validate_lookback_days(0)
            Traceback (most recent call last):
                ...
            ValueError: lookback_days must be >= 1 (got 0)
            >>> InputValidator.validate_lookback_days(100)
            Traceback (most recent call last):
                ...
            ValueError: lookback_days must be <= 60 (got 100)
        """
        if lookback_days < 1:
            raise ValueError(
                f"lookback_days must be >= 1 (got {lookback_days})"
            )

        if lookback_days > max_days:
            raise ValueError(
                f"lookback_days must be <= {max_days} (got {lookback_days})"
            )

        return lookback_days

    @staticmethod
    def validate_ticker_list(tickers: str) -> list[str]:
        """
        Validate and parse comma-separated ticker list.

        Args:
            tickers: Comma-separated ticker string (e.g., "AAPL,MSFT,GOOGL")

        Returns:
            List of validated, uppercase tickers

        Raises:
            ValueError: If any ticker is invalid

        Examples:
            >>> InputValidator.validate_ticker_list("aapl,msft")
            ['AAPL', 'MSFT']
            >>> InputValidator.validate_ticker_list("  GOOGL  ,  TSLA  ")
            ['GOOGL', 'TSLA']
            >>> InputValidator.validate_ticker_list("")
            Traceback (most recent call last):
                ...
            ValueError: Ticker list cannot be empty
        """
        if not tickers or not tickers.strip():
            raise ValueError("Ticker list cannot be empty")

        # Split and validate each ticker
        ticker_list = []
        for ticker in tickers.split(','):
            validated = InputValidator.validate_ticker(ticker)
            ticker_list.append(validated)

        return ticker_list
