"""Integration wrapper for 5.0's Perplexity client.

Provides direct API access to Perplexity for sentiment analysis.
"""

import sys
import threading
from pathlib import Path
from typing import Dict, Any

from common.constants import PERPLEXITY_COST_PER_CALL_ESTIMATE
from ..utils.paths import MAIN_REPO, REPO_5_0

# Thread lock for sys.path manipulation (not thread-safe by default)
_path_lock = threading.Lock()

# Add 5.0/ to Python path
# 5.0's code uses "from src.core..." imports, so it needs 5.0/ in path, not 5.0/src/
_main_repo = MAIN_REPO
_5_0_dir = _main_repo / "5.0"
_5_0_dir_str = str(_5_0_dir)

with _path_lock:
    # Remove if already in path (so we can re-insert)
    if _5_0_dir_str in sys.path:
        sys.path.remove(_5_0_dir_str)

    # Insert with priority (after 2.0 and 4.0)
    sys.path.insert(2, _5_0_dir_str)


class Perplexity5_0:
    """
    Wrapper for 5.0's Perplexity API client.

    Provides direct API access without MCP, suitable for standalone scripts.

    Example:
        client = Perplexity5_0()
        sentiment = await client.get_sentiment("NVDA", "2026-02-05")
    """

    def __init__(self):
        """Initialize Perplexity client."""
        import os

        # Get API key from environment
        self.api_key = os.environ.get('PERPLEXITY_API_KEY')
        if not self.api_key:
            raise ValueError(
                "PERPLEXITY_API_KEY environment variable not set. "
                "This is required for standalone Perplexity API calls."
            )

        with _path_lock:
            # Critical: Remove 6.0/ from sys.path temporarily to avoid namespace collision
            # Both 6.0 and 5.0 use 'src' as top-level package
            _6_0_paths = [p for p in sys.path if '6.0' in p]
            for p in _6_0_paths:
                sys.path.remove(p)

            # Ensure 5.0/src is at position 0
            if _5_0_dir_str not in sys.path:
                sys.path.insert(0, _5_0_dir_str)
            elif sys.path.index(_5_0_dir_str) != 0:
                sys.path.remove(_5_0_dir_str)
                sys.path.insert(0, _5_0_dir_str)

            try:
                # Clear cached imports of 'src' package to avoid using 6.0's cached version
                if 'src' in sys.modules:
                    # Save 6.0's src modules
                    _6_0_src_modules = {
                        k: v for k, v in sys.modules.items()
                        if k.startswith('src.')
                    }
                    # Clear src from sys.modules
                    del sys.modules['src']
                    for k in list(_6_0_src_modules.keys()):
                        if k in sys.modules:
                            del sys.modules[k]

                # Initialize client (import after sys.path is set)
                from src.integrations.perplexity import PerplexityClient

                # Set database path to main repo
                db_path = _main_repo / "4.0" / "data" / "perplexity_budget.db"

                self.client = PerplexityClient(
                    api_key=self.api_key,
                    db_path=str(db_path),
                    model="sonar"  # Use basic sonar model
                )
            finally:
                # Restore 6.0/ paths after import
                for p in _6_0_paths:
                    if p not in sys.path:
                        sys.path.append(p)

    async def get_sentiment(
        self,
        ticker: str,
        earnings_date: str
    ) -> Dict[str, Any]:
        """
        Get sentiment for ticker's upcoming earnings.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Sentiment dict with direction, score, tailwinds, headwinds
        """
        # Build prompt
        prompt = (
            f"Analyze sentiment for {ticker} earnings on {earnings_date}. "
            f"Provide ONLY:\n"
            f"Direction: [bullish/bearish/neutral]\n"
            f"Score: [number from -1 to +1]\n"
            f"Catalysts: [2-3 bullets, max 10 words each]\n"
            f"Risks: [1-2 bullets, max 10 words each]"
        )

        try:
            # Call Perplexity API
            response = await self.client._request([
                {"role": "user", "content": prompt}
            ])

            # Parse response (import already done in __init__)
            # We have to import here because parse_sentiment_response is in 5.0's module
            with _path_lock:
                _6_0_paths = [p for p in sys.path if '6.0' in p]
                for p in _6_0_paths:
                    if p in sys.path:
                        sys.path.remove(p)

                try:
                    from src.integrations.perplexity import parse_sentiment_response
                    text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
                    sentiment = parse_sentiment_response(text)
                finally:
                    for p in _6_0_paths:
                        if p not in sys.path:
                            sys.path.append(p)

            return {
                'success': True,
                'ticker': ticker,
                'direction': sentiment['direction'],
                'score': sentiment['score'],
                'tailwinds': sentiment['tailwinds'],
                'headwinds': sentiment['headwinds'],
                'raw': sentiment['raw']
            }

        except Exception as e:
            return {
                'success': False,
                'ticker': ticker,
                'error': str(e)
            }

    def can_call(self) -> bool:
        """Check if budget allows Perplexity API call."""
        return self.client.budget.can_call()

    def record_call(self, cost: float = PERPLEXITY_COST_PER_CALL_ESTIMATE):
        """Record an API call for budget tracking."""
        self.client.budget.record_call(cost)
