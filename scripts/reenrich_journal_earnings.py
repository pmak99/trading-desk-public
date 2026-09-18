#!/usr/bin/env python3
"""
Fill missing earnings_date / actual_move on trade_journal legs and strategies.

`/journal` correlates each option leg with the earnings event it brackets
(journal/vrp_correlation.py) at import time and copies the result onto the
strategy it creates. Legs imported before an event reached historical_moves,
or while the correlation was broken, keep NULL forever -- nothing revisits
them. On 2026-09-13 that was 768 of 1,121 option legs and 155 of 282
spread/single strategies, and every DTE-to-earnings figure is computed from
those columns.

By default it fills gaps only:
  * a leg is written only where its earnings_date is NULL;
  * a strategy is written only where its earnings_date is NULL and every leg
    that has a date names the same event;
  * a stored date the matcher disagrees with is REPORTED, never replaced --
    later calendar repairs move events, and which date is right is a review
    question, not something to settle by overwrite.

--refresh does the other half instead: a dated leg or strategy whose
actual_move no longer matches its event's historical_moves row gets the row's
current move. A data repair re-measures a move in place and nothing revisited
the copies; after the 2026-09-14 repairs 93 of 205 dated strategies (3 of them
NULL) and 282 of 941 dated legs disagreed with their event. Dates are never
changed, and a row dated to an event with no move row is reported, not written.
Run it after any change to historical_moves prices.

Usage:
    2.0/venv/bin/python scripts/reenrich_journal_earnings.py                      # report only
    2.0/venv/bin/python scripts/reenrich_journal_earnings.py --execute            # fill gaps
    2.0/venv/bin/python scripts/reenrich_journal_earnings.py --refresh            # report stale moves
    2.0/venv/bin/python scripts/reenrich_journal_earnings.py --refresh --execute  # refresh them
"""

import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from journal.models import Trade  # noqa: E402
from journal.vrp_correlation import (  # noqa: E402
    find_nearest_earnings,
    load_historical_moves,
)

from common.db import connect, target  # noqa: E402


@dataclass
class LegFill:
    leg_id: int
    strategy_id: Optional[int]
    symbol: str
    earnings_date: str
    actual_move: float


@dataclass
class Disagreement:
    leg_id: int
    symbol: str
    stored: str
    matched: str


@dataclass
class StrategyFill:
    strategy_id: int
    symbol: str
    earnings_date: str
    actual_move: float


@dataclass
class Conflict:
    strategy_id: int
    symbol: str
    dates: List[str]


@dataclass
class Plan:
    legs: List[LegFill] = field(default_factory=list)
    strategies: List[StrategyFill] = field(default_factory=list)
    disagreements: List[Disagreement] = field(default_factory=list)
    conflicted_strategies: List[Conflict] = field(default_factory=list)
    unmatched: int = 0


def _as_trade(row) -> Trade:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return Trade(symbol=row.symbol, description="", quantity=0,
                 acquired_date=iso(row.acquired_date), sale_date=iso(row.sale_date),
                 cost_basis=0.0, proceeds=0.0, gain_loss=0.0, term="SHORT",
                 option_type=row.option_type, strike=row.strike)


