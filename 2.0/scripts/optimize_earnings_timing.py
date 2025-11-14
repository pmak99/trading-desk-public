#!/usr/bin/env python3
"""
Earnings Timing Analysis - BMO vs AMC performance comparison.

Analyzes IV Crush performance based on earnings announcement timing:
- BMO (Before Market Open): Announced before 9:30 AM ET
- AMC (After Market Close): Announced after 4:00 PM ET

Hypothesis: AMC earnings may have better IV crush because options
decay overnight, while BMO has intraday volatility.

Usage:
    python scripts/optimize_earnings_timing.py --input results/forward_test_2024.json
"""

import sys
import argparse
import logging
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import statistics

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@dataclass
class TimingMetrics:
    """Performance metrics for an earnings timing."""
    timing: str
    trade_count: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    avg_score: float
    avg_actual_move: float
    avg_predicted_move: float


def load_trades_from_json(json_path: Path) -> List[Dict]:
    """
    Load backtest trades from JSON file.

    Args:
        json_path: Path to JSON results file

    Returns:
        List of trade dictionaries
    """
    with open(json_path, 'r') as f:
        data = json.load(f)

    # Get best performing config (highest Sharpe)
    best_config = max(data, key=lambda x: x['metrics']['sharpe_ratio'])

    logger.info(f"Using config: {best_config['config_name']}")
    logger.info(f"Sharpe: {best_config['metrics']['sharpe_ratio']:.2f}, Win Rate: {best_config['metrics']['win_rate']:.1f}%")

    # Extract trades
    trades = best_config['trades']

    return trades


def calculate_sharpe(pnls: List[float]) -> float:
    """Calculate Sharpe ratio from list of P&Ls."""
    if not pnls or len(pnls) < 2:
        return 0.0

    avg = statistics.mean(pnls)
    std = statistics.stdev(pnls)

    if std == 0:
        return 0.0

    # Annualized Sharpe (assuming ~50 trades per year)
    return (avg / std) * (50 ** 0.5)


def calculate_max_drawdown(pnls: List[float]) -> float:
    """Calculate maximum drawdown from cumulative P&L."""
    if not pnls:
        return 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_dd = max(max_dd, drawdown)

    return max_dd


