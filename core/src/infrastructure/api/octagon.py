"""
Octagon AI API client for market intelligence.

Provides earnings call sentiment and institutional holdings analysis.
Designed for conservative usage - cache aggressively, query selectively.

Note: Free tier has limited monthly requests. Only query for high-VRP opportunities.
"""

import logging
import time
from datetime import date
from typing import Optional, Dict, Any
from dataclasses import dataclass

from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.infrastructure.cache.hybrid_cache import HybridCache

logger = logging.getLogger(__name__)

# Rate limiting: Very conservative for free tier
MIN_CALL_INTERVAL = 2.0  # 2 seconds between calls
DAILY_CALL_LIMIT = 20  # Conservative daily limit for free tier


@dataclass
class EarningsCallSentiment:
    """Earnings call sentiment analysis."""
    ticker: str
    quarter: str
    overall_sentiment: str  # bullish, neutral, bearish
    confidence: float  # 0-1
    key_topics: list  # Main topics discussed
    guidance_tone: str  # positive, neutral, negative
    summary: str


@dataclass
class InstitutionalFlow:
    """Institutional holdings changes."""
    ticker: str
    quarter: str
    total_institutional_pct: float
    change_pct: float  # QoQ change
    top_buyers: list  # List of institutions adding
    top_sellers: list  # List of institutions reducing
    summary: str


class OctagonAPI:
    """
    Octagon AI API client for market intelligence.

    Provides:
    - Earnings call sentiment analysis
    - Institutional holdings flow
    - Deep research queries

    Usage: Only call for high-VRP opportunities (>1.8x) to conserve API calls.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache: Optional[HybridCache] = None,
        base_url: str = "https://api.octagonai.co/v1"
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.cache = cache
        self.timeout = 30  # Longer timeout for AI queries
        self._last_call_time = 0.0
        self._daily_calls = 0
        self._daily_reset = date.today()

        # Check if openai library is available
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key,
                base_url=base_url
            ) if api_key else None
        except ImportError:
            logger.warning("openai library not installed. Octagon API unavailable.")
            self._client = None

    def __repr__(self):
        return f"OctagonAPI(base_url={self.base_url}, key={'***' if self.api_key else 'None'})"

    def _check_daily_limit(self) -> bool:
        """Check if daily call limit exceeded."""
        if date.today() != self._daily_reset:
            self._daily_calls = 0
            self._daily_reset = date.today()

        if self._daily_calls >= DAILY_CALL_LIMIT:
            logger.warning(f"Octagon daily limit reached ({DAILY_CALL_LIMIT} calls)")
            return False

        return True

    def _rate_limit(self):
        """Enforce minimum interval between API calls."""
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < MIN_CALL_INTERVAL:
            time.sleep(MIN_CALL_INTERVAL - elapsed)
        self._last_call_time = time.time()
        self._daily_calls += 1

    def is_available(self) -> bool:
        """Check if Octagon API is available and configured."""
        return self._client is not None and self.api_key is not None

    def get_earnings_sentiment(
        self,
        ticker: str,
        quarter: Optional[str] = None
    ) -> Result[EarningsCallSentiment, AppError]:
        """
        Get earnings call sentiment analysis.

        Analyzes the most recent earnings call for tone, key topics, and guidance.

        Args:
            ticker: Stock symbol
            quarter: Specific quarter (e.g., "2024Q3") or None for most recent

        Returns:
            EarningsCallSentiment object
        """
        if not self.is_available():
            return Err(AppError(ErrorCode.CONFIGURATION, "Octagon API not configured"))

        # Check cache first (14 day TTL - earnings calls don't change)
        cache_key = f"octagon:sentiment:{ticker}:{quarter or 'latest'}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                logger.debug(f"Octagon cache hit: {ticker} sentiment")
                return Ok(cached)

        if not self._check_daily_limit():
            return Err(AppError(ErrorCode.RATELIMIT, "Octagon daily limit exceeded"))

        self._rate_limit()

        try:
            # Query Octagon agent for earnings sentiment
            prompt = f"""Analyze the most recent earnings call for {ticker}.
Provide a concise analysis with:
1. Overall sentiment (bullish/neutral/bearish)
2. Key topics discussed (3-5 bullet points)
3. Management guidance tone (positive/neutral/negative)
4. One sentence summary

Format as JSON with keys: sentiment, confidence (0-1), topics (list), guidance, summary"""

            response = self._client.chat.completions.create(
                model="octagon-financial-agent",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500
            )

            content = response.choices[0].message.content

            # Parse response (basic parsing, could be enhanced)
            result = self._parse_sentiment_response(ticker, quarter or "latest", content)

            if result and self.cache:
                self.cache.set(cache_key, result)

            logger.info(f"Octagon: Analyzed earnings sentiment for {ticker}")
            return Ok(result) if result else Err(AppError(ErrorCode.NODATA, "Could not parse sentiment"))

        except Exception as e:
            logger.error(f"Octagon sentiment error for {ticker}: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, f"Octagon error: {e}"))

    def get_institutional_flow(
        self,
        ticker: str
    ) -> Result[InstitutionalFlow, AppError]:
        """
        Get institutional holdings flow analysis.

        Shows which institutions are buying/selling and overall positioning changes.

        Args:
            ticker: Stock symbol

        Returns:
            InstitutionalFlow object
        """
        if not self.is_available():
            return Err(AppError(ErrorCode.CONFIGURATION, "Octagon API not configured"))

        # Check cache (14 day TTL - 13F filings are quarterly)
        cache_key = f"octagon:flow:{ticker}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                logger.debug(f"Octagon cache hit: {ticker} institutional flow")
                return Ok(cached)

        if not self._check_daily_limit():
            return Err(AppError(ErrorCode.RATELIMIT, "Octagon daily limit exceeded"))

        self._rate_limit()

        try:
            prompt = f"""Analyze institutional holdings changes for {ticker}.
