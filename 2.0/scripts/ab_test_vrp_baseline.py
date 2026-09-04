"""A/B test of the LIVE scan composite (scripts/scan/quality_scorer.py).

score = 55 * (vrp / 4.0) + liquidity_pts + 25 * max(0, 1 - implied_move/20)
Liquidity is constant (unknown -> 12) across historical events, so ranking
differences come from the VRP definition and the VRP:Move weight split.
Outcome: gap-inclusive |close_move_pct| crush + edge. Matched selectivity.
"""
import sqlite3
import statistics
from collections import defaultdict

from pathlib import Path
DB = str(Path(__file__).parent.parent / "data" / "ivcrush.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

moves = defaultdict(list)
for r in conn.execute(
    """SELECT ticker, earnings_date, intraday_move_pct, close_move_pct,
              pre_earnings_straddle_pct FROM historical_moves
       ORDER BY ticker, earnings_date"""
):
    moves[r["ticker"]].append(dict(r))

events = []
for ticker, ms in moves.items():
    for i, m in enumerate(ms):
        if not m["pre_earnings_straddle_pct"] or m["close_move_pct"] is None:
            continue
        prior = ms[max(0, i - 8):i]
        abs_intra = [abs(p["intraday_move_pct"]) for p in prior if p["intraday_move_pct"] is not None]
        abs_close = [abs(p["close_move_pct"]) for p in prior if p["close_move_pct"] is not None]
        if len(abs_intra) < 4 or len(abs_close) < 4:
            continue
        mi, mc = statistics.mean(abs_intra), statistics.mean(abs_close)
        if mi <= 0 or mc <= 0:
            continue
        st = m["pre_earnings_straddle_pct"]
        actual = abs(m["close_move_pct"])
        events.append(dict(date=m["earnings_date"], straddle=st,
                           vrp_i=st / mi, vrp_c=st / mc,
                           crushed=1 if st > actual else 0, edge=st - actual))

train = [e for e in events if e["date"] < "2025-06-01"]
test = [e for e in events if e["date"] >= "2025-06-01"]
print(f"events={len(events)} train={len(train)} test={len(test)}")

def live_score(e, vrp_pts, move_pts, vrp_key, vrp_target=4.0):
    vrp = e[vrp_key] if vrp_key != "min" else min(e["vrp_i"], e["vrp_c"])
    return (vrp_pts * vrp / vrp_target
            + 12.0
            + move_pts * max(0.0, 1.0 - e["straddle"] / 20.0))

VARIANTS = {
    "LIVE today (vrp_i 55 / move 25)": dict(vrp_pts=55, move_pts=25, vrp_key="vrp_i"),
    "pre-Dec25   (vrp_i 45 / move 35)": dict(vrp_pts=45, move_pts=35, vrp_key="vrp_i"),
    "vrp-only    (vrp_i 80 / move 0)": dict(vrp_pts=80, move_pts=0, vrp_key="vrp_i"),
    "gap-VRP     (vrp_c 55 / move 25)": dict(vrp_pts=55, move_pts=25, vrp_key="vrp_c"),
    "gap-VRP-only(vrp_c 80 / move 0)": dict(vrp_pts=80, move_pts=0, vrp_key="vrp_c"),
    "min-blend   (min   55 / move 25)": dict(vrp_pts=55, move_pts=25, vrp_key="min"),
    "min-blendVO (min   80 / move 0)": dict(vrp_pts=80, move_pts=0, vrp_key="min"),
}

print(f"\n{'variant':<34} {'split':<6}" + "".join(f"{'top'+str(p)+'%':>24}" for p in (20, 30, 50)))
for name, kw in VARIANTS.items():
    for spn, sp in (("train", train), ("test", test)):
        ranked = sorted(sp, key=lambda e: live_score(e, **kw), reverse=True)
        cells = []
        for pct in (20, 30, 50):
            sel = ranked[:int(len(ranked) * pct / 100)]
            cr = 100 * statistics.mean([e["crushed"] for e in sel])
            ed = statistics.mean([e["edge"] for e in sel])
            cells.append(f"cr={cr:5.1f}% ed={ed:+5.2f}%")
        print(f"{name:<34} {spn:<6}" + "".join(f"{c:>24}" for c in cells))
conn.close()
