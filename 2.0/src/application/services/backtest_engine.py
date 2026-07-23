"""Backward-compat shim — all code lives in src.application.services.backtest."""
from src.application.services.backtest import BacktestEngine
from src.application.services.backtest.types import BacktestTrade, BacktestResult

__all__ = ["BacktestEngine", "BacktestTrade", "BacktestResult"]
