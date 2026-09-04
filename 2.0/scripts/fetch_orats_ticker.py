#!/usr/bin/env python3
"""
Fetch live ORATS data for a single ticker and print as JSON.

Called by /analyze skill to get current-quarter data:
  - fcstErnEffct   → fcst_ern_iv_effect  (ORATS model forecast for NEXT quarter)
  - impliedEarningsMove → orats_implied_ern_mv  (ORATS own implied move forecast)
  - iv30d, clsHv20d, absAvgErnMv  (current vol snapshot)
  - ivRank1y, ivPct1y  (52-week IV rank/percentile)

Also writes the values back to position_limits so the DB stays current.

Usage:
    source 2.0/.env
    python scripts/fetch_orats_ticker.py AAPL
    python scripts/fetch_orats_ticker.py AAPL --no-write   # read-only
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

sys_path_root = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, sys_path_root)

from common.constants import ORATS_ENABLED  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ORATS_BASE_URL = "https://api.orats.io/datav2"

_CORES_FIELDS = {
    "fcst_ern_iv_effect": "fcstErnEffct",
    "orats_implied_ern_mv": "impliedEarningsMove",
    "iv30d": "iv30d",
    "hv20d": "clsHv20d",
    "abs_avg_ern_mv": "absAvgErnMv",
}

_IVRANK_FIELDS = {
    "iv_rank_1y": "ivRank1y",
    "iv_pct_1y": "ivPct1y",
}

_SUMMARIES_FIELDS = {
    "iee_earn_effect": "ieeEarnEffect",
    "r_slp_30":        "rSlp30",
}

_ALLOWED_COLS = frozenset(_CORES_FIELDS.keys()) | frozenset(_IVRANK_FIELDS.keys()) | frozenset(_SUMMARIES_FIELDS.keys())


def fetch_cores(api_key: str, ticker: str) -> dict:
    url = f"{ORATS_BASE_URL}/cores"
    try:
        resp = requests.get(url, params={"token": api_key, "tickers": ticker}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ORATS cores error for {ticker}: {e}")
        return {}

    data = resp.json()
    if not isinstance(data, dict) or "data" not in data or not data["data"]:
        return {}

    record = data["data"][0]
    row = {}
    for db_col, orats_key in _CORES_FIELDS.items():
        val = record.get(orats_key)
        if val is not None:
            try:
                row[db_col] = float(val)
            except (TypeError, ValueError):
                pass
    return row


def fetch_ivrank(api_key: str, ticker: str) -> dict:
    url = f"{ORATS_BASE_URL}/ivrank"
    try:
        resp = requests.get(url, params={"token": api_key, "tickers": ticker}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ORATS ivrank error for {ticker}: {e}")
        return {}

    data = resp.json()
    if not isinstance(data, dict) or "data" not in data or not data["data"]:
        return {}

    record = data["data"][0]
    row = {}
    for db_col, orats_key in _IVRANK_FIELDS.items():
        val = record.get(orats_key)
        if val is not None:
            try:
                row[db_col] = float(val)
            except (TypeError, ValueError):
                pass
    return row


def fetch_summaries(api_key: str, ticker: str) -> dict:
    url = f"{ORATS_BASE_URL}/summaries"
    try:
        resp = requests.get(url, params={"token": api_key, "tickers": ticker}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ORATS summaries error for {ticker}: {e}")
        return {}

    data = resp.json()
    if not isinstance(data, dict) or "data" not in data or not data["data"]:
        return {}

    record = data["data"][0]
    row = {}
    for db_col, orats_key in _SUMMARIES_FIELDS.items():
        val = record.get(orats_key)
        if val is not None:
            try:
                row[db_col] = float(val)
            except (TypeError, ValueError):
                pass
    return row


def write_to_db(db_path: str, ticker: str, fields: dict) -> bool:
    safe = {k: v for k, v in fields.items() if k in _ALLOWED_COLS}
    if not safe:
        return False
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT ticker FROM position_limits WHERE ticker=?", (ticker,))
        if not cursor.fetchone():
            return False
        set_parts = ", ".join(f"[{col}]=?" for col in safe)
        conn.execute(
            f"UPDATE position_limits SET {set_parts}, last_updated=CURRENT_TIMESTAMP "
            f"WHERE ticker=?",
            list(safe.values()) + [ticker],
        )
        conn.commit()
        return True
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Fetch live ORATS data for a single ticker")
    parser.add_argument("ticker", help="Ticker symbol (e.g. AAPL)")
    parser.add_argument("--db-path", default="data/ivcrush.db")
    parser.add_argument("--no-write", action="store_true", help="Skip writing to DB")
    args = parser.parse_args()

    ticker = args.ticker.strip().upper()

    if not ORATS_ENABLED:
        print(json.dumps({"orats_enabled": False}))
        return 0

    api_key = os.environ.get("ORATS_API_KEY")
    if not api_key:
        # Return empty JSON so /analyze gracefully degrades
        print(json.dumps({"error": "ORATS_API_KEY not set"}))
        return 1

    cores = fetch_cores(api_key, ticker)
    ivrank = fetch_ivrank(api_key, ticker)
    summaries = fetch_summaries(api_key, ticker)
    merged = {**cores, **ivrank, **summaries}

    if not args.no_write and Path(args.db_path).exists() and merged:
        write_to_db(args.db_path, ticker, merged)

    # Output JSON for the skill to parse
    print(json.dumps(merged, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
