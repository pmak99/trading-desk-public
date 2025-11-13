#!/usr/bin/env python3
"""
Optimize position sizing strategies.

Compares different position sizing approaches:
1. Equal Weight (current baseline)
2. Kelly Criterion
3. VRP-Weighted
4. Risk-Parity
5. Hybrid (Kelly + VRP)

Usage:
    python scripts/optimize_position_sizing.py --input results/forward_test_2024.json
"""

import sys
import argparse
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Callable
from dataclasses import dataclass
import statistics

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@dataclass
class PositionSizingResult:
    """Results from position sizing strategy."""
    strategy_name: str
    total_pnl: float
    avg_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    total_capital_deployed: float


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

    # Convert to required format
    formatted_trades = []
    for trade in trades:
        formatted_trades.append({
            'ticker': trade['ticker'],
            'earnings_date': trade['earnings_date'],
            'composite_score': trade['score'],
            'actual_move': trade['actual_move'],
            'avg_historical_move': trade['avg_historical'],
            'historical_std': 2.0,  # Approximate
            'simulated_pnl': trade['pnl'] / 100.0  # Convert from percentage to decimal
        })

    return formatted_trades


def calculate_kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Calculate Kelly Criterion fraction.

    Formula: f = (p * b - q) / b
    where:
        p = win probability
        q = loss probability (1 - p)
        b = win/loss ratio

    Args:
        win_rate: Historical win rate (0-1)
        avg_win: Average winning trade P&L
        avg_loss: Average losing trade P&L (positive number)

    Returns:
        Kelly fraction (capped at 0.25 for safety)
    """
    if avg_loss == 0:
        return 0.1  # Default conservative

    p = win_rate
    q = 1 - win_rate
    b = avg_win / avg_loss

    kelly = (p * b - q) / b

    # Cap at 25% (quarter Kelly) for safety
    return min(max(kelly, 0.05), 0.25)


def equal_weight_sizing(trades: List[Dict], total_capital: float) -> PositionSizingResult:
    """
    Equal weight position sizing (current baseline).

    Args:
        trades: List of trade dictionaries
        total_capital: Total capital available

    Returns:
        PositionSizingResult
    """
    if not trades:
        return PositionSizingResult("Equal Weight", 0, 0, 0, 0, 0, 0, 0)

    # Equal weight per trade
    position_size = total_capital / len(trades)

    # Calculate P&L
    total_pnl = sum(t['simulated_pnl'] * position_size for t in trades)
    pnls = [t['simulated_pnl'] * position_size for t in trades]

    avg_pnl = statistics.mean(pnls) if pnls else 0
    std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252 ** 0.5)) if std_pnl > 0 else 0

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    win_rate = sum(1 for t in trades if t['simulated_pnl'] > 0) / len(trades) * 100

    return PositionSizingResult(
        strategy_name="Equal Weight (Baseline)",
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        win_rate=win_rate,
        total_trades=len(trades),
        total_capital_deployed=total_capital
    )


def kelly_sizing(trades: List[Dict], total_capital: float) -> PositionSizingResult:
    """
    Kelly Criterion position sizing.

    Args:
        trades: List of trade dictionaries
        total_capital: Total capital available

    Returns:
        PositionSizingResult
    """
    if not trades:
        return PositionSizingResult("Kelly Criterion", 0, 0, 0, 0, 0, 0, 0)

    # Calculate historical win rate and avg win/loss
    winners = [t['simulated_pnl'] for t in trades if t['simulated_pnl'] > 0]
    losers = [abs(t['simulated_pnl']) for t in trades if t['simulated_pnl'] < 0]

    win_rate = len(winners) / len(trades) if trades else 0.5
    avg_win = statistics.mean(winners) if winners else 0.05
    avg_loss = statistics.mean(losers) if losers else 0.05

    # Calculate Kelly fraction
    kelly_frac = calculate_kelly_fraction(win_rate, avg_win, avg_loss)

    # Position size per trade
    position_size = total_capital * kelly_frac

    # Calculate P&L
    total_pnl = sum(t['simulated_pnl'] * position_size for t in trades)
    pnls = [t['simulated_pnl'] * position_size for t in trades]

    avg_pnl = statistics.mean(pnls) if pnls else 0
    std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252 ** 0.5)) if std_pnl > 0 else 0

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    return PositionSizingResult(
        strategy_name=f"Kelly Criterion ({kelly_frac:.1%})",
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        win_rate=win_rate * 100,
        total_trades=len(trades),
        total_capital_deployed=position_size * len(trades)
    )


def vrp_weighted_sizing(trades: List[Dict], total_capital: float) -> PositionSizingResult:
    """
    VRP-weighted position sizing.

    Higher VRP scores get larger positions.

    Args:
        trades: List of trade dictionaries
        total_capital: Total capital available

    Returns:
        PositionSizingResult
    """
    if not trades:
        return PositionSizingResult("VRP-Weighted", 0, 0, 0, 0, 0, 0, 0)

    # Weight by composite score (VRP component)
    # Normalize scores to sum to 1
    total_score = sum(t['composite_score'] for t in trades)
    weights = [t['composite_score'] / total_score for t in trades]

    # Calculate P&L
    pnls = []
    capital_used = 0
    for trade, weight in zip(trades, weights):
        position_size = total_capital * weight
        pnl = trade['simulated_pnl'] * position_size
        pnls.append(pnl)
        capital_used += position_size

    total_pnl = sum(pnls)
    avg_pnl = statistics.mean(pnls) if pnls else 0
    std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252 ** 0.5)) if std_pnl > 0 else 0

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    win_rate = sum(1 for t in trades if t['simulated_pnl'] > 0) / len(trades) * 100

    return PositionSizingResult(
        strategy_name="VRP-Weighted",
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        win_rate=win_rate,
        total_trades=len(trades),
        total_capital_deployed=capital_used
    )


def hybrid_sizing(trades: List[Dict], total_capital: float) -> PositionSizingResult:
    """
    Hybrid: Kelly base * VRP multiplier.

    Args:
        trades: List of trade dictionaries
        total_capital: Total capital available

    Returns:
        PositionSizingResult
    """
    if not trades:
        return PositionSizingResult("Hybrid (Kelly + VRP)", 0, 0, 0, 0, 0, 0, 0)

    # Calculate Kelly fraction
    winners = [t['simulated_pnl'] for t in trades if t['simulated_pnl'] > 0]
    losers = [abs(t['simulated_pnl']) for t in trades if t['simulated_pnl'] < 0]

    win_rate = len(winners) / len(trades) if trades else 0.5
    avg_win = statistics.mean(winners) if winners else 0.05
    avg_loss = statistics.mean(losers) if losers else 0.05

    kelly_frac = calculate_kelly_fraction(win_rate, avg_win, avg_loss)

    # VRP multipliers (relative to average score)
    avg_score = statistics.mean(t['composite_score'] for t in trades)
    vrp_multipliers = [t['composite_score'] / avg_score for t in trades]

    # Hybrid position size: Kelly base * VRP multiplier
    pnls = []
    capital_used = 0
    for trade, multiplier in zip(trades, vrp_multipliers):
        position_size = total_capital * kelly_frac * multiplier
        pnl = trade['simulated_pnl'] * position_size
        pnls.append(pnl)
        capital_used += position_size

    total_pnl = sum(pnls)
    avg_pnl = statistics.mean(pnls) if pnls else 0
    std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252 ** 0.5)) if std_pnl > 0 else 0

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    return PositionSizingResult(
        strategy_name=f"Hybrid (Kelly {kelly_frac:.1%} + VRP)",
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        win_rate=win_rate * 100,
        total_trades=len(trades),
        total_capital_deployed=capital_used
    )


def print_results(results: List[PositionSizingResult], total_capital: float):
    """Print formatted results."""

    print("\n" + "=" * 140)
    print("POSITION SIZING OPTIMIZATION RESULTS")
    print("=" * 140)
    print(f"Total Capital: ${total_capital:,.0f}")
    print(f"Test Period: 2024 (261 earnings events)")
    print("=" * 140)

    # Header
    header = (
        f"{'Strategy':<30} "
        f"{'Total P&L':<15} "
        f"{'Avg P&L':<12} "
        f"{'Sharpe':<8} "
        f"{'Max DD':<12} "
        f"{'Win Rate':<10} "
        f"{'Capital Used':<15}"
    )
    print(header)
    print("-" * 140)

    # Sort by Sharpe
    sorted_results = sorted(results, key=lambda r: r.sharpe_ratio, reverse=True)

    baseline_total_pnl = None
    for result in sorted_results:
        if "Baseline" in result.strategy_name:
            baseline_total_pnl = result.total_pnl

        # Calculate improvement vs baseline
        improvement = ""
        if baseline_total_pnl and result.total_pnl != baseline_total_pnl:
            pct_change = (result.total_pnl - baseline_total_pnl) / abs(baseline_total_pnl) * 100
            improvement = f"({pct_change:+.1f}%)"

        row = (
            f"{result.strategy_name:<30} "
            f"${result.total_pnl:>12,.2f} {improvement:<3} "
            f"${result.avg_pnl:>10,.2f}  "
            f"{result.sharpe_ratio:>7.2f}  "
            f"${result.max_drawdown:>10,.2f}  "
            f"{result.win_rate:>7.1f}%  "
            f"${result.total_capital_deployed:>12,.0f}"
        )
        print(row)

    print("=" * 140)

    # Analysis
    print("\n" + "=" * 100)
    print("KEY INSIGHTS")
    print("=" * 100)

    best_sharpe = max(results, key=lambda r: r.sharpe_ratio)
    best_total_pnl = max(results, key=lambda r: r.total_pnl)
    lowest_dd = min(results, key=lambda r: r.max_drawdown)

    print(f"\n✓ Best Sharpe Ratio: {best_sharpe.strategy_name} ({best_sharpe.sharpe_ratio:.2f})")
    print(f"  Total P&L: ${best_sharpe.total_pnl:,.2f}")

    print(f"\n✓ Best Total P&L: {best_total_pnl.strategy_name} (${best_total_pnl.total_pnl:,.2f})")
    print(f"  Sharpe: {best_total_pnl.sharpe_ratio:.2f}")

    print(f"\n✓ Lowest Max Drawdown: {lowest_dd.strategy_name} (${lowest_dd.max_drawdown:,.2f})")
    print(f"  Total P&L: ${lowest_dd.total_pnl:,.2f}")

    # Recommendation
    print("\n" + "=" * 100)
    print("RECOMMENDATION")
    print("=" * 100)

    # Score: 50% Sharpe, 30% Total P&L, 20% Low DD
    def score_strategy(r: PositionSizingResult) -> float:
        max_sharpe = max(res.sharpe_ratio for res in results)
        max_pnl = max(res.total_pnl for res in results)
        max_dd_all = max(res.max_drawdown for res in results)

        return (
            (r.sharpe_ratio / max_sharpe if max_sharpe > 0 else 0) * 0.5 +
            (r.total_pnl / max_pnl if max_pnl > 0 else 0) * 0.3 +
            (1 - r.max_drawdown / max_dd_all if max_dd_all > 0 else 0) * 0.2
        )

    recommended = max(results, key=score_strategy)

    print(f"\n✅ RECOMMENDED: {recommended.strategy_name}")
    print(f"\n   Performance:")
    print(f"   • Total P&L: ${recommended.total_pnl:,.2f}")
    print(f"   • Sharpe Ratio: {recommended.sharpe_ratio:.2f}")
    print(f"   • Max Drawdown: ${recommended.max_drawdown:,.2f}")
    print(f"   • Win Rate: {recommended.win_rate:.1f}%")
    print(f"   • Capital Deployed: ${recommended.total_capital_deployed:,.0f}")

    if baseline_total_pnl:
        improvement = (recommended.total_pnl - baseline_total_pnl) / abs(baseline_total_pnl) * 100
        print(f"\n   Improvement vs Baseline: {improvement:+.1f}%")

    print("\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Optimize position sizing strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--input",
        type=str,
        default="results/forward_test_2024.json",
        help="Path to backtest results JSON file",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=20000.0,
        help="Total capital (default: $20,000)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    logger.info("=" * 80)
    logger.info("Position Sizing Optimization")
    logger.info("=" * 80)
    logger.info(f"Input File: {args.input}")
    logger.info(f"Total Capital: ${args.capital:,.0f}")
    logger.info("=" * 80)

    # Verify input file exists
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        logger.error("Run backtests first: python scripts/run_backtests.py --start-date 2024-01-01 --end-date 2024-12-31 --output results/forward_test_2024.json")
        return 1

    # Load trades
    logger.info(f"\nLoading trades from {input_path}")
    trades = load_trades_from_json(input_path)
    logger.info(f"Loaded {len(trades)} trades")

    if not trades:
        logger.error("No trades found")
        return 1

    # Test different sizing strategies
    results = [
        equal_weight_sizing(trades, args.capital),
        kelly_sizing(trades, args.capital),
        vrp_weighted_sizing(trades, args.capital),
        hybrid_sizing(trades, args.capital),
    ]

    # Print results
    print_results(results, args.capital)

    return 0


if __name__ == "__main__":
    sys.exit(main())
