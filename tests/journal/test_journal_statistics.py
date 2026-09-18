import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from journal.models import Trade
from journal.statistics import group_trades_into_strategies


def make_trade(symbol="NVDA", option_type="PUT", strike=150.0, expiration="2025-01-17", gain_loss=100.0):
    return Trade(
        symbol=symbol, description="", quantity=1, acquired_date="2025-01-10",
        sale_date="2025-01-13", cost_basis=0.0, proceeds=0.0, gain_loss=gain_loss,
        term="SHORT", option_type=option_type, strike=strike, expiration=expiration,
    )


class TestGroupTradesIntoStrategies:
    def test_single_strike_becomes_single(self):
        trades = [make_trade(strike=150.0, gain_loss=100.0)]
        strategies = group_trades_into_strategies(trades)
        assert len(strategies) == 1
        assert strategies[0].strategy_type == "SINGLE"

    def test_two_strikes_becomes_spread(self):
        trades = [
            make_trade(strike=150.0, gain_loss=100.0),
            make_trade(strike=155.0, gain_loss=-40.0),
        ]
        strategies = group_trades_into_strategies(trades)
        assert len(strategies) == 1
        assert strategies[0].strategy_type == "SPREAD"
        assert strategies[0].gain_loss == 60.0

    def test_three_plus_strikes_grouped_as_one_spread(self):
        trades = [
            make_trade(strike=150.0, gain_loss=10.0),
            make_trade(strike=155.0, gain_loss=10.0),
            make_trade(strike=160.0, gain_loss=10.0),
        ]
        strategies = group_trades_into_strategies(trades)
        assert len(strategies) == 1
        assert strategies[0].strategy_type == "SPREAD"
        assert len(strategies[0].legs) == 3

    def test_stock_trade_becomes_stock_type(self):
        trades = [make_trade(option_type=None, strike=None, expiration=None, gain_loss=500.0)]
        strategies = group_trades_into_strategies(trades)
        assert strategies[0].strategy_type == "STOCK"

    def test_puts_and_calls_grouped_separately(self):
        trades = [
            make_trade(option_type="PUT", strike=150.0, gain_loss=10.0),
            make_trade(option_type="CALL", strike=150.0, gain_loss=20.0),
        ]
        strategies = group_trades_into_strategies(trades)
        # Same symbol/expiration/strike but different option_type -> 2 separate SINGLEs
        assert len(strategies) == 2
        assert {s.strategy_type for s in strategies} == {"SINGLE"}


class TestStrategyStatisticsWashSales:
    """The parser's summary reads wash_sales from calculate_strategy_statistics,
    which returned a count of 0 whatever the lots carried."""

    def test_counts_every_lot_with_an_adjustment_and_totals_them(self):
        from dataclasses import replace

        from journal.statistics import calculate_strategy_statistics

        trades = [
            # A spread whose short leg carried a disallowed loss; the long leg none.
            replace(make_trade(strike=150.0, gain_loss=0.0), wash_sale_amount=57.02),
            make_trade(strike=145.0, gain_loss=-40.0),
            # A single with two wash rows folded into one lot counts once.
            replace(make_trade(symbol="MU", strike=145.0, gain_loss=-10.0), wash_sale_amount=1250.50),
            make_trade(symbol="AAPL", option_type="CALL", strike=225.0, gain_loss=80.0),
        ]
        stats = calculate_strategy_statistics(group_trades_into_strategies(trades))
        assert stats["wash_sales"] == {"count": 2, "total": 1307.52}

    def test_no_adjustments_is_zero(self):
        from journal.statistics import calculate_strategy_statistics

        stats = calculate_strategy_statistics(group_trades_into_strategies([make_trade()]))
        assert stats["wash_sales"] == {"count": 0, "total": 0.0}


class TestStrategyStatisticsProfitFactor:
    """Profit factor is winners' P&L over losers' loss. It printed 0 for a book
    with no losing dollars — the worst possible reading of the best possible book —
    and a journal with no trades crashed the summary before the files were written."""

    def test_is_winners_over_losers(self):
        from journal.statistics import calculate_strategy_statistics

        trades = [
            make_trade(symbol="NVDA", gain_loss=400.0),
            make_trade(symbol="MU", gain_loss=-150.0),
        ]
        stats = calculate_strategy_statistics(group_trades_into_strategies(trades))
        assert stats["profit_factor"] == 2.67

    def test_is_undefined_when_nothing_lost(self):
        from journal.statistics import calculate_strategy_statistics

        trades = [
            make_trade(symbol="NVDA", gain_loss=400.0),
            # A breakeven lot is not a winner, but it lost nothing.
            make_trade(symbol="AVGO", gain_loss=0.0),
        ]
        stats = calculate_strategy_statistics(group_trades_into_strategies(trades))
        assert (stats["winners"], stats["losers"]) == (1, 1)
        assert stats["profit_factor"] is None

    def test_no_trades_is_undefined_not_empty(self):
        from journal.statistics import calculate_strategy_statistics

        stats = calculate_strategy_statistics([])
        assert (stats["total_trades"], stats["total_pnl"]) == (0, 0)
        assert stats["profit_factor"] is None

    def test_summary_prints_undefined_as_na(self, capsys):
        from journal.reporting import print_summary
        from journal.statistics import calculate_strategy_statistics

        print_summary(calculate_strategy_statistics(group_trades_into_strategies(
            [make_trade(gain_loss=400.0)])))
        print_summary(calculate_strategy_statistics([]))
        lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
        assert lines.count("Profit Factor:   n/a") == 2
        assert "Total Trades:    0" in lines


class TestStrategyStatisticsUndefinedRatios:
    """Win rate divides by the trades, average win by the winners, average loss by
    the losers. With none to divide by, the summary printed "Win Rate: 0%" and
    "$0.00" averages -- what a book of all losers prints. They are n/a, like
    profit factor."""

    def test_no_trades_leaves_every_ratio_undefined(self):
        from journal.statistics import calculate_strategy_statistics

        stats = calculate_strategy_statistics([])
        assert (stats["win_rate"], stats["avg_win"], stats["avg_loss"],
                stats["profit_factor"]) == (None, None, None, None)

    def test_an_average_is_undefined_only_without_its_own_trades(self):
        from journal.statistics import calculate_strategy_statistics

        winners = calculate_strategy_statistics(group_trades_into_strategies(
            [make_trade(gain_loss=400.0)]))
        assert (winners["win_rate"], winners["avg_win"], winners["avg_loss"]) == (100.0, 400.0, None)

        losers = calculate_strategy_statistics(group_trades_into_strategies(
            [make_trade(gain_loss=-150.0)]))
        # No winner in one trade is a 0% win rate, not an undefined one.
        assert (losers["win_rate"], losers["avg_win"], losers["avg_loss"]) == (0.0, None, -150.0)

    def test_summary_prints_undefined_ratios_as_na(self, capsys):
        from journal.reporting import print_summary
        from journal.statistics import calculate_strategy_statistics

        print_summary(calculate_strategy_statistics([]))
        lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
        for label in ("Win Rate:", "Avg Win:", "Avg Loss:", "Profit Factor:"):
            assert f"{label:17}n/a" in lines, label

        print_summary(calculate_strategy_statistics(group_trades_into_strategies(
            [make_trade(gain_loss=-150.0)])))
        lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
        assert "Win Rate:        0.0%" in lines
        assert "Avg Win:         n/a" in lines
        assert "Avg Loss:        $-150.00" in lines
