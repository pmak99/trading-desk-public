#!/usr/bin/env python3
"""Query strategy-level statistics from the database."""

import sqlite3
import sys
from pathlib import Path


def print_strategy_stats(db_path: str):
    """Print strategy-level performance statistics."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("\n" + "=" * 70)
    print("STRATEGY PERFORMANCE (Accurate Win Rates)")
    print("=" * 70)

    # By strategy type
    cursor.execute("""
        SELECT strategy_type,
               COUNT(*) as trades,
               SUM(CASE WHEN is_winner THEN 1 ELSE 0 END) as winners,
               ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
               ROUND(SUM(gain_loss), 2) as total_pnl
        FROM strategies
        GROUP BY strategy_type
        ORDER BY total_pnl DESC
    """)

    print("\nBy Strategy Type:")
    print(f"{'Type':<15} {'Trades':>8} {'Winners':>8} {'Win Rate':>10} {'P&L':>15}")
    print("-" * 60)

    for row in cursor.fetchall():
        strategy_type, trades, winners, win_rate, total_pnl = row
        print(f"{strategy_type:<15} {trades:>8} {winners:>8} {win_rate:>9.1f}% ${total_pnl:>14,.2f}")

    # Monthly
    cursor.execute("""
        SELECT strftime('%Y-%m', sale_date) as month,
               COUNT(*) as trades,
               ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
               ROUND(SUM(gain_loss), 2) as pnl
        FROM strategies
        GROUP BY month
        ORDER BY month DESC
        LIMIT 12
    """)

    print("\nMonthly Performance (Last 12 Months):")
    print(f"{'Month':<10} {'Trades':>8} {'Win Rate':>10} {'P&L':>15}")
    print("-" * 50)

    for row in cursor.fetchall():
        month, trades, win_rate, pnl = row
        print(f"{month:<10} {trades:>8} {win_rate:>9.1f}% ${pnl:>14,.2f}")

    # Overall
    cursor.execute("""
        SELECT COUNT(*) as trades,
               ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
               ROUND(SUM(gain_loss), 2) as total_pnl
        FROM strategies
    """)

    row = cursor.fetchone()
    print(f"\nOverall: {row[0]} trades, {row[1]}% win rate, ${row[2]:,.2f} P&L")

    conn.close()


if __name__ == "__main__":
    project_root = Path(__file__).parent.parent
    db_path = project_root / "2.0" / "data" / "ivcrush.db"
    print_strategy_stats(str(db_path))
