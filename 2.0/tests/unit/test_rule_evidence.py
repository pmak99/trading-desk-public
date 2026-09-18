"""Tests for rule_evidence.py, the one place CLAUDE.md's trade evidence comes from.

The audit of 2026-09-13 found the DTE, sizing and TRR evidence in CLAUDE.md did
not reproduce from `strategies` under any of four definitions. The definitions
are now fixed in code and tested here, and the gate at the bottom fails when a
figure in CLAUDE.md or README.md stops matching the book.

§6's return on max loss came from an ad-hoc audit script, deleted after it ran on
137 spread rows; `strategies` has no max loss, so it is rebuilt here from the
journal legs and tested on small books whose max loss is worked by hand.
"""

import re
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from common.db import connect
from common.pgtesting import baseline_ddl
from scripts.rule_evidence import (
    Leg,
    Trade,
    Unbuildable,
    compute,
    dte_band,
    held_band,
    is_earnings_trade,
    is_index_option,
    load_book,
    load_legs,
    log_optimal_fractions,
    max_loss,
    missing_fragments,
    money,
    rebuild,
    render_tables,
    resample_counts,
    size_band,
    tally,
    trading_days_held,
    trr_tier_at_entry,
)

REPO = Path(__file__).resolve().parents[3]


def _trade(**kw) -> Trade:
    base = {"id": 1, "symbol": "TEST", "strategy_type": "SPREAD", "account_type": "TAXABLE",
            "acquired_date": date(2026, 5, 4), "sale_date": date(2026, 5, 5),
            "expiration": date(2026, 5, 8), "quantity": 50.0, "gain_loss": 100.0,
            "earnings_date": date(2026, 5, 4), "parent_strategy_id": None}
    base.update(kw)
    base.setdefault("strategy_ids", (base["id"],))
    return Trade(**base)


class TestDefinitions:
    def test_a_win_is_positive_net_pnl_and_breakeven_is_not(self):
        assert _trade(gain_loss=0.01).won
        assert not _trade(gain_loss=0.0).won
        assert not _trade(gain_loss=-5.0).won

    def test_index_options_exclude_index_stock(self):
        assert is_index_option(_trade(symbol="SPX", strategy_type="SINGLE"))
        assert is_index_option(_trade(symbol="SPXW", strategy_type="SPREAD"))
        assert not is_index_option(_trade(symbol="SPY", strategy_type="STOCK"))
        assert not is_index_option(_trade(symbol="VOO", strategy_type="SINGLE"))

    def test_earnings_trade_is_a_dated_non_index_spread_or_single(self):
        assert is_earnings_trade(_trade())
        assert is_earnings_trade(_trade(strategy_type="SINGLE"))
        assert not is_earnings_trade(_trade(earnings_date=None))
        assert not is_earnings_trade(_trade(symbol="SPY"))
        assert not is_earnings_trade(_trade(strategy_type="IRON_CONDOR"))
        assert not is_earnings_trade(_trade(strategy_type="STOCK"))

    def test_dte_is_expiration_minus_entry(self):
        entry = date(2026, 5, 4)
        assert dte_band(_trade(acquired_date=entry, expiration=date(2026, 5, 6))) == "0–2"
        assert dte_band(_trade(acquired_date=entry, expiration=date(2026, 5, 7))) == "3–5"
        assert dte_band(_trade(acquired_date=entry, expiration=date(2026, 5, 9))) == "3–5"
        assert dte_band(_trade(acquired_date=entry, expiration=date(2026, 5, 10))) == "6+"

    def test_size_bands(self):
        assert [size_band(q) for q in (1, 50, 51, 100, 101, 200, 201)] == [
            "1–50", "1–50", "51–100", "51–100", "101–200", "101–200", "200+"]

    def test_holding_is_counted_in_trading_days(self):
        # Friday entry, Monday exit is the next trading day.
        assert trading_days_held(date(2026, 5, 8), date(2026, 5, 11)) == 1
        # Thursday 2025-07-03 to Monday 2025-07-07 skips the July 4 holiday.
        assert trading_days_held(date(2025, 7, 3), date(2025, 7, 7)) == 1
        assert trading_days_held(date(2026, 5, 4), date(2026, 5, 4)) == 0
        assert trading_days_held(date(2026, 5, 4), date(2026, 5, 6)) == 2
        assert held_band(_trade(acquired_date=date(2026, 5, 8),
                                sale_date=date(2026, 5, 11))) == "0–1"
        assert held_band(_trade(acquired_date=date(2026, 5, 4),
                                sale_date=date(2026, 5, 6))) == "2+"

    def test_trr_tier_is_as_of_entry_and_excludes_the_entry_day_event(self):
        history = {"TEST": [(date(2025, 2, 4), 2.0), (date(2025, 5, 4), 2.0), (date(2025, 8, 4), 2.0),
                            (date(2025, 11, 4), 2.0), (date(2026, 5, 4), 40.0)]}
        # An AMC entry on the report day: the 40% move is its outcome, not an input.
        assert trr_tier_at_entry(_trade(), history) == "LOW"
        # A position opened after that report did know about it.
        assert trr_tier_at_entry(_trade(acquired_date=date(2026, 5, 5)), history) == "HIGH"
        assert trr_tier_at_entry(_trade(symbol="NEW"), history) == "UNKNOWN"


