#!/usr/bin/env python3
"""
Perplexity MCP Server with Token Tracking.

A custom MCP server that replaces the standard Perplexity MCP and logs all API usage
with accurate token counts to the budget tracker database.

Usage:
    python server.py

Environment variables:
    PERPLEXITY_API_KEY: Perplexity API key (required)
    BUDGET_DB_PATH: Path to budget tracker database (optional, defaults to 4.0 db)

The server exposes the same tools as the standard perplexity MCP:
- perplexity_ask: Basic chat completion
- perplexity_search: Web search
- perplexity_research: Deep research
- perplexity_reason: Reasoning tasks
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any

import httpx

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "4.0" / "src"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from perplexity_client import PerplexityClient, PerplexityResponse


# Perplexity token pricing (per token, from invoice)
PRICING = {
    "sonar_output": 0.000001,      # $1/1M tokens
    "sonar_pro_output": 0.000015,  # $15/1M tokens
    "reasoning_pro": 0.000003,     # $3/1M tokens
    "search_request": 0.005,       # $5/1000 requests (flat)
}


class BudgetLogger:
    """
    Logs API usage to budget tracker database.

    Uses the same schema as the 4.0 budget tracker for consistency.
    """

    # Budget limits (matching 4.0 BudgetTracker)
    MAX_DAILY_CALLS = 40
    MONTHLY_BUDGET = 5.00

    def __init__(self, db_path: str = None):
        if db_path is None:
            # Default to 4.0 sentiment_cache.db
            db_path = str(
                Path(__file__).parent.parent.parent / "4.0" / "data" / "sentiment_cache.db"
            )
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self):
        """Ensure database schema exists with token columns."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_budget (
                    date TEXT PRIMARY KEY,
                    calls INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    last_updated TEXT,
                    output_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    search_requests INTEGER DEFAULT 0
                )
            """)
            # Add token columns if they don't exist (migration)
            for column in ['output_tokens', 'reasoning_tokens', 'search_requests']:
                try:
                    conn.execute(f"ALTER TABLE api_budget ADD COLUMN {column} INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.commit()
        finally:
            conn.close()

    def log_usage(
        self,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        search_requests: int = 0,
        model: str = "sonar"
    ) -> float:
        """
        Log API usage with token counts.

        Args:
            output_tokens: Number of output/completion tokens
            reasoning_tokens: Number of reasoning tokens
            search_requests: Number of search API requests
            model: Model used for pricing calculation

        Returns:
            Calculated cost in dollars
        """
        # Calculate cost
        cost = 0.0
        if output_tokens > 0:
            if "pro" in model.lower() and "reasoning" not in model.lower():
                cost += output_tokens * PRICING["sonar_pro_output"]
            else:
                cost += output_tokens * PRICING["sonar_output"]
        if reasoning_tokens > 0:
            cost += reasoning_tokens * PRICING["reasoning_pro"]
        if search_requests > 0:
            cost += search_requests * PRICING["search_request"]

        # Log to database
        today = date.today().isoformat()
        timestamp = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            # Ensure today's row exists
            conn.execute("""
                INSERT OR IGNORE INTO api_budget (date, calls, cost, last_updated,
                                                   output_tokens, reasoning_tokens, search_requests)
                VALUES (?, 0, 0.0, ?, 0, 0, 0)
            """, (today, timestamp))

            # Update with new usage
            conn.execute("""
                UPDATE api_budget
                SET calls = calls + 1,
                    cost = cost + ?,
                    output_tokens = output_tokens + ?,
                    reasoning_tokens = reasoning_tokens + ?,
                    search_requests = search_requests + ?,
                    last_updated = ?
                WHERE date = ?
            """, (cost, output_tokens, reasoning_tokens, search_requests, timestamp, today))

            conn.commit()
        finally:
            conn.close()

        return cost

    def can_call(self) -> tuple[bool, str]:
        """
        Check if we can make another API call.

        Returns:
            Tuple of (can_call: bool, reason: str)
        """
        today_str = date.today().isoformat()
        month_prefix = today_str[:7]

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            # Get today's calls
            cursor = conn.execute(
                "SELECT calls, cost FROM api_budget WHERE date = ?",
                (today_str,)
            )
            row = cursor.fetchone()
            daily_calls = row[0] if row else 0

            if daily_calls >= self.MAX_DAILY_CALLS:
                return False, f"Daily limit reached ({daily_calls}/{self.MAX_DAILY_CALLS} calls)"

            # Get monthly cost
            cursor = conn.execute(
                "SELECT SUM(cost) FROM api_budget WHERE date LIKE ?",
                (f"{month_prefix}%",)
            )
            row = cursor.fetchone()
            monthly_cost = row[0] if row and row[0] else 0.0

            if monthly_cost >= self.MONTHLY_BUDGET:
                return False, f"Monthly budget exceeded (${monthly_cost:.2f}/${self.MONTHLY_BUDGET:.2f})"

            return True, f"OK ({daily_calls}/{self.MAX_DAILY_CALLS} calls today)"
        finally:
            conn.close()


class BudgetExhausted(Exception):
    """Raised when API budget is exhausted."""
    pass


# Initialize server
app = Server("perplexity-tracked")

# Initialize client and logger (lazy, to allow env vars to be set)
_client = None
_logger = None


def get_client() -> PerplexityClient:
    global _client
    if _client is None:
        _client = PerplexityClient()
    return _client


def get_logger() -> BudgetLogger:
    global _logger
    if _logger is None:
        db_path = os.environ.get("BUDGET_DB_PATH")
        _logger = BudgetLogger(db_path)
    return _logger


def log_response(response: PerplexityResponse) -> float:
    """Log API response to budget tracker and return cost."""
    logger = get_logger()

    # Determine search requests
    search_requests = 1 if response.is_search else 0

    return logger.log_usage(
        output_tokens=response.usage.output_tokens,
        reasoning_tokens=response.usage.reasoning_tokens,
        search_requests=search_requests,
        model=response.model
    )


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available Perplexity tools."""
    return [
        Tool(
            name="perplexity_ask",
            description="Engages in a conversation using the Perplexity API. "
                        "Accepts an array of messages (each with a role and content) "
                        "and returns a chat completion response from the sonar model.",
            inputSchema={
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "description": "Array of conversation messages",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "description": "Role of the message (e.g., system, user, assistant)"
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The content of the message"
                                }
                            },
                            "required": ["role", "content"]
                        }
                    }
                },
                "required": ["messages"]
            }
        ),
        Tool(
            name="perplexity_search",
            description="Performs web search using the Perplexity Search API. "
                        "Returns ranked search results with titles, URLs, snippets, and metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string"
                    },
                    "max_results": {
                        "type": "number",
                        "description": "Maximum number of results to return (1-20, default: 10)",
                        "minimum": 1,
                        "maximum": 20
                    },
                    "country": {
                        "type": "string",
                        "description": "ISO 3166-1 alpha-2 country code for regional results (e.g., 'US', 'GB')"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="perplexity_research",
            description="Performs deep research using the Perplexity API. "
                        "Accepts an array of messages and returns a comprehensive research response "
                        "with citations using the sonar-pro model.",
            inputSchema={
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "description": "Array of conversation messages",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "description": "Role of the message (e.g., system, user, assistant)"
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The content of the message"
                                }
                            },
                            "required": ["role", "content"]
                        }
                    },
                    "strip_thinking": {
                        "type": "boolean",
                        "description": "If true, removes <think>...</think> tags from the response"
                    }
                },
                "required": ["messages"]
            }
        ),
        Tool(
            name="perplexity_reason",
            description="Performs reasoning tasks using the Perplexity API. "
                        "Accepts an array of messages and returns a well-reasoned response "
                        "using the sonar-reasoning-pro model.",
            inputSchema={
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "description": "Array of conversation messages",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "description": "Role of the message (e.g., system, user, assistant)"
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The content of the message"
                                }
                            },
                            "required": ["role", "content"]
                        }
                    },
                    "strip_thinking": {
                        "type": "boolean",
                        "description": "If true, removes <think>...</think> tags from the response"
                    }
                },
                "required": ["messages"]
            }
        ),
    ]


