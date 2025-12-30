"""
Centralized ticker symbol validation and normalization.

Provides consistent ticker handling across all API endpoints and integrations.
"""

import re
from typing import Optional, Tuple


# Standard ticker pattern: 1-5 uppercase letters, optional 1-2 letter suffix (e.g., BRK.B)
TICKER_PATTERN = re.compile(r'^[A-Z]{1,5}(\.[A-Z]{1,2})?$')

# Common company name → ticker symbol mappings
# Used to handle natural language ticker references
TICKER_ALIASES = {
    "NIKE": "NKE",
    "GOOGLE": "GOOGL",
    "FACEBOOK": "META",
    "AMAZON": "AMZN",
    "APPLE": "AAPL",
    "MICROSOFT": "MSFT",
    "TESLA": "TSLA",
    "NETFLIX": "NFLX",
    "NVIDIA": "NVDA",
    "COSTCO": "COST",
    "STARBUCKS": "SBUX",
    "WALMART": "WMT",
    "TARGET": "TGT",
    "DISNEY": "DIS",
    "BERKSHIRE": "BRK.B",
    "JPMORGAN": "JPM",
    "ALPHABET": "GOOGL",
    "PAYPAL": "PYPL",
    "SALESFORCE": "CRM",
    "VISA": "V",
    "MASTERCARD": "MA",
    "MCDONALDS": "MCD",
    "BOEING": "BA",
    "INTEL": "INTC",
    "AMD": "AMD",  # Already correct but included for completeness
    "BROADCOM": "AVGO",
    "QUALCOMM": "QCOM",
    "ADOBE": "ADBE",
    "ORACLE": "ORCL",
    "LULULEMON": "LULU",
}


class InvalidTickerError(ValueError):
    """Raised when a ticker symbol is invalid."""

    def __init__(self, ticker: str, reason: str = "Invalid format"):
        self.ticker = ticker
        self.reason = reason
        super().__init__(f"Invalid ticker '{ticker}': {reason}")


def validate_ticker(ticker: str) -> bool:
    """
    Check if a ticker symbol is valid.

    Args:
        ticker: Ticker symbol to validate (should be uppercase)

    Returns:
        True if valid, False otherwise
    """
    if not ticker:
        return False
    return bool(TICKER_PATTERN.match(ticker))


def normalize_ticker(ticker: str) -> str:
    """
    Normalize a ticker symbol to standard format.

    - Converts to uppercase
    - Strips whitespace
    - Resolves common company name aliases

    Args:
        ticker: Raw ticker input

    Returns:
        Normalized ticker symbol

    Raises:
        InvalidTickerError: If ticker cannot be normalized to valid format
    """
    if not ticker:
        raise InvalidTickerError("", "Empty ticker")

    # Normalize: uppercase and strip
    normalized = ticker.upper().strip()

    # Check for company name alias
    if normalized in TICKER_ALIASES:
        normalized = TICKER_ALIASES[normalized]

    # Validate final format
    if not validate_ticker(normalized):
        raise InvalidTickerError(
            ticker,
            f"Must be 1-5 letters with optional .XX suffix (got: {normalized})"
        )

    return normalized


def safe_normalize_ticker(ticker: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Safely normalize a ticker, returning error instead of raising.

    Useful for API endpoints that want to return error messages.

    Args:
        ticker: Raw ticker input

    Returns:
        Tuple of (normalized_ticker, error_message)
        If valid: (ticker, None)
        If invalid: (None, error_message)
    """
    try:
        return (normalize_ticker(ticker), None)
    except InvalidTickerError as e:
        return (None, str(e))


def resolve_alias(name: str) -> Optional[str]:
    """
    Resolve a company name to its ticker symbol if known.

    Args:
        name: Company name (case-insensitive)

    Returns:
        Ticker symbol if found, None otherwise
    """
    return TICKER_ALIASES.get(name.upper().strip())