class TestTally:
    def test_counts_wins_pnl_and_mean_loss(self):
        t = tally([_trade(gain_loss=300.0), _trade(gain_loss=-100.0),
                   _trade(gain_loss=-300.0), _trade(gain_loss=0.0)])
        assert (t.n, t.wins, t.pnl) == (4, 1, -100.0)
        assert t.avg_loss == pytest.approx(-400.0 / 3)

    def test_no_losers_means_no_average_loss(self):
        assert tally([_trade(gain_loss=5.0)]).avg_loss is None


class TestMoney:
    def test_signs_use_the_minus_character_claude_md_uses(self):
        assert money(1234.4) == "+$1,234"
        assert money(-1234.5) == "−$1,235"
        assert money(0.4) == "+$0"


class TestCompute:
    def test_every_trade_lands_in_exactly_one_book_partition(self):
        trades = [
            _trade(id=1, symbol="SPX", strategy_type="SINGLE", earnings_date=None),
            _trade(id=2, symbol="SPY", strategy_type="STOCK", earnings_date=None),
            _trade(id=3),
            _trade(id=4, earnings_date=None),
            _trade(id=5, strategy_type="IRON_CONDOR"),
            _trade(id=6, strategy_type="STRANGLE"),
            _trade(id=7, strategy_type="STOCK", earnings_date=None),
        ]
        ev = compute(trades, {}, {})
        parts = ev["book"]
        assert sum(t.n for label, t in parts.items() if label != "Total") == 7
        assert parts["Total"].n == 7

    def test_ex_crwv_table_drops_only_crwv(self):
        trades = [_trade(id=1, symbol="CRWV"), _trade(id=2, symbol="MU")]
        ev = compute(trades, {}, {})
        assert sum(t.n for t in ev["trr"].values()) == 2
        assert sum(t.n for t in ev["trr_ex_crwv"].values()) == 1

    def test_adjustments_are_positions_with_a_parent(self):
        trades = [_trade(id=1, symbol="MU", gain_loss=-10.0),
                  _trade(id=2, symbol="MU", parent_strategy_id=1, gain_loss=-20.0),
                  _trade(id=3, symbol="MU", parent_strategy_id=1, gain_loss=5.0),
                  _trade(id=4, symbol="NVDA", gain_loss=-99.0)]
        ev = compute(trades, {}, {})
        assert ev["adjustments"]["All"].n == 2
        # The original loss, then what it became once adjusted.
        assert ev["adjustment_chains"] == {1: ("MU", -10.0, -25.0)}


