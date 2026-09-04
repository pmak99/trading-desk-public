#!/usr/bin/env python3
"""
A/B Testing Framework for Scan Quality Scoring Algorithm.

This script tests multiple scoring configurations against historical trade data
to identify the optimal weight distribution for predicting profitable trades.

Key Analysis:
1. Tests 6 different scoring configurations
2. Simulates rankings for historical earnings trades
3. Measures correlation between score and actual P&L
4. Identifies which configuration best separates winners from losers

Usage:
    python scripts/scoring_ab_test.py

Author: Trading Desk 2.0
Date: December 2025
"""

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import statistics

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class ScoringConfig:
    """Configuration for a scoring algorithm variant."""
    name: str
    description: str

    # VRP scoring
    vrp_max_points: float
    vrp_target: float

    # Edge scoring (0 = disabled)
    edge_max_points: float
    edge_target: float

    # Liquidity scoring
    liq_excellent: float
    liq_warning: float
    liq_reject: float

    # Implied move scoring
    move_max_points: float
    move_easy_threshold: float  # % for full points
    move_moderate_threshold: float  # % for moderate points
    move_moderate_points: float
    move_challenging_threshold: float  # % for challenging points
    move_challenging_points: float
    move_extreme_points: float

    # Optional flags (defaults at end)
    vrp_use_linear: bool = False  # True = no cap at target
    use_continuous_move: bool = False  # True = linear interpolation


@dataclass
class TradeData:
    """Historical trade data for backtesting."""
    ticker: str
    date: str
    strategy: str
    pnl: float
    winner: bool

    # Simulated metrics (we'll use realistic distributions)
    vrp_ratio: float
    edge_score: float
    liquidity_tier: str
    implied_move_pct: float


def calculate_score(trade: TradeData, config: ScoringConfig) -> float:
    """Calculate quality score for a trade using given config."""

    # Factor 1: VRP Score
    if config.vrp_use_linear:
        # Linear scaling, no cap
        vrp_normalized = trade.vrp_ratio / config.vrp_target
    else:
        # Capped at target
        vrp_normalized = min(trade.vrp_ratio / config.vrp_target, 1.0)
    vrp_score = max(0.0, vrp_normalized) * config.vrp_max_points

    # Factor 2: Edge Score (may be 0 if disabled)
    if config.edge_max_points > 0:
        edge_normalized = min(trade.edge_score / config.edge_target, 1.0)
        edge_score = max(0.0, edge_normalized) * config.edge_max_points
    else:
        edge_score = 0.0

    # Factor 3: Liquidity Score
    if trade.liquidity_tier == 'EXCELLENT':
        liq_score = config.liq_excellent
    elif trade.liquidity_tier == 'WARNING':
        liq_score = config.liq_warning
    else:  # REJECT
        liq_score = config.liq_reject

    # Factor 4: Implied Move Score
    if config.use_continuous_move:
        # Linear interpolation: 0% = max points, 20% = 0 points
        move_normalized = max(0.0, 1.0 - (trade.implied_move_pct / 20.0))
        move_score = move_normalized * config.move_max_points
    else:
        # Discrete buckets
        if trade.implied_move_pct <= config.move_easy_threshold:
            move_score = config.move_max_points
        elif trade.implied_move_pct <= config.move_moderate_threshold:
            move_score = config.move_moderate_points
        elif trade.implied_move_pct <= config.move_challenging_threshold:
            move_score = config.move_challenging_points
        else:
            move_score = config.move_extreme_points

    return vrp_score + edge_score + liq_score + move_score


