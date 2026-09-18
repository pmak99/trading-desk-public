#!/usr/bin/env python3
"""
Set strategies.trr_at_entry to the TRR the engine would have shown at entry.

`/journal` Step 7 runs this after an import. It replaced an inline block that
took the last 8 *close* moves at import time -- after the trade had closed, so
the event being traded was among them -- where the engine sizes on the last 12
*gap* moves. With the frozen ORATS snapshot copied into other rows, the column
held three definitions; on 2026-09-13, 83 of 181 earnings trades sat in a
different tier than the engine would have shown.

TRR here is `src/domain/tail_risk.trr_as_of` over gap moves strictly before the
entry date (an AMC entry is made before that day's report), rounded to 3
places. Fewer than 2 prior quarters is unknown and stored as NULL.

Fills NULLs only, unless --all, which recomputes every row and reports each
change. Report-only unless --execute.

Usage (from the repo root):
    2.0/venv/bin/python 2.0/scripts/backfill_trr_at_entry.py                  # report
    2.0/venv/bin/python 2.0/scripts/backfill_trr_at_entry.py --execute        # fill NULLs
    2.0/venv/bin/python 2.0/scripts/backfill_trr_at_entry.py --all            # report every change
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from common.db import as_date, connect, target  # noqa: E402
from src.domain.tail_risk import trr_as_of, trr_level  # noqa: E402

TOLERANCE = 0.0005   # stored values are rounded to 3 places


@dataclass(frozen=True)
class Change:
    strategy_id: int
    symbol: str
    acquired_date: date
    stored: Optional[float]
    computed: Optional[float]


def computed_trr(history: Sequence[Tuple[date, float]], entry: date) -> Optional[float]:
    trr = trr_as_of(history, entry)
    return round(trr, 3) if trr is not None else None


def _differs(stored: Optional[float], computed: Optional[float]) -> bool:
    if stored is None or computed is None:
        return stored is not computed
    return abs(stored - computed) > TOLERANCE


def plan(conn, recompute_all: bool = False) -> List[Change]:
    history: Dict[str, List[Tuple[date, float]]] = defaultdict(list)
    for r in conn.execute("SELECT ticker, earnings_date, gap_move_pct FROM historical_moves"):
        history[r.ticker].append((as_date(r.earnings_date), r.gap_move_pct))
    sql = "SELECT id, symbol, acquired_date, trr_at_entry FROM strategies"
    if not recompute_all:
        sql += " WHERE trr_at_entry IS NULL"
    changes = []
    for r in conn.execute(sql + " ORDER BY acquired_date, id"):
        entry = as_date(r.acquired_date)
        computed = computed_trr(history.get(r.symbol, []), entry)
        if _differs(r.trr_at_entry, computed):
            changes.append(Change(r.id, r.symbol, entry, r.trr_at_entry, computed))
    return changes


def apply(conn, changes: Sequence[Change]) -> int:
    written = 0
    with conn.transaction():
        for c in changes:
            written += conn.execute(
                "UPDATE strategies SET trr_at_entry = %s WHERE id = %s",
                (c.computed, c.strategy_id)).rowcount
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--all", action="store_true",
                        help="recompute every row, not only NULLs")
    parser.add_argument("--execute", action="store_true", help="write the changes")
    args = parser.parse_args(argv)

    with connect() as conn:
        changes = plan(conn, recompute_all=args.all)
        tier_moves = sum((trr_level(c.stored) or "UNKNOWN") != (trr_level(c.computed) or "UNKNOWN")
                         for c in changes)
        print(f"db: {target()}   scope: {'every strategy' if args.all else 'NULL trr_at_entry'}")
        print(f"rows to change: {len(changes)}   of which change tier: {tier_moves}")
        for c in changes:
            print(f"  {c.strategy_id:5} {c.symbol:6} {c.acquired_date}  "
                  f"{c.stored if c.stored is not None else 'NULL':>8} -> "
                  f"{c.computed if c.computed is not None else 'NULL':<8} "
                  f"{trr_level(c.stored) or 'UNKNOWN'} -> {trr_level(c.computed) or 'UNKNOWN'}")
        if not args.execute:
            print("report only: nothing written (pass --execute to write)")
            return 0
        print(f"written: {apply(conn, changes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
