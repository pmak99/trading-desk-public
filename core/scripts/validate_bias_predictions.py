#!/usr/bin/env python3
"""
Validate directional bias predictions against actual price moves.

This script checks predictions that haven't been validated yet,
fetches the actual price moves post-earnings, and updates the database.

Usage:
    # Validate all unvalidated predictions
    python scripts/validate_bias_predictions.py

    # Validate specific ticker
    python scripts/validate_bias_predictions.py AAPL

    # Validate predictions in date range
    python scripts/validate_bias_predictions.py --start 2025-11-01 --end 2025-11-30
"""

import sys
import argparse
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
import yfinance as yf
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.config import Config

logger = logging.getLogger(__name__)


def get_actual_move(ticker: str, earnings_date: date) -> Optional[tuple]:
    """
    Get actual price move after earnings using yfinance.

    Args:
        ticker: Stock symbol
        earnings_date: Earnings announcement date

    Returns:
        (close_to_close_pct, gap_pct, direction) or None if failed
    """
    try:
        # Fetch data for earnings day and next day
        start = earnings_date - timedelta(days=5)
        end = earnings_date + timedelta(days=5)

        stock = yf.Ticker(ticker)
        hist = stock.history(start=start, end=end)

        if hist.empty:
            logger.warning(f"{ticker}: No price data available for {earnings_date}")
            return None

        # Find earnings day in data
        earnings_str = earnings_date.strftime('%Y-%m-%d')

        # Get pre-earnings close (day before earnings)
        pre_earnings_date = earnings_date - timedelta(days=1)

        # Find the actual trading day before earnings
        pre_close = None
        for i in range(10):  # Look back up to 10 days for trading day
            check_date = earnings_date - timedelta(days=i+1)
            check_str = check_date.strftime('%Y-%m-%d')
            if check_str in hist.index:
                pre_close = hist.loc[check_str, 'Close']
                break

        if pre_close is None:
            logger.warning(f"{ticker}: No pre-earnings close found")
            return None

        # Get post-earnings close (day of or after earnings)
        post_close = None
        post_open = None
        for i in range(10):  # Look ahead up to 10 days
            check_date = earnings_date + timedelta(days=i)
            check_str = check_date.strftime('%Y-%m-%d')
            if check_str in hist.index:
                post_close = hist.loc[check_str, 'Close']
                post_open = hist.loc[check_str, 'Open']
                break

        if post_close is None:
            logger.warning(f"{ticker}: No post-earnings close found")
            return None

        # Calculate moves
        close_to_close_pct = ((post_close - pre_close) / pre_close) * 100
        gap_pct = ((post_open - pre_close) / pre_close) * 100 if post_open else 0

        # Determine direction
        if abs(close_to_close_pct) < 0.5:
            direction = "FLAT"
        elif close_to_close_pct > 0:
            direction = "UP"
        else:
            direction = "DOWN"

        logger.debug(
            f"{ticker}: Move {close_to_close_pct:.2f}% "
            f"(gap: {gap_pct:.2f}%, direction: {direction})"
        )

        return (close_to_close_pct, gap_pct, direction)

    except Exception as e:
        logger.error(f"{ticker}: Failed to get actual move - {e}")
        return None


def is_prediction_correct(bias: str, direction: str) -> bool:
    """
    Check if directional bias prediction matched actual direction.

    Args:
        bias: Predicted bias (e.g., "strong_bullish", "weak_bearish", "neutral")
        direction: Actual direction ("UP", "DOWN", "FLAT")

    Returns:
        True if prediction was correct
    """
    # Bullish biases should match UP
    if "bullish" in bias.lower():
        return direction == "UP"

    # Bearish biases should match DOWN
    if "bearish" in bias.lower():
        return direction == "DOWN"

    # Neutral can match any (or specifically FLAT)
    if "neutral" in bias.lower():
        return direction == "FLAT"  # Strict: neutral should predict flat
        # Alternative: return True  # Lenient: neutral is always "correct"

    return False


