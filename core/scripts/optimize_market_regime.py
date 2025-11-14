#!/usr/bin/env python3
"""
Market Regime Analysis - VIX-based trade segmentation.

Analyzes IV Crush performance across different market volatility regimes:
- Low Vol: VIX < 15
- Normal Vol: 15 <= VIX < 25
- High Vol: VIX >= 25

Usage:
    python scripts/optimize_market_regime.py --input results/forward_test_2024.json
"""

import sys
import argparse
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import statistics

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@dataclass
class RegimeMetrics:
    """Performance metrics for a market regime."""
    regime_name: str
    vix_range: str
    trade_count: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    avg_score: float
    avg_vix: float


def fetch_vix_data(start_date: str, end_date: str) -> Dict[str, float]:
    """
    Fetch historical VIX data using yfinance.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)

    Returns:
        Dictionary mapping date (YYYY-MM-DD) to VIX close price
    """
    try:
        import yfinance as yf

        logger.info(f"Fetching VIX data from {start_date} to {end_date}...")

        vix = yf.Ticker("^VIX")
        hist = vix.history(start=start_date, end=end_date)

        # Convert to dict mapping date -> close price
        vix_data = {}
        for date, row in hist.iterrows():
            date_str = date.strftime('%Y-%m-%d')
            vix_data[date_str] = row['Close']

        logger.info(f"✓ Fetched VIX data for {len(vix_data)} days")
        return vix_data

    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return {}
    except Exception as e:
        logger.error(f"Failed to fetch VIX data: {e}")
        return {}


def get_vix_at_date(vix_data: Dict[str, float], target_date: str) -> Optional[float]:
    """
    Get VIX value at a specific date, with fallback to previous trading day.

    Args:
        vix_data: VIX data dictionary
        target_date: Target date (YYYY-MM-DD)

    Returns:
        VIX close price, or None if not found
    """
    # Try exact date
    if target_date in vix_data:
        return vix_data[target_date]

    # Try previous 5 days (in case of weekends/holidays)
    target = datetime.strptime(target_date, '%Y-%m-%d')
    for i in range(1, 6):
        prev_date = (target - timedelta(days=i)).strftime('%Y-%m-%d')
        if prev_date in vix_data:
            logger.debug(f"Using VIX from {prev_date} for {target_date}")
            return vix_data[prev_date]

    logger.warning(f"No VIX data found near {target_date}")
    return None


def classify_regime(vix: float) -> str:
    """
    Classify market regime based on VIX level.

    Args:
        vix: VIX close price

    Returns:
        Regime name: 'low_vol', 'normal', or 'high_vol'
    """
    if vix < 15:
        return 'low_vol'
    elif vix < 25:
        return 'normal'
    else:
        return 'high_vol'


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


def analyze_regime_performance(trades: List[Dict], vix_data: Dict[str, float]) -> Dict[str, RegimeMetrics]:
    """
    Analyze trade performance by market regime.

    Args:
        trades: List of trade dictionaries
        vix_data: VIX historical data

    Returns:
        Dictionary mapping regime name to metrics
    """
    # Segment trades by regime
    regimes = {
        'low_vol': [],
        'normal': [],
        'high_vol': []
    }

    trades_with_vix = []

    for trade in trades:
        # Get VIX at earnings date
        earnings_date = trade['earnings_date']
        vix = get_vix_at_date(vix_data, earnings_date)

        if vix is None:
            logger.warning(f"Skipping {trade['ticker']} - no VIX data for {earnings_date}")
            continue

        regime = classify_regime(vix)
        trade['vix'] = vix
        trade['regime'] = regime

        regimes[regime].append(trade)
        trades_with_vix.append(trade)

    # Calculate metrics for each regime
    results = {}

    for regime_name, regime_trades in regimes.items():
        if not regime_trades:
            continue

        # Calculate metrics
        pnls = [t['pnl'] / 100.0 for t in regime_trades]  # Convert to decimal
        wins = [p for p in pnls if p > 0]

        win_rate = (len(wins) / len(pnls)) * 100 if pnls else 0
        avg_pnl = statistics.mean(pnls) * 100  # Back to percentage for display
        total_pnl = sum(pnls) * 100
        sharpe = calculate_sharpe(pnls)
        max_dd = calculate_max_drawdown(pnls) * 100
        avg_score = statistics.mean(t['score'] for t in regime_trades)
        avg_vix = statistics.mean(t['vix'] for t in regime_trades)

        # Determine VIX range string
        if regime_name == 'low_vol':
            vix_range = "VIX < 15"
        elif regime_name == 'normal':
            vix_range = "15 ≤ VIX < 25"
        else:
            vix_range = "VIX ≥ 25"

        results[regime_name] = RegimeMetrics(
            regime_name=regime_name,
            vix_range=vix_range,
            trade_count=len(regime_trades),
            win_rate=win_rate,
            avg_pnl=avg_pnl,
            total_pnl=total_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            avg_score=avg_score,
            avg_vix=avg_vix
        )

    return results


