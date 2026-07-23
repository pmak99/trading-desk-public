"""Backtest orchestration: run_backtest and run_walk_forward_backtest."""
import logging
import statistics
import uuid
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import List, Dict

from src.config.scoring_config import ScoringConfig
from src.application.services.scorer import TickerScorer, TickerScore
from src.application.services.backtest.types import BacktestTrade, BacktestResult
from src.application.services.backtest.db import get_historical_moves, get_all_earnings_in_period
from src.application.services.backtest.math import (
    calculate_consistency, simulate_pnl, apply_position_sizing,
)

logger = logging.getLogger(__name__)


def run_backtest(
    db_path: Path,
    config: ScoringConfig,
    start_date: date,
    end_date: date,
    position_sizing: bool = False,
    total_capital: float = 40000.0,
) -> BacktestResult:
    """
    Run backtest for a specific configuration.

    Args:
        db_path: Path to SQLite database
        config: Scoring configuration to test
        start_date: Start of backtest period
        end_date: End of backtest period
        position_sizing: Whether to apply Kelly + VRP position sizing
        total_capital: Total capital if position_sizing=True

    Returns:
        BacktestResult with performance metrics and trades
    """
    run_id = str(uuid.uuid4())[:8]
    scorer = TickerScorer(config)

    logger.info(f"Running backtest: {config.name} ({run_id})")
    logger.info(f"Period: {start_date} to {end_date}")

    # Get all earnings events in period
    events = get_all_earnings_in_period(db_path, start_date, end_date)
    logger.info(f"Found {len(events)} earnings events")

    # Score each event based on known historical data
    scores: List = []

    for ticker, earnings_date, actual_move in events:
        # Get historical moves BEFORE this earnings
        historical_moves = get_historical_moves(
            db_path, ticker, earnings_date, num_quarters=8
        )

        if len(historical_moves) < 1:
            # Skip if no historical data at all
            continue

        move_pcts = [move_pct for _, move_pct in historical_moves]
        avg_move = statistics.mean(move_pcts)
        consistency = calculate_consistency(move_pcts)
        std_move = statistics.stdev(move_pcts) if len(move_pcts) > 1 else 0

        # For backtesting, simulate "VRP ratio" as if implied move = avg historical * 1.4
        # This represents typical IV inflation (40%) for earnings
        # A high consistency + high avg move = attractive opportunity
        simulated_implied = avg_move * 1.4
        simulated_vrp = simulated_implied / avg_move if avg_move > 0 else 1.4

        # Score the ticker using simulated VRP and actual consistency
        # Liquidity values simulate reasonable market liquidity
        score = scorer.score_ticker(
            ticker=ticker,
            earnings_date=earnings_date,
            vrp_ratio=simulated_vrp,  # Simulated: 40% IV inflation (typical for earnings)
            consistency=consistency,
            iv_crush_rate=None,  # Not available in backtest simulation
            skew=None,  # No historical skew data (defaults to neutral 75/100)
            avg_historical_move=avg_move,
            open_interest=300,  # Good liquidity (above minimum, below excellent)
            bid_ask_spread_pct=9.0,  # Good spread (between marginal and excellent)
            volume=100,  # Good volume (meets good threshold)
        )

        # Store actual move for P&L calculation
        score.vrp_ratio = actual_move / avg_move if avg_move > 0 else 0
        scores.append((score, actual_move, avg_move, std_move))

    # Rank and select based on scores
    ticker_scores = [s[0] for s in scores]
    ranked_scores = scorer.rank_and_select(ticker_scores)

    # Create detailed trades with outcomes
    trades: List[BacktestTrade] = []

    for i, (score, actual_move, avg_move, std_move) in enumerate(scores):
        # Find corresponding ranked score
        ranked = next(
            (s for s in ranked_scores
             if s.ticker == score.ticker and s.earnings_date == score.earnings_date),
            score
        )

        # Simulate P&L with realistic costs
        # Use $100 as default stock price for commission calculation
        # (commission impact is minimal for most stocks $50-$500)
        # Use 10% bid-ask spread (typical for earnings straddles)
        pnl = simulate_pnl(
            actual_move=actual_move,
            avg_historical_move=avg_move,
            stock_price=100.0,
            bid_ask_spread_pct=0.10,
            use_realistic_model=True,
        )

        trade = BacktestTrade(
            ticker=score.ticker,
            earnings_date=score.earnings_date,
            composite_score=score.composite_score,
            rank=ranked.rank or 999,
            selected=ranked.selected,
            avg_historical_move=avg_move,
            consistency=score.consistency or 0,
            historical_std=std_move,
            actual_move=actual_move,
            simulated_pnl=pnl,
            run_id=run_id,
            config_name=config.name,
        )

        trades.append(trade)

    # Calculate aggregate metrics for SELECTED trades only
    selected_trades = [t for t in trades if t.selected]

    if not selected_trades:
        logger.warning(f"No trades selected for {config.name}")
        return BacktestResult(
            run_id=run_id,
            config_name=config.name,
            config_description=config.description,
            start_date=start_date,
            end_date=end_date,
            total_opportunities=len(events),
            qualified_opportunities=len([s for s in ranked_scores]),
            selected_trades=0,
            win_rate=0.0,
            total_pnl=0.0,
            avg_pnl_per_trade=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            avg_score_winners=0.0,
            avg_score_losers=0.0,
            trades=trades,
        )

    # Apply position sizing if enabled
    kelly_frac = 0.0
    if position_sizing:
        kelly_frac, total_pnl_dollars, max_dd_pct = apply_position_sizing(
            selected_trades,
            total_capital=total_capital,
            use_hybrid=True,
        )

        # After position sizing, P&L is in dollars, drawdown is in %
        total_pnl = total_pnl_dollars
        avg_pnl = total_pnl / len(selected_trades)
        max_drawdown = max_dd_pct

        # Sharpe ratio with dollar P&L
        pnls = [t.simulated_pnl for t in selected_trades]
        sharpe = (
            (statistics.mean(pnls) / statistics.stdev(pnls)) * (50 ** 0.5)
            if len(pnls) > 1 and statistics.stdev(pnls) > 0
            else 0.0
        )

    else:
        # Original percentage-based metrics
        total_pnl = sum(t.simulated_pnl for t in selected_trades)
        avg_pnl = total_pnl / len(selected_trades)

        # Sharpe ratio (simplified)
        pnls = [t.simulated_pnl for t in selected_trades]
        sharpe = (
            statistics.mean(pnls) / statistics.stdev(pnls)
            if len(pnls) > 1 and statistics.stdev(pnls) > 0
            else 0.0
        )

        # Max drawdown (as percentage of running equity)
        # Start with 100% equity, compound returns
        equity = 100.0
        peak_equity = 100.0
        max_drawdown_pct = 0.0

        for trade in selected_trades:
            # Apply trade return to equity
            equity = equity * (1 + trade.simulated_pnl / 100.0)
            peak_equity = max(peak_equity, equity)

            # Calculate drawdown as % from peak
            if peak_equity > 0:
                drawdown_pct = (peak_equity - equity) / peak_equity * 100.0
                max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

        max_drawdown = max_drawdown_pct

    # Performance metrics
    winners = [t for t in selected_trades if t.simulated_pnl > 0]
    losers = [t for t in selected_trades if t.simulated_pnl <= 0]

    win_rate = len(winners) / len(selected_trades) * 100

    # Trade quality
    avg_score_winners = (
        statistics.mean([t.composite_score for t in winners])
        if winners else 0
    )
    avg_score_losers = (
        statistics.mean([t.composite_score for t in losers])
        if losers else 0
    )

    result = BacktestResult(
        run_id=run_id,
        config_name=config.name,
        config_description=config.description,
        start_date=start_date,
        end_date=end_date,
        total_opportunities=len(events),
        qualified_opportunities=len(ranked_scores),
        selected_trades=len(selected_trades),
        win_rate=win_rate,
        total_pnl=total_pnl,
        avg_pnl_per_trade=avg_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        avg_score_winners=avg_score_winners,
        avg_score_losers=avg_score_losers,
        trades=trades,
        position_sizing_enabled=position_sizing,
        total_capital=total_capital if position_sizing else 0.0,
        kelly_fraction=kelly_frac,
    )

    logger.info(f"Backtest complete: {config.name}")
    logger.info(f"  Selected: {len(selected_trades)} trades")
    logger.info(f"  Win rate: {win_rate:.1f}%")
    logger.info(f"  Total P&L: {total_pnl:.2f}%")
    logger.info(f"  Sharpe: {sharpe:.2f}")

    return result


