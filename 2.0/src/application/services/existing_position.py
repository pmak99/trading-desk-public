"""One position per ticker per earnings event, checked on every path.

CLAUDE.md: "One position per ticker per earnings event. No second bet, no repair."
A repair campaign on record was two positions on one event; the repair, not the first
loss, made it catastrophic.

/analyze, scan, whisper and both Telegram commands call existing_position_warning().
Until 2026-09-14 the check was TickerAnalyzer._check_existing_position and ran only
for /analyze with --strategies, so every other surface showed a second bet on an
already-traded event with no warning.

⚠️ It reads the `positions` view over `strategies`, which /journal fills from Fidelity
exports after a position closes. It sees a closed, journaled trade on the event and
nothing else: an open position, or one closed and not yet journaled, is invisible, and
the warning says so. No live open-position tracker exists (CLAUDE.md data caveats).
A warning, never a block: a trade may have been closed and re-entered on purpose.

The count is of trades: one placed in both the IRA and TAXABLE is one prior position,
its size the two accounts' together (until 2026-09-15 it read `strategies` and said
"2 prior position(s)" for PLTR, DDOG, WDAY, COF, GOOG and NVDA's mirrored spreads).
"""

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_QUERY = (
    "SELECT strategy_type, quantity, acquired_date, campaign_id, count(*) OVER () AS n "
    "FROM positions WHERE symbol = %s AND earnings_date = %s "
    "ORDER BY acquired_date DESC, id DESC LIMIT 1"
)


def existing_position_warning(pool, ticker: str, earnings_date: date) -> Optional[str]:
    """The warning text when `positions` already holds this ticker on this event, else None.

    `pool` is a psycopg ConnectionPool (container.db_pool). A failed query logs a
    WARNING with its traceback and returns None: at DEBUG a broken guard was
    indistinguishable from "no prior position".
    """
    try:
        with pool.connection() as conn:
            row = conn.execute(_QUERY, (ticker, earnings_date)).fetchone()
    except Exception:
        logger.warning(
            f"{ticker}: existing-position check failed — the one-position-per-event "
            f"guard did not run",
            exc_info=True,
        )
        return None

    if row is None:
        return None

    # Attribute access (namedtuple_row: row['x'] raises), and quantity is nullable.
    quantity = f" x{row.quantity:g}" if row.quantity is not None else ""
    detail = (
        f"{row.n} prior position(s) already exist for {ticker} on "
        f"{earnings_date.isoformat()} — most recent {row.strategy_type}{quantity}, "
        f"acquired {row.acquired_date}"
    )
    if row.campaign_id:
        detail += f", campaign {row.campaign_id}"
    detail += (
        ". One position per event: no second bet, no repair. Only closed, journaled "
        "trades are visible here; check the broker for an open one."
    )
    logger.warning(f"{ticker}: {detail}")
    return detail
