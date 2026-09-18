"""scripts/reconcile_journal.py: make the journal agree with Fidelity's exports.

Every case here is a shape found reconciling production on 2026-09-13 against
the Realized Gain/Loss exports (IRA 2025-2026, TAXABLE 2025-2026), the monthly
statements and the Closed Positions printout:

  - a trade imported twice, once from the CSV and once from the PDF statement
    parser with shifted dates (CRM, CRWD, MRVL, SNOW, OKTA, CRDO)
  - fills dropped on import (FIG, CMG, ORCL, AVGO 270P, WMT, APLD, RDDT, SPY)
  - calls imported as puts (SMCI: "COMPUTER" contains PUT)
  - a spread's other side never imported (the MU Dec 2025 put spread, which
    makes strategy 201 an iron condor)
  - lots imported twice by overlapping exports, a cent apart (CAVA, CRM, SNOW)
  - STOCK strategies storing 2-6x their legs' shares
"""

from datetime import date

import pytest

from scripts.reconcile_journal import Leg, Lot, Strategy, apply_plan, build_plan

D = date.fromisoformat
TAX, IRA = "TAXABLE", "IRA"


def lot(symbol, typ, strike, exp, acq, sale, qty, cost, proceeds, wash=0.0, account=TAX):
    return Lot(account=account, symbol=symbol, option_type=typ, strike=strike,
               expiration=D(exp) if exp else None, acquired=D(acq), sale=D(sale),
               quantity=qty, cost=cost, proceeds=proceeds, gain_loss=round(proceeds - cost, 2),
               wash=wash, term="SHORT")


def leg(id, sid, symbol, typ, strike, exp, acq, sale, qty, cost, proceeds, account=TAX, gl=None):
    return Leg(id=id, strategy_id=sid, account=account, symbol=symbol, option_type=typ, strike=strike,
               expiration=D(exp) if exp else None, acquired=D(acq), sale=D(sale), quantity=qty,
               cost=cost, proceeds=proceeds,
               gain_loss=round(proceeds - cost, 2) if gl is None else gl,
               wash=0.0, earnings_date=None, actual_move=None)


def strat(id, symbol, stype, acq, sale, qty, pnl, account=TAX, parent=None):
    return Strategy(id=id, account=account, symbol=symbol, strategy_type=stype, acquired=D(acq),
                    sale=D(sale), quantity=qty, gain_loss=pnl, parent_strategy_id=parent)


SCOPE = {TAX: (D("2025-01-01"), D("2025-12-31"))}


# --------------------------------------------------------------------------- pure planning

