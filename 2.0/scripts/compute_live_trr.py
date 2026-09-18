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
thresholds and the same `< 4 quarters` -> unknown guard, both imported from
`src/domain/tail_risk.py` (the ad hoc SQL versions silently reported unknown
as LOW instead).

Usage:
    ./venv/bin/python scripts/compute_live_trr.py TICKER [TICKER ...]
"""

import argparse
import sys

from common.db import connect  # noqa: E402
from src.domain.tail_risk import (  # noqa: E402
    TRR_LOOKBACK_QUARTERS,
    tail_risk_ratio,
    trr_level,
)


def compute_trr(tickers: list[str], limit: int = TRR_LOOKBACK_QUARTERS) -> dict[str, dict]:
    """
    Compute live TRR for each ticker.

    Returns a dict keyed by every requested ticker (even ones absent from
    historical_moves — never silently dropped), each mapping to:
      quarters: int, number of gap_move_pct rows used (<=limit)
      max_move: float | None
      avg_move: float | None
      trr: float | None
      trr_level: 'HIGH' | 'NORMAL' | 'LOW' | None (None = unknown, <TRR_MIN_QUARTERS)
    """
    conn = connect()
    try:
        # One statement for every ticker: the `limit` most recent quarters each
        # ((ticker, earnings_date) is unique, so the window is deterministic).
        rows = conn.execute(
            """
            SELECT ticker, gap_move_pct FROM (
                SELECT ticker, gap_move_pct,
                       row_number() OVER (PARTITION BY ticker ORDER BY earnings_date DESC) AS n
                FROM historical_moves
                WHERE ticker = ANY(%s) AND gap_move_pct IS NOT NULL
            ) recent
            WHERE n <= %s
            """,
            (list(tickers), limit),
        ).fetchall()
    finally:
        conn.close()

    moves_by_ticker: dict[str, list[float]] = {ticker: [] for ticker in tickers}
    for ticker, gap_move_pct in rows:
        moves_by_ticker[ticker].append(abs(gap_move_pct))

    results = {}
    for ticker, gap_moves in moves_by_ticker.items():
        trr = tail_risk_ratio(gap_moves)
        known = trr is not None
        results[ticker] = {
            "quarters": len(gap_moves),
            "max_move": max(gap_moves) if known else None,
            "avg_move": sum(gap_moves) / len(gap_moves) if known else None,
            "trr": trr,
            "trr_level": trr_level(trr),
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+", help="Ticker symbols")
    # No --db flag: the target comes from PG* in the environment
    # (`PGDATABASE=ivcrush_scratch ...`). A path argument would be a
    # lie -- connect() would ignore it.
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers]
    results = compute_trr(tickers)

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
