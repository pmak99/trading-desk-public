#!/usr/bin/env python3
"""
Weekly data-integrity report for the trade journal and the earnings data.

Read-only. Every check here is a defect that was found by hand on 2026-09-13
after sitting unnoticed: strategies dated days after their report, 93 rows with
no trade_type, traded events missing their move row, 29 same-ticker move
clusters, positions whose P&L disagreed with their legs. `/maintenance` runs it
weekly so the next one surfaces in days, not months.

It reports; it never repairs. Each finding needs its own verified fix (the
reconciler, re-enrich, the calendar sync's two-source rule, a scoped backfill).

Two checks watch the `positions` view (migration 5), the one definition of a
trade. Until 2026-09-15 rule_evidence.py merged a trade placed in both accounts
in Python and refused two shapes: the same trade twice in one account, and a
cross-account pair that differed only in earnings date or parent. The view
leaves both unmerged and silent, so they are reported here instead.

Usage:
    2.0/venv/bin/python scripts/integrity_report.py          # counts, plus rows for journal checks
    2.0/venv/bin/python scripts/integrity_report.py --full   # every row of every check
"""

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import List, Sequence

from common.constants import QUARTER_GAP_DAYS  # noqa: E402
from common.db import connect, target  # noqa: E402
from common.timezone import today_et  # noqa: E402

# Same index set rule_evidence.py excludes from earnings trades.
INDEX_SYMBOLS = ("SPY", "SPX", "SPXW")
CLUSTER_DAYS = 15              # CLAUDE.md: audit duplicates at <=15 days, never <=2
MOVE_BACKFILL_LAG_DAYS = 5     # an AMC report's move needs the next session's bars
TRADED_HISTORY_DAYS = 365      # how far before a ticker's first trade its moves matter


@dataclass(frozen=True)
class Check:
    name: str
    section: str      # "journal" or "market data"
    title: str
    sql: str          # parameters: %(today)s, %(index)s, %(cluster_days)s, %(lag_days)s, %(history_days)s, %(quarter_gap_days)s
    rows_by_default: bool


@dataclass
class Result:
    check: Check
    columns: List[str]
    rows: List[tuple]


_TRADED = "SELECT symbol FROM strategies UNION SELECT symbol FROM trade_journal"

# analysis_log gets one row per ticker every time /analyze, scan or whisper actually
# ran VRP on it -- broader than a fill (241 tickers vs 107 traded on 2026-09-15),
# narrower than the raw whisper candidate list, most of which never clears VRP at
# all (calendar_cluster_15d below finds ~250 same-ticker clusters, 0 of them on a
# traded ticker -- that's the noise floor _ANALYZED is built to stay out of).
# Used only by quarter_missing_from_calendar_and_moves: added 2026-09-16 after
# LEN's Mar/Jun 2026 quarters sat missing for six months, invisible to that check
# because it scanned _TRADED alone and LEN had never been traded.
_ANALYZED = f"{_TRADED} UNION SELECT ticker FROM analysis_log"

# Every strategies row with the legs the positions view keyed it on, and the rows
# paired on the rest of a trade: symbol, strategy type, entry and exit dates,
# expiration and legs. Earnings date, parent and account are left for the check.
_SAME_TRADE = """
    WITH keyed AS (
        SELECT s.id, s.symbol, s.strategy_type, s.account_type, s.acquired_date, s.sale_date,
               s.expiration, p.legs, s.earnings_date, s.parent_strategy_id
        FROM positions p
        CROSS JOIN LATERAL unnest(p.strategy_ids) AS member(id)
        JOIN strategies s ON s.id = member.id)
    SELECT a.id AS first_id, b.id AS second_id, a.symbol, a.strategy_type,
           a.account_type AS first_account, b.account_type AS second_account,
           a.acquired_date, a.sale_date, a.legs,
           a.earnings_date AS first_earnings, b.earnings_date AS second_earnings,
           a.parent_strategy_id AS first_parent, b.parent_strategy_id AS second_parent
    FROM keyed a JOIN keyed b
      ON a.id < b.id AND a.symbol = b.symbol AND a.strategy_type = b.strategy_type
     AND a.acquired_date = b.acquired_date AND a.sale_date = b.sale_date
     AND a.expiration IS NOT DISTINCT FROM b.expiration AND a.legs = b.legs"""

