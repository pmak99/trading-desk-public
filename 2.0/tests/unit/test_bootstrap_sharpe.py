"""Unit tests for bootstrap_sharpe.py's netting/bucketing/CI/Monte-Carlo pipeline.

Netting and bucketing are covered with synthetic books; the reproduction gate
against the operator's own database is not part of this repository.
"""

import random
from datetime import date

import pytest

from common.db import connect
from scripts.bootstrap_sharpe import (
    NettedUnit,
    bootstrap_sharpe_ci,
    bucket_unit,
    calendar_months,
    filter_monthly_series,
    filter_units,
    load_positions,
    load_voo_monthly,
    monte_carlo_drawdown,
    monthly_series,
    net_campaigns,
    sharpe_ratio,
    sortino_ratio,
    stationary_bootstrap_resample,
    trailing_window_series,
    voo_series_for_months,
    zero_filled,
)


def _row(**kwargs):
    """A strategies row with sane defaults, overridable per test."""
    defaults = {
        "id": None, "symbol": "TEST", "strategy_type": "SINGLE", "acquired_date": "2025-01-01",
        "sale_date": "2025-01-02", "days_held": 1, "expiration": None, "quantity": 1,
        "net_credit": None, "net_debit": None, "gain_loss": 0.0, "is_winner": True,
        "earnings_date": None, "actual_move": None, "trade_type": "NEW",
        "parent_strategy_id": None, "campaign_id": None, "trr_at_entry": None,
        "position_limit_at_entry": None, "account_type": "TAXABLE",
    }
    defaults.update(kwargs)
    return defaults


class TestNetCampaigns:
    def test_stock_and_overlapping_option_leg_net_together(self):
        # A stock and its overlapping covered calls are one campaign.
        rows = [
            _row(id=1, symbol="CRWV", strategy_type="STOCK", account_type="IRA",
                 acquired_date="2025-01-01", sale_date="2025-08-01", gain_loss=400000),
            _row(id=2, symbol="CRWV", strategy_type="COVERED_CALL", account_type="IRA",
                 acquired_date="2025-03-01", sale_date="2025-06-01", gain_loss=-300000),
        ]
        units = net_campaigns(rows)
        assert len(units) == 1
        assert units[0].net_pnl == pytest.approx(100000)

    def test_non_overlapping_option_leg_stays_separate(self):
        rows = [
            _row(id=1, symbol="CRWV", strategy_type="STOCK", account_type="IRA",
                 acquired_date="2025-01-01", sale_date="2025-03-01", gain_loss=400000),
            _row(id=2, symbol="CRWV", strategy_type="COVERED_CALL", account_type="IRA",
                 acquired_date="2025-06-01", sale_date="2025-07-01", gain_loss=-1000),
        ]
        units = net_campaigns(rows)
        assert len(units) == 2
        pnls = sorted(u.net_pnl for u in units)
        assert pnls == pytest.approx([-1000, 400000])

    def test_never_merges_across_accounts(self):
        rows = [
            _row(id=1, symbol="CRWV", strategy_type="STOCK", account_type="IRA",
                 acquired_date="2025-01-01", sale_date="2025-08-01", gain_loss=400000),
            _row(id=2, symbol="CRWV", strategy_type="COVERED_CALL", account_type="TAXABLE",
                 acquired_date="2025-03-01", sale_date="2025-06-01", gain_loss=-20002),
        ]
        units = net_campaigns(rows)
        assert len(units) == 2

    def test_a_stock_position_in_both_accounts_nets_each_accounts_overlapping_options(self):
        # Assigned shares can be one trade across both accounts
        # (a positions row, account IRA+TAXABLE); each account's puts belong to it.
        rows = [
            _row(id=1, symbol="ZS", strategy_type="STOCK", account_type="IRA+TAXABLE",
                 acquired_date="2026-05-27", sale_date="2026-05-29", gain_loss=-215000),
            _row(id=2, symbol="ZS", strategy_type="SINGLE", account_type="IRA",
                 acquired_date="2026-05-20", sale_date="2026-05-27", gain_loss=-9000),
            _row(id=3, symbol="ZS", strategy_type="SINGLE", account_type="TAXABLE",
                 acquired_date="2026-05-20", sale_date="2026-05-27", gain_loss=-6000),
        ]
        units = net_campaigns(rows)
        assert len(units) == 1
        assert units[0].account_type == "IRA+TAXABLE"
        assert units[0].net_pnl == pytest.approx(-230000)

    def test_an_option_position_in_both_accounts_nets_with_a_stock_campaign_in_one(self):
        rows = [
            _row(id=1, symbol="CRWV", strategy_type="STOCK", account_type="IRA",
                 acquired_date="2025-01-01", sale_date="2025-08-01", gain_loss=1000),
            _row(id=2, symbol="CRWV", strategy_type="SINGLE", account_type="IRA+TAXABLE",
                 acquired_date="2025-03-01", sale_date="2025-06-01", gain_loss=-400),
        ]
        units = net_campaigns(rows)
        assert [round(u.net_pnl) for u in units] == [600]

    def test_standalone_earnings_option_trade_is_its_own_unit(self):
        rows = [_row(id=1, symbol="ZM", strategy_type="SPREAD", gain_loss=1847)]
        units = net_campaigns(rows)
        assert len(units) == 1
        assert units[0].net_pnl == pytest.approx(1847)

    def test_index_stock_is_never_netted_even_with_overlapping_option(self):
        # SPY is an index ticker -- §2 scopes netting to single-name STOCK only.
        rows = [
            _row(id=1, symbol="SPY", strategy_type="STOCK", account_type="TAXABLE",
                 acquired_date="2025-01-01", sale_date="2025-08-01", gain_loss=59272),
            _row(id=2, symbol="SPY", strategy_type="SINGLE", account_type="TAXABLE",
                 acquired_date="2025-03-01", sale_date="2025-06-01", gain_loss=167889),
        ]
        units = net_campaigns(rows)
        assert len(units) == 2
        pnls = sorted(u.net_pnl for u in units)
        assert pnls == pytest.approx([59272, 167889])

    def test_option_leg_claimed_by_at_most_one_campaign(self):
        # Two sequential single-name STOCK campaigns on the same symbol/account;
        # an option leg overlapping only the second one must not be double-counted
        # into the first.
        rows = [
            _row(id=1, symbol="ZS", strategy_type="STOCK", account_type="TAXABLE",
                 acquired_date="2025-01-01", sale_date="2025-02-01", gain_loss=-1000),
            _row(id=2, symbol="ZS", strategy_type="STOCK", account_type="TAXABLE",
                 acquired_date="2025-05-01", sale_date="2025-06-01", gain_loss=-42814),
            _row(id=3, symbol="ZS", strategy_type="COVERED_CALL", account_type="TAXABLE",
                 acquired_date="2025-05-10", sale_date="2025-05-20", gain_loss=-1000),
        ]
        units = net_campaigns(rows)
        assert len(units) == 2
        pnls = sorted(u.net_pnl for u in units)
        assert pnls == pytest.approx([-43814, -1000])


