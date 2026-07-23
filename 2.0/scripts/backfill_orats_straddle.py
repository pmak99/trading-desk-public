#!/usr/bin/env python3
"""
One-time ORATS backfill: populate historical_moves.pre_earnings_straddle_pct.

ORATS /datav2/cores returns up to 12 past earnings records per ticker with the
pre-earnings ATM straddle price as a % of stock price (ernStraPct1-12). This
fills the historical gap before analysis_log started capturing it automatically.

Usage:
    source 2.0/.env
    python scripts/backfill_orats_straddle.py [--dry-run] [--tickers AAPL,MSFT]

After running successfully: cancel ORATS subscription.
"""

import argparse
import logging
import os
import sqlite3
import time
from datetime import datetime
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
    """Return distinct tickers that have earnings history."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT DISTINCT ticker FROM historical_moves ORDER BY ticker"
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def fetch_orats_batch(api_key: str, tickers: list[str]) -> dict:
    """
    Call ORATS /datav2/cores for a batch of tickers.

    Returns dict keyed by ticker with list of (earnings_date, straddle_pct) pairs.
    """
    if api_key == "__dry_run_no_key__":
        logger.info(f"  [no key] would fetch: {', '.join(tickers)}")
        return {}

    ticker_str = ",".join(tickers)
    url = f"{ORATS_BASE_URL}/cores"
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

        pairs = []
        for i in range(1, 13):
            date_val = record.get(f"ernDate{i}")
            pct_val = record.get(f"ernStraPct{i}")
            if date_val and pct_val is not None:
                # ernStraPct is in percentage form (e.g. 8.70 = 8.70%).
                try:
                    raw = float(pct_val)
                    # ORATS returns ernStraPct already in percentage form
                    # (e.g. 8.70 = 8.70%, not 0.087). Guard against absurd values.
                    if raw <= 0 or raw > 100.0:
                        logger.warning(
                            f"{ticker} {date_val}: ernStraPct={raw:.4f} out of range "
                            f"[0, 100] — skipping."
                        )
                        continue
                    # Normalize ORATS date (M/D/YYYY or YYYY-MM-DD) → YYYY-MM-DD
                    date_str = str(date_val).strip()
                    try:
                        if "/" in date_str:
                            parsed = datetime.strptime(date_str, "%m/%d/%Y")
                        else:
                            parsed = datetime.strptime(date_str[:10], "%Y-%m-%d")
                        iso_date = parsed.strftime("%Y-%m-%d")
                    except ValueError:
                        logger.warning(f"{ticker}: unrecognized date format '{date_str}' — skipping")
                        continue
                    pairs.append((iso_date, raw))
                except (TypeError, ValueError):
                    pass

        if pairs:
            result[ticker] = pairs

    return result


def update_db(
    db_path: str,
    ticker_data: dict[str, list[tuple[str, float]]],
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    UPDATE historical_moves.pre_earnings_straddle_pct for matched rows.

    Returns (rows_matched, rows_updated) counts.
    """
    conn = sqlite3.connect(db_path)
    matched = 0
    updated = 0

    try:
        for ticker, pairs in ticker_data.items():
            for earnings_date, straddle_pct in pairs:
                cursor = conn.execute(
                    "SELECT id FROM historical_moves WHERE ticker=? AND earnings_date=?",
                    (ticker, earnings_date),
                )
                row = cursor.fetchone()
                if not row:
                    continue
                matched += 1
                if not dry_run:
                    conn.execute(
                        "UPDATE historical_moves SET pre_earnings_straddle_pct=? "
                        "WHERE ticker=? AND earnings_date=?",
                        (straddle_pct, ticker, earnings_date),
                    )
                    updated += 1
                else:
                    logger.debug(
                        f"DRY RUN: would set {ticker} {earnings_date} → {straddle_pct:.2f}%"
                    )

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return matched, updated


def main():
    parser = argparse.ArgumentParser(description="Backfill ORATS straddle history")
    parser.add_argument(
        "--db-path",
        default="data/ivcrush.db",
        help="Path to ivcrush.db (default: data/ivcrush.db)",
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated ticker list (default: all tickers in historical_moves)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without writing to DB",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ORATS_API_KEY")
    if not api_key:
        if args.dry_run:
            logger.warning("ORATS_API_KEY not set — dry-run will show ticker/batch plan only (no API calls)")
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

    logger.info(f"Backfilling {len(tickers)} tickers from ORATS{'  [DRY RUN]' if args.dry_run else ''}")
    logger.info(f"Database: {db_path}")

    total_matched = 0
    total_updated = 0
    batches = [tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches, 1):
        logger.info(f"Batch {batch_idx}/{len(batches)}: {', '.join(batch)}")

        ticker_data = fetch_orats_batch(api_key, batch)

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
    if not args.dry_run and total_updated > 0:
        logger.info("Cancel ORATS subscription once you have verified the data.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
