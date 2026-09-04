#!/usr/bin/env python
"""Finnhub MCP stdio server, backed by the project's own FinnhubAPI client.

Why this exists instead of an off-the-shelf Finnhub MCP server:

  1. Tool names. The seven skill files in .claude/commands/ call
     `finnhub_calendar_data`, `finnhub_stock_market_data`,
     `finnhub_stock_estimates` and `finnhub_news_sentiment` with an
     `operation=` argument. No published server uses that shape, so a
     third-party server would mean rewriting every call site.
  2. Rate-limit discipline. `src/infrastructure/api/finnhub.py` carries the
     token bucket and the Retry-After-aware 429 backoff added after the
     2026-08-24 incident. This server routes through that same client and
     imports its constants rather than re-deriving them, so the two paths
     cannot drift apart.

Honest limitation: the token bucket is per-process. This server runs as its
own process, so it does NOT share a budget with a concurrent `./trade.sh`
run — each is independently well-behaved, but their calls still sum against
Finnhub's real 60/min. Prefer not to run a heavy `/maintenance sync` and a
Finnhub-backed skill at the same moment.

stdout is the MCP transport. All logging goes to stderr.
"""

import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "2.0"))

from src.config.config import Config  # noqa: E402
from src.container import Container  # noqa: E402
from src.infrastructure.api.finnhub import (  # noqa: E402
    MAX_RESPONSE_SIZE,
    RATE_LIMIT_BACKOFF_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
)

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("finnhub-mcp")

DEFAULT_NEWS_LIMIT = 20

server = MCPServer(name="finnhub")

_container: Optional[Container] = None


def get_finnhub():
    """Lazily build the project container and hand back its Finnhub client."""
    global _container
    if _container is None:
        _container = Container(Config.from_env())
    return _container.finnhub


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    if not value:
        raise ToolError(f"{field} is required")
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _coerce_int(value: Any, default: int) -> int:
    """Coerce a limit that may arrive as a string.

    MCP arguments cross a JSON boundary, so `max_results` can arrive as "10"
    rather than 10. Comparing that against an int raised
    `'>' not supported between instances of 'str' and 'int'` and failed the
    whole call — the bug council.md still warns callers to work around by
    omitting the argument. Coercing here retires that workaround.
    """
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _raw_get(path: str, params: dict) -> dict | list:
    """GET a Finnhub endpoint the typed client doesn't wrap.

    Mirrors `FinnhubAPI._fetch_calendar_window`'s retry contract, reusing that
    module's constants and this client's own rate limiter so behaviour under
    a 429 matches the trading path exactly.
    """
    client = get_finnhub()

    if client.rate_limiter and not client.rate_limiter.acquire(blocking=True):
        raise ToolError("Finnhub rate limit exceeded (local bucket)")

    params = {**params, "token": client.api_key}

    for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
        response = requests.get(
            f"{client.base_url}/{path}", params=params, timeout=client.timeout
        )

        if response.status_code == 429:
            if attempt < RATE_LIMIT_MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else RATE_LIMIT_BACKOFF_SECONDS
                except ValueError:
                    wait = RATE_LIMIT_BACKOFF_SECONDS
                logger.warning("Finnhub 429 on %s — backing off %.0fs", path, wait)
                time.sleep(wait)
                continue
            raise ToolError("Finnhub rate limit exceeded (429, retries exhausted)")

        if response.status_code == 403:
            raise ToolError(
                f"Finnhub denied /{path}: this endpoint is not on the free tier "
                f"(HTTP 403). Verified 2026-08-31 for calendar/economic."
            )

        response.raise_for_status()

        if len(response.content) > MAX_RESPONSE_SIZE:
            raise ToolError(f"Response too large: {len(response.content)} bytes")

        return response.json()

    raise ToolError("Finnhub rate limit exceeded")


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------

def _earnings_calendar(args: dict) -> dict:
    from_date = _parse_date(args.get("from_date"), "from_date")
    to_date = _parse_date(args.get("to_date"), "to_date")
    symbol = (args.get("symbol") or "").strip().upper() or None

    result = get_finnhub().get_earnings_calendar(
        symbol=symbol, from_date=from_date, to_date=to_date
    )
    if result.is_err:
        raise ToolError(str(result.unwrap_err()))

    entries = [
        {
            "symbol": ticker,
            "earnings_date": earnings_date.isoformat(),
            "timing": timing.value if hasattr(timing, "value") else str(timing),
        }
        for ticker, earnings_date, timing in result.unwrap()
    ]
    return {"count": len(entries), "earnings": entries}


def _economic_calendar(args: dict) -> dict:
    return _raw_get(
        "calendar/economic",
        {
            "from": _parse_date(args.get("from_date"), "from_date").isoformat(),
            "to": _parse_date(args.get("to_date"), "to_date").isoformat(),
        },
    )


