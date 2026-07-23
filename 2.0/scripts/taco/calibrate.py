"""One-time calibration: score your own long-dated (DTE>=90) historical index
entries with the TACO signal set, and measure exit retracement captured.

Output informs the # CALIBRATION placeholders in constants.py. Losers are the
most informative rows. Populate EXTRA_TRADES and HISTORICAL_EVENT_LABELS
below with your own trade history before running. Run:
    ./venv/bin/python -m scripts.taco.calibrate
"""

import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.taco.constants import (  # noqa: E402
    CROSS_ASSET_SYMBOLS,
    TIER_FULL,
    TIER_HALF,
    TIER_PILOT,
    EventType,
)
from scripts.taco.cross_asset import (  # noqa: E402
    evaluate_cross_asset,
    fetch_twelvedata,
)
from scripts.taco.market_data import (  # noqa: E402
    SPX_SYMBOL,
    VIX3M_SYMBOL,
    VIX_SYMBOL,
    compute_refs,
    default_event_date,
    drawdown_pct,
    fetch_series,
    up_to,
    vix_spike,
)
from scripts.taco.scoring import score_entry  # noqa: E402

DB = "data/ivcrush.db"

# Trades not yet journaled into the strategies table (dedupe key: symbol +
# acquired_date). Format: (symbol, acquired_date, sale_date, expiration,
# gain_loss, is_winner). Populate with your own.
EXTRA_TRADES = []


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# Event labels for your own historical entries, reviewed by you before
# calibrating. Key: (symbol, acquired_date) -> (EventType, direction).
# PUT rows are excluded from call-side fitting. Populate with your own.
HISTORICAL_EVENT_LABELS = {}


def _long_history(symbol: str):
    """3y+ daily closes for a cross-asset ETF: yfinance primary, Twelve Data
    fallback (outputsize 800 covers back past the 2024-06 earliest entry)."""
    s = fetch_series(symbol, period="4y")
    if s:
        return s, "yfinance"
    key = os.environ.get("TWELVE_DATA_KEY", "")
    if key:
        s = fetch_twelvedata(symbol, key, outputsize=800)
        if s:
            return s, "twelvedata"
    return [], ""