CHECKS: Sequence[Check] = (
    Check("strategy_pnl_differs_from_legs", "journal",
          "Strategies whose P&L is not the sum of their legs",
          """SELECT s.id, s.symbol, s.gain_loss, sum(t.gain_loss) AS legs_sum
             FROM strategies s JOIN trade_journal t ON t.strategy_id = s.id
             GROUP BY s.id, s.symbol, s.gain_loss
             HAVING abs(s.gain_loss - sum(t.gain_loss)) > 0.01 ORDER BY s.id""", True),
    Check("strategy_without_legs", "journal", "Strategies with no legs",
          """SELECT s.id, s.symbol, s.acquired_date, s.gain_loss FROM strategies s
             WHERE NOT EXISTS (SELECT 1 FROM trade_journal t WHERE t.strategy_id = s.id)
             ORDER BY s.id""", True),
    Check("leg_without_strategy", "journal", "Legs linked to no strategy",
          """SELECT id, symbol, sale_date, gain_loss FROM trade_journal
             WHERE strategy_id IS NULL ORDER BY id""", True),
    Check("strategy_dates_inverted", "journal",
          "Strategies closed or expiring before they opened (strategies is normalised)",
          """SELECT id, symbol, acquired_date, sale_date, expiration FROM strategies
             WHERE sale_date < acquired_date OR expiration < acquired_date ORDER BY id""", True),
    Check("earnings_date_without_move", "journal",
          "Strategies dated to an earnings event that has no move row",
          """SELECT s.id, s.symbol, s.acquired_date, s.earnings_date FROM strategies s
             WHERE s.earnings_date IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM historical_moves h
               WHERE h.ticker = s.symbol AND h.earnings_date = s.earnings_date)
             ORDER BY s.id""", True),
    Check("trade_type_missing", "journal", "Strategies with no trade_type",
          """SELECT id, symbol, strategy_type, account_type, acquired_date FROM strategies
             WHERE trade_type IS NULL ORDER BY acquired_date, id""", True),
    Check("adjustment_label_disagrees_with_parent", "journal",
          "A parent link without an adjustment label, or ROLL/REPAIR without a parent",
          """SELECT id, symbol, trade_type, parent_strategy_id FROM strategies
             WHERE (parent_strategy_id IS NOT NULL
                    AND trade_type IS DISTINCT FROM 'ROLL' AND trade_type IS DISTINCT FROM 'REPAIR'
                    AND trade_type IS DISTINCT FROM 'ADJUSTMENT')
                OR (parent_strategy_id IS NULL AND trade_type IN ('ROLL', 'REPAIR', 'ADJUSTMENT'))
             ORDER BY id""", True),
    Check("mirrored_trade_not_merged", "journal",
          "One trade in IRA and TAXABLE that the positions view counts twice (earnings date or parent differ)",
          _SAME_TRADE + """
             WHERE a.account_type <> b.account_type
               AND (a.earnings_date IS DISTINCT FROM b.earnings_date
                    OR a.parent_strategy_id IS DISTINCT FROM b.parent_strategy_id)
             ORDER BY a.acquired_date, a.id""", True),
    Check("same_trade_twice_in_one_account", "journal",
          "The same trade twice in one account (a duplicate import, or a fill split into two strategies)",
          _SAME_TRADE + """
             WHERE a.account_type = b.account_type
             ORDER BY a.acquired_date, a.id""", True),
    Check("undated_trade_held_across_event", "journal",
          "Undated non-index spreads/singles open across a calendar earnings date",
          """SELECT s.id, s.symbol, s.strategy_type, s.acquired_date, s.sale_date,
                    min(e.earnings_date) AS event, s.gain_loss
             FROM strategies s JOIN earnings_calendar e
               ON e.ticker = s.symbol AND e.earnings_date BETWEEN s.acquired_date AND s.sale_date
             WHERE s.earnings_date IS NULL AND s.strategy_type IN ('SPREAD', 'SINGLE')
               AND s.symbol <> ALL(%(index)s)
             GROUP BY s.id ORDER BY s.acquired_date, s.id""", True),
    Check("move_cluster_15d", "market data",
          "Same-ticker move rows within 15 days (one event stored twice, or a wrong date)",
          """SELECT a.ticker, a.earnings_date AS first, b.earnings_date AS second,
                    b.earnings_date - a.earnings_date AS days
             FROM historical_moves a JOIN historical_moves b
               ON a.ticker = b.ticker AND a.earnings_date < b.earnings_date
              AND b.earnings_date - a.earnings_date <= %(cluster_days)s
             ORDER BY a.ticker, a.earnings_date""", True),
    Check("calendar_cluster_15d", "market data", "Same-ticker calendar rows within 15 days",
          f"""SELECT a.ticker, a.earnings_date AS first, b.earnings_date AS second,
                     a.ticker IN ({_TRADED}) AS traded
              FROM earnings_calendar a JOIN earnings_calendar b
                ON a.ticker = b.ticker AND a.earnings_date < b.earnings_date
               AND b.earnings_date - a.earnings_date <= %(cluster_days)s
              ORDER BY traded DESC, a.ticker, a.earnings_date""", False),
    Check("move_without_calendar_row", "market data",
          "Move rows with no calendar row (their reaction day cannot be checked)",
          """SELECT h.ticker, h.earnings_date FROM historical_moves h
             WHERE NOT EXISTS (SELECT 1 FROM earnings_calendar e
                               WHERE e.ticker = h.ticker AND e.earnings_date = h.earnings_date)
             ORDER BY h.earnings_date, h.ticker""", False),
    Check("past_event_timing_unknown", "market data",
          "Past calendar events whose timing is still UNKNOWN (moves measure the wrong day)",
          f"""SELECT ticker, earnings_date, ticker IN ({_TRADED}) AS traded
              FROM earnings_calendar
              WHERE timing = 'UNKNOWN' AND earnings_date < %(today)s
              ORDER BY traded DESC, earnings_date, ticker""", False),
    Check("traded_event_without_move", "market data",
          "Past calendar events on traded tickers with no move row",
          """WITH first_trade AS (
                SELECT symbol, min(acquired_date) AS first FROM strategies GROUP BY symbol)
              SELECT e.ticker, e.earnings_date, e.timing, e.confirmed
              FROM earnings_calendar e JOIN first_trade f ON f.symbol = e.ticker
              WHERE e.earnings_date >= f.first - %(history_days)s
                AND e.earnings_date <= %(today)s::date - %(lag_days)s
                AND e.ticker <> ALL(%(index)s)
                AND NOT EXISTS (SELECT 1 FROM historical_moves h
                                WHERE h.ticker = e.ticker AND h.earnings_date = e.earnings_date)
              ORDER BY e.earnings_date, e.ticker""", True),
    # traded_event_without_move needs a calendar row to notice a gap. A quarter the
    # calendar never held, or held and later lost, is invisible to
    # it and to jobs/record_moves.py, which starts from calendar rows. This check
    # looks for the hole itself: consecutive stored dates, from either table, too
    # far apart to be adjacent quarters.
    #
    # Scoped to _ANALYZED (traded, journaled, or ever run through /analyze/scan/
    # whisper), not just _TRADED: until 2026-09-16 this only scanned traded
    # tickers, so a gap on a name nobody had traded yet -- LEN's missing Mar/Jun
    # 2026 quarters -- was invisible here too, found only by hand months later.
    # rows_by_default=False (was True): _ANALYZED is ~2x _TRADED, so this prints
    # a by-year summary plus every row on an actually-traded ticker, same as
    # calendar_cluster_15d and past_event_timing_unknown below.
    Check("quarter_missing_from_calendar_and_moves", "market data",
          f"More than {QUARTER_GAP_DAYS} days between consecutive calendar-or-move dates "
          "for a traded or analyzed ticker (a quarter absent from both tables)",
          f"""WITH dates AS (
                SELECT ticker, earnings_date FROM earnings_calendar WHERE ticker IN ({_ANALYZED})
                UNION
                SELECT ticker, earnings_date FROM historical_moves WHERE ticker IN ({_ANALYZED})),
              seq AS (
                SELECT ticker, earnings_date,
                       lag(earnings_date) OVER (PARTITION BY ticker ORDER BY earnings_date) AS previous,
                       ticker IN ({_TRADED}) AS traded
                FROM dates WHERE ticker <> ALL(%(index)s))
              SELECT ticker, previous, earnings_date AS next, earnings_date - previous AS days, traded
              FROM seq WHERE earnings_date - previous > %(quarter_gap_days)s
              ORDER BY traded DESC, ticker, previous""", False),
)