class TestBucketUnit:
    def test_index_stock_is_index_equity(self):
        unit = NettedUnit(symbol="SPY", account_type="TAXABLE", sale_date="2025-01-01",
                           net_pnl=100, strategy_types=frozenset({"STOCK"}), is_equity_campaign=False)
        assert bucket_unit(unit) == "index_equity"

    def test_index_option_is_index_options(self):
        unit = NettedUnit(symbol="SPY", account_type="TAXABLE", sale_date="2025-01-01",
                           net_pnl=100, strategy_types=frozenset({"SINGLE"}), is_equity_campaign=False)
        assert bucket_unit(unit) == "index_options"

    def test_netted_single_name_campaign_is_equity_campaign(self):
        unit = NettedUnit(symbol="CRWV", account_type="IRA", sale_date="2025-01-01",
                           net_pnl=100, strategy_types=frozenset({"STOCK", "COVERED_CALL"}),
                           is_equity_campaign=True)
        assert bucket_unit(unit) == "equity_campaign"

    def test_iron_condor_is_condor_strangle(self):
        unit = NettedUnit(symbol="AVGO", account_type="TAXABLE", sale_date="2025-01-01",
                           net_pnl=-100, strategy_types=frozenset({"IRON_CONDOR"}),
                           is_equity_campaign=False)
        assert bucket_unit(unit) == "condor_strangle"

    def test_strangle_is_condor_strangle(self):
        unit = NettedUnit(symbol="MU", account_type="TAXABLE", sale_date="2025-01-01",
                           net_pnl=-100, strategy_types=frozenset({"STRANGLE"}),
                           is_equity_campaign=False)
        assert bucket_unit(unit) == "condor_strangle"

    def test_single_name_single_leg_is_earnings_options(self):
        unit = NettedUnit(symbol="ZM", account_type="TAXABLE", sale_date="2025-01-01",
                           net_pnl=100, strategy_types=frozenset({"SPREAD"}), is_equity_campaign=False)
        assert bucket_unit(unit) == "earnings_options"


