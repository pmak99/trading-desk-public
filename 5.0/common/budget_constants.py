"""Shared budget tracking constants for Perplexity API.

Token pricing, cost estimates, and status types used by both
4.0's BudgetTracker and 5.0's BudgetTracker.
"""

from enum import Enum
from dataclasses import dataclass


# Perplexity token pricing (per token, from invoice January 2025)
PRICING = {
    "sonar_output": 0.000001,      # $1/1M tokens
    "sonar_pro_output": 0.000015,  # $15/1M tokens
    "reasoning_pro": 0.000003,     # $3/1M tokens
    "search_request": 0.005,       # $5/1000 requests (flat)
}

# MCP operation cost estimates (for operations without token counts)
# NOTE: These are empirical estimates based on typical response sizes observed
# in production (January-February 2025). MCP tools don't return token counts,
# so we estimate based on:
# - perplexity_ask: Simple Q&A responses averaging ~200 output tokens
# - perplexity_search: Fixed $0.005/request per Perplexity API pricing
# - perplexity_research: Detailed analysis averaging ~500 tokens (sonar-pro model)
# - perplexity_reason: Extended reasoning averaging ~4000 tokens
#
# ACCURACY: These estimates may vary +/-50% from actual costs. Monitor monthly
# invoice against budget tracker totals to adjust if needed.
MCP_COST_ESTIMATES = {
    "perplexity_ask": 0.001,      # ~200 sonar output tokens @ $1/1M
    "perplexity_search": 0.005,   # 1 search request (fixed fee)
    "perplexity_research": 0.008, # ~500 sonar-pro output tokens @ $15/1M
    "perplexity_reason": 0.012,   # ~4000 reasoning tokens @ $3/1M
}

# Token count bounds (sanity check to catch bugs)
MAX_TOKENS_PER_CALL = 10_000_000  # 10M tokens max per call (very generous limit)

# Valid service and model names
VALID_SERVICES = {"perplexity"}
VALID_MODELS = {"sonar", "sonar-pro", "reasoning-pro"}


class BudgetStatus(Enum):
    """Budget status levels."""
    OK = "ok"           # Under 80%
    WARNING = "warning"  # 80-99%
    EXHAUSTED = "exhausted"  # 100%+


class BudgetExhaustedError(Exception):
    """Raised when the API budget is exhausted and no more calls can be made."""

    def __init__(self, calls_today: int = 0, max_calls: int = 40, message: str = None):
        self.calls_today = calls_today
        self.max_calls = max_calls
        self.message = message or (
            f"Daily budget exhausted ({calls_today}/{max_calls} calls). "
            "Use WebSearch fallback."
        )
        super().__init__(self.message)


def validate_token_counts(
    output_tokens: int,
    reasoning_tokens: int,
    search_requests: int
) -> None:
    """
    Validate token counts are within reasonable bounds.

    Args:
        output_tokens: Number of output tokens
        reasoning_tokens: Number of reasoning tokens
        search_requests: Number of search requests

    Raises:
        ValueError: If any count is negative or exceeds MAX_TOKENS_PER_CALL
    """
    for name, value in [
        ("output_tokens", output_tokens),
        ("reasoning_tokens", reasoning_tokens),
        ("search_requests", search_requests),
    ]:
        if not isinstance(value, int):
            raise ValueError(f"{name} must be an integer, got: {type(value).__name__}")
        if value < 0:
            raise ValueError(f"{name} cannot be negative, got: {value}")
        if value > MAX_TOKENS_PER_CALL:
            raise ValueError(f"{name} exceeds maximum ({MAX_TOKENS_PER_CALL}), got: {value}")


def calculate_token_cost(
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    search_requests: int = 0,
    model: str = "sonar"
) -> float:
    """
    Calculate cost from token counts using invoice-verified rates.

    Args:
        output_tokens: Number of output tokens
        reasoning_tokens: Number of reasoning tokens
        search_requests: Number of search API requests
        model: Model used ("sonar", "sonar-pro", or "reasoning-pro")

    Returns:
        Calculated cost in dollars
    """
    cost = 0.0
    if output_tokens > 0:
        if model == "sonar-pro":
            cost += output_tokens * PRICING["sonar_pro_output"]
        else:
            cost += output_tokens * PRICING["sonar_output"]
    if reasoning_tokens > 0:
        cost += reasoning_tokens * PRICING["reasoning_pro"]
    if search_requests > 0:
        cost += search_requests * PRICING["search_request"]
    return cost