Provide:
1. Total institutional ownership percentage
2. Quarter-over-quarter change in institutional ownership
3. Top 3 institutions that increased positions
4. Top 3 institutions that decreased positions
5. One sentence summary of institutional sentiment

Format as JSON with keys: total_pct, change_pct, buyers (list), sellers (list), summary"""

            response = self._client.chat.completions.create(
                model="octagon-financial-agent",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500
            )

            content = response.choices[0].message.content

            result = self._parse_flow_response(ticker, content)

            if result and self.cache:
                self.cache.set(cache_key, result)

            logger.info(f"Octagon: Analyzed institutional flow for {ticker}")
            return Ok(result) if result else Err(AppError(ErrorCode.NODATA, "Could not parse flow"))

        except Exception as e:
            logger.error(f"Octagon flow error for {ticker}: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, f"Octagon error: {e}"))

    def get_research_summary(
        self,
        ticker: str,
        query: str
    ) -> Result[str, AppError]:
        """
        Get custom research summary from Octagon.

        Use sparingly - each call counts against daily limit.

        Args:
            ticker: Stock symbol
            query: Research question

        Returns:
            Research summary string
        """
        if not self.is_available():
            return Err(AppError(ErrorCode.CONFIGURATION, "Octagon API not configured"))

        # Check cache
        cache_key = f"octagon:research:{ticker}:{hash(query)}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                return Ok(cached)

        if not self._check_daily_limit():
            return Err(AppError(ErrorCode.RATELIMIT, "Octagon daily limit exceeded"))

        self._rate_limit()

        try:
            response = self._client.chat.completions.create(
                model="octagon-financial-agent",
                messages=[{"role": "user", "content": f"For {ticker}: {query}"}],
                temperature=0.1,
                max_tokens=1000
            )

            content = response.choices[0].message.content

            if self.cache:
                self.cache.set(cache_key, content)

            return Ok(content)

        except Exception as e:
            return Err(AppError(ErrorCode.EXTERNAL, f"Octagon error: {e}"))

    def _parse_sentiment_response(
        self,
        ticker: str,
        quarter: str,
        content: str
    ) -> Optional[EarningsCallSentiment]:
        """Parse Octagon sentiment response into structured data."""
        import json

        try:
            # Try to extract JSON from response
            # Handle both pure JSON and JSON embedded in text
            json_start = content.find('{')
            json_end = content.rfind('}') + 1

            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                data = json.loads(json_str)

                return EarningsCallSentiment(
                    ticker=ticker,
                    quarter=quarter,
                    overall_sentiment=data.get("sentiment", "neutral"),
                    confidence=float(data.get("confidence", 0.5)),
                    key_topics=data.get("topics", []),
                    guidance_tone=data.get("guidance", "neutral"),
                    summary=data.get("summary", "")
                )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug(f"Failed to parse sentiment JSON: {e}")

        # Fallback: return basic sentiment from text
        sentiment = "neutral"
        if "bullish" in content.lower():
            sentiment = "bullish"
        elif "bearish" in content.lower():
            sentiment = "bearish"

        return EarningsCallSentiment(
            ticker=ticker,
            quarter=quarter,
            overall_sentiment=sentiment,
            confidence=0.5,
            key_topics=[],
            guidance_tone="neutral",
            summary=content[:200] if content else ""
        )

    def _parse_flow_response(
        self,
        ticker: str,
        content: str
    ) -> Optional[InstitutionalFlow]:
        """Parse Octagon institutional flow response."""
        import json

        try:
            json_start = content.find('{')
            json_end = content.rfind('}') + 1

            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                data = json.loads(json_str)

                return InstitutionalFlow(
                    ticker=ticker,
                    quarter="latest",
                    total_institutional_pct=float(data.get("total_pct", 0)),
                    change_pct=float(data.get("change_pct", 0)),
                    top_buyers=data.get("buyers", []),
                    top_sellers=data.get("sellers", []),
                    summary=data.get("summary", "")
                )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug(f"Failed to parse flow JSON: {e}")

        # Fallback
        return InstitutionalFlow(
            ticker=ticker,
            quarter="latest",
            total_institutional_pct=0,
            change_pct=0,
            top_buyers=[],
            top_sellers=[],
            summary=content[:200] if content else ""
        )

    def format_sentiment_summary(self, sentiment: EarningsCallSentiment) -> str:
        """Format sentiment for display in analysis output."""
        emoji = {"bullish": "🟢", "neutral": "🟡", "bearish": "🔴"}.get(
            sentiment.overall_sentiment, "⚪"
        )
        return f"{emoji} {sentiment.overall_sentiment.upper()} (conf: {sentiment.confidence:.0%}) | {sentiment.summary[:100]}"

    def format_flow_summary(self, flow: InstitutionalFlow) -> str:
        """Format institutional flow for display."""
        direction = "↑" if flow.change_pct > 0 else "↓" if flow.change_pct < 0 else "→"
        return f"Inst: {flow.total_institutional_pct:.1f}% ({direction}{abs(flow.change_pct):.1f}% QoQ) | {flow.summary[:80]}"