def get_test_configs() -> List[ScoringConfig]:
    """Get all A/B test configurations."""

    configs = [
        # CONFIG G: OPTIMAL (Implemented in scan.py)
        # Hybrid of D_Continuous and F_Balanced based on A/B test results
        ScoringConfig(
            name="G_Optimal",
            description="OPTIMAL: Continuous VRP (4.0 target), no Edge, heavy Move weight",
            vrp_max_points=45.0,
            vrp_target=4.0,
            edge_max_points=0.0,  # Disabled
            edge_target=1.0,
            liq_excellent=20.0,
            liq_warning=12.0,
            liq_reject=4.0,
            move_max_points=35.0,
            move_easy_threshold=8.0,
            move_moderate_threshold=12.0,
            move_moderate_points=25.0,
            move_challenging_threshold=15.0,
            move_challenging_points=15.0,
            move_extreme_points=5.0,
            vrp_use_linear=True,  # Continuous
            use_continuous_move=True,
        ),

        # CONFIG A: Current (Baseline) - with VRP+Edge double-counting
        ScoringConfig(
            name="A_Current",
            description="Current config (VRP 40 + Edge 30 = 70% VRP-derived)",
            vrp_max_points=40.0,
            vrp_target=3.0,
            vrp_use_linear=False,
            edge_max_points=30.0,
            edge_target=3.0,
            liq_excellent=15.0,
            liq_warning=10.0,
            liq_reject=5.0,
            move_max_points=15.0,
            move_easy_threshold=8.0,
            move_moderate_threshold=12.0,
            move_moderate_points=10.0,
            move_challenging_threshold=15.0,
            move_challenging_points=6.0,
            move_extreme_points=3.0,
        ),

        # CONFIG B: VRP-Only (Remove Edge redundancy)
        ScoringConfig(
            name="B_VRP_Only",
            description="Remove redundant Edge, boost VRP + Move weights",
            vrp_max_points=55.0,
            vrp_target=3.0,
            vrp_use_linear=False,
            edge_max_points=0.0,  # Disabled
            edge_target=1.0,
            liq_excellent=20.0,
            liq_warning=12.0,
            liq_reject=4.0,
            move_max_points=25.0,
            move_easy_threshold=8.0,
            move_moderate_threshold=12.0,
            move_moderate_points=18.0,
            move_challenging_threshold=15.0,
            move_challenging_points=10.0,
            move_extreme_points=5.0,
        ),

        # CONFIG C: Risk-Adjusted (Heavy penalty for high IV)
        ScoringConfig(
            name="C_Risk_Adjusted",
            description="Heavy weight on implied move (easier trades = higher score)",
            vrp_max_points=35.0,
            vrp_target=3.0,
            vrp_use_linear=False,
            edge_max_points=0.0,
            edge_target=1.0,
            liq_excellent=20.0,
            liq_warning=12.0,
            liq_reject=4.0,
            move_max_points=45.0,
            move_easy_threshold=8.0,
            move_moderate_threshold=12.0,
            move_moderate_points=30.0,
            move_challenging_threshold=15.0,
            move_challenging_points=15.0,
            move_extreme_points=5.0,
        ),

        # CONFIG D: Continuous Scoring (No cliffs)
        ScoringConfig(
            name="D_Continuous",
            description="Linear interpolation for VRP and Move (no cliffs)",
            vrp_max_points=45.0,
            vrp_target=5.0,  # Higher target for linear scaling
            vrp_use_linear=True,  # No cap
            edge_max_points=0.0,
            edge_target=1.0,
            liq_excellent=15.0,
            liq_warning=10.0,
            liq_reject=5.0,
            move_max_points=40.0,
            move_easy_threshold=8.0,  # Not used in continuous
            move_moderate_threshold=12.0,
            move_moderate_points=10.0,
            move_challenging_threshold=15.0,
            move_challenging_points=6.0,
            move_extreme_points=3.0,
            use_continuous_move=True,  # Linear interpolation
        ),

        # CONFIG E: Liquidity-First
        ScoringConfig(
            name="E_Liquidity_First",
            description="Heavy liquidity weight, REJECT = near-zero",
            vrp_max_points=30.0,
            vrp_target=3.0,
            vrp_use_linear=False,
            edge_max_points=0.0,
            edge_target=1.0,
            liq_excellent=40.0,
            liq_warning=20.0,
            liq_reject=0.0,  # Deal-breaker
            move_max_points=30.0,
            move_easy_threshold=8.0,
            move_moderate_threshold=12.0,
            move_moderate_points=20.0,
            move_challenging_threshold=15.0,
            move_challenging_points=10.0,
            move_extreme_points=5.0,
        ),

        # CONFIG F: Balanced Premium Capture
        ScoringConfig(
            name="F_Balanced",
            description="Higher VRP threshold, balanced weights",
            vrp_max_points=40.0,
            vrp_target=4.0,  # Harder to achieve full points
            vrp_use_linear=False,
            edge_max_points=0.0,
            edge_target=1.0,
            liq_excellent=25.0,
            liq_warning=15.0,
            liq_reject=5.0,
            move_max_points=35.0,
            move_easy_threshold=8.0,
            move_moderate_threshold=12.0,
            move_moderate_points=25.0,
            move_challenging_threshold=15.0,
            move_challenging_points=15.0,
            move_extreme_points=8.0,
        ),
    ]

    return configs


