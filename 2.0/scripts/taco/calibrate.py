"""Calibration report: score the long-dated (DTE >= MIN_DTE) SPY/SPX/SPXW
entries in the journal with the TACO signal set, and measure the exit
retracement each one captured.

⚠️ IN-SAMPLE. The values constants.py marks "calibrated" were read off this
report's rows for entries through CALIBRATED_THROUGH (the Jul 14 and Jul 22
2026 passes), so those rows' tiers and win rates describe the fit, not the
rule. Entries after CALIBRATED_THROUGH print in their own OUT-OF-SAMPLE
section; they are the only evidence this report holds about the thresholds.

Read-only. The trades come from the `positions` view through `common.db` (PG* from
2.0/.env) and the side of each from its `trade_journal` legs; market history
comes from yfinance (Twelve Data fallback). Nothing is written. Run from 2.0/:
    ./venv/bin/python -m scripts.taco.calibrate
"""

import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from common.db import as_date, connect  # noqa: E402
from scripts.taco.constants import (  # noqa: E402
    CALIBRATED_THROUGH,
    CROSS_ASSET_SYMBOLS,
    MIN_DTE,
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
from src.config.config import APIConfig  # noqa: E402

# User-approved event labels (reviewed 2026-07-22), keyed by
# (symbol, acquired_date) as a `date` -- strategies returns dates since the
# Postgres cutover, and string keys silently labelled every row UNKNOWN.
# Judgment calls approved as drafted: 2025-08-01 TARIFF_TACO (tariff deadline
# + NFP miss same day); 2026-02-23 GEOPOLITICAL (pre-escalation Iran jitters,
# strikes began Feb 28). Entries after 2026-07-17 have no approved label and
# read UNKNOWN until one is reviewed.
#
# The side is NOT labelled here. The hand-written table said CALL for every
# entry but 2026-07-09, and two were puts (SPY 2025-02-18, SPX 2025-05-13 --
# their journal legs are puts, and their cost basis prices as puts, not
# calls), so both passes fitted them as calls. load_trades() takes the side
# from trade_journal instead.
HISTORICAL_EVENT_LABELS = {
    ("SPY", date(2024, 6, 20)): EventType.ECON_DATA,
    ("SPY", date(2025, 2, 18)): EventType.TARIFF_TACO,
    ("SPY", date(2025, 3, 4)): EventType.TARIFF_TACO,
    ("SPY", date(2025, 3, 11)): EventType.TARIFF_TACO,
    ("SPY", date(2025, 4, 4)): EventType.TARIFF_TACO,
    ("SPX", date(2025, 5, 13)): EventType.ECON_DATA,
    ("SPX", date(2025, 8, 1)): EventType.TARIFF_TACO,
    ("SPY", date(2025, 9, 5)): EventType.ECON_DATA,
    ("SPXW", date(2026, 1, 20)): EventType.TARIFF_TACO,
    ("SPY", date(2026, 1, 20)): EventType.TARIFF_TACO,
    ("SPX", date(2026, 2, 23)): EventType.GEOPOLITICAL,
    ("SPXW", date(2026, 2, 23)): EventType.GEOPOLITICAL,
    ("SPY", date(2026, 3, 3)): EventType.GEOPOLITICAL,
    ("SPY", date(2026, 3, 9)): EventType.GEOPOLITICAL,
    ("SPXW", date(2026, 3, 13)): EventType.GEOPOLITICAL,
    ("SPY", date(2026, 3, 27)): EventType.GEOPOLITICAL,
    ("SPX", date(2026, 6, 5)): EventType.GEOPOLITICAL,
    ("SPY", date(2026, 6, 9)): EventType.GEOPOLITICAL,
    ("SPX", date(2026, 6, 23)): EventType.GEOPOLITICAL,
    ("SPX", date(2026, 7, 9)): EventType.GEOPOLITICAL,
    ("SPXW", date(2026, 7, 17)): EventType.GEOPOLITICAL,
}


def event_label(symbol: str, acquired) -> EventType:
    """The approved EventType for an entry; UNKNOWN when none was reviewed.

    `acquired` may be a `date` (what the database returns) or an ISO string.
    """
    return HISTORICAL_EVENT_LABELS.get((symbol, as_date(acquired)),
                                       EventType.UNKNOWN)


def _long_history(symbol: str):
    """3y+ daily closes for a cross-asset ETF: yfinance primary, Twelve Data
    fallback (outputsize 800 covers back past the 2024-06 earliest entry)."""
    s = fetch_series(symbol, period="4y")
    if s:
        return s, "yfinance"
    key = APIConfig.from_env().twelve_data_key
    if key:
        s = fetch_twelvedata(symbol, key, outputsize=800)
        if s:
            return s, "twelvedata"
    return [], ""


def load_trades():
    """Every long-dated index option position in the journal, oldest first.

    A position is a row of the `positions` view: one call placed in both TAXABLE
    and the IRA is one entry with its P&L summed (SPXW 2026-03-13, strategies
    301/333, counted as two in-sample winners until 2026-09-15).
    `direction` is the option type of the position's journal legs: 'CALL',
    'PUT', 'CALL/PUT' for a mixed position, or None when it has no legs.
    A win is net P&L > 0, the definition 2.0/scripts/rule_evidence.py uses.
    """
    with connect() as con:
        rows = con.execute("""
            SELECT p.symbol, p.acquired_date, p.sale_date, p.expiration,
                   p.gain_loss, p.is_winner,
                   (SELECT string_agg(DISTINCT j.option_type, '/'
                                      ORDER BY j.option_type)
                      FROM trade_journal j
                     WHERE j.strategy_id = ANY(p.strategy_ids)) AS direction
            FROM positions p
            WHERE p.symbol IN ('SPY', 'SPX', 'SPXW')
              AND p.expiration IS NOT NULL
              AND p.expiration - p.acquired_date >= %s
            ORDER BY p.acquired_date, p.id""", (MIN_DTE,)).fetchall()
    return [r._asdict() for r in rows]


def _median(values):
    """Upper median, NaN ignored (a zero-depth dip has no retracement)."""
    s = sorted(v for v in values if not math.isnan(v))
    return s[len(s) // 2] if s else float("nan")


def _evaluate(t, spx, vix, vix3m, asset_history):
    entry, exit_ = t["acquired_date"], t["sale_date"]
    event_type = event_label(t["symbol"], entry)
    direction = t["direction"]
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
    xa = evaluate_cross_asset("PUT" if direction == "PUT" else "CALL",
                              event_type, asset_history, ev, entry)
    if direction == "CALL":
        sig = score_entry("CALL", drawdown=dd, vix_level=v,
                          vix_spike_ratio=spk, vix3m_ratio=term,
                          event_type=event_type,
                          cross_asset_count=xa.count,
                          cross_asset_available=xa.available)
        score, tier = sig.score, sig.tier.value
    else:
        # Excluded from call-side fitting: a put, a mixed position, or one
        # with no journal legs to take a side from.
        score = float("nan")
        tier = "PUT-EX" if direction == "PUT" else "?-EX"
    return {"t": t, "dd": dd, "vix": v, "spike": spk, "term": term,
            "retrace": retrace, "event_type": event_type, "xa": xa,
            "score": score, "tier": tier}


def _print_header():
    print(f"\n{'sym':5} {'entry':10} {'side':5} {'dd%':>5} {'vix':>5} "
          f"{'spike':>5} {'term':>5} {'retrace':>7} {'event':12} {'xa':>4} "
          f"{'score':>5} {'tier':6} {'pnl':>9} win")


def _print_row(r):
    t, xa = r["t"], r["xa"]
    term = r["term"] if r["term"] else float("nan")
    print(f"{t['symbol']:5} {t['acquired_date'].isoformat()} "
          f"{t['direction'] or '?':5} {r['dd']:5.1f} {r['vix']:5.1f} "
          f"{r['spike']:5.2f} {term:5.2f} {r['retrace']:7.2f} "
          f"{r['event_type'].value:12} {f'{xa.count}/{xa.available}':>4} "
          f"{r['score']:5.1f} {r['tier']:6} {t['gain_loss']:9.0f} "
          f"{t['is_winner']}")
    detail = "  ".join(
        f"{a.symbol} {a.move_pct:+.1f}%{'✓' if a.confirmed else '✗'}"
        for a in xa.reads)
    print(f"      xa: {detail}"
          + (f"  unavail: {','.join(xa.unavailable)}"
             if xa.unavailable else ""))


def _print_tiers(calls):
    print(f"Tier assignments under current thresholds "
          f"({TIER_FULL:.0f}/{TIER_HALF:.0f}/{TIER_PILOT:.0f}):")
    for tier_name in ("FULL", "HALF", "PILOT", "SKIP"):
        members = [r for r in calls if r["tier"] == tier_name]
        if members:
            wl = sum(1 for r in members if r["t"]["is_winner"])
            print(f"  {tier_name:6} n={len(members)} "
                  f"({wl} win / {len(members) - wl} loss)")


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

    rows = []
    for t in load_trades():
        try:
            rows.append(_evaluate(t, spx, vix, vix3m, asset_history))
        except ValueError as e:
            print(f"{t['symbol']:5} {t['acquired_date'].isoformat()} "
                  f"SKIPPED: {e}")
    in_sample = [r for r in rows if r["t"]["acquired_date"] <= CALIBRATED_THROUGH]
    later = [r for r in rows if r["t"]["acquired_date"] > CALIBRATED_THROUGH]

    print(f"\nIN-SAMPLE — entries through {CALIBRATED_THROUGH}. The calibrated "
          f"constants were fitted to these rows: their tiers and win rates "
          f"describe the fit, not the rule.")
    _print_header()
    for r in in_sample:
        _print_row(r)

    calls = [r for r in in_sample if r["t"]["direction"] == "CALL"]
    winners = [r for r in calls if r["t"]["is_winner"]]
    losers = [r for r in calls if not r["t"]["is_winner"]]
    for name, grp in (("WINNERS", winners), ("LOSERS", losers)):
        if not grp:
            continue
        counts = sorted(g["xa"].count for g in grp)
        print(f"\n{name} (n={len(grp)}): "
              f"dd med {_median(g['dd'] for g in grp):.1f}% | "
              f"vix med {_median(g['vix'] for g in grp):.1f} | "
              f"retrace med {_median(g['retrace'] for g in grp):.2f} | "
              f"xa count med {counts[len(counts) // 2]} dist {counts}")
    if calls:
        deconfirmed = [r for r in winners if r["xa"].count <= 1]
        print(f"\nDe-confirmed winners (count<=1): {len(deconfirmed)}"
              + (" — " + ", ".join(
                  f"{r['t']['symbol']} {r['t']['acquired_date'].isoformat()}"
                  for r in deconfirmed) if deconfirmed else ""))
        _print_tiers(calls)

    print(f"\nOUT-OF-SAMPLE — entries after {CALIBRATED_THROUGH}. Only these "
          f"rows can test the thresholds.")
    if later:
        _print_header()
        for r in later:
            _print_row(r)
        later_calls = [r for r in later if r["t"]["direction"] == "CALL"]
        if later_calls:
            wins = sum(1 for r in later_calls if r["t"]["is_winner"])
            pnl = sum(r["t"]["gain_loss"] for r in later_calls)
            print(f"\nCALLS (n={len(later_calls)}): {wins} win / "
                  f"{len(later_calls) - wins} loss, net {pnl:+,.0f}")
            _print_tiers(later_calls)
    else:
        print("  none yet")

    print("\nReview: cross-asset thresholds (CROSS_ASSET_THRESHOLDS), tier "
          "thresholds (TIER_*). Winners should keep their intended tiers; "
          "the component's job is dragging false positives, not winners. "
          "A refit makes every row it reads in-sample: move "
          "CALIBRATED_THROUGH with it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