class TestPositions:
    """A position is a row of the `positions` view (migration 5): the same trade placed
    in IRA and TAXABLE is one, not two. The merge rule lives in the view and is tested in
    scripts/tests/test_positions_view.py; these hold rule_evidence.py to reading it."""

    def test_an_adjustment_finds_a_parent_that_is_one_half_of_a_merged_trade(self):
        pair = _trade(id=297, strategy_ids=(297, 322), symbol="MU", gain_loss=-10.0)
        roll = _trade(id=400, symbol="MU", parent_strategy_id=322, gain_loss=-20.0)
        assert compute([pair, roll], {}, {})["adjustment_chains"] == {297: ("MU", -10.0, -30.0)}


@pytest.mark.pg
def test_load_book_reads_the_positions_view_and_counts_a_mirrored_trade_once(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "positions"))

        def position(account, quantity, pnl, strikes, sale="2026-05-05"):
            sid = conn.execute(
                "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date, expiration,"
                " quantity, gain_loss, is_winner, earnings_date, account_type)"
                " VALUES ('PLTR', 'SPREAD', '2026-05-04', %s, '2026-05-08', %s, %s, %s,"
                " '2026-05-04', %s) RETURNING id",
                (sale, quantity, pnl, pnl > 0, account)).fetchone()[0]
            for strike in strikes:
                conn.execute(
                    "INSERT INTO trade_journal (symbol, acquired_date, sale_date, option_type, strike,"
                    " expiration, quantity, cost_basis, proceeds, gain_loss, is_winner, strategy_id,"
                    " account_type) VALUES ('PLTR', '2026-05-04', %s, 'PUT', %s,"
                    " '2026-05-08', %s, %s, 0, 0, false, %s, %s)",
                    (sale, strike, quantity, sid, sid, account))    # cost basis: one lot per leg
            return sid

        taxable = position("TAXABLE", 200, 2381.47, (115, 120))
        ira = position("IRA", 200, 2381.48, (115, 120))
        other = position("IRA", 50, -500.00, (110, 115))       # same day, other strikes
        later = position("IRA", 10, 75.00, (110, 115), sale="2026-05-12")
        trades = load_book(conn)
        through = load_book(conn, through=date(2026, 5, 5))

    assert [(t.id, t.strategy_ids, t.account_type, t.quantity) for t in trades] == [
        (taxable, (taxable, ira), "IRA+TAXABLE", 400.0), (other, (other,), "IRA", 50.0),
        (later, (later,), "IRA", 10.0)]
    assert trades[0].gain_loss == pytest.approx(4762.95)
    assert [t.id for t in through] == [taxable, other]
    # One position of 400, not two of 200.
    size = compute(trades, {}, {})["size"]
    assert (size["200+"].n, size["101–200"].n) == (1, 0)


EXPIRY = date(2026, 5, 8)


def _leg(option_type, strike, quantity, *, short, cost=0.0, proceeds=0.0, sid=1, wash=0.0,
         expiration=EXPIRY):
    return Leg(strategy_id=sid, option_type=option_type, strike=strike, expiration=expiration,
               quantity=quantity, cost_basis=cost, proceeds=proceeds, wash_sale=wash,
               opened_short=short)


def _credit_put_spread(quantity, width, credit, sid=1, top=100.0):
    """Short the higher put for `credit` + 1000, long the lower for 1000."""
    return [_leg("PUT", top, quantity, short=True, proceeds=credit + 1000.0, cost=50.0, sid=sid),
            _leg("PUT", top - width, quantity, short=False, cost=1000.0, proceeds=10.0, sid=sid)]