def print_regime_analysis(results: Dict[str, RegimeMetrics]):
    """Print regime analysis results in a formatted table."""

    print("\n" + "="*80)
    print("MARKET REGIME ANALYSIS - VIX-BASED SEGMENTATION")
    print("="*80)

    # Overall summary
    total_trades = sum(r.trade_count for r in results.values())
    print(f"\n📊 Total Trades Analyzed: {total_trades}")

    # Print each regime
    for regime_name in ['low_vol', 'normal', 'high_vol']:
        if regime_name not in results:
            continue

        r = results[regime_name]

        print(f"\n{'─'*80}")
        print(f"🎯 {r.regime_name.upper().replace('_', ' ')} REGIME: {r.vix_range}")
        print(f"{'─'*80}")
        print(f"  Trades:          {r.trade_count} ({r.trade_count/total_trades*100:.1f}% of total)")
        print(f"  Avg VIX:         {r.avg_vix:.2f}")
        print(f"  Win Rate:        {r.win_rate:.1f}%")
        print(f"  Avg P&L/Trade:   {r.avg_pnl:+.2f}%")
        print(f"  Total P&L:       {r.total_pnl:+.2f}%")
        print(f"  Sharpe Ratio:    {r.sharpe_ratio:.2f}")
        print(f"  Max Drawdown:    {r.max_drawdown:.2f}%")
        print(f"  Avg Score:       {r.avg_score:.1f}")

    print(f"\n{'='*80}")

    # Analysis and recommendations
    print("\n📈 KEY INSIGHTS:")

    # Find best regime by Sharpe
    best_sharpe = max(results.values(), key=lambda x: x.sharpe_ratio)
    print(f"  • Best Sharpe: {best_sharpe.regime_name.replace('_', ' ').title()} ({best_sharpe.sharpe_ratio:.2f})")

    # Find best regime by win rate
    best_win_rate = max(results.values(), key=lambda x: x.win_rate)
    print(f"  • Best Win Rate: {best_win_rate.regime_name.replace('_', ' ').title()} ({best_win_rate.win_rate:.1f}%)")

    # Find best regime by avg P&L
    best_pnl = max(results.values(), key=lambda x: x.avg_pnl)
    print(f"  • Best Avg P&L: {best_pnl.regime_name.replace('_', ' ').title()} ({best_pnl.avg_pnl:+.2f}%)")

    # Compare high vol vs low vol
    if 'low_vol' in results and 'high_vol' in results:
        low = results['low_vol']
        high = results['high_vol']

        print(f"\n🔍 LOW VOL vs HIGH VOL COMPARISON:")
        print(f"  • Trade Count: Low={low.trade_count}, High={high.trade_count}")
        print(f"  • Win Rate: Low={low.win_rate:.1f}%, High={high.win_rate:.1f}% (Δ{high.win_rate-low.win_rate:+.1f}%)")
        print(f"  • Avg P&L: Low={low.avg_pnl:+.2f}%, High={high.avg_pnl:+.2f}% (Δ{high.avg_pnl-low.avg_pnl:+.2f}%)")
        print(f"  • Sharpe: Low={low.sharpe_ratio:.2f}, High={high.sharpe_ratio:.2f} (Δ{high.sharpe_ratio-low.sharpe_ratio:+.2f})")

    print(f"\n{'='*80}\n")


