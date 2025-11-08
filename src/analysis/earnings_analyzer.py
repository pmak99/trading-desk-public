"""
Main earnings analyzer - orchestrates all Phase 2 components.

This is the master module that:
1. Filters tickers by IV crush criteria (IV Rank, implied vs actual moves)
2. Analyzes sentiment (retail/institutional/hedge fund)
3. Generates 3-4 trade strategies with sizing

Usage:
    python -m src.earnings_analyzer

Output: Complete research report for manual trade execution on Fidelity
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import sys
from multiprocessing import Pool, cpu_count
import pytz
import os
import glob
import yaml

from src.data.calendars.factory import EarningsCalendarFactory
from src.analysis.ticker_filter import TickerFilter
from src.analysis.report_formatter import ReportFormatter
from src.ai.sentiment_analyzer import SentimentAnalyzer
from src.ai.strategy_generator import StrategyGenerator
from typing import Optional

logger = logging.getLogger(__name__)


def _analyze_single_ticker(args: Tuple[str, Dict, str, bool]) -> Dict:
    """
    Standalone function for multiprocessing - analyzes a single ticker.

    Args:
        args: Tuple of (ticker, ticker_data, earnings_date, override_daily_limit)

    Returns:
        Complete analysis dict
    """
    ticker, ticker_data, earnings_date, override_daily_limit = args

    try:
        logger.info(f"📊 {ticker}: Starting analysis (Score: {ticker_data.get('score', 0):.1f}/100, IV: {ticker_data.get('options_data', {}).get('current_iv', 'N/A')}%)")

        # Initialize clients within worker process (use defaults from config)
        sentiment_analyzer = SentimentAnalyzer()  # Uses sonar-pro for Reddit sentiment
        strategy_generator = StrategyGenerator()   # Uses gpt-4o-mini for cost savings

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
    Master orchestrator for earnings trade research automation.

    Automates the research process from your Trading Research Prompt.pdf:
    1. Get upcoming earnings (this week)
    2. Filter by IV crush criteria (IV Rank >50%, implied > actual moves)
    3. Analyze sentiment (retail/institutional/hedge fund)
    4. Generate 3-4 trade strategies with $20K position sizing
    5. Output formatted research report for manual execution
    """

    def __init__(
        self,
        earnings_calendar = None,
        ticker_filter: Optional[TickerFilter] = None,
        earnings_source: Optional[str] = None
    ):
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

        # Note: Sentiment analyzer and strategy generator are initialized
        # in worker processes for thread-safe parallel processing

    def _validate_earnings_date(self, earnings_date: str = None) -> str:
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

                # Warn if date is in the past
                if parsed_date.date() < datetime.now().date():
                    logger.warning(f"Earnings date {earnings_date} is in the past")

                # Warn if date is too far in future (>90 days)
                days_out = (parsed_date - datetime.now()).days
                if days_out > 90:
                    logger.warning(f"Earnings date {earnings_date} is {days_out} days out - options may not exist")

                return earnings_date

            except ValueError as e:
                logger.error(f"Invalid earnings date format: {earnings_date}. Expected YYYY-MM-DD")
                raise ValueError(f"Invalid earnings date format: {earnings_date}. Expected YYYY-MM-DD") from e
        else:
            # Default to next trading day
            default_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
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

    def _fetch_tickers_data(self, tickers: List[str], earnings_date: str) -> Tuple[List[Dict], List[str]]:
        """
        Fetch basic ticker data from yfinance and options data from Tradier.

        Uses batch fetching with yf.Tickers() for improved performance (50% faster).

        Args:
            tickers: List of ticker symbols
            earnings_date: Earnings date for options selection

        Returns:
            Tuple of (tickers_data, failed_tickers)
            - tickers_data: List of dicts with ticker info and options data
            - failed_tickers: List of tickers that failed to fetch
        """
        import yfinance as yf

        tickers_data = []
        failed_tickers = []

        if not tickers:
            return tickers_data, failed_tickers

        # Batch fetch all tickers at once (more efficient than individual calls)
        logger.info(f"📥 Batch fetching data for {len(tickers)} tickers...")
        tickers_str = ' '.join(tickers)
        tickers_obj = yf.Tickers(tickers_str)

        for i, ticker in enumerate(tickers, 1):
            logger.info(f"  [{i}/{len(tickers)}] Processing {ticker}...")
            try:
                # Access individual ticker from batch
                stock = tickers_obj.tickers[ticker]
                info = stock.info

                ticker_data = {
                    'ticker': ticker,
                    'earnings_date': earnings_date,
                    'earnings_time': 'amc',  # Default to after-market
                    'market_cap': info.get('marketCap', 0),
                    'price': info.get('currentPrice', info.get('regularMarketPrice', 0))
                }

                # Get options data from Tradier
                options_data = self.ticker_filter.tradier_client.get_options_data(
                    ticker,
                    current_price=ticker_data['price'],
                    earnings_date=earnings_date
                )

                # Validate options data
                if not options_data or not options_data.get('current_iv'):
                    logger.warning(f"{ticker}: No valid options data - skipping")
                    failed_tickers.append(ticker)
                    continue

                ticker_data['options_data'] = options_data

                # Calculate score
                ticker_data['score'] = self.ticker_filter.calculate_score(ticker_data)

                tickers_data.append(ticker_data)
                logger.info(f"    ✓ {ticker}: IV={options_data.get('current_iv', 0):.2f}%, Score={ticker_data['score']:.1f}")

            except Exception as e:
                logger.warning(f"    ✗ {ticker}: Failed to fetch data: {e}")
                failed_tickers.append(ticker)
                continue

        return tickers_data, failed_tickers

    def _run_parallel_analysis(
        self,
        tickers_data: List[Dict],
        earnings_date: str,
        override_daily_limit: bool
    ) -> List[Dict]:
        """
        Run parallel analysis on tickers using multiprocessing.

        Args:
            tickers_data: List of ticker data dicts
            earnings_date: Earnings date
            override_daily_limit: Whether to bypass daily API limits

        Returns:
            List of analysis results (may include errors)
        """
        logger.info(f"Running full analysis on {len(tickers_data)} tickers...")

        # Prepare arguments for parallel processing
        analysis_args = [
            (td['ticker'], td, earnings_date, override_daily_limit)
            for td in tickers_data
        ]

        # Use multiprocessing for parallel analysis
        num_workers = min(cpu_count(), len(tickers_data), 4)
        logger.info(f"Using {num_workers} parallel workers")

        timeout = 120 * len(tickers_data)

        try:
            with Pool(processes=num_workers) as pool:
                result = pool.map_async(_analyze_single_ticker, analysis_args)
                ticker_analyses = result.get(timeout=timeout)
        except TimeoutError:
            logger.error(f"Pool operation timed out after {timeout}s")
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
            logger.warning(f"❌ {len(filtered_out)} ticker(s) filtered out (IV < 50%): {', '.join([td['ticker'] for td in filtered_out])}")
            failed_tickers.extend([td['ticker'] for td in filtered_out])

        if not filtered_tickers:
            logger.warning("❌ No tickers passed IV filter (>50% IV or IV Rank required)")
            logger.info("💡 Try tickers with higher IV or adjust filter thresholds in config")
            return {
                'date': earnings_date,
                'analyzed_count': 0,
                'failed_count': len(failed_tickers),
                'ticker_analyses': [],
                'failed_analyses': [{'ticker': t, 'error': 'Failed IV filter (< 50%)' if t in [td['ticker'] for td in filtered_out] else 'Data fetch failed'} for t in failed_tickers]
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
            - filtered_count: Companies passing IV Rank filter
            - analyzed_count: Companies fully analyzed
            - ticker_analyses: List of full analyses for top tickers
        """
        if target_date is None:
            target_date = datetime.now().strftime('%Y-%m-%d')

        logger.info(f"Analyzing earnings for {target_date}...")

        # Step 1: Get earnings for the date (with filtering for already-reported)
        week_earnings = self.earnings_calendar.get_week_earnings(days=7)

        if target_date not in week_earnings:
            logger.warning(f"No earnings found for {target_date}")
            return {'date': target_date, 'error': 'No earnings found'}

        # Filter out already-reported earnings
        eastern = pytz.timezone('US/Eastern')
        now_et = datetime.now(eastern)

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
        pre_filtered_tickers = self.ticker_filter.pre_filter_tickers(
            all_tickers,
            min_market_cap=500_000_000,  # $500M minimum
            min_avg_volume=100_000       # 100K shares/day minimum
        )

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

        # Prepare arguments for parallel processing
        analysis_args = [
            (ticker_data['ticker'], ticker_data, target_date, override_daily_limit)
            for ticker_data in tickers_to_analyze
        ]

        # Use multiprocessing for parallel analysis
        # Limit workers to avoid overwhelming APIs
        num_workers = min(cpu_count(), len(tickers_to_analyze), 4)
        logger.info(f"Using {num_workers} parallel workers")

        # Timeout: 120 seconds per ticker (generous for API calls + Reddit scraping)
        timeout = 120 * len(tickers_to_analyze)

        try:
            with Pool(processes=num_workers) as pool:
                result = pool.map_async(_analyze_single_ticker, analysis_args)
                ticker_analyses = result.get(timeout=timeout)
        except TimeoutError:
            logger.error(f"Pool operation timed out after {timeout}s")
            ticker_analyses = []

        # Separate successful and failed analyses
        successful_analyses = []
        failed_analyses = []

        for analysis in ticker_analyses:
            if analysis.get('error'):
                failed_analyses.append(analysis)
                logger.warning(f"❌ {analysis['ticker']}: {analysis['error']}")
            else:
                successful_analyses.append(analysis)

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

    def generate_report(self, analysis_result: Dict) -> str:
        """
        Generate formatted research report.

        Args:
            analysis_result: Result from analyze_daily_earnings() or analyze_specific_tickers()

        Returns:
            Formatted text report
        """
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

EXAMPLES:
  # Analyze specific tickers with confirmation
  python -m src.analysis.earnings_analyzer --tickers "NVDA,META" 2025-11-05

  # Analyze specific tickers, skip confirmation
  python -m src.analysis.earnings_analyzer --tickers "NVDA,META,GOOGL" 2025-11-05 --yes

  # Scan calendar for Nov 5, analyze top 5 tickers
  python -m src.analysis.earnings_analyzer 2025-11-05 5 --yes

  # Use override mode to bypass daily limits
  python -m src.analysis.earnings_analyzer --tickers "NVDA,META" 2025-11-05 --yes --override

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

    # Parse arguments
    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv
    override_daily_limit = '--override' in sys.argv

    # Check for ticker list mode
    ticker_list = None
    if '--tickers' in sys.argv:
        idx = sys.argv.index('--tickers')
        if idx + 1 < len(sys.argv):
            ticker_list = sys.argv[idx + 1].upper().replace(' ', '').split(',')

    # Remove flags from args
    args = [arg for arg in sys.argv[1:] if arg not in ['--yes', '-y', '--tickers', '--override'] and not (ticker_list and arg.upper().replace(' ', '') == ','.join(ticker_list))]

    analyzer = EarningsAnalyzer()

    # TICKER LIST MODE
    if ticker_list:
        earnings_date = args[0] if len(args) > 0 else None

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
        max_analyze = int(args[1]) if len(args) > 1 else 2

        if target_date is None:
            target_date = datetime.now().strftime('%Y-%m-%d')
            logger.info(f"No date specified, using today: {target_date}")

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
    report = analyzer.generate_report(result)
    logger.info("\n\n")
    logger.info(report)

    # Save to file with timestamp to avoid overwriting
    timestamp = datetime.now().strftime('%H%M%S')
    output_file = f"data/earnings_analysis_{result['date']}_{timestamp}.txt"
    with open(output_file, 'w') as f:
        f.write(report)

    logger.info(f"\n\nReport saved to: {output_file}")

    # Cleanup old reports (>15 days) as final step
    EarningsAnalyzer.cleanup_old_reports(days=15)