class TestMaxLoss:
    """Short legs open on their proceeds (Fidelity dates them sale-before-acquired),
    long legs on their cost basis; the loss is the worst payoff at expiry."""

    def test_a_credit_put_spread_risks_its_width_less_the_credit(self):
        legs = [_leg("PUT", 95.0, 10, short=True, proceeds=3000.0, cost=400.0),
                _leg("PUT", 90.0, 10, short=False, cost=1000.0, proceeds=20.0)]
        assert max_loss(legs) == pytest.approx(5 * 10 * 100 - 2000.0)

    def test_a_credit_call_spread_risks_its_width_less_the_credit(self):
        legs = [_leg("CALL", 100.0, 5, short=True, proceeds=1500.0),
                _leg("CALL", 105.0, 5, short=False, cost=500.0)]
        assert max_loss(legs) == pytest.approx(5 * 5 * 100 - 1000.0)

    def test_a_debit_spread_risks_its_debit(self):
        legs = [_leg("PUT", 95.0, 10, short=False, cost=3000.0),
                _leg("PUT", 90.0, 10, short=True, proceeds=1000.0)]
        assert max_loss(legs) == pytest.approx(2000.0)

    def test_lots_and_wash_sale_rows_sum_within_a_leg(self):
        # The long leg in two lots, one a wash-sale row whose basis is negative:
        # lot + wash rows sum to the cash paid, 600 for the 10 puts.
        legs = [_leg("PUT", 95.0, 4, short=True, proceeds=1200.0),
                _leg("PUT", 95.0, 6, short=True, proceeds=1800.0),
                _leg("PUT", 90.0, 6, short=False, cost=900.0),
                _leg("PUT", 90.0, 4, short=False, cost=-300.0, wash=700.0)]
        assert max_loss(legs) == pytest.approx(5000.0 - (3000.0 - 600.0))

    def test_unequal_legs_risk_the_worst_payoff_of_the_legs_held(self):
        # UPWK #269: 33 short 15 puts against 100 long 12.5s (67 shorts were assigned
        # into stock). Worst at 12.5: 33 x $2.50 x 100 against a $219.83 credit.
        legs = [_leg("PUT", 15.0, 33, short=True, proceeds=1022.21, cost=4455.78),
                _leg("PUT", 12.5, 100, short=False, cost=802.38)]
        assert max_loss(legs) == pytest.approx(8250.0 - 219.83)

    def test_extra_long_puts_below_the_spread_add_their_cost_and_no_risk(self):
        # ORCL #212: 600 x 220/215 plus 50 long 205 puts.
        legs = [_leg("PUT", 220.0, 600, short=True, proceeds=200_000.0),
                _leg("PUT", 215.0, 600, short=False, cost=130_000.0),
                _leg("PUT", 205.0, 50, short=False, cost=5_000.0)]
        assert max_loss(legs) == pytest.approx(300_000.0 - 65_000.0)

    @pytest.mark.parametrize("legs, reason", [
        ([], "no journal legs"),
        ([_leg("CALL", 100.0, 10, short=True, proceeds=900.0),
          _leg("CALL", 105.0, 5, short=False, cost=100.0)], "unbounded"),
        ([_leg("PUT", 95.0, 10, short=True, proceeds=900.0),
          _leg("PUT", 90.0, 10, short=None, cost=100.0)], "same day"),
        ([_leg("PUT", 95.0, 10, short=True, proceeds=900.0),
          _leg("PUT", 90.0, 10, short=False, cost=100.0, expiration=date(2026, 5, 15))],
         "expir"),
        ([_leg(None, None, 100, short=False, cost=5000.0)], "stock"),
        ([_leg("PUT", 95.0, 10, short=True, proceeds=9000.0),
          _leg("PUT", 90.0, 10, short=False, cost=100.0)], "cannot lose"),
    ])
    def test_legs_that_do_not_bound_a_loss_are_not_rebuilt(self, legs, reason):
        with pytest.raises(Unbuildable, match=reason):
            max_loss(legs)