def check_budget() -> None:
    """
    Check if budget allows another API call.

    Raises:
        BudgetExhausted: If daily or monthly limit is exceeded
    """
    logger = get_logger()
    can_call, reason = logger.can_call()
    if not can_call:
        raise BudgetExhausted(reason)


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    # Check budget before making any API call
    try:
        check_budget()
    except BudgetExhausted as e:
        return [TextContent(
            type="text",
            text=f"Budget exhausted: {str(e)}. Use WebSearch as fallback."
        )]

    client = get_client()

    try:
        if name == "perplexity_ask":
            messages = arguments.get("messages", [])
            response = client.ask(messages)
            cost = log_response(response)
            return [TextContent(
                type="text",
                text=response.content
            )]

        elif name == "perplexity_search":
            query = arguments.get("query", "")
            response = client.search(query)
            cost = log_response(response)

            # Format search results
            result = {
                "content": response.content,
                "citations": response.citations,
            }
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "perplexity_research":
            messages = arguments.get("messages", [])
            strip_thinking = arguments.get("strip_thinking", False)
            response = client.research(messages)

            if strip_thinking and "<think>" in response.content:
                import re
                response.content = re.sub(
                    r"<think>.*?</think>",
                    "",
                    response.content,
                    flags=re.DOTALL
                ).strip()

            cost = log_response(response)

            result = {
                "content": response.content,
                "citations": response.citations,
            }
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "perplexity_reason":
            messages = arguments.get("messages", [])
            strip_thinking = arguments.get("strip_thinking", False)
            response = client.reason(messages, strip_thinking=strip_thinking)
            cost = log_response(response)
            return [TextContent(
                type="text",
                text=response.content
            )]

        else:
            return [TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]

    except httpx.HTTPStatusError as e:
        # Sanitize HTTP errors to avoid leaking sensitive info
        status_code = e.response.status_code
        if status_code == 401:
            error_msg = "Authentication failed - check PERPLEXITY_API_KEY"
        elif status_code == 403:
            error_msg = "Access forbidden - API key may lack required permissions"
        elif status_code == 429:
            error_msg = "Rate limit exceeded - try again later"
        elif status_code >= 500:
            error_msg = f"Perplexity server error (HTTP {status_code})"
        else:
            error_msg = f"HTTP error {status_code}"
        return [TextContent(
            type="text",
            text=f"Error: {error_msg}"
        )]
    except httpx.RequestError as e:
        # Network errors - safe to show
        return [TextContent(
            type="text",
            text=f"Error: Network error - {type(e).__name__}"
        )]
    except Exception as e:
        # Generic errors - only show type, not full message which may contain secrets
        return [TextContent(
            type="text",
            text=f"Error: {type(e).__name__} - {str(e)[:100]}"
        )]


async def main():
    """Run the MCP server."""
    # Validate API key at startup
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        print("ERROR: PERPLEXITY_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(1)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
