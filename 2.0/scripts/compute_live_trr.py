#!/usr/bin/env python3
"""
Compute live Tail Risk Ratio (TRR) for one or more tickers, matching the
live engine exactly.

`position_limits.tail_risk_ratio`/`tail_risk_level` are an ORATS-era
snapshot frozen at 2026-06-24 that the live engine never reads for sizing —
`analyzer.py`'s `_compute_tail_risk_level()` recomputes TRR fresh from
`historical_moves` on every `/analyze` run. An Aug 25 2026 audit found 6 of
14 sampled tickers had the wrong TRR tier between the frozen snapshot and a
live recompute (ADSK/DLTR/MRVL/S/ULTA/WDAY: frozen-HIGH vs live-NORMAL).

Ten `.claude/commands/*.md` files each carried their own copy of this SQL.
This module is the one place that logic lives now — same source
(`historical_moves.gap_move_pct`, not intraday), same window (12 most
recent quarters by `earnings_date DESC`, matching
`prices_repository.get_historical_moves`'s default `limit=12`), same
thresholds (`trr > 2.5` -> HIGH, `trr >= 1.5` -> NORMAL, else LOW), and the
same `< 2 quarters` -> unknown guard `analyzer._compute_tail_risk_level`
applies (the ad hoc SQL versions silently reported these as LOW instead).

Usage:
    ./venv/bin/python scripts/compute_live_trr.py TICKER [TICKER ...]
    ./venv/bin/python scripts/compute_live_trr.py --db data/ivcrush.db NVDA AAPL MU
"""

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = str(Path(__file__).parent.parent / "data" / "ivcrush.db")


def compute_trr(db_path: str, tickers: list[str], limit: int = 12) -> dict[str, dict]:
    """
    Compute live TRR for each ticker.

    Returns a dict keyed by every requested ticker (even ones absent from
    historical_moves — never silently dropped), each mapping to:
      quarters: int, number of gap_move_pct rows used (<=limit)
      max_move: float | None
      avg_move: float | None
      trr: float | None
      trr_level: 'HIGH' | 'NORMAL' | 'LOW' | None (None = unknown, <2 quarters)
    """
    conn = sqlite3.connect(db_path)
    try:
        results = {}
        for ticker in tickers:
            rows = conn.execute(
                """
                SELECT gap_move_pct FROM (
                    SELECT gap_move_pct, earnings_date
                    FROM historical_moves
                    WHERE ticker = ? AND gap_move_pct IS NOT NULL
                    ORDER BY earnings_date DESC
                    LIMIT ?
                )
                """,
                (ticker, limit),
            ).fetchall()
            gap_moves = [abs(r[0]) for r in rows]

            if len(gap_moves) < 2:
                results[ticker] = {
                    "quarters": len(gap_moves),
                    "max_move": None,
                    "avg_move": None,
                    "trr": None,
                    "trr_level": None,
                }
                continue

            avg_move = sum(gap_moves) / len(gap_moves)
            max_move = max(gap_moves)
            trr = max_move / avg_move if avg_move > 0 else 0
            if trr > 2.5:
                trr_level = "HIGH"
            elif trr >= 1.5:
                trr_level = "NORMAL"
            else:
                trr_level = "LOW"

            results[ticker] = {
                "quarters": len(gap_moves),
                "max_move": max_move,
                "avg_move": avg_move,
                "trr": trr,
                "trr_level": trr_level,
            }
        return results
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+", help="Ticker symbols")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to ivcrush.db")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers]
    results = compute_trr(args.db, tickers)

    ordered = sorted(
        results.items(),
        key=lambda kv: (kv[1]["trr"] is None, -(kv[1]["trr"] or 0)),
    )

    print(f"{'TICKER':<8} {'QUARTERS':>8} {'MAX':>7} {'AVG':>7} {'TRR':>6} {'LEVEL':>8}")
    for ticker, r in ordered:
        if r["trr_level"] is None:
            print(f"{ticker:<8} {r['quarters']:>8} {'--':>7} {'--':>7} {'--':>6} {'UNKNOWN':>8}")
        else:
            print(
                f"{ticker:<8} {r['quarters']:>8} {r['max_move']:>7.2f} "
                f"{r['avg_move']:>7.2f} {r['trr']:>6.2f} {r['trr_level']:>8}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
