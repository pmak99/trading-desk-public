"""
Earnings calendar scanner using yfinance.
Finds earnings in next N days for specified tickers.
"""

import yfinance as yf
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class EarningsScanner:
    """Scan for upcoming earnings dates."""

    def __init__(self, tickers: Optional[List[str]] = None):
        """
        Initialize scanner with ticker list.

        Args:
            tickers: List of ticker symbols to monitor
        """
        self.tickers = tickers or self._get_default_tickers()

    def _get_default_tickers(self) -> List[str]:
        """Default tickers to monitor."""
        return [
            "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL",
            "AMZN", "META", "NFLX", "AMD", "MSTR",
            "JPM", "BAC", "WFC", "GS", "MS",
            "DIS", "NKE", "SBUX", "MCD", "CMG"
        ]

    def get_earnings_candidates(
        self,
        days_ahead: int = 14,
        min_market_cap: float = 10e9
    ) -> List[Dict]:
        """
        Get tickers with earnings in next N days.

        Args:
            days_ahead: Days to look ahead
            min_market_cap: Minimum market cap filter

        Returns:
            List of dicts with ticker info
        """
        candidates = []
        today = datetime.now()

        for ticker_str in self.tickers:
            try:
                ticker = yf.Ticker(ticker_str)
                info = ticker.info

                # Get earnings date
                earnings_date = info.get('earningsDate')
                if not earnings_date:
                    continue

                # Check if within range
                if isinstance(earnings_date, list):
                    earnings_date = earnings_date[0]

                days_until = (earnings_date - today).days

                if 0 <= days_until <= days_ahead:
                    # Get market cap
                    market_cap = info.get('marketCap', 0)

                    if market_cap >= min_market_cap:
                        candidates.append({
                            'ticker': ticker_str,
                            'earnings_date': earnings_date,
                            'days_until': days_until,
                            'market_cap': market_cap,
                            'sector': info.get('sector', 'Unknown'),
                            'industry': info.get('industry', 'Unknown')
                        })

            except Exception as e:
                logger.warning(f"Error processing {ticker_str}: {e}")
                continue

        # Sort by days until earnings
        return sorted(candidates, key=lambda x: x['days_until'])

    def get_earnings_for_ticker(self, ticker: str) -> Optional[Dict]:
        """
        Get earnings info for specific ticker.

        Args:
            ticker: Ticker symbol

        Returns:
            Dict with earnings info or None
        """
        try:
            t = yf.Ticker(ticker)
            info = t.info

            earnings_date = info.get('earningsDate')
            if not earnings_date:
                return None

            if isinstance(earnings_date, list):
                earnings_date = earnings_date[0]

            return {
                'ticker': ticker,
                'earnings_date': earnings_date,
                'market_cap': info.get('marketCap', 0),
                'sector': info.get('sector', 'Unknown')
            }

        except Exception as e:
            logger.error(f"Error getting earnings for {ticker}: {e}")
            return None


# CLI for testing
if __name__ == "__main__":
    scanner = EarningsScanner()
    candidates = scanner.get_earnings_candidates(days_ahead=14)

    print(f"\nFound {len(candidates)} earnings in next 14 days:\n")
    for c in candidates:
        print(f"{c['ticker']:6} - {c['earnings_date'].strftime('%Y-%m-%d')} "
              f"({c['days_until']} days) - {c['sector']}")