class TestRebuild:
    def test_a_clean_vertical_and_an_irregular_one(self):
        clean = rebuild(_trade(id=1, gain_loss=600.0), {1: _credit_put_spread(10, 5, 2000.0)})
        assert (clean.max_loss, clean.clean, clean.r) == (pytest.approx(3000.0), True,
                                                         pytest.approx(0.2))
        uneven = rebuild(_trade(id=2), {2: [
            _leg("PUT", 15.0, 33, short=True, proceeds=1000.0, sid=2),
            _leg("PUT", 12.5, 100, short=False, cost=800.0, sid=2)]})
        assert not uneven.clean

    def test_a_trade_in_both_accounts_risks_both_accounts_max_loss(self):
        legs = {3: _credit_put_spread(300, 5, 20_000.0, sid=3),
                4: _credit_put_spread(200, 5, 12_000.0, sid=4)}
        merged = rebuild(_trade(id=3, strategy_ids=(3, 4), quantity=500.0, gain_loss=7000.0), legs)
        assert merged.max_loss == pytest.approx(130_000.0 + 88_000.0)
        assert merged.union_max_loss == pytest.approx(merged.max_loss)
        assert merged.r == pytest.approx(7000.0 / 218_000.0)

    def test_wash_sale_rows_are_marked(self):
        legs = _credit_put_spread(10, 5, 2000.0)
        assert not rebuild(_trade(), {1: legs}).wash_sale
        assert rebuild(_trade(), {1: [*legs, _leg("PUT", 95.0, 1, short=False, wash=5.0)]}).wash_sale


class TestLogOptimalFraction:
    def test_the_fraction_maximises_mean_log_growth_within_zero_and_one(self):
        r = np.array([1.0, -0.5])
        # Row 0: 0.5 log(1+f) + 0.5 log(1-f/2) peaks at f = 0.5. Row 1 doubles the
        # winner and the optimum passes 1, so the bound holds. Row 2 only loses.
        counts = np.array([[1.0, 1.0], [2.0, 1.0], [0.0, 1.0]])
        assert log_optimal_fractions(r, counts) == pytest.approx([0.5, 1.0, 0.0])

    def test_no_edge_is_a_zero_fraction(self):
        assert log_optimal_fractions(np.array([0.1, -0.2]), np.ones((1, 2)))[0] == 0.0


class TestResampleCounts:
    def test_a_resample_draws_whole_groups_and_the_seed_fixes_it(self):
        counts = resample_counts([[0, 1], [2]], n=3, resamples=200, seed=1)
        assert counts.shape == (200, 3)
        assert (counts[:, 0] == counts[:, 1]).all()          # one week, drawn together
        assert (counts[:, 0] + counts[:, 2] == 2).all()      # two weeks drawn per resample
        assert (counts == resample_counts([[0, 1], [2]], n=3, resamples=200, seed=1)).all()
        assert (counts != resample_counts([[0, 1], [2]], n=3, resamples=200, seed=2)).any()


