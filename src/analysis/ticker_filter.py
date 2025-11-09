"""
Ticker filtering and scoring for IV crush earnings strategies.

Filters tickers by IV metrics, liquidity, and historical performance.
Uses LRU caching and batch fetching for optimal performance.
"""

import yfinance as yf
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.options.data_client import OptionsDataClient
from src.options.tradier_client import TradierOptionsClient
from src.analysis.scorers import CompositeScorer
from src.core.retry_utils import retry_on_rate_limit
from src.core.lru_cache import LRUCache

logger = logging.getLogger(__name__)

# Constants for filtering and rate limiting
RATE_LIMIT_DELAY_SECONDS = 0.3  # Delay between individual API requests
MIN_MARKET_CAP_DOLLARS = 500_000_000  # $500M minimum for tradeable options
MIN_DAILY_VOLUME = 100_000  # 100K shares/day minimum for liquidity
BATCH_PREFETCH_THRESHOLD = 5  # Only batch prefetch for 5+ tickers


class TickerFilter:
    """Filter and score earnings candidates."""

    def __init__(self, cache_ttl_minutes: int = 15):
        """
        Initialize ticker filter for IV crush strategies.

        Strategy: Sell premium before earnings when IV is high, buy back after IV crush.

        Filters:
            - IV Rank >50% (prefer 75%+)
            - Liquid options (tight spreads, high OI/volume)
            - Historical implied > actual moves

        Args:
            cache_ttl_minutes: Cache TTL in minutes (default: 15)
        """
        self.weights = {
            'iv_score': 0.40,          # IV level and rank
            'options_liquidity': 0.30,  # Volume, OI, spreads
            'iv_crush_edge': 0.25,     # Historical IV > actual
            'fundamentals': 0.05       # Market cap, price
        }

        self.IV_RANK_MIN = 50
        self.IV_RANK_GOOD = 60
        self.IV_RANK_EXCELLENT = 75

        # LRU caches (bounded memory, automatic eviction)
        self._ticker_cache = LRUCache(max_size=500, ttl_minutes=cache_ttl_minutes)
        self._info_cache = LRUCache(max_size=1000, ttl_minutes=cache_ttl_minutes)
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)

        self.scorer = CompositeScorer()

        # Initialize Tradier (real IV) with fallback to yfinance (RV proxy)
        self.tradier_client = None
        try:
            self.tradier_client = TradierOptionsClient()
            if self.tradier_client.is_available():
                logger.info("Using Tradier API for real IV data")
            else:
                self.tradier_client = None
        except (ValueError, KeyError, OSError) as e:
            logger.info(f"Tradier client not configured: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error initializing Tradier: {e}", exc_info=True)

        self.options_client = None
        try:
            self.options_client = OptionsDataClient()
            logger.debug("yfinance fallback initialized")
        except (ValueError, KeyError) as e:
            logger.info(f"Options client not configured: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error initializing options client: {e}", exc_info=True)

    def pre_filter_tickers(
        self,
        tickers: List[str],
        min_market_cap: int = MIN_MARKET_CAP_DOLLARS,
        min_avg_volume: int = MIN_DAILY_VOLUME,
        use_batch: bool = True  # Use batch fetching (30-50% faster)
    ) -> List[str]:
        """
        Pre-filter tickers by basic criteria before expensive API calls.

        This SIGNIFICANTLY reduces API usage by filtering out:
        - Micro-cap stocks (< $500M market cap)
        - Low-volume stocks (< 100K shares/day)
        - Problematic tickers (API errors, missing data)

        PERFORMANCE: Uses batch fetching via yf.Tickers() for 30-50% speed improvement.
        Typical reduction: 265 tickers → ~50 tickers (80% reduction)

        Args:
            tickers: List of ticker symbols to pre-filter
            min_market_cap: Minimum market cap in dollars (default: $500M)
            min_avg_volume: Minimum average daily volume (default: 100K)
            use_batch: Use batch fetching (default: True, faster)

        Returns:
            Filtered list of tickers that meet basic criteria
        """
        logger.info(f"🔍 Pre-filtering {len(tickers)} tickers (market cap ≥ ${min_market_cap/1e6:.0f}M, volume ≥ {min_avg_volume:,})...")

        if not tickers:
            return []

        filtered = []

        if use_batch:
            # OPTIMIZED: Batch fetch all tickers at once (30-50% faster than individual calls)
            logger.debug(f"   Using batch fetch for {len(tickers)} tickers...")
            tickers_str = ' '.join(tickers)

            try:
                tickers_obj = yf.Tickers(tickers_str)

                for ticker in tickers:
                    try:
                        stock = tickers_obj.tickers.get(ticker)
                        if not stock:
                            logger.debug(f"  {ticker}: Filtered (not found in batch)")
                            continue

                        # Get info dict - needed for both market cap and volume
                        info = stock.info
                        # Ensure we get numeric values, never None (prevents TypeError in division)
                        market_cap = info.get('marketCap') or 0
                        avg_volume = info.get('averageVolume') or 0

                        # Check market cap
                        if market_cap < min_market_cap:
                            logger.debug(f"  {ticker}: Filtered (market cap ${market_cap/1e6:.0f}M < ${min_market_cap/1e6:.0f}M)")
                            continue

                        # Check average volume
                        if avg_volume < min_avg_volume:
                            logger.debug(f"  {ticker}: Filtered (volume {avg_volume:,} < {min_avg_volume:,})")
                            continue

                        # Cache the info dict to avoid re-fetching in get_ticker_data()
                        # This saves 75 duplicate API calls (one per ticker that passes pre-filter)
                        self._info_cache.set(ticker, info)
                        logger.debug(f"  {ticker}: ✓ Passed pre-filter (${market_cap/1e6:.0f}M, {avg_volume:,} vol) [cached info]")
                        filtered.append(ticker)

                    except Exception as e:
                        logger.debug(f"  {ticker}: Filtered (error: {e})")
                        continue

            except Exception as e:
                logger.warning(f"Batch fetch failed ({e}), falling back to individual fetches")
                # Fall back to individual fetching
                return self.pre_filter_tickers(tickers, min_market_cap, min_avg_volume, use_batch=False)

        else:
            # FALLBACK: Individual fetching (slower but more reliable for small batches)
            logger.debug(f"   Using individual fetch for {len(tickers)} tickers...")
            for ticker in tickers:
                try:
                    time.sleep(RATE_LIMIT_DELAY_SECONDS)  # Delay to avoid rate limits
                    stock = yf.Ticker(ticker)

                    # Get info dict - needed for both market cap and volume
                    info = stock.info
                    # Ensure we get numeric values, never None (prevents TypeError in division)
                    market_cap = info.get('marketCap') or 0
                    avg_volume = info.get('averageVolume') or 0

                    # Check market cap
                    if market_cap < min_market_cap:
                        logger.debug(f"  {ticker}: Filtered (market cap ${market_cap/1e6:.0f}M < ${min_market_cap/1e6:.0f}M)")
                        continue

                    # Check average volume
                    if avg_volume < min_avg_volume:
                        logger.debug(f"  {ticker}: Filtered (volume {avg_volume:,} < {min_avg_volume:,})")
                        continue

                    # Cache the info dict to avoid re-fetching in get_ticker_data()
                    self._info_cache.set(ticker, info)
                    logger.debug(f"  {ticker}: ✓ Passed pre-filter (${market_cap/1e6:.0f}M, {avg_volume:,} vol) [cached info]")
                    filtered.append(ticker)

                except Exception as e:
                    logger.debug(f"  {ticker}: Filtered (error: {e})")
                    continue

        logger.info(f"✅ Pre-filter: {len(filtered)}/{len(tickers)} tickers passed ({len(tickers) - len(filtered)} filtered out)")
        # Each filtered ticker saves ~3 expensive calls (5d history, 2y history, options analysis)
        # We already paid 1 info call per ticker, but that info is cached for reuse
        logger.info(f"   Saved {((len(tickers) - len(filtered)) * 3):.0f} expensive API calls (history + options analysis avoided)")

        return filtered

    def _calculate_iv_crush_ratio(self, options_data: Dict) -> None:
        """
        Calculate IV crush ratio in-place.

        IV crush ratio = expected_move_pct / avg_actual_move_pct
        Ratio > 1.0 means implied move > actual move (good for selling premium)

        Args:
            options_data: Options data dict to update in-place
        """
        expected = options_data.get('expected_move_pct')
        actual = options_data.get('avg_actual_move_pct')

        if expected and actual and expected > 0 and actual > 0:
            iv_crush_ratio = expected / actual
            options_data['iv_crush_ratio'] = round(iv_crush_ratio, 2)

    @retry_on_rate_limit(max_retries=3, initial_backoff=2.0, backoff_multiplier=2.0)
    def get_ticker_data(self, ticker: str, use_cache: bool = True) -> Optional[Dict]:
        """
        Get market data for a ticker including options data.

        Fetches yfinance data ONCE and extracts all needed information
        to avoid duplicate API calls. Uses TTL cache to prevent redundant requests.

        Decorated with @retry_on_rate_limit for automatic retry on rate limit errors.

        Args:
            ticker: Ticker symbol
            use_cache: Whether to use cache (default: True)

        Returns:
            Dict with market data and options_data or None if error
        """
        if use_cache:
            cached_data = self._ticker_cache.get(ticker)
            if cached_data is not None:
                logger.debug(f"{ticker}: Cache hit")
                return cached_data

        try:
            stock = yf.Ticker(ticker)

            # Reuse cached info from pre_filter to avoid duplicate API calls
            info = self._info_cache.get(ticker)
            if info is not None:
                logger.debug(f"{ticker}: Reusing cached info")
            else:
                info = stock.info
                logger.debug(f"{ticker}: Fetched fresh info")

            # Fetch 2y history if needed for earnings analysis, otherwise 5d
            if self.tradier_client and self.options_client:
                hist = stock.history(period='2y')
                logger.debug(f"{ticker}: Fetched 2y history")
            else:
                hist = stock.history(period='5d')
                logger.debug(f"{ticker}: Fetched 5d history")

            if hist.empty:
                logger.warning(f"No historical data for {ticker}")
                return None

            # Get current price
            current_price = info.get('currentPrice') or info.get('regularMarketPrice') or hist['Close'].iloc[-1]

            data = {
                'ticker': ticker,
                'market_cap': info.get('marketCap') or 0,
                'volume': hist['Volume'].iloc[-1] if len(hist) > 0 else 0,  # Stock volume
                'avg_volume': info.get('averageVolume') or 0,
                'price': current_price,
                'iv': info.get('impliedVolatility') or 0,
                'sector': info.get('sector', 'Unknown'),
                'beta': info.get('beta') or 1.0
            }

            options_data = {}

            # Try Tradier first (real IV), fallback to yfinance (RV proxy)
            if self.tradier_client:
                try:
                    logger.debug(f"{ticker}: Fetching from Tradier")
                    tradier_data = self.tradier_client.get_options_data(ticker, current_price)

                    if tradier_data:
                        options_data = tradier_data

                        # Supplement with yfinance historical earnings data
                        if self.options_client:
                            try:
                                yf_data = self.options_client.get_options_data_from_stock(
                                    stock, ticker, hist, current_price
                                )

                                options_data['avg_actual_move_pct'] = yf_data.get('avg_actual_move_pct')
                                options_data['last_earnings_move'] = yf_data.get('last_earnings_move')
                                options_data['earnings_beat_rate'] = yf_data.get('earnings_beat_rate')

                                # Use yfinance IV Rank if Tradier doesn't provide it
                                if options_data.get('iv_rank', 0) == 0 and yf_data.get('iv_rank', 0) > 0:
                                    options_data['iv_rank'] = yf_data['iv_rank']
                                    options_data['iv_rank_source'] = 'yfinance_rv_proxy'
                                    logger.debug(f"{ticker}: Using yfinance RV Rank proxy")

                            except Exception as e:
                                logger.warning(f"{ticker}: yfinance supplement failed: {e}")

                        self._calculate_iv_crush_ratio(options_data)
                        options_data['data_source'] = 'tradier'
                        logger.debug(f"{ticker}: Using Tradier (IV: {options_data.get('current_iv')}%)")

                except Exception as e:
                    logger.warning(f"{ticker}: Tradier failed: {e}")

            # Fallback to yfinance if Tradier didn't work
            if not options_data and self.options_client:
                try:
                    logger.debug(f"{ticker}: Using yfinance fallback")

                    # Reuse existing history if sufficient (252+ days for IV rank)
                    if hist.empty or len(hist) < 252:
                        hist = stock.history(period='1y')
                        logger.debug(f"{ticker}: Fetched 1y history")
                    else:
                        logger.debug(f"{ticker}: Reusing history ({len(hist)} days)")

                    options_data = self.options_client.get_options_data_from_stock(
                        stock, ticker, hist, current_price
                    )

                    # Calculate IV crush ratio
                    self._calculate_iv_crush_ratio(options_data)

                    options_data['data_source'] = 'yfinance_rv_proxy'

                except (KeyError, ValueError, AttributeError) as e:
                    # Data access errors - likely empty/malformed data
                    logger.warning(f"{ticker}: Options data error (empty/malformed history): {e}")
                except Exception as e:
                    # Catch-all for unexpected errors
                    logger.warning(f"{ticker}: Unexpected options data error: {e}")

            data['options_data'] = options_data

            # Update cache (LRU cache handles timestamp internally)
            if use_cache:
                self._ticker_cache.set(ticker, data)
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

        Scoring based on Trading Research Prompt.pdf and trading_criteria.yaml:
        - IV >= 60% OR IV Rank >= 50% required (uses actual IV from Tradier/options)
        - 60-80% IV is good, 80-100% excellent, 100%+ premium for IV crush trades
        - Historical implied move > actual move (IV overpricing edge)
        - Options liquidity (volume, OI, bid-ask spreads)

        Args:
            data: Ticker data dict (with optional options_data from Tradier)

        Returns:
            Score (0-100), or 0 if filtered out by hard filters
        """
        return self.scorer.calculate_score(data)

    def _process_single_ticker(self, ticker: str, add_delay: bool = True) -> Optional[Dict]:
        """
        Process a single ticker: fetch data and calculate score.

        Args:
            ticker: Ticker symbol
            add_delay: Add small delay to avoid rate limits (default: True)

        Returns:
            Dict with ticker data and score, or None if filtered out
        """
        try:
            # Add small delay to avoid rate limits (helps with parallel processing)
            if add_delay:
                time.sleep(RATE_LIMIT_DELAY_SECONDS)

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
        max_workers: int = 2,  # Reduced from 5 to avoid rate limits
        use_batch_prefetch: bool = True  # New: batch prefetch for speed
    ) -> List[Dict]:
        """
        Filter and score a list of tickers.

        FILTERS OUT:
        - Tickers with IV Rank < 50% (score = 0)
        - Tickers with liquidity below minimums (volume < 100, OI < 500)
        - Tickers with insufficient data

        PERFORMANCE: Optionally batch prefetches all ticker info to speed up parallel processing.

        Args:
            tickers: List of ticker symbols
            max_tickers: Max number to process (pass len(tickers) to score ALL)
            parallel: Use parallel processing (default: True)
            max_workers: Max parallel workers (default: 2)
            use_batch_prefetch: Batch prefetch ticker data (default: True, 20-30% faster)

        Returns:
            List of dicts with ticker data and scores, sorted by score descending
            Only returns tickers that pass all filters (score > 0)
        """
        # No longer need shuffle since we score ALL tickers and sort by score
        # This ensures deterministic, reproducible results
        tickers_to_process = tickers[:max_tickers]
        results = []

        # OPTIMIZATION: Batch prefetch ticker info if not already cached
        # This is especially beneficial when pre_filter wasn't run
        if use_batch_prefetch and len(tickers_to_process) > BATCH_PREFETCH_THRESHOLD:
            # LRU cache handles TTL internally, just check if ticker is in cache
            uncached_tickers = [
                t for t in tickers_to_process
                if t not in self._info_cache
            ]

            if uncached_tickers:
                logger.debug(f"   Batch prefetching {len(uncached_tickers)} uncached tickers...")
                try:
                    tickers_str = ' '.join(uncached_tickers)
                    tickers_obj = yf.Tickers(tickers_str)

                    for ticker in uncached_tickers:
                        try:
                            stock = tickers_obj.tickers.get(ticker)
                            if stock:
                                info = stock.info
                                self._info_cache.set(ticker, info)
                        except Exception as e:
                            logger.debug(f"  {ticker}: Prefetch failed: {e}")
                except Exception as e:
                    logger.debug(f"Batch prefetch failed ({e}), continuing without prefetch")

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
                logger.info(f"   Stock Volume: {ticker_data['volume']:,} (Avg: {ticker_data['avg_volume']:,})")
                logger.info(f"   Price: ${ticker_data['price']:.2f}")

        if selected['after_hours']:
            logger.info("\nAFTER-HOURS (Top 3):")
            for i, ticker_data in enumerate(selected['after_hours'], 1):
                logger.info(f"{i}. {ticker_data['ticker']:6s} - Score: {ticker_data['score']:5.1f}")
                logger.info(f"   Market Cap: ${ticker_data['market_cap']/1e9:.1f}B")
                logger.info(f"   Stock Volume: {ticker_data['volume']:,} (Avg: {ticker_data['avg_volume']:,})")
                logger.info(f"   Price: ${ticker_data['price']:.2f}")

        logger.info("")
        logger.info('='*70)
        logger.info(f"\n✅ Selected {len(selected['pre_market']) + len(selected['after_hours'])} tickers for analysis")
        logger.info("   (2 pre-market + 3 after-hours = 5 total per day)")
        logger.info('='*70)

        break  # Just show first day for demo
