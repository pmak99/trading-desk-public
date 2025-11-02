"""
Report formatter for earnings analysis results.

Separates report formatting logic from analysis logic for better maintainability.
"""

from typing import Dict


class ReportFormatter:
    """Formats earnings analysis results into readable reports."""

    @staticmethod
    def format_analysis_report(analysis_result: Dict) -> str:
        """
        Generate formatted research report.

        Args:
            analysis_result: Result from analyze_daily_earnings() or analyze_specific_tickers()
                Contains:
                - date: Analysis date
                - total_earnings: Total companies (calendar mode only)
                - filtered_count: Tickers passing IV filter (calendar mode only)
                - analyzed_count: Fully analyzed tickers
                - failed_count: Failed analyses
                - ticker_analyses: List of analysis dicts
                - failed_analyses: List of failed analysis dicts

        Returns:
            Formatted text report with sections for each ticker
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
            ticker_section = ReportFormatter._format_ticker_section(ticker_analysis, i)
            report_lines.append(ticker_section)

        # Add failed tickers section if any
        if analysis_result.get('failed_analyses'):
            failed_section = ReportFormatter._format_failed_section(analysis_result['failed_analyses'])
            report_lines.append(failed_section)

        report_lines.append("\n\n" + "=" * 80)
        report_lines.append("END OF REPORT")
        report_lines.append("=" * 80)

        return "\n".join(report_lines)

    @staticmethod
    def _format_ticker_section(ticker_analysis: Dict, index: int) -> str:
        """
        Format a single ticker's analysis section.

        Args:
            ticker_analysis: Analysis dict for one ticker
            index: Ticker index in report

        Returns:
            Formatted ticker section
        """
        lines = []
        ticker = ticker_analysis['ticker']
        options = ticker_analysis['options_data']
        sentiment = ticker_analysis['sentiment']
        strategies = ticker_analysis.get('strategies', {})

        lines.append(f"\n\nTICKER {index}: {ticker}")
        lines.append("-" * 80)

        # Key metrics
        lines.append(f"\nPrice: ${ticker_analysis['price']:.2f}")
        lines.append(f"Score: {ticker_analysis['score']:.1f}/100")
        lines.append(f"Earnings: {ticker_analysis['earnings_date']}")

        # Options data
        if options:
            options_section = ReportFormatter._format_options_section(options)
            lines.append(options_section)

        # Sentiment
        if sentiment:
            sentiment_section = ReportFormatter._format_sentiment_section(sentiment)
            lines.append(sentiment_section)

        # Strategies
        if strategies and strategies.get('strategies'):
            strategies_section = ReportFormatter._format_strategies_section(strategies)
            lines.append(strategies_section)

        return "\n".join(lines)

    @staticmethod
    def _format_options_section(options: Dict) -> str:
        """Format options metrics section."""
        lines = []
        lines.append(f"\nOPTIONS METRICS:")

        # Show actual IV % prominently (primary filter metric)
        current_iv = options.get('current_iv', None)
        if current_iv is not None and current_iv > 0:
            iv_note = '(HIGH - Good for IV crush)' if current_iv >= 60 else ''
            lines.append(f"  Current IV: {current_iv}% {iv_note}")

        lines.append(f"  IV Rank: {options.get('iv_rank', 'N/A')}%")
        lines.append(f"  Expected Move: {options.get('expected_move_pct', 'N/A')}%")
        lines.append(f"  Avg Actual Move: {options.get('avg_actual_move_pct', 'N/A')}%")
        lines.append(f"  IV Crush Ratio: {options.get('iv_crush_ratio', 'N/A')}x")
        lines.append(f"  Options Volume: {options.get('options_volume', 0):,}")
        lines.append(f"  Open Interest: {options.get('open_interest', 0):,}")

        return "\n".join(lines)

    @staticmethod
    def _format_sentiment_section(sentiment: Dict) -> str:
        """Format sentiment analysis section."""
        lines = []
        lines.append(f"\nSENTIMENT:")
        lines.append(f"  Overall: {sentiment.get('overall_sentiment', 'N/A').upper()}")
        lines.append(f"  Retail: {sentiment.get('retail_sentiment', 'N/A')[:150]}...")
        lines.append(f"  Institutional: {sentiment.get('institutional_sentiment', 'N/A')[:150]}...")

        if sentiment.get('tailwinds'):
            lines.append(f"\n  Tailwinds:")
            for tw in sentiment['tailwinds'][:3]:
                lines.append(f"    + {tw}")

        if sentiment.get('headwinds'):
            lines.append(f"  Headwinds:")
            for hw in sentiment['headwinds'][:3]:
                lines.append(f"    - {hw}")

        return "\n".join(lines)

    @staticmethod
    def _format_strategies_section(strategies: Dict) -> str:
        """Format trading strategies section."""
        lines = []
        lines.append(f"\nTRADE STRATEGIES:")

        for j, strat in enumerate(strategies['strategies'], 1):
            lines.append(f"\n  Strategy {j}: {strat.get('name', 'N/A')}")
            lines.append(f"    Strikes: {strat.get('strikes', 'N/A')}")
            lines.append(f"    Credit/Debit: {strat.get('credit_debit', 'N/A')}")
            lines.append(f"    Max Profit: {strat.get('max_profit', 'N/A')}")
            lines.append(f"    Max Loss: {strat.get('max_loss', 'N/A')}")
            lines.append(f"    POP: {strat.get('probability_of_profit', 'N/A')}")
            lines.append(f"    Contracts: {strat.get('contract_count', 'N/A')} (for $20K risk)")
            lines.append(f"    Scores: Profit {strat.get('profitability_score', 'N/A')}/10, Risk {strat.get('risk_score', 'N/A')}/10")

        rec_idx = strategies.get('recommended_strategy', 0)
        lines.append(f"\n  RECOMMENDED: Strategy {rec_idx + 1}")
        lines.append(f"  Why: {strategies.get('recommendation_rationale', 'N/A')[:200]}...")

        return "\n".join(lines)

    @staticmethod
    def _format_failed_section(failed_analyses: list) -> str:
        """Format failed analyses section."""
        lines = []
        lines.append("\n\n" + "=" * 80)
        lines.append("FAILED ANALYSES")
        lines.append("=" * 80)

        for failed in failed_analyses:
            lines.append(f"\n{failed['ticker']}: {failed.get('error', 'Unknown error')}")

        return "\n".join(lines)