def generate_recommendations(results: Dict[str, RegimeMetrics]) -> List[str]:
    """Generate actionable recommendations based on regime analysis."""
    recommendations = []

    # Check if we have enough data
    total_trades = sum(r.trade_count for r in results.values())
    if total_trades < 10:
        recommendations.append("⚠️  Insufficient data for reliable regime-based conclusions (< 10 trades)")
        return recommendations

    # Analyze regime differences
    if 'low_vol' in results and 'high_vol' in results:
        low = results['low_vol']
        high = results['high_vol']

        # Win rate difference
        win_rate_diff = abs(high.win_rate - low.win_rate)
        if win_rate_diff > 15:
            better_regime = "high vol" if high.win_rate > low.win_rate else "low vol"
            recommendations.append(
                f"✅ Significantly better win rate in {better_regime} ({win_rate_diff:.1f}% difference). "
                f"Consider increasing position sizing or lowering score threshold in this regime."
            )

        # Sharpe difference
        sharpe_diff = abs(high.sharpe_ratio - low.sharpe_ratio)
        if sharpe_diff > 0.3:
            better_regime = "high vol" if high.sharpe_ratio > low.sharpe_ratio else "low vol"
            recommendations.append(
                f"✅ Superior risk-adjusted returns in {better_regime} (Sharpe Δ{sharpe_diff:.2f}). "
                f"Favor trading in this regime."
            )

        # P&L consistency
        pnl_diff = abs(high.avg_pnl - low.avg_pnl)
        if pnl_diff > 2.0:
            better_regime = "high vol" if high.avg_pnl > low.avg_pnl else "low vol"
            recommendations.append(
                f"💰 Higher average P&L in {better_regime} ({pnl_diff:+.2f}%). "
                f"Strategy excels in this environment."
            )

    # Check if normal regime dominates
    if 'normal' in results:
        normal = results['normal']
        if normal.trade_count > total_trades * 0.7:
            recommendations.append(
                f"📊 Most trades occur in normal vol regime ({normal.trade_count}/{total_trades}). "
                f"Optimization efforts should focus on 15 ≤ VIX < 25 range."
            )

    # Check for regime to avoid
    worst_regime = min(results.values(), key=lambda x: x.sharpe_ratio)
    if worst_regime.sharpe_ratio < 0.5 and worst_regime.trade_count >= 3:
        recommendations.append(
            f"⚠️  Poor performance in {worst_regime.regime_name.replace('_', ' ')} "
            f"(Sharpe {worst_regime.sharpe_ratio:.2f}). Consider filtering out trades when {worst_regime.vix_range}."
        )

    # If no significant differences found
    if not recommendations:
        recommendations.append(
            "✅ Performance is relatively consistent across regimes. "
            "No regime-specific adjustments needed at this time."
        )

    return recommendations


def save_results(results: Dict[str, RegimeMetrics], output_path: Path):
    """Save regime analysis results to JSON file."""
    output = {
        'analysis_date': datetime.now().isoformat(),
        'regimes': {}
    }

    for regime_name, metrics in results.items():
        output['regimes'][regime_name] = {
            'vix_range': metrics.vix_range,
            'trade_count': metrics.trade_count,
            'win_rate': metrics.win_rate,
            'avg_pnl': metrics.avg_pnl,
            'total_pnl': metrics.total_pnl,
            'sharpe_ratio': metrics.sharpe_ratio,
            'max_drawdown': metrics.max_drawdown,
            'avg_score': metrics.avg_score,
            'avg_vix': metrics.avg_vix
        }

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    logger.info(f"✓ Results saved to {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Analyze IV Crush performance by market regime')
    parser.add_argument('--input', '-i', type=Path, required=True,
                      help='Path to forward test results JSON')
    parser.add_argument('--output', '-o', type=Path,
                      default=Path('results/regime_analysis.json'),
                      help='Output path for results')
    parser.add_argument('--start-date', default='2024-01-01',
                      help='Start date for VIX data (YYYY-MM-DD)')
    parser.add_argument('--end-date', default='2024-12-31',
                      help='End date for VIX data (YYYY-MM-DD)')

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    logger.info("="*80)
    logger.info("MARKET REGIME ANALYSIS (VIX-Based)")
    logger.info("="*80)

    # Load trades
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        return 1

    trades = load_trades_from_json(args.input)
    logger.info(f"Loaded {len(trades)} trades from {args.input}")

    # Fetch VIX data
    vix_data = fetch_vix_data(args.start_date, args.end_date)
    if not vix_data:
        logger.error("Failed to fetch VIX data. Cannot proceed.")
        return 1

    # Analyze by regime
    results = analyze_regime_performance(trades, vix_data)

    if not results:
        logger.error("No regime results generated. Check VIX data alignment.")
        return 1

    # Print analysis
    print_regime_analysis(results)

    # Generate recommendations
    recommendations = generate_recommendations(results)
    print("\n💡 RECOMMENDATIONS:")
    for i, rec in enumerate(recommendations, 1):
        print(f"\n{i}. {rec}")

    # Save results
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_results(results, args.output)

    logger.info("\n✓ Market regime analysis complete!")

    return 0


if __name__ == '__main__':
    sys.exit(main())
