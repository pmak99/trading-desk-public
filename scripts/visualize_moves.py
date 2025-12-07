#!/usr/bin/env python3
"""
Visualize historical earnings moves for a ticker.
Generates ASCII charts and statistical analysis.
"""

import sys
import sqlite3
from pathlib import Path
from collections import defaultdict
import statistics


def get_db_path() -> Path:
    """Get path to ivcrush database."""
    return Path(__file__).parent.parent / "2.0" / "data" / "ivcrush.db"


def get_historical_moves(ticker: str, limit: int = 20) -> list[dict]:
    """Fetch historical moves from database."""
    db_path = get_db_path()

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return []

    query = """
        SELECT ticker, earnings_date,
               close_move_pct as actual_move_pct,
               CASE WHEN earnings_close > prev_close THEN 'UP' ELSE 'DOWN' END as direction,
               prev_close as close_before, earnings_close as close_after
        FROM historical_moves
        WHERE ticker = ?
        ORDER BY earnings_date DESC
        LIMIT ?
    """

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, (ticker.upper(), limit))
        results = [dict(row) for row in cursor.fetchall()]

    return results


def ascii_histogram(values: list[float], bins: int = 10, width: int = 40) -> str:
    """Generate ASCII histogram."""
    if not values:
        return "No data"

    min_val = min(values)
    max_val = max(values)
    range_val = max_val - min_val

    if range_val == 0:
        return f"All values: {min_val:.1f}%"

    bin_width = range_val / bins
    bin_counts = defaultdict(int)

    for v in values:
        bin_idx = min(int((v - min_val) / bin_width), bins - 1)
        bin_counts[bin_idx] += 1

    max_count = max(bin_counts.values()) if bin_counts else 1

    lines = []
    for i in range(bins):
        bin_start = min_val + i * bin_width
        bin_end = bin_start + bin_width
        count = bin_counts.get(i, 0)
        bar_len = int((count / max_count) * width) if max_count > 0 else 0
        bar = '█' * bar_len
        lines.append(f"{bin_start:5.1f}-{bin_end:5.1f}% │{bar} ({count})")

    return '\n'.join(lines)


def ascii_sparkline(values: list[float], width: int = 20) -> str:
    """Generate ASCII sparkline."""
    if not values or len(values) < 2:
        return ""

    min_val = min(values)
    max_val = max(values)
    range_val = max_val - min_val

    if range_val == 0:
        return "▄" * min(len(values), width)

    chars = "▁▂▃▄▅▆▇█"

    # Sample if too many values
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    line = ""
    for v in sampled:
        idx = int((v - min_val) / range_val * (len(chars) - 1))
        line += chars[idx]

    return line


def analyze_ticker(ticker: str, implied_move: float = None):
    """Analyze a ticker's historical moves."""
    moves = get_historical_moves(ticker, limit=40)

    if not moves:
        print(f"No historical data found for {ticker}")
        return

    move_pcts = [m['actual_move_pct'] for m in moves]

    print(f"\n{'='*60}")
    print(f"HISTORICAL EARNINGS MOVES: {ticker.upper()}")
    print(f"{'='*60}")

    # Statistics
    mean = statistics.mean(move_pcts)
    median = statistics.median(move_pcts)
    stdev = statistics.stdev(move_pcts) if len(move_pcts) > 1 else 0

    print(f"\nStatistics (last {len(moves)} earnings):")
    print(f"  Mean:   {mean:.2f}%")
    print(f"  Median: {median:.2f}%")
    print(f"  Std Dev: {stdev:.2f}%")
    print(f"  Min:    {min(move_pcts):.2f}%")
    print(f"  Max:    {max(move_pcts):.2f}%")

    # Direction breakdown
    up_moves = [m for m in moves if m['direction'] == 'UP']
    down_moves = [m for m in moves if m['direction'] == 'DOWN']

    print(f"\nDirection:")
    print(f"  UP:   {len(up_moves)} ({len(up_moves)/len(moves)*100:.0f}%)")
    print(f"  DOWN: {len(down_moves)} ({len(down_moves)/len(moves)*100:.0f}%)")

    # Implied move comparison
    if implied_move:
        wins = len([m for m in move_pcts if m < implied_move])
        win_rate = wins / len(move_pcts) * 100
        vrp = implied_move / mean if mean > 0 else 0

        print(f"\nVRP Analysis:")
        print(f"  Current Implied Move: {implied_move:.2f}%")
        print(f"  VRP Ratio: {vrp:.2f}x")
        print(f"  Historical Win Rate: {win_rate:.0f}% (moves < implied)")

    # Histogram
    print(f"\nMove Distribution:")
    print(ascii_histogram(move_pcts, bins=8, width=30))

    # Trend sparkline
    print(f"\nRecent Trend (newest → oldest):")
    print(f"  {ascii_sparkline(move_pcts[:20], width=20)}")
    print(f"  └─ Range: {min(move_pcts):.1f}% to {max(move_pcts):.1f}%")

    # Recent moves table
    print(f"\nRecent Earnings:")
    print(f"  {'Date':<12} {'Move':>8} {'Direction':>10}")
    print(f"  {'-'*32}")
    for m in moves[:10]:
        print(f"  {m['earnings_date']:<12} {m['actual_move_pct']:>7.2f}% {m['direction']:>10}")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python visualize_moves.py TICKER [implied_move]")
        print("Example: python visualize_moves.py NVDA 12.5")
        sys.exit(1)

    ticker = sys.argv[1].upper()
    implied_move = None

    if len(sys.argv) > 2:
        try:
            implied_move = float(sys.argv[2])
        except ValueError:
            print(f"Error: Invalid implied move '{sys.argv[2]}'. Must be a number.")
            sys.exit(1)

    analyze_ticker(ticker, implied_move)


if __name__ == "__main__":
    main()
