"""Tests for backtest.math pure simulation functions."""
from dataclasses import dataclass

from src.application.services.backtest.math import (
    calculate_consistency,
    simulate_pnl,
    calculate_kelly_fraction,
    apply_position_sizing,
)


@dataclass
class FakeTrade:
    """Duck-typed stand-in for BacktestTrade in math tests."""
    simulated_pnl: float
    composite_score: float


class TestCalculateConsistency:
    def test_empty_list_returns_neutral(self):
        assert calculate_consistency([]) == 0.5

    def test_single_move_returns_default(self):
        assert calculate_consistency([5.0]) == 0.6

    def test_identical_moves_return_one(self):
        # std = 0 → CV = 0 → consistency = 1/(1+0) = 1.0
        result = calculate_consistency([5.0, 5.0, 5.0])
        assert result == 1.0

    def test_zero_mean_returns_neutral(self):
        # mean = 0 → return 0.5
        assert calculate_consistency([5.0, -5.0]) == 0.5

    def test_tight_more_consistent_than_wide(self):
        tight = calculate_consistency([5.0, 5.1, 4.9])
        wide = calculate_consistency([1.0, 5.0, 15.0])
        assert tight > wide

    def test_result_bounded(self):
        for moves in [[], [5.0], [1.0, 10.0, 100.0], [5.0, 5.0]]:
            result = calculate_consistency(moves)
            assert 0.0 <= result <= 1.0


class TestSimulatePnl:
    def test_crush_returns_positive(self):
        # Actual move < implied → IV crush → profit
        result = simulate_pnl(actual_move=2.0, avg_historical_move=5.0)
        assert result > 0

    def test_large_move_returns_negative(self):
        # Actual >> implied → large loss
        result = simulate_pnl(actual_move=20.0, avg_historical_move=5.0)
        assert result < 0

    def test_simple_model_differs_from_realistic(self):
        simple = simulate_pnl(3.0, 5.0, use_realistic_model=False)
        realistic = simulate_pnl(3.0, 5.0, use_realistic_model=True)
        assert simple != realistic

    def test_simple_model_formula(self):
        # implied = 5.0 * 1.3 = 6.5
        # premium = 6.5 * 0.5 = 3.25
        # actual (2.0) < implied (6.5) → loss = 0
        # pnl = 3.25
        result = simulate_pnl(2.0, 5.0, use_realistic_model=False)
        assert abs(result - 3.25) < 0.01

    def test_higher_commission_reduces_pnl(self):
        baseline = simulate_pnl(2.0, 5.0, commission_per_contract=0.65)
        higher = simulate_pnl(2.0, 5.0, commission_per_contract=5.00)
        assert baseline > higher


class TestCalculateKellyFraction:
    def test_empty_trades_returns_default(self):
        assert calculate_kelly_fraction([]) == 0.10

    def test_all_winners_returns_default(self):
        trades = [FakeTrade(5.0, 80.0) for _ in range(5)]
        assert calculate_kelly_fraction(trades) == 0.10

    def test_all_losers_returns_default(self):
        trades = [FakeTrade(-3.0, 60.0) for _ in range(5)]
        assert calculate_kelly_fraction(trades) == 0.10

    def test_result_capped_at_quarter_kelly(self):
        # 9 big wins, 1 tiny loss → Kelly would be huge → capped at 0.25
        trades = [FakeTrade(100.0, 80.0) for _ in range(9)] + [FakeTrade(-0.5, 60.0)]
        result = calculate_kelly_fraction(trades)
        assert result <= 0.25

    def test_result_floored_at_5pct(self):
        # Many big losses, 1 small win → Kelly negative → floors at 0.05
        trades = [FakeTrade(-20.0, 60.0) for _ in range(9)] + [FakeTrade(0.1, 80.0)]
        result = calculate_kelly_fraction(trades)
        assert result >= 0.05

    def test_result_in_valid_range(self):
        trades = [FakeTrade(3.0, 70.0), FakeTrade(-2.0, 65.0), FakeTrade(1.0, 72.0)]
        result = calculate_kelly_fraction(trades)
        assert 0.05 <= result <= 0.25


class TestApplyPositionSizing:
    def test_empty_trades_returns_zeros(self):
        assert apply_position_sizing([], 10000.0) == (0.0, 0.0, 0.0)

    def test_modifies_pnl_in_place(self):
        trades = [FakeTrade(5.0, 80.0), FakeTrade(-2.0, 60.0)]
        original_pnls = [t.simulated_pnl for t in trades]
        apply_position_sizing(trades, total_capital=10000.0, use_hybrid=False)
        # PnL should now be in dollars, not the original percentages
        assert trades[0].simulated_pnl != original_pnls[0]

    def test_returns_three_tuple(self):
        trades = [FakeTrade(3.0, 75.0), FakeTrade(-1.0, 60.0)]
        result = apply_position_sizing(trades, total_capital=10000.0)
        kelly_frac, total_pnl, max_dd = result
        assert isinstance(kelly_frac, float)
        assert isinstance(total_pnl, float)
        assert isinstance(max_dd, float)

    def test_max_drawdown_is_non_negative(self):
        trades = [FakeTrade(3.0, 75.0), FakeTrade(-1.0, 60.0), FakeTrade(2.0, 70.0)]
        _, _, max_dd = apply_position_sizing(trades, total_capital=10000.0)
        assert max_dd >= 0.0

    def test_all_winners_max_drawdown_is_zero(self):
        trades = [FakeTrade(3.0, 75.0), FakeTrade(2.0, 70.0), FakeTrade(5.0, 80.0)]
        _, _, max_dd = apply_position_sizing(trades, total_capital=10000.0, use_hybrid=False)
        assert max_dd == 0.0
