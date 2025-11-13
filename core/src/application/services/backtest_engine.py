"""
Backtest engine for A/B testing scoring configurations.

Simulates historical trades to evaluate which weight configurations
would have performed best.
"""

import logging
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import statistics

from src.config.scoring_config import ScoringConfig
from src.application.services.scorer import TickerScorer, TickerScore

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """
    Individual trade in a backtest run.
    """

    ticker: str
    earnings_date: date

    # Scoring
    composite_score: float
    rank: int
    selected: bool

    # Historical data (known before earnings)
    avg_historical_move: float
    consistency: float
    historical_std: float

    # Actual outcome
    actual_move: float

    # Simulated P&L
    simulated_pnl: float

    # Trade metadata
    run_id: str
    config_name: str


@dataclass
class BacktestResult:
    """
    Results from a backtest run with aggregate metrics.
    """

    run_id: str
    config_name: str
    config_description: str

    # Date range
    start_date: date
    end_date: date

    # Opportunity metrics
    total_opportunities: int  # Total earnings events in period
    qualified_opportunities: int  # Met minimum score threshold
    selected_trades: int  # Actually selected for trading

    # Performance metrics (selected trades only)
    win_rate: float  # % of profitable trades
    total_pnl: float  # Total P&L
    avg_pnl_per_trade: float  # Average P&L per trade
    sharpe_ratio: float  # Risk-adjusted return
    max_drawdown: float  # Maximum peak-to-trough decline

    # Trade quality metrics
    avg_score_winners: float  # Average score of winning trades
    avg_score_losers: float  # Average score of losing trades

    # Raw trades
    trades: List[BacktestTrade]


