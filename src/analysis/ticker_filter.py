"""
Ticker filtering algorithm to select best earnings candidates.
Selects top N tickers by score based on IV metrics and liquidity.
"""

import yfinance as yf
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.options.data_client import OptionsDataClient
from src.options.tradier_client import TradierOptionsClient
from src.analysis.scorers import CompositeScorer

logger = logging.getLogger(__name__)


class TickerFilter:
    """Filter and score earnings candidates."""

    def __init__(self, cache_ttl_minutes: int = 15):
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

        Args:
            cache_ttl_minutes: Cache TTL in minutes (default: 15)
        """
        self.weights = {
            'iv_score': 0.40,         # 40% - PRIMARY: IV % 60%+ required, 80%+ ideal
            'options_liquidity': 0.30, # 30% - CRITICAL: Volume, OI, bid-ask spreads (tighter spreads!)
            'iv_crush_edge': 0.25,    # 25% - Historical implied > actual move
            'fundamentals': 0.05      # 5% - Market cap, price for premium quality
        }

        # IV Rank thresholds from your criteria
        self.IV_RANK_MIN = 50      # Skip anything below this
        self.IV_RANK_GOOD = 60     # Standard allocation
        self.IV_RANK_EXCELLENT = 75 # Larger allocation, prefer spreads

        # Cache for ticker data (prevents redundant API calls)
        self._ticker_cache: Dict[str, tuple[Dict, datetime]] = {}
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)

        # Initialize composite scorer (Strategy pattern - refactored from 172-line god function)
        self.scorer = CompositeScorer(min_iv=self.IV_RANK_MIN)

        # Initialize Tradier client (preferred - real IV data)
        self.tradier_client = None
        try:
            self.tradier_client = TradierOptionsClient()
            if self.tradier_client.is_available():
                logger.info("Using Tradier API for real IV data")
            else:
                logger.info("Tradier client initialized but not available (missing API key)")
                self.tradier_client = None
        except (ValueError, KeyError, OSError) as e:
            # Expected errors: missing config, env vars, file access
            logger.info(f"Tradier client not configured: {e}")
        except Exception as e:
            # Unexpected initialization errors
            logger.warning(f"Unexpected error initializing Tradier client: {e}", exc_info=True)

        # Initialize fallback options client (yfinance RV proxy)
        self.options_client = None
        try:
            self.options_client = OptionsDataClient()
            logger.debug("Options data client (yfinance fallback) initialized")
        except (ValueError, KeyError) as e:
            # Expected configuration errors
            logger.info(f"Options client not configured: {e}")
        except Exception as e:
            # Unexpected initialization errors
            logger.warning(f"Unexpected error initializing options client: {e}", exc_info=True)

    def get_ticker_data(self, ticker: str, use_cache: bool = True) -> Optional[Dict]:
        """
        Get market data for a ticker including options data.

        Fetches yfinance data ONCE and extracts all needed information
        to avoid duplicate API calls. Uses TTL cache to prevent redundant requests.

        Args:
            ticker: Ticker symbol
            use_cache: Whether to use cache (default: True)

        Returns:
            Dict with market data and options_data or None if error
        """
        # Check cache first
        if use_cache and ticker in self._ticker_cache:
            cached_data, cached_time = self._ticker_cache[ticker]
            age = datetime.now() - cached_time
            if age < self._cache_ttl:
                logger.debug(f"{ticker}: Cache hit (age: {age.seconds}s)")
                return cached_data
            else:
                logger.debug(f"{ticker}: Cache expired (age: {age.seconds}s)")

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

                        # Calculate IV crush ratio (check for division by zero)
                        expected = options_data.get('expected_move_pct', 0)
                        actual = options_data.get('avg_actual_move_pct', 0)
                        if expected > 0 and actual > 0:
                            iv_crush_ratio = expected / actual
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

                    # Calculate IV crush ratio (check for division by zero)
                    expected = options_data.get('expected_move_pct', 0)
                    actual = options_data.get('avg_actual_move_pct', 0)
                    if expected > 0 and actual > 0:
                        iv_crush_ratio = expected / actual
                        options_data['iv_crush_ratio'] = round(iv_crush_ratio, 2)

                    options_data['data_source'] = 'yfinance_rv_proxy'

                except Exception as e:
                    logger.warning(f"{ticker}: Could not fetch options data: {e}")

            data['options_data'] = options_data

            # Update cache
            if use_cache:
                self._ticker_cache[ticker] = (data, datetime.now())
                logger.debug(f"{ticker}: Cached ticker data")

            return data

        except Exception as e:
            logger.error(f"Error fetching data for {ticker}: {e}")
            return None

    def calculate_score(self, data: Dict) -> float:
        """
        Calculate ticker score for IV crush strategy.

        REFACTORED: Now delegates to CompositeScorer (Strategy pattern).
        Previous 172-line implementation broken into separate scorer classes.

        Scoring based on Trading Research Prompt.pdf criteria:
        - IV % >= 60% required (uses actual implied volatility from options)
        - 60-80% IV is good, 80-100% excellent, 100%+ premium for IV crush trades
        - Historical implied move > actual move (IV overpricing edge)
        - Options liquidity (volume, OI, bid-ask spreads)

        Args:
            data: Ticker data dict (with optional options_data from Tradier)

        Returns:
            Score (0-100), or 0 if IV < 60%
        """
        return self.scorer.calculate_score(data)

    def _process_single_ticker(self, ticker: str) -> Optional[Dict]:
        """
        Process a single ticker: fetch data and calculate score.

        Args:
            ticker: Ticker symbol

        Returns:
            Dict with ticker data and score, or None if filtered out
        """
        try:
            logger.info(f"Analyzing {ticker}...")

            data = self.get_ticker_data(ticker)
            if not data:
                return None

            score = self.calculate_score(data)

            # HARD FILTER: Skip if IV Rank < 50% (score = 0)
            if score == 0:
                logger.info(f"{ticker}: Filtered out (IV Rank < 50%)")
                return None

            data['score'] = score
            return data

        except Exception as e:
            logger.warning(f"{ticker}: Processing failed: {e}")
            return None

    def filter_and_score_tickers(
        self,
        tickers: List[str],
        max_tickers: int = 10,
        parallel: bool = True,
        max_workers: int = 5
    ) -> List[Dict]:
        """
        Filter and score a list of tickers.

        FILTERS OUT:
        - Tickers with IV Rank < 50% (score = 0)
        - Tickers with liquidity below minimums (volume < 100, OI < 500)
        - Tickers with insufficient data

        Args:
            tickers: List of ticker symbols
            max_tickers: Max number to process (pass len(tickers) to score ALL)
            parallel: Use parallel processing (default: True)
            max_workers: Max parallel workers (default: 5)

        Returns:
            List of dicts with ticker data and scores, sorted by score descending
            Only returns tickers that pass all filters (score > 0)
        """
        # No longer need shuffle since we score ALL tickers and sort by score
        # This ensures deterministic, reproducible results
        tickers_to_process = tickers[:max_tickers]
        results = []

        if parallel and len(tickers_to_process) > 1:
            # Parallel processing (much faster for I/O-bound operations)
            logger.debug(f"Processing {len(tickers_to_process)} tickers in parallel ({max_workers} workers)")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_ticker = {
                    executor.submit(self._process_single_ticker, ticker): ticker
                    for ticker in tickers_to_process
                }

                # Collect results as they complete
                for future in as_completed(future_to_ticker):
                    ticker = future_to_ticker[future]
                    try:
                        data = future.result()
                        if data:
                            results.append(data)
                    except Exception as e:
                        logger.error(f"{ticker}: Unexpected error: {e}")
        else:
            # Sequential processing (fallback or single ticker)
            logger.debug(f"Processing {len(tickers_to_process)} tickers sequentially")
            for ticker in tickers_to_process:
                data = self._process_single_ticker(ticker)
                if data:
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

            # Process up to 10x the requested count (min 20, max 100) to avoid alphabetical bias
            max_process = min(max(pre_market_count * 10, 20), 100)
            scored = self.filter_and_score_tickers(pre_tickers, max_tickers=max_process)
            selected['pre_market'] = scored[:pre_market_count]

        # Process after-hours tickers
        if 'after_hours' in earnings_by_timing:
            ah_tickers = earnings_by_timing['after_hours']
            logger.info(f"Scoring {len(ah_tickers)} after-hours candidates...")

            # Process up to 10x the requested count (min 30, max 100) to avoid alphabetical bias
            max_process = min(max(after_hours_count * 10, 30), 100)
            scored = self.filter_and_score_tickers(ah_tickers, max_tickers=max_process)
            selected['after_hours'] = scored[:after_hours_count]

        return selected


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    from src.data.calendars.base import EarningsCalendar
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
            time = earning.get('time', '').lower()
            ticker = earning.get('ticker', '')

            if 'pre-market' in time or 'pre_market' in time or 'bmo' in time:
                by_timing['pre_market'].append(ticker)
            elif 'after-hours' in time or 'after_hours' in time or 'amc' in time:
                by_timing['after_hours'].append(ticker)
            elif time:  # Has time field but unknown timing - default to after-hours
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
