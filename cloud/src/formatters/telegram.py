"""
Telegram message formatter.

Creates HTML-formatted messages with emoji for Telegram notifications.
"""

import html
from typing import List, Dict, Any
from datetime import datetime

from src.core.config import Settings

settings = Settings()


def format_ticker_line(ticker_data: Dict[str, Any], rank: int) -> str:
    """
    Format single ticker for digest — compact one-line format.

    Output format:
    1. <b>RSG</b> 2.1x | 58 | NEUT | IC $2.39 | AMC
    """
    ticker = ticker_data.get("ticker", "???")
    vrp = ticker_data.get("vrp_ratio", 0)
    score = ticker_data.get("score", 0)
    direction = ticker_data.get("direction", "NEUTRAL")
    strategy_type = ticker_data.get("strategy_type", "")
    credit = ticker_data.get("credit", 0)
    timing = ticker_data.get("timing", "")
    trr_high = ticker_data.get("trr_high", False)

    # Abbreviate direction
    dir_abbr = {
        "BULLISH": "BULL", "BEARISH": "BEAR", "NEUTRAL": "NEUT",
        "STRONG_BULLISH": "STR BULL", "STRONG_BEARISH": "STR BEAR",
    }.get(direction, direction[:4])

    # Score with tradeable indicator (>= 55 = 4.0 threshold)
    score_display = f"{score:.0f} \u2705" if score >= 55 else f"{score:.0f}"

    # Strategy type + credit
    if strategy_type and credit:
        strat_display = f"{strategy_type} ${credit:.2f}"
    elif strategy_type:
        strat_display = strategy_type
    else:
        strat_display = ""

    # Build line
    parts = [f"{rank}. <b>{ticker}</b> {vrp:.1f}x | {score_display} | {dir_abbr}"]
    if strat_display:
        parts[0] += f" | {strat_display}"
    if timing:
        parts[0] += f" | {timing}"
    if trr_high:
        parts[0] += " \u26a0\ufe0f"

    return parts[0]


def format_digest(
    date: str,
    tickers: List[Dict[str, Any]],
    budget_calls: int = 0,
    budget_remaining: float = settings.PERPLEXITY_MONTHLY_BUDGET,
) -> str:
    """
    Format morning digest message.

    Args:
        date: Date string (YYYY-MM-DD)
        tickers: List of qualified ticker data
        budget_calls: API calls used today
        budget_remaining: Budget remaining

    Returns:
        HTML-formatted Telegram message
    """
    # Check if any ticker has earnings_date for grouped mode
    has_dates = any(t.get("earnings_date") for t in tickers)

    if has_dates:
        return _format_digest_grouped(tickers, budget_calls, budget_remaining)

    # Fallback: single-date format (backward compatible)
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        date_display = dt.strftime("%b %d")
    except ValueError:
        date_display = date

    lines = [
        f"\u2600\ufe0f <b>{date_display} EARNINGS</b> ({len(tickers)} qualified)",
        "",
    ]

    for i, ticker_data in enumerate(tickers, 1):
        lines.append(format_ticker_line(ticker_data, i))

    lines.append("")
    lines.append(f"Budget: {budget_calls}/{settings.PERPLEXITY_DAILY_LIMIT} | ${budget_remaining:.2f}")

    return "\n".join(lines)


def _format_digest_grouped(
    tickers: List[Dict[str, Any]],
    budget_calls: int,
    budget_remaining: float,
) -> str:
    """Format digest grouped by earnings_date with sub-headers."""
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

    lines = [
        f"\u2600\ufe0f <b>Earnings Digest</b> ({len(tickers)} qualified)",
        "",
    ]

    rank = 1
    for ed in sorted_dates:
        # Date sub-header
        try:
            dt = datetime.strptime(ed, "%Y-%m-%d")
            date_label = dt.strftime("%b %d")
        except ValueError:
            date_label = ed
        lines.append(f"\U0001f4c5 <b>{date_label}</b>")

        for ticker_data in groups[ed]:
            lines.append(format_ticker_line(ticker_data, rank))
            rank += 1

    lines.append("")
    lines.append(f"Budget: {budget_calls}/{settings.PERPLEXITY_DAILY_LIMIT} | ${budget_remaining:.2f}")

    return "\n".join(lines)


