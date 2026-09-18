"""
Account isolation in strategy regrouping.

Regression cover for the id=342 bug (found Sep 2026): a single strategy row
ended up owning option legs from BOTH the IRA and the TAXABLE account, netting two
genuinely separate spreads into one row and mis-attributing legs between accounts.

Root cause: consolidate_fragmented_spreads() grouped merge candidates on the
*strategy's* account_type tag rather than the account of its underlying legs, so a
single mistagged strategy let the merge reach across the account boundary. Once
merged, fix_mistagged_strategies() could no longer repair it, because that function
only recognised the all-IRA-legs case and a merged row has mixed legs.
"""


import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402
from scripts.regroup_strategies import (  # noqa: E402
    LegRecord,
    consolidate_fragmented_spreads,
    fix_mistagged_strategies,
    link_stock_duplicates,
    load_stock_orphans,
    run_regrouping,
)

pytestmark = pytest.mark.pg


@pytest.fixture
def db(pg_schema):
    """Tables built FROM 2.0/sql/baseline_schema.sql, not transcribed.

    A hand-copied fixture schema drifts from the real one and then tests the
    wrong thing while staying green -- the exact failure the generated
    baseline exists to prevent.
    """
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal"))
        conn.commit()
    return pg_schema


def _add_strategy(db, *, symbol, acq, sale, exp, pnl, account, stype="SPREAD", qty=200):
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO strategies
           (symbol, strategy_type, acquired_date, sale_date, expiration,
            quantity, gain_loss, is_winner, account_type)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (symbol, stype, acq, sale, exp, qty, pnl, pnl > 0, account),
    )
    sid = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return sid


