"""
Earnings analyzer - orchestrates IV crush trading strategy research.

Workflow:
    1. Filter tickers by IV metrics and liquidity
    2. Analyze sentiment (retail/institutional/hedge fund)
    3. Generate 3-4 trade strategies with position sizing

Usage:
    python -m src.analysis.earnings_analyzer --tickers "AAPL,MSFT" --yes

Output: Research report ready for manual execution
"""

# Standard library imports
import glob
import logging
import os
import sys
from datetime import datetime, timedelta
from multiprocessing import Pool, cpu_count
from typing import Dict, List, Optional, Tuple, Any

# Third-party imports
import pytz
import yaml

# Local application imports
from src.ai.sentiment_analyzer import SentimentAnalyzer
from src.ai.strategy_generator import StrategyGenerator
from src.analysis.formatters.csv_formatter import CSVFormatter
from src.analysis.formatters.json_formatter import JSONFormatter
from src.analysis.report_formatter import ReportFormatter
from src.analysis.ticker_data_fetcher import TickerDataFetcher
from src.analysis.ticker_filter import TickerFilter
from src.core.input_validator import InputValidator
from src.core.startup_validator import StartupValidator
from src.core.timezone_utils import get_eastern_now, get_market_date
from src.core.types import TickerData, AnalysisResult, SentimentData, StrategyData
from src.data.calendars.factory import EarningsCalendarFactory

logger = logging.getLogger(__name__)

# Constants for analysis configuration
ANALYSIS_TIMEOUT_PER_TICKER = 120  # Generous timeout for AI + API calls
MAX_PARALLEL_WORKERS = 4  # Maximum workers for multiprocessing
MULTIPROCESSING_THRESHOLD = 3  # Use sequential processing for fewer than 3 tickers


def _analyze_single_ticker(args: Tuple[str, TickerData, str, bool, str]) -> AnalysisResult:
    """
    Standalone function for multiprocessing - analyzes a single ticker.

    Accepts shared config_path to ensure all workers use same SQLite DB
    for budget tracking.

    Args:
        args: Tuple of (ticker, ticker_data, earnings_date, override_daily_limit, config_path)

    Returns:
        Complete analysis dict
    """
    ticker, ticker_data, earnings_date, override_daily_limit, config_path = args

    try:
        logger.info(f"📊 {ticker}: Starting analysis (Score: {ticker_data.get('score', 0):.1f}/100, IV: {ticker_data.get('options_data', {}).get('current_iv', 'N/A')}%)")

        # Use shared UsageTracker via config path
        from src.core.usage_tracker import UsageTracker
        shared_tracker = UsageTracker(config_path=config_path)

        # Initialize clients with shared tracker
        sentiment_analyzer = SentimentAnalyzer(usage_tracker=shared_tracker)
        strategy_generator = StrategyGenerator(usage_tracker=shared_tracker)

        analysis = {
            'ticker': ticker,
            'earnings_date': earnings_date,
            'price': ticker_data.get('price', 0),
            'score': ticker_data.get('score', 0),
            'options_data': ticker_data.get('options_data', {}),
            'sentiment': {},
            'strategies': []
        }

        # Get sentiment analysis
        try:
            logger.info(f"  {ticker}: Fetching sentiment...")
            sentiment = sentiment_analyzer.analyze_earnings_sentiment(ticker, earnings_date, override_daily_limit)
            analysis['sentiment'] = sentiment
        except Exception as e:
            error_msg = str(e)
            if "DAILY_LIMIT" in error_msg or "budget" in error_msg.lower() or "limit" in error_msg.lower():
                logger.warning(f"  {ticker}: API limit reached for sentiment")
                analysis['sentiment'] = {
                    'overall_sentiment': 'unavailable',
                    'error': 'API limit exceeded',
                    'note': 'Daily/monthly API limits reached for all providers. Run again tomorrow or use --override flag.'
                }
            else:
                logger.error(f"  {ticker}: Sentiment analysis failed: {e}")
                analysis['sentiment'] = {
                    'overall_sentiment': 'unavailable',
                    'error': str(e)[:100],
                    'note': 'Sentiment analysis failed - see logs for details'
                }

        # Generate strategies - try even if sentiment failed (don't let sentiment block strategies)
        if analysis['options_data']:
            try:
                logger.info(f"  {ticker}: Generating strategies...")
                strategies = strategy_generator.generate_strategies(
                    ticker,
                    analysis['options_data'],
                    analysis['sentiment'],
                    ticker_data,
                    override_daily_limit
                )
                analysis['strategies'] = strategies
            except Exception as e:
                error_msg = str(e)
                if "DAILY_LIMIT" in error_msg or "budget" in error_msg.lower() or "limit" in error_msg.lower():
                    logger.warning(f"  {ticker}: API limit reached for strategies")
                    analysis['strategies'] = {
                        'strategies': [],
                        'error': 'API limit exceeded',
                        'note': 'Daily/monthly API limits reached for all providers. Run again tomorrow or use --override flag.'
                    }
                else:
                    logger.error(f"  {ticker}: Strategy generation failed: {e}")
                    analysis['strategies'] = {
                        'strategies': [],
                        'error': str(e)[:100],
                        'note': 'Strategy generation failed - see logs for details'
                    }

        logger.info(f"✅ {ticker}: Analysis complete")
        return analysis

    except Exception as e:
        logger.error(f"{ticker}: Full analysis failed: {e}")
        return {
            'ticker': ticker,
            'earnings_date': earnings_date,
            'price': ticker_data.get('price', 0),
            'score': ticker_data.get('score', 0),
            'options_data': {},
            'sentiment': {},
            'strategies': [],
            'error': str(e)
        }


