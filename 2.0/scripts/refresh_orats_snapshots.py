#!/usr/bin/env python3
"""
Refresh ORATS snapshot data in position_limits.

Fetches live values from /datav2/cores and /datav2/ivrank for all tickers
in position_limits (or a specified subset) and writes:
  - fcst_ern_iv_effect      ORATS model forecast of ern_iv_effect for NEXT quarter
  - orats_implied_ern_mv    ORATS own implied earnings move forecast
  - iv30d                   Current 30-day ATM IV (annualized %)
  - hv20d                   Current 20-day close-to-close HV
  - abs_avg_ern_mv          ORATS historical avg absolute earnings move
  - iv_rank_1y              52-week IV rank (0-100)
  - iv_pct_1y               52-week IV percentile (0-100)

Run weekly or before major scan sessions.

Usage:
    source 2.0/.env
    python scripts/refresh_orats_snapshots.py [--dry-run] [--tickers AAPL,MSFT]
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

sys_path_root = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, sys_path_root)

from common.constants import ORATS_ENABLED  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

ORATS_BASE_URL = "https://api.orats.io/datav2"
BATCH_SIZE = 20
BATCH_DELAY_SECONDS = 0.5

_CORES_FIELDS = {
    "fcst_ern_iv_effect": "fcstErnEffct",
    "orats_implied_ern_mv": "impliedEarningsMove",
    "iv30d": "iv30d",
    "hv20d": "clsHv20d",
    "abs_avg_ern_mv": "absAvgErnMv",
}

_SUMMARIES_FIELDS = {
    "iee_earn_effect": "ieeEarnEffect",
    "r_slp_30":        "rSlp30",
}

_ALLOWED_COLS = frozenset(_CORES_FIELDS.keys()) | {"iv_rank_1y", "iv_pct_1y"} | frozenset(_SUMMARIES_FIELDS.keys())


def fetch_tickers_from_db(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT ticker FROM position_limits ORDER BY ticker")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def fetch_cores_batch(api_key: str, tickers: list[str]) -> dict[str, dict]:
    """Call /datav2/cores. Returns {ticker: {field: value}}."""
    if api_key == "__dry_run__":
        logger.info(f"  [dry-run] would fetch cores: {', '.join(tickers)}")
        return {}

    url = f"{ORATS_BASE_URL}/cores"
    try:
        resp = requests.get(url, params={"token": api_key, "tickers": ",".join(tickers)}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ORATS cores error for batch starting {tickers[0]}: {e}")
        return {}

    data = resp.json()
    if not isinstance(data, dict) or "data" not in data:
        logger.warning(f"Unexpected cores response: {list(data.keys())[:5]}")
        return {}

    result = {}
    for record in data["data"]:
        ticker = record.get("ticker")
        if not ticker:
            continue
        row: dict = {}
        for db_col, orats_key in _CORES_FIELDS.items():
            val = record.get(orats_key)
            if val is not None:
                try:
                    row[db_col] = float(val)
                except (TypeError, ValueError):
                    pass
        if row:
            result[ticker] = row

    return result


def fetch_ivrank_batch(api_key: str, tickers: list[str]) -> dict[str, dict]:
    """Call /datav2/ivrank. Returns {ticker: {iv_rank_1y, iv_pct_1y}}."""
    if api_key == "__dry_run__":
        logger.info(f"  [dry-run] would fetch ivrank: {', '.join(tickers)}")
        return {}

    url = f"{ORATS_BASE_URL}/ivrank"
    try:
        resp = requests.get(url, params={"token": api_key, "tickers": ",".join(tickers)}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ORATS ivrank error for batch starting {tickers[0]}: {e}")
        return {}

    data = resp.json()
    if not isinstance(data, dict) or "data" not in data:
        return {}

    result = {}
    for record in data["data"]:
        ticker = record.get("ticker")
        if not ticker:
            continue
        row: dict = {}
        for orats_key, db_col in [("ivRank1y", "iv_rank_1y"), ("ivPct1y", "iv_pct_1y")]:
            val = record.get(orats_key)
            if val is not None:
                try:
                    row[db_col] = float(val)
                except (TypeError, ValueError):
                    pass
        if row:
            result[ticker] = row
    return result


def fetch_summaries_batch(api_key: str, tickers: list[str]) -> dict[str, dict]:
    """Call /datav2/summaries. Returns {ticker: {iee_earn_effect, r_slp_30}}."""
    if api_key == "__dry_run__":
        logger.info(f"  [dry-run] would fetch summaries: {', '.join(tickers)}")
        return {}

    url = f"{ORATS_BASE_URL}/summaries"
    try:
        resp = requests.get(url, params={"token": api_key, "tickers": ",".join(tickers)}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ORATS summaries error for batch starting {tickers[0]}: {e}")
        return {}

    data = resp.json()
    if not isinstance(data, dict) or "data" not in data:
        return {}

    result = {}
    for record in data["data"]:
        ticker = record.get("ticker")
        if not ticker:
            continue
        row: dict = {}
        for db_col, orats_key in _SUMMARIES_FIELDS.items():
            val = record.get(orats_key)
            if val is not None:
                try:
                    row[db_col] = float(val)
                except (TypeError, ValueError):
                    pass
        if row:
            result[ticker] = row
    return result


def update_db(db_path: str, ticker_data: dict[str, dict], dry_run: bool) -> tuple[int, int]:
    """Write snapshot data to position_limits. Returns (matched, updated)."""
    conn = sqlite3.connect(db_path)
    matched = updated = 0
    try:
        for ticker, fields in ticker_data.items():
            cursor = conn.execute("SELECT ticker FROM position_limits WHERE ticker=?", (ticker,))
            if not cursor.fetchone():
                continue
            matched += 1
            safe = {k: v for k, v in fields.items() if k in _ALLOWED_COLS}
            if not safe:
                continue
            if not dry_run:
                set_parts = ", ".join(f"[{col}]=?" for col in safe)
                conn.execute(
                    f"UPDATE position_limits SET {set_parts}, last_updated=CURRENT_TIMESTAMP "
                    f"WHERE ticker=?",
                    list(safe.values()) + [ticker],
                )
                updated += 1
            else:
                logger.info(f"DRY RUN: would update {ticker}: {list(safe.keys())}")
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return matched, updated


def run_batches(api_key: str, db_path: str, tickers: list[str], dry_run: bool):
    batches = [tickers[i: i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    total_matched = total_updated = 0

    for idx, batch in enumerate(batches, 1):
        logger.info(f"Batch {idx}/{len(batches)}: {', '.join(batch)}")

        # Merge cores + ivrank + summaries data for the batch
        cores = fetch_cores_batch(api_key, batch)
        ivrank = fetch_ivrank_batch(api_key, batch)
        summaries = fetch_summaries_batch(api_key, batch)

        merged: dict[str, dict] = {}
        for t in batch:
            row = {**cores.get(t, {}), **ivrank.get(t, {}), **summaries.get(t, {})}
            if row:
                merged[t] = row

        if merged:
            m, u = update_db(db_path, merged, dry_run)
            total_matched += m
            total_updated += u
            logger.info(f"  {m} matched, {u} updated")
        else:
            logger.warning(f"  No data returned for batch {idx}")

        if idx < len(batches):
            time.sleep(BATCH_DELAY_SECONDS)

    return total_matched, total_updated


def main():
    parser = argparse.ArgumentParser(description="Refresh ORATS snapshot data in position_limits")
    parser.add_argument("--db-path", default="data/ivcrush.db")
    parser.add_argument("--tickers", help="Comma-separated tickers (default: all in position_limits)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not ORATS_ENABLED:
        logger.info("ORATS disabled (ORATS_ENABLED=false). Skipping refresh.")
        return 0

    api_key = os.environ.get("ORATS_API_KEY")
    if not api_key:
        if args.dry_run:
            logger.warning("ORATS_API_KEY not set — dry-run will show plan only")
            api_key = "__dry_run__"
        else:
            logger.error("ORATS_API_KEY not set. Run: export ORATS_API_KEY=your_key")
            return 1

    db_path = args.db_path
    if not Path(db_path).exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else fetch_tickers_from_db(db_path)
    )

    dry_tag = "  [DRY RUN]" if args.dry_run else ""
    logger.info(f"Refreshing ORATS snapshots for {len(tickers)} tickers{dry_tag}")
    logger.info(f"Database: {db_path}")
    logger.info(f"Fields: {', '.join(_ALLOWED_COLS)}")

    matched, updated = run_batches(api_key, db_path, tickers, args.dry_run)

    dry = "  (dry run — no writes)" if args.dry_run else ""
    logger.info(f"\nDone{dry}. {matched} tickers matched, {updated} rows updated in position_limits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