def test_a_journal_that_matches_is_left_alone():
    lots = [lot("CRM", "CALL", 270.0, "2025-12-05", "2025-12-04", "2025-12-03", 50, 1050.71, 5399.29),
            lot("CRM", "CALL", 275.0, "2025-12-05", "2025-12-03", "2025-12-04", 50, 3650.71, 799.29)]
    legs = [leg(525, 31, "CRM", "CALL", 270.0, "2025-12-05", "2025-12-04", "2025-12-03", 50, 1050.71, 5399.29),
            leg(532, 31, "CRM", "CALL", 275.0, "2025-12-05", "2025-12-03", "2025-12-04", 50, 3650.71, 799.29)]
    strategies = [strat(31, "CRM", "SPREAD", "2025-12-03", "2025-12-04", 50, 1497.16)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    assert plan.is_empty()


def test_a_trade_imported_twice_keeps_the_copy_whose_dates_match():
    lots = [lot("CRM", "CALL", 270.0, "2025-12-05", "2025-12-04", "2025-12-03", 50, 1050.71, 5399.29),
            lot("CRM", "CALL", 275.0, "2025-12-05", "2025-12-03", "2025-12-04", 50, 3650.71, 799.29)]
    legs = [leg(525, 31, "CRM", "CALL", 270.0, "2025-12-05", "2025-12-04", "2025-12-03", 50, 1050.71, 5399.29),
            leg(532, 31, "CRM", "CALL", 275.0, "2025-12-05", "2025-12-03", "2025-12-04", 50, 3650.71, 799.29),
            # the PDF parser's copy: settlement dates, proceeds = |gain|
            leg(543, 33, "CRM", "CALL", 270.0, "2025-12-05", "2025-12-04", "2025-12-05", 50, 5399.29, 4348.58, gl=4348.58),
            leg(542, 33, "CRM", "CALL", 275.0, "2025-12-05", "2025-12-04", "2025-12-05", 50, 3650.71, 2851.42, gl=-2851.42)]
    strategies = [strat(31, "CRM", "SPREAD", "2025-12-03", "2025-12-04", 50, 1497.16),
                  strat(33, "CRM", "SPREAD", "2025-12-04", "2025-12-05", 50, 1497.16, parent=31)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    assert sorted(plan.delete_leg_ids) == [542, 543]
    assert plan.insert == []
    assert plan.delete_strategy_ids == [33]


def test_dropped_fills_are_inserted_into_the_position():
    lots = [lot("CMG", "CALL", 44.0, "2025-10-31", "2025-10-30", "2025-10-29", 115, 347.15, 8703.34),
            lot("CMG", "CALL", 44.0, "2025-10-31", "2025-10-30", "2025-10-29", 10, 30.18, 731.82)]
    legs = [leg(1, 25, "CMG", "CALL", 44.0, "2025-10-31", "2025-10-30", "2025-10-29", 115, 347.15, 8703.34)]
    strategies = [strat(25, "CMG", "SINGLE", "2025-10-29", "2025-10-30", 115, 8356.19)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    assert plan.delete_leg_ids == []
    assert [(lt.quantity, sid) for lt, sid in plan.insert] == [(10, 25)]
    upd = plan.strategy_updates[25]
    assert (upd.quantity, round(upd.gain_loss, 2)) == (125, 9057.83)


def test_calls_imported_as_puts_are_replaced_in_the_same_position():
    lots = [lot("SMCI", "CALL", 56.0, "2025-11-07", "2025-11-06", "2025-11-05", 85, 596.59, 3967.91),
            lot("SMCI", "CALL", 61.0, "2025-11-07", "2025-11-05", "2025-11-06", 85, 1217.09, 321.41)]
    legs = [leg(1, 117, "SMCI", "PUT", 56.0, "2025-11-07", "2025-11-06", "2025-11-05", 85, 596.59, 3967.91),
            leg(2, 117, "SMCI", "PUT", 61.0, "2025-11-07", "2025-11-05", "2025-11-06", 85, 1217.09, 321.41)]
    strategies = [strat(117, "SMCI", "SPREAD", "2025-11-05", "2025-11-06", 85, 2475.64)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    assert sorted(plan.delete_leg_ids) == [1, 2]
    assert sorted((lt.option_type, lt.strike, sid) for lt, sid in plan.insert) == [("CALL", 56.0, 117), ("CALL", 61.0, 117)]
    assert plan.delete_strategy_ids == []
    assert plan.strategy_updates.get(117) is None or plan.strategy_updates[117].strategy_type == "SPREAD"


def test_the_missing_side_of_a_position_joins_it_and_it_becomes_an_iron_condor():
    lots = [lot("MU", "CALL", 265.0, "2025-12-19", "2025-12-19", "2025-12-17", 200, 51002.83, 20797.18),
            lot("MU", "CALL", 275.0, "2025-12-19", "2025-12-17", "2025-12-19", 200, 11002.82, 1397.17),
            lot("MU", "PUT", 175.0, "2025-12-19", "2025-12-19", "2025-12-17", 200, 0.00, 1597.18),
            lot("MU", "PUT", 165.0, "2025-12-19", "2025-12-17", "2025-12-19", 200, 1402.82, 0.00)]
    # stored on a Sunday by the PDF parser, with proceeds = |gain|
    legs = [leg(555, 201, "MU", "CALL", 265.0, "2025-12-19", "2025-12-21", "2025-12-22", 200, 20797.18, 30205.65, gl=-30205.65),
            leg(558, 201, "MU", "CALL", 275.0, "2025-12-19", "2025-12-21", "2025-12-22", 200, 11002.82, 9605.65, gl=-9605.65)]
    strategies = [strat(201, "MU", "SPREAD", "2025-12-21", "2025-12-22", 200, -39811.30)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    assert sorted(plan.delete_leg_ids) == [555, 558]
    assert sorted((lt.option_type, lt.strike, sid) for lt, sid in plan.insert) == [
        ("CALL", 265.0, 201), ("CALL", 275.0, 201), ("PUT", 165.0, 201), ("PUT", 175.0, 201)]
    upd = plan.strategy_updates[201]
    assert upd.strategy_type == "IRON_CONDOR"
    assert (upd.acquired, upd.sale) == (D("2025-12-17"), D("2025-12-19"))
    assert round(upd.gain_loss, 2) == -39616.94


def test_a_lot_imported_twice_a_cent_apart_is_removed():
    lots = [lot("CAVA", "CALL", 80.0, "2025-02-27", "2025-02-26", "2025-02-24", 50, 11001.19, 3048.81)]
    legs = [leg(1160, 272, "CAVA", "CALL", 80.0, "2025-02-27", "2025-02-26", "2025-02-24", 50, 11001.18, 3048.81, gl=-7952.37),
            leg(1319, 272, "CAVA", "CALL", 80.0, "2025-02-27", "2025-02-26", "2025-02-24", 50, 11001.19, 3048.81, gl=-7952.38)]
    strategies = [strat(272, "CAVA", "SINGLE", "2025-02-24", "2025-02-26", 100, -15904.75)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    assert plan.delete_leg_ids == [1160]
    assert plan.insert == []
    assert plan.strategy_updates[272].quantity == 50


def test_a_journal_only_contract_outside_the_exports_years_is_not_touched():
    legs = [leg(9, 5, "NVDA", "PUT", 100.0, "2024-12-20", "2024-12-18", "2024-12-19", 10, 1.0, 2.0)]
    strategies = [strat(5, "NVDA", "SINGLE", "2024-12-18", "2024-12-19", 10, 1.0)]
    plan = build_plan([], legs, strategies, SCOPE)
    assert plan.is_empty()


def test_a_journal_only_contract_inside_the_exports_years_is_removed():
    lots = [lot("CRM", "CALL", 270.0, "2025-12-05", "2025-12-04", "2025-12-03", 50, 1050.71, 5399.29)]
    legs = [leg(1, 31, "CRM", "CALL", 270.0, "2025-12-05", "2025-12-04", "2025-12-03", 50, 1050.71, 5399.29),
            leg(2, 40, "ZZZ", "PUT", 10.0, "2025-06-20", "2025-06-18", "2025-06-19", 5, 1.0, 2.0)]
    strategies = [strat(31, "CRM", "SINGLE", "2025-12-03", "2025-12-04", 50, 4348.58),
                  strat(40, "ZZZ", "SINGLE", "2025-06-18", "2025-06-19", 5, 1.0)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    assert plan.delete_leg_ids == [2]
    assert plan.delete_strategy_ids == [40]


def test_legs_after_the_exports_last_row_are_not_touched():
    scope = {TAX: (D("2026-01-01"), D("2026-09-11"))}
    lots = [lot("NVDA", "CALL", 235.0, "2026-08-28", "2026-08-27", "2026-08-26", 11, 308.15, 733.55)]
    legs = [leg(1, 7, "NVDA", "CALL", 235.0, "2026-08-28", "2026-08-27", "2026-08-26", 11, 308.15, 733.55),
            leg(2, 8, "ADBE", "CALL", 310.0, "2026-09-18", "2026-09-16", "2026-09-17", 40, 1.0, 2.0)]
    strategies = [strat(7, "NVDA", "SINGLE", "2026-08-26", "2026-08-27", 11, 425.40),
                  strat(8, "ADBE", "SINGLE", "2026-09-16", "2026-09-17", 40, 1.0)]
    assert build_plan(lots, legs, strategies, scope).is_empty()


def test_a_stock_strategy_storing_three_times_its_shares_is_corrected():
    lots = [lot("SHOP", None, None, None, "2026-02-01", "2026-02-13", 6400, 100.0, 200.0, account=IRA)]
    legs = [leg(1, 352, "SHOP", None, None, None, "2026-02-01", "2026-02-13", 6400, 100.0, 200.0, account=IRA)]
    strategies = [strat(352, "SHOP", "STOCK", "2026-02-01", "2026-02-13", 19200, 100.0, account=IRA)]
    plan = build_plan(lots, legs, strategies, {IRA: (D("2026-01-01"), D("2026-12-31"))})
    assert plan.delete_leg_ids == [] and plan.insert == []
    assert plan.strategy_updates[352].quantity == 6400


def test_fills_with_an_identical_cost_are_one_leg():
    # Two real 72-lot fills at the same cost collide on trade_journal_dedup.
    lots = [lot("MU", "CALL", 265.0, "2025-12-19", "2025-12-19", "2025-12-17", 72, 18361.02, 7486.98),
            lot("MU", "CALL", 265.0, "2025-12-19", "2025-12-19", "2025-12-17", 72, 18361.02, 7486.99)]
    strategies = [strat(201, "MU", "SINGLE", "2025-12-17", "2025-12-19", 0, 0.0)]
    legs = [leg(1, 201, "MU", "CALL", 265.0, "2025-12-19", "2025-12-19", "2025-12-17", 1, 1.0, 1.0)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    assert [(lt.quantity, lt.cost, lt.proceeds) for lt, _ in plan.insert] == [(144, 36722.04, 14973.97)]


def test_a_corrected_lot_stays_in_the_strategy_its_old_leg_was_in():
    # Two VOO positions with overlapping windows. The old leg lost its
    # fractional share (the importer's int() bug), so it is replaced -- and the
    # replacement must not wander into the other position.
    scope = {IRA: (D("2026-01-01"), D("2026-12-31"))}
    lots = [lot("VOO", None, None, None, "2025-02-21", "2026-02-18", 53.19, 30000.0, 40311.87, account=IRA)]
    legs = [leg(1, 357, "VOO", None, None, None, "2025-01-08", "2026-01-16", 10, 1.0, 2.0, account=IRA),
            leg(2, 358, "VOO", None, None, None, "2025-02-21", "2026-02-18", 53, 30000.0, 40311.87, account=IRA)]
    strategies = [strat(357, "VOO", "STOCK", "2025-01-08", "2026-02-18", 10, 1.0, account=IRA),
                  strat(358, "VOO", "STOCK", "2025-02-21", "2026-02-18", 53, 10311.87, account=IRA)]
    plan = build_plan(lots, legs, strategies, scope)
    assert [(lt.quantity, sid) for lt, sid in plan.insert] == [(53.19, 358)]


def test_share_lots_stored_as_a_spread_become_stock():
    scope = {TAX: (D("2025-01-01"), D("2025-12-31"))}
    lots = [lot("VOO", None, None, None, "2025-05-13", "2025-06-27", 305, 100.0, 7820.0)]
    legs = [leg(1, 167, "VOO", None, None, None, "2025-05-13", "2025-06-27", 305, 100.0, 7820.0)]
    strategies = [strat(167, "VOO", "SPREAD", "2025-05-13", "2025-06-27", 305, 7720.0)]
    plan = build_plan(lots, legs, strategies, scope)
    assert plan.strategy_updates[167].strategy_type == "STOCK"


def test_merging_fills_never_leaves_two_legs_on_one_dedup_key():
    # FIG 55C, 2025-11-06: two 1-lots at $17.02 merge to $34.04 -- the cost of a
    # real 2-lot that is already a leg. trade_journal_dedup keys on cost, so the
    # merge must continue until no two final legs share a key.
    lots = [lot("FIG", "CALL", 55.0, "2025-11-07", "2025-11-06", "2025-11-05", 2, 34.04, 151.36),
            lot("FIG", "CALL", 55.0, "2025-11-07", "2025-11-06", "2025-11-05", 1, 17.02, 80.68),
            lot("FIG", "CALL", 55.0, "2025-11-07", "2025-11-06", "2025-11-05", 1, 17.02, 70.68)]
    legs = [leg(370, 193, "FIG", "CALL", 55.0, "2025-11-07", "2025-11-06", "2025-11-05", 2, 34.04, 151.36),
            leg(371, 193, "FIG", "CALL", 55.0, "2025-11-07", "2025-11-06", "2025-11-05", 1, 17.02, 80.68)]
    strategies = [strat(193, "FIG", "SINGLE", "2025-11-05", "2025-11-06", 3, 180.98)]
    plan = build_plan(lots, legs, strategies, SCOPE)
    final_keys = [(lt.acquired, lt.sale, lt.cost) for lt, _ in plan.insert] + [
        (x.acquired, x.sale, x.cost) for x in legs if x.id not in plan.delete_leg_ids]
    assert len(final_keys) == len(set(final_keys))
    assert sum(lt.quantity for lt, _ in plan.insert) + sum(
        x.quantity for x in legs if x.id not in plan.delete_leg_ids) == 4
    assert round(plan.strategy_updates[193].gain_loss, 2) == 234.64   # 117.32 + 63.66 + 53.66


# --------------------------------------------------------------------------- against Postgres

@pytest.mark.pg
def test_execute_writes_the_plan_and_a_second_run_is_a_no_op(pg_schema):
    from common.db import connect
    from common.pgtesting import baseline_ddl
    from scripts.reconcile_journal import load_journal

    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal"))
        conn.execute("INSERT INTO strategies (id, symbol, strategy_type, acquired_date, sale_date, expiration,"
                     " quantity, gain_loss, is_winner, account_type) VALUES"
                     " (201, 'MU', 'SPREAD', '2025-12-21', '2025-12-22', '2025-12-19', 200, -39811.30, false, 'TAXABLE'),"
                     " (202, 'MU', 'SPREAD', '2025-12-22', '2025-12-23', '2025-12-19', 200, -39811.30, false, 'TAXABLE')")
        for sid, id_, strike, cost, proceeds, gl in [(201, 555, 265, 20797.18, 30205.65, -30205.65),
                                                     (201, 558, 275, 11002.82, 9605.65, -9605.65),
                                                     (202, 600, 265, 20797.19, 30205.65, -30205.65)]:   # a cent apart, as real copies are
            conn.execute("INSERT INTO trade_journal (id, symbol, acquired_date, sale_date, option_type, strike,"
                         " expiration, quantity, cost_basis, proceeds, gain_loss, is_winner, strategy_id,"
                         " account_type, earnings_date)"
                         " VALUES (%s, 'MU', '2025-12-21', '2025-12-22', 'CALL', %s, '2025-12-19', 200,"
                         " %s, %s, %s, false, %s, 'TAXABLE', '2025-12-17')",
                         (id_, strike, cost, proceeds, gl, sid))
        conn.commit()

    lots = [lot("MU", "CALL", 265.0, "2025-12-19", "2025-12-19", "2025-12-17", 200, 51002.83, 20797.18),
            lot("MU", "CALL", 275.0, "2025-12-19", "2025-12-17", "2025-12-19", 200, 11002.82, 1397.17, wash=10.0),
            lot("MU", "PUT", 175.0, "2025-12-19", "2025-12-19", "2025-12-17", 200, 0.00, 1597.18),
            lot("MU", "PUT", 165.0, "2025-12-19", "2025-12-17", "2025-12-19", 200, 1402.82, 0.00)]

    with connect() as conn:
        legs, strategies = load_journal(conn)
        plan = build_plan(lots, legs, strategies, SCOPE)
        apply_plan(conn, plan)

    with connect() as conn:
        s = conn.execute("SELECT id, strategy_type, acquired_date, sale_date, quantity, gain_loss, is_winner"
                         " FROM strategies ORDER BY id").fetchall()
        j = conn.execute("SELECT strategy_id, option_type, strike, quantity, cost_basis, proceeds, gain_loss,"
                         " wash_sale_amount, earnings_date FROM trade_journal ORDER BY option_type, strike").fetchall()
    assert [(r.id, r.strategy_type, r.acquired_date, r.sale_date, r.quantity, float(r.gain_loss), r.is_winner)
            for r in s] == [(201, "IRON_CONDOR", D("2025-12-17"), D("2025-12-19"), 200, -39616.94, False)]
    assert [(r.option_type, r.strike, float(r.gain_loss)) for r in j] == [
        ("CALL", 265.0, -30205.65), ("CALL", 275.0, -9605.65), ("PUT", 165.0, -1402.82), ("PUT", 175.0, 1597.18)]
    assert {r.strategy_id for r in j} == {201}
    assert all(r.earnings_date == D("2025-12-17") for r in j)
    assert [float(r.wash_sale_amount) for r in j if r.strike == 275.0] == [10.0]

    with connect() as conn:
        legs, strategies = load_journal(conn)
    assert build_plan(lots, legs, strategies, SCOPE).is_empty()