def _quote(args: dict) -> dict:
    symbol = (args.get("symbol") or "").strip().upper()
    if not symbol:
        raise ToolError("symbol is required")
    data = _raw_get("quote", {"symbol": symbol})
    return {
        "symbol": symbol,
        "current": data.get("c"),
        "change": data.get("d"),
        "percent_change": data.get("dp"),
        "high": data.get("h"),
        "low": data.get("l"),
        "open": data.get("o"),
        "previous_close": data.get("pc"),
    }


def _recommendations(args: dict) -> dict:
    symbol = (args.get("symbol") or "").strip().upper()
    if not symbol:
        raise ToolError("symbol is required")
    data = _raw_get("stock/recommendation", {"symbol": symbol})
    if not data:
        return {"symbol": symbol, "recommendations": []}
    return {"symbol": symbol, "recommendations": data[:4]}


def _company_news(args: dict) -> dict:
    symbol = (args.get("symbol") or "").strip().upper()
    if not symbol:
        raise ToolError("symbol is required")
    to_date = _parse_date(args.get("to_date") or date.today(), "to_date")
    from_date = _parse_date(
        args.get("from_date") or (to_date - timedelta(days=7)), "from_date"
    )
    limit = _coerce_int(args.get("max_results"), DEFAULT_NEWS_LIMIT)

    data = _raw_get(
        "company-news",
        {"symbol": symbol, "from": from_date.isoformat(), "to": to_date.isoformat()},
    )
    articles = [
        {
            "headline": a.get("headline"),
            "summary": (a.get("summary") or "")[:300],
            "source": a.get("source"),
            "url": a.get("url"),
            "datetime": datetime.fromtimestamp(a["datetime"], tz=timezone.utc).isoformat()
            if a.get("datetime")
            else None,
        }
        for a in (data or [])[:limit]
    ]
    return {"symbol": symbol, "count": len(articles), "articles": articles}


OPERATIONS = {
    "finnhub_calendar_data": {
        "get_earnings_calendar": _earnings_calendar,
        "get_economic_calendar": _economic_calendar,
    },
    "finnhub_stock_market_data": {
        # health.md says operation="quote", council.md says "get_quote".
        # Both are live call sites, so both are accepted.
        "get_quote": _quote,
        "quote": _quote,
    },
    "finnhub_stock_estimates": {
        "get_recommendations": _recommendations,
    },
    "finnhub_news_sentiment": {
        "get_company_news": _company_news,
    },
}


def _dispatch(tool: str, arguments: dict) -> str:
    """Route a (tool, operation) pair to its handler and serialize the result."""
    handlers = OPERATIONS[tool]
    operation = arguments.get("operation")
    handler = handlers.get(operation)
    if handler is None:
        raise ToolError(
            f"Unknown operation '{operation}' for {tool}. "
            f"Supported: {', '.join(sorted(handlers))}"
        )
    return json.dumps(handler(arguments), indent=2, default=str)


@server.tool(
    name="finnhub_calendar_data",
    description=(
        "Earnings calendar (chunked to avoid Finnhub's 1,500-entry cap) or "
        "economic calendar. NOTE: get_economic_calendar is a premium endpoint "
        "and returns HTTP 403 on this account's free tier."
    ),
)
def finnhub_calendar_data(
    operation: str,
    from_date: str,
    to_date: str,
    symbol: str | None = None,
) -> str:
    """operation: get_earnings_calendar | get_economic_calendar. Dates YYYY-MM-DD."""
    return _dispatch(
        "finnhub_calendar_data",
        {
            "operation": operation,
            "from_date": from_date,
            "to_date": to_date,
            "symbol": symbol,
        },
    )


@server.tool(
    name="finnhub_stock_market_data",
    description="Real-time quote for a symbol.",
)
def finnhub_stock_market_data(operation: str, symbol: str) -> str:
    """operation: get_quote (council.md) or quote (health.md) — both accepted."""
    return _dispatch(
        "finnhub_stock_market_data", {"operation": operation, "symbol": symbol}
    )


@server.tool(
    name="finnhub_stock_estimates",
    description="Analyst recommendation trends (strongBuy/buy/hold/sell/strongSell).",
)
def finnhub_stock_estimates(operation: str, symbol: str) -> str:
    """operation: get_recommendations."""
    return _dispatch(
        "finnhub_stock_estimates", {"operation": operation, "symbol": symbol}
    )


@server.tool(
    name="finnhub_news_sentiment",
    description="Company news headlines over a date window.",
)
def finnhub_news_sentiment(
    operation: str,
    symbol: str,
    from_date: str | None = None,
    to_date: str | None = None,
    max_results: int | str | None = None,
) -> str:
    """operation: get_company_news. Defaults to the trailing 7 days.

    max_results accepts a string as well as an int — see _coerce_int.
    """
    return _dispatch(
        "finnhub_news_sentiment",
        {
            "operation": operation,
            "symbol": symbol,
            "from_date": from_date,
            "to_date": to_date,
            "max_results": max_results,
        },
    )


if __name__ == "__main__":
    server.run(transport="stdio")