def run_checks(conn, today: date) -> List[Result]:
    params = {"today": today, "index": list(INDEX_SYMBOLS), "cluster_days": CLUSTER_DAYS,
              "lag_days": MOVE_BACKFILL_LAG_DAYS, "history_days": TRADED_HISTORY_DAYS,
              "quarter_gap_days": QUARTER_GAP_DAYS}
    results = []
    for check in CHECKS:
        cur = conn.execute(check.sql, params)
        results.append(Result(check, [c.name for c in cur.description], cur.fetchall()))
    return results


def _print(results: List[Result], full: bool) -> None:
    for section in ("journal", "market data"):
        print(f"\n== {section} ==")
        for r in (r for r in results if r.check.section == section):
            mark = "ok " if not r.rows else "!! "
            print(f"\n{mark}{r.check.name}: {len(r.rows)}  — {r.check.title}")
            if not r.rows:
                continue
            if full or r.check.rows_by_default:
                print("    " + " | ".join(r.columns))
                for row in r.rows:
                    print("    " + " | ".join("" if v is None else str(v) for v in row))
                continue
            years = Counter(str(v.year) for row in r.rows for v in row[1:2] if isinstance(v, date))
            if years:
                print("    by year: " + ", ".join(f"{y}: {n}" for y, n in sorted(years.items())))
            if "traded" in r.columns:
                traded = [row for row in r.rows if row[r.columns.index("traded")]]
                print(f"    on traded tickers: {len(traded)}")
                for row in traded:
                    print("    " + " | ".join("" if v is None else str(v) for v in row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--full", action="store_true", help="print every row of every check")
    args = parser.parse_args()
    today = today_et()
    with connect() as conn:
        results = run_checks(conn, today)
    flagged = sum(1 for r in results if r.rows)
    print(f"db: {target()}   as of {today}   checks: {len(results)}   with findings: {flagged}")
    _print(results, args.full)
    return 0


if __name__ == "__main__":
    sys.exit(main())