class EarningsAnalyzer:
    """
    Orchestrates earnings trade research for IV crush strategies.

    Optimized for 1-2 day pre-earnings entries with IV expansion detection.

    Workflow:
        1. Get upcoming earnings from calendar
        2. Filter by IV expansion (>40% weekly) & absolute IV (>60%)
        3. Score based on expansion velocity, liquidity, and crush edge
        4. Analyze sentiment (retail/institutional)
        5. Generate trade strategies with position sizing
        6. Output formatted research report
    """

    # Validation methods delegated to InputValidator
    # (kept as static methods for backward compatibility)

    @staticmethod
    def validate_ticker(ticker: str) -> str:
        """Validate ticker symbol format. Delegates to InputValidator."""
        return InputValidator.validate_ticker(ticker)

    @staticmethod
    def validate_date(date_str: Optional[str]) -> Optional[str]:
        """Validate date format (YYYY-MM-DD). Delegates to InputValidator."""
        return InputValidator.validate_date(date_str)

    @staticmethod
    def validate_max_analyze(max_analyze: int) -> int:
        """Validate max_analyze parameter. Delegates to InputValidator."""
        return InputValidator.validate_max_analyze(max_analyze)

    def __init__(
        self,
        earnings_calendar: Optional[Any] = None,
        ticker_filter: Optional[TickerFilter] = None,
        earnings_source: Optional[str] = None
    ) -> None:
        """
        Initialize earnings analyzer components.

        Args:
            earnings_calendar: Calendar instance (for dependency injection/testing)
            ticker_filter: TickerFilter instance (for dependency injection/testing)
            earnings_source: Calendar source ('nasdaq' or 'alphavantage'), defaults to config
        """
        logger.info("Initializing Earnings Analyzer...")

        # Load calendar source from config if not provided
        if earnings_calendar is None:
            if earnings_source is None:
                # Load from config
                config_path = 'config/budget.yaml'
                try:
                    with open(config_path, 'r') as f:
                        config = yaml.safe_load(f)
                        earnings_source = config.get('earnings_source', 'alphavantage')
                except (FileNotFoundError, yaml.YAMLError) as e:
                    logger.warning(f"Could not load config, using default: {e}")
                    earnings_source = 'alphavantage'

            # Create calendar using factory
            self.earnings_calendar = EarningsCalendarFactory.create(earnings_source)
        else:
            self.earnings_calendar = earnings_calendar

        self.ticker_filter = ticker_filter or TickerFilter()
        self.ticker_data_fetcher = TickerDataFetcher(self.ticker_filter)

        # Note: Sentiment analyzer and strategy generator are initialized
        # in worker processes for thread-safe parallel processing

    def _validate_earnings_date(self, earnings_date: Optional[str] = None) -> str:
        """
        Validate and normalize earnings date.

        Args:
            earnings_date: Earnings date string (YYYY-MM-DD) or None

        Returns:
            Validated earnings date string (YYYY-MM-DD)

        Raises:
            ValueError: If date format is invalid
        """
        if earnings_date is not None:
            try:
                # Validate format YYYY-MM-DD
                parsed_date = datetime.strptime(earnings_date, '%Y-%m-%d')

                # Use Eastern timezone for market date comparison
                now_et = get_eastern_now()

                # Make parsed_date timezone-aware to allow comparison
                from src.core.timezone_utils import EASTERN
                parsed_date_aware = EASTERN.localize(parsed_date)

                # Warn if date is in the past
                if parsed_date_aware.date() < now_et.date():
                    logger.warning(f"Earnings date {earnings_date} is in the past")

                # Warn if date is too far in future (>90 days)
                days_out = (parsed_date_aware - now_et).days
                if days_out > 90:
                    logger.warning(f"Earnings date {earnings_date} is {days_out} days out - options may not exist")

                return earnings_date

            except ValueError as e:
                logger.error(f"Invalid earnings date format: {earnings_date}. Expected YYYY-MM-DD")
                raise ValueError(f"Invalid earnings date format: {earnings_date}. Expected YYYY-MM-DD") from e
        else:
            # Default to next trading day in Eastern time
            default_date = (get_eastern_now() + timedelta(days=1)).strftime('%Y-%m-%d')
            logger.info(f"No earnings date provided, using next trading day: {default_date}")
            return default_date

    def _validate_tradier_client(self, tickers: List[str], earnings_date: str) -> Optional[Dict]:
        """
        Validate that Tradier client is available.

        Args:
            tickers: List of tickers (for error reporting)
            earnings_date: Earnings date (for error reporting)

        Returns:
            Error response dict if validation fails, None if successful
        """
        if not self.ticker_filter.tradier_client or not self.ticker_filter.tradier_client.is_available():
            logger.error("Tradier client not available - cannot analyze in ticker list mode")
            logger.error("Set TRADIER_ACCESS_TOKEN in .env to use ticker list mode")
            return {
                'date': earnings_date,
                'analyzed_count': 0,
                'failed_count': len(tickers),
                'ticker_analyses': [],
                'failed_analyses': [{'ticker': t, 'error': 'Tradier API not available'} for t in tickers]
            }
        return None

    def _fetch_tickers_data(self, tickers: List[str], earnings_date: str) -> Tuple[List[TickerData], List[str]]:
        """
        Fetch basic ticker data from yfinance and options data from Tradier.

        Delegates to TickerDataFetcher for modular data fetching.

        Args:
            tickers: List of ticker symbols
            earnings_date: Earnings date for options selection

        Returns:
            Tuple of (tickers_data, failed_tickers)
            - tickers_data: List of dicts with ticker info and options data
            - failed_tickers: List of tickers that failed to fetch
        """
        return self.ticker_data_fetcher.fetch_tickers_data(tickers, earnings_date)

    def _run_parallel_analysis(
        self,
        tickers_data: List[TickerData],
        earnings_date: str,
        override_daily_limit: bool
    ) -> List[AnalysisResult]:
        """
        Run analysis on tickers (parallel for 3+ tickers, sequential for 1-2).

        OPTIMIZATION: Multiprocessing has overhead (process creation, initialization).
        For 1-2 tickers, sequential processing is actually faster.
        For 3+ tickers, parallel processing provides 2-3x speedup.

        Args:
            tickers_data: List of ticker data dicts
            earnings_date: Earnings date
            override_daily_limit: Whether to bypass daily API limits

        Returns:
            List of analysis results (may include errors)
        """
        logger.info(f"Running full analysis on {len(tickers_data)} tickers...")

        # Pass shared config path for budget tracking across workers
        config_path = "config/budget.yaml"

        # Prepare arguments for parallel processing
        analysis_args = [
            (td['ticker'], td, earnings_date, override_daily_limit, config_path)
            for td in tickers_data
        ]

        # OPTIMIZATION: Skip multiprocessing for small batches (overhead > benefit)
        if len(tickers_data) < MULTIPROCESSING_THRESHOLD:
            logger.info(f"Using sequential processing for {len(tickers_data)} ticker(s) (faster than multiprocessing overhead)")
            ticker_analyses = [_analyze_single_ticker(args) for args in analysis_args]
            return ticker_analyses

        # Use multiprocessing for 3+ tickers (significant speedup)
        num_workers = min(cpu_count(), len(tickers_data), MAX_PARALLEL_WORKERS)
        logger.info(f"Using {num_workers} parallel workers")

        timeout = ANALYSIS_TIMEOUT_PER_TICKER * len(tickers_data)
        logger.debug(f"Pool timeout set to {timeout}s ({ANALYSIS_TIMEOUT_PER_TICKER}s per ticker)")

        try:
            with Pool(processes=num_workers) as pool:
                result = pool.map_async(_analyze_single_ticker, analysis_args)
                ticker_analyses = result.get(timeout=timeout)
        except TimeoutError:
            logger.error(f"Pool operation timed out after {timeout}s - some workers may be hung")
            logger.error("Try reducing the number of tickers or increase ANALYSIS_TIMEOUT_PER_TICKER")
            ticker_analyses = []
        except KeyboardInterrupt:
            logger.warning("Analysis interrupted by user (Ctrl+C)")
            pool.terminate()
            pool.join()
            raise
        except Exception as e:
            logger.error(f"Pool operation failed unexpectedly: {e}", exc_info=True)
            ticker_analyses = []

        return ticker_analyses

    def _process_analysis_results(self, ticker_analyses: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """
        Separate successful and failed analyses.

        Args:
            ticker_analyses: List of analysis results from parallel processing

        Returns:
            Tuple of (successful_analyses, failed_analyses)
        """
        successful_analyses = []
        failed_analyses = []

        for analysis in ticker_analyses:
            if analysis.get('error'):
                failed_analyses.append(analysis)
                logger.warning(f"❌ {analysis['ticker']}: {analysis['error']}")
            else:
                successful_analyses.append(analysis)

        return successful_analyses, failed_analyses

    def analyze_specific_tickers(self, tickers: list, earnings_date: str = None, override_daily_limit: bool = False) -> Dict:
        """
        Analyze specific tickers directly (bypass calendar scanning).

        Useful when you have specific tickers in mind for earnings plays.

        Args:
            tickers: List of ticker symbols (e.g., ['META', 'MSFT', 'GOOGL'])
            earnings_date: Expected earnings date (YYYY-MM-DD), defaults to next trading day
            override_daily_limit: If True, bypass daily API call limits (but still respect hard caps)

        Returns:
            Dict with:
            - date: Earnings date used
            - analyzed_count: Number of tickers analyzed
            - ticker_analyses: List of full analyses
            - failed_analyses: List of failed tickers
        """
        # Step 1: Validate earnings date
        earnings_date = self._validate_earnings_date(earnings_date)

        logger.info(f"Analyzing {len(tickers)} specific tickers for {earnings_date}")

        # Step 2: Validate Tradier client is available
        error_response = self._validate_tradier_client(tickers, earnings_date)
        if error_response:
            return error_response

        # Step 3: Fetch ticker data
        tickers_data, failed_tickers = self._fetch_tickers_data(tickers, earnings_date)

        if not tickers_data:
            logger.warning(f"No valid tickers to analyze. Failed: {', '.join(failed_tickers) if failed_tickers else 'none'}")
            return {
                'date': earnings_date,
                'analyzed_count': 0,
                'failed_count': len(failed_tickers),
                'ticker_analyses': [],
                'failed_analyses': [{'ticker': t, 'error': 'Data fetch failed'} for t in failed_tickers]
            }

        # Step 4: Filter out tickers that failed IV filter (score=0)
        filtered_tickers = [td for td in tickers_data if td.get('score', 0) > 0]
        filtered_out = [td for td in tickers_data if td.get('score', 0) == 0]

        if filtered_out:
            logger.warning(f"❌ {len(filtered_out)} ticker(s) filtered out (IV too low or insufficient liquidity): {', '.join([td['ticker'] for td in filtered_out])}")
            failed_tickers.extend([td['ticker'] for td in filtered_out])

        if not filtered_tickers:
            logger.warning("❌ No tickers passed IV filter (IV >= 60% required)")
            logger.info("💡 Try tickers with higher IV or adjust filter thresholds in config/trading_criteria.yaml")
            return {
                'date': earnings_date,
                'analyzed_count': 0,
                'failed_count': len(failed_tickers),
                'ticker_analyses': [],
                'failed_analyses': [{'ticker': t, 'error': 'Failed IV filter (IV < 60% and IV Rank < 50%)' if t in [td['ticker'] for td in filtered_out] else 'Data fetch failed'} for t in failed_tickers]
            }

        logger.info(f"✅ {len(filtered_tickers)}/{len(tickers_data)} ticker(s) passed IV filter")

        # Step 5: Run parallel analysis
        ticker_analyses = self._run_parallel_analysis(filtered_tickers, earnings_date, override_daily_limit)

        # Step 6: Process results
        successful_analyses, failed_analyses = self._process_analysis_results(ticker_analyses)

        analyzed_count = len(successful_analyses)
        failed_count = len(failed_analyses)

        logger.info(f"Successfully analyzed {analyzed_count}/{len(tickers)} tickers")
        if failed_count > 0:
            logger.warning(f"Failed to analyze {failed_count} tickers - see warnings above for details")

        return {
            'date': earnings_date,
            'analyzed_count': analyzed_count,
            'failed_count': failed_count,
            'ticker_analyses': successful_analyses,
            'failed_analyses': failed_analyses
        }

    def analyze_daily_earnings(self, target_date: str = None, max_analyze: int = 2, override_daily_limit: bool = False) -> Dict:
        """
        Analyze earnings for a specific day and generate trade ideas.

        Args:
            target_date: Date to analyze (YYYY-MM-DD), defaults to today
            max_analyze: Maximum number of tickers to fully analyze (costs $$$)
            override_daily_limit: If True, bypass daily API call limits (but still respect hard caps)

        Returns:
            Dict with:
            - date: Analysis date
            - total_earnings: Total companies reporting
            - filtered_count: Companies passing IV filters (expansion + level)
            - analyzed_count: Companies fully analyzed
            - ticker_analyses: List of full analyses for top tickers
        """
        if target_date is None:
            # Use Eastern timezone for market date
            target_date = get_market_date()

        logger.info(f"Analyzing earnings for {target_date}...")

        # Step 1: Get earnings for the date (with filtering for already-reported)
        week_earnings = self.earnings_calendar.get_week_earnings(days=7)

        if target_date not in week_earnings:
            logger.warning(f"No earnings found for {target_date}")
            return {
                'date': target_date,
                'total_earnings': 0,
                'filtered_count': 0,
                'analyzed_count': 0,
                'failed_count': 0,
                'ticker_analyses': [],
                'failed_analyses': [],
                'error': 'No earnings found'
            }

        # Filter out already-reported earnings using timezone utility
        now_et = get_eastern_now()

        earnings_list_unfiltered = week_earnings[target_date]
        earnings_list = [
            earning for earning in earnings_list_unfiltered
            if not self.earnings_calendar._is_already_reported(earning, now_et)
        ]

        filtered_count = len(earnings_list_unfiltered) - len(earnings_list)
        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} already-reported earnings")

        total_count = len(earnings_list)

        logger.info(f"Found {total_count} companies reporting on {target_date}")

        # Step 2: Extract all tickers
        all_tickers = [earning.get('ticker', '') for earning in earnings_list if earning.get('ticker')]

        # Step 2.5: Pre-filter by market cap and volume (HUGE API savings!)
        # This eliminates penny stocks and low-volume tickers BEFORE expensive API calls
        # Typical reduction: 265 → ~50 tickers (80% reduction)
        try:
            pre_filtered_tickers = self.ticker_filter.pre_filter_tickers(
                all_tickers,
                min_market_cap=500_000_000,  # $500M minimum
                min_avg_volume=100_000       # 100K shares/day minimum
            )
        except Exception as e:
            logger.error(f"Pre-filter failed: {e}")
            pre_filtered_tickers = None

        # Validate pre-filter results
        if pre_filtered_tickers is None or not isinstance(pre_filtered_tickers, list):
            logger.error("❌ Pre-filter returned invalid data (None or not a list)")
            logger.warning("Falling back to processing all tickers (this may be slow!)")
            pre_filtered_tickers = all_tickers  # Fallback to all tickers

        if not pre_filtered_tickers:
            logger.warning("❌ No tickers passed pre-filter (market cap/volume requirements)")
            return {
                'date': target_date,
                'total_earnings': total_count,
                'filtered_count': 0,
                'analyzed_count': 0,
                'ticker_analyses': []
            }

        logger.info(f"📊 Scoring {len(pre_filtered_tickers)} pre-filtered tickers by options data (IV, liquidity, etc.)...")
        logger.info("This will identify the best candidates before expensive AI analysis")

        # Score pre-filtered tickers with options data
        # This ensures we get the BEST tickers by score from viable candidates
        filtered_tickers = self.ticker_filter.filter_and_score_tickers(
            pre_filtered_tickers,
            max_tickers=len(pre_filtered_tickers)  # Process all pre-filtered tickers
        )

        filtered_count = len(filtered_tickers)

        logger.info(f"✅ Scored {len(pre_filtered_tickers)} tickers, {filtered_count} passed IV filter (>50%)")

        if filtered_count > 0:
            logger.info(f"🏆 Top scorer: {filtered_tickers[0]['ticker']} (Score: {filtered_tickers[0]['score']:.1f}/100)")
            if filtered_count > 1:
                logger.info(f"   Runner-up: {filtered_tickers[1]['ticker']} (Score: {filtered_tickers[1]['score']:.1f}/100)")

        # Step 3: Select top N tickers by score for expensive AI analysis
        tickers_to_analyze = filtered_tickers[:max_analyze]

        if not tickers_to_analyze:
            return {
                'date': target_date,
                'total_earnings': total_count,
                'filtered_count': filtered_count,
                'analyzed_count': 0,
                'ticker_analyses': []
            }

        logger.info(f"🤖 Running AI analysis on top {len(tickers_to_analyze)} scorer(s)...")
        logger.info(f"   Selected: {', '.join([td['ticker'] for td in tickers_to_analyze])}")

        # Run analysis (uses smart parallelization: sequential for 1-2, parallel for 3+)
        ticker_analyses = self._run_parallel_analysis(tickers_to_analyze, target_date, override_daily_limit)

        # Separate successful and failed analyses
        successful_analyses, failed_analyses = self._process_analysis_results(ticker_analyses)

        analyzed_count = len(successful_analyses)
        failed_count = len(failed_analyses)

        logger.info(f"Successfully analyzed {analyzed_count}/{len(tickers_to_analyze)} tickers")
        if failed_count > 0:
            logger.warning(f"Failed to analyze {failed_count} tickers - see warnings above for details")

        return {
            'date': target_date,
            'total_earnings': total_count,
            'filtered_count': filtered_count,
            'analyzed_count': analyzed_count,
            'failed_count': failed_count,
            'ticker_analyses': successful_analyses,
            'failed_analyses': failed_analyses
        }

    def generate_report(self, analysis_result: Dict, format: str = 'text') -> str:
        """
        Generate formatted research report.

        Args:
            analysis_result: Result from analyze_daily_earnings() or analyze_specific_tickers()
            format: Output format - 'text', 'json', or 'csv' (default: 'text')

        Returns:
            Formatted report in specified format
        """
        if format == 'json':
            return JSONFormatter.format(analysis_result)
        elif format == 'csv':
            return CSVFormatter.format(analysis_result)
        else:  # default to text
            return ReportFormatter.format_analysis_report(analysis_result)

    @staticmethod
    def cleanup_old_reports(days: int = 15) -> None:
        """
        Delete report files older than specified number of days.

        Args:
            days: Number of days to keep reports (default: 15)
        """
        try:
            data_dir = "data"
            if not os.path.exists(data_dir):
                return

            # Find all earnings analysis report files
            pattern = os.path.join(data_dir, "earnings_analysis_*.txt")
            report_files = glob.glob(pattern)

            if not report_files:
                return

            # Calculate cutoff date
            cutoff_date = datetime.now() - timedelta(days=days)
            deleted_count = 0

            for file_path in report_files:
                try:
                    # Get file modification time
                    file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))

                    # Delete if older than cutoff
                    if file_mtime < cutoff_date:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.debug(f"Deleted old report: {os.path.basename(file_path)} (age: {(datetime.now() - file_mtime).days} days)")

                except Exception as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")

            if deleted_count > 0:
                logger.info(f"🗑️  Cleaned up {deleted_count} old report(s) (>{days} days)")

        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")


