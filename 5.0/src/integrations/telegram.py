"""
Telegram Bot API client for notifications.

Sends alerts and daily digests to configured chat.
"""

import httpx
from typing import Dict, List, Any, Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from src.core.logging import log

BASE_URL = "https://api.telegram.org/bot"
MAX_MESSAGE_LENGTH = 4096  # Telegram API limit


class TelegramSender:
    """Async Telegram Bot API client."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"{BASE_URL}{bot_token}"

    def _truncate_message(self, text: str) -> str:
        """Truncate message to Telegram's limit if needed."""
        if len(text) <= MAX_MESSAGE_LENGTH:
            return text
        # Truncate with ellipsis, preserving room for indicator
        truncated = text[:MAX_MESSAGE_LENGTH - 20]
        return truncated + "\n\n[...truncated]"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
    )
    async def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send message via Telegram API."""
        try:
            # Truncate to Telegram's limit if needed
            text = self._truncate_message(text)

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data.get("ok", False)
        except Exception as e:
            log("error", "Telegram send failed", error=str(e))
            return False

    async def send_message(self, text: str) -> bool:
        """
        Send plain text message.

        Args:
            text: Message text

        Returns:
            True if sent successfully
        """
        log("debug", "Sending Telegram message", length=len(text))
        return await self._send(text)

    def _format_alert(
        self,
        ticker: str,
        score: int,
        vrp: float,
        implied_move: float,
        direction: str = "NEUTRAL",
        liquidity: str = "GOOD",
    ) -> str:
        """Format alert message with HTML."""
        emoji = "🎯" if score >= 80 else "📊" if score >= 70 else "📈"

        return f"""
{emoji} <b>IV Crush Alert: {ticker}</b>

<b>Score:</b> {score}/100
<b>VRP:</b> {vrp:.1f}x
<b>Implied Move:</b> {implied_move:.1f}%
<b>Direction:</b> {direction}
<b>Liquidity:</b> {liquidity}

#ivcrush #{ticker.lower()}
""".strip()

    async def send_alert(
        self,
        ticker: str,
        score: int,
        vrp: float,
        implied_move: float,
        direction: str = "NEUTRAL",
        liquidity: str = "GOOD",
    ) -> bool:
        """
        Send trading alert.

        Args:
            ticker: Stock symbol
            score: Composite score (0-100)
            vrp: VRP ratio
            implied_move: Implied move percentage
            direction: BULLISH, BEARISH, or NEUTRAL
            liquidity: Liquidity tier

        Returns:
            True if sent successfully
        """
        log("info", "Sending alert", ticker=ticker, score=score)
        message = self._format_alert(ticker, score, vrp, implied_move, direction, liquidity)
        return await self._send(message)

    def _format_digest(
        self,
        date: str,
        tickers: List[Dict[str, Any]],
    ) -> str:
        """Format daily digest message."""
        header = f"📋 <b>IV Crush Digest: {date}</b>\n\n"

        if not tickers:
            return header + "No high-VRP opportunities today."

        lines = []
        for t in tickers[:10]:  # Top 10
            symbol = t.get("symbol", "???")
            score = t.get("score", 0)
            vrp = t.get("vrp", 0)
            lines.append(f"• <b>{symbol}</b>: Score {score}, VRP {vrp:.1f}x")

        return header + "\n".join(lines) + "\n\n#ivcrush #digest"

    async def send_digest(
        self,
        date: str,
        tickers: List[Dict[str, Any]],
    ) -> bool:
        """
        Send daily digest.

        Args:
            date: Earnings date
            tickers: List of ticker data dicts

        Returns:
            True if sent successfully
        """
        log("info", "Sending digest", date=date, ticker_count=len(tickers))
        message = self._format_digest(date, tickers)
        return await self._send(message)

    async def send_error(self, error_msg: str, context: str = "") -> bool:
        """
        Send error notification.

        Args:
            error_msg: Error message
            context: Additional context

        Returns:
            True if sent successfully
        """
        text = f"⚠️ <b>IV Crush Error</b>\n\n{error_msg}"
        if context:
            text += f"\n\n<i>Context: {context}</i>"

        log("warn", "Sending error notification", error=error_msg)
        return await self._send(text)