def validate_prediction(db_path: str, prediction_id: int, ticker: str, earnings_date: date):
    """
    Validate a single bias prediction.

    Args:
        db_path: Path to database
        prediction_id: Prediction ID
        ticker: Stock symbol
        earnings_date: Earnings date
    """
    # Get actual move
    move_data = get_actual_move(ticker, earnings_date)

    if move_data is None:
        logger.warning(f"{ticker}: Could not validate - no price data")
        return False

    close_to_close_pct, gap_pct, direction = move_data

    # Get prediction from database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT directional_bias, bias_strength, bias_confidence
        FROM bias_predictions
        WHERE id = ?
    """, (prediction_id,))

    row = cursor.fetchone()
    if not row:
        logger.error(f"Prediction ID {prediction_id} not found")
        conn.close()
        return False

    bias, strength, confidence = row

    # Check if prediction was correct
    correct = is_prediction_correct(bias, direction)

    # Update database with validation
    try:
        cursor.execute("""
            UPDATE bias_predictions
            SET actual_move_pct = ?,
                actual_gap_pct = ?,
                actual_direction = ?,
                prediction_correct = ?,
                validated_at = ?
            WHERE id = ?
        """, (
            close_to_close_pct,
            gap_pct,
            direction,
            1 if correct else 0,
            datetime.now(),
            prediction_id
        ))

        conn.commit()

        result_emoji = "✓" if correct else "✗"
        logger.info(
            f"{ticker}: {result_emoji} Validated - "
            f"predicted {bias}, actual {direction} "
            f"({close_to_close_pct:+.2f}%)"
        )

        return True

    except Exception as e:
        logger.error(f"{ticker}: Failed to update validation - {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_unvalidated_predictions(
    db_path: str,
    ticker: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> list:
    """
    Get predictions that need validation.

    Args:
        db_path: Path to database
        ticker: Optional ticker filter
        start_date: Optional start date filter
        end_date: Optional end date filter

    Returns:
        List of (id, ticker, earnings_date) tuples
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = """
        SELECT id, ticker, earnings_date
        FROM bias_predictions
        WHERE validated_at IS NULL
        AND earnings_date < date('now')
    """
    params = []

    if ticker:
        query += " AND ticker = ?"
        params.append(ticker)

    if start_date:
        query += " AND earnings_date >= ?"
        params.append(start_date)

    if end_date:
        query += " AND earnings_date <= ?"
        params.append(end_date)

    query += " ORDER BY earnings_date"

    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()

    return [(row[0], row[1], datetime.strptime(row[2], '%Y-%m-%d').date())
            for row in results]


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate directional bias predictions"
    )

    parser.add_argument(
        'ticker',
        nargs='?',
        help='Ticker symbol to validate (optional, validates all if not specified)'
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

    # Get config
    config = Config.from_env()
    db_path = str(config.database.path)

    print("=" * 70)
    print("BIAS PREDICTION VALIDATION")
    print("=" * 70)

    # Parse dates if provided
    start_date = datetime.strptime(args.start, '%Y-%m-%d').date() if args.start else None
    end_date = datetime.strptime(args.end, '%Y-%m-%d').date() if args.end else None

    # Get predictions to validate
    predictions = get_unvalidated_predictions(
        db_path,
        ticker=args.ticker,
        start_date=start_date,
        end_date=end_date
    )

    if not predictions:
        print("\n✓ No predictions need validation")
        return 0

    print(f"\nFound {len(predictions)} predictions to validate")
    print("-" * 70)

    # Validate each prediction
    success_count = 0
    fail_count = 0

    for pred_id, ticker, earnings_date in predictions:
        print(f"\n{ticker} - {earnings_date}:")

        if validate_prediction(db_path, pred_id, ticker, earnings_date):
            success_count += 1
        else:
            fail_count += 1

    # Summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    print(f"  Total: {len(predictions)}")
    print(f"  ✓ Validated: {success_count}")
    print(f"  ✗ Failed: {fail_count}")

    # Show quick accuracy stats
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(prediction_correct) as correct
        FROM bias_predictions
        WHERE validated_at IS NOT NULL
    """)

    row = cursor.fetchone()
    if row and row[0] > 0:
        total, correct = row
        accuracy = (correct / total) * 100 if total > 0 else 0
        print(f"\n  Overall Accuracy: {accuracy:.1f}% ({correct}/{total})")

    conn.close()

    print("=" * 70)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
