"""
CLI formatter for terminal output.

Creates ASCII-formatted tables for Mac terminal display.
"""

from typing import List, Dict, Any
from datetime import datetime

from src.core.config import Settings

settings = Settings()


def format_ticker_line_cli(ticker_data: Dict[str, Any], rank: int) -> str:
    """
    Format single ticker for CLI.

    Output format:
     1  AVGO     7.2x   82     BULLISH  Bull Put 165/160
        + AI demand          - China risk
    """
    ticker = ticker_data.get("ticker", "???")
    vrp = ticker_data.get("vrp_ratio", 0)
    score = ticker_data.get("score", 0)
    direction = ticker_data.get("direction", "NEUTRAL")[:7]  # Truncate
    tailwinds = ticker_data.get("tailwinds", "")[:18]
    headwinds = ticker_data.get("headwinds", "")[:18]
    strategy = ticker_data.get("strategy", "")

    line1 = f" {rank}  {ticker:<8} {vrp}x   {score:<5} {direction:<8} {strategy}"
    line2 = f"    + {tailwinds:<18} - {headwinds}"

    return f"{line1}\n{line2}"


def format_digest_cli(
    date: str,
    tickers: List[Dict[str, Any]],
    budget_calls: int = 0,
    budget_remaining: float = settings.PERPLEXITY_MONTHLY_BUDGET,
) -> str:
    """
    Format morning digest for CLI with ASCII borders.
    """
    # Check if any ticker has earnings_date for grouped mode
    has_dates = any(t.get("earnings_date") for t in tickers)

    if has_dates:
        return _format_digest_cli_grouped(tickers, budget_calls, budget_remaining)

    # Fallback: single-date format (backward compatible)
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        date_display = dt.strftime("%b %d")
    except ValueError:
        date_display = date

    width = 55
    border = "═" * width
    thin = "─" * width

    lines = [
        border,
        f" {date_display} EARNINGS ({len(tickers)} qualified)",
        border,
        " #  TICKER   VRP    SCORE  DIR      STRATEGY",
        thin,
    ]

    for i, ticker_data in enumerate(tickers, 1):
        lines.append(format_ticker_line_cli(ticker_data, i))

    lines.extend([
        thin,
        f" Budget: {budget_calls}/{settings.PERPLEXITY_DAILY_LIMIT} calls | ${budget_remaining:.2f} remaining",
        border,
    ])

    return "\n".join(lines)


def _format_digest_cli_grouped(
    tickers: List[Dict[str, Any]],
    budget_calls: int,
    budget_remaining: float,
) -> str:
    """Format CLI digest grouped by earnings_date with ASCII sub-headers."""
    # Group by earnings_date
    groups: dict[str, List[Dict[str, Any]]] = {}
    for t in tickers:
        ed = t.get("earnings_date", "")
        groups.setdefault(ed, []).append(t)

    # Sort groups chronologically
    sorted_dates = sorted(groups.keys())

    # Sort tickers within each group by score descending
    for ed in sorted_dates:
        groups[ed].sort(key=lambda t: t.get("score", 0), reverse=True)

    width = 55
    border = "═" * width
    thin = "─" * width

    lines = [
        border,
        f" EARNINGS DIGEST ({len(tickers)} qualified)",
        border,
        " #  TICKER   VRP    SCORE  DIR      STRATEGY",
        thin,
    ]

    rank = 1
    for ed in sorted_dates:
        # Date sub-header
        try:
            dt = datetime.strptime(ed, "%Y-%m-%d")
            date_label = dt.strftime("%b %d")
        except ValueError:
            date_label = ed
        lines.append(f" --- {date_label} ---")

        for ticker_data in groups[ed]:
            lines.append(format_ticker_line_cli(ticker_data, rank))
            rank += 1

    lines.extend([
        thin,
        f" Budget: {budget_calls}/{settings.PERPLEXITY_DAILY_LIMIT} calls | ${budget_remaining:.2f} remaining",
        border,
    ])

    return "\n".join(lines)


