#!/usr/bin/env python3
"""Recompute `risk_metrics_snapshot` — trailing Sharpe/Sortino for the book.

Salvaged from `grafana_snapshot.py`'s `add_risk_metrics()`, which goes away
with the SQLite snapshot pipeline. The snapshot existed because Grafana read a
disposable SQLite copy; Grafana reads Postgres directly now, but this table
still has to exist, for the reason it always did:

**Sharpe and Sortino are not expressible as a SQL panel.** They need
`net_campaigns()`'s netting rule — each option leg claimed by at most one
overlapping single-name stock campaign — which is not a simple aggregate. A
SQL reimplementation would be a second, untested copy of correctness-critical
logic: precisely the gap `docs/SHARPE_ANALYSIS_2026-09.md` was written to
close. So it is computed in Python, weekly, and Grafana does a plain SELECT.

Run from the repo root:

    2.0/venv/bin/python -m scripts.refresh_risk_metrics
"""

import logging
import sys
from pathlib import Path

# 2.0/scripts, for the bare `import bootstrap_sharpe` below; `common` is installed.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "2.0" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from common.db import session, target  # noqa: E402

logger = logging.getLogger(__name__)

# Trailing windows, in months. 1 is deliberately included even though a single
# month can rarely support a meaningful ratio: the panel shows the shape of the
# series, and a NULL is more honest than a hidden window.
RISK_WINDOWS = (1, 3, 6, 12)


def refresh_risk_metrics(conn) -> int:
    """Recompute every window. Returns the number of rows written.

    Always writes one row per window, even where a window has too little data
    for a ratio (sharpe/sortino NULL, n_months possibly 0) — so the panel never
    returns zero rows purely because a window has no trades yet.
    """
    import bootstrap_sharpe as bs

    rows = bs.load_positions(conn)
    units = bs.net_campaigns(rows)
    monthly = bs.monthly_series(units)

    # DELETE, not DROP+CREATE: the table is part of the baseline schema now,
    # not something a snapshot build materialises. Truncating inside the
    # transaction keeps a reader from ever seeing it empty.
    with conn.cursor() as cur:
        cur.execute("DELETE FROM risk_metrics_snapshot")
        for months in RISK_WINDOWS:
            series = bs.trailing_window_series(monthly, months)
            cur.execute(
                "INSERT INTO risk_metrics_snapshot"
                " (window_months, sharpe, sortino, n_months)"
                " VALUES (%s, %s, %s, %s)",
                (months, bs.sharpe_ratio(series), bs.sortino_ratio(series),
                 len(series)),
            )
    return len(RISK_WINDOWS)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with session() as conn:
        n = refresh_risk_metrics(conn)
    logger.info(f"refreshed {n} risk-metric window(s) on {target()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
