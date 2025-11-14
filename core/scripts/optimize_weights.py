#!/usr/bin/env python3
"""
A/B Testing Framework for Trading Algorithm Weight Optimization

Runs backtests on actual traded tickers with various scoring configurations
to find optimal weights for VRP, consistency, skew, and liquidity.

Account Size: $XXX
Position Sizing: Half-Kelly (5%)

Usage:
    python scripts/optimize_weights.py
"""

import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict
import json

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.scoring_config import (
    ScoringConfig,
    ScoringWeights,
    ScoringThresholds,
    get_all_configs
)
from src.application.services.backtest_engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)

# Tickers from actual trading history (last 90 days)
ACTUAL_TRADED_TICKERS = [
    "AMAT", "ASAN", "DIS", "CRWV", "SNDK", "PINS", "FIG", "AMD", "SMCI",
    "ANET", "UPST", "NVO", "DOCN", "SPOT", "HIMS", "SHOP", "PLTR", "RDDT",
    "RBLX", "NOW", "MSFT", "META", "GOOGL", "CVNA", "CMG", "ENPH", "SPY",
    "SOFI", "PYPL", "INTC", "SPXW", "NFLX", "TSLA", "TSM", "UAL", "OSCR",
    "ASML", "JPM", "GOOG", "APLD", "DAL", "NVDA", "ACN", "MU", "FDX",
    "ORCL", "ADBE", "OXM", "AVGO", "GTLB", "MDB", "WMT", "TGT"
]

# Account configuration
ACCOUNT_SIZE = 600_000  # $XXX
HALF_KELLY = 0.05  # 5% position sizing
QUARTER_KELLY = 0.10  # 10% position sizing


def create_additional_test_configs() -> Dict[str, ScoringConfig]:
    """
    Create additional scoring configurations to test beyond the defaults.

    Adds variations optimized for actual trading patterns observed.
    """

    default_thresholds = ScoringThresholds()

    additional_configs = {
        # Based on actual trading analysis: focus on VRP + consistency
        "actual_optimized": ScoringConfig(
            name="Actual-Optimized",
            description="Optimized for patterns seen in real trading data. High VRP + Consistency.",
            weights=ScoringWeights(
                vrp_weight=0.50,  # Strong VRP edge
                consistency_weight=0.30,  # Predictability important
                skew_weight=0.10,
                liquidity_weight=0.10,
            ),
            thresholds=ScoringThresholds(
                vrp_excellent=1.9,
                vrp_good=1.5,
                vrp_marginal=1.3,
                min_composite_score=65.0,
            ),
            max_positions=8,
            min_score=65.0,
        ),

        # Ultra-conservative for $XXX account
        "ultra_conservative": ScoringConfig(
            name="Ultra-Conservative",
            description="Maximum quality, minimum risk. For large accounts.",
            weights=ScoringWeights(
                vrp_weight=0.35,
                consistency_weight=0.40,  # Very high consistency requirement
                skew_weight=0.15,
                liquidity_weight=0.10,
            ),
            thresholds=ScoringThresholds(
                vrp_excellent=2.5,  # Very high thresholds
                vrp_good=1.9,
                vrp_marginal=1.6,
                consistency_excellent=0.85,
                consistency_good=0.70,
                consistency_marginal=0.55,
                min_composite_score=75.0,
            ),
            max_positions=5,
            min_score=75.0,
        ),

        # VRP + Liquidity focus (for large position sizes)
        "vrp_liquid": ScoringConfig(
            name="VRP-Liquid",
            description="High VRP with excellent liquidity. Best for $XXX account.",
            weights=ScoringWeights(
                vrp_weight=0.45,
                consistency_weight=0.20,
                skew_weight=0.10,
                liquidity_weight=0.25,  # Higher liquidity for large positions
            ),
            thresholds=ScoringThresholds(
                vrp_excellent=1.8,
                vrp_good=1.5,
                vrp_marginal=1.3,
                min_open_interest=200,  # Higher liquidity requirements
                good_open_interest=750,
                excellent_open_interest=1500,
                max_spread_excellent=3.0,  # Tighter spreads
                max_spread_good=6.0,
                max_spread_marginal=10.0,
                min_composite_score=62.0,
            ),
            max_positions=10,
            min_score=62.0,
        ),

        # Moderate-aggressive (more trades, still quality)
        "moderate_aggressive": ScoringConfig(
            name="Moderate-Aggressive",
            description="Higher trade frequency with good quality standards.",
            weights=ScoringWeights(
                vrp_weight=0.50,
                consistency_weight=0.25,
                skew_weight=0.15,
                liquidity_weight=0.10,
            ),
            thresholds=ScoringThresholds(
                vrp_excellent=1.7,
                vrp_good=1.4,
                vrp_marginal=1.2,
                min_composite_score=55.0,
            ),
            max_positions=12,
            min_score=55.0,
        ),
    }

    return additional_configs