def format_analyze_cli(data: Dict[str, Any]) -> str:
    """
    Format /analyze output for CLI.
    """
    ticker = data.get("ticker", "???")
    date = data.get("earnings_date", "")
    timing = data.get("timing", "")
    vrp = data.get("vrp_ratio", 0)
    vrp_tier = data.get("vrp_tier", "")
    score = data.get("score", 0)
    implied = data.get("implied_move_pct", 0)
    historical = data.get("historical_mean", 0)
    liquidity = data.get("liquidity_tier", "")
    direction = data.get("direction", "NEUTRAL")
    sentiment = data.get("sentiment_score", 0)
    tailwinds = data.get("tailwinds", "")
    headwinds = data.get("headwinds", "")
    strategy = data.get("strategy", "")
    credit = data.get("credit", 0)
    risk = data.get("max_risk", 0)
    pop = data.get("pop", 0)
    size = data.get("position_size", 0)

    width = 55
    border = "═" * width
    thin = "─" * width

    return f"""{border}
 {ticker} Analysis - {date} ({timing})
{border}
 VRP: {vrp}x ({vrp_tier})    Score: {score}
 Implied: {implied}%            Historical: {historical}%
 Liquidity: {liquidity}
{thin}
 SENTIMENT: {direction} ({sentiment:+.1f})
 + {tailwinds}
 - {headwinds}
{thin}
 TOP STRATEGY: {strategy}
 Credit: ${credit:.2f} | Max Risk: ${risk:.2f} | POP: {pop}%
 Size: {size} contracts (Half-Kelly)
{border}"""


def format_council_cli(result) -> str:
    """
    Format council consensus result for CLI.

    Args:
        result: CouncilResult dataclass or dict

    Returns:
        ASCII-formatted table string
    """
    # Support both dataclass and dict
    if hasattr(result, "ticker"):
        d = result
        get = lambda k, default=None: getattr(d, k, default)
        members = d.members
    else:
        get = lambda k, default=None: result.get(k, default)
        members = result.get("members", [])

    ticker = get("ticker", "???")
    earnings_date = get("earnings_date", "")
    timing = get("timing", "")
    price = get("price", 0)
    status = get("status", "")

    width = 60
    border = "=" * width
    thin = "-" * width

    if status != "success":
        return f"{border}\n COUNCIL: {ticker} - {status}\n{border}"

    timing_str = f" ({timing})" if timing else ""
    lines = [
        border,
        f" COUNCIL: {ticker} | {earnings_date}{timing_str} | ${price:.2f}",
        border,
        f" {'Member':<22} {'Dir':<6} {'Score':>6}  {'Status'}",
        thin,
    ]

    for m in members:
        if hasattr(m, "name"):
            name, score, direction, failed, m_status = m.name, m.score, m.direction, m.failed, m.status
        else:
            name = m.get("name", "")
            score = m.get("score", 0)
            direction = m.get("direction", "")
            failed = m.get("failed", False)
            m_status = m.get("status", "")

        if failed:
            lines.append(f" {name:<22} {'--':<6} {'--':>6}  {m_status}")
        else:
            dir_short = {"bullish": "BULL", "bearish": "BEAR", "neutral": "NEUT"}.get(direction, direction[:4].upper())
            lines.append(f" {name:<22} {dir_short:<6} {score:+.2f}  {m_status}")

    lines.append(thin)

    consensus_score = get("consensus_score", 0)
    consensus_direction = get("consensus_direction", "neutral")
    agreement = get("agreement", "LOW")
    agreement_count = get("agreement_count", 0)
    active_count = get("active_count", 0)

    lines.append(f" Consensus: {consensus_direction.upper()} {consensus_score:+.2f} | {agreement} ({agreement_count}/{active_count})")

    base_score = get("base_score", 0)
    final_score = get("final_score", 0)
    modifier = get("modifier", 0)
    modifier_pct = f"{modifier * 100:+.0f}%" if modifier else "+0%"
    lines.append(f" Score: {base_score:.0f} -> {final_score:.0f} ({modifier_pct})")

    direction = get("direction", "NEUTRAL")
    rule_applied = get("rule_applied", "")
    lines.append(f" Direction: {direction}")
    if rule_applied:
        lines.append(f"   {rule_applied}")

    risk_flags = get("risk_flags", [])
    if risk_flags:
        lines.append(thin)
        for flag in risk_flags:
            lines.append(f" WARNING: {flag}")

    lines.append(border)
    return "\n".join(lines)
