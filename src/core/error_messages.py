"""
Improved error messages with context and actionable suggestions.

Provides user-friendly error messages that help diagnose and fix issues quickly.
"""

from typing import Optional, Dict, Any


class ErrorMessage:
    """Enhanced error message with context and suggestions."""

    def __init__(
        self,
        error_type: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        suggestion: Optional[str] = None,
        technical_details: Optional[str] = None
    ):
        self.error_type = error_type
        self.message = message
        self.context = context or {}
        self.suggestion = suggestion
        self.technical_details = technical_details

    def format(self, verbose: bool = False) -> str:
        """
        Format error message for display.

        Args:
            verbose: Include technical details

        Returns:
            Formatted error string
        """
        lines = [f"❌ {self.error_type}: {self.message}"]

        # Add context
        if self.context:
            lines.append("\nContext:")
            for key, value in self.context.items():
                lines.append(f"  • {key}: {value}")

        # Add suggestion
        if self.suggestion:
            lines.append(f"\n💡 Suggestion: {self.suggestion}")

        # Add technical details if verbose
        if verbose and self.technical_details:
            lines.append(f"\nTechnical Details:\n{self.technical_details}")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format(verbose=False)


# Common error messages with helpful context

def api_rate_limit_error(
    api_name: str,
    current_usage: Optional[int] = None,
    limit: Optional[int] = None,
    reset_time: Optional[str] = None
) -> ErrorMessage:
    """Error message for API rate limit exceeded."""
    context = {"API": api_name}
    if current_usage and limit:
        context["Usage"] = f"{current_usage}/{limit} requests"
    if reset_time:
        context["Reset Time"] = reset_time

    return ErrorMessage(
        error_type="API Rate Limit Exceeded",
        message=f"{api_name} API rate limit has been reached",
        context=context,
        suggestion=(
            "Wait for rate limit to reset, or configure a rate limiter "
            "in config/performance.yaml to prevent this error"
        )
    )


def ticker_not_found_error(
    ticker: str,
    searched_sources: Optional[list] = None
) -> ErrorMessage:
    """Error message for ticker not found."""
    context = {"Ticker": ticker}
    if searched_sources:
        context["Searched Sources"] = ", ".join(searched_sources)

    return ErrorMessage(
        error_type="Ticker Not Found",
        message=f"Could not find data for ticker symbol '{ticker}'",
        context=context,
        suggestion=(
            "Verify the ticker symbol is correct and actively traded. "
            "Check if the symbol has been delisted or merged."
        )
    )


def insufficient_data_error(
    ticker: str,
    missing_fields: list,
    data_source: Optional[str] = None
) -> ErrorMessage:
    """Error message for insufficient ticker data."""
    context = {
        "Ticker": ticker,
        "Missing Fields": ", ".join(missing_fields)
    }
    if data_source:
        context["Data Source"] = data_source

    return ErrorMessage(
        error_type="Insufficient Data",
        message=f"{ticker} is missing required data fields",
        context=context,
        suggestion=(
            "Try using a different data provider or wait for the market "
            "to open if data is stale. Some tickers may not have options data."
        )
    )


def api_connection_error(
    api_name: str,
    error_details: Optional[str] = None,
    retry_count: Optional[int] = None
) -> ErrorMessage:
    """Error message for API connection failure."""
    context = {"API": api_name}
    if retry_count:
        context["Retry Attempts"] = str(retry_count)

    return ErrorMessage(
        error_type="API Connection Error",
        message=f"Failed to connect to {api_name} API",
        context=context,
        suggestion=(
            "Check your internet connection and API credentials. "
            "Verify that the API endpoint is accessible and not experiencing downtime."
        ),
        technical_details=error_details
    )


def circuit_breaker_open_error(
    service_name: str,
    failure_count: int,
    recovery_time: Optional[str] = None
) -> ErrorMessage:
    """Error message for circuit breaker open state."""
    context = {
        "Service": service_name,
        "Consecutive Failures": str(failure_count)
    }
    if recovery_time:
        context["Recovery Attempt In"] = recovery_time

    return ErrorMessage(
        error_type="Circuit Breaker Open",
        message=f"{service_name} circuit breaker is open due to repeated failures",
        context=context,
        suggestion=(
            "The service will automatically retry after the recovery timeout. "
            "Check service health and logs for underlying issues. "
            "You can manually reset the circuit breaker if the issue is resolved."
        )
    )