def print_summary_table(results: List[BacktestResult]):
    """Print formatted comparison table of backtest results."""

    # Sort by Sharpe ratio (descending)
    sorted_results = sorted(results, key=lambda r: r.sharpe_ratio, reverse=True)

    print("\n" + "=" * 140)
    print("A/B TEST RESULTS - RANKED BY SHARPE RATIO")
    print("=" * 140)

    # Header
    header = (
        f"{'Rank':<6} "
        f"{'Config':<22} "
        f"{'Trades':<8} "
        f"{'Win%':<8} "
        f"{'Avg P&L':<12} "
        f"{'Total P&L':<12} "
        f"{'Sharpe':<8} "
        f"{'Max DD':<12} "
        f"{'Qualified':<10}"
    )
    print(header)
    print("-" * 140)

    # Rows
    for i, result in enumerate(sorted_results, 1):
        row = (
            f"{i:<6} "
            f"{result.config_name:<22} "
            f"{result.selected_trades:<8} "
            f"{result.win_rate:>6.1f}%  "
            f"{result.avg_pnl_per_trade:>10.2f}%  "
            f"{result.total_pnl:>10.2f}%  "
            f"{result.sharpe_ratio:>7.2f}  "
            f"{result.max_drawdown:>10.2f}%  "
            f"{result.qualified_opportunities:<10}"
        )
        print(row)

    print("=" * 140)


def print_detailed_analysis(result: BacktestResult, rank: int):
    """Print detailed analysis for a single configuration."""

    print(f"\n{'='*80}")
    print(f"#{rank}: {result.config_name.upper()}")
    print(f"{'='*80}")

    print(f"\nPerformance Metrics:")
    print(f"  Qualified Opportunities: {result.qualified_opportunities}")
    print(f"  Trades Selected        : {result.selected_trades}")
    if result.qualified_opportunities > 0:
        print(f"  Selection Rate         : {result.selected_trades/result.qualified_opportunities*100:.1f}%")
    print(f"  Win Rate               : {result.win_rate:.1f}%")
    print(f"  Average P&L per Trade  : {result.avg_pnl_per_trade:.2f}%")
    print(f"  Total P&L              : {result.total_pnl:.2f}%")
    print(f"  Sharpe Ratio           : {result.sharpe_ratio:.2f}")
    print(f"  Max Drawdown           : {result.max_drawdown:.2f}%")

    # Position sizing for $XXX account
    avg_trade_pnl_dollars = (result.avg_pnl_per_trade / 100) * (ACCOUNT_SIZE * HALF_KELLY)
    total_pnl_dollars = (result.total_pnl / 100) * ACCOUNT_SIZE
    max_dd_dollars = (result.max_drawdown / 100) * ACCOUNT_SIZE

    print(f"\nProjected Performance on ${ACCOUNT_SIZE:,.0f} Account:")
    print(f"  Half-Kelly Position Size: ${ACCOUNT_SIZE * HALF_KELLY:,.0f} per trade (5%)")
    print(f"  Avg P&L per Trade ($)   : ${avg_trade_pnl_dollars:,.0f}")
    print(f"  Total P&L ($)           : ${total_pnl_dollars:,.0f}")
    print(f"  Max Drawdown ($)        : ${max_dd_dollars:,.0f}")
    print(f"  Max 3 Positions ($)     : ${ACCOUNT_SIZE * HALF_KELLY * 3:,.0f} (15% exposure)")
    print(f"  Max 4 Positions ($)     : ${ACCOUNT_SIZE * QUARTER_KELLY * 4:,.0f} (40% exposure)")

    # Risk-adjusted metrics
    if result.selected_trades > 0:
        # Assume backtest period is 2 years = 24 months
        monthly_trades = result.selected_trades / 24
        monthly_pnl = total_pnl_dollars / 24

        print(f"\nProjected Monthly Performance:")
        print(f"  Trades per Month       : {monthly_trades:.1f}")
        print(f"  P&L per Month          : ${monthly_pnl:,.0f}")
        print(f"  Return on Capital      : {(monthly_pnl / ACCOUNT_SIZE) * 100:.2f}% per month")
        print(f"  Annualized Return      : {(monthly_pnl * 12 / ACCOUNT_SIZE) * 100:.1f}%")


