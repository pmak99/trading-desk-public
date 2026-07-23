#!/usr/bin/env python3
"""
Expand historical_moves to the S&P 500 + MidCap 400 universe (~900 stocks).

Pipeline per new ticker:
  1. Fetch S&P 500 + MidCap 400 from Wikipedia
  2. Find tickers not already in historical_moves
  3. ORATS /datav2/cores → last 12Q earnings dates, close moves, ern_iv_effect, straddle pcts
  4. yfinance → OHLCV (one call per ticker, 3-year window) → gap_move_pct + all price fields
  5. INSERT OR IGNORE into historical_moves + earnings_calendar

Resumable: INSERT OR IGNORE means re-runs skip already-inserted rows.

Usage:
    export ORATS_API_KEY=xxx
    python scripts/expand_universe.py [--dry-run] [--limit N] [--tickers AAPL,MSFT]
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

ORATS_BASE_URL = "https://api.orats.io/datav2"
ORATS_BATCH_SIZE = 20
ORATS_BATCH_DELAY = 0.5   # seconds between ORATS batch calls
YFINANCE_DELAY = 0.5       # seconds between yfinance ticker calls
CLOSE_MATCH_TOLERANCE = 1.5  # % tolerance for AMC vs BMO validation
WIKIPEDIA_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
HISTORY_START = "2022-12-01"  # covers 12+ quarters back from mid-2026


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def _fetch_wikipedia_tickers(url: str, id_attr: str | None = None) -> list[str]:
    resp = requests.get(url, headers={"User-Agent": WIKIPEDIA_UA}, timeout=20)
    resp.raise_for_status()
    kwargs = {"attrs": {"id": id_attr}} if id_attr else {}
    tables = pd.read_html(StringIO(resp.text), **kwargs)
    for df in tables:
        for col in ("Symbol", "Ticker", "ticker", "symbol"):
            if col in df.columns:
                return (
                    df[col]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .str.replace(".", "-", regex=False)
                    .tolist()
                )
    raise RuntimeError(f"No symbol column found in tables from {url}")


def get_universe() -> list[str]:
    logger.info("Fetching S&P 500 from Wikipedia...")
    sp500 = _fetch_wikipedia_tickers(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        id_attr="constituents",
    )
    logger.info(f"  S&P 500: {len(sp500)} tickers")

    logger.info("Fetching S&P MidCap 400 from Wikipedia...")
    sp400 = _fetch_wikipedia_tickers(
        "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
    )
    logger.info(f"  MidCap 400: {len(sp400)} tickers")

    combined = sorted(set(sp500 + sp400))
    logger.info(f"Combined universe: {len(combined)} unique tickers")
    return combined


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_existing_tickers(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT DISTINCT ticker FROM historical_moves")
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ORATS
# ---------------------------------------------------------------------------

def fetch_orats_batch(api_key: str, tickers: list[str]) -> dict[str, list[dict]]:
    """
    Returns {ticker: [{earnings_date, close_move_pct, ern_iv_effect, pre_earnings_straddle_pct}, ...]}
    """
    try:
        resp = requests.get(
            f"{ORATS_BASE_URL}/cores",
            params={"token": api_key, "tickers": ",".join(tickers)},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ORATS error for batch {tickers[:3]}: {e}")
        return {}

    result: dict[str, list[dict]] = {}
    for record in resp.json().get("data", []):
        ticker = record.get("ticker")
        if not ticker:
            continue

        rows = []
        for i in range(1, 13):
            date_val = record.get(f"ernDate{i}")
            mv       = record.get(f"ernMv{i}")
            eff      = record.get(f"ernEffct{i}")
            stra     = record.get(f"ernStraPct{i}")

            if not date_val or mv is None:
                continue
            try:
                date_str = str(date_val).strip()
                parsed = (
                    datetime.strptime(date_str, "%m/%d/%Y")
                    if "/" in date_str
                    else datetime.strptime(date_str[:10], "%Y-%m-%d")
                )
                raw_eff = float(eff) if eff is not None else None
                if raw_eff is not None and (raw_eff <= 0 or raw_eff > 50):
                    raw_eff = None
                raw_stra = float(stra) if stra is not None else None

                rows.append({
                    "earnings_date": parsed.strftime("%Y-%m-%d"),
                    "close_move_pct": float(mv),
                    "ern_iv_effect": raw_eff,
                    "pre_earnings_straddle_pct": raw_stra,
                })
            except (TypeError, ValueError) as e:
                logger.debug(f"{ticker} Q{i}: parse error — {e}")

        if rows:
            result[ticker] = rows

    return result


# ---------------------------------------------------------------------------
# yfinance — one call per ticker, look up individual dates from the window
# ---------------------------------------------------------------------------

def fetch_price_history(ticker: str) -> pd.DataFrame | None:
    """Fetch 3+ year OHLCV history for ticker. Returns normalized DataFrame or None."""
    try:
        hist = yf.Ticker(ticker).history(
            start=HISTORY_START,
            end=datetime.today().strftime("%Y-%m-%d"),
            auto_adjust=True,
        )
    except Exception as e:
        logger.warning(f"{ticker}: yfinance error — {e}")
        return None

    if hist.empty:
        return None

    # Normalize index to plain date objects (drop tz)
    hist.index = [ts.date() for ts in hist.index]
    return hist


def compute_price_row(
    hist: pd.DataFrame,
    earnings_date_str: str,
    orats_close_mv: float,
) -> dict | None:
    """
    Compute price fields for one earnings date from the full history DataFrame.
    Tries AMC (T close → T+1 open/close) then BMO (T-1 close → T open/close).
    Validates against ORATS close_move_pct within CLOSE_MATCH_TOLERANCE.
    Falls back to unvalidated AMC if neither interpretation validates.
    """
    from datetime import date as date_type

    edate = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
    idx_list = list(hist.index)  # list of date objects

    def idx_of(d) -> int | None:
        try:
            return idx_list.index(d)
        except ValueError:
            return None

    ei = idx_of(edate)
    if ei is None:
        return None  # earnings date not a trading day in yfinance data

    def build_row(t_idx: int, t1_idx: int) -> dict | None:
        if t_idx < 0 or t1_idx >= len(idx_list):
            return None
        t_row  = hist.iloc[t_idx]
        t1_row = hist.iloc[t1_idx]

        prev_close     = float(t_row["Close"])
        earnings_open  = float(t1_row["Open"])
        earnings_high  = float(t1_row["High"])
        earnings_low   = float(t1_row["Low"])
        earnings_close = float(t1_row["Close"])

        if prev_close == 0 or earnings_open == 0:
            return None

        gap_mv   = (earnings_open  - prev_close) / prev_close * 100
        close_mv = (earnings_close - prev_close) / prev_close * 100
        intra_mv = (earnings_close - earnings_open) / earnings_open * 100

        return {
            "prev_close":        prev_close,
            "earnings_open":     earnings_open,
            "earnings_high":     earnings_high,
            "earnings_low":      earnings_low,
            "earnings_close":    earnings_close,
            "gap_move_pct":      gap_mv,
            "close_move_pct":    close_mv,
            "intraday_move_pct": intra_mv,
            "volume_before":     int(t_row.get("Volume", 0)) or None,
            "volume_earnings":   int(t1_row.get("Volume", 0)) or None,
            "validated":         abs(close_mv - orats_close_mv) <= CLOSE_MATCH_TOLERANCE,
        }

    # AMC: close on earnings_date, reaction on earnings_date+1
    row = build_row(ei, ei + 1)
    if row and row["validated"]:
        del row["validated"]
        return row

    # BMO: close on earnings_date-1, reaction on earnings_date
    row_bmo = build_row(ei - 1, ei)
    if row_bmo and row_bmo["validated"]:
        del row_bmo["validated"]
        return row_bmo

    # Neither validated — use AMC unvalidated (best guess, log it)
    if row:
        logger.debug(
            f"  {earnings_date_str}: unvalidated "
            f"(ORATS close_mv={orats_close_mv:.2f}%, computed={row['close_move_pct']:.2f}%)"
        )
        del row["validated"]
        return row

    return None


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def insert_rows(
    db_path: str,
    ticker: str,
    orats_rows: list[dict],
    hist: pd.DataFrame | None,
    dry_run: bool,
) -> tuple[int, int]:
    """Returns (hm_inserted, ec_inserted)."""
    hm_count = ec_count = 0
    conn = sqlite3.connect(db_path)
    try:
        for orats_row in orats_rows:
            edate = orats_row["earnings_date"]

            price = None
            if hist is not None:
                price = compute_price_row(hist, edate, orats_row["close_move_pct"])

            if price is None:
                logger.debug(f"  {ticker} {edate}: no price data — skipping")
                continue

            if not dry_run:
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO historical_moves
                          (ticker, earnings_date,
                           prev_close, earnings_open, earnings_high, earnings_low, earnings_close,
                           intraday_move_pct, gap_move_pct, close_move_pct,
                           volume_before, volume_earnings,
                           pre_earnings_straddle_pct, ern_iv_effect)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            ticker, edate,
                            price["prev_close"], price["earnings_open"],
                            price["earnings_high"], price["earnings_low"], price["earnings_close"],
                            price["intraday_move_pct"], price["gap_move_pct"], price["close_move_pct"],
                            price["volume_before"], price["volume_earnings"],
                            orats_row["pre_earnings_straddle_pct"], orats_row["ern_iv_effect"],
                        ),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        hm_count += 1
                except sqlite3.Error as e:
                    logger.warning(f"  {ticker} {edate}: historical_moves error — {e}")

                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO earnings_calendar (ticker, earnings_date, timing, confirmed) VALUES (?,?,'UNKNOWN',0)",
                        (ticker, edate),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        ec_count += 1
                except sqlite3.Error as e:
                    logger.warning(f"  {ticker} {edate}: earnings_calendar error — {e}")
            else:
                hm_count += 1  # count as would-insert in dry-run

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return hm_count, ec_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Expand historical_moves to S&P 500 + MidCap 400")
    parser.add_argument("--dry-run", action="store_true", help="Fetch data but do not write to DB")
    parser.add_argument("--limit", type=int, help="Process at most N new tickers")
    parser.add_argument("--tickers", help="Comma-separated ticker list (overrides universe fetch)")
    args = parser.parse_args()

    api_key = os.environ.get("ORATS_API_KEY")
    if not api_key:
        logger.error("ORATS_API_KEY not set. Run: export ORATS_API_KEY=your_key")
        return 1

    db_path = str(Path(__file__).parent.parent / "data" / "ivcrush.db")

    # --- Universe ---
    if args.tickers:
        new_tickers = [t.strip().upper() for t in args.tickers.split(",")]
        logger.info(f"Using explicit ticker list: {len(new_tickers)} tickers")
    else:
        universe = get_universe()
        existing = get_existing_tickers(db_path)
        new_tickers = [t for t in universe if t not in existing]
        logger.info(
            f"Universe {len(universe)} | In DB already {len(existing)} | New: {len(new_tickers)}"
        )

    if args.limit:
        new_tickers = new_tickers[: args.limit]
        logger.info(f"Capped at {args.limit} tickers for this run")

    if not new_tickers:
        logger.info("No new tickers — nothing to do.")
        return 0

    dry_tag = "  [DRY RUN]" if args.dry_run else ""
    logger.info(f"Processing {len(new_tickers)} tickers{dry_tag}")

    total_hm = total_ec = skipped_no_orats = skipped_no_price = 0
    total_batches = (len(new_tickers) + ORATS_BATCH_SIZE - 1) // ORATS_BATCH_SIZE

    for batch_start in range(0, len(new_tickers), ORATS_BATCH_SIZE):
        batch = new_tickers[batch_start : batch_start + ORATS_BATCH_SIZE]
        batch_num = batch_start // ORATS_BATCH_SIZE + 1
        logger.info(f"Batch {batch_num}/{total_batches}: {', '.join(batch)}")

        orats_data = fetch_orats_batch(api_key, batch)

        for ticker in batch:
            if ticker not in orats_data:
                logger.info(f"  {ticker}: not in ORATS — skipping")
                skipped_no_orats += 1
                continue

            orats_rows = orats_data[ticker]
            logger.info(f"  {ticker}: {len(orats_rows)} ORATS quarters — fetching price history...")

            hist = fetch_price_history(ticker)
            if hist is None:
                logger.warning(f"  {ticker}: no yfinance data — skipping")
                skipped_no_price += 1
                continue

            hm, ec = insert_rows(db_path, ticker, orats_rows, hist, args.dry_run)
            price_misses = len(orats_rows) - hm - (0 if args.dry_run else 0)
            logger.info(
                f"  {ticker}: +{hm} historical_moves  +{ec} earnings_calendar"
                + (f"  ({len(orats_rows) - hm} dates no price match)" if len(orats_rows) != hm else "")
            )
            total_hm += hm
            total_ec += ec
            time.sleep(YFINANCE_DELAY)

        time.sleep(ORATS_BATCH_DELAY)

    logger.info(
        f"\nDone{dry_tag}."
        f"\n  Tickers processed:             {len(new_tickers)}"
        f"\n  Not in ORATS (skipped):        {skipped_no_orats}"
        f"\n  No yfinance data (skipped):    {skipped_no_price}"
        f"\n  historical_moves inserted:     {total_hm}"
        f"\n  earnings_calendar inserted:    {total_ec}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
