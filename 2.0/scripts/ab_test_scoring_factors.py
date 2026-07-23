"""Final A/B: gap-inclusive outcomes + matched-selectivity weight comparison.

Outcome = |close_move_pct| (prev close -> reaction close, includes overnight
gap — what a short premium position actually experiences).
Matched-selectivity: each config ranks all events by composite; we take the
top 30%/50%/70% and compare crush rate and edge. This isolates RANKING
quality of the weights from cutoff strictness.
"""
import sqlite3
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from src.config.scoring_config import ScoringWeights, ScoringThresholds

from pathlib import Path
DB = str(Path(__file__).parent.parent / "data" / "ivcrush.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

moves = defaultdict(list)
for r in conn.execute(
    """SELECT ticker, earnings_date, intraday_move_pct, close_move_pct,
              pre_earnings_straddle_pct, ern_iv_effect
       FROM historical_moves ORDER BY ticker, earnings_date"""
):
    moves[r["ticker"]].append(dict(r))

events = []
for ticker, ms in moves.items():
    for i, m in enumerate(ms):
        if not m["pre_earnings_straddle_pct"] or m["close_move_pct"] is None:
            continue
        prior = ms[max(0, i - 8):i]
        abs_moves = [abs(p["intraday_move_pct"]) for p in prior if p["intraday_move_pct"] is not None]
        if len(abs_moves) < 4:
            continue
        mean_mv = statistics.mean(abs_moves)
        if mean_mv <= 0:
            continue
        straddle = m["pre_earnings_straddle_pct"]
        stdev = statistics.stdev(abs_moves)
        crush_evts = [(p["pre_earnings_straddle_pct"], abs(p["intraday_move_pct"]))
                      for p in prior if p["pre_earnings_straddle_pct"] and p["intraday_move_pct"] is not None]
        crush_rate = (sum(1 for s_, a_ in crush_evts if s_ > a_) / len(crush_evts)) if len(crush_evts) >= 3 else None
        # gap-aware historical baseline: mean |close_move| of prior quarters
        abs_close = [abs(p["close_move_pct"]) for p in prior if p["close_move_pct"] is not None]
        mean_close = statistics.mean(abs_close) if len(abs_close) >= 4 else None
        actual = abs(m["close_move_pct"])
        events.append(dict(ticker=ticker, date=m["earnings_date"],
                           vrp=straddle / mean_mv,
                           vrp_close=(straddle / mean_close) if mean_close else None,
                           consistency=max(0.0, min(1.0, 1.0 - stdev / mean_mv)),
                           crush_rate=crush_rate,
                           iv_eff_now=m["ern_iv_effect"],
                           crushed=1 if straddle > actual else 0,
                           edge=straddle - actual))

train = [e for e in events if e["date"] < "2025-06-01"]
test = [e for e in events if e["date"] >= "2025-06-01"]
print(f"Universe events: {len(events)} | train {len(train)} | test {len(test)}")
for nm, sp in (("train", train), ("test", test)):
    print(f"  {nm}: base crush(gap-incl)={100*statistics.mean([e['crushed'] for e in sp]):.1f}% "
          f"base edge={statistics.mean([e['edge'] for e in sp]):+.2f}%")

def vrp_score(v, th):
    if v is None or v <= 0: return 0.0
    if v >= th.vrp_excellent: return 100.0
    if v >= th.vrp_good: return 75.0 + 25.0 * (v - th.vrp_good) / (th.vrp_excellent - th.vrp_good)
    if v >= th.vrp_marginal: return 50.0 + 25.0 * (v - th.vrp_marginal) / (th.vrp_good - th.vrp_marginal)
    if v >= 1.0: return 50.0 * (v - 1.0) / (th.vrp_marginal - 1.0)
    return 0.0

def cons_score(c, th):
    if c is None or c < 0: return 0.0
    if c >= th.consistency_excellent: return 100.0
    if c >= th.consistency_good: return 75.0 + 25.0 * (c - th.consistency_good) / (th.consistency_excellent - th.consistency_good)
    if c >= th.consistency_marginal: return 50.0 + 25.0 * (c - th.consistency_marginal) / (th.consistency_good - th.consistency_marginal)
    return 0.0

def crush_sc(cr):
    if cr is None: return 50.0
    if cr >= 0.70: return 100.0
    if cr >= 0.55: return 75.0 + 25.0 * (cr - 0.55) / 0.15
    if cr >= 0.40: return 50.0 + 25.0 * (cr - 0.40) / 0.15
    return max(0.0, 50.0 * cr / 0.40)

th = ScoringThresholds()

def composite(e, w, vrp_key="vrp"):
    return (w.vrp_weight * vrp_score(e[vrp_key], th)
            + w.consistency_weight * cons_score(e["consistency"], th)
            + w.iv_crush_rate_weight * crush_sc(e["crush_rate"])
            + w.skew_weight * 75.0 + w.liquidity_weight * 50.0)

CANDIDATES = {
    "current_balanced": (ScoringWeights(0.30, 0.25, 0.15, 0.10, 0.20), "vrp"),
    "vrp_heavy":        (ScoringWeights(0.50, 0.05, 0.15, 0.10, 0.20), "vrp"),
    "vrp_pure":         (ScoringWeights(0.70, 0.00, 0.00, 0.10, 0.20), "vrp"),
    "no_consistency":   (ScoringWeights(0.40, 0.00, 0.30, 0.10, 0.20), "vrp"),
    "claude_md_doc":    (ScoringWeights(0.40, 0.25, 0.00, 0.15, 0.20), "vrp"),
    "gap_aware_vrp":    (ScoringWeights(0.50, 0.05, 0.15, 0.10, 0.20), "vrp_close"),
}

print("\n=== MATCHED-SELECTIVITY COMPARISON (gap-inclusive outcomes) ===")
print(f"{'config':<18} {'split':<6} " + "  ".join(f"{'top'+str(p)+'%':>22}" for p in (30, 50, 70)))
for name, (w, vk) in CANDIDATES.items():
    for spn, sp in (("train", train), ("test", test)):
        pool = [e for e in sp if vk != "vrp_close" or e["vrp_close"] is not None]
        ranked = sorted(pool, key=lambda e: composite(e, w, vk), reverse=True)
        cells = []
        for pct in (30, 50, 70):
            n = int(len(ranked) * pct / 100)
            sel = ranked[:n]
            cr = 100 * statistics.mean([e["crushed"] for e in sel])
            ed = statistics.mean([e["edge"] for e in sel])
            cells.append(f"cr={cr:5.1f}% ed={ed:+5.2f}%")
        print(f"{name:<18} {spn:<6} " + "  ".join(f"{c:>22}" for c in cells))

print("\n=== GAP-INCLUSIVE FACTOR CHECK ===")
def fbuck(label, key, bounds, names):
    print(f"\n{label}:")
    bk = defaultdict(list)
    for e in events:
        v = e[key]
        if v is None: continue
        for b, nm in zip(bounds, names):
            if v < b: bk[nm].append(e); break
        else: bk[names[-1]].append(e)
    for nm in names:
        es = bk.get(nm)
        if not es: continue
        cr = 100 * statistics.mean([e["crushed"] for e in es])
        ed = statistics.mean([e["edge"] for e in es])
        print(f"  {nm:<14} n={len(es):>5} crush={cr:>5.1f}% edge={ed:>+5.2f}%")

fbuck("System VRP (intraday baseline)", "vrp", [1.2, 1.8, 2.5, 99], ["<1.2", "1.2-1.8", "1.8-2.5", ">=2.5"])
fbuck("Gap-aware VRP (close baseline)", "vrp_close", [1.0, 1.2, 1.4, 1.8, 2.5, 99], ["<1.0", "1.0-1.2", "1.2-1.4", "1.4-1.8", "1.8-2.5", ">=2.5"])
fbuck("CURRENT-Q ern_iv_effect (post-hoc)", "iv_eff_now", [1.5, 2.0, 2.5, 99], ["<1.5x", "1.5-2.0x", "2.0-2.5x", ">=2.5x"])
conn.close()
