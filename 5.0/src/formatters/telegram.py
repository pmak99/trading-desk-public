"""
Telegram message formatter.

Creates HTML-formatted messages with emoji for Telegram notifications.
"""

from typing import List, Dict, Any
from datetime import datetime


def format_ticker_line(ticker_data: Dict[str, Any], rank: int) -> str:
    """
    Format single ticker for digest.

    Output format:
    1. AVGO | 7.2x | 82 | BULLISH
       ✅ AI tailwinds  ⚠️ China risk
       → Bull Put 165/160 @ $2.10
    """
    ticker = ticker_data.get("ticker", "???")
    vrp = ticker_data.get("vrp_ratio", 0)
    score = ticker_data.get("score", 0)
    direction = ticker_data.get("direction", "NEUTRAL")
    tailwinds = ticker_data.get("tailwinds", "")
    headwinds = ticker_data.get("headwinds", "")
    strategy = ticker_data.get("strategy", "")
    credit = ticker_data.get("credit", 0)

    # Truncate tailwinds/headwinds
    if len(tailwinds) > 20:
        tailwinds = tailwinds[:17] + "..."
    if len(headwinds) > 20:
        headwinds = headwinds[:17] + "..."

    lines = [
        f"{rank}. <b>{ticker}</b> | {vrp}x | {score} | {direction}",
        f"   ✅ {tailwinds}  ⚠️ {headwinds}",
        f"   → {strategy} @ ${credit:.2f}" if credit else f"   → {strategy}",
    ]

    return "\n".join(lines)


def format_digest(
    date: str,
    tickers: List[Dict[str, Any]],
    budget_calls: int = 0,
    budget_remaining: float = 5.00,
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
    # Parse date for display
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        date_display = dt.strftime("%b %d")
    except ValueError:
        date_display = date

    lines = [
        f"☀️ <b>{date_display} EARNINGS</b> ({len(tickers)} qualified)",
        "",
    ]

    for i, ticker_data in enumerate(tickers, 1):
        lines.append(format_ticker_line(ticker_data, i))
        lines.append("")

    lines.append(f"Budget: {budget_calls}/40 calls | ${budget_remaining:.2f} left")

    return "\n".join(lines)


def format_alert(ticker_data: Dict[str, Any]) -> str:
    """
    Format critical alert for high-VRP opportunity.

    Args:
        ticker_data: Ticker analysis data

    Returns:
        HTML-formatted alert message
    """
    ticker = ticker_data.get("ticker", "???")
    vrp = ticker_data.get("vrp_ratio", 0)
    score = ticker_data.get("score", 0)
    direction = ticker_data.get("direction", "NEUTRAL")
    sentiment_score = ticker_data.get("sentiment_score", 0)
    tailwinds = ticker_data.get("tailwinds", "")
    headwinds = ticker_data.get("headwinds", "")
    strategy = ticker_data.get("strategy", "")
    credit = ticker_data.get("credit", 0)
    risk = ticker_data.get("max_risk", 0)
    pop = ticker_data.get("pop", 0)

    return f"""🚨 <b>{ticker}</b> | VRP {vrp}x | Score {score}

📊 {direction} | Sentiment {sentiment_score:+.1f}
✅ {tailwinds}
⚠️ {headwinds}

💰 <b>{strategy}</b>
   Credit ${credit:.2f} | Risk ${risk:.2f} | POP {pop}%"""
