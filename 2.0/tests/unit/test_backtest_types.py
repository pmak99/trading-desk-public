"""Smoke tests for backtest data types."""
from datetime import date

from src.application.services.backtest.types import BacktestTrade, BacktestResult


class TestBacktestTrade:
    def test_all_fields(self):
        t = BacktestTrade(
            ticker="AAPL", earnings_date=date(2026, 1, 15),
            composite_score=75.0, rank=1, selected=True,
            avg_historical_move=5.0, consistency=0.8, historical_std=1.2,
            actual_move=3.5, simulated_pnl=1.2, run_id="abc123", config_name="Balanced",
        )
        assert t.ticker == "AAPL"
        assert t.selected is True
        assert t.run_id == "abc123"

    def test_is_mutable(self):
        t = BacktestTrade(
            ticker="AAPL", earnings_date=date(2026, 1, 15),
            composite_score=75.0, rank=1, selected=True,
            avg_historical_move=5.0, consistency=0.8, historical_std=1.2,
            actual_move=3.5, simulated_pnl=1.2, run_id="x", config_name="y",
        )
        t.simulated_pnl = 99.9  # apply_position_sizing modifies this in place
        assert t.simulated_pnl == 99.9


class TestBacktestResult:
    def test_all_required_fields(self):
        r = BacktestResult(
            run_id="abc", config_name="test", config_description="desc",
            start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
            total_opportunities=100, qualified_opportunities=50, selected_trades=20,
            win_rate=65.0, total_pnl=15.2, avg_pnl_per_trade=0.76,
            sharpe_ratio=1.5, max_drawdown=5.0,
            avg_score_winners=80.0, avg_score_losers=65.0, trades=[],
        )
        assert r.win_rate == 65.0
        assert r.trades == []

    def test_optional_fields_default(self):
        r = BacktestResult(
            run_id="abc", config_name="test", config_description="desc",
            start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
            total_opportunities=10, qualified_opportunities=5, selected_trades=3,
            win_rate=66.7, total_pnl=5.0, avg_pnl_per_trade=1.67,
            sharpe_ratio=1.0, max_drawdown=2.0,
            avg_score_winners=80.0, avg_score_losers=60.0, trades=[],
        )
        assert r.position_sizing_enabled is False
        assert r.total_capital == 0.0
        assert r.kelly_fraction == 0.0
