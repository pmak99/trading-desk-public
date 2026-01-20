"""
Perplexity API client with token tracking.

Makes direct API calls to Perplexity and extracts token usage for budget tracking.
"""

import os
import json
import httpx
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class TokenUsage:
    """Token usage from a Perplexity API response."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    @property
    def output_tokens(self) -> int:
        """Alias for completion_tokens."""
        return self.completion_tokens


@dataclass
class PerplexityResponse:
    """Response from Perplexity API with token tracking."""
    content: str
    citations: list
    usage: TokenUsage
    model: str
    is_search: bool = False


class PerplexityClient:
    """
    Perplexity API client with token extraction.

    Supports all Perplexity operations:
    - ask: Basic chat completion (sonar model)
    - search: Web search API
    - research: Deep research (sonar-pro model)
    - reason: Reasoning tasks (sonar-reasoning-pro model)
    """

    BASE_URL = "https://api.perplexity.ai"

    # Model mapping for operations
    MODELS = {
        "ask": "sonar",
        "search": "sonar",  # Search uses sonar with search enabled
        "research": "sonar-pro",
        "reason": "sonar-reasoning-pro",
    }

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize client.

        Args:
            api_key: Perplexity API key. If not provided, reads from PERPLEXITY_API_KEY env var.
        """
        self.api_key = api_key or os.environ.get("PERPLEXITY_API_KEY")
        if not self.api_key:
            raise ValueError("PERPLEXITY_API_KEY must be set")

        self.http_client = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    def _extract_usage(self, response_data: Dict[str, Any]) -> TokenUsage:
        """Extract token usage from API response."""
        usage = response_data.get("usage", {})
        return TokenUsage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )

    def _extract_content(self, response_data: Dict[str, Any]) -> str:
        """Extract content from API response."""
        choices = response_data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        return message.get("content", "")

    def _extract_citations(self, response_data: Dict[str, Any]) -> list:
        """Extract citations from API response."""
        return response_data.get("citations", [])

    def _make_request(
        self,
        messages: list,
        model: str,
        search_domain_filter: Optional[list] = None,
        return_citations: bool = True,
        return_related_questions: bool = False,
    ) -> PerplexityResponse:
        """
        Make a request to the Perplexity API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use
            search_domain_filter: Optional domain filter for search
            return_citations: Whether to return citations
            return_related_questions: Whether to return related questions

        Returns:
            PerplexityResponse with content, citations, and token usage
        """
        payload = {
            "model": model,
            "messages": messages,
            "return_citations": return_citations,
            "return_related_questions": return_related_questions,
        }

        if search_domain_filter:
            payload["search_domain_filter"] = search_domain_filter

        response = self.http_client.post("/chat/completions", json=payload)
        response.raise_for_status()

        data = response.json()
        return PerplexityResponse(
            content=self._extract_content(data),
            citations=self._extract_citations(data),
            usage=self._extract_usage(data),
            model=data.get("model", model),
            is_search=search_domain_filter is not None or "search" in model.lower(),
        )

    def ask(self, messages: list) -> PerplexityResponse:
        """
        Basic chat completion using sonar model.

        Args:
            messages: List of message dicts

        Returns:
            PerplexityResponse with content and token usage
        """
        return self._make_request(
            messages=messages,
            model=self.MODELS["ask"],
            return_citations=False,
        )

    def search(
        self,
        query: str,
        domain_filter: Optional[list] = None,
    ) -> PerplexityResponse:
        """
        Web search using Perplexity Search API.

        Args:
            query: Search query
            domain_filter: Optional list of domains to search

        Returns:
            PerplexityResponse with search results and token usage
        """
        messages = [{"role": "user", "content": query}]
        return self._make_request(
            messages=messages,
            model=self.MODELS["search"],
            search_domain_filter=domain_filter or [],
            return_citations=True,
        )

    def research(self, messages: list) -> PerplexityResponse:
        """
        Deep research using sonar-pro model.

        Args:
            messages: List of message dicts

        Returns:
            PerplexityResponse with research results and token usage
        """
        return self._make_request(
            messages=messages,
            model=self.MODELS["research"],
            return_citations=True,
        )

    def reason(self, messages: list, strip_thinking: bool = False) -> PerplexityResponse:
        """
        Reasoning tasks using sonar-reasoning-pro model.

        Args:
            messages: List of message dicts
            strip_thinking: If True, removes <think>...</think> tags from response

        Returns:
            PerplexityResponse with reasoning and token usage
        """
        response = self._make_request(
            messages=messages,
            model=self.MODELS["reason"],
            return_citations=False,
        )

        if strip_thinking and "<think>" in response.content:
            import re
            response.content = re.sub(
                r"<think>.*?</think>",
                "",
                response.content,
                flags=re.DOTALL
            ).strip()

        return response

    def close(self):
        """Close the HTTP client."""
        self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
