"""Scoring-factor A/B: does anything beyond VRP rank IV crush? (Jun 2026)

The evidence behind CLAUDE.md "Scoring" -> "Do not re-litigate" (Jun 2026 A/B):
VRP is the only strongly monotonic factor, consistency weighting is
flat-to-anti-predictive, and the VRP denominator is a bigger lever than the
weights. Moved here from 2.0/scripts/ab_test_scoring_factors.py on 2026-09-14,
when the preset framework it borrowed its weights from (config/scoring_config.py,
TickerScorer) was deleted as never live.

Outcome = |close_move_pct| (prev close -> reaction close, includes overnight
gap -- what a short premium position actually experiences). Held FIXED while
only the VRP denominator varies; see research/events.py OUTCOME_COLUMN.
Matched-selectivity: each ranking orders all events and we take the top
30%/50%/70% and compare crush rate and edge. This isolates RANKING quality
from cutoff strictness.

Rankings:
  live_scan_score -- calculate_scan_quality_score, imported, never restated:
                     VRP (intraday baseline) plus implied move. Liquidity is
                     not stored per event, so every event scores UNKNOWN, a
                     constant that cannot reorder events. The reference: a
                     weighting that cannot beat it has nothing to add live.
  weight sets     -- the Jun 2026 candidates, a composite of VRP, consistency
                     and trailing crush rate (skew and liquidity fixed, not
                     stored historically). Their component scales are the
                     retired presets' (VRP 1.2/1.5/2.0, consistency
                     0.4/0.6/0.8): score scales, not the CLAUDE.md section 1
                     tiers, kept so each candidate ranks as it did in June.

Event construction comes from the shared core (research/events.py) rather
than a second local copy. Two harnesses previously built their own event
lists and diverged -- one of them silently hardcoded VRP to a constant. Edges
here are NET of execution costs. A short-straddle proxy, never spread P&L.

Read-only: one SELECT per baseline, through load_events.

Usage (from 2.0/, with 2.0/.env exported):
    ./venv/bin/python scripts/research/scoring_factors.py
"""
import statistics
import sys
from collections import defaultdict, namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))            # 2.0/scripts
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))     # 2.0/
from research.events import load_events, net_edge  # noqa: E402
from scripts.scan.quality_scorer import calculate_scan_quality_score  # noqa: E402

# 8, not the core's 12-quarter default: preserved from this study's original
# window so its numbers stay comparable with previously published results.
LOOKBACK = 8
HISTORICAL_LIQUIDITY_TIER = "UNKNOWN"   # not stored per event; scored as WARNING

# Component scales of the retired presets (score breakpoints, not tiers).
VRP_SCALE = (1.2, 1.5, 2.0)             # marginal, good, excellent
CONSISTENCY_SCALE = (0.4, 0.6, 0.8)     # marginal, good, excellent

Weights = namedtuple("Weights", "vrp consistency crush_rate skew liquidity")

CANDIDATES = {
    "current_balanced": (Weights(0.30, 0.25, 0.15, 0.10, 0.20), "vrp"),
    "vrp_heavy":        (Weights(0.50, 0.05, 0.15, 0.10, 0.20), "vrp"),
    "vrp_pure":         (Weights(0.70, 0.00, 0.00, 0.10, 0.20), "vrp"),
    "no_consistency":   (Weights(0.40, 0.00, 0.30, 0.10, 0.20), "vrp"),
    "claude_md_doc":    (Weights(0.40, 0.25, 0.00, 0.15, 0.20), "vrp"),
    "gap_aware_vrp":    (Weights(0.50, 0.05, 0.15, 0.10, 0.20), "vrp_close"),
}


def vrp_score(v):
    marginal, good, excellent = VRP_SCALE
    if v is None or v <= 0:
        return 0.0
    if v >= excellent:
        return 100.0
    if v >= good:
        return 75.0 + 25.0 * (v - good) / (excellent - good)
    if v >= marginal:
        return 50.0 + 25.0 * (v - marginal) / (good - marginal)
    if v >= 1.0:
        return 50.0 * (v - 1.0) / (marginal - 1.0)
    return 0.0


def cons_score(c):
    marginal, good, excellent = CONSISTENCY_SCALE
    if c is None or c < 0:
        return 0.0
    if c >= excellent:
        return 100.0
    if c >= good:
        return 75.0 + 25.0 * (c - good) / (excellent - good)
    if c >= marginal:
        return 50.0 + 25.0 * (c - marginal) / (good - marginal)
    return 0.0


def crush_sc(cr):
    if cr is None:
        return 50.0
    if cr >= 0.70:
        return 100.0
    if cr >= 0.55:
        return 75.0 + 25.0 * (cr - 0.55) / 0.15
    if cr >= 0.40:
        return 50.0 + 25.0 * (cr - 0.40) / 0.15
    return max(0.0, 50.0 * cr / 0.40)


