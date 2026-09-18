#!/usr/bin/env python3
"""
Weekly IV snapshot tracker for harvest-sleeve tickers.

Logs ATM iv30d (30-day IV proxy) from Tradier option chains, and HV20 from
daily closes, into iv_history so we can compute our own IV rank once 52 weeks
accumulate.

⚠️ HV20 used to be copied from position_limits.hv20d -- an ORATS snapshot frozen
at 2026-06-24 -- so every row written after that date carried June's value
(22 of 24 snapshots per ticker, verified 2026-09-13). It is now computed from
closes with harvest_live.compute_hv20, the same function /harvest's IV/HV
screen uses, so the two can never disagree. `--recompute-hv` repairs rows
already written.

Report-only unless --execute is passed.

Usage:
    source 2.0/.env
    python scripts/track_iv_weekly.py --execute      # snapshot all harvest tickers
    python scripts/track_iv_weekly.py --tickers SPY,QQQ --execute
    python scripts/track_iv_weekly.py                # fetch and show, don't write
    python scripts/track_iv_weekly.py --ivr          # print current IVR from history
    python scripts/track_iv_weekly.py --recompute-hv             # show HV20 repairs
    python scripts/track_iv_weekly.py --recompute-hv --execute   # apply them

Replaces ORATS iv_rank_1y / iv_pct_1y for the IVR > 25 harvest gate after
52 weekly readings accumulate (approximately June 2027).
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.db import connect  # noqa: E402
from common.timezone import today_et  # noqa: E402
from src.config.config import Config  # noqa: E402
from src.container import Container  # noqa: E402
from src.domain.protocols import CachedOptionsProvider  # noqa: E402
from src.infrastructure.api.yahoo import yahoo_call  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

HARVEST_TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN"]
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


def _hv20_now(ticker: str) -> float | None:
    """Current 20-day realised vol (%), from the last ~3 months of closes."""
    from scripts.scan.harvest_live import (  # noqa: PLC0415
        _fetch_closes_yf,
        compute_hv20,
    )

    hv = compute_hv20(_fetch_closes_yf(ticker, "3mo"))
    return round(hv, 2) if hv is not None else None


def _dated_closes(ticker: str, start: date, end: date) -> list[tuple[date, float]]:
    """Daily (date, close) pairs in [start, end], under the Yahoo limit; [] on any failure."""
    try:
        import yfinance as yf  # noqa: PLC0415
        hist = yahoo_call(lambda: yf.Ticker(ticker).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=False))
        return [(ts.date(), float(c)) for ts, c in zip(hist.index, hist["Close"], strict=True)
                if c and c > 0]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"    {ticker}: yfinance history failed — {e}")
        return []


def recompute_hv20(conn: psycopg.Connection, tickers: list[str], dry_run: bool) -> int:
    """Recompute hv20d for existing iv_history rows, as of each snapshot date.

    Uses closes dated on or before the snapshot -- what the live job would have
    seen that day. Returns the number of rows whose value changed.
    """
    from scripts.scan.harvest_live import compute_hv20  # noqa: PLC0415

    rows = conn.execute(
        "SELECT ticker, snapshot_date, hv20d FROM iv_history"
        " WHERE ticker = ANY(%s) ORDER BY ticker, snapshot_date",
        (tickers,),
    ).fetchall()
    updates: list[tuple[str, date, float | None]] = []
    for ticker in sorted({r.ticker for r in rows}):
        snaps = [r for r in rows if r.ticker == ticker]
        closes = _dated_closes(ticker, snaps[0].snapshot_date - timedelta(days=60),
                               snaps[-1].snapshot_date)
        if not closes:
            logger.warning(f"  {ticker}: no closes — left unchanged")
            continue
        for r in snaps:
            hv = compute_hv20([c for d, c in closes if d <= r.snapshot_date])
            new = round(hv, 2) if hv is not None else None
            if new == r.hv20d:
                continue
            updates.append((ticker, r.snapshot_date, new))
            logger.info(f"  {ticker} {r.snapshot_date}: hv20d {r.hv20d} -> {new}")
    if not dry_run and updates:
        # One statement for every changed row
        tickers_col, dates_col, hv_col = (list(col) for col in zip(*updates, strict=True))
        conn.execute(
            "UPDATE iv_history AS h SET hv20d = v.hv20d"
            " FROM unnest(%s::text[], %s::date[], %s::double precision[])"
            "   AS v(ticker, snapshot_date, hv20d)"
            " WHERE h.ticker = v.ticker AND h.snapshot_date = v.snapshot_date",
            (tickers_col, dates_col, hv_col),
        )
    if not dry_run:
        conn.commit()
    return len(updates)


def fetch_and_store(
    tickers: list[str],
    api: CachedOptionsProvider,
    conn: psycopg.Connection,
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

        hv20d = _hv20_now(ticker)

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
                """INSERT INTO iv_history
                   (ticker, snapshot_date, iv30d, hv20d, atm_strike, expiration, dte, source)
                   VALUES (%(ticker)s, %(snapshot_date)s, %(iv30d)s, %(hv20d)s,
                           %(atm_strike)s, %(expiration)s, %(dte)s, 'tradier')
                   ON CONFLICT (ticker, snapshot_date) DO UPDATE SET
                      iv30d = EXCLUDED.iv30d, hv20d = EXCLUDED.hv20d,
                      atm_strike = EXCLUDED.atm_strike,
                      expiration = EXCLUDED.expiration, dte = EXCLUDED.dte,
                      source = EXCLUDED.source""",
                row,
            )
    if not dry_run:
        conn.commit()
    return results


def print_ivr_table(conn: psycopg.Connection) -> None:
    """Print computed IVR from the accumulated iv_history."""
    rows = conn.execute(
        """
        SELECT
          ticker,
          COUNT(*)                                             AS weeks,
          -- ::numeric on every ROUND: Postgres has no
          -- ROUND(double precision, int), and these are all float columns.
          ROUND(MAX(iv30d)::numeric, 1)                       AS iv_max,
          ROUND(MIN(iv30d)::numeric, 1)                       AS iv_min,
          ROUND((SELECT iv30d FROM iv_history h2
                 WHERE h2.ticker = h.ticker
                 ORDER BY snapshot_date DESC LIMIT 1)::numeric, 1) AS iv_current,
          ROUND(
            (100.0 * (
              (SELECT iv30d FROM iv_history h2
               WHERE h2.ticker = h.ticker
               ORDER BY snapshot_date DESC LIMIT 1) - MIN(iv30d)
            ) / NULLIF(MAX(iv30d) - MIN(iv30d), 0))::numeric, 1
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
    parser.add_argument("--execute", action="store_true",
                        help="Write snapshots / HV20 repairs (default: fetch and report only)")
    parser.add_argument("--recompute-hv", action="store_true",
                        help="Recompute hv20d for rows already in iv_history, then exit")
    args = parser.parse_args()
    dry_run = not args.execute

    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else HARVEST_TICKERS

    conn = connect()

    if args.ivr:
        print_ivr_table(conn)
        conn.close()
        return 0

    if args.recompute_hv:
        n = recompute_hv20(conn, tickers, dry_run=dry_run)
        conn.close()
        logger.info(f"{n} hv20d value(s) {'would change' if dry_run else 'updated'}")
        return 0

    config = Config.from_env()
    if not config.api.tradier_api_key:
        logger.error("TRADIER_API_KEY not set")
        return 1

    # The Container's cached provider, not a TradierAPI of our own: that one had
    # no limiter (rate_limiter=None), so this job ran outside the shared
    # per-minute limit and without retries. And not the Container's raw client,
    # whose get_option_chain quotes before it waits for the chain's slot, so the
    # spot that picks the ATM strike aged by the wait (until 2026-09-14); the
    # provider takes the slot first and reads the quote after it. No validation
    # (only the Tradier key matters here). No migration: a Container does not
    # migrate, and weekly maintenance applies migrations before /maintenance runs this.
    api = Container(config, skip_validation=True).cached_options_provider
    today = today_et()

    logger.info(f"IV snapshot — {today}  ({'report only, pass --execute to write' if dry_run else 'live'})")
    results = fetch_and_store(tickers, api, conn, today, dry_run=dry_run)

    conn.close()
    ok = len(results)
    fail = len(tickers) - ok
    verb = "fetched (not written; pass --execute)" if dry_run else "snapshotted"
    logger.info(f"\nDone. {ok}/{len(tickers)} tickers {verb}" + (f", {fail} failed" if fail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