def load_trades():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT symbol, acquired_date, sale_date, expiration,
               gain_loss, is_winner
        FROM strategies
        WHERE symbol IN ('SPY','SPX','SPXW')
          AND expiration IS NOT NULL
          AND CAST(julianday(expiration) - julianday(acquired_date) AS INT) >= 90
        ORDER BY acquired_date""").fetchall()
    trades = [dict(r) for r in rows]
    have = {(t["symbol"], t["acquired_date"]) for t in trades}
    for sym, acq, sale, exp, pnl, win in EXTRA_TRADES:
        if (sym, acq) not in have:
            trades.append({"symbol": sym, "acquired_date": acq,
                           "sale_date": sale, "expiration": exp,
                           "gain_loss": pnl, "is_winner": win})
    return trades


def main() -> int:
    spx = fetch_series(SPX_SYMBOL, "4y")
    vix = fetch_series(VIX_SYMBOL, "4y")
    vix3m = fetch_series(VIX3M_SYMBOL, "4y")
    if not spx or not vix:
        print("ERROR: market history download failed")
        return 1

    asset_history = {sym: _long_history(sym) for sym in CROSS_ASSET_SYMBOLS}
    for sym, (s, src) in asset_history.items():
        first = s[0][0].isoformat() if s else "—"
        print(f"asset {sym}: {len(s)} closes from {first} ({src or 'UNAVAILABLE'})")

    trades = load_trades()
    print(f"\n{'sym':5} {'entry':11} {'dd%':>5} {'vix':>5} {'spike':>5} "
          f"{'term':>5} {'retrace':>7} {'event':12} {'xa':>4} "
          f"{'score':>5} {'tier':6} {'pnl':>9} win")
    rows = []
    for t in trades:
        entry, exit_ = _d(t["acquired_date"]), _d(t["sale_date"])
        label = HISTORICAL_EVENT_LABELS.get((t["symbol"], t["acquired_date"]),
                                            (EventType.UNKNOWN, "CALL"))
        event_type, direction = label
        try:
            dd = drawdown_pct(spx, entry)
            spk = vix_spike(vix, entry)
            v = up_to(vix, entry)[-1][1]
            v3 = up_to(vix3m, entry)
            term = v / v3[-1][1] if v3 else None
            ev = default_event_date(spx, entry)
            refs = compute_refs("CALL", spx, vix, ev, entry)
            dip = refs.pre_event_high - refs.panic_low
            exit_close = up_to(spx, exit_)[-1][1]
            retrace = ((exit_close - refs.panic_low) / dip
                       if dip > 0 else float("nan"))
            xa = evaluate_cross_asset(direction, event_type, asset_history,
                                      ev, entry)
            if direction == "CALL":
                sig = score_entry("CALL", drawdown=dd, vix_level=v,
                                  vix_spike_ratio=spk, vix3m_ratio=term,
                                  event_type=event_type,
                                  cross_asset_count=xa.count,
                                  cross_asset_available=xa.available)
                score, tier = sig.score, sig.tier.value
            else:
                score, tier = float("nan"), "PUT-EX"  # excluded from fitting
        except ValueError as e:
            print(f"{t['symbol']:5} {t['acquired_date']:11} SKIPPED: {e}")
            continue
        rows.append((dd, v, spk, term, retrace, t, event_type, direction,
                     xa, score, tier))
        xa_str = f"{xa.count}/{xa.available}"
        print(f"{t['symbol']:5} {t['acquired_date']:11} {dd:5.1f} {v:5.1f} "
              f"{spk:5.2f} {term if term else float('nan'):5.2f} "
              f"{retrace:7.2f} {event_type.value:12} {xa_str:>4} "
              f"{score:5.1f} {tier:6} {t['gain_loss']:9.0f} {t['is_winner']}")
        detail = "  ".join(
            f"{r.symbol} {r.move_pct:+.1f}%{'✓' if r.confirmed else '✗'}"
            for r in xa.reads)
        print(f"      xa: {detail}"
              + (f"  unavail: {','.join(xa.unavailable)}"
                 if xa.unavailable else ""))

    calls = [r for r in rows if r[7] == "CALL"]
    winners = [r for r in calls if r[5]["is_winner"]]
    losers = [r for r in calls if not r[5]["is_winner"]]
    for name, grp in (("WINNERS", winners), ("LOSERS", losers)):
        if not grp:
            continue
        n = len(grp)
        counts = sorted(g[8].count for g in grp)
        print(f"\n{name} (n={n}): "
              f"dd med {sorted(g[0] for g in grp)[n // 2]:.1f}% | "
              f"vix med {sorted(g[1] for g in grp)[n // 2]:.1f} | "
              f"retrace med {sorted(g[4] for g in grp)[n // 2]:.2f} | "
              f"xa count med {counts[n // 2]} dist {counts}")

    deconfirmed = [r for r in winners if r[8].count <= 1]
    print(f"\nDe-confirmed winners (count<=1): {len(deconfirmed)}"
          + (" — " + ", ".join(f"{r[5]['symbol']} {r[5]['acquired_date']}"
                               for r in deconfirmed) if deconfirmed else ""))
    print(f"Tier assignments under current thresholds "
          f"({TIER_FULL:.0f}/{TIER_HALF:.0f}/{TIER_PILOT:.0f}):")
    for tier_name in ("FULL", "HALF", "PILOT", "SKIP"):
        members = [r for r in calls if r[10] == tier_name]
        if members:
            wl = sum(1 for r in members if r[5]["is_winner"])
            print(f"  {tier_name:6} n={len(members)} "
                  f"({wl} win / {len(members) - wl} loss)")
    print("\nReview: cross-asset thresholds (CROSS_ASSET_THRESHOLDS), tier "
          "thresholds (TIER_*). Winners should keep their intended tiers; "
          "the component's job is dragging false positives, not winners.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
