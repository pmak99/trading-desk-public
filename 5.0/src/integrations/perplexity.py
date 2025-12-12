"""
Perplexity API client for AI sentiment analysis.

Replaces MCP perplexity integration with direct REST calls.
"""

import re
import httpx
from typing import Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.logging import log

BASE_URL = "https://api.perplexity.ai"


def parse_sentiment_response(text: str) -> Dict[str, Any]:
    """
    Parse structured sentiment response.

    Expected format:
        Direction: [bullish/bearish/neutral]
        Score: [number -1 to +1]
        Catalysts: [tailwinds]
        Risks: [headwinds]
    """
    result = {
        "direction": "neutral",
        "score": 0.0,
        "tailwinds": "",
        "headwinds": "",
        "raw": text,
    }

    # Parse direction
    dir_match = re.search(r'Direction:\s*(bullish|bearish|neutral)', text, re.I)
    if dir_match:
        result["direction"] = dir_match.group(1).lower()

    # Parse score
    score_match = re.search(r'Score:\s*([+-]?\d*\.?\d+)', text)
    if score_match:
        result["score"] = float(score_match.group(1))

    # Parse catalysts/tailwinds
    cat_match = re.search(r'Catalysts?:\s*(.+?)(?=\n|Risks?:|$)', text, re.I | re.S)
    if cat_match:
        result["tailwinds"] = cat_match.group(1).strip()

    # Parse risks/headwinds
    risk_match = re.search(r'Risks?:\s*(.+?)(?=\n|$)', text, re.I | re.S)
    if risk_match:
        result["headwinds"] = risk_match.group(1).strip()

    return result


class PerplexityClient:
    """Async Perplexity API client."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _request(self, messages: list) -> Dict[str, Any]:
        """Make request to Perplexity API."""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-sonar-small-128k-online",
                    "messages": messages,
                }
            )
            response.raise_for_status()
            return response.json()

    async def get_sentiment(
        self,
        ticker: str,
        earnings_date: str
    ) -> Dict[str, Any]:
        """
        Get AI sentiment for ticker earnings.

        Args:
            ticker: Stock symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Parsed sentiment with direction, score, tailwinds, headwinds
        """
        log("info", "Fetching sentiment", ticker=ticker, date=earnings_date)

        prompt = f"""For {ticker} earnings on {earnings_date}, respond ONLY in this format:
Direction: [bullish/bearish/neutral]
Score: [number -1 to +1]
Catalysts: [2 bullets, max 10 words each]
Risks: [1 bullet, max 10 words]"""

        messages = [{"role": "user", "content": prompt}]

        data = await self._request(messages)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        result = parse_sentiment_response(content)
        result["ticker"] = ticker
        result["earnings_date"] = earnings_date

        return result
