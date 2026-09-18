"""Postgres persistence for TACO runs and positions.

Two things changed at the port beyond dialect:

- `db_path` is gone from every signature. The target comes from PG* in the
  environment; a path argument would have been ignored.
- `cursor.lastrowid` does not exist in psycopg. `RETURNING id` does, and is
  the correct way to get a generated identity back — `lastrowid` was always a
  DB-API extension SQLite happened to implement.

Types since migration 2 (2026-09-13): dates are `date`, timestamps are
`timestamptz`, `rule_compliant` is boolean, `contracts` is an integer and the
dollar columns are numeric(14,2). Rows come back with `date` objects, not
strings -- read them through `common.db.as_date`, which accepts either.
"""

from datetime import date
from typing import List

from common.db import as_date, session  # noqa: E402
from common.timezone import now_et  # noqa: E402

from .constants import MIN_DTE
from .market_data import EntryRefs

LOG_FIELDS = ("mode", "direction", "spot", "drawdown_pct", "runup_z", "vix",
              "vix_spike", "vix_term_ratio", "event_date", "event_type",
              "event_rationale", "score", "tier", "recommendation",
              "cross_asset_count", "cross_asset_available", "cross_asset_detail")


def log_run(**fields) -> int:
    unknown = set(fields) - set(LOG_FIELDS)
    if unknown:
        raise ValueError(f"unknown taco_log fields: {unknown}")
    cols = ["timestamp"] + list(fields)
    # now_et(), not datetime.now(): the column is timestamptz, and a naive
    # wall clock is parsed in the session zone (UTC), four hours off.
    vals = [now_et()] + list(fields.values())
    with session() as con:
        return con.execute(
            f"INSERT INTO taco_log ({','.join(cols)}) "
            f"VALUES ({','.join(['%s'] * len(cols))}) RETURNING id", vals
        ).fetchone()[0]


def _required_date(name: str, value) -> date:
    parsed = as_date(value)
    if parsed is None:
        raise ValueError(f"{name} must be a date or YYYY-MM-DD, got {value!r}")
    return parsed


def require_min_dte(entry_date, expiration) -> int:
    """The TACO DTE floor: expiration - entry_date >= MIN_DTE, or ValueError.

    The one place the rule is written. record_position enforces it on every
    write; the CLI calls it too, but only to refuse before spending a
    market-data download -- not as a second copy of the rule. Returns the DTE.
    """
    entry = _required_date("entry_date", entry_date)
    exp = _required_date("expiration", expiration)
    dte = (exp - entry).days
    if dte < MIN_DTE:
        raise ValueError(
            f"DTE {dte} is below TACO's hard minimum {MIN_DTE} (entry {entry}, "
            f"expiration {exp}) -- short-dated index trades are a different, "
            f"worse strategy")
    return dte


def record_position(*, direction: str, symbol: str,
                    contracts: int, strike: float, expiration,
                    premium_paid: float, entry_date, event_date,
                    refs: EntryRefs, rule_compliant: bool = True) -> int:
    """Insert an OPEN position. Dates may be `date` or YYYY-MM-DD strings.

    Raises ValueError, writing nothing, when the expiration is under MIN_DTE.
    """
    entry_date = _required_date("entry_date", entry_date)
    expiration = _required_date("expiration", expiration)
    event_date = _required_date("event_date", event_date)
    require_min_dte(entry_date, expiration)
    with session() as con:
        return con.execute(
            """INSERT INTO taco_positions
               (direction, symbol, contracts, strike, expiration,
                premium_paid, entry_date, event_date, pre_event_high,
                panic_low, euphoria_high, rip_base, pre_event_vix,
                status, rule_compliant, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,%s)
               RETURNING id""",
            (direction, symbol, contracts, strike, expiration, premium_paid,
             entry_date, event_date, refs.pre_event_high, refs.panic_low,
             refs.euphoria_high, refs.rip_base, refs.pre_event_vix,
             bool(rule_compliant), now_et())).fetchone()[0]


def close_position(position_id: int, exit_date: str, proceeds: float) -> float:
    with session() as con:
        row = con.execute(
            "SELECT premium_paid FROM taco_positions WHERE id = %s "
            "AND status = 'OPEN'", (position_id,)).fetchone()
        if row is None:
            raise ValueError(f"no OPEN taco position with id {position_id}")
        pnl = proceeds - row.premium_paid
        con.execute(
            """UPDATE taco_positions SET status='CLOSED', exit_date=%s,
               proceeds=%s, outcome_pnl=%s WHERE id=%s""",
            (exit_date, proceeds, pnl, position_id))
        return pnl


def open_positions() -> List[dict]:
    with session() as con:
        # The columns /taco positions reads; the exit fields are empty while OPEN
        rows = con.execute(
            "SELECT id, direction, symbol, contracts, strike, expiration,"
            " premium_paid, entry_date, event_date, pre_event_high, panic_low,"
            " euphoria_high, rip_base, pre_event_vix, rule_compliant"
            " FROM taco_positions WHERE status='OPEN' "
            "ORDER BY entry_date").fetchall()
        return [r._asdict() for r in rows]
