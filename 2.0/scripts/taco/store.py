"""SQLite persistence for TACO runs and positions (ivcrush.db)."""

import sqlite3
from datetime import datetime
from typing import List

from .market_data import EntryRefs

LOG_FIELDS = ("mode", "direction", "spot", "drawdown_pct", "runup_z", "vix",
              "vix_spike", "vix_term_ratio", "event_date", "event_type",
              "event_rationale", "score", "tier", "recommendation",
              "cross_asset_count", "cross_asset_available", "cross_asset_detail")


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def log_run(db_path: str, **fields) -> int:
    unknown = set(fields) - set(LOG_FIELDS)
    if unknown:
        raise ValueError(f"unknown taco_log fields: {unknown}")
    cols = ["timestamp"] + list(fields)
    vals = [datetime.now().isoformat()] + list(fields.values())
    with _connect(db_path) as con:
        cur = con.execute(
            f"INSERT INTO taco_log ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})", vals)
        return cur.lastrowid


def record_position(db_path: str, *, direction: str, symbol: str,
                    contracts: float, strike: float, expiration: str,
                    premium_paid: float, entry_date: str, event_date: str,
                    refs: EntryRefs, rule_compliant: int = 1) -> int:
    with _connect(db_path) as con:
        cur = con.execute(
            """INSERT INTO taco_positions
               (direction, symbol, contracts, strike, expiration,
                premium_paid, entry_date, event_date, pre_event_high,
                panic_low, euphoria_high, rip_base, pre_event_vix,
                status, rule_compliant, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?)""",
            (direction, symbol, contracts, strike, expiration, premium_paid,
             entry_date, event_date, refs.pre_event_high, refs.panic_low,
             refs.euphoria_high, refs.rip_base, refs.pre_event_vix,
             rule_compliant, datetime.now().isoformat()))
        return cur.lastrowid


def close_position(db_path: str, position_id: int, exit_date: str,
                   proceeds: float) -> float:
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT premium_paid FROM taco_positions WHERE id = ? "
            "AND status = 'OPEN'", (position_id,)).fetchone()
        if row is None:
            raise ValueError(f"no OPEN taco position with id {position_id}")
        pnl = proceeds - row["premium_paid"]
        con.execute(
            """UPDATE taco_positions SET status='CLOSED', exit_date=?,
               proceeds=?, outcome_pnl=? WHERE id=?""",
            (exit_date, proceeds, pnl, position_id))
        return pnl


def open_positions(db_path: str) -> List[dict]:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT * FROM taco_positions WHERE status='OPEN' "
            "ORDER BY entry_date").fetchall()
        return [dict(r) for r in rows]
