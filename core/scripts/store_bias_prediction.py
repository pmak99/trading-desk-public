#!/usr/bin/env python3
"""
Store directional bias predictions for upcoming earnings.

This script analyzes skew for upcoming earnings and stores predictions
in the database for later validation against actual price moves.

Usage:
    # Store predictions for specific tickers
    python scripts/store_bias_prediction.py AAPL TSLA CRM

    # Store predictions for all upcoming earnings (next 14 days)
    python scripts/store_bias_prediction.py --all

    # Store for specific date range
    python scripts/store_bias_prediction.py --start 2025-12-01 --end 2025-12-07
"""

import sys
import argparse
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.config import Config
from src.container import Container
from src.domain.enums import DirectionalBias

logger = logging.getLogger(__name__)


def store_bias_prediction(
    db_path: str,
    ticker: str,
    earnings_date: date,
    expiration: date,
    stock_price: float,
    skew_analysis,
    vrp_result=None
):
    """
    Store a directional bias prediction in the database.

    Args:
        db_path: Path to database
        ticker: Stock symbol
        earnings_date: Earnings announcement date
        expiration: Option expiration date
        stock_price: Current stock price
        skew_analysis: SkewAnalysis object from skew_enhanced.py
        vrp_result: Optional VRPResult object
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Extract data from skew analysis
        bias = skew_analysis.directional_bias

        cursor.execute("""
            INSERT OR REPLACE INTO bias_predictions (
                ticker, earnings_date, expiration,
                stock_price, predicted_at,
                skew_atm, skew_curvature, skew_strength, slope_atm,
                directional_bias, bias_strength, bias_confidence,
                r_squared, num_points,
                vrp_ratio, implied_move_pct, historical_mean_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker,
            earnings_date,
            expiration,
            stock_price,
            datetime.now(),
            skew_analysis.skew_atm.value,
            skew_analysis.curvature,
            skew_analysis.strength,
            skew_analysis.slope_atm,
            bias.value,  # Store enum value (e.g., "strong_bullish")
            bias.strength(),
            skew_analysis.bias_confidence,
            skew_analysis.confidence,
            skew_analysis.num_points,
            vrp_result.vrp_ratio if vrp_result else None,
            vrp_result.implied_move.value if vrp_result else None,
            vrp_result.historical_mean.value if vrp_result else None,
        ))

        conn.commit()
        logger.info(
            f"Stored bias prediction: {ticker} - {bias.value} "
            f"(strength={bias.strength()}, confidence={skew_analysis.bias_confidence:.2f})"
        )

    except Exception as e:
        logger.error(f"Failed to store bias prediction for {ticker}: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def analyze_and_store(
    container: Container,
    ticker: str,
    earnings_date: date,
    db_path: str
) -> bool:
    """
    Analyze skew and store bias prediction for a ticker.

    Returns:
        True if successful, False otherwise
    """
    try:
        # Get appropriate expiration
        expiration = earnings_date + timedelta(days=2)  # Typically 2 days after

        # Analyze skew
        skew_analyzer = container.skew_analyzer
        skew_result = skew_analyzer.analyze_skew_curve(ticker, expiration)

        if skew_result.is_err:
            logger.warning(f"{ticker}: Skew analysis failed - {skew_result.error}")
            return False

        skew_analysis = skew_result.value
        stock_price = float(skew_analysis.stock_price.amount)

        # Optionally get VRP for context
        vrp_result = None
        try:
            vrp_calculator = container.vrp_calculator
            vrp_res = vrp_calculator.calculate_vrp(ticker, earnings_date)
            if vrp_res.is_ok:
                vrp_result = vrp_res.value
        except Exception as e:
            logger.debug(f"{ticker}: VRP calculation failed (non-critical): {e}")

        # Store prediction
        store_bias_prediction(
            db_path,
            ticker,
            earnings_date,
            expiration,
            stock_price,
            skew_analysis,
            vrp_result
        )

        logger.info(
            f"{ticker}: ✓ Stored prediction - {skew_analysis.directional_bias.value} "
            f"(confidence={skew_analysis.bias_confidence:.2f})"
        )
        return True

    except Exception as e:
        logger.error(f"{ticker}: Failed to analyze and store - {e}")
        return False


def get_upcoming_earnings(
    container: Container,
    days_ahead: int = 14
) -> List[tuple]:
    """
    Get upcoming earnings events.

    Returns:
        List of (ticker, earnings_date) tuples
    """
    earnings_repo = container.earnings_repository
    today = datetime.now().date()
    end_date = today + timedelta(days=days_ahead)

    # Query database directly for upcoming earnings
    conn = sqlite3.connect(container.config.database.path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ticker, earnings_date
        FROM earnings_calendar
        WHERE earnings_date >= ? AND earnings_date <= ?
        ORDER BY earnings_date
    """, (today, end_date))

    results = cursor.fetchall()
    conn.close()

    return [(row[0], datetime.strptime(row[1], '%Y-%m-%d').date()) for row in results]


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Store directional bias predictions for upcoming earnings"
    )

    parser.add_argument(
        'tickers',
        nargs='*',
        help='Ticker symbols to analyze'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Analyze all upcoming earnings in next 14 days'
    )
    parser.add_argument(
        '--days-ahead',
        type=int,
        default=14,
        help='Days ahead to look for earnings (default: 14)'
    )
    parser.add_argument(
        '--start',
        type=str,
        help='Start date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end',
        type=str,
        help='End date (YYYY-MM-DD)'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    # Initialize container
    config = Config.from_env()
    container = Container(config)
    db_path = str(config.database.path)

    print("=" * 70)
    print("DIRECTIONAL BIAS PREDICTION STORAGE")
    print("=" * 70)

    # Determine which tickers to analyze
    earnings_to_analyze = []

    if args.all:
        print(f"\nFetching upcoming earnings (next {args.days_ahead} days)...")
        earnings_to_analyze = get_upcoming_earnings(container, args.days_ahead)
        print(f"Found {len(earnings_to_analyze)} upcoming earnings events")

    elif args.tickers:
        # Get earnings dates for specified tickers
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for ticker in args.tickers:
            cursor.execute("""
                SELECT earnings_date
                FROM earnings_calendar
                WHERE ticker = ?
                AND earnings_date >= date('now')
                ORDER BY earnings_date
                LIMIT 1
            """, (ticker,))

            row = cursor.fetchone()
            if row:
                earnings_date = datetime.strptime(row[0], '%Y-%m-%d').date()
                earnings_to_analyze.append((ticker, earnings_date))
            else:
                logger.warning(f"{ticker}: No upcoming earnings found")

        conn.close()
    else:
        parser.print_help()
        return 1

    if not earnings_to_analyze:
        print("\n❌ No earnings events to analyze")
        return 1

    # Analyze and store predictions
    print(f"\nAnalyzing {len(earnings_to_analyze)} earnings events...")
    print("-" * 70)

    success_count = 0
    fail_count = 0

    for ticker, earnings_date in earnings_to_analyze:
        print(f"\n{ticker} - {earnings_date}:")

        if analyze_and_store(container, ticker, earnings_date, db_path):
            success_count += 1
        else:
            fail_count += 1

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total: {len(earnings_to_analyze)}")
    print(f"  ✓ Stored: {success_count}")
    print(f"  ✗ Failed: {fail_count}")
    print("=" * 70)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