def plan() -> Plan:
    """Work out every write without making one."""
    moves = load_historical_moves()
    out = Plan()
    with connect() as conn:
        legs = conn.execute("""
            SELECT id, strategy_id, symbol, acquired_date, sale_date, option_type, strike,
                   earnings_date, actual_move
            FROM trade_journal WHERE option_type IS NOT NULL ORDER BY id
        """).fetchall()
        open_strategies = conn.execute("""
            SELECT id, symbol FROM strategies WHERE earnings_date IS NULL ORDER BY id
        """).fetchall()

    # The date each leg will carry after the fill: stored if present, else matched.
    events_by_strategy = defaultdict(dict)
    for leg in legs:
        match = find_nearest_earnings(_as_trade(leg), moves)
        if leg.earnings_date is not None:
            stored = leg.earnings_date.isoformat()
            if match and match["earnings_date"] != stored:
                out.disagreements.append(
                    Disagreement(leg.id, leg.symbol, stored, match["earnings_date"]))
            if leg.strategy_id is not None:
                events_by_strategy[leg.strategy_id][stored] = leg.actual_move
            continue
        if not match:
            out.unmatched += 1
            continue
        out.legs.append(LegFill(leg.id, leg.strategy_id, leg.symbol,
                                match["earnings_date"], match["actual_move"]))
        if leg.strategy_id is not None:
            events_by_strategy[leg.strategy_id][match["earnings_date"]] = match["actual_move"]

    for strategy in open_strategies:
        events = events_by_strategy.get(strategy.id)
        if not events:
            continue
        if len(events) > 1:
            out.conflicted_strategies.append(
                Conflict(strategy.id, strategy.symbol, sorted(events)))
            continue
        (date, move), = events.items()
        out.strategies.append(StrategyFill(strategy.id, strategy.symbol, date, move))
    return out


def apply(p: Plan, execute: bool) -> Tuple[int, int]:
    """Write the plan when execute is True. Returns (legs, strategies) written."""
    if not execute:
        return 0, 0
    legs = strategies = 0
    with connect() as conn:
        with conn.transaction():
            for fill in p.legs:
                legs += conn.execute(
                    "UPDATE trade_journal SET earnings_date = %s, actual_move = %s"
                    " WHERE id = %s AND earnings_date IS NULL",
                    (fill.earnings_date, fill.actual_move, fill.leg_id)).rowcount
            for fill in p.strategies:
                strategies += conn.execute(
                    "UPDATE strategies SET earnings_date = %s, actual_move = %s"
                    " WHERE id = %s AND earnings_date IS NULL",
                    (fill.earnings_date, fill.actual_move, fill.strategy_id)).rowcount
    return legs, strategies


@dataclass(frozen=True)
class MoveRefresh:
    table: str                  # "strategies" or "trade_journal"
    row_id: int
    symbol: str
    earnings_date: date
    stored: Optional[float]
    current: float


@dataclass(frozen=True)
class MissingMove:
    table: str
    row_id: int
    symbol: str
    earnings_date: date


@dataclass
class RefreshPlan:
    changes: List[MoveRefresh] = field(default_factory=list)
    no_move_row: List[MissingMove] = field(default_factory=list)


# Float noise. The copies are the matcher's own float, or -- through /journal's
# CSV, which prints the move with one decimal (journal/reporting.py) -- that
# float rounded to a tenth.
_FLOAT_TOLERANCE = 1e-6
_ONE_DECIMAL_HALF_STEP = 0.05


def _is_current(stored: Optional[float], current: float) -> bool:
    if stored is None:
        return False
    if abs(stored - current) <= _FLOAT_TOLERANCE:
        return True
    one_decimal = abs(stored - round(stored, 1)) <= _FLOAT_TOLERANCE
    return one_decimal and abs(stored - current) <= _ONE_DECIMAL_HALF_STEP + _FLOAT_TOLERANCE


def refresh_plan() -> RefreshPlan:
    """Every dated row whose actual_move is not its event's current move. Writes nothing."""
    # The matcher's own definition of the move, over every stored event.
    current: Dict[Tuple[str, str], float] = {
        (ticker, m["earnings_date"]): m["actual_move"]
        for ticker, moves in load_historical_moves(min_date="1900-01-01").items()
        for m in moves
    }
    out = RefreshPlan()
    with connect() as conn:
        for table in ("strategies", "trade_journal"):
            rows = conn.execute(
                f"SELECT id, symbol, earnings_date, actual_move FROM {table}"
                " WHERE earnings_date IS NOT NULL ORDER BY earnings_date, symbol, id").fetchall()
            for r in rows:
                move = current.get((r.symbol, r.earnings_date.isoformat()))
                if move is None:
                    out.no_move_row.append(MissingMove(table, r.id, r.symbol, r.earnings_date))
                elif not _is_current(r.actual_move, move):
                    out.changes.append(MoveRefresh(table, r.id, r.symbol, r.earnings_date,
                                                   r.actual_move, move))
    return out


