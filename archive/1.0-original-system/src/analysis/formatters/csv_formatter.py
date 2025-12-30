"""CSV output formatter for analysis results."""

import csv
from io import StringIO
from typing import Dict, Any, List


class CSVFormatter:
    """Format analysis results as CSV."""

    @staticmethod
    def format(analysis_result: Dict[str, Any]) -> str:
        """
        Format analysis results as CSV.

        Args:
            analysis_result: Analysis results from EarningsAnalyzer

        Returns:
            CSV string with analysis results
        """
        output = StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow([
            'Ticker', 'IV', 'Weekly IV Change', 'Score', 'Sentiment',
            'Confidence', 'Price Target', 'Risk Level',
            'Primary Strategy', 'Strategy Details', 'Status'
        ])

        # Write analyzed tickers
        if 'analyzed_tickers' in analysis_result:
            for ticker_data in analysis_result['analyzed_tickers']:
                CSVFormatter._write_ticker_row(writer, ticker_data, 'Analyzed')

        # Write failed tickers
        if 'failed_tickers' in analysis_result:
            for ticker_data in analysis_result['failed_tickers']:
                CSVFormatter._write_ticker_row(writer, ticker_data, 'Failed')

        return output.getvalue()

    @staticmethod
    def _write_ticker_row(writer: csv.writer, ticker_data: Dict[str, Any], status: str):
        """Write a single ticker row to CSV."""
        ticker = ticker_data.get('ticker', 'N/A')

        # IV metrics
        iv_metrics = ticker_data.get('iv_metrics', {})
        # Also check options_data (used in newer structure)
        options_data = ticker_data.get('options_data', {})
        iv = iv_metrics.get('current_iv') or options_data.get('current_iv', 0)
        weekly_change = iv_metrics.get('weekly_iv_change') or options_data.get('weekly_iv_change')

        # Score
        score = ticker_data.get('score', 0)

        # Sentiment
        sentiment_data = ticker_data.get('sentiment', {})
        sentiment = sentiment_data.get('overall_sentiment', 'N/A')
        confidence = sentiment_data.get('confidence_level', 'N/A')
        price_target = sentiment_data.get('price_target', 'N/A')

        # Risk
        risk = sentiment_data.get('risk_factors', ['N/A'])
        risk_level = risk[0] if isinstance(risk, list) and risk else 'N/A'

        # Strategy
        strategies = ticker_data.get('strategies', [])
        if strategies:
            primary_strategy = strategies[0].get('strategy_type', 'N/A')
            strategy_details = strategies[0].get('rationale', 'N/A')
        else:
            primary_strategy = 'N/A'
            strategy_details = 'N/A'

        weekly_str = f"{weekly_change:+.1f}%" if weekly_change is not None else "N/A"
        writer.writerow([
            ticker, f"{iv:.1f}%", weekly_str, f"{score:.1f}",
            sentiment, confidence, price_target, risk_level,
            primary_strategy, strategy_details, status
        ])

    @staticmethod
    def format_summary(analysis_result: Dict[str, Any]) -> str:
        """
        Format a summary CSV with just key metrics.

        Args:
            analysis_result: Analysis results from EarningsAnalyzer

        Returns:
            CSV string with summary metrics
        """
        output = StringIO()
        writer = csv.writer(output)

        # Write summary header
        writer.writerow(['Metric', 'Value'])
        writer.writerow(['Analysis Date', analysis_result.get('date', 'N/A')])
        writer.writerow(['Total Tickers', analysis_result.get('analyzed_count', 0) + analysis_result.get('failed_count', 0)])
        writer.writerow(['Successfully Analyzed', analysis_result.get('analyzed_count', 0)])
        writer.writerow(['Failed', analysis_result.get('failed_count', 0)])

        return output.getvalue()


# CLI for testing
if __name__ == "__main__":
    # Test with sample data
    test_data = {
        'date': '2025-11-08',
        'analyzed_count': 2,
        'failed_count': 1,
        'analyzed_tickers': [
            {
                'ticker': 'NVDA',
                'iv_metrics': {
                    'current_iv': 65.5,
                    'weekly_iv_change': 45.2
                },
                'score': 82.3,
                'sentiment': {
                    'overall_sentiment': 'bullish',
                    'confidence_level': 'high',
                    'price_target': '$150-160',
                    'risk_factors': ['High volatility']
                },
                'strategies': [
                    {
                        'strategy_type': 'Iron Condor',
                        'rationale': 'High IV, expect crush'
                    }
                ]
            }
        ],
        'failed_tickers': [
            {
                'ticker': 'AAPL',
                'error': 'IV too low'
            }
        ]
    }

    print("DETAILED CSV:")
    print(CSVFormatter.format(test_data))
    print("\nSUMMARY CSV:")
    print(CSVFormatter.format_summary(test_data))
