#!/usr/bin/env python3
"""
Generate accuracy report for directional bias predictions.

This script analyzes validated predictions and generates comprehensive
accuracy statistics by strength level, confidence bucket, and more.

Usage:
    # Generate full report
    python scripts/bias_accuracy_report.py

    # Report for specific date range
    python scripts/bias_accuracy_report.py --start 2025-11-01 --end 2025-11-30

    # Update statistics table
    python scripts/bias_accuracy_report.py --update-stats
"""

import sys
import argparse
import logging
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.config import Config

logger = logging.getLogger(__name__)


def get_accuracy_stats(
    db_path: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> dict:
    """
    Calculate accuracy statistics for bias predictions.

    Returns:
        Dictionary with comprehensive statistics
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Build query with optional date filters
    where_clause = "WHERE validated_at IS NOT NULL"
    params = []

    if start_date:
        where_clause += " AND earnings_date >= ?"
        params.append(start_date)

    if end_date:
        where_clause += " AND earnings_date <= ?"
        params.append(end_date)

    # Overall stats
    cursor.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(prediction_correct) as correct
        FROM bias_predictions
        {where_clause}
    """, params)

    row = cursor.fetchone()
    total = row[0] if row else 0
    correct = row[1] if row else 0
    overall_accuracy = (correct / total * 100) if total > 0 else 0

    # By strength level
    strength_stats = {}
    for strength in [0, 1, 2, 3]:
        cursor.execute(f"""
            SELECT
                COUNT(*) as total,
                SUM(prediction_correct) as correct
            FROM bias_predictions
            {where_clause}
            AND bias_strength = ?
        """, params + [strength])

        row = cursor.fetchone()
        if row:
            s_total, s_correct = row
            strength_stats[strength] = {
                'total': s_total,
                'correct': s_correct or 0,
                'accuracy': (s_correct / s_total * 100) if s_total > 0 else 0
            }

    # By confidence bucket
    confidence_buckets = {
        'high': (0.7, 1.0),
        'medium': (0.3, 0.7),
        'low': (0.0, 0.3)
    }

    confidence_stats = {}
    for bucket_name, (min_conf, max_conf) in confidence_buckets.items():
        cursor.execute(f"""
            SELECT
                COUNT(*) as total,
                SUM(prediction_correct) as correct
            FROM bias_predictions
            {where_clause}
            AND bias_confidence > ? AND bias_confidence <= ?
        """, params + [min_conf, max_conf])

        row = cursor.fetchone()
        if row:
            c_total, c_correct = row
            confidence_stats[bucket_name] = {
                'total': c_total,
                'correct': c_correct or 0,
                'accuracy': (c_correct / c_total * 100) if c_total > 0 else 0
            }

    # By bias direction
    direction_stats = {}
    for prefix in ['bullish', 'bearish', 'neutral']:
        cursor.execute(f"""
            SELECT
                COUNT(*) as total,
                SUM(prediction_correct) as correct
            FROM bias_predictions
            {where_clause}
            AND directional_bias LIKE ?
        """, params + [f'%{prefix}%'])

        row = cursor.fetchone()
        if row:
            d_total, d_correct = row
            direction_stats[prefix] = {
                'total': d_total,
                'correct': d_correct or 0,
                'accuracy': (d_correct / d_total * 100) if d_total > 0 else 0
            }

    # Recent predictions (last 20)
    cursor.execute(f"""
        SELECT
            ticker,
            earnings_date,
            directional_bias,
            bias_strength,
            bias_confidence,
            actual_direction,
            actual_move_pct,
            prediction_correct
        FROM bias_predictions
        WHERE validated_at IS NOT NULL
        ORDER BY earnings_date DESC
        LIMIT 20
    """)

    recent = cursor.fetchall()

    conn.close()

    return {
        'overall': {
            'total': total,
            'correct': correct,
            'accuracy': overall_accuracy
        },
        'by_strength': strength_stats,
        'by_confidence': confidence_stats,
        'by_direction': direction_stats,
        'recent_predictions': recent
    }


