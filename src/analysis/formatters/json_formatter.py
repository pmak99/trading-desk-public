"""JSON output formatter for analysis results."""

import json
from datetime import datetime, date
from typing import Dict, Any
import numpy as np


class JSONFormatter:
    """Format analysis results as JSON."""

    @staticmethod
    def format(analysis_result: Dict[str, Any]) -> str:
        """
        Format analysis results as pretty-printed JSON.

        Args:
            analysis_result: Analysis results from EarningsAnalyzer

        Returns:
            JSON string with analysis results
        """
        return json.dumps(analysis_result, indent=2, default=JSONFormatter._json_serializer)

    @staticmethod
    def _json_serializer(obj: Any) -> Any:
        """
        Custom JSON serializer for non-serializable types.

        Handles:
        - numpy int64/float64 types (from pandas/yfinance)
        - datetime/date objects
        - Custom objects with __dict__
        """
        # Handle numpy scalar types (int64, float64, etc.)
        if isinstance(obj, (np.integer, np.floating)):
            return obj.item()  # Convert to native Python type
        elif isinstance(obj, np.ndarray):
            return obj.tolist()  # Convert arrays to lists
        elif isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif hasattr(obj, '__dict__'):
            return str(obj)
        else:
            return str(obj)


# CLI for testing
if __name__ == "__main__":
    # Test with sample data
    test_data = {
        'date': '2025-11-08',
        'analyzed_count': 2,
        'failed_count': 1,
        'tickers': [
            {
                'ticker': 'NVDA',
                'iv_rank': 75.5,
                'score': 82.3,
                'sentiment': {
                    'overall': 'bullish',
                    'confidence': 'high'
                },
                'strategies': [
                    {
                        'type': 'Iron Condor',
                        'strikes': '120/125/135/140',
                        'premium': 1.25
                    }
                ]
            }
        ]
    }

    output = JSONFormatter.format(test_data)
    print(output)