class TestReturnOnMaxLossEvidence:
    """Earnings spreads only, per position, every irregular or unbuildable one named."""

    def book(self):
        uneven = [_leg("PUT", 15.0, 33, short=True, proceeds=1000.0, sid=4),
                  _leg("PUT", 12.5, 100, short=False, cost=800.0, sid=4)]
        trades = [
            _trade(id=1, gain_loss=600.0),                                     # max loss 3,000
            _trade(id=2, strategy_ids=(2, 3), gain_loss=10_000.0,
                   acquired_date=date(2026, 5, 11), sale_date=date(2026, 5, 12)),  # 130k x 2
            _trade(id=4, gain_loss=-4025.0),                                   # 8,050, irregular
            _trade(id=5, gain_loss=2425.0),                                    # no legs
            _trade(id=6, gain_loss=1000.0),                                    # exactly 100k
            _trade(id=7, gain_loss=2500.0),                                    # 125k
            _trade(id=8, strategy_type="SINGLE", gain_loss=99.0),              # not a spread
        ]
        legs = {1: _credit_put_spread(10, 5, 2000.0, sid=1),
                2: _credit_put_spread(300, 5, 20_000.0, sid=2),
                3: _credit_put_spread(300, 5, 20_000.0, sid=3),
                4: uneven,
                6: _credit_put_spread(200, 5, 0.0, sid=6),
                7: _credit_put_spread(250, 5, 0.0, sid=7)}
        return trades, legs

    def test_the_evidence_is_computed_per_position(self):
        trades, legs = self.book()
        risk = compute(trades, {}, legs)["risk"]
        assert [p.trade.id for p in risk.positions] == [1, 2, 4, 6, 7]
        assert [p.trade.id for p in risk.irregular] == [4]
        assert [(t.id, reason) for t, reason in risk.unbuildable] == [(5, "no journal legs")]
        assert risk.mean_r == pytest.approx((0.2 + 10_000 / 260_000 - 0.5 + 0.01 + 0.02) / 5)
        assert risk.f_star == 0.0
        assert risk.worst.trade.id == 4
        assert (risk.large.n, risk.large.wins, risk.large.pnl) == (2, 2, 12_500.0)
        assert risk.spread_pnl == pytest.approx(12_500.0)
        assert risk.weeks == 2
        lo, hi = risk.mean_ci
        assert lo <= risk.mean_r <= hi

    def test_the_rendered_evidence_is_what_the_gate_checks(self):
        trades, legs = self.book()
        first, second, *_ = render_tables(compute(trades, {}, legs))["risk"]
        assert re.fullmatch(
            r"across 5 earnings spreads, mean return on max loss −4\.6% \[[−+]\d+\.\d%, [−+]\d+\.\d%\]"
            r" and f\* 0 \[0, (0|1|0\.\d\d)\] \(week-clustered \[0, (0|1|0\.\d\d)\]\)", first)
        assert second == ("100% of earnings-spread P&L, +$12,500, came from 2 positions that each"
                          " risked over $100k, all winners")

    def test_a_book_with_nothing_rebuilt_renders_a_figure_no_doc_carries(self):
        (line, *_) = render_tables(compute([_trade(id=5)], {}, {}))["risk"]
        assert line.startswith("across 0 earnings spreads")


@pytest.mark.pg
def test_load_legs_reads_each_lot_with_the_side_its_dates_give(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal"))
        sid = conn.execute(
            "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date, expiration,"
            " quantity, gain_loss, is_winner) VALUES ('AAA', 'SPREAD', '2026-05-04', '2026-05-05',"
            " '2026-05-08', 10, 100, true) RETURNING id").fetchone()[0]
        for acquired, sale, strike, cost, proceeds, wash in (
                ("2026-05-05", "2026-05-04", 95, 400, 3000, 0),     # sold to open: dates inverted
                ("2026-05-04", "2026-05-05", 90, 1000, 20, 12.5),   # bought to open
                ("2026-05-04", "2026-05-04", 85, 1, 1, 0)):         # same day: side unknown
            conn.execute(
                "INSERT INTO trade_journal (symbol, acquired_date, sale_date, option_type, strike,"
                " expiration, quantity, cost_basis, proceeds, gain_loss, is_winner, strategy_id,"
                " wash_sale_amount) VALUES ('AAA', %s, %s, 'PUT', %s, '2026-05-08', 10, %s, %s,"
                " 0, false, %s, %s)", (acquired, sale, strike, cost, proceeds, sid, wash))
        legs = load_legs(conn)

    assert [(leg.strike, leg.opened_short, leg.cost_basis, leg.proceeds, leg.wash_sale)
            for leg in legs[sid]] == [(95.0, True, 400.0, 3000.0, 0.0),
                                      (90.0, False, 1000.0, 20.0, 12.5),
                                      (85.0, None, 1.0, 1.0, 0.0)]
    assert legs[sid][0].expiration == date(2026, 5, 8) and legs[sid][0].quantity == 10.0


class TestFragments:
    def test_missing_fragments_ignore_line_wrapping(self):
        text = "Never adjust a loser. 0 for 2,\n  −$99,999 (XYZ"
        assert missing_fragments(["0 for 2, −$99,999 (XYZ"], text) == []
        assert missing_fragments(["0 for 3"], text) == ["0 for 3"]