class TestMonthlySeries:
    def test_months_come_back_in_calendar_order_whatever_the_unit_order(self):
        # The stationary bootstrap resamples blocks of *adjacent* months. Until
        # 2026-09-13 the series came back in the order units happened to appear
        # (2024-06, 2025-02, 2026-05, ...), so every bootstrap CI in
        # SHARPE_BOOTSTRAP_2026-09.md was computed on shuffled months.
        units = [
            NettedUnit("A", "IRA", "2025-03-10", 1.0, frozenset({"SINGLE"}), False),
            NettedUnit("B", "IRA", "2024-07-01", 2.0, frozenset({"SINGLE"}), False),
            NettedUnit("C", "IRA", "2025-01-15", 3.0, frozenset({"SINGLE"}), False),
        ]
        assert list(monthly_series(units)) == ["2024-07", "2025-01", "2025-03"]

    def test_aggregates_by_calendar_month(self):
        units = [
            NettedUnit(symbol="A", account_type="TAXABLE", sale_date="2025-01-05",
                       net_pnl=100, strategy_types=frozenset({"SPREAD"}), is_equity_campaign=False),
            NettedUnit(symbol="B", account_type="TAXABLE", sale_date="2025-02-10",
                       net_pnl=-50, strategy_types=frozenset({"SPREAD"}), is_equity_campaign=False),
        ]
        assert monthly_series(units) == {"2025-01": 100, "2025-02": -50}

    def test_sums_multiple_trades_in_same_month(self):
        units = [
            NettedUnit(symbol="A", account_type="TAXABLE", sale_date="2025-01-05",
                       net_pnl=100, strategy_types=frozenset({"SPREAD"}), is_equity_campaign=False),
            NettedUnit(symbol="B", account_type="TAXABLE", sale_date="2025-01-28",
                       net_pnl=25, strategy_types=frozenset({"SPREAD"}), is_equity_campaign=False),
        ]
        assert monthly_series(units) == {"2025-01": 125}


class TestSharpeRatio:
    def test_matches_hand_computation(self):
        # mean=10, sample stdev=10 (n=3: [0,10,20]) -> Sharpe = 10/10 * sqrt(12) = sqrt(12)
        import math
        assert sharpe_ratio([0.0, 10.0, 20.0]) == pytest.approx(math.sqrt(12))

    def test_returns_none_for_zero_variance_series(self):
        assert sharpe_ratio([5.0, 5.0, 5.0]) is None

    def test_returns_none_for_fewer_than_two_values(self):
        assert sharpe_ratio([5.0]) is None
        assert sharpe_ratio([]) is None


class TestSortinoRatio:
    def test_matches_hand_computation(self):
        # mean=5, downside deviation (target=0, N divisor) = sqrt(100/2) -> sortino = sqrt(6)
        import math
        assert sortino_ratio([20.0, -10.0]) == pytest.approx(math.sqrt(6))

    def test_returns_none_for_fewer_than_two_values(self):
        assert sortino_ratio([5.0]) is None
        assert sortino_ratio([]) is None

    def test_returns_none_when_series_has_no_downside(self):
        # Every value >= mar (default 0): downside deviation is 0, nothing to divide by.
        assert sortino_ratio([5.0, 10.0, 15.0]) is None

    def test_custom_mar_changes_what_counts_as_downside(self):
        series = [3.0, 5.0]
        assert sortino_ratio(series, mar=0.0) is None  # both >= 0: no downside
        assert sortino_ratio(series, mar=4.0) is not None  # 3.0 < 4.0: now downside


class TestTrailingWindowSeries:
    def test_selects_trailing_n_months_ending_at_as_of(self):
        monthly = {"2026-06": 1.0, "2026-07": 2.0, "2026-08": 3.0, "2026-09": 4.0}
        result = trailing_window_series(monthly, months=3, as_of=date(2026, 9, 15))
        assert sorted(result) == [2.0, 3.0, 4.0]

    def test_months_1_keeps_only_the_as_of_month(self):
        monthly = {"2026-08": 3.0, "2026-09": 4.0}
        result = trailing_window_series(monthly, months=1, as_of=date(2026, 9, 15))
        assert result == [4.0]

    def test_missing_months_in_range_are_skipped_not_zero_filled(self):
        # 2026-08 has no trades at all -- must be absent, not a fabricated 0.0.
        monthly = {"2026-07": 2.0, "2026-09": 4.0}
        result = trailing_window_series(monthly, months=3, as_of=date(2026, 9, 15))
        assert sorted(result) == [2.0, 4.0]

    def test_window_crosses_a_year_boundary(self):
        monthly = {"2025-11": 1.0, "2025-12": 2.0, "2026-01": 3.0}
        result = trailing_window_series(monthly, months=3, as_of=date(2026, 1, 20))
        assert sorted(result) == [1.0, 2.0, 3.0]


