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
from datetime import datetime
from typing import Dict, List, Tuple
import sys
from multiprocessing import Pool, cpu_count

from src.earnings_calendar import EarningsCalendar
from src.ticker_filter import TickerFilter
from src.sentiment_analyzer import SentimentAnalyzer
from src.strategy_generator import StrategyGenerator

logger = logging.getLogger(__name__)


def _analyze_single_ticker(args: Tuple[str, Dict, str]) -> Dict:
    """
    Standalone function for multiprocessing - analyzes a single ticker.

    Args:
        args: Tuple of (ticker, ticker_data, earnings_date)

    Returns:
        Complete analysis dict
    """
    ticker, ticker_data, earnings_date = args

    try:
        # Initialize clients within worker process
        sentiment_analyzer = SentimentAnalyzer(preferred_model="sonar-pro")
        strategy_generator = StrategyGenerator(preferred_model="sonar-pro")

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
            logger.info(f"{ticker}: Fetching sentiment...")
            sentiment = sentiment_analyzer.analyze_earnings_sentiment(ticker, earnings_date)
            analysis['sentiment'] = sentiment
        except Exception as e:
            logger.error(f"{ticker}: Sentiment analysis failed: {e}")

        # Generate strategies
        if analysis['options_data'] and analysis['sentiment']:
            try:
                logger.info(f"{ticker}: Generating strategies...")
                strategies = strategy_generator.generate_strategies(
                    ticker,
                    analysis['options_data'],
                    analysis['sentiment'],
                    ticker_data
                )
                analysis['strategies'] = strategies
            except Exception as e:
                logger.error(f"{ticker}: Strategy generation failed: {e}")

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

    def __init__(self):
        """Initialize earnings analyzer components."""
        logger.info("Initializing Earnings Analyzer...")

        self.earnings_calendar = EarningsCalendar()
        self.ticker_filter = TickerFilter()

        # Note: Sentiment analyzer and strategy generator are initialized
        # in worker processes for thread-safe parallel processing

    def analyze_specific_tickers(self, tickers: list, earnings_date: str = None) -> Dict:
        """
        Analyze specific tickers directly (bypass calendar scanning).

        Useful when you have specific tickers in mind for earnings plays.

        Args:
            tickers: List of ticker symbols (e.g., ['META', 'MSFT', 'GOOGL'])
            earnings_date: Expected earnings date (YYYY-MM-DD), defaults to next trading day

        Returns:
            Dict with:
            - date: Earnings date used
            - analyzed_count: Number of tickers analyzed
            - ticker_analyses: List of full analyses
            - failed_analyses: List of failed tickers
        """
        import yfinance as yf

        if earnings_date is None:
            # Default to next trading day
            earnings_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

        logger.info(f"Analyzing {len(tickers)} specific tickers for {earnings_date}")

        # Get basic ticker data from yfinance
        tickers_data = []
        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
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
                ticker_data['options_data'] = options_data

                # Calculate score
                ticker_data['score'] = self.ticker_filter.calculate_score(ticker_data)

                tickers_data.append(ticker_data)
                logger.info(f"{ticker}: IV={options_data.get('current_iv', 0):.2f}%, Score={ticker_data['score']:.1f}")

            except Exception as e:
                logger.error(f"{ticker}: Failed to fetch data: {e}")
                continue

        if not tickers_data:
            logger.warning("No valid tickers to analyze")
            return {
                'date': earnings_date,
                'analyzed_count': 0,
                'failed_count': 0,
                'ticker_analyses': [],
                'failed_analyses': []
            }

        # Prepare for parallel analysis
        logger.info(f"Running full analysis on {len(tickers_data)} tickers...")

        analysis_args = [
            (td['ticker'], td, earnings_date)
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

    def analyze_daily_earnings(self, target_date: str = None, max_analyze: int = 2) -> Dict:
        """
        Analyze earnings for a specific day and generate trade ideas.

        Args:
            target_date: Date to analyze (YYYY-MM-DD), defaults to today
            max_analyze: Maximum number of tickers to fully analyze (costs $$$)

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

        # Step 1: Get earnings for the date
        week_earnings = self.earnings_calendar.get_week_earnings(days=7)

        if target_date not in week_earnings:
            logger.warning(f"No earnings found for {target_date}")
            return {'date': target_date, 'error': 'No earnings found'}

        earnings_list = week_earnings[target_date]
        total_count = len(earnings_list)

        logger.info(f"Found {total_count} companies reporting on {target_date}")

        # Step 2: Separate by timing and filter/score
        by_timing = {'pre_market': [], 'after_hours': []}
        for earning in earnings_list:
            time = earning.get('time', '')
            ticker = earning.get('ticker', '')

            if 'pre-market' in time:
                by_timing['pre_market'].append(ticker)
            elif 'after-hours' in time:
                by_timing['after_hours'].append(ticker)

        # Apply ticker filter (includes IV Rank check)
        logger.info("Filtering and scoring tickers...")
        selected = self.ticker_filter.select_daily_candidates(
            by_timing,
            pre_market_count=min(2, len(by_timing['pre_market'])),
            after_hours_count=min(3, len(by_timing['after_hours']))
        )

        filtered_tickers = selected['pre_market'] + selected['after_hours']
        filtered_count = len(filtered_tickers)

        logger.info(f"Filtered to {filtered_count} tickers passing IV Rank criteria")

        # Step 3: Full analysis for top tickers (parallel processing)
        tickers_to_analyze = filtered_tickers[:max_analyze]

        if not tickers_to_analyze:
            return {
                'date': target_date,
                'total_earnings': total_count,
                'filtered_count': filtered_count,
                'analyzed_count': 0,
                'ticker_analyses': []
            }

        logger.info(f"Analyzing {len(tickers_to_analyze)} tickers in parallel...")

        # Prepare arguments for parallel processing
        analysis_args = [
            (ticker_data['ticker'], ticker_data, target_date)
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
            analysis_result: Result from analyze_daily_earnings()

        Returns:
            Formatted text report
        """
        report_lines = []
        report_lines.append("=" * 80)
        report_lines.append("EARNINGS TRADE RESEARCH REPORT")
        report_lines.append("=" * 80)
        report_lines.append(f"\nDate: {analysis_result['date']}")

        # Different format for ticker list mode vs calendar mode
        if 'total_earnings' in analysis_result:
            # Calendar mode
            report_lines.append(f"Total Earnings: {analysis_result['total_earnings']} companies")
            report_lines.append(f"Passed IV Filter: {analysis_result['filtered_count']} tickers")
            report_lines.append(f"Fully Analyzed: {analysis_result['analyzed_count']} tickers")
        else:
            # Ticker list mode
            report_lines.append(f"Fully Analyzed: {analysis_result['analyzed_count']} tickers")

        if analysis_result.get('failed_count', 0) > 0:
            report_lines.append(f"Failed: {analysis_result['failed_count']} tickers")

        report_lines.append("\n" + "=" * 80)

        # Detail each analyzed ticker
        for i, ticker_analysis in enumerate(analysis_result['ticker_analyses'], 1):
            ticker = ticker_analysis['ticker']
            options = ticker_analysis['options_data']
            sentiment = ticker_analysis['sentiment']
            strategies = ticker_analysis.get('strategies', {})

            report_lines.append(f"\n\nTICKER {i}: {ticker}")
            report_lines.append("-" * 80)

            # Key metrics
            report_lines.append(f"\nPrice: ${ticker_analysis['price']:.2f}")
            report_lines.append(f"Score: {ticker_analysis['score']:.1f}/100")
            report_lines.append(f"Earnings: {ticker_analysis['earnings_date']}")

            # Options data
            if options:
                report_lines.append(f"\nOPTIONS METRICS:")
                # Show actual IV % prominently (primary filter metric)
                current_iv = options.get('current_iv', None)
                if current_iv is not None and current_iv > 0:
                    report_lines.append(f"  Current IV: {current_iv}% {'(HIGH - Good for IV crush)' if current_iv >= 60 else ''}")
                report_lines.append(f"  IV Rank: {options.get('iv_rank', 'N/A')}%")
                report_lines.append(f"  Expected Move: {options.get('expected_move_pct', 'N/A')}%")
                report_lines.append(f"  Avg Actual Move: {options.get('avg_actual_move_pct', 'N/A')}%")
                report_lines.append(f"  IV Crush Ratio: {options.get('iv_crush_ratio', 'N/A')}x")
                report_lines.append(f"  Options Volume: {options.get('options_volume', 0):,}")
                report_lines.append(f"  Open Interest: {options.get('open_interest', 0):,}")

            # Sentiment
            if sentiment:
                report_lines.append(f"\nSENTIMENT:")
                report_lines.append(f"  Overall: {sentiment.get('overall_sentiment', 'N/A').upper()}")
                report_lines.append(f"  Retail: {sentiment.get('retail_sentiment', 'N/A')[:150]}...")
                report_lines.append(f"  Institutional: {sentiment.get('institutional_sentiment', 'N/A')[:150]}...")

                if sentiment.get('tailwinds'):
                    report_lines.append(f"\n  Tailwinds:")
                    for tw in sentiment['tailwinds'][:3]:
                        report_lines.append(f"    + {tw}")

                if sentiment.get('headwinds'):
                    report_lines.append(f"  Headwinds:")
                    for hw in sentiment['headwinds'][:3]:
                        report_lines.append(f"    - {hw}")

            # Strategies
            if strategies and strategies.get('strategies'):
                report_lines.append(f"\nTRADE STRATEGIES:")
                for j, strat in enumerate(strategies['strategies'], 1):
                    report_lines.append(f"\n  Strategy {j}: {strat.get('name', 'N/A')}")
                    report_lines.append(f"    Strikes: {strat.get('strikes', 'N/A')}")
                    report_lines.append(f"    Credit/Debit: {strat.get('credit_debit', 'N/A')}")
                    report_lines.append(f"    Max Profit: {strat.get('max_profit', 'N/A')}")
                    report_lines.append(f"    Max Loss: {strat.get('max_loss', 'N/A')}")
                    report_lines.append(f"    POP: {strat.get('probability_of_profit', 'N/A')}")
                    report_lines.append(f"    Contracts: {strat.get('contract_count', 'N/A')} (for $20K risk)")
                    report_lines.append(f"    Scores: Profit {strat.get('profitability_score', 'N/A')}/10, Risk {strat.get('risk_score', 'N/A')}/10")

                rec_idx = strategies.get('recommended_strategy', 0)
                report_lines.append(f"\n  RECOMMENDED: Strategy {rec_idx + 1}")
                report_lines.append(f"  Why: {strategies.get('recommendation_rationale', 'N/A')[:200]}...")

        # Add failed tickers section if any
        if analysis_result.get('failed_analyses'):
            report_lines.append("\n\n" + "=" * 80)
            report_lines.append("FAILED ANALYSES")
            report_lines.append("=" * 80)
            for failed in analysis_result['failed_analyses']:
                report_lines.append(f"\n{failed['ticker']}: {failed.get('error', 'Unknown error')}")

        report_lines.append("\n\n" + "=" * 80)
        report_lines.append("END OF REPORT")
        report_lines.append("=" * 80)

        return "\n".join(report_lines)


# CLI
if __name__ == "__main__":
    # Setup logging for CLI execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger.info("")
    logger.info('='*80)
    logger.info('EARNINGS TRADE ANALYZER - AUTOMATED RESEARCH SYSTEM')
    logger.info('='*80)
    logger.info("")

    # Parse arguments
    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv

    # Check for ticker list mode
    ticker_list = None
    if '--tickers' in sys.argv:
        idx = sys.argv.index('--tickers')
        if idx + 1 < len(sys.argv):
            ticker_list = sys.argv[idx + 1].upper().replace(' ', '').split(',')

    # Remove flags from args
    args = [arg for arg in sys.argv[1:] if arg not in ['--yes', '-y', '--tickers'] and not (ticker_list and arg.upper().replace(' ', '') == ','.join(ticker_list))]

    analyzer = EarningsAnalyzer()

    # TICKER LIST MODE
    if ticker_list:
        earnings_date = args[0] if len(args) > 0 else None

        logger.info(f"\n📋 TICKER LIST MODE")
        logger.info(f"Tickers: {', '.join(ticker_list)}")
        logger.info(f"Earnings Date: {earnings_date or 'next trading day (default)'}")
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
        result = analyzer.analyze_specific_tickers(ticker_list, earnings_date)

    # CALENDAR SCANNING MODE (default)
    else:
        target_date = args[0] if len(args) > 0 else None
        max_analyze = int(args[1]) if len(args) > 1 else 2

        if target_date is None:
            target_date = datetime.now().strftime('%Y-%m-%d')
            logger.info(f"No date specified, using today: {target_date}")

        logger.info(f"\nAnalyzing up to {max_analyze} tickers for {target_date}")
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
        result = analyzer.analyze_daily_earnings(target_date, max_analyze)

    # Generate and display report
    report = analyzer.generate_report(result)
    logger.info("\n\n")
    logger.info(report)

    # Optionally save to file
    output_file = f"data/earnings_analysis_{result['date']}.txt"
    with open(output_file, 'w') as f:
        f.write(report)

    logger.info(f"\n\nReport saved to: {output_file}")