# CLI
if __name__ == "__main__":
    # Fix multiprocessing RuntimeWarning on macOS with 'python -m'
    # Set start method to 'spawn' to avoid module import issues
    try:
        from multiprocessing import set_start_method
        set_start_method('spawn', force=True)
    except RuntimeError:
        # Already set, ignore
        pass

    # Setup logging for CLI execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Check for help flag first
    if '-h' in sys.argv or '--help' in sys.argv:
        print("""
EARNINGS TRADE ANALYZER - Automated Research System

USAGE:
  Mode 1 - Specific Tickers (Recommended):
    python -m src.analysis.earnings_analyzer --tickers "TICK1,TICK2,..." [DATE] [OPTIONS]

  Mode 2 - Calendar Scan:
    python -m src.analysis.earnings_analyzer [DATE] [MAX_TICKERS] [OPTIONS]

ARGUMENTS:
  --tickers TICKERS     Comma-separated list of tickers (e.g., "NVDA,META,GOOGL")
  DATE                  Earnings date in YYYY-MM-DD format (default: today)
  MAX_TICKERS           Max number of tickers to analyze in calendar mode (default: 2)

OPTIONS:
  -h, --help            Show this help message and exit
  -y, --yes             Skip confirmation prompt
  --override            Bypass daily API limits (hard caps still enforced)
  --format FORMAT       Output format: text, json, or csv (default: text)

EXAMPLES:
  # Analyze specific tickers with confirmation
  python -m src.analysis.earnings_analyzer --tickers "NVDA,META" 2025-11-05

  # Analyze specific tickers, skip confirmation
  python -m src.analysis.earnings_analyzer --tickers "NVDA,META,GOOGL" 2025-11-05 --yes

  # Scan calendar for Nov 5, analyze top 5 tickers
  python -m src.analysis.earnings_analyzer 2025-11-05 5 --yes

  # Use override mode to bypass daily limits
  python -m src.analysis.earnings_analyzer --tickers "NVDA,META" 2025-11-05 --yes --override

  # Export as JSON for automation
  python -m src.analysis.earnings_analyzer --tickers "NVDA" 2025-11-05 --yes --format json > output.json

  # Export as CSV for Excel
  python -m src.analysis.earnings_analyzer --tickers "NVDA,META" 2025-11-05 --yes --format csv > output.csv

MODES:
  Ticker List Mode:
    - Analyzes only the specified tickers
    - Recommended for targeted analysis
    - Use when you have a watchlist

  Calendar Scan Mode:
    - Scans earnings calendar for specified date
    - Filters by IV metrics (60%+ minimum)
    - Analyzes top N tickers by score
    - Use for discovering new opportunities

OUTPUT:
  - Generates timestamped report in data/ directory
  - Shows IV metrics, sentiment, and 3-4 option strategies
  - Report saved to: data/earnings_analysis_DATE_HHMMSS.txt

COST:
  - ~$0.01 per ticker analyzed
  - Uses Perplexity Sonar Pro until daily/monthly limits
  - Auto-fallback to free Google Gemini when limits reached
  - Override mode bypasses daily limits but respects hard caps

For more information, see README.md
""")
        exit(0)

    logger.info("")
    logger.info('='*80)
    logger.info('EARNINGS TRADE ANALYZER - AUTOMATED RESEARCH SYSTEM')
    logger.info('='*80)
    logger.info("")

    # Validate environment before proceeding
    logger.info("Validating environment...")
    if not StartupValidator.check_and_exit_on_errors(mode='full', strict=True):
        logger.error("\n❌ Startup validation failed. Fix the errors above and try again.")
        logger.info("See README.md for setup instructions.")
        exit(1)
    logger.info("")

    # Parse arguments
    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv
    override_daily_limit = '--override' in sys.argv

    # Parse output format
    output_format = 'text'  # default
    if '--format' in sys.argv:
        idx = sys.argv.index('--format')
        if idx + 1 < len(sys.argv):
            output_format = sys.argv[idx + 1].lower()
            if output_format not in ['text', 'json', 'csv']:
                logger.error(f"❌ Invalid format: {output_format}")
                logger.info("💡 Valid formats: text, json, csv")
                exit(1)

    # Check for ticker list mode
    ticker_list = None
    if '--tickers' in sys.argv:
        idx = sys.argv.index('--tickers')
        if idx + 1 < len(sys.argv):
            raw_tickers = sys.argv[idx + 1].upper().replace(' ', '').split(',')
            # Validate each ticker
            try:
                ticker_list = [EarningsAnalyzer.validate_ticker(t) for t in raw_tickers]
            except ValueError as e:
                logger.error(f"❌ {e}")
                logger.info("💡 Example: python -m src.analysis.earnings_analyzer --tickers \"AAPL,MSFT,GOOGL\"")
                exit(1)

    # Remove flags from args
    flags_to_remove = ['--yes', '-y', '--tickers', '--override', '--format']
    args = []
    skip_next = False
    for i, arg in enumerate(sys.argv[1:]):
        if skip_next:
            skip_next = False
            continue
        if arg in flags_to_remove:
            skip_next = True  # Skip the next arg if it's a flag value
            continue
        if ticker_list and arg.upper().replace(' ', '') == ','.join(ticker_list):
            continue
        if arg == output_format and i > 0 and sys.argv[i] == '--format':
            continue
        args.append(arg)

    analyzer = EarningsAnalyzer()

    # TICKER LIST MODE
    if ticker_list:
        earnings_date = args[0] if len(args) > 0 else None

        # Validate date format
        try:
            earnings_date = EarningsAnalyzer.validate_date(earnings_date)
        except ValueError as e:
            logger.error(f"❌ {e}")
            logger.info("💡 Example: python -m src.analysis.earnings_analyzer --tickers \"NVDA\" 2025-11-08")
            exit(1)

        logger.info(f"\n📋 TICKER LIST MODE")
        logger.info(f"Tickers: {', '.join(ticker_list)}")
        logger.info(f"Earnings Date: {earnings_date or 'next trading day (default)'}")
        if override_daily_limit:
            logger.warning("⚠️  OVERRIDE MODE: Daily limits bypassed (hard caps still enforced)")
        logger.warning(f"This will make API calls (estimated cost: ${0.05 * len(ticker_list):.2f})")
        logger.info("")

        if not skip_confirm:
            confirmation = input("Continue? (y/n): ")
            if confirmation.lower() != 'y':
                logger.info("Aborted.")
                exit()
        else:
            logger.info("Auto-confirmed with --yes flag")

        # Run ticker list analysis
        result = analyzer.analyze_specific_tickers(ticker_list, earnings_date, override_daily_limit)

    # CALENDAR SCANNING MODE (default)
    else:
        target_date = args[0] if len(args) > 0 else None

        # Parse and validate max_analyze
        try:
            max_analyze = int(args[1]) if len(args) > 1 else 2
            max_analyze = EarningsAnalyzer.validate_max_analyze(max_analyze)
        except ValueError as e:
            logger.error(f"❌ {e}")
            logger.info("💡 max_analyze must be a positive integer (e.g., 2, 5, 10)")
            exit(1)

        # Validate date
        if target_date is None:
            # Use Eastern timezone for market date
            target_date = get_market_date()
            logger.info(f"No date specified, using today: {target_date}")
        else:
            try:
                target_date = EarningsAnalyzer.validate_date(target_date)
            except ValueError as e:
                logger.error(f"❌ {e}")
                logger.info("💡 Example: python -m src.analysis.earnings_analyzer 2025-11-08 5")
                exit(1)

        logger.info(f"\nAnalyzing up to {max_analyze} tickers for {target_date}")
        if override_daily_limit:
            logger.warning("⚠️  OVERRIDE MODE: Daily limits bypassed (hard caps still enforced)")
        logger.warning(f"This will make API calls (estimated cost: ${0.05 * max_analyze:.2f})")
        logger.info("")

        if not skip_confirm:
            confirmation = input("Continue? (y/n): ")
            if confirmation.lower() != 'y':
                logger.info("Aborted.")
                exit()
        else:
            logger.info("Auto-confirmed with --yes flag")

        # Run calendar-based analysis
        result = analyzer.analyze_daily_earnings(target_date, max_analyze, override_daily_limit)

    # Generate and display report
    report = analyzer.generate_report(result, format=output_format)

    # Only display to console for text format
    if output_format == 'text':
        logger.info("\n\n")
        logger.info(report)

    # Save to file with timestamp to avoid overwriting
    timestamp = datetime.now().strftime('%H%M%S')
    file_extension = {'text': 'txt', 'json': 'json', 'csv': 'csv'}[output_format]
    output_file = f"data/earnings_analysis_{result['date']}_{timestamp}.{file_extension}"
    with open(output_file, 'w') as f:
        f.write(report)

    logger.info(f"\n\nReport saved to: {output_file}")

    # Cleanup old reports (>15 days) as final step
    EarningsAnalyzer.cleanup_old_reports(days=15)