def generate_simulated_metrics(trade_data: List[Dict], seed: Optional[int] = None) -> List[TradeData]:
    """
    Generate simulated VRP/Edge/Liquidity metrics for historical trades.

    Since we don't have the actual metrics from when trades were made,
    we simulate realistic distributions based on trade characteristics.
    """
    import random
    if seed is not None:
        random.seed(seed)

    trades = []

    for trade in trade_data:
        ticker = trade['ticker']
        pnl = trade['pnl']
        winner = trade['winner']
        strategy = trade['strategy']

        # Simulate VRP based on winner/loser status
        # Winners tend to have higher VRP (but not always - that's the noise)
        if winner:
            vrp_base = random.gauss(4.5, 1.5)  # Winners: mean 4.5, std 1.5
        else:
            vrp_base = random.gauss(3.0, 1.5)  # Losers: mean 3.0, std 1.5
        vrp_ratio = max(1.0, vrp_base)  # Floor at 1.0

        # Edge score derived from VRP (with consistency factor)
        consistency = random.uniform(0.1, 0.5)
        edge_score = vrp_ratio / (1 + consistency)

        # Liquidity tier - losers more likely to have poor liquidity
        if winner:
            liq_roll = random.random()
            if liq_roll < 0.4:
                liquidity = 'EXCELLENT'
            elif liq_roll < 0.8:
                liquidity = 'WARNING'
            else:
                liquidity = 'REJECT'
        else:
            liq_roll = random.random()
            if liq_roll < 0.2:
                liquidity = 'EXCELLENT'
            elif liq_roll < 0.5:
                liquidity = 'WARNING'
            else:
                liquidity = 'REJECT'

        # Implied move - losers tend to have higher implied moves
        if winner:
            implied_move = max(4.0, random.gauss(9.0, 3.0))
        else:
            implied_move = max(4.0, random.gauss(13.0, 4.0))

        trades.append(TradeData(
            ticker=ticker,
            date=trade['date'],
            strategy=strategy,
            pnl=pnl,
            winner=winner,
            vrp_ratio=vrp_ratio,
            edge_score=edge_score,
            liquidity_tier=liquidity,
            implied_move_pct=implied_move,
        ))

    return trades


def parse_pnl(pnl_str: str) -> float:
    """Parse P&L string like '$1,234.56' or '$-1,234.56' to float."""
    clean = pnl_str.replace('$', '').replace(',', '').replace('"', '')
    return float(clean)


def load_historical_trades() -> List[Dict]:
    """Load historical trade data from CSV."""
    import csv

    csv_path = Path(__file__).parent.parent.parent / "docs/2025 Trades/trading_journal_validated_2025.csv"

    trades = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Filter to earnings-related option trades
            strategy = row['Strategy']
            if strategy in ['short_put', 'short_call', 'credit_put_spread', 'credit_call_spread',
                           'debit_put_spread', 'debit_call_spread']:
                try:
                    pnl = parse_pnl(row['Net P&L'])
                    trades.append({
                        'ticker': row['Ticker'],
                        'date': row['Date Close'],
                        'strategy': strategy,
                        'pnl': pnl,
                        'winner': row['Winner'] == 'YES',
                    })
                except (ValueError, KeyError) as e:
                    continue  # Skip malformed rows

    return trades