def composite(e, w, vrp_key="vrp"):
    return (w.vrp * vrp_score(e[vrp_key])
            + w.consistency * cons_score(e["consistency"])
            + w.crush_rate * crush_sc(e["crush_rate"])
            + w.skew * 75.0 + w.liquidity * 50.0)


def live_score(e):
    """Score one historical event with the live scan scorer."""
    return calculate_scan_quality_score({
        "vrp_ratio": e["vrp"],
        "implied_move_pct": e["straddle"],
        "liquidity_tier": HISTORICAL_LIQUIDITY_TIER,
    }, vrp_target=4.0)  # the target these results were recorded with (RESEARCH_RESULTS, CLAUDE.md "VRP denominator")


def load():
    # One call per VRP denominator. The outcome is identical across both by
    # construction, so joining on (ticker, date) is safe and the comparison
    # isolates the denominator.
    intraday = load_events(lookback=LOOKBACK, baseline="intraday")
    close = {(e["ticker"], e["earnings_date"]): e
             for e in load_events(lookback=LOOKBACK, baseline="close")}

    events = []
    for e in intraday:
        prior = e["prior_moves"]
        stdev = statistics.stdev(prior) if len(prior) > 1 else 0.0
        other = close.get((e["ticker"], e["earnings_date"]))
        events.append({
            "ticker": e["ticker"],
            "date": e["earnings_date"].isoformat(),
            "vrp": e["vrp"],
            "vrp_close": other["vrp"] if other else None,
            "straddle": e["straddle"],
            "consistency": max(0.0, min(1.0, 1.0 - stdev / e["mean_prior"])),
            "crush_rate": e["prior_crush_rate"],
            "iv_eff_now": e["iv_effect"],
            "crushed": e["crushed"],
            "edge": net_edge(e["edge"], e["straddle"]),
        })
    return events


def print_row(name, split, ranked):
    cells = []
    for pct in (30, 50, 70):
        n = int(len(ranked) * pct / 100)
        sel = ranked[:n]
        cr = 100 * statistics.mean([e["crushed"] for e in sel])
        ed = statistics.mean([e["edge"] for e in sel])
        cells.append(f"cr={cr:5.1f}% ed={ed:+5.2f}%")
    print(f"{name:<18} {split:<6} " + "  ".join(f"{c:>22}" for c in cells))


def fbuck(events, label, key, bounds, names):
    print(f"\n{label}:")
    bk = defaultdict(list)
    for e in events:
        v = e[key]
        if v is None:
            continue
        for b, nm in zip(bounds, names, strict=True):
            if v < b:
                bk[nm].append(e)
                break
        else:
            bk[names[-1]].append(e)
    for nm in names:
        es = bk.get(nm)
        if not es:
            continue
        cr = 100 * statistics.mean([e["crushed"] for e in es])
        ed = statistics.mean([e["edge"] for e in es])
        print(f"  {nm:<14} n={len(es):>5} crush={cr:>5.1f}% edge={ed:>+5.2f}%")


def main():
    events = load()
    train = [e for e in events if e["date"] < "2025-06-01"]
    test = [e for e in events if e["date"] >= "2025-06-01"]
    print(f"Universe events: {len(events)} | train {len(train)} | test {len(test)}")
    for nm, sp in (("train", train), ("test", test)):
        print(f"  {nm}: base crush(gap-incl)={100*statistics.mean([e['crushed'] for e in sp]):.1f}% "
              f"base edge={statistics.mean([e['edge'] for e in sp]):+.2f}%")

    print("\n=== MATCHED-SELECTIVITY COMPARISON (gap-inclusive outcomes) ===")
    print(f"{'config':<18} {'split':<6} " + "  ".join(f"{'top'+str(p)+'%':>22}" for p in (30, 50, 70)))
    for spn, sp in (("train", train), ("test", test)):
        print_row("live_scan_score", spn, sorted(sp, key=live_score, reverse=True))
    for name, (w, vk) in CANDIDATES.items():
        for spn, sp in (("train", train), ("test", test)):
            pool = [e for e in sp if vk != "vrp_close" or e["vrp_close"] is not None]
            print_row(name, spn, sorted(pool, key=lambda e: composite(e, w, vk), reverse=True))

    print("\n=== GAP-INCLUSIVE FACTOR CHECK ===")
    fbuck(events, "System VRP (intraday baseline)", "vrp", [1.2, 1.8, 2.5, 99], ["<1.2", "1.2-1.8", "1.8-2.5", ">=2.5"])
    fbuck(events, "Gap-aware VRP (close baseline)", "vrp_close", [1.0, 1.2, 1.4, 1.8, 2.5, 99], ["<1.0", "1.0-1.2", "1.2-1.4", "1.4-1.8", "1.8-2.5", ">=2.5"])
    fbuck(events, "CURRENT-Q ern_iv_effect (post-hoc)", "iv_eff_now", [1.5, 2.0, 2.5, 99], ["<1.5x", "1.5-2.0x", "2.0-2.5x", ">=2.5x"])


if __name__ == "__main__":
    main()
