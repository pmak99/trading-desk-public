#!/usr/bin/env python3
"""
One-time ORATS backfill: populate position_limits with IV rank/percentile snapshot.

ORATS /datav2/ivrank returns current 52-week IV rank and percentile per ticker.
These are stored as a snapshot in position_limits — useful as a real-time filter
alongside VRP: high IV rank = structurally elevated vol, not just an earnings bump.

Usage:
    source 2.0/.env
    python scripts/backfill_orats_ivrank.py [--dry-run] [--tickers AAPL,MSFT]

Fields populated in position_limits:
    iv_rank_1y   — 52-week IV rank (0–100); 50 = median of past year
    iv_pct_1y    — 52-week IV percentile (0–100)
"""

import argparse
import logging
import os
import sqlite3
import time
from pathlib import Path

import requests

sys_path_root = str(Path(__file__).parent.parent)
import sys
sys.path.insert(0, sys_path_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

ORATS_BASE_URL = "https://api.orats.io/datav2"
BATCH_SIZE = 20
BATCH_DELAY_SECONDS = 0.5


def fetch_tickers_from_db(db_path: str) -> list[str]:
    """Return distinct tickers that have position_limits rows (our active universe)."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT DISTINCT ticker FROM position_limits ORDER BY ticker"
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def fetch_orats_ivrank_batch(api_key: str, tickers: list[str]) -> dict:
    """
    Call ORATS /datav2/ivrank for a batch of tickers.

    Returns dict keyed by ticker: {'iv_rank_1y': float, 'iv_pct_1y': float}
    """
    if api_key == "__dry_run_no_key__":
        logger.info(f"  [no key] would fetch: {', '.join(tickers)}")
        return {}

    ticker_str = ",".join(tickers)
    url = f"{ORATS_BASE_URL}/ivrank"
    params = {"token": api_key, "tickers": ticker_str}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ORATS API error for batch {tickers[:3]}...: {e}")
        return {}

    data = resp.json()
    if not isinstance(data, dict) or "data" not in data:
        logger.warning(f"Unexpected ORATS response shape: {list(data.keys())[:5]}")
        return {}

    result = {}
    for record in data["data"]:
        ticker = record.get("ticker")
        if not ticker:
            continue
        iv_rank = record.get("ivRank1y")
        iv_pct = record.get("ivPct1y")
        if iv_rank is not None and iv_pct is not None:
            result[ticker] = {
                "iv_rank_1y": float(iv_rank),
                "iv_pct_1y": float(iv_pct),
            }

    return result


def update_db(
    db_path: str,
    ticker_data: dict,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    UPDATE position_limits.iv_rank_1y and iv_pct_1y for matched tickers.

    Returns (rows_matched, rows_updated).
    """
    conn = sqlite3.connect(db_path)
    matched = 0
    updated = 0

    try:
        for ticker, vals in ticker_data.items():
            cursor = conn.execute(
                "SELECT ticker FROM position_limits WHERE ticker=?", (ticker,)
            )
            if not cursor.fetchone():
                continue
            matched += 1
            if not dry_run:
                conn.execute(
                    "UPDATE position_limits SET iv_rank_1y=?, iv_pct_1y=? WHERE ticker=?",
                    (vals["iv_rank_1y"], vals["iv_pct_1y"], ticker),
                )
                updated += 1
            else:
                logger.debug(
                    f"DRY RUN: would set {ticker} iv_rank_1y={vals['iv_rank_1y']:.1f}"
                    f" iv_pct_1y={vals['iv_pct_1y']:.1f}"
                )
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return matched, updated


def main():
    parser = argparse.ArgumentParser(description="Backfill ORATS IV rank into position_limits")
    parser.add_argument("--db-path", default="data/ivcrush.db")
    parser.add_argument("--tickers", help="Comma-separated list (default: all in position_limits)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("ORATS_API_KEY")
    if not api_key:
        if args.dry_run:
            logger.warning("ORATS_API_KEY not set — dry-run will show plan only (no API calls)")
            api_key = "__dry_run_no_key__"
        else:
            logger.error("ORATS_API_KEY not set. Run: export ORATS_API_KEY=your_key")
            return 1

    db_path = args.db_path
    if not Path(db_path).exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = fetch_tickers_from_db(db_path)

    logger.info(f"Backfilling IV rank for {len(tickers)} tickers{'  [DRY RUN]' if args.dry_run else ''}")
    logger.info(f"Database: {db_path}")

    total_matched = 0
    total_updated = 0
    batches = [tickers[i: i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches, 1):
        logger.info(f"Batch {batch_idx}/{len(batches)}: {', '.join(batch)}")
        ticker_data = fetch_orats_ivrank_batch(api_key, batch)

        if not ticker_data:
            logger.warning(f"  No data returned for batch {batch_idx}")
        else:
            logger.info(f"  ORATS returned data for {len(ticker_data)} tickers")
            matched, updated = update_db(db_path, ticker_data, dry_run=args.dry_run)
            total_matched += matched
            total_updated += updated
            logger.info(f"  Matched {matched} DB rows, updated {updated}")

        if batch_idx < len(batches):
            time.sleep(BATCH_DELAY_SECONDS)

    logger.info(
        f"\nDone. Total: {total_matched} rows matched, {total_updated} rows updated"
        f"{'  (dry run — no writes)' if args.dry_run else ''}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