def run_walk_forward_backtest(
    db_path: Path,
    configs: List[ScoringConfig],
    start_date: date,
    end_date: date,
    train_window_days: int = 180,
    test_window_days: int = 90,
    step_days: int = 90,
) -> Dict:
    """
    Walk-forward optimization to prevent overfitting.

    Process:
    1. Train on window 1 (e.g., 6 months) → select best config
    2. Test on window 2 (e.g., 3 months) → validate performance
    3. Roll window forward by step_days
    4. Repeat until end of date range

    Args:
        db_path: Path to SQLite database
        configs: List of scoring configurations to test
        start_date: Start of backtest period
        end_date: End of backtest period
        train_window_days: Training window size (default 180 days / 6 months)
        test_window_days: Testing window size (default 90 days / 3 months)
        step_days: Days to roll window forward (default 90 days)

    Returns:
        Dictionary with keys:
        - "train_results": List of training period results for each window
        - "test_results": List of test period results (out-of-sample)
        - "best_configs": List of best config names per window
        - "summary": Aggregate statistics
    """
    logger.info("=" * 80)
    logger.info("WALK-FORWARD BACKTEST")
    logger.info(f"Period: {start_date} to {end_date}")
    logger.info(f"Train window: {train_window_days} days")
    logger.info(f"Test window: {test_window_days} days")
    logger.info(f"Step: {step_days} days")
    logger.info(f"Configs: {len(configs)}")
    logger.info("=" * 80)

    train_results = []
    test_results = []
    best_configs = []

    # Generate windows
    current_train_start = start_date
    window_num = 0

    while True:
        window_num += 1

        # Calculate window dates
        current_train_end = current_train_start + timedelta(days=train_window_days)
        current_test_start = current_train_end + timedelta(days=1)
        current_test_end = current_test_start + timedelta(days=test_window_days)

        # Stop if test window goes beyond end date
        if current_test_end > end_date:
            logger.info(f"Stopping: test window would exceed {end_date}")
            break

        logger.info(f"\n--- Window {window_num} ---")
        logger.info(f"Train: {current_train_start} to {current_train_end}")
        logger.info(f"Test:  {current_test_start} to {current_test_end}")

        # Phase 1: Train on training window with all configs
        window_train_results = []
        for config in configs:
            result = run_backtest(
                db_path=db_path,
                config=config,
                start_date=current_train_start,
                end_date=current_train_end,
            )
            window_train_results.append(result)
            train_results.append(result)

        # Select best config based on training performance
        # Use Sharpe ratio as primary metric (risk-adjusted return)
        best_config_result = max(
            window_train_results,
            key=lambda r: r.sharpe_ratio if r.selected_trades > 0 else -999
        )
        best_config_name = best_config_result.config_name
        best_configs.append(best_config_name)

        logger.info(f"Best config (train): {best_config_name}")
        logger.info(f"  Train Sharpe: {best_config_result.sharpe_ratio:.2f}")
        logger.info(f"  Train Win Rate: {best_config_result.win_rate:.1f}%")
        logger.info(f"  Train Trades: {best_config_result.selected_trades}")

        # Phase 2: Test best config on test window (out-of-sample)
        best_config = next(c for c in configs if c.name == best_config_name)
        test_result = run_backtest(
            db_path=db_path,
            config=best_config,
            start_date=current_test_start,
            end_date=current_test_end,
        )
        test_results.append(test_result)

        logger.info(f"Best config (test): {best_config_name}")
        logger.info(f"  Test Sharpe: {test_result.sharpe_ratio:.2f}")
        logger.info(f"  Test Win Rate: {test_result.win_rate:.1f}%")
        logger.info(f"  Test Trades: {test_result.selected_trades}")
        logger.info(f"  Test P&L: {test_result.total_pnl:.2f}%")

        # Advance window
        current_train_start += timedelta(days=step_days)

    # Calculate summary statistics
    if test_results:
        total_test_trades = sum(r.selected_trades for r in test_results)
        avg_test_sharpe = statistics.mean([r.sharpe_ratio for r in test_results if r.selected_trades > 0])
        avg_test_win_rate = statistics.mean([r.win_rate for r in test_results if r.selected_trades > 0])
        total_test_pnl = sum(r.total_pnl for r in test_results)

        # Count config selections
        config_counts = Counter(best_configs)

        summary = {
            "total_windows": window_num - 1,
            "total_test_trades": total_test_trades,
            "avg_test_sharpe": avg_test_sharpe,
            "avg_test_win_rate": avg_test_win_rate,
            "total_test_pnl": total_test_pnl,
            "config_selection_counts": dict(config_counts),
            "most_selected_config": config_counts.most_common(1)[0] if config_counts else None,
        }

        logger.info("\n" + "=" * 80)
        logger.info("WALK-FORWARD SUMMARY (Out-of-Sample Performance)")
        logger.info("=" * 80)
        logger.info(f"Total windows: {summary['total_windows']}")
        logger.info(f"Total test trades: {summary['total_test_trades']}")
        logger.info(f"Avg test Sharpe: {summary['avg_test_sharpe']:.2f}")
        logger.info(f"Avg test win rate: {summary['avg_test_win_rate']:.1f}%")
        logger.info(f"Total test P&L: {summary['total_test_pnl']:.2f}%")
        logger.info(f"\nConfig selection counts:")
        for config_name, count in config_counts.most_common():
            logger.info(f"  {config_name}: {count} times")
        logger.info("=" * 80)
    else:
        summary = {
            "total_windows": 0,
            "error": "No test windows completed"
        }

    return {
        "train_results": train_results,
        "test_results": test_results,
        "best_configs": best_configs,
        "summary": summary,
    }
