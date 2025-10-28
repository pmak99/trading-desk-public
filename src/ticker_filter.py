"""
Ticker filtering algorithm to select best earnings candidates.
Selects 2 pre-market + 3 after-hours tickers per trading day.
"""

import yfinance as yf
from typing import List, Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class TickerFilter:
    """Filter and score earnings candidates."""

    def __init__(self):
        """Initialize ticker filter."""
        self.weights = {
            'market_cap': 0.30,      # 30% - Prefer large caps (liquidity)
            'volume': 0.25,          # 25% - Prefer high volume (tradability)
            'iv_rank': 0.20,         # 20% - Prefer high IV rank (options premiums)
            'avg_volume': 0.15,      # 15% - Consistent volume
            'price': 0.10            # 10% - Prefer reasonable price ($20-500)
        }

    def get_ticker_data(self, ticker: str) -> Optional[Dict]:
        """
        Get market data for a ticker.

        Args:
            ticker: Ticker symbol

        Returns:
            Dict with market data or None if error
        """
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            # Get historical data for volume analysis
            hist = stock.history(period='1mo')

            if hist.empty:
                logger.warning(f"No historical data for {ticker}")
                return None

            data = {
                'ticker': ticker,
                'market_cap': info.get('marketCap', 0),
                'volume': hist['Volume'].iloc[-1] if len(hist) > 0 else 0,
                'avg_volume': info.get('averageVolume', 0),
                'price': info.get('currentPrice') or info.get('regularMarketPrice', 0),
                'iv': info.get('impliedVolatility', 0),  # May not always be available
                'sector': info.get('sector', 'Unknown'),
                'beta': info.get('beta', 1.0)
            }

            return data

        except Exception as e:
            logger.error(f"Error fetching data for {ticker}: {e}")
            return None

    def calculate_score(self, data: Dict) -> float:
        """
        Calculate ticker score based on multiple factors.

        Args:
            data: Ticker data dict

        Returns:
            Score (0-100)
        """
        score = 0.0

        # 1. Market Cap Score (0-100)
        market_cap = data.get('market_cap', 0)
        if market_cap >= 500e9:  # $500B+ mega cap
            market_cap_score = 100
        elif market_cap >= 200e9:  # $200B+ large cap
            market_cap_score = 90
        elif market_cap >= 50e9:  # $50B+ mid-large cap
            market_cap_score = 70
        elif market_cap >= 10e9:  # $10B+ mid cap
            market_cap_score = 50
        else:
            market_cap_score = 20

        score += market_cap_score * self.weights['market_cap']

        # 2. Volume Score (0-100)
        volume = data.get('volume', 0)
        avg_volume = data.get('avg_volume', 0)

        if avg_volume > 0:
            volume_ratio = volume / avg_volume
            if volume_ratio >= 1.5:  # 50%+ above average
                volume_score = 100
            elif volume_ratio >= 1.2:  # 20%+ above average
                volume_score = 80
            elif volume_ratio >= 0.8:  # Within 20% of average
                volume_score = 60
            else:
                volume_score = 30
        else:
            volume_score = 50  # Neutral if no data

        score += volume_score * self.weights['volume']

        # 3. IV Rank Score (0-100)
        # Note: We'll estimate this since yfinance doesn't provide IV rank directly
        # High IV is preferred for earnings plays
        iv = data.get('iv', 0)
        if iv >= 0.60:  # Very high IV
            iv_score = 100
        elif iv >= 0.40:  # High IV
            iv_score = 80
        elif iv >= 0.25:  # Moderate IV
            iv_score = 60
        elif iv >= 0.15:  # Low IV
            iv_score = 40
        else:
            iv_score = 50  # Neutral if no data

        score += iv_score * self.weights['iv_rank']

        # 4. Average Volume Score (0-100) - Liquidity measure
        if avg_volume >= 10_000_000:  # 10M+ very liquid
            avg_vol_score = 100
        elif avg_volume >= 5_000_000:  # 5M+ liquid
            avg_vol_score = 80
        elif avg_volume >= 1_000_000:  # 1M+ tradable
            avg_vol_score = 60
        elif avg_volume >= 500_000:  # 500K+ okay
            avg_vol_score = 40
        else:
            avg_vol_score = 20

        score += avg_vol_score * self.weights['avg_volume']

        # 5. Price Score (0-100) - Prefer reasonable option prices
        price = data.get('price', 0)
        if 50 <= price <= 300:  # Ideal range for options
            price_score = 100
        elif 20 <= price <= 500:  # Acceptable range
            price_score = 80
        elif 10 <= price < 20 or 500 < price <= 1000:  # Less ideal
            price_score = 50
        else:
            price_score = 30

        score += price_score * self.weights['price']

        return round(score, 2)

    def filter_and_score_tickers(
        self,
        tickers: List[str],
        max_tickers: int = 10
    ) -> List[Dict]:
        """
        Filter and score a list of tickers.

        Args:
            tickers: List of ticker symbols
            max_tickers: Max number to process (avoid rate limits)

        Returns:
            List of dicts with ticker data and scores, sorted by score
        """
        results = []

        for ticker in tickers[:max_tickers]:
            logger.info(f"Analyzing {ticker}...")

            data = self.get_ticker_data(ticker)
            if not data:
                continue

            score = self.calculate_score(data)
            data['score'] = score
            results.append(data)

        # Sort by score descending
        results.sort(key=lambda x: x['score'], reverse=True)

        return results

    def select_daily_candidates(
        self,
        earnings_by_timing: Dict[str, List[str]],
        pre_market_count: int = 2,
        after_hours_count: int = 3
    ) -> Dict[str, List[Dict]]:
        """
        Select best candidates for the day.

        Args:
            earnings_by_timing: Dict with 'pre_market' and 'after_hours' ticker lists
            pre_market_count: Number of pre-market tickers to select
            after_hours_count: Number of after-hours tickers to select

        Returns:
            Dict with 'pre_market' and 'after_hours' selected candidates with scores
        """
        selected = {
            'pre_market': [],
            'after_hours': []
        }

        # Process pre-market tickers
        if 'pre_market' in earnings_by_timing:
            pre_tickers = earnings_by_timing['pre_market']
            logger.info(f"Scoring {len(pre_tickers)} pre-market candidates...")

            scored = self.filter_and_score_tickers(pre_tickers, max_tickers=15)
            selected['pre_market'] = scored[:pre_market_count]

        # Process after-hours tickers
        if 'after_hours' in earnings_by_timing:
            ah_tickers = earnings_by_timing['after_hours']
            logger.info(f"Scoring {len(ah_tickers)} after-hours candidates...")

            scored = self.filter_and_score_tickers(ah_tickers, max_tickers=20)
            selected['after_hours'] = scored[:after_hours_count]

        return selected