def validation_error(
    field_name: str,
    expected_type: str,
    actual_value: Any,
    ticker: Optional[str] = None
) -> ErrorMessage:
    """Error message for data validation failure."""
    context = {
        "Field": field_name,
        "Expected Type": expected_type,
        "Actual Value": str(actual_value),
        "Actual Type": type(actual_value).__name__
    }
    if ticker:
        context["Ticker"] = ticker

    return ErrorMessage(
        error_type="Data Validation Error",
        message=f"Invalid data type for field '{field_name}'",
        context=context,
        suggestion=(
            "Check the data source for corrupted or malformed data. "
            "This may indicate an API change or data quality issue."
        )
    )


def insufficient_liquidity_error(
    ticker: str,
    options_volume: int,
    open_interest: int,
    min_volume: int = 100,
    min_oi: int = 500
) -> ErrorMessage:
    """Error message for insufficient options liquidity."""
    context = {
        "Ticker": ticker,
        "Options Volume": f"{options_volume:,} (min: {min_volume:,})",
        "Open Interest": f"{open_interest:,} (min: {min_oi:,})"
    }

    return ErrorMessage(
        error_type="Insufficient Options Liquidity",
        message=f"{ticker} does not meet minimum liquidity requirements",
        context=context,
        suggestion=(
            "This ticker has low options volume or open interest. "
            "Consider using more liquid alternatives or adjusting minimum "
            "liquidity thresholds in config/budget.yaml"
        )
    )


def iv_criteria_not_met_error(
    ticker: str,
    current_iv: Optional[float] = None,
    iv_rank: Optional[float] = None,
    min_iv: float = 60,
    min_iv_rank: float = 50
) -> ErrorMessage:
    """Error message for IV criteria not met."""
    context = {"Ticker": ticker}
    if current_iv:
        context["Current IV"] = f"{current_iv:.2f}% (min: {min_iv:.0f}%)"
    if iv_rank:
        context["IV Rank"] = f"{iv_rank:.1f}% (min: {min_iv_rank:.0f}%)"

    return ErrorMessage(
        error_type="IV Criteria Not Met",
        message=f"{ticker} has insufficient implied volatility",
        context=context,
        suggestion=(
            "This strategy targets high IV tickers (IV ≥ 60% or IV Rank ≥ 50%). "
            "This ticker may not be suitable for IV crush trades. "
            "Consider waiting for earnings or other catalysts to increase IV."
        )
    )


def budget_exceeded_error(
    provider: str,
    current_cost: float,
    daily_limit: float,
    monthly_limit: Optional[float] = None
) -> ErrorMessage:
    """Error message for budget limit exceeded."""
    context = {
        "Provider": provider,
        "Current Cost": f"${current_cost:.2f}",
        "Daily Limit": f"${daily_limit:.2f}"
    }
    if monthly_limit:
        context["Monthly Limit"] = f"${monthly_limit:.2f}"

    return ErrorMessage(
        error_type="Budget Limit Exceeded",
        message=f"{provider} API budget limit has been reached",
        context=context,
        suggestion=(
            "Wait for daily reset or increase budget limits in config/budget.yaml. "
            "Use --override flag to bypass limits temporarily (not recommended for production)."
        )
    )


def format_error(
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
    suggestion: Optional[str] = None
) -> str:
    """
    Format any exception as an enhanced error message.

    Args:
        error: The exception to format
        context: Additional context
        suggestion: Suggested fix

    Returns:
        Formatted error string
    """
    error_msg = ErrorMessage(
        error_type=type(error).__name__,
        message=str(error),
        context=context,
        suggestion=suggestion,
        technical_details=f"{type(error).__module__}.{type(error).__name__}"
    )

    return error_msg.format(verbose=True)
