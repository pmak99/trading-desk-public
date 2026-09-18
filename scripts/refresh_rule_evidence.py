#!/usr/bin/env python3
"""Recompute `rule_evidence_snapshot` — the band tallies four panels read.

Four dashboard panels report the band tables CLAUDE.md prints, and
`2.0/scripts/rule_evidence.py` already computes all four. Two of them **cannot
be expressed as a SQL panel**: exit timing needs `is_trading_day`
(`src/domain/market_calendar.py`) and TRR needs `tail_risk.py`'s ladder, which
`trading_trr_at_entry` duplicated as SQL literals while filtering away the 65
of 300 positions whose TRR is UNKNOWN — a named tier in §3, not an absence.
A SQL reimplementation is a second, untested copy of correctness-critical
logic. Same argument, same answer as `refresh_risk_metrics.py`: computed in
Python, weekly, and Grafana does a plain SELECT.

Two cohorts per dimension where the dashboard needs both. `earnings` is
`rule_evidence`'s own scope, what CLAUDE.md's tables report. `all` is the wider
population `trading_trr_at_entry` and `trading_contract_buckets` have always
shown and say so in their descriptions — publishing only `earnings` would
silently narrow them by 48 and 88 positions.

Run from the repo root:

    2.0/venv/bin/python -m scripts.refresh_rule_evidence
"""

import logging
import sys
from pathlib import Path

# 2.0/scripts, for the bare `import rule_evidence` below; `common` is installed.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "2.0" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from common.db import session, target  # noqa: E402

logger = logging.getLogger(__name__)

INSERT = (
    "INSERT INTO rule_evidence_snapshot (dimension, cohort, band, band_order,"
    " trades, wins, pnl, avg_loss, median_quantity)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def published_rows(trades, history, legs):
    """(dimension, cohort, band, band_order, Tally) for every band published.

    Pure: takes the loaded book and returns tuples, so the tallies can be
    verified against CLAUDE.md without writing anything.
    """
    import rule_evidence as re_

    ev = re_.compute(trades, history, legs)
    earnings = [t for t in trades if re_.is_earnings_trade(t)]
    options = [t for t in trades
               if t.strategy_type != "STOCK" and t.quantity is not None]
    spreads = [t for t in earnings if t.strategy_type == "SPREAD"]
    singles = [t for t in earnings if t.strategy_type == "SINGLE"]

    out = []

    def emit(dimension, cohort, bands, tallies):
        for order, band in enumerate(bands):
            out.append((dimension, cohort, band, order, tallies[band]))

    # The earnings cohorts come straight from compute(), so the dashboard and
    # CLAUDE.md cannot disagree about what a band means or which trades it holds.
    emit("trr", "earnings", re_.TRR_TIERS, ev["trr"])
    emit("dte", "earnings", re_.DTE_BANDS, ev["dte"])
    emit("size", "earnings", re_.SIZE_BANDS, ev["size"])
    emit("held", "SPREAD", re_.HELD_BANDS,
         re_._group(spreads, re_.held_band, re_.HELD_BANDS))
    emit("held", "SINGLE", re_.HELD_BANDS,
         re_._group(singles, re_.held_band, re_.HELD_BANDS))

    # The `all` cohorts apply the SAME band functions to the wider population
    # the two existing panels cover. STOCK is left out of size because its
    # quantity is shares, matching the panel it replaces. There is no `dte`
    # all-cohort: STOCK has no expiration, and no panel asks for one.
    emit("trr", "all", re_.TRR_TIERS,
         re_._group(trades, lambda t: re_.trr_tier_at_entry(t, history),
                    re_.TRR_TIERS))
    emit("size", "all", re_.SIZE_BANDS,
         re_._group(options, lambda t: re_.size_band(t.quantity), re_.SIZE_BANDS))
    return out


def refresh_rule_evidence(conn) -> int:
    """Recompute every band from the book and replace the table's contents."""
    import rule_evidence as re_

    trades = re_.load_book(conn, None)
    rows = published_rows(trades, re_.load_gap_moves(conn), re_.load_legs(conn))

    # DELETE, not DROP+CREATE: the table is part of the baseline schema, not
    # something a snapshot build materialises. Truncating inside the
    # transaction keeps a reader from ever seeing it empty.
    with conn.cursor() as cur:
        cur.execute("DELETE FROM rule_evidence_snapshot")
        for dimension, cohort, band, order, tally in rows:
            cur.execute(INSERT, (dimension, cohort, band, order, tally.n,
                                 tally.wins, tally.pnl, tally.avg_loss,
                                 tally.median_quantity))
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with session() as conn:
        n = refresh_rule_evidence(conn)
    logger.info(f"refreshed {n} rule-evidence band(s) on {target()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