def load_timing_data(db_path: Path) -> Dict[tuple, str]:
    """
    Load earnings timing data from database.

    Args:
        db_path: Path to SQLite database

    Returns:
        Dictionary mapping (ticker, earnings_date) -> timing
    """
    timing_map = {}

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT ticker, earnings_date, timing
            FROM earnings_calendar
            WHERE timing IS NOT NULL
        """)

        for row in cursor.fetchall():
            ticker, earnings_date, timing = row
            timing_map[(ticker, earnings_date)] = timing

        conn.close()

        logger.info(f"✓ Loaded timing data for {len(timing_map)} earnings events")

    except Exception as e:
        logger.error(f"Failed to load timing data: {e}")

    return timing_map


def infer_earnings_timing(trade: Dict, timing_map: Dict[tuple, str]) -> Optional[str]:
    """
    Infer earnings timing (BMO/AMC) from trade data.

    Args:
        trade: Trade dictionary
        timing_map: Dictionary mapping (ticker, date) -> timing

    Returns:
        'BMO', 'AMC', or None if unknown
    """
    # Check if timing is in the trade data
    if 'timing' in trade:
        return trade['timing']

    # Look up from timing map
    ticker = trade['ticker']
    earnings_date = trade['earnings_date']

    return timing_map.get((ticker, earnings_date))


def analyze_timing_performance(trades: List[Dict], timing_map: Dict[tuple, str]) -> Dict[str, TimingMetrics]:
    """
    Analyze trade performance by earnings timing.

    Args:
        trades: List of trade dictionaries
        timing_map: Dictionary mapping (ticker, date) -> timing

    Returns:
        Dictionary mapping timing to metrics
    """
    # Segment trades by timing
    timings = {
        'BMO': [],
        'AMC': [],
        'Unknown': []
    }

    for trade in trades:
        timing = infer_earnings_timing(trade, timing_map)

        if timing in ['BMO', 'AMC']:
            timings[timing].append(trade)
        else:
            timings['Unknown'].append(trade)

    # Calculate metrics for each timing
    results = {}

    for timing_name, timing_trades in timings.items():
        if not timing_trades:
            continue

        # Calculate metrics
        pnls = [t['pnl'] / 100.0 for t in timing_trades]  # Convert to decimal
        wins = [p for p in pnls if p > 0]

        win_rate = (len(wins) / len(pnls)) * 100 if pnls else 0
        avg_pnl = statistics.mean(pnls) * 100  # Back to percentage for display
        total_pnl = sum(pnls) * 100
        sharpe = calculate_sharpe(pnls)
        max_dd = calculate_max_drawdown(pnls) * 100
        avg_score = statistics.mean(t['score'] for t in timing_trades)
        avg_actual = statistics.mean(t['actual_move'] for t in timing_trades)
        avg_predicted = statistics.mean(t['avg_historical'] for t in timing_trades)

        results[timing_name] = TimingMetrics(
            timing=timing_name,
            trade_count=len(timing_trades),
            win_rate=win_rate,
            avg_pnl=avg_pnl,
            total_pnl=total_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            avg_score=avg_score,
            avg_actual_move=avg_actual,
            avg_predicted_move=avg_predicted
        )

    return results


def print_timing_analysis(results: Dict[str, TimingMetrics]):
    """Print timing analysis results in a formatted table."""

    print("\n" + "="*80)
    print("EARNINGS TIMING ANALYSIS - BMO vs AMC")
    print("="*80)

    # Overall summary
    total_trades = sum(r.trade_count for r in results.values())
    print(f"\n📊 Total Trades Analyzed: {total_trades}")

    # Print each timing
    for timing_name in ['BMO', 'AMC', 'Unknown']:
        if timing_name not in results:
            continue

        r = results[timing_name]

        print(f"\n{'─'*80}")
        print(f"🎯 {r.timing} EARNINGS")
        print(f"{'─'*80}")
        print(f"  Trades:          {r.trade_count} ({r.trade_count/total_trades*100:.1f}% of total)")
        print(f"  Win Rate:        {r.win_rate:.1f}%")
        print(f"  Avg P&L/Trade:   {r.avg_pnl:+.2f}%")
        print(f"  Total P&L:       {r.total_pnl:+.2f}%")
        print(f"  Sharpe Ratio:    {r.sharpe_ratio:.2f}")
        print(f"  Max Drawdown:    {r.max_drawdown:.2f}%")
        print(f"  Avg Score:       {r.avg_score:.1f}")
        print(f"  Avg Actual Move: {r.avg_actual_move:.2f}%")
        print(f"  Avg Pred Move:   {r.avg_predicted_move:.2f}%")

    print(f"\n{'='*80}")

    # Analysis and recommendations
    if 'BMO' in results and 'AMC' in results:
        bmo = results['BMO']
        amc = results['AMC']

        print("\n📈 KEY INSIGHTS:")
        print(f"\n🔍 BMO vs AMC COMPARISON:")
        print(f"  • Trade Count: BMO={bmo.trade_count}, AMC={amc.trade_count}")
        print(f"  • Win Rate: BMO={bmo.win_rate:.1f}%, AMC={amc.win_rate:.1f}% (Δ{amc.win_rate-bmo.win_rate:+.1f}%)")
        print(f"  • Avg P&L: BMO={bmo.avg_pnl:+.2f}%, AMC={amc.avg_pnl:+.2f}% (Δ{amc.avg_pnl-bmo.avg_pnl:+.2f}%)")
        print(f"  • Sharpe: BMO={bmo.sharpe_ratio:.2f}, AMC={amc.sharpe_ratio:.2f} (Δ{amc.sharpe_ratio-bmo.sharpe_ratio:+.2f})")
        print(f"  • Actual Move: BMO={bmo.avg_actual_move:.2f}%, AMC={amc.avg_actual_move:.2f}% (Δ{amc.avg_actual_move-bmo.avg_actual_move:+.2f}%)")

        # Determine which is better
        better_timing = "AMC" if amc.sharpe_ratio > bmo.sharpe_ratio else "BMO"
        print(f"\n💡 WINNER: {better_timing} earnings show better risk-adjusted returns")

    print(f"\n{'='*80}\n")


def generate_recommendations(results: Dict[str, TimingMetrics]) -> List[str]:
    """Generate actionable recommendations based on timing analysis."""
    recommendations = []

    # Check if we have both BMO and AMC data
    if 'BMO' not in results or 'AMC' not in results:
        recommendations.append("⚠️  Insufficient timing data. Trades may not have BMO/AMC labels.")
        return recommendations

    bmo = results['BMO']
    amc = results['AMC']

    # Check sample size
    if bmo.trade_count < 3 or amc.trade_count < 3:
        recommendations.append(
            f"⚠️  Small sample size (BMO={bmo.trade_count}, AMC={amc.trade_count}). "
            f"Need 5+ trades per timing for reliable conclusions."
        )

    # Win rate difference
    win_rate_diff = abs(amc.win_rate - bmo.win_rate)
    if win_rate_diff > 15:
        better_timing = "AMC" if amc.win_rate > bmo.win_rate else "BMO"
        recommendations.append(
            f"✅ Significantly better win rate for {better_timing} ({win_rate_diff:.1f}% difference). "
            f"Consider favoring {better_timing} earnings when selecting trades."
        )

    # P&L difference
    pnl_diff = abs(amc.avg_pnl - bmo.avg_pnl)
    if pnl_diff > 2.0:
        better_timing = "AMC" if amc.avg_pnl > bmo.avg_pnl else "BMO"
        recommendations.append(
            f"💰 Higher average P&L for {better_timing} ({pnl_diff:+.2f}%). "
            f"Strategy performs better with {better_timing} timing."
        )

    # Sharpe difference
    sharpe_diff = abs(amc.sharpe_ratio - bmo.sharpe_ratio)
    if sharpe_diff > 0.3:
        better_timing = "AMC" if amc.sharpe_ratio > bmo.sharpe_ratio else "BMO"
        recommendations.append(
            f"✅ Superior risk-adjusted returns for {better_timing} (Sharpe Δ{sharpe_diff:.2f}). "
            f"Prioritize {better_timing} earnings in trade selection."
        )

    # Actual move comparison
    move_diff = abs(amc.avg_actual_move - bmo.avg_actual_move)
    if move_diff > 1.0:
        larger_move = "AMC" if amc.avg_actual_move > bmo.avg_actual_move else "BMO"
        recommendations.append(
            f"📊 {larger_move} earnings have larger actual moves ({move_diff:.2f}% difference). "
            f"More volatility = more risk but potentially higher returns."
        )

    # If no significant differences
    if not recommendations or all("⚠️" in r for r in recommendations):
        recommendations.append(
            "✅ Performance is relatively consistent between BMO and AMC. "
            "No timing-specific adjustments needed at this time."
        )

    return recommendations


def save_results(results: Dict[str, TimingMetrics], output_path: Path):
    """Save timing analysis results to JSON file."""
    output = {
        'analysis_date': datetime.now().isoformat(),
        'timings': {}
    }

    for timing_name, metrics in results.items():
        output['timings'][timing_name] = {
            'trade_count': metrics.trade_count,
            'win_rate': metrics.win_rate,
            'avg_pnl': metrics.avg_pnl,
            'total_pnl': metrics.total_pnl,
            'sharpe_ratio': metrics.sharpe_ratio,
            'max_drawdown': metrics.max_drawdown,
            'avg_score': metrics.avg_score,
            'avg_actual_move': metrics.avg_actual_move,
            'avg_predicted_move': metrics.avg_predicted_move
        }

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    logger.info(f"✓ Results saved to {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Analyze IV Crush performance by earnings timing')
    parser.add_argument('--input', '-i', type=Path, required=True,
                      help='Path to forward test results JSON')
    parser.add_argument('--output', '-o', type=Path,
                      default=Path('results/timing_analysis.json'),
                      help='Output path for results')
    parser.add_argument('--db', type=Path,
                      default=Path('data/ivcrush.db'),
                      help='Path to SQLite database')

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    logger.info("="*80)
    logger.info("EARNINGS TIMING ANALYSIS (BMO vs AMC)")
    logger.info("="*80)

    # Load trades
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        return 1

    trades = load_trades_from_json(args.input)
    logger.info(f"Loaded {len(trades)} trades from {args.input}")

    # Load timing data from database
    if not args.db.exists():
        logger.error(f"Database not found: {args.db}")
        return 1

    timing_map = load_timing_data(args.db)

    # Analyze by timing
    results = analyze_timing_performance(trades, timing_map)

    if not results:
        logger.error("No timing results generated. Check if trades have 'timing' field.")
        return 1

    # Print analysis
    print_timing_analysis(results)

    # Generate recommendations
    recommendations = generate_recommendations(results)
    print("\n💡 RECOMMENDATIONS:")
    for i, rec in enumerate(recommendations, 1):
        print(f"\n{i}. {rec}")

    # Save results
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_results(results, args.output)

    logger.info("\n✓ Earnings timing analysis complete!")

    return 0


if __name__ == '__main__':
    sys.exit(main())