def main():
    """Main A/B testing execution."""

    setup_logging()

    print("="*80)
    print("TRADING ALGORITHM WEIGHT OPTIMIZATION - A/B TESTING")
    print("="*80)
    print(f"\nAccount Size: ${ACCOUNT_SIZE:,.0f}")
    print(f"Position Sizing: {HALF_KELLY*100:.0f}% (Half-Kelly)")
    print(f"Tickers from Actual Trading: {len(ACTUAL_TRADED_TICKERS)}")
    print()

    # Get all configurations (existing + additional)
    all_configs = get_all_configs()
    additional_configs = create_additional_test_configs()

    # Combine configs
    test_configs = {**all_configs, **additional_configs}

    print(f"Testing {len(test_configs)} configurations:")
    for name in sorted(test_configs.keys()):
        config = test_configs[name]
        print(f"  - {name:<25} ({config.description[:60]})")

    # Backtest date range (last 2 years of historical data)
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=730)

    print(f"\nBacktest Period: {start_date} to {end_date}")
    print("="*80)

    # Initialize backtest engine
    db_path = Path(__file__).parent.parent / "data" / "ivcrush.db"
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        return

    backtest_engine = BacktestEngine(str(db_path))

    # Run backtests
    print("\nRUNNING BACKTESTS")
    print("="*80)

    results = []

    for config_name, config in test_configs.items():
        logger.info(f"Testing configuration: {config_name}")

        try:
            # Run backtest
            result = backtest_engine.run_backtest(
                config=config,
                start_date=start_date,
                end_date=end_date,
                position_sizing=False,  # Using percentage-based returns
                total_capital=ACCOUNT_SIZE
            )

            results.append(result)

            print(f"✓ {config_name:<25}: {result.selected_trades:>4} trades, "
                  f"WR {result.win_rate:>5.1f}%, Sharpe {result.sharpe_ratio:>6.2f}")

        except Exception as e:
            logger.error(f"Error testing {config_name}: {e}")
            print(f"✗ {config_name:<25}: ERROR - {str(e)[:50]}")
            continue

    if not results:
        print("\nERROR: No successful backtests completed!")
        return

    # Sort by Sharpe ratio
    results.sort(key=lambda x: x.sharpe_ratio, reverse=True)

    # Print summary table
    print_summary_table(results)

    # Print detailed analysis for top 3
    print("\n" + "="*80)
    print("TOP 3 CONFIGURATIONS - DETAILED ANALYSIS")
    print("="*80)

    for i, result in enumerate(results[:3], 1):
        print_detailed_analysis(result, i)

    # Save results
    output_path = Path(__file__).parent.parent / "results" / "weight_optimization.json"
    output_path.parent.mkdir(exist_ok=True)

    output_data = {
        'analysis_date': datetime.now().isoformat(),
        'account_size': ACCOUNT_SIZE,
        'position_sizing': HALF_KELLY,
        'backtest_period': {
            'start': str(start_date),
            'end': str(end_date)
        },
        'tickers_tested': ACTUAL_TRADED_TICKERS,
        'results': [
            {
                'config_name': r.config_name,
                'qualified_opportunities': r.qualified_opportunities,
                'selected_trades': r.selected_trades,
                'win_rate': r.win_rate,
                'avg_pnl_per_trade': r.avg_pnl_per_trade,
                'total_pnl': r.total_pnl,
                'sharpe_ratio': r.sharpe_ratio,
                'max_drawdown': r.max_drawdown
            }
            for r in results
        ]
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n✅ Results saved to: {output_path}")

    # Recommendations
    print("\n" + "="*80)
    print("RECOMMENDATIONS")
    print("="*80)

    best = results[0]

    print(f"\n🎯 RECOMMENDED CONFIGURATION: {best.config_name.upper()}")
    print(f"\nThis configuration achieved:")
    print(f"  - Highest Sharpe Ratio: {best.sharpe_ratio:.2f}")
    print(f"  - Win Rate: {best.win_rate:.1f}%")
    print(f"  - Trades Selected: {best.selected_trades}")
    print(f"  - Avg P&L per Trade: {best.avg_pnl_per_trade:.2f}%")

    # Calculate dollar projections
    total_pnl_dollars = (best.total_pnl / 100) * ACCOUNT_SIZE
    max_dd_dollars = (best.max_drawdown / 100) * ACCOUNT_SIZE
    monthly_pnl = total_pnl_dollars / 24  # 2 year backtest

    print(f"\n💰 PROJECTED PERFORMANCE ON ${ACCOUNT_SIZE:,.0f} ACCOUNT:")
    print(f"  - Total P&L (24 months): ${total_pnl_dollars:,.0f}")
    print(f"  - Monthly P&L: ${monthly_pnl:,.0f}")
    print(f"  - Annualized Return: {(monthly_pnl * 12 / ACCOUNT_SIZE) * 100:.1f}%")
    print(f"  - Max Drawdown: ${max_dd_dollars:,.0f}")
    print(f"  - Position Size: ${ACCOUNT_SIZE * HALF_KELLY:,.0f} per trade (5% half-Kelly)")
    print(f"  - Max Exposure: ${ACCOUNT_SIZE * HALF_KELLY * 3:,.0f} (3 positions)")

    print(f"\n⚠️  RISK MANAGEMENT:")
    print(f"  - Max position size: ${ACCOUNT_SIZE * HALF_KELLY:,.0f} (5% half-Kelly)")
    print(f"  - Max concurrent positions: 3-4")
    print(f"  - Stop loss: 2x credit received or -${ACCOUNT_SIZE * HALF_KELLY:,.0f}")
    print(f"  - Max total exposure: 15-20% of capital")
    print(f"  - Ticker blacklist: AVGO, NFLX, META (from actual trading analysis)")

    print(f"\n📊 IMPLEMENTATION STEPS:")
    print(f"  1. Update trade.sh to use '{best.config_name}' configuration")
    print(f"  2. Set position size cap at ${ACCOUNT_SIZE * HALF_KELLY:,.0f} per trade")
    print(f"  3. Implement stop loss at 2x credit or -${ACCOUNT_SIZE * HALF_KELLY:,.0f}")
    print(f"  4. Limit to maximum 3 concurrent positions initially")
    print(f"  5. Monitor performance for 20 trades before adjusting")
    print(f"  6. Re-run optimization quarterly with new data")

    print(f"\n🎓 KEY INSIGHTS FROM OPTIMIZATION:")
    print(f"  - Tested {len(test_configs)} different scoring configurations")
    print(f"  - Analyzed {len(ACTUAL_TRADED_TICKERS)} tickers from actual trading history")
    print(f"  - Best config selected {best.selected_trades} trades from {best.qualified_opportunities} opportunities")
    print(f"  - Selection rate: {best.selected_trades/max(best.qualified_opportunities, 1)*100:.1f}% (quality over quantity)")


if __name__ == "__main__":
    main()