def calculate_metrics(trades: List[TradeData], config: ScoringConfig) -> Dict:
    """Calculate performance metrics for a scoring configuration."""

    scores_winners = []
    scores_losers = []
    all_scores_pnl = []

    for trade in trades:
        score = calculate_score(trade, config)
        all_scores_pnl.append((score, trade.pnl, trade.winner))

        if trade.winner:
            scores_winners.append(score)
        else:
            scores_losers.append(score)

    # Basic statistics
    avg_winner_score = statistics.mean(scores_winners) if scores_winners else 0
    avg_loser_score = statistics.mean(scores_losers) if scores_losers else 0
    score_separation = avg_winner_score - avg_loser_score

    # Calculate correlation between score and P&L
    scores = [x[0] for x in all_scores_pnl]
    pnls = [x[1] for x in all_scores_pnl]

    n = len(scores)
    if n > 2:
        mean_score = statistics.mean(scores)
        mean_pnl = statistics.mean(pnls)

        numerator = sum((s - mean_score) * (p - mean_pnl) for s, p in zip(scores, pnls))
        denom_score = sum((s - mean_score) ** 2 for s in scores) ** 0.5
        denom_pnl = sum((p - mean_pnl) ** 2 for p in pnls) ** 0.5

        if denom_score > 0 and denom_pnl > 0:
            correlation = numerator / (denom_score * denom_pnl)
        else:
            correlation = 0.0
    else:
        correlation = 0.0

    # Win rate in top quartile by score
    sorted_by_score = sorted(all_scores_pnl, key=lambda x: -x[0])
    top_quartile = sorted_by_score[:len(sorted_by_score)//4]
    top_quartile_wins = sum(1 for _, _, w in top_quartile if w)
    top_quartile_win_rate = top_quartile_wins / len(top_quartile) if top_quartile else 0

    # Win rate in bottom quartile
    bottom_quartile = sorted_by_score[-len(sorted_by_score)//4:]
    bottom_quartile_wins = sum(1 for _, _, w in bottom_quartile if w)
    bottom_quartile_win_rate = bottom_quartile_wins / len(bottom_quartile) if bottom_quartile else 0

    # Total P&L if only traded top quartile
    top_quartile_pnl = sum(p for _, p, _ in top_quartile)

    # Total P&L if only traded bottom quartile
    bottom_quartile_pnl = sum(p for _, p, _ in bottom_quartile)

    return {
        'config_name': config.name,
        'description': config.description,
        'avg_winner_score': avg_winner_score,
        'avg_loser_score': avg_loser_score,
        'score_separation': score_separation,
        'correlation': correlation,
        'top_quartile_win_rate': top_quartile_win_rate,
        'bottom_quartile_win_rate': bottom_quartile_win_rate,
        'quartile_win_rate_delta': top_quartile_win_rate - bottom_quartile_win_rate,
        'top_quartile_pnl': top_quartile_pnl,
        'bottom_quartile_pnl': bottom_quartile_pnl,
        'quartile_pnl_delta': top_quartile_pnl - bottom_quartile_pnl,
        'n_trades': len(trades),
        'n_winners': len(scores_winners),
        'n_losers': len(scores_losers),
    }


def run_ab_tests():
    """Run A/B tests across all configurations."""

    print("=" * 100)
    print("SCORING ALGORITHM A/B TEST - Trading Desk 2.0")
    print("=" * 100)

    # Load historical data
    print("\n[1] Loading historical trade data...")
    raw_trades = load_historical_trades()
    print(f"    Loaded {len(raw_trades)} option trades from 2025 journal")

    # Generate simulated metrics
    print("\n[2] Generating simulated VRP/Edge/Liquidity metrics...")
    trades = generate_simulated_metrics(raw_trades, seed=42)

    winners = sum(1 for t in trades if t.winner)
    losers = len(trades) - winners
    print(f"    {winners} winners, {losers} losers ({winners/len(trades)*100:.1f}% win rate)")

    # Get configurations
    configs = get_test_configs()
    print(f"\n[3] Testing {len(configs)} scoring configurations...")

    # Run tests
    results = []
    for config in configs:
        metrics = calculate_metrics(trades, config)
        results.append(metrics)

    # Display results
    print("\n" + "=" * 100)
    print("A/B TEST RESULTS")
    print("=" * 100)

    print("\n" + "-" * 100)
    print(f"{'Config':<18} {'Separation':<12} {'Correlation':<12} {'Top Q Win%':<12} {'Bot Q Win%':<12} {'Delta':<10}")
    print("-" * 100)

    for r in sorted(results, key=lambda x: -x['quartile_win_rate_delta']):
        print(f"{r['config_name']:<18} {r['score_separation']:>10.1f} {r['correlation']:>10.3f} "
              f"{r['top_quartile_win_rate']*100:>10.1f}% {r['bottom_quartile_win_rate']*100:>10.1f}% "
              f"{r['quartile_win_rate_delta']*100:>8.1f}%")

    print("\n" + "-" * 100)
    print(f"{'Config':<18} {'Avg Winner':<12} {'Avg Loser':<12} {'Top Q P&L':<15} {'Bot Q P&L':<15} {'PnL Delta':<12}")
    print("-" * 100)

    for r in sorted(results, key=lambda x: -x['quartile_pnl_delta']):
        print(f"{r['config_name']:<18} {r['avg_winner_score']:>10.1f} {r['avg_loser_score']:>10.1f} "
              f"${r['top_quartile_pnl']:>12,.0f} ${r['bottom_quartile_pnl']:>12,.0f} "
              f"${r['quartile_pnl_delta']:>10,.0f}")

    # Detailed breakdown
    print("\n" + "=" * 100)
    print("DETAILED CONFIGURATION ANALYSIS")
    print("=" * 100)

    for r in results:
        print(f"\n{r['config_name']}: {r['description']}")
        print(f"  Score Separation (Winner - Loser): {r['score_separation']:.2f} points")
        print(f"  Score-PnL Correlation: {r['correlation']:.3f}")
        print(f"  Top Quartile Win Rate: {r['top_quartile_win_rate']*100:.1f}%")
        print(f"  Bottom Quartile Win Rate: {r['bottom_quartile_win_rate']*100:.1f}%")
        print(f"  Win Rate Delta: {r['quartile_win_rate_delta']*100:.1f}%")
        print(f"  Top Quartile P&L: ${r['top_quartile_pnl']:,.0f}")
        print(f"  Bottom Quartile P&L: ${r['bottom_quartile_pnl']:,.0f}")

    # Find best configuration
    print("\n" + "=" * 100)
    print("RECOMMENDATION")
    print("=" * 100)

    # Score configs on multiple dimensions
    def composite_score(r):
        """Composite score weighing multiple factors."""
        return (
            r['quartile_win_rate_delta'] * 100 +  # Win rate separation (0-100)
            r['correlation'] * 50 +                # Correlation with P&L (0-50)
            r['score_separation'] * 2              # Score separation points
        )

    ranked = sorted(results, key=lambda x: -composite_score(x))

    print(f"\nBest Configuration: {ranked[0]['config_name']}")
    print(f"  {ranked[0]['description']}")
    print(f"\n  Why it's best:")
    print(f"  - Win rate delta: {ranked[0]['quartile_win_rate_delta']*100:.1f}% (top vs bottom quartile)")
    print(f"  - Score-PnL correlation: {ranked[0]['correlation']:.3f}")
    print(f"  - Score separation: {ranked[0]['score_separation']:.1f} points (winners vs losers)")
    print(f"  - P&L delta: ${ranked[0]['quartile_pnl_delta']:,.0f} (top vs bottom quartile)")

    print(f"\nRunners-up:")
    for i, r in enumerate(ranked[1:4], 2):
        print(f"  {i}. {r['config_name']}: separation={r['score_separation']:.1f}, "
              f"corr={r['correlation']:.3f}, win_delta={r['quartile_win_rate_delta']*100:.1f}%")

    print("\n" + "=" * 100)
    print("KEY FINDINGS")
    print("=" * 100)

    # Check if removing Edge helps
    current = next(r for r in results if r['config_name'] == 'A_Current')
    vrp_only = next(r for r in results if r['config_name'] == 'B_VRP_Only')

    if vrp_only['quartile_win_rate_delta'] > current['quartile_win_rate_delta']:
        print("\n1. REMOVING EDGE REDUNDANCY HELPS")
        print(f"   Config B (VRP-only) outperforms Config A (current) by "
              f"{(vrp_only['quartile_win_rate_delta'] - current['quartile_win_rate_delta'])*100:.1f}% win rate delta")
    else:
        print("\n1. EDGE FACTOR ADDS VALUE")
        print(f"   Config A (current with Edge) outperforms Config B (VRP-only)")

    # Check if continuous scoring helps
    continuous = next(r for r in results if r['config_name'] == 'D_Continuous')
    if continuous['correlation'] > current['correlation']:
        print("\n2. CONTINUOUS SCORING IMPROVES CORRELATION")
        print(f"   Config D correlation: {continuous['correlation']:.3f} vs Current: {current['correlation']:.3f}")
    else:
        print("\n2. DISCRETE BUCKETS WORK FINE")
        print(f"   No significant improvement from continuous scoring")

    # Check liquidity importance
    liquidity_first = next(r for r in results if r['config_name'] == 'E_Liquidity_First')
    if liquidity_first['quartile_pnl_delta'] > current['quartile_pnl_delta']:
        print("\n3. LIQUIDITY WEIGHT MATTERS")
        print(f"   Config E (liquidity-first) P&L delta: ${liquidity_first['quartile_pnl_delta']:,.0f} "
              f"vs Current: ${current['quartile_pnl_delta']:,.0f}")

    # Check risk-adjusted
    risk_adj = next(r for r in results if r['config_name'] == 'C_Risk_Adjusted')
    if risk_adj['score_separation'] > current['score_separation']:
        print("\n4. PENALIZING HIGH IMPLIED MOVE HELPS SEPARATION")
        print(f"   Config C score separation: {risk_adj['score_separation']:.1f} vs Current: {current['score_separation']:.1f}")

    print("\n" + "=" * 100)


def run_monte_carlo_tests(n_iterations: int = 100):
    """Run Monte Carlo simulation to validate results across random seeds."""

    print("=" * 100)
    print(f"MONTE CARLO VALIDATION ({n_iterations} iterations)")
    print("=" * 100)

    import random

    # Load historical data once
    raw_trades = load_historical_trades()
    configs = get_test_configs()

    # Track results across iterations
    config_wins = {c.name: 0 for c in configs}
    config_metrics = {c.name: {'sep': [], 'corr': [], 'delta': [], 'pnl': []} for c in configs}

    for seed in range(n_iterations):
        trades = generate_simulated_metrics(raw_trades, seed=seed)

        best_score = -float('inf')
        best_config = None

        for config in configs:
            metrics = calculate_metrics(trades, config)

            # Track metrics
            config_metrics[config.name]['sep'].append(metrics['score_separation'])
            config_metrics[config.name]['corr'].append(metrics['correlation'])
            config_metrics[config.name]['delta'].append(metrics['quartile_win_rate_delta'])
            config_metrics[config.name]['pnl'].append(metrics['quartile_pnl_delta'])

            # Composite score
            score = (
                metrics['quartile_win_rate_delta'] * 100 +
                metrics['correlation'] * 50 +
                metrics['score_separation'] * 2
            )

            if score > best_score:
                best_score = score
                best_config = config.name

        config_wins[best_config] += 1

    # Display results
    print("\nWin Counts (which config was best across iterations):")
    print("-" * 60)
    for name, wins in sorted(config_wins.items(), key=lambda x: -x[1]):
        print(f"  {name:<20} won {wins:>3} / {n_iterations} iterations ({wins/n_iterations*100:.1f}%)")

    print("\n" + "-" * 100)
    print(f"{'Config':<18} {'Avg Sep':<10} {'Avg Corr':<10} {'Avg WR Delta':<12} {'Avg PnL Delta':<15}")
    print("-" * 100)

    for config in configs:
        m = config_metrics[config.name]
        avg_sep = statistics.mean(m['sep'])
        avg_corr = statistics.mean(m['corr'])
        avg_delta = statistics.mean(m['delta'])
        avg_pnl = statistics.mean(m['pnl'])
        print(f"{config.name:<18} {avg_sep:>8.1f} {avg_corr:>10.3f} {avg_delta*100:>10.1f}% ${avg_pnl:>12,.0f}")

    # Find most consistent winner
    print("\n" + "=" * 100)
    print("MONTE CARLO CONCLUSION")
    print("=" * 100)

    winner = max(config_wins.items(), key=lambda x: x[1])
    print(f"\nMost Robust Configuration: {winner[0]}")
    print(f"  Won {winner[1]} out of {n_iterations} iterations ({winner[1]/n_iterations*100:.1f}%)")

    # Get its average metrics
    m = config_metrics[winner[0]]
    print(f"\n  Average Metrics:")
    print(f"    Score Separation: {statistics.mean(m['sep']):.1f} (std: {statistics.stdev(m['sep']):.1f})")
    print(f"    Correlation: {statistics.mean(m['corr']):.3f} (std: {statistics.stdev(m['corr']):.3f})")
    print(f"    Win Rate Delta: {statistics.mean(m['delta'])*100:.1f}% (std: {statistics.stdev(m['delta'])*100:.1f}%)")
    print(f"    P&L Delta: ${statistics.mean(m['pnl']):,.0f} (std: ${statistics.stdev(m['pnl']):,.0f})")


if __name__ == "__main__":
    run_ab_tests()
    print("\n\n")
    run_monte_carlo_tests(100)