class BacktestEngine:
    """
    Engine for backtesting scoring configurations.

    Loads historical data, scores tickers using different configs,
    simulates trades, and calculates performance metrics.
    """

    def __init__(self, db_path: Path):
        """
        Initialize backtest engine.

        Args:
            db_path: Path to SQLite database with historical moves
        """
        self.db_path = db_path

    def get_historical_moves(
        self,
        ticker: str,
        before_date: date,
        num_quarters: int = 4,
    ) -> List[Tuple[date, float]]:
        """
        Get historical moves for a ticker before a specific date.

        Args:
            ticker: Stock symbol
            before_date: Only include moves before this date
            num_quarters: Number of past quarters to include

        Returns:
            List of (date, move_pct) tuples
        """
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        cursor = conn.cursor()

        cursor.execute(
            '''
            SELECT earnings_date, close_move_pct
            FROM historical_moves
            WHERE ticker = ?
              AND earnings_date < ?
            ORDER BY earnings_date DESC
            LIMIT ?
            ''',
            (ticker, str(before_date), num_quarters),
        )

        moves = [
            (date.fromisoformat(row[0]), row[1])
            for row in cursor.fetchall()
        ]

        conn.close()
        return moves

    def calculate_consistency(self, moves: List[float]) -> float:
        """
        Calculate consistency score from historical moves.

        Uses coefficient of variation (lower is more consistent).

        Args:
            moves: List of historical move percentages

        Returns:
            Consistency score (0-1, higher is more consistent)
        """
        if len(moves) == 0:
            return 0.5  # Neutral if no data

        if len(moves) == 1:
            # With only 1 move, assume moderate consistency
            return 0.6  # Slightly above neutral (benefit of the doubt)

        mean = statistics.mean(moves)
        std = statistics.stdev(moves)

        if mean == 0:
            return 0.5

        # Coefficient of variation
        cv = std / abs(mean)

        # Convert to 0-1 score (lower CV = higher consistency)
        # CV of 0.5 = score 0.5, CV of 0 = score 1.0, CV of 1.0+ = score ~0
        consistency = 1.0 / (1.0 + cv)

        return max(0.0, min(1.0, consistency))

    def simulate_pnl(
        self,
        actual_move: float,
        avg_historical_move: float,
    ) -> float:
        """
        Simulate P&L for a straddle trade.

        Simplified model:
        - Assume we sell ATM straddle
        - Premium collected = 50% of implied move (simplified)
        - Implied move = avg_historical_move * 1.3 (typical IV inflation)
        - P&L = premium - max(0, actual_move - implied_move) * stock_price

        Further simplified as percentage:
        P&L% = implied_move * 0.5 - max(0, actual_move - implied_move)

        Args:
            actual_move: Actual move percentage
            avg_historical_move: Average historical move percentage

        Returns:
            Simulated P&L as percentage of stock price
        """
        # Assume implied move is historical * 1.3 (typical IV inflation for earnings)
        implied_move = avg_historical_move * 1.3

        # Premium collected (50% of straddle cost, which equals implied move)
        premium = implied_move * 0.5

        # Loss if actual exceeds implied
        loss = max(0, actual_move - implied_move)

        # Net P&L
        pnl = premium - loss

        return pnl

    def get_all_earnings_in_period(
        self,
        start_date: date,
        end_date: date,
    ) -> List[Tuple[str, date, float]]:
        """
        Get all earnings events in a date range.

        Args:
            start_date: Start of period
            end_date: End of period

        Returns:
            List of (ticker, earnings_date, actual_move) tuples
        """
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        cursor = conn.cursor()

        cursor.execute(
            '''
            SELECT ticker, earnings_date, close_move_pct
            FROM historical_moves
            WHERE earnings_date >= ?
              AND earnings_date <= ?
            ORDER BY earnings_date, ticker
            ''',
            (str(start_date), str(end_date)),
        )

        events = [
            (row[0], date.fromisoformat(row[1]), row[2])
            for row in cursor.fetchall()
        ]

        conn.close()
        return events

    def run_backtest(
        self,
        config: ScoringConfig,
        start_date: date,
        end_date: date,
    ) -> BacktestResult:
        """
        Run backtest for a specific configuration.

        Args:
            config: Scoring configuration to test
            start_date: Start of backtest period
            end_date: End of backtest period

        Returns:
            BacktestResult with performance metrics and trades
        """
        run_id = str(uuid.uuid4())[:8]
        scorer = TickerScorer(config)

        logger.info(f"Running backtest: {config.name} ({run_id})")
        logger.info(f"Period: {start_date} to {end_date}")

        # Get all earnings events in period
        events = self.get_all_earnings_in_period(start_date, end_date)
        logger.info(f"Found {len(events)} earnings events")

        # Score each event based on known historical data
        scores: List[TickerScore] = []

        for ticker, earnings_date, actual_move in events:
            # Get historical moves BEFORE this earnings
            historical_moves = self.get_historical_moves(
                ticker, earnings_date, num_quarters=8
            )

            if len(historical_moves) < 1:
                # Skip if no historical data at all
                continue

            move_pcts = [move_pct for _, move_pct in historical_moves]
            avg_move = statistics.mean(move_pcts)
            consistency = self.calculate_consistency(move_pcts)
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

            # Simulate P&L
            pnl = self.simulate_pnl(actual_move, avg_move)

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

        # Performance metrics
        winners = [t for t in selected_trades if t.simulated_pnl > 0]
        losers = [t for t in selected_trades if t.simulated_pnl <= 0]

        win_rate = len(winners) / len(selected_trades) * 100
        total_pnl = sum(t.simulated_pnl for t in selected_trades)
        avg_pnl = total_pnl / len(selected_trades)

        # Sharpe ratio (simplified)
        pnls = [t.simulated_pnl for t in selected_trades]
        sharpe = (
            statistics.mean(pnls) / statistics.stdev(pnls)
            if len(pnls) > 1 and statistics.stdev(pnls) > 0
            else 0.0
        )

        # Max drawdown (simplified: cumulative worst losing streak)
        cumulative_pnl = 0
        peak_pnl = 0
        max_drawdown = 0

        for trade in selected_trades:
            cumulative_pnl += trade.simulated_pnl
            peak_pnl = max(peak_pnl, cumulative_pnl)
            drawdown = peak_pnl - cumulative_pnl
            max_drawdown = max(max_drawdown, drawdown)

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
        )

        logger.info(f"Backtest complete: {config.name}")
        logger.info(f"  Selected: {len(selected_trades)} trades")
        logger.info(f"  Win rate: {win_rate:.1f}%")
        logger.info(f"  Total P&L: {total_pnl:.2f}%")
        logger.info(f"  Sharpe: {sharpe:.2f}")

        return result