def apply_refresh(p: RefreshPlan, execute: bool) -> Tuple[int, int]:
    """Write the refresh when execute is True. Returns (legs, strategies) written.

    Each UPDATE is guarded on the date and move the plan read; if any row has
    changed since, nothing is written.
    """
    if not execute:
        return 0, 0
    written = Counter()
    with connect() as conn:
        with conn.transaction():
            for c in p.changes:
                written[c.table] += conn.execute(
                    f"UPDATE {c.table} SET actual_move = %s"
                    " WHERE id = %s AND earnings_date = %s AND actual_move IS NOT DISTINCT FROM %s",
                    (c.current, c.row_id, c.earnings_date, c.stored)).rowcount
            if sum(written.values()) != len(p.changes):
                raise RuntimeError(
                    f"{len(p.changes) - sum(written.values())} of {len(p.changes)} rows "
                    "changed since the plan was made; nothing written")
    return written["trade_journal"], written["strategies"]


def _main_refresh(execute: bool) -> int:
    p = refresh_plan()
    by_table = Counter(c.table for c in p.changes)
    print(f"Moves to refresh: {len(p.changes)} "
          f"({by_table['strategies']} strategies, {by_table['trade_journal']} legs)")
    print(f"Dated rows with no move row (not changed): {len(p.no_move_row)}")
    if p.changes:
        print("\nStale moves (table id symbol event: stored -> current):")
        for c in p.changes:
            stored = "NULL" if c.stored is None else f"{c.stored:.6f}"
            print(f"  {c.table:13} {c.row_id:5} {c.symbol:6} {c.earnings_date}: "
                  f"{stored} -> {c.current:.6f}")
    if p.no_move_row:
        print("\nNo move row:")
        for m in p.no_move_row:
            print(f"  {m.table:13} {m.row_id:5} {m.symbol:6} {m.earnings_date}")
    legs, strategies = apply_refresh(p, execute)
    if execute:
        print(f"\nRefreshed {legs} legs and {strategies} strategies.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--refresh", action="store_true",
                        help="instead of filling gaps, re-derive actual_move on dated rows"
                             " whose stored move no longer matches historical_moves")
    parser.add_argument("--execute", action="store_true",
                        help="write (default: report only)")
    args = parser.parse_args(argv)

    print(f"Database: {target()}")
    print(f"Mode: {'EXECUTE' if args.execute else 'REPORT ONLY (pass --execute to write)'}\n")
    if args.refresh:
        return _main_refresh(args.execute)

    p = plan()
    print(f"Legs to fill:            {len(p.legs)}")
    print(f"Legs with no event:      {p.unmatched}")
    print(f"Strategies to fill:      {len(p.strategies)}")
    print(f"Strategies left NULL because their legs name different events: "
          f"{len(p.conflicted_strategies)}")
    print(f"Stored leg dates the matcher disagrees with (not changed): {len(p.disagreements)}")

    print("\nLegs to fill, by symbol:")
    for symbol, n in sorted(Counter(f.symbol for f in p.legs).items()):
        print(f"  {symbol:6} {n}")
    if p.conflicted_strategies:
        print("\nConflicted strategies:")
        for c in p.conflicted_strategies:
            print(f"  id={c.strategy_id} {c.symbol}: {', '.join(c.dates)}")
    if p.disagreements:
        print("\nDisagreements (review by hand):")
        for d in p.disagreements:
            print(f"  leg id={d.leg_id} {d.symbol}: stored {d.stored}, matcher {d.matched}")

    legs, strategies = apply(p, args.execute)
    if args.execute:
        print(f"\nWrote {legs} legs and {strategies} strategies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