def print_report(stats: dict):
    """Print formatted accuracy report."""
    print("\n" + "=" * 70)
    print("DIRECTIONAL BIAS PREDICTION ACCURACY REPORT")
    print("=" * 70)

    # Overall
    overall = stats['overall']
    print(f"\n📊 OVERALL PERFORMANCE")
    print(f"   Total Predictions: {overall['total']}")
    print(f"   Correct: {overall['correct']}")
    print(f"   Accuracy: {overall['accuracy']:.1f}%")

    # By strength
    print(f"\n📈 BY BIAS STRENGTH")
    print(f"   {'Level':<15} {'Name':<15} {'Total':<8} {'Correct':<8} {'Accuracy'}")
    print(f"   {'-'*60}")

    strength_names = {0: 'NEUTRAL', 1: 'WEAK', 2: 'MODERATE', 3: 'STRONG'}
    for strength in [3, 2, 1, 0]:
        if strength in stats['by_strength']:
            data = stats['by_strength'][strength]
            name = strength_names[strength]
            print(f"   {strength:<15} {name:<15} {data['total']:<8} "
                  f"{data['correct']:<8} {data['accuracy']:.1f}%")

    # By confidence
    print(f"\n🎯 BY CONFIDENCE LEVEL")
    print(f"   {'Bucket':<15} {'Range':<15} {'Total':<8} {'Correct':<8} {'Accuracy'}")
    print(f"   {'-'*60}")

    for bucket_name in ['high', 'medium', 'low']:
        if bucket_name in stats['by_confidence']:
            data = stats['by_confidence'][bucket_name]
            range_str = '>0.7' if bucket_name == 'high' else \
                       '0.3-0.7' if bucket_name == 'medium' else '<0.3'
            print(f"   {bucket_name.upper():<15} {range_str:<15} {data['total']:<8} "
                  f"{data['correct']:<8} {data['accuracy']:.1f}%")

    # By direction
    print(f"\n🧭 BY DIRECTIONAL BIAS")
    print(f"   {'Direction':<15} {'Total':<8} {'Correct':<8} {'Accuracy'}")
    print(f"   {'-'*60}")

    for direction in ['bullish', 'bearish', 'neutral']:
        if direction in stats['by_direction']:
            data = stats['by_direction'][direction]
            print(f"   {direction.upper():<15} {data['total']:<8} "
                  f"{data['correct']:<8} {data['accuracy']:.1f}%")

    # Recent predictions
    print(f"\n📋 RECENT PREDICTIONS (Last 20)")
    print(f"   {'Ticker':<8} {'Date':<12} {'Predicted':<18} {'Actual':<8} "
          f"{'Move':<8} {'✓/✗'}")
    print(f"   {'-'*70}")

    for pred in stats['recent_predictions']:
        ticker, earnings_date, bias, strength, confidence, actual_dir, move, correct = pred
        result = "✓" if correct else "✗"
        bias_short = bias.replace('_', ' ').upper()[:16]
        move_str = f"{move:+.1f}%" if move else "N/A"

        print(f"   {ticker:<8} {earnings_date:<12} {bias_short:<18} "
              f"{actual_dir:<8} {move_str:<8} {result}")

    print("\n" + "=" * 70)


def update_stats_table(db_path: str):
    """
    Update the bias_accuracy_stats table with current statistics.

    This creates a snapshot of accuracy metrics for historical tracking.
    """
    stats = get_accuracy_stats(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Extract data from stats
        overall = stats['overall']
        by_strength = stats['by_strength']
        by_confidence = stats['by_confidence']

        cursor.execute("""
            INSERT INTO bias_accuracy_stats (
                calculated_at,
                total_predictions, total_validated,
                strong_predictions, strong_correct, strong_accuracy,
                moderate_predictions, moderate_correct, moderate_accuracy,
                weak_predictions, weak_correct, weak_accuracy,
                neutral_predictions, neutral_correct, neutral_accuracy,
                high_confidence_predictions, high_confidence_correct, high_confidence_accuracy,
                med_confidence_predictions, med_confidence_correct, med_confidence_accuracy,
                low_confidence_predictions, low_confidence_correct, low_confidence_accuracy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(),
            overall['total'], overall['total'],

            by_strength.get(3, {}).get('total', 0),
            by_strength.get(3, {}).get('correct', 0),
            by_strength.get(3, {}).get('accuracy', 0),

            by_strength.get(2, {}).get('total', 0),
            by_strength.get(2, {}).get('correct', 0),
            by_strength.get(2, {}).get('accuracy', 0),

            by_strength.get(1, {}).get('total', 0),
            by_strength.get(1, {}).get('correct', 0),
            by_strength.get(1, {}).get('accuracy', 0),

            by_strength.get(0, {}).get('total', 0),
            by_strength.get(0, {}).get('correct', 0),
            by_strength.get(0, {}).get('accuracy', 0),

            by_confidence.get('high', {}).get('total', 0),
            by_confidence.get('high', {}).get('correct', 0),
            by_confidence.get('high', {}).get('accuracy', 0),

            by_confidence.get('medium', {}).get('total', 0),
            by_confidence.get('medium', {}).get('correct', 0),
            by_confidence.get('medium', {}).get('accuracy', 0),

            by_confidence.get('low', {}).get('total', 0),
            by_confidence.get('low', {}).get('correct', 0),
            by_confidence.get('low', {}).get('accuracy', 0),
        ))

        conn.commit()
        logger.info("Updated bias_accuracy_stats table")

    except Exception as e:
        logger.error(f"Failed to update stats table: {e}")
        conn.rollback()
    finally:
        conn.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate bias prediction accuracy report"
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
    parser.add_argument(
        '--update-stats',
        action='store_true',
        help='Update bias_accuracy_stats table'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    # Get config
    config = Config.from_env()
    db_path = str(config.database.path)

    # Parse dates if provided
    start_date = datetime.strptime(args.start, '%Y-%m-%d').date() if args.start else None
    end_date = datetime.strptime(args.end, '%Y-%m-%d').date() if args.end else None

    # Get and print stats
    stats = get_accuracy_stats(db_path, start_date, end_date)
    print_report(stats)

    # Update stats table if requested
    if args.update_stats:
        print("\nUpdating bias_accuracy_stats table...")
        update_stats_table(db_path)
        print("✓ Stats table updated")

    return 0


if __name__ == "__main__":
    sys.exit(main())
