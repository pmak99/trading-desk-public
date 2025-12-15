"""
Perplexity API client for AI sentiment analysis.

Replaces MCP perplexity integration with direct REST calls.
"""

import asyncio
import os
import re
import time
import httpx
from typing import Dict, Any, Optional

from src.core.logging import log, get_request_id
from src.core.budget import BudgetTracker, BudgetExhausted
from src.core import metrics

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

    # Parse score with bounds validation
    score_match = re.search(r'Score:\s*([+-]?\d*\.?\d+)', text)
    if score_match:
        score = float(score_match.group(1))
        # Clamp to valid range [-1, +1]
        result["score"] = max(-1.0, min(1.0, score))

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

    # Default model - can be overridden via environment or constructor
    # Note: llama-3.1-sonar-* models were discontinued Feb 22, 2025
    # Current models: sonar, sonar-pro, sonar-reasoning, sonar-reasoning-pro
    DEFAULT_MODEL = "sonar"

    def __init__(
        self,
        api_key: str,
        db_path: str = "data/ivcrush.db",
        model: str = None,
        budget_tracker: Optional[BudgetTracker] = None
    ):
        self.api_key = api_key
        self.db_path = db_path
        self.model = model or os.environ.get("PERPLEXITY_MODEL", self.DEFAULT_MODEL)
        self.budget = budget_tracker or BudgetTracker(db_path=db_path)

    async def _request(self, messages: list) -> Dict[str, Any]:
        """Make request to Perplexity API with retry handling."""
        start_time = time.time()

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        f"{BASE_URL}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "X-Request-ID": get_request_id(),  # Distributed tracing
                        },
                        json={
                            "model": self.model,
                            "messages": messages,
                        }
                    )

                    if response.status_code != 200:
                        log("warn", "Perplexity API error",
                            status=response.status_code,
                            response=response.text[:200] if response.text else "empty")
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        duration_ms = (time.time() - start_time) * 1000
                        metrics.api_call("perplexity", duration_ms, success=False)
                        return {"error": f"API error: {response.status_code}"}

                    duration_ms = (time.time() - start_time) * 1000
                    metrics.api_call("perplexity", duration_ms, success=True)
                    return response.json()

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                log("warn", "Perplexity request failed", error=str(e), attempt=attempt+1)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                duration_ms = (time.time() - start_time) * 1000
                metrics.api_call("perplexity", duration_ms, success=False)
                return {"error": str(e)}

        duration_ms = (time.time() - start_time) * 1000
        metrics.api_call("perplexity", duration_ms, success=False)
        return {"error": "All retries failed"}

    # Model-specific pricing (per token) as of 2025
    PRICING = {
        "sonar": {"input": 0.000001, "output": 0.000001},           # $1/M input, $1/M output
        "sonar-pro": {"input": 0.000003, "output": 0.000015},       # $3/M input, $15/M output
        "sonar-reasoning": {"input": 0.000001, "output": 0.000005}, # $1/M input, $5/M output
        "sonar-reasoning-pro": {"input": 0.000002, "output": 0.000008},  # Estimated
    }

    def _estimate_cost(self, data: Dict[str, Any]) -> float:
        """
        Estimate cost from API response usage data.

        Uses model-specific pricing for accurate cost tracking.
        Falls back to conservative estimate (sonar-pro pricing) if no usage data.
        """
        usage = data.get("usage", {})

        # Get pricing for current model, default to most expensive (sonar-pro) for safety
        pricing = self.PRICING.get(self.model, self.PRICING["sonar-pro"])

        if not usage:
            # Accurate estimate based on prompt structure: ~50 input, ~150 output tokens
            return max(0.001, pricing["input"] * 50 + pricing["output"] * 150)

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        input_cost = input_tokens * pricing["input"]
        output_cost = output_tokens * pricing["output"]

        # Add small buffer for safety
        return max(0.001, round(input_cost + output_cost, 6))

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
        # Atomic check-and-acquire with estimated cost
        if not self.budget.try_acquire_call("perplexity", cost=0.005):
            log("warn", "Perplexity budget exceeded", ticker=ticker)
            raise BudgetExhausted(
                service="perplexity",
                reason=f"Budget exhausted before fetching sentiment for {ticker}"
            )

        log("info", "Fetching sentiment", ticker=ticker, date=earnings_date)

        prompt = f"""For {ticker} earnings on {earnings_date}, respond ONLY in this format:
Direction: [bullish/bearish/neutral]
Score: [number -1 to +1]
Catalysts: [2 bullets, max 10 words each]
Risks: [1 bullet, max 10 words]"""

        messages = [{"role": "user", "content": prompt}]

        data = await self._request(messages)

        # Handle error response
        if "error" in data:
            log("warn", "Sentiment fetch failed", ticker=ticker, error=data["error"])
            return {
                "direction": "neutral",
                "score": 0.0,
                "tailwinds": "",
                "headwinds": "",
                "error": data["error"],
                "ticker": ticker,
                "earnings_date": earnings_date,
            }

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        result = parse_sentiment_response(content)
        result["ticker"] = ticker
        result["earnings_date"] = earnings_date

        # Parse actual cost from response (for reporting, call already counted)
        actual_cost = self._estimate_cost(data)
        result["api_cost"] = actual_cost

        return result
