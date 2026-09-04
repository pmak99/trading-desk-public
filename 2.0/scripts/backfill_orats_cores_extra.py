#!/usr/bin/env python3
"""
One-time ORATS backfill: populate ern_iv_effect (historical_moves) and
per-ticker snapshot fields (position_limits) from /datav2/cores.

Fields populated:
    historical_moves.ern_iv_effect   — IV multiplier earnings added (ernEffct1-12).
                                        1.54 = IV was 54% above pre-earnings baseline.
                                        High = options were specifically bid up for earnings
                                        (quality signal for IV crush vs random IV spike).
    position_limits.iv30d            — Current 30-day ATM IV (annualised %)
    position_limits.hv20d            — Current 20-day close-to-close HV (annualised %)
    position_limits.abs_avg_ern_mv   — ORATS average absolute earnings move (%)
    position_limits.orats_implied_ern_mv — ORATS forecast of next earnings move (%)

Usage:
    source 2.0/.env
    python scripts/backfill_orats_cores_extra.py [--dry-run] [--tickers AAPL,MSFT]
"""

import argparse
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

_ALLOWED_PL_COLS = frozenset({'iv30d', 'hv20d', 'abs_avg_ern_mv', 'orats_implied_ern_mv'})

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
    """Return distinct tickers that have historical_moves rows."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT DISTINCT ticker FROM historical_moves ORDER BY ticker"
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def fetch_orats_cores_batch(api_key: str, tickers: list[str]) -> dict:
    """
    Call ORATS /datav2/cores for a batch.

    Returns dict keyed by ticker:
      {
        'ern_effects': [(iso_date, effect_float), ...],  # for historical_moves
        'snapshot': {iv30d, hv20d, abs_avg_ern_mv, orats_implied_ern_mv},  # for position_limits
      }
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

        # --- Historical ern_iv_effect pairs ---
        effects = []
        for i in range(1, 13):
            date_val = record.get(f"ernDate{i}")
            eff_val = record.get(f"ernEffct{i}")
            if date_val and eff_val is not None:
                try:
                    raw = float(eff_val)
                    if raw <= 0 or raw > 50:
                        logger.warning(f"{ticker} {date_val}: ernEffct={raw:.4f} out of range — skipping")
                        continue
                    # Normalize ORATS date M/D/YYYY → YYYY-MM-DD
                    date_str = str(date_val).strip()
                    if "/" in date_str:
                        parsed = datetime.strptime(date_str, "%m/%d/%Y")
                    else:
                        parsed = datetime.strptime(date_str[:10], "%Y-%m-%d")
                    effects.append((parsed.strftime("%Y-%m-%d"), raw))
                except (TypeError, ValueError) as e:
                    logger.warning(f"{ticker}: could not parse date '{date_val}': {e}")

        # --- Current snapshot fields for position_limits ---
        snapshot = {}
        for field, key in [
            ("iv30d", "iv30d"),
            ("hv20d", "clsHv20d"),
            ("abs_avg_ern_mv", "absAvgErnMv"),
            ("orats_implied_ern_mv", "impliedEarningsMove"),
        ]:
            val = record.get(key)
            if val is not None:
                try:
                    snapshot[field] = float(val)
                except (TypeError, ValueError):
                    pass

        if effects or snapshot:
            result[ticker] = {"ern_effects": effects, "snapshot": snapshot}

    return result


def update_db(
    db_path: str,
    ticker_data: dict,
    dry_run: bool = False,
) -> tuple[int, int, int, int]:
    """
    Write ern_iv_effect to historical_moves and snapshot to position_limits.

    Returns (hm_matched, hm_updated, pl_matched, pl_updated).
    """
    conn = sqlite3.connect(db_path)
    hm_matched = hm_updated = pl_matched = pl_updated = 0

    try:
        for ticker, payload in ticker_data.items():
            # --- historical_moves: ern_iv_effect ---
            for earnings_date, effect in payload.get("ern_effects", []):
                cursor = conn.execute(
                    "SELECT id FROM historical_moves WHERE ticker=? AND earnings_date=?",
                    (ticker, earnings_date),
                )
                if not cursor.fetchone():
                    continue
                hm_matched += 1
                if not dry_run:
                    conn.execute(
                        "UPDATE historical_moves SET ern_iv_effect=? WHERE ticker=? AND earnings_date=?",
                        (effect, ticker, earnings_date),
                    )
                    hm_updated += 1
                else:
                    logger.debug(f"DRY RUN: would set {ticker} {earnings_date} ern_iv_effect={effect:.4f}")

            # --- position_limits: snapshot fields ---
            snap = payload.get("snapshot", {})
            if not snap:
                continue
            cursor = conn.execute(
                "SELECT ticker FROM position_limits WHERE ticker=?", (ticker,)
            )
            if not cursor.fetchone():
                continue
            pl_matched += 1
            snap_safe = {k: v for k, v in snap.items() if k in _ALLOWED_PL_COLS}
            if not snap_safe:
                continue
            if not dry_run:
                set_parts = ", ".join(f"[{col}]=?" for col in snap_safe)
                values = list(snap_safe.values()) + [ticker]
                conn.execute(
                    f"UPDATE position_limits SET {set_parts} WHERE ticker=?", values
                )
                pl_updated += 1
            else:
                logger.info(f"DRY RUN: would update {ticker} position_limits: {list(snap_safe.keys())}")

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return hm_matched, hm_updated, pl_matched, pl_updated


def main():
    parser = argparse.ArgumentParser(description="Backfill ORATS cores extra fields")
    parser.add_argument("--db-path", default="data/ivcrush.db")
    parser.add_argument("--tickers", help="Comma-separated list (default: all in historical_moves)")
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

    logger.info(f"Backfilling cores extra fields for {len(tickers)} tickers{'  [DRY RUN]' if args.dry_run else ''}")
    logger.info(f"Database: {db_path}")

    total_hm_matched = total_hm_updated = 0
    total_pl_matched = total_pl_updated = 0
    batches = [tickers[i: i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches, 1):
        logger.info(f"Batch {batch_idx}/{len(batches)}: {', '.join(batch)}")
        ticker_data = fetch_orats_cores_batch(api_key, batch)

        if not ticker_data:
            logger.warning(f"  No data returned for batch {batch_idx}")
        else:
            logger.info(f"  ORATS returned data for {len(ticker_data)} tickers")
            hm_m, hm_u, pl_m, pl_u = update_db(db_path, ticker_data, dry_run=args.dry_run)
            total_hm_matched += hm_m
            total_hm_updated += hm_u
            total_pl_matched += pl_m
            total_pl_updated += pl_u
            logger.info(f"  historical_moves: {hm_m} matched, {hm_u} updated | position_limits: {pl_m} matched, {pl_u} updated")

        if batch_idx < len(batches):
            time.sleep(BATCH_DELAY_SECONDS)

    dry = "  (dry run — no writes)" if args.dry_run else ""
    logger.info(
        f"\nDone{dry}."
        f"\n  historical_moves: {total_hm_matched} matched, {total_hm_updated} updated (ern_iv_effect)"
        f"\n  position_limits:  {total_pl_matched} matched, {total_pl_updated} updated (iv30d, hv20d, abs_avg_ern_mv, orats_implied_ern_mv)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
