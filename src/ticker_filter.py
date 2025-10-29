"""
Ticker filtering algorithm to select best earnings candidates.
Selects 2 pre-market + 3 after-hours tickers per trading day.
"""

import yfinance as yf
from typing import List, Dict, Optional
from datetime import datetime
import logging
from src.options_data_client import OptionsDataClient
from src.tradier_options_client import TradierOptionsClient

logger = logging.getLogger(__name__)


class TickerFilter:
    """Filter and score earnings candidates."""

    def __init__(self):
        """
        Initialize ticker filter.

        Optimized for POST-EARNINGS IV CRUSH STRATEGY:
        - Sell premium before earnings (high IV Rank 75%+)
        - Buy it back cheaper after earnings (IV crush)
        - Based on criteria from Trading Research Prompt.pdf

        KEY FILTERS:
        - IV Rank > 50% (minimum), prefer 75%+ for max edge
        - Historical implied move > actual move (consistent IV overpricing)
        - Liquid options markets (tight spreads, high OI/volume)
        """
        self.weights = {
            'iv_rank': 0.50,          # 50% - PRIMARY: IV Rank 75%+ is ideal
            'iv_crush_edge': 0.30,    # 30% - Historical implied > actual move
            'options_liquidity': 0.15, # 15% - Volume, OI, bid-ask spreads
            'fundamentals': 0.05      # 5% - Market cap, price for premium quality
        }

        # IV Rank thresholds from your criteria
        self.IV_RANK_MIN = 50      # Skip anything below this
        self.IV_RANK_GOOD = 60     # Standard allocation
        self.IV_RANK_EXCELLENT = 75 # Larger allocation, prefer spreads

        # Initialize Tradier client (preferred - real IV data)
        try:
            self.tradier_client = TradierOptionsClient()
            if self.tradier_client.is_available():
                logger.info("Using Tradier API for real IV data")
            else:
                self.tradier_client = None
        except Exception as e:
            logger.warning(f"Tradier client not available: {e}")
            self.tradier_client = None

        # Initialize fallback options client (yfinance RV proxy)
        try:
            self.options_client = OptionsDataClient()
        except Exception as e:
            logger.warning(f"Options client not available: {e}")
            self.options_client = None

    def get_ticker_data(self, ticker: str) -> Optional[Dict]:
        """
        Get market data for a ticker including options data.

        Fetches yfinance data ONCE and extracts all needed information
        to avoid duplicate API calls.

        Args:
            ticker: Ticker symbol

        Returns:
            Dict with market data and options_data or None if error
        """
        try:
            # Fetch yfinance data ONCE
            stock = yf.Ticker(ticker)
            info = stock.info

            # Get historical data for volume analysis
            hist = stock.history(period='1y')  # Get full year for IV rank calculation

            if hist.empty:
                logger.warning(f"No historical data for {ticker}")
                return None

            # Get current price
            current_price = info.get('currentPrice') or info.get('regularMarketPrice') or hist['Close'].iloc[-1]

            data = {
                'ticker': ticker,
                'market_cap': info.get('marketCap', 0),
                'volume': hist['Volume'].iloc[-1] if len(hist) > 0 else 0,
                'avg_volume': info.get('averageVolume', 0),
                'price': current_price,
                'iv': info.get('impliedVolatility', 0),
                'sector': info.get('sector', 'Unknown'),
                'beta': info.get('beta', 1.0)
            }

            # Get options data - try Tradier first (real IV), fall back to yfinance (RV proxy)
            options_data = {}

            # Try Tradier first (preferred - real implied volatility)
            if self.tradier_client:
                try:
                    logger.debug(f"{ticker}: Fetching options data from Tradier (real IV)")
                    tradier_data = self.tradier_client.get_options_data(ticker, current_price)

                    if tradier_data:
                        options_data = tradier_data

                        # Get supplemental data from yfinance:
                        # 1. Historical earnings moves (not in Tradier)
                        # 2. IV Rank based on RV (Tradier doesn't have 52-week IV history yet)
                        if self.options_client:
                            try:
                                yf_data = self.options_client.get_options_data_from_stock(
                                    stock, ticker, hist, current_price
                                )

                                # Extract earnings-related fields
                                options_data['avg_actual_move_pct'] = yf_data.get('avg_actual_move_pct', 0)
                                options_data['last_earnings_move'] = yf_data.get('last_earnings_move', 0)
                                options_data['earnings_beat_rate'] = yf_data.get('earnings_beat_rate', 0)

                                # Use yfinance IV Rank (RV-based) if Tradier doesn't have it
                                # Note: yfinance RV Rank is ~70-80% correlated with real IV Rank
                                if options_data.get('iv_rank', 0) == 0 and yf_data.get('iv_rank', 0) > 0:
                                    options_data['iv_rank'] = yf_data['iv_rank']
                                    options_data['iv_rank_source'] = 'yfinance_rv_proxy'
                                    logger.debug(f"{ticker}: Using yfinance RV Rank ({options_data['iv_rank']}%) as proxy")

                            except Exception as e:
                                logger.warning(f"{ticker}: Could not get yfinance supplement: {e}")

                        # Calculate IV crush ratio
                        if options_data.get('expected_move_pct') and options_data.get('avg_actual_move_pct'):
                            iv_crush_ratio = options_data['expected_move_pct'] / options_data['avg_actual_move_pct']
                            options_data['iv_crush_ratio'] = round(iv_crush_ratio, 2)

                        # Mark as using real IV
                        options_data['data_source'] = 'tradier'
                        logger.debug(f"{ticker}: Using Tradier data (real IV: {options_data.get('current_iv')}%)")

                except Exception as e:
                    logger.warning(f"{ticker}: Tradier failed, falling back to yfinance: {e}")

            # Fall back to OptionsDataClient (yfinance RV proxy) if Tradier didn't work
            if not options_data and self.options_client:
                try:
                    logger.debug(f"{ticker}: Using yfinance fallback (RV proxy)")
                    options_data = self.options_client.get_options_data_from_stock(
                        stock, ticker, hist, current_price
                    )

                    # Calculate IV crush ratio
                    if options_data.get('expected_move_pct') and options_data.get('avg_actual_move_pct'):
                        iv_crush_ratio = options_data['expected_move_pct'] / options_data['avg_actual_move_pct']
                        options_data['iv_crush_ratio'] = round(iv_crush_ratio, 2)

                    options_data['data_source'] = 'yfinance_rv_proxy'

                except Exception as e:
                    logger.warning(f"{ticker}: Could not fetch options data: {e}")

            data['options_data'] = options_data

            return data

        except Exception as e:
            logger.error(f"Error fetching data for {ticker}: {e}")
            return None

    def calculate_score(self, data: Dict) -> float:
        """
        Calculate ticker score for IV crush strategy.

        Scoring based on Trading Research Prompt.pdf criteria:
        - IV Rank > 50% required, 75%+ preferred
        - Historical implied move > actual move (IV overpricing edge)
        - Options liquidity (volume, OI, bid-ask spreads)

        Args:
            data: Ticker data dict (with optional options_data from Alpha Vantage)

        Returns:
            Score (0-100), or 0 if IV Rank < 50%
        """
        options_data = data.get('options_data', {})

        # ==========================================
        # 1. IV Rank Score (50% weight) - PRIMARY
        # ==========================================
        iv_rank = options_data.get('iv_rank', None)

        if iv_rank is not None:
            # HARD FILTER: Skip anything below 50% IV Rank
            if iv_rank < self.IV_RANK_MIN:
                logger.info(f"{data['ticker']}: IV Rank {iv_rank}% < {self.IV_RANK_MIN}% - SKIPPING")
                return 0.0

            # Score based on IV Rank thresholds
            if iv_rank >= self.IV_RANK_EXCELLENT:  # 75%+
                iv_rank_score = 100
            elif iv_rank >= self.IV_RANK_GOOD:  # 60-75%
                iv_rank_score = 70 + (iv_rank - self.IV_RANK_GOOD) * 2  # Scale 70-100
            else:  # 50-60%
                iv_rank_score = 50 + (iv_rank - self.IV_RANK_MIN) * 2  # Scale 50-70
        else:
            # No options data yet - use basic IV estimate from yfinance
            iv = data.get('iv', 0)
            if iv >= 0.60:
                iv_rank_score = 80  # Probably high IV rank
            elif iv >= 0.40:
                iv_rank_score = 60
            else:
                iv_rank_score = 30  # Low confidence without real IV rank

        # ==========================================
        # 2. IV Crush Edge Score (30% weight)
        # ==========================================
        # Does implied move historically > actual move?
        iv_crush_ratio = options_data.get('iv_crush_ratio', None)

        if iv_crush_ratio is not None:
            # iv_crush_ratio > 1.0 means implied consistently beats actual (GOOD!)
            if iv_crush_ratio >= 1.3:  # Implied 30%+ higher than actual
                iv_crush_score = 100
            elif iv_crush_ratio >= 1.2:  # Implied 20%+ higher
                iv_crush_score = 80
            elif iv_crush_ratio >= 1.1:  # Implied 10%+ higher
                iv_crush_score = 60
            elif iv_crush_ratio >= 1.0:  # Implied slightly higher
                iv_crush_score = 40
            else:  # Implied < actual (BAD - no edge)
                iv_crush_score = 0
        else:
            # No historical data yet - assume neutral
            iv_crush_score = 50

        # ==========================================
        # 3. Options Liquidity Score (15% weight)
        # ==========================================
        options_volume = options_data.get('options_volume', 0)
        open_interest = options_data.get('open_interest', 0)
        bid_ask_spread_pct = options_data.get('bid_ask_spread_pct', None)

        liquidity_score = 0

        # Options volume component (40% of liquidity score)
        if options_volume >= 50000:  # Very high options volume
            vol_score = 100
        elif options_volume >= 10000:  # High
            vol_score = 80
        elif options_volume >= 5000:  # Good
            vol_score = 60
        elif options_volume >= 1000:  # Acceptable
            vol_score = 40
        else:
            vol_score = 20
        liquidity_score += vol_score * 0.4

        # Open interest component (40% of liquidity score)
        if open_interest >= 100000:  # Very liquid
            oi_score = 100
        elif open_interest >= 50000:  # Liquid
            oi_score = 80
        elif open_interest >= 10000:  # Good
            oi_score = 60
        elif open_interest >= 5000:  # Acceptable
            oi_score = 40
        else:
            oi_score = 20
        liquidity_score += oi_score * 0.4

        # Bid-ask spread component (20% of liquidity score)
        if bid_ask_spread_pct is not None:
            if bid_ask_spread_pct <= 0.02:  # 2% or less - excellent
                spread_score = 100
            elif bid_ask_spread_pct <= 0.05:  # 5% or less - good
                spread_score = 80
            elif bid_ask_spread_pct <= 0.10:  # 10% or less - okay
                spread_score = 60
            else:  # Wide spreads - bad
                spread_score = 20
        else:
            spread_score = 50  # No data
        liquidity_score += spread_score * 0.2

        # ==========================================
        # 4. Fundamentals Score (5% weight)
        # ==========================================
        market_cap = data.get('market_cap', 0)
        price = data.get('price', 0)

        # Market cap (50% of fundamentals)
        if market_cap >= 200e9:  # $200B+ mega/large cap
            cap_score = 100
        elif market_cap >= 50e9:  # $50B+
            cap_score = 80
        elif market_cap >= 10e9:  # $10B+
            cap_score = 60
        else:
            cap_score = 40

        # Price range for quality premiums (50% of fundamentals)
        if 50 <= price <= 400:  # Ideal for selling premium
            price_score = 100
        elif 20 <= price <= 500:  # Acceptable
            price_score = 80
        else:
            price_score = 50

        fundamentals_score = (cap_score + price_score) / 2

        # ==========================================
        # TOTAL SCORE
        # ==========================================
        total_score = (
            iv_rank_score * self.weights['iv_rank'] +
            iv_crush_score * self.weights['iv_crush_edge'] +
            liquidity_score * self.weights['options_liquidity'] +
            fundamentals_score * self.weights['fundamentals']
        )

        return round(total_score, 2)

    def filter_and_score_tickers(
        self,
        tickers: List[str],
        max_tickers: int = 10
    ) -> List[Dict]:
        """
        Filter and score a list of tickers.

        FILTERS OUT:
        - Tickers with IV Rank < 50% (score = 0)
        - Tickers with insufficient data

        Args:
            tickers: List of ticker symbols
            max_tickers: Max number to process (avoid rate limits)

        Returns:
            List of dicts with ticker data and scores, sorted by score
            Only returns tickers that pass IV Rank filter (score > 0)
        """
        results = []

        for ticker in tickers[:max_tickers]:
            logger.info(f"Analyzing {ticker}...")

            data = self.get_ticker_data(ticker)
            if not data:
                continue

            score = self.calculate_score(data)

            # HARD FILTER: Skip if IV Rank < 50% (score = 0)
            if score == 0:
                logger.info(f"{ticker}: Filtered out (IV Rank < 50%)")
                continue

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
    logging.basicConfig(level=logging.INFO)

    from src.earnings_calendar import EarningsCalendar
    from collections import defaultdict

    logger.info("")
    logger.info('='*70)
    logger.info('TICKER FILTER - SELECT BEST EARNINGS CANDIDATES')
    logger.info('='*70)
    logger.info("")

    # Get this week's earnings
    calendar = EarningsCalendar()
    week_earnings = calendar.get_week_earnings(days=7)

    if not week_earnings:
        logger.info("No earnings found for this week.")
        exit()

    filter_system = TickerFilter()

    # Process each day
    for date_str, earnings in list(week_earnings.items())[:1]:  # Just first day for demo
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        day_name = date_obj.strftime('%A, %B %d, %Y')

        logger.info(f"\n{day_name}")
        logger.info('='*70)
        logger.info(f"Total earnings: {len(earnings)} companies")
        logger.info("")

        # Separate by timing
        by_timing = defaultdict(list)
        for earning in earnings:
            time = earning.get('time', '')
            ticker = earning.get('ticker', '')

            if 'pre-market' in time:
                by_timing['pre_market'].append(ticker)
            elif 'after-hours' in time:
                by_timing['after_hours'].append(ticker)

        logger.info(f"Pre-market: {len(by_timing['pre_market'])} tickers")
        logger.info(f"After-hours: {len(by_timing['after_hours'])} tickers")
        logger.info("")

        # Select best candidates (2 pre-market + 3 after-hours)
        logger.info("Analyzing and scoring tickers...")
        logger.info("")

        selected = filter_system.select_daily_candidates(
            by_timing,
            pre_market_count=2,
            after_hours_count=3
        )

        # Display results
        logger.info("\n🏆 SELECTED CANDIDATES (2 pre-market + 3 after-hours)")
        logger.info('-'*70)

        if selected['pre_market']:
            logger.info("\nPRE-MARKET (Top 2):")
            for i, ticker_data in enumerate(selected['pre_market'], 1):
                logger.info(f"{i}. {ticker_data['ticker']:6s} - Score: {ticker_data['score']:5.1f}")
                logger.info(f"   Market Cap: ${ticker_data['market_cap']/1e9:.1f}B")
                logger.info(f"   Volume: {ticker_data['volume']:,} (Avg: {ticker_data['avg_volume']:,})")
                logger.info(f"   Price: ${ticker_data['price']:.2f}")

        if selected['after_hours']:
            logger.info("\nAFTER-HOURS (Top 3):")
            for i, ticker_data in enumerate(selected['after_hours'], 1):
                logger.info(f"{i}. {ticker_data['ticker']:6s} - Score: {ticker_data['score']:5.1f}")
                logger.info(f"   Market Cap: ${ticker_data['market_cap']/1e9:.1f}B")
                logger.info(f"   Volume: {ticker_data['volume']:,} (Avg: {ticker_data['avg_volume']:,})")
                logger.info(f"   Price: ${ticker_data['price']:.2f}")

        logger.info("")
        logger.info('='*70)
        logger.info(f"\n✅ Selected {len(selected['pre_market']) + len(selected['after_hours'])} tickers for analysis")
        logger.info("   (2 pre-market + 3 after-hours = 5 total per day)")
        logger.info('='*70)

        break  # Just show first day for demo
