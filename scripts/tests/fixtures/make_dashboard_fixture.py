"""Build a fixture database mirroring production's schema AND its null-ness.

Column population is copied from the 2026-09-08 measurement, because that is
what makes the "returns at least one row" assertion meaningful:
  - analysis_log.strategy_*   : dropped in migration 2 (were always NULL)
  - analysis_log.term_slope_ratio : sparse (137 of 1,238)
  - strategies.earnings_date  : ~43% populated (137 of 315)
  - strategies.trade_type     : set on every row (re-measured 2026-09-15: 0 NULL
                                of 310; every writer stores it since 2026-09-13)
"""

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402

# Tables come FROM 2.0/sql/baseline_schema.sql, never transcribed. A fixture
# that drifts from the real schema tests the wrong thing while staying green,
# and this fixture exists precisely because the Grafana plugin renders a
# missing column as an empty panel rather than an error.
FIXTURE_TABLES = ("analysis_log", "strategies", "job_status",
                  "historical_moves", "trade_journal", "risk_metrics_snapshot",
                  "rule_evidence_snapshot", "positions")

SOURCES = ["whisper", "scan", "analyze", "whisper", "analyze"]
STRATEGY_TYPES = ["SPREAD", "SPREAD", "SINGLE", "STOCK", "IRON_CONDOR", "STRANGLE"]


def build_fixture(schema):
    """Populate the caller's throwaway schema. Returns the schema name."""
    conn = connect()
    try:
        return _populate(conn, schema)
    finally:
        conn.close()