class TestStationaryBootstrapResample:
    def test_output_length_matches_input(self):
        rng = random.Random(1)
        series = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = stationary_bootstrap_resample(series, rng, mean_block_len=2.0)
        assert len(out) == len(series)

    def test_output_values_always_drawn_from_input(self):
        series = [1.0, 2.0, 3.0, 4.0, 5.0]
        for seed in range(20):
            rng = random.Random(seed)
            out = stationary_bootstrap_resample(series, rng, mean_block_len=2.0)
            assert set(out) <= set(series)


class TestBootstrapSharpeCI:
    def test_deterministic_for_fixed_seed(self):
        series = [10000, -5000, 20000, 15000, -8000, 12000, 9000, -2000,
                  11000, 7000, -3000, 14000, 6000, -1000, 13000, 8000,
                  10000, -6000, 15000, 9000, -4000, 12000, 7000, 11000]
        result1 = bootstrap_sharpe_ci(series, n_boot=500, seed=7)
        result2 = bootstrap_sharpe_ci(series, n_boot=500, seed=7)
        assert result1 == result2

    def test_point_estimate_matches_sharpe_ratio(self):
        series = [10000, -5000, 20000, 15000, -8000, 12000, 9000, -2000,
                  11000, 7000, -3000, 14000, 6000, -1000, 13000, 8000,
                  10000, -6000, 15000, 9000, -4000, 12000, 7000, 11000]
        point, lo, hi = bootstrap_sharpe_ci(series, n_boot=500, seed=7)
        assert point == sharpe_ratio(series)
        assert lo <= hi


class TestMonteCarloDrawdown:
    def test_all_positive_series_has_zero_drawdown(self):
        result = monte_carlo_drawdown([100.0, 200.0, 50.0], n_sims=200, seed=1)
        assert result["median_drawdown"] == 0
        assert result["p95_drawdown"] == 0

    def test_degenerate_two_identical_losses_gives_exact_drawdown(self):
        # Every resample of [-100,-100] is [-100,-100]: cum -100 (dd 100), then
        # cum -200 (dd 200) -- max drawdown is deterministically 200.
        result = monte_carlo_drawdown([-100.0, -100.0], n_sims=50, seed=1,
                                       drawdown_thresholds=(100.0, 150.0))
        assert result["median_drawdown"] == pytest.approx(200.0)
        assert result["p_drawdown_gt"] == {100.0: 1.0, 150.0: 1.0}

    def test_rolling_window_all_negative_series_always_flags(self):
        result = monte_carlo_drawdown([-10.0, -20.0, -30.0], n_sims=50, seed=1, rolling_window=1)
        assert result["p_rolling_window_negative"] == 1.0

    def test_default_rolling_window_is_half_the_series_length(self):
        # The real sample spans ~2 years -> a rolling-window "year" is
        # approximated as half the trade count, since resampling reorders
        # trades and can't preserve real calendar dates.
        result = monte_carlo_drawdown([1.0] * 24, n_sims=10, seed=1)
        assert result["rolling_window_trades"] == 12

    def test_deterministic_for_fixed_seed(self):
        series = [100.0, -50.0, 200.0, -300.0, 150.0]
        r1 = monte_carlo_drawdown(series, n_sims=300, seed=7)
        r2 = monte_carlo_drawdown(series, n_sims=300, seed=7)
        assert r1 == r2


class TestFilterUnits:
    def _units(self):
        return [
            NettedUnit(symbol="A", account_type="TAXABLE", sale_date="2025-01-01", net_pnl=1,
                       strategy_types=frozenset({"IRON_CONDOR"}), is_equity_campaign=False),
            NettedUnit(symbol="B", account_type="TAXABLE", sale_date="2025-01-01", net_pnl=2,
                       strategy_types=frozenset({"STOCK", "COVERED_CALL"}), is_equity_campaign=True),
            NettedUnit(symbol="C", account_type="TAXABLE", sale_date="2025-01-01", net_pnl=3,
                       strategy_types=frozenset({"SPREAD"}), is_equity_campaign=False),
        ]

    def test_no_filter_returns_all(self):
        assert len(filter_units(self._units())) == 3

    def test_exclude_removes_matching_buckets(self):
        result = filter_units(self._units(), exclude={"condor_strangle"})
        assert {u.symbol for u in result} == {"B", "C"}

    def test_include_only_keeps_matching_buckets(self):
        result = filter_units(self._units(), include_only={"equity_campaign"})
        assert {u.symbol for u in result} == {"B"}