# CLI for testing
if __name__ == "__main__":
    from src.earnings_calendar import EarningsCalendar
    from collections import defaultdict

    print()
    print('='*70)
    print('TICKER FILTER - SELECT BEST EARNINGS CANDIDATES')
    print('='*70)
    print()

    # Get this week's earnings
    calendar = EarningsCalendar()
    week_earnings = calendar.get_week_earnings(days=7)

    if not week_earnings:
        print("No earnings found for this week.")
        exit()

    filter_system = TickerFilter()

    # Process each day
    for date_str, earnings in list(week_earnings.items())[:1]:  # Just first day for demo
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        day_name = date_obj.strftime('%A, %B %d, %Y')

        print(f"\n{day_name}")
        print('='*70)
        print(f"Total earnings: {len(earnings)} companies")
        print()

        # Separate by timing
        by_timing = defaultdict(list)
        for earning in earnings:
            time = earning.get('time', '')
            ticker = earning.get('ticker', '')

            if 'pre-market' in time:
                by_timing['pre_market'].append(ticker)
            elif 'after-hours' in time:
                by_timing['after_hours'].append(ticker)

        print(f"Pre-market: {len(by_timing['pre_market'])} tickers")
        print(f"After-hours: {len(by_timing['after_hours'])} tickers")
        print()

        # Select best candidates (2 pre-market + 3 after-hours)
        print("Analyzing and scoring tickers...")
        print()

        selected = filter_system.select_daily_candidates(
            by_timing,
            pre_market_count=2,
            after_hours_count=3
        )

        # Display results
        print("\n🏆 SELECTED CANDIDATES (2 pre-market + 3 after-hours)")
        print('-'*70)

        if selected['pre_market']:
            print("\nPRE-MARKET (Top 2):")
            for i, ticker_data in enumerate(selected['pre_market'], 1):
                print(f"{i}. {ticker_data['ticker']:6s} - Score: {ticker_data['score']:5.1f}")
                print(f"   Market Cap: ${ticker_data['market_cap']/1e9:.1f}B")
                print(f"   Volume: {ticker_data['volume']:,} (Avg: {ticker_data['avg_volume']:,})")
                print(f"   Price: ${ticker_data['price']:.2f}")

        if selected['after_hours']:
            print("\nAFTER-HOURS (Top 3):")
            for i, ticker_data in enumerate(selected['after_hours'], 1):
                print(f"{i}. {ticker_data['ticker']:6s} - Score: {ticker_data['score']:5.1f}")
                print(f"   Market Cap: ${ticker_data['market_cap']/1e9:.1f}B")
                print(f"   Volume: {ticker_data['volume']:,} (Avg: {ticker_data['avg_volume']:,})")
                print(f"   Price: ${ticker_data['price']:.2f}")

        print()
        print('='*70)
        print(f"\n✅ Selected {len(selected['pre_market']) + len(selected['after_hours'])} tickers for analysis")
        print("   (2 pre-market + 3 after-hours = 5 total per day)")
        print('='*70)

        break  # Just show first day for demo
