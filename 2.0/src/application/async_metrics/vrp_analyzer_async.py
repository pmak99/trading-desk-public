"""Async wrapper for concurrent ticker analysis."""

import asyncio
import logging
from datetime import date
from typing import List

from src.application.services.analyzer import TickerAnalyzer
from src.domain.types import TickerAnalysis
from src.domain.errors import Result, AppError

logger = logging.getLogger(__name__)


class AsyncTickerAnalyzer:
    """Async wrapper for TickerAnalyzer with concurrency control.

    Allows analyzing multiple tickers concurrently while respecting
    API rate limits through semaphore-based throttling.

    Args:
        sync_analyzer: Synchronous TickerAnalyzer instance
    """

    def __init__(self, sync_analyzer: TickerAnalyzer):
        self.sync_analyzer = sync_analyzer

    async def analyze_many(
        self,
        tickers: List[str],
        earnings_date: date,
        expiration: date,
        max_concurrent: int = 10,
    ) -> List[tuple[str, Result[TickerAnalysis, AppError]]]:
        """Analyze multiple tickers concurrently.

        Args:
            tickers: List of ticker symbols to analyze
            earnings_date: Date of earnings announcement
            expiration: Option expiration date
            max_concurrent: Maximum concurrent analyses

        Returns:
            List of tuples (ticker, result) for each ticker
        """

        semaphore = asyncio.Semaphore(max_concurrent)

        async def analyze_with_limit(ticker):
            """Analyze single ticker with concurrency limit."""
            async with semaphore:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: self.sync_analyzer.analyze(ticker, earnings_date, expiration)
                )
                return ticker, result

        # Create tasks for all tickers
        tasks = [analyze_with_limit(ticker) for ticker in tickers]

        # Execute all tasks concurrently (respecting semaphore limit)
        results = await asyncio.gather(*tasks, return_exceptions=False)

        return results