def _populate(conn, schema):
    conn.execute(baseline_ddl(*FIXTURE_TABLES))

    for i in range(40):
        # recommendation cycles through all four tiers directly (i % 4); vrp
        # itself no longer determines the tier -- panels read the stored
        # recommendation, graded at write time in its own convention.
        vrp = 1.0 + (i % 12) * 0.12
        conn.execute(
            "INSERT INTO analysis_log (timestamp, ticker, earnings_date, expiration,"
            " implied_move_pct, historical_intraday_mean_pct, vrp_intraday_ratio,"
            " recommendation, vix_level, historical_close_mean_pct, vrp_close_ratio,"
            " term_slope_ratio, source, vrp_convention)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                f"2026-0{1 + i % 9}-1{i % 9}T10:00:00", f"TK{i % 11}",
                f"2026-0{1 + i % 9}-2{i % 8}",
                # NOT NULL in the real schema -- the hand-written fixture this
                # replaced omitted the constraint and so omitted the column.
                f"2026-0{1 + i % 9}-2{i % 8}",
                6.0 + i % 5, 3.0 + i % 3, vrp,
                # Lowercase, as production stores Recommendation.value and as
                # analysis_log's CHECK constraint requires.
                ("skip", "marginal", "good", "excellent")[i % 4], 15.0 + i % 10,
                3.4 + i % 3, vrp / 1.9,
                # sparse, like production (137 of 1,238)
                (1.1 + (i % 14) * 0.1) if i % 9 == 0 else None,
                SOURCES[i % len(SOURCES)],
                # alternates so both conventions' rows exist; vrp_close_ratio
                # above is always populated, satisfying either convention's CHECK.
                "intraday" if i % 2 == 0 else "close",
            ),
        )

    for i in range(30):
        acct = ["TAXABLE", "IRA"][i % 2]
        conn.execute(
            "INSERT INTO strategies (symbol, strategy_type, acquired_date,"
            " sale_date, expiration, days_held, quantity, gain_loss, is_winner,"
            " earnings_date, trade_type, trr_at_entry, account_type)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                f"TK{i % 11}", STRATEGY_TYPES[i % len(STRATEGY_TYPES)],
                f"2026-0{1 + i % 9}-05", f"2026-0{1 + i % 9}-1{i % 8}",
                # Expiration is NOT nullable in practice: production has no
                # earnings trade without one, and rule_evidence.dte_band does
                # `expiration - acquired_date`. Cycling 1 / 4 / 10 days past the
                # 05 acquisition puts rows in all three of §5's DTE bands.
                f"2026-0{1 + i % 9}-{['06', '09', '15'][i % 3]}",
                1 + i % 5, [25, 75, 150, 250][i % 4],
                (500.0 * (i % 7)) - 1500.0, bool(i % 3),
                # ~43% populated, like production
                f"2026-0{1 + i % 9}-2{i % 8}" if i % 7 < 3 else None,
                ["NEW", "NEW", "REPAIR", "ROLL"][i % 4],
                1.2 + (i % 9) * 0.4, acct,
            ),
        )

    # Outcome rows so the A/B accrual panel has something to count. Dates are
    # offset a few days from the analysis_log rows to exercise the 30-day
    # nearest-date tolerance rather than an exact-equality join.
    for i in range(40):
        # Prices only: the three move percentages are GENERATED from them
        # (migration 2), so close_move_pct here comes out at
        # -3.0 + (i % 9) * 0.8, the value this loop used to insert directly.
        conn.execute(
            "INSERT INTO historical_moves (ticker, earnings_date, prev_close,"
            " earnings_open, earnings_high, earnings_low, earnings_close)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                f"TK{i % 11}", f"2026-0{1 + i % 9}-2{(i + 2) % 8}",
                100.0, 99.0, 102.0, 97.0, 100.0 * (1 + (-3.0 + (i % 9) * 0.8) / 100),
            ),
        )

    for i in range(12):
        conn.execute(
            "INSERT INTO job_status (date, job_name, status, updated_at)"
            " VALUES (%s,%s,%s,%s)",
            (
                f"2026-09-{1 + i:02d}",
                "morning-digest" if i % 2 else "ab-digest-diff",
                "error" if i == 5 else "success",
                f"2026-09-{1 + i:02d}T07:41:00-04:00",
            ),
        )

    # Targeted rows for the trade-accuracy panels (trading_stance_accuracy,
    # trading_direction_accuracy, trading_strike_accuracy,
    # trading_direction_strike_crosstab), which need trade_journal legs (for
    # short-leg detection via the acquired>sale date inversion -- Fidelity's
    # credit-trade convention) joined to a same-ticker historical_moves row
    # with earnings_close populated. The generic loops above provide neither,
    # so these strategies are separate rows, isolated by symbol (ACn), from
    # the TK0-TK10 rows the rest of this fixture uses -- they carry no
    # trade_journal legs and so are correctly invisible to the new queries.
    # One of each cross-tab quadrant, plus one implausible strike/price ratio
    # (AC5, ratio 3.0x) to exercise the 0.45-2.2x plausibility filter.
    accuracy_cases = [
        # symbol, strategy_type, short_option_type, short_strike, other_strike,
        # close_move_pct, earnings_close, is_winner, gain_loss
        ("AC1", "SPREAD", "PUT", 90.0, 85.0, 5.0, 100.0, 1, 800.0),
        ("AC2", "SPREAD", "CALL", 110.0, 115.0, -5.0, 100.0, 1, 750.0),
        ("AC3", "SPREAD", "PUT", 95.0, 90.0, -8.0, 88.0, 0, -1200.0),
        ("AC4", "SINGLE", "CALL", 105.0, None, 3.0, 108.0, 0, -900.0),
        ("AC5", "SPREAD", "PUT", 300.0, 295.0, 4.0, 100.0, 1, 600.0),
    ]
    for i, (sym, stype, short_opt, short_strike, other_strike, close_move,
            e_close, winner, gain) in enumerate(accuracy_cases):
        acquired, sale, edate = f"2026-0{1 + i}-01", f"2026-0{1 + i}-10", f"2026-0{1 + i}-15"
        cur = conn.execute(
            "INSERT INTO strategies (symbol, strategy_type, acquired_date,"
            " sale_date, expiration, days_held, quantity, gain_loss, is_winner,"
            " earnings_date, trade_type, account_type)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            # expiration 4 days after entry: every row here carries an earnings
            # date, so all of them reach dte_band.
            (sym, stype, acquired, sale, f"2026-0{1 + i}-05", 9, 50, gain,
             bool(winner), edate, "NEW", "TAXABLE"),
        )
        sid = cur.fetchone()[0]
        # Short leg: acquired_date > sale_date (inverted vs the long leg
        # below) is the sell-to-open signal, per CLAUDE.md's documented
        # Fidelity CSV convention.
        conn.execute(
            "INSERT INTO trade_journal (symbol, acquired_date, sale_date,"
            " option_type, strike, quantity, cost_basis, proceeds, gain_loss,"
            " is_winner, strategy_id, account_type) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sym, sale, acquired, short_opt, short_strike, 50, 500.0, 1000.0,
             500.0, True, sid, "TAXABLE"),
        )
        if other_strike is not None:
            conn.execute(
                "INSERT INTO trade_journal (symbol, acquired_date, sale_date,"
                " option_type, strike, quantity, cost_basis, proceeds, gain_loss,"
                " is_winner, strategy_id, account_type) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (sym, acquired, sale, short_opt, other_strike, 50, 300.0, 100.0,
                 -200.0, False, sid, "TAXABLE"),
            )
        # The percentages are generated from the prices, so solve for the
        # previous close that makes close_move_pct equal the case's intended
        # move while keeping its earnings_close (the strike panels read both).
        prev_close = e_close / (1 + close_move / 100)
        conn.execute(
            "INSERT INTO historical_moves (ticker, earnings_date, prev_close,"
            " earnings_open, earnings_high, earnings_low, earnings_close)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (sym, edate, prev_close, prev_close * (1 + close_move / 200),
             105.0, 95.0, e_close),
        )

    # Two trades placed in both accounts, so a panel that counts strategies rows
    # rather than positions counts each twice (test_dashboard_queries). MIR is a
    # put spread with legs and a move, 60 contracts in the IRA and 50 in TAXABLE
    # (one 110-contract trade), and enters every trade panel; MIS is a strangle
    # opened after the ban, which enters the tripwire. Both rows of a pair carry
    # the same trade, legs and TRR, as the view's merge requires.
    mirrored = [
        # symbol, strategy_type, acquired, sale, earnings, trr, legs, per account (acct, qty, gain)
        ("MIR", "SPREAD", "2026-07-01", "2026-07-02", "2026-07-01", 2.0,
         [("PUT", 95.0, True), ("PUT", 90.0, False)],
         [("IRA", 60, 700.0), ("TAXABLE", 50, 500.0)]),
        ("MIS", "STRANGLE", "2026-06-10", "2026-06-11", None, 2.6, [],
         [("IRA", 10, -300.0), ("TAXABLE", 10, -200.0)]),
    ]
    for sym, stype, acquired, sale, edate, trr, legs, accounts in mirrored:
        for acct, qty, gain in accounts:
            sid = conn.execute(
                "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date,"
                " expiration, days_held, quantity, gain_loss, is_winner, earnings_date,"
                " trade_type, trr_at_entry, account_type)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (sym, stype, acquired, sale, sale, 1, qty, gain, gain > 0, edate, "NEW",
                 trr, acct),
            ).fetchone()[0]
            for option_type, strike, short in legs:
                opened, closed = (sale, acquired) if short else (acquired, sale)
                conn.execute(
                    "INSERT INTO trade_journal (symbol, acquired_date, sale_date,"
                    " option_type, strike, expiration, quantity, cost_basis, proceeds,"
                    " gain_loss, is_winner, strategy_id, account_type)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sym, opened, closed, option_type, strike, sale, qty, 100.0, 200.0,
                     100.0, True, sid, acct),
                )
    # MIR's reaction: close +2%, the 95 short put held under a 102 close.
    conn.execute(
        "INSERT INTO historical_moves (ticker, earnings_date, prev_close,"
        " earnings_open, earnings_high, earnings_low, earnings_close)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s)",
        ("MIR", "2026-07-01", 100.0, 101.0, 103.0, 99.0, 102.0),
    )

    conn.commit()

    # Real computation, not fabricated rows: exercises add_risk_metrics()'s
    # actual code path (net_campaigns/sharpe_ratio/sortino_ratio) against
    # this fixture's own strategies data, the same way production's snapshot
    # build does. Robust to wall-clock drift from the fixture's fixed 2026
    # dates -- add_risk_metrics always writes one row per window even when a
    # window's trailing series is empty (sharpe/sortino NULL in that case).
    from scripts.refresh_risk_metrics import refresh_risk_metrics

    refresh_risk_metrics(conn)

    # Same reasoning as the risk metrics above: real computation over this
    # fixture's own book, not fabricated rows. The four snapshot-reading panels
    # return nothing against an empty table, and
    # test_all_queries_return_at_least_one_row has no exemption list -- so a
    # fabricated fixture would prove the panels run without proving they agree
    # with rule_evidence.py, which is the entire point of the table.
    from scripts.refresh_rule_evidence import refresh_rule_evidence

    refresh_rule_evidence(conn)
    conn.commit()
    return schema
