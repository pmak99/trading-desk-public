"""BacktestEngine — thin coordinator delegating to focused backtest modules."""
from pathlib import Path
from datetime import date
from typing import List, Dict

from src.application.services.backtest.types import BacktestTrade, BacktestResult
from src.application.services.backtest import db, runner
from src.application.services.backtest import math as bmath
from src.config.scoring_config import ScoringConfig

__all__ = ["BacktestEngine", "BacktestTrade", "BacktestResult"]


class BacktestEngine:
    """
    Engine for backtesting scoring configurations.

    Thin coordinator: all computation is in backtest.db, backtest.math, backtest.runner.
    Constructor signature and method signatures are identical to the original monolith.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def get_historical_moves(self, ticker, before_date, num_quarters=4):
        return db.get_historical_moves(self.db_path, ticker, before_date, num_quarters)

    def get_all_earnings_in_period(self, start_date, end_date):
        return db.get_all_earnings_in_period(self.db_path, start_date, end_date)

    def calculate_consistency(self, moves):
        return bmath.calculate_consistency(moves)

    def simulate_pnl(self, actual_move, avg_historical_move, stock_price=100.0,
                     bid_ask_spread_pct=0.10, commission_per_contract=0.65,
                     use_realistic_model=True):
        return bmath.simulate_pnl(
            actual_move, avg_historical_move, stock_price,
            bid_ask_spread_pct, commission_per_contract, use_realistic_model,
        )

    def calculate_kelly_fraction(self, trades):
        return bmath.calculate_kelly_fraction(trades)

    def apply_position_sizing(self, trades, total_capital=40000.0, use_hybrid=True):
        return bmath.apply_position_sizing(trades, total_capital, use_hybrid)

    def run_backtest(self, config, start_date, end_date,
                     position_sizing=False, total_capital=40000.0):
        return runner.run_backtest(
            self.db_path, config, start_date, end_date, position_sizing, total_capital
        )

    def run_walk_forward_backtest(self, configs, start_date, end_date,
                                  train_window_days=180, test_window_days=90, step_days=90):
        return runner.run_walk_forward_backtest(
            self.db_path, configs, start_date, end_date,
            train_window_days, test_window_days, step_days,
        )
