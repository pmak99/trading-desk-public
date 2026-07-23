#!/usr/bin/env python3
"""
Weekly IV snapshot tracker for harvest-sleeve tickers.

Logs ATM iv30d (30-day IV proxy) and HV20 from Tradier option chains into
iv_history so we can compute our own IV rank once 52 weeks accumulate.

Usage:
    source 2.0/.env
    python scripts/track_iv_weekly.py               # all harvest tickers
    python scripts/track_iv_weekly.py --tickers SPY,QQQ
    python scripts/track_iv_weekly.py --ivr          # print current IVR from history
    python scripts/track_iv_weekly.py --dry-run       # fetch but don't write

Replaces ORATS iv_rank_1y / iv_pct_1y for the IVR > 25 harvest gate after
52 weekly readings accumulate (approximately June 2027).
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infrastructure.api.tradier import TradierAPI  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

HARVEST_TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN"]
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ivcrush.db"
TARGET_DTE_MIN = 20
TARGET_DTE_MAX = 45


def _pick_expiration(expirations: list[date], today: date) -> date | None:
    """Pick the expiration closest to 30 DTE, within [20, 45] days."""
    candidates = [e for e in expirations if TARGET_DTE_MIN <= (e - today).days <= TARGET_DTE_MAX]
    if not candidates:
        # Expand to nearest outside the window
        candidates = [e for e in expirations if (e - today).days > 0]
    if not candidates:
        return None
    target = today + timedelta(days=30)
    return min(candidates, key=lambda e: abs((e - target).days))


def _atm_iv(chain, stock_price: float) -> tuple[float | None, float | None]:
    """
    Extract ATM IV from an OptionChain: average of call and put IV at the
    strike nearest to the current stock price.

    Returns (atm_iv, atm_strike).
    """
    try:
        strike = chain.atm_strike()
    except (ValueError, AttributeError):
        return None, None

    ivs = []
    for side in (chain.calls, chain.puts):
        quote = side.get(strike)
        if quote and quote.implied_volatility is not None:
            iv = float(quote.implied_volatility.value)
            if iv > 0:
                ivs.append(iv)
    if not ivs:
        return None, None
    return sum(ivs) / len(ivs), float(strike.price)


def _hv20_from_position_limits(conn: sqlite3.Connection, ticker: str) -> float | None:
    """Pull the most recent hv20d from position_limits as a fallback."""
    row = conn.execute(
        "SELECT hv20d FROM position_limits WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row[0] if row else None


def fetch_and_store(
    tickers: list[str],
    api: TradierAPI,
    conn: sqlite3.Connection,
    today: date,
    dry_run: bool = False,
) -> dict[str, dict]:
    results = {}
    for ticker in tickers:
        logger.info(f"  {ticker}...")

        exp_result = api.get_expirations(ticker)
        if exp_result.is_err:
            logger.warning(f"    {ticker}: no expirations — {exp_result.error}")
            continue

        expiration = _pick_expiration(exp_result.value, today)
        if expiration is None:
            logger.warning(f"    {ticker}: no suitable expiration found")
            continue

        dte = (expiration - today).days
        chain_result = api.get_option_chain(ticker, expiration)
        if chain_result.is_err:
            logger.warning(f"    {ticker}: chain error — {chain_result.error}")
            continue

        chain = chain_result.value
        stock_price = float(chain.stock_price.amount)
        iv, atm_strike = _atm_iv(chain, stock_price)

        if iv is None:
            logger.warning(f"    {ticker}: no ATM IV in chain")
            continue

        hv20d = _hv20_from_position_limits(conn, ticker)

        row = {
            "ticker": ticker,
            "snapshot_date": today.isoformat(),
            "iv30d": round(iv, 4),
            "hv20d": hv20d,
            "atm_strike": atm_strike,
            "expiration": expiration.isoformat(),
            "dte": dte,
        }
        results[ticker] = row
        logger.info(f"    iv30d={iv:.1f}  hv20d={hv20d}  exp={expiration} ({dte}d)")

        if not dry_run:
            conn.execute(
                """INSERT OR REPLACE INTO iv_history
                   (ticker, snapshot_date, iv30d, hv20d, atm_strike, expiration, dte, source)
                   VALUES (:ticker, :snapshot_date, :iv30d, :hv20d, :atm_strike, :expiration, :dte, 'tradier')""",
                row,
            )
    if not dry_run:
        conn.commit()
    return results


def print_ivr_table(conn: sqlite3.Connection) -> None:
    """Print computed IVR from the accumulated iv_history."""
    rows = conn.execute(
        """
        SELECT
          ticker,
          COUNT(*)                                             AS weeks,
          ROUND(MAX(iv30d), 1)                                AS iv_max,
          ROUND(MIN(iv30d), 1)                                AS iv_min,
          ROUND((SELECT iv30d FROM iv_history h2
                 WHERE h2.ticker = h.ticker
                 ORDER BY snapshot_date DESC LIMIT 1), 1)     AS iv_current,
          ROUND(
            100.0 * (
              (SELECT iv30d FROM iv_history h2
               WHERE h2.ticker = h.ticker
               ORDER BY snapshot_date DESC LIMIT 1) - MIN(iv30d)
            ) / NULLIF(MAX(iv30d) - MIN(iv30d), 0), 1
          )                                                   AS ivr,
          MIN(snapshot_date)                                  AS since,
          MAX(snapshot_date)                                  AS latest
        FROM iv_history h
        GROUP BY ticker
        ORDER BY ticker
        """
    ).fetchall()

    if not rows:
        print("No iv_history data yet. Run track_iv_weekly.py first.")
        return

    gate_weeks = 52
    print(f"\n{'Ticker':<8} {'Weeks':>5} {'IVR':>6} {'Current':>8} {'Min':>6} {'Max':>6} {'Gate':>8} {'Since':<12}")
    print("-" * 70)
    for r in rows:
        ticker, weeks, iv_max, iv_min, iv_cur, ivr, since, _ = r
        gate = "PASS ✓" if (ivr or 0) > 25 else "SKIP ✗"
        reliable = "~" if weeks < gate_weeks else ""
        print(f"{ticker:<8} {weeks:>5} {reliable}{ivr or 0:>5.1f}% {iv_cur or 0:>7.1f} {iv_min or 0:>6.1f} {iv_max or 0:>6.1f} {gate:>8} {since}")

    if any(r[1] < gate_weeks for r in rows):
        have = rows[0][1] if rows else 0
        print(f"\n~ IVR marked with '~' has <52 weeks of data ({have} weeks so far)")
        print(f"  Full reliability: ~{gate_weeks - have} more weekly runs needed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly IV snapshot tracker for harvest tickers")
    parser.add_argument("--tickers", help="Comma-separated tickers (default: all harvest tickers)")
    parser.add_argument("--ivr", action="store_true", help="Print IVR table from history and exit")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to DB")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else HARVEST_TICKERS

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if args.ivr:
        print_ivr_table(conn)
        conn.close()
        return 0

    api_key = os.environ.get("TRADIER_API_KEY", "")
    if not api_key:
        logger.error("TRADIER_API_KEY not set")
        return 1

    api = TradierAPI(api_key=api_key)
    today = date.today()

    logger.info(f"IV snapshot — {today}  ({'dry-run' if args.dry_run else 'live'})")
    results = fetch_and_store(tickers, api, conn, today, dry_run=args.dry_run)

    conn.close()
    ok = len(results)
    fail = len(tickers) - ok
    logger.info(f"\nDone. {ok}/{len(tickers)} tickers snapshotted" + (f", {fail} failed" if fail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