class TestLoadVooMonthly:
    def test_returns_none_when_file_missing(self, tmp_path):
        assert load_voo_monthly(tmp_path / "nope.json") is None

    def test_parses_month_to_fractional_return(self, tmp_path):
        p = tmp_path / "voo.json"
        p.write_text('{"2024-06": 1.60, "2024-07": -2.00}')
        result = load_voo_monthly(p)
        assert result == pytest.approx({"2024-06": 0.016, "2024-07": -0.02})


class TestFilterMonthlySeries:
    def test_no_start_returns_all(self):
        series = {"2024-01": 1.0, "2024-02": 2.0}
        assert filter_monthly_series(series) == series

    def test_drops_months_before_start(self):
        series = {"2024-01": 1.0, "2024-06": 2.0, "2024-07": 3.0}
        assert filter_monthly_series(series, start="2024-06") == {"2024-06": 2.0, "2024-07": 3.0}


class TestVooSeriesForMonths:
    def test_selects_only_matching_months_in_sorted_order(self):
        voo = {"2024-03": 0.03, "2024-01": 0.01, "2024-02": -0.02}
        result = voo_series_for_months(voo, {"2024-01", "2024-03"})
        assert result == [0.01, 0.03]

    def test_ignores_requested_months_not_present_in_voo(self):
        voo = {"2024-01": 0.01}
        result = voo_series_for_months(voo, {"2024-01", "2024-12"})
        assert result == [0.01]


class TestCalendarMonths:
    """A month with no closed trade is a month the book earned nothing. The
    active-month series drops it, which flatters a sparse sub-book against VOO:
    on 2026-09-13 earnings options alone read 1.61 over 20 active months and
    1.35 over the 27 calendar months VOO was invested for."""

    def test_spans_every_month_across_a_year_boundary(self):
        assert calendar_months("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"]

    def test_single_month(self):
        assert calendar_months("2025-03", "2025-03") == ["2025-03"]

    def test_zero_filled_counts_missing_months_as_zero(self):
        series = {"2024-11": 5.0, "2025-02": -1.0}
        assert zero_filled(series, calendar_months("2024-11", "2025-02")) == [5.0, 0.0, 0.0, -1.0]

    def test_zero_filled_ignores_months_outside_the_window(self):
        assert zero_filled({"2024-01": 9.0, "2024-02": 1.0}, ["2024-02"]) == [1.0]


@pytest.mark.pg
def test_load_positions_breaks_date_ties_by_id(pg_schema):
    """Same-day rows came back in physical order, which an UPDATE rewrites, so the
    Monte Carlo draws (index -> trade) changed between runs with the same seed."""
    from common.pgtesting import baseline_ddl

    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "positions"))
        for sid, symbol in ((3, "C"), (1, "A"), (2, "B")):
            conn.execute(
                "INSERT INTO strategies (id, symbol, strategy_type, acquired_date, sale_date,"
                " gain_loss, is_winner) VALUES (%s, %s, 'SINGLE', '2025-01-02', '2025-01-03',"
                " 1, true)", (sid, symbol))
        conn.execute("UPDATE strategies SET gain_loss = 2 WHERE id = 1")
        conn.commit()
        assert [r["id"] for r in load_positions(conn)] == [1, 2, 3]


@pytest.mark.pg
def test_a_trade_in_both_accounts_is_one_draw(pg_schema):
    """The Monte Carlo resamples units. Read from strategies, a trade placed in the
    IRA and TAXABLE was two half-size draws, so the resampled books never held it
    at its real size."""
    from common.pgtesting import baseline_ddl

    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "positions"))
        for account, pnl in (("IRA", 3000), ("TAXABLE", 1000)):
            conn.execute(
                "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date,"
                " gain_loss, is_winner, account_type) VALUES ('PLTR', 'SPREAD', '2026-05-04',"
                " '2026-05-05', %s, true, %s)", (pnl, account))
        conn.commit()
        units = net_campaigns(load_positions(conn))
    assert [(u.account_type, round(u.net_pnl)) for u in units] == [("IRA+TAXABLE", 4000)]