def format_alert(ticker_data: Dict[str, Any]) -> str:
    """
    Format deep analysis result for Telegram /analyze command.

    Args:
        ticker_data: Full analysis data from analyze endpoint

    Returns:
        HTML-formatted Telegram message
    """
    ticker = ticker_data.get("ticker", "???")
    vrp = ticker_data.get("vrp_ratio", 0)
    vrp_tier = ticker_data.get("vrp_tier", "")
    score = ticker_data.get("score", 0)
    direction = ticker_data.get("direction", "NEUTRAL")
    price = ticker_data.get("price", 0)
    earnings_date = ticker_data.get("earnings_date", "")
    timing = ticker_data.get("timing", "")
    sentiment_score = ticker_data.get("sentiment_score", 0)
    tailwinds = ticker_data.get("tailwinds", "")
    headwinds = ticker_data.get("headwinds", "")
    strategy_desc = ticker_data.get("strategy_desc", "")
    strategy_name = ticker_data.get("strategy", "")
    credit = ticker_data.get("credit", 0)
    risk = ticker_data.get("max_risk", 0)
    pop = ticker_data.get("pop", 0)
    liquidity_tier = ticker_data.get("liquidity_tier", "")
    implied_move = ticker_data.get("implied_move_pct", 0)
    hist_mean = ticker_data.get("hist_mean_pct", 0)
    hist_count = ticker_data.get("hist_count", 0)
    trr_ratio = ticker_data.get("trr_ratio", 0)
    trr_level = ticker_data.get("trr_level", "")
    skew_bias = ticker_data.get("skew_bias", "")

    # Header: ticker + VRP tier + score + direction
    score_mark = " \u2705" if score >= 55 else ""
    lines = [f"\U0001f6a8 <b>{html.escape(ticker)}</b> | {vrp_tier} {vrp:.1f}x | Score {score:.0f}{score_mark} | {direction}"]

    # Earnings date + timing + price
    date_parts = []
    if earnings_date:
        date_parts.append(earnings_date)
    if timing:
        date_parts.append(f"({timing})")
    if price:
        date_parts.append(f"| ${price:.2f}")
    if date_parts:
        lines.append(f"\U0001f4c5 {' '.join(date_parts)}")

    # Sentiment + skew
    sent_parts = [f"Sentiment {sentiment_score:+.1f}"]
    if skew_bias:
        sent_parts.append(f"Skew: {skew_bias}")
    lines.append(f"\U0001f4ca {' | '.join(sent_parts)}")

    # Tailwinds/headwinds (only show if non-empty)
    if tailwinds:
        lines.append(f"\u2705 {html.escape(tailwinds)}")
    if headwinds:
        lines.append(f"\u26a0\ufe0f {html.escape(headwinds)}")

    # Strategy with strikes
    if strategy_desc:
        lines.append(f"\n\U0001f4b0 <b>{html.escape(strategy_name)}</b> \u2014 {html.escape(strategy_desc)}")
    elif strategy_name:
        lines.append(f"\n\U0001f4b0 <b>{html.escape(strategy_name)}</b>")
    if credit or risk:
        lines.append(f"  Credit ${credit:.2f} | Risk ${risk:.2f} | POP {pop}%")

    # Historical context + TRR + liquidity
    context_parts = []
    if hist_count:
        context_parts.append(f"{hist_count}Q avg {hist_mean:.1f}%")
    if implied_move:
        context_parts.append(f"implied {implied_move:.1f}%")
    if trr_ratio:
        trr_warn = " \u26a0\ufe0f" if trr_level == "HIGH" else ""
        context_parts.append(f"TRR {trr_ratio:.1f}x {trr_level}{trr_warn}")
    if liquidity_tier:
        context_parts.append(f"Liq: {liquidity_tier}")
    if context_parts:
        lines.append(f"\n\U0001f4c8 {' | '.join(context_parts)}")

    return "\n".join(lines)


def format_council(result) -> str:
    """
    Format council consensus result for Telegram.

    Args:
        result: CouncilResult dataclass or dict

    Returns:
        HTML-formatted Telegram message (max 4096 chars)
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

    if status != "success":
        msg = html.escape(get("status", "Unknown error"))
        return f"\U0001f3db <b>COUNCIL: {ticker}</b>\n\n{msg}"

    # Header
    timing_str = f" ({timing})" if timing else ""
    lines = [
        f"\U0001f3db <b>COUNCIL: {ticker}</b>",
        f"\U0001f4c5 Earnings {earnings_date}{timing_str} | ${price:.2f}",
        "",
    ]

    # Members table
    lines.append("<b>Members</b>")
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
            lines.append(f"  {name[:20]:<20} --     --")
            continue

        # Abbreviate direction
        dir_short = {"bullish": "BULL", "bearish": "BEAR", "neutral": "NEUT"}.get(direction, direction[:4].upper())
        status_suffix = f"  ({html.escape(m_status)})" if m_status else ""
        lines.append(f"  {name[:20]:<20} {dir_short:<4}  {score:+.2f}{status_suffix}")

    lines.append("")

    # Consensus
    consensus_score = get("consensus_score", 0)
    consensus_direction = get("consensus_direction", "neutral")
    agreement = get("agreement", "LOW")
    agreement_count = get("agreement_count", 0)
    active_count = get("active_count", 0)

    lines.append(f"<b>Consensus: {consensus_direction.upper()} {consensus_score:+.2f}</b> | {agreement} ({agreement_count}/{active_count})")

    # Score
    base_score = get("base_score", 0)
    final_score = get("final_score", 0)
    modifier = get("modifier", 0)
    modifier_pct = f"{modifier * 100:+.0f}%" if modifier else "+0%"
    tradeable = "\u2705" if final_score >= 55 else ""
    lines.append(f"Score: {base_score:.0f} \u2192 {final_score:.0f} ({modifier_pct}) {tradeable}")

    # Direction
    direction = get("direction", "NEUTRAL")
    rule_applied = get("rule_applied", "")
    lines.append(f"Direction: {direction}")
    if rule_applied:
        lines.append(f"  {html.escape(rule_applied)}")

    # Risk flags
    risk_flags = get("risk_flags", [])
    if risk_flags:
        lines.append("")
        for flag in risk_flags:
            lines.append(f"\u26a0\ufe0f {html.escape(flag)}")

    return "\n".join(lines)
