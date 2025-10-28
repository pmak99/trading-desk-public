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
from typing import Dict, List
import sys

from src.earnings_calendar import EarningsCalendar
from src.ticker_filter import TickerFilter
from src.options_data_client import OptionsDataClient
from src.sentiment_analyzer import SentimentAnalyzer
from src.strategy_generator import StrategyGenerator

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
        """Initialize all Phase 2 components."""
        logger.info("Initializing Earnings Analyzer...")

        self.earnings_calendar = EarningsCalendar()
        self.ticker_filter = TickerFilter()

        # Optional components (graceful degradation if unavailable)
        try:
            self.options_client = OptionsDataClient()
        except Exception as e:
            logger.warning(f"Options client unavailable: {e}")
            self.options_client = None

        try:
            self.sentiment_analyzer = SentimentAnalyzer(model="sonar-pro")
        except Exception as e:
            logger.warning(f"Sentiment analyzer unavailable: {e}")
            self.sentiment_analyzer = None

        try:
            self.strategy_generator = StrategyGenerator(model="sonar-pro")
        except Exception as e:
            logger.warning(f"Strategy generator unavailable: {e}")
            self.strategy_generator = None

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

        # Step 3: Full analysis for top tickers (limited by max_analyze)
        ticker_analyses = []
        analyzed_count = 0

        for ticker_data in filtered_tickers[:max_analyze]:
            ticker = ticker_data['ticker']
            logger.info(f"Running full analysis for {ticker}...")

            try:
                analysis = self._analyze_ticker(ticker, ticker_data, target_date)
                ticker_analyses.append(analysis)
                analyzed_count += 1
            except Exception as e:
                logger.error(f"Error analyzing {ticker}: {e}")
                continue

        return {
            'date': target_date,
            'total_earnings': total_count,
            'filtered_count': filtered_count,
            'analyzed_count': analyzed_count,
            'ticker_analyses': ticker_analyses
        }

    def _analyze_ticker(self, ticker: str, ticker_data: Dict, earnings_date: str) -> Dict:
        """
        Run full analysis for a single ticker.

        Args:
            ticker: Ticker symbol
            ticker_data: Basic ticker data from filter
            earnings_date: Earnings date

        Returns:
            Complete analysis dict
        """
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
        if self.sentiment_analyzer:
            try:
                logger.info(f"{ticker}: Fetching sentiment...")
                sentiment = self.sentiment_analyzer.analyze_earnings_sentiment(ticker, earnings_date)
                analysis['sentiment'] = sentiment
            except Exception as e:
                logger.error(f"{ticker}: Sentiment analysis failed: {e}")

        # Generate strategies
        if self.strategy_generator and analysis['options_data'] and analysis['sentiment']:
            try:
                logger.info(f"{ticker}: Generating strategies...")
                strategies = self.strategy_generator.generate_strategies(
                    ticker,
                    analysis['options_data'],
                    analysis['sentiment'],
                    ticker_data
                )
                analysis['strategies'] = strategies
            except Exception as e:
                logger.error(f"{ticker}: Strategy generation failed: {e}")

        return analysis

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
        report_lines.append(f"Total Earnings: {analysis_result['total_earnings']} companies")
        report_lines.append(f"Passed IV Filter: {analysis_result['filtered_count']} tickers")
        report_lines.append(f"Fully Analyzed: {analysis_result['analyzed_count']} tickers")
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

        report_lines.append("\n\n" + "=" * 80)
        report_lines.append("END OF REPORT")
        report_lines.append("=" * 80)

        return "\n".join(report_lines)


# CLI
if __name__ == "__main__":
    print()
    print('='*80)
    print('EARNINGS TRADE ANALYZER - AUTOMATED RESEARCH SYSTEM')
    print('='*80)
    print()

    # Parse arguments
    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv
    args = [arg for arg in sys.argv[1:] if arg not in ['--yes', '-y']]

    target_date = args[0] if len(args) > 0 else None
    max_analyze = int(args[1]) if len(args) > 1 else 2

    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')
        print(f"No date specified, using today: {target_date}")

    print(f"\nAnalyzing up to {max_analyze} tickers for {target_date}")
    print(f"WARNING: This will make API calls (estimated cost: ${0.05 * max_analyze:.2f})")
    print()

    if not skip_confirm:
        confirmation = input("Continue? (y/n): ")
        if confirmation.lower() != 'y':
            print("Aborted.")
            exit()
    else:
        print("Auto-confirmed with --yes flag")

    # Run analysis
    analyzer = EarningsAnalyzer()
    result = analyzer.analyze_daily_earnings(target_date, max_analyze)

    # Generate and display report
    report = analyzer.generate_report(result)
    print("\n\n")
    print(report)

    # Optionally save to file
    output_file = f"data/earnings_analysis_{target_date}.txt"
    with open(output_file, 'w') as f:
        f.write(report)

    print(f"\n\nReport saved to: {output_file}")
