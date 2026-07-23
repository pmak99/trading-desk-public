"""BacktestTrade and BacktestResult data types."""
from dataclasses import dataclass
from datetime import date
from typing import List


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
    total_pnl: float  # Total P&L (in dollars if position_sizing=True, else %)
    avg_pnl_per_trade: float  # Average P&L per trade
    sharpe_ratio: float  # Risk-adjusted return
    max_drawdown: float  # Maximum peak-to-trough decline

    # Trade quality metrics
    avg_score_winners: float  # Average score of winning trades
    avg_score_losers: float  # Average score of losing trades

    # Raw trades
    trades: List[BacktestTrade]

    # Position sizing metrics (if enabled)
    position_sizing_enabled: bool = False
    total_capital: float = 0.0  # Total capital deployed
    kelly_fraction: float = 0.0  # Kelly fraction used
