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