def _add_leg(db, *, sid, symbol, acq, sale, exp, strike, pnl, account,
             option_type="PUT", qty=200, cost=0.0, proceeds=0.0):
    # `with`: an insert that raises (a test asserting a constraint) must still
    # close the connection, or its aborted transaction keeps a lock and the
    # schema teardown reports a leak.
    with connect() as conn:
        lid = conn.execute(
            """INSERT INTO trade_journal
               (symbol, acquired_date, sale_date, option_type, strike, expiration,
                quantity, cost_basis, proceeds, gain_loss, is_winner,
                strategy_id, account_type)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (symbol, acq, sale, option_type, strike, exp, qty, cost, proceeds,
             pnl, pnl > 0, sid, account),
        ).fetchone()[0]
    return lid


def _legs_by_account(db, sid):
    conn = connect()
    rows = conn.execute(
        "SELECT account_type, COUNT(*) FROM trade_journal WHERE strategy_id=%s GROUP BY account_type",
        (sid,),
    ).fetchall()
    conn.close()
    return dict(rows)   # [(account, count), ...]


def _build_ddog(db, ira_tag="TAXABLE"):
    """The real DDOG 2026-05 shape: same symbol/open/expiry/right in both accounts.

    ira_tag simulates the IRA strategy having been mistagged TAXABLE, which is the
    precondition that let the merge cross accounts.
    """
    tax = _add_strategy(db, symbol="DDOG", acq="2026-05-06", sale="2026-05-08",
                        exp="2026-05-08", pnl=8091.0, account="TAXABLE")
    _add_leg(db, sid=tax, symbol="DDOG", acq="2026-05-08", sale="2026-05-06",
             exp="2026-05-08", strike=115.0, pnl=16495.0, account="TAXABLE")
    _add_leg(db, sid=tax, symbol="DDOG", acq="2026-05-06", sale="2026-05-08",
             exp="2026-05-08", strike=110.0, pnl=-8404.0, account="TAXABLE")

    ira = _add_strategy(db, symbol="DDOG", acq="2026-05-06", sale="2026-05-08",
                        exp="2026-05-08", pnl=8190.0, account=ira_tag)
    _add_leg(db, sid=ira, symbol="DDOG", acq="2026-05-08", sale="2026-05-06",
             exp="2026-05-08", strike=115.0, pnl=16395.0, account="IRA")
    _add_leg(db, sid=ira, symbol="DDOG", acq="2026-05-06", sale="2026-05-08",
             exp="2026-05-08", strike=110.0, pnl=-8205.0, account="IRA")
    return tax, ira


class TestCrossAccountMerge:

    def test_mistagged_strategy_is_not_merged_across_accounts(self, db):
        """The DDOG 342 bug: two accounts' spreads collapsing into one row."""
        tax, ira = _build_ddog(db, ira_tag="TAXABLE")

        consolidate_fragmented_spreads(dry_run=False, account_type="TAXABLE")

        conn = connect()
        surviving = conn.execute("SELECT id FROM strategies").fetchall()
        conn.close()

        assert len(surviving) == 2, (
            "IRA and TAXABLE DDOG spreads were merged into one strategy row"
        )
        for (sid,) in surviving:
            accounts = _legs_by_account(db, sid)
            assert len(accounts) == 1, (
                f"strategy {sid} owns legs from multiple accounts: {accounts}"
            )

    def test_genuine_same_account_fragments_still_merge(self, db):
        """The fix must not break the feature: real fill fragments still consolidate."""
        a = _add_strategy(db, symbol="MU", acq="2026-03-02", sale="2026-03-04",
                          exp="2026-03-06", pnl=500.0, account="TAXABLE", stype="SINGLE")
        _add_leg(db, sid=a, symbol="MU", acq="2026-03-02", sale="2026-03-04",
                 exp="2026-03-06", strike=90.0, pnl=500.0, account="TAXABLE")
        b = _add_strategy(db, symbol="MU", acq="2026-03-02", sale="2026-03-04",
                          exp="2026-03-06", pnl=-200.0, account="TAXABLE", stype="SINGLE")
        _add_leg(db, sid=b, symbol="MU", acq="2026-03-02", sale="2026-03-04",
                 exp="2026-03-06", strike=85.0, pnl=-200.0, account="TAXABLE")

        deleted = consolidate_fragmented_spreads(dry_run=False, account_type="TAXABLE")

        assert deleted == 1
        conn = connect()
        rows = conn.execute(
            "SELECT id, gain_loss, strategy_type, quantity FROM strategies"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][1] == pytest.approx(300.0)
        assert rows[0][2] == "SPREAD"
        # Quantity bug (root-caused 2026-09-11): each pre-merge fragment
        # already carried this leg's own quantity (200 on both, per
        # _add_strategy/_add_leg's default). Summing the two fragments'
        # quantity fields, as the code did before the fix, would give 400 --
        # double the true 200-contract spread.
        assert rows[0][3] == 200

    def test_correctly_tagged_accounts_never_merge(self, db):
        """Even with no mistagging, an IRA run must not absorb TAXABLE strategies."""
        _build_ddog(db, ira_tag="IRA")

        consolidate_fragmented_spreads(dry_run=False, account_type="IRA")
        consolidate_fragmented_spreads(dry_run=False, account_type="TAXABLE")

        conn = connect()
        count = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
        conn.close()
        assert count == 2


class TestMixedLegDetection:

    def test_fix_mistagged_repairs_all_ira_legs(self, db):
        """Existing behaviour must be preserved."""
        sid = _add_strategy(db, symbol="NVDA", acq="2026-04-01", sale="2026-04-03",
                            exp="2026-04-03", pnl=1000.0, account="TAXABLE")
        _add_leg(db, sid=sid, symbol="NVDA", acq="2026-04-01", sale="2026-04-03",
                 exp="2026-04-03", strike=100.0, pnl=1000.0, account="IRA")

        assert fix_mistagged_strategies(dry_run=False) == 1
        conn = connect()
        acct = conn.execute("SELECT account_type FROM strategies WHERE id=%s", (sid,)).fetchone()[0]
        conn.close()
        assert acct == "IRA"

    def test_mixed_account_strategy_is_split(self, db):
        """A strategy already owning both accounts' legs must be split, not left alone."""
        sid = _add_strategy(db, symbol="DDOG", acq="2026-05-06", sale="2026-05-08",
                            exp="2026-05-08", pnl=16281.0, account="TAXABLE")
        _add_leg(db, sid=sid, symbol="DDOG", acq="2026-05-08", sale="2026-05-06",
                 exp="2026-05-08", strike=115.0, pnl=16495.0, account="TAXABLE")
        _add_leg(db, sid=sid, symbol="DDOG", acq="2026-05-06", sale="2026-05-08",
                 exp="2026-05-08", strike=110.0, pnl=-8404.0, account="TAXABLE")
        _add_leg(db, sid=sid, symbol="DDOG", acq="2026-05-08", sale="2026-05-06",
                 exp="2026-05-08", strike=115.0, pnl=16395.0, account="IRA")
        _add_leg(db, sid=sid, symbol="DDOG", acq="2026-05-06", sale="2026-05-08",
                 exp="2026-05-08", strike=110.0, pnl=-8205.0, account="IRA")

        from scripts.regroup_strategies import split_mixed_account_strategies
        assert split_mixed_account_strategies(dry_run=False) == 1

        conn = connect()
        rows = conn.execute(
            "SELECT id, account_type, gain_loss FROM strategies ORDER BY account_type"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        by_acct = {r[1]: r[2] for r in rows}
        assert by_acct["IRA"] == pytest.approx(8190.0)
        assert by_acct["TAXABLE"] == pytest.approx(8091.0)
        for sid_, acct, _ in rows:
            assert list(_legs_by_account(db, sid_)) == [acct]

        # Quantity bug (root-caused 2026-09-11): each split-out account owns
        # a 2-leg spread (strikes 115 and 110), 200 contracts per leg by
        # _add_leg's default. Summing both legs, as the code did before the
        # fix, would give 400 -- double the true 200-contract spread.
        conn = connect()
        quantities = dict(conn.execute(
            "SELECT account_type, quantity FROM strategies"
        ).fetchall())
        conn.close()
        assert quantities == {"IRA": 200, "TAXABLE": 200}


class TestStockDuplicateLinking:

    def test_link_stock_duplicates_respects_account(self, db):
        """Latent same-class bug: identical stock lots in two accounts must not cross-link."""
        sid = _add_strategy(db, symbol="VOO", acq="2026-01-02", sale="2026-01-05",
                            exp=None, pnl=500.0, account="TAXABLE", stype="STOCK")
        conn = connect()
        cur = conn.cursor()
        for acct, s in (("TAXABLE", sid), ("IRA", None)):
            cur.execute(
                """INSERT INTO trade_journal
                   (symbol, acquired_date, sale_date, option_type, strike, expiration,
                    quantity, cost_basis, proceeds, gain_loss, is_winner, strategy_id, account_type)
                   VALUES ('VOO','2026-01-02','2026-01-05',NULL,NULL,NULL,10,1000.0,1500.0,500.0,true,%s,%s)""",
                (s, acct),
            )
        conn.commit()
        conn.close()

        leg = LegRecord(id=0, symbol="VOO", acquired_date="2026-01-02", sale_date="2026-01-05",
                        option_type=None, strike=None, expiration=None, quantity=10,
                        cost_basis=1000.0, proceeds=1500.0, gain_loss=500.0,
                        is_winner=True, earnings_date=None, actual_move=None)

        conn = connect()
        link_stock_duplicates(conn, [leg], sid, account_type="TAXABLE")
        conn.commit()
        ira_linked = conn.execute(
            "SELECT strategy_id FROM trade_journal WHERE account_type='IRA'"
        ).fetchone()[0]
        conn.close()

        assert ira_linked is None, "IRA stock lot was linked to a TAXABLE strategy"


class TestQuantityAcrossLegs:
    """Regression cover for the quantity bug (root-caused 2026-09-11): a
    strategy's quantity must be per-LEG, not summed across every leg -- a
    spread trades matching quantity on every leg by construction. The three
    call sites that got this wrong (create_strategy_and_link via
    run_regrouping, consolidate_fragmented_spreads, split_mixed_account_
    strategies's _aggregates) each have their own test; this one covers
    run_regrouping end-to-end against orphan legs, reproducing the exact
    shape that surfaced the bug live: an 800-contract $145/$150 LULU call
    spread stored as quantity=1600 (both legs summed) instead of 800.
    """

    def test_run_regrouping_takes_one_leg_not_the_sum(self, db):
        """Mirrors the live LULU case: one leg split across multiple partial
        fills (300 + 500 = 800), the other leg filled in one shot (800).
        Summing every trade_journal row for this position, as
        create_strategy_and_link did before the fix, gives 800+800+800 =
        2400; taking the sum within each strike and the max across strikes
        gives the correct 800.
        """
        # Distinct cost_basis per fill: the real idx_trade_journal_dedup
        # treats two legs identical on (symbol, dates, type, strike,
        # cost_basis, account) as the same fill imported twice.
        _add_leg(db, sid=None, symbol="LULU", acq="2026-09-03", sale="2026-09-04",
                 exp="2026-09-11", strike=145.0, pnl=16334.0, account="IRA",
                 option_type="CALL", qty=300, cost=9000.0)
        _add_leg(db, sid=None, symbol="LULU", acq="2026-09-03", sale="2026-09-04",
                 exp="2026-09-11", strike=145.0, pnl=17334.0, account="IRA",
                 option_type="CALL", qty=500, cost=15000.0)
        _add_leg(db, sid=None, symbol="LULU", acq="2026-09-03", sale="2026-09-04",
                 exp="2026-09-11", strike=150.0, pnl=-21370.0, account="IRA",
                 option_type="CALL", qty=800)

        stats = run_regrouping(dry_run=False, account_type="IRA")

        assert stats['strategies_created'] == 1
        conn = connect()
        row = conn.execute(
            "SELECT strategy_type, quantity FROM strategies"
        ).fetchone()
        conn.close()
        assert row == ("SPREAD", 800)


class TestStockOrphanPath:
    """load_stock_orphans() went unexercised and was broken by the port.

    Its dedup query used `COALESCE(acquired_date, '')` (a date column compared
    to an empty string, which does not type-check in Postgres) and
    `ROUND(cost_basis, 2)` (no such function for double precision). Every
    fixture in this file inserts OPTION legs, so nothing reached the
    stock-only branch and the suite stayed green while /journal's stock path
    raised on the first row.
    """

    def test_stock_orphans_load(self, db):
        _add_leg(db, sid=None, symbol="VOO", acq="2026-01-02", sale="2026-01-05",
                 exp=None, strike=None, pnl=500.0, account="TAXABLE",
                 option_type=None, qty=10, cost=1000.0, proceeds=1500.0)
        legs = load_stock_orphans("TAXABLE")
        assert [leg.symbol for leg in legs] == ["VOO"]
        assert legs[0].option_type is None

    def test_a_null_acquired_date_does_not_break_the_dedup(self, db):
        """The exact shape that broke: GROUP BY over a NULL date column.
        Postgres GROUP BY already treats NULLs as equal, so the COALESCE the
        SQLite version carried was both unnecessary and untypeable."""
        _add_leg(db, sid=None, symbol="SPY", acq=None, sale="2026-02-05",
                 exp=None, strike=None, pnl=100.0, account="TAXABLE",
                 option_type=None, qty=5, cost=500.0, proceeds=600.0)
        legs = load_stock_orphans("TAXABLE")
        assert [leg.symbol for leg in legs] == ["SPY"]
        assert legs[0].acquired_date is None

    def test_sub_cent_cost_differences_collapse_to_one(self, db):
        """A sub-cent cost difference is one lot, and since migration 2 the
        database says so before regroup ever runs.

        cost_basis was double precision, so 1000.001 and 1000.004 were two
        legal rows and only load_stock_orphans()' ROUND(cost_basis, 2) dedup
        folded them together. It is numeric(14,2) now: both values store as
        1000.00, and the second insert hits trade_journal_dedup -- the guard
        moved from the reader to the schema. A genuinely different lot
        (2000.00) still survives, and the reader's ROUND dedup is kept for
        proceeds, which is not part of the unique key."""
        import psycopg
        for cost in (1000.001, 2000.0):
            _add_leg(db, sid=None, symbol="QQQ", acq="2026-03-02",
                     sale="2026-03-05", exp=None, strike=None, pnl=50.0,
                     account="TAXABLE", option_type=None, qty=3,
                     cost=cost, proceeds=1050.0)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _add_leg(db, sid=None, symbol="QQQ", acq="2026-03-02",
                     sale="2026-03-05", exp=None, strike=None, pnl=50.0,
                     account="TAXABLE", option_type=None, qty=3,
                     cost=1000.004, proceeds=1050.0)
        legs = load_stock_orphans("TAXABLE")
        assert sorted(leg.cost_basis for leg in legs) == [1000.0, 2000.0]

    def test_account_isolation_holds_for_stock_too(self, db):
        """The DDOG defect's stock-side form: the same lot in both accounts
        must not be linked into whichever ran first."""
        for acct in ("TAXABLE", "IRA"):
            _add_leg(db, sid=None, symbol="VTI", acq="2026-04-02",
                     sale="2026-04-05", exp=None, strike=None, pnl=75.0,
                     account=acct, option_type=None, qty=8,
                     cost=800.0, proceeds=875.0)
        assert [leg.symbol for leg in load_stock_orphans("IRA")] == ["VTI"]
        assert [leg.symbol for leg in load_stock_orphans("TAXABLE")] == ["VTI"]


class TestConsolidateDuplicateStocks:
    """Merging duplicate STOCK strategies set quantity to (sum of legs) // copies
    and P&L to the AVERAGE of the copies' stored P&L, on the stated theory that
    "legs were triple-linked" -- impossible, since a leg has one strategy_id.
    Eleven STOCK strategies ended up storing 2-6x their legs' shares (one at twice
    its legs, another at three times). The merged row is recomputed
    from the legs it ends up owning."""

    def test_the_merged_strategy_is_the_sum_of_its_legs(self, db):
        from scripts.regroup_strategies import consolidate_duplicate_stocks

        a = _add_strategy(db, symbol="VOO", acq="2026-01-02", sale="2026-03-02", exp=None,
                          pnl=1000.0, account="TAXABLE", stype="STOCK", qty=100)
        b = _add_strategy(db, symbol="VOO", acq="2026-01-05", sale="2026-03-02", exp=None,
                          pnl=500.0, account="TAXABLE", stype="STOCK", qty=50)
        _add_leg(db, sid=a, symbol="VOO", acq="2026-01-02", sale="2026-03-02", exp=None,
                 strike=None, pnl=1000.0, account="TAXABLE", option_type=None, qty=100, cost=1.0)
        _add_leg(db, sid=b, symbol="VOO", acq="2026-01-05", sale="2026-03-02", exp=None,
                 strike=None, pnl=500.0, account="TAXABLE", option_type=None, qty=50, cost=2.0)

        assert consolidate_duplicate_stocks(dry_run=False) == 1
        with connect() as conn:
            rows = conn.execute("SELECT id, quantity, gain_loss, is_winner FROM strategies").fetchall()
        assert [(r.id, r.quantity, r.gain_loss, r.is_winner) for r in rows] == [(a, 150, 1500.0, True)]
