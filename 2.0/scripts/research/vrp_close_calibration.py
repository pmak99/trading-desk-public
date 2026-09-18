#!/usr/bin/env python3
"""Close-to-close VRP constants at matched selectivity on the live population, and a gate.

Pre-registered 2026-09-16 and committed before its first run
(docs/superpowers/plans/2026-09-16-vrp-close-denominator.md v2, Task 1). It moves no
constant: it prints the values the migration writes into code, and whether the gate
lets it proceed.

Why live. The universe backtest's events (stored ORATS straddles, 743 tickers)
include 6 of the 241 tickers the system scans, and the scanned names lean more on the
overnight gap (median intraday/close VRP 1.85 against 1.49). Constants mapped on the
universe would cut the live score >= 55 share from 72.0% to 56.1% (2026-09-16).

Population (LIVE): the first analysis_log row per (ticker, earnings_date) with an
implied move, intraday VRP and close VRP. Before 2026-09-04 scans logged only
intraday-tradeable names, so the lowest cutoff is weakly identified.

Constants, from intraday values frozen as literals (INTRADAY_*):
  tiers, target_vrp  k = live events with intraday VRP >= the literal; the value is
                     the k-th largest live close VRP, rounded to 2 dp.
  SCORE_VRP_TARGET   the 2-dp target in [1.00, 4.00] whose live share scoring >=
                     SHORTLIST_SCORE (close VRP read against it) is closest to today's
                     share (intraday VRP read against 4.0); ties to the larger target.
                     Each event's logged implied move, one constant liquidity tier.

Gate:
  G0  live events >= MIN_LIVE_EVENTS
  G1  universe backtest events through the live scan scorer: proposed (close VRP read
      against the mapped target) top-20% crush >= today's (intraday VRP against 4.0).
      The only outcome data with power -- mostly other names.
  G2  live share scoring >= SHORTLIST_SCORE: proposed within SHARE_TOLERANCE_PTS of today's.
PASS on all three: proceed with the printed constants. Otherwise STOP (exit 1) and take
the output to the user; nothing is adjusted to pass. A crash prints no GATE line.

Printed and deciding nothing: the universe mapping of each constant, median intraday/
close VRP per population, live intraday-tier x close-tier counts, G1 on universe events
from 2025-06-01, live graded-reaction top-20% crush (ab_evaluate_live's sample), and
the live share a close >= 1.00 floor would admit.

Read-only. Usage (from 2.0/, env exported):
    ./venv/bin/python scripts/research/vrp_close_calibration.py
"""

import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
# ab_test_vrp_baseline's own shim puts 2.0/ on sys.path; `scripts.scan` below needs it.
import ab_evaluate_live as live  # noqa: E402
from ab_test_vrp_baseline import LOOKBACK_QUARTERS, match_events, top_k  # noqa: E402

from common.db import session  # noqa: E402
from research.events import load_events  # noqa: E402
from scripts.scan.quality_scorer import calculate_scan_quality_score  # noqa: E402
from src.domain.enums import Recommendation  # noqa: E402
from src.domain.vrp_tier import vrp_tier  # noqa: E402

# The intraday constants as they stood when this was registered. Literals, never the
# live imports: after the migration those hold the close-to-close values.
INTRADAY_TIERS = (("VRP_MARGINAL_FROM", 1.2), ("VRP_GOOD_FROM", 1.4),
                  ("VRP_EXCELLENT_FROM", 1.8), ("target_vrp", 2.0))
INTRADAY_SCORE_TARGET = 4.0

MIN_LIVE_EVENTS = 200           # G0
SHORTLIST_SCORE = 55.0          # CLAUDE.md §7: the one shortlist cutoff
SHARE_TOLERANCE_PTS = 5.0       # G2
TOP_K_PCT = 20                  # G1
TARGET_GRID = [round(1.00 + i / 100, 2) for i in range(301)]   # 1.00 .. 4.00
CONSTANT_LIQUIDITY = "UNKNOWN"  # not logged per event; scored as WARNING on both sides
CONTEXT_CLOSE_FLOOR = 1.00      # printed only (plan D2)
UNIVERSE_SPLIT = date(2025, 6, 1)

LIVE_QUERY = """
SELECT DISTINCT ON (ticker, earnings_date)
       ticker, earnings_date, implied_move_pct AS im, vrp_intraday_ratio AS vi, vrp_close_ratio AS vc
FROM analysis_log
WHERE implied_move_pct IS NOT NULL AND vrp_intraday_ratio IS NOT NULL AND vrp_close_ratio IS NOT NULL
ORDER BY ticker, earnings_date, "timestamp", id
"""


def matched_cutoff(intraday: list[float], close: list[float], cutoff: float) -> float | None:
    """The close value admitting as many events as intraday >= cutoff (k-th largest)."""
    k = sum(1 for v in intraday if v >= cutoff)
    if k == 0:
        return None
    return sorted(close, reverse=True)[k - 1]


def share(values: list[float], cutoff: float) -> float:
    """Percent of values at or above cutoff."""
    return 100.0 * sum(1 for v in values if v >= cutoff) / len(values)


def score(vrp: float, implied_pct: float, target: float) -> float:
    """The live scan score with its VRP term read against `target`."""
    return calculate_scan_quality_score(
        {"vrp_ratio": vrp, "implied_move_pct": implied_pct, "liquidity_tier": CONSTANT_LIQUIDITY},
        vrp_target=target,
    )


def matched_share_target(today_share: float, shares_by_target: dict[float, float]) -> float:
    """The target whose share is closest to today's; ties go to the larger target."""
    return min(shares_by_target, key=lambda t: (abs(shares_by_target[t] - today_share), -t))


def gate(n_live: int, today_crush: float, proposed_crush: float,
         today_share: float, proposed_share: float) -> tuple[bool, bool, bool]:
    """(G0, G1, G2) as pre-registered in the module docstring."""
    return (n_live >= MIN_LIVE_EVENTS,
            proposed_crush >= today_crush,
            abs(proposed_share - today_share) <= SHARE_TOLERANCE_PTS)


def _tier(ratio: float, ladder: dict[str, float]) -> str:
    return vrp_tier(ratio, ladder["VRP_EXCELLENT_FROM"], ladder["VRP_GOOD_FROM"],
                    ladder["VRP_MARGINAL_FROM"]).name


def main() -> int:
    with session() as conn:
        rows = conn.execute(LIVE_QUERY).fetchall()
    li = [float(r.vi) for r in rows]
    lc = [float(r.vc) for r in rows]
    lm = [float(r.im) for r in rows]
    universe = match_events(
        load_events(baseline="intraday", lookback=LOOKBACK_QUARTERS),
        load_events(baseline="close", lookback=LOOKBACK_QUARTERS),
    )
    ui = [e["vrp_intraday"] for e in universe]
    uc = [e["vrp_close"] for e in universe]

    print("VRP close-to-close calibration -- live population, matched selectivity "
          "(pre-registered 2026-09-16, plan v2)")
    print(f"live: {len(rows)} first-log events; universe: {len(universe)} matched backtest events")
    print(f"median intraday/close VRP: live "
          f"{statistics.median(a / b for a, b in zip(li, lc, strict=True)):.2f}, "
          f"universe {statistics.median(a / b for a, b in zip(ui, uc, strict=True)):.2f}")
    if len(rows) < MIN_LIVE_EVENTS:
        print(f"G0 live events >= {MIN_LIVE_EVENTS}: FAIL ({len(rows)})")
        print("GATE: STOP")
        return 1
    print()

    mapped: dict[str, float] = {}
    print(f"{'constant':<22}{'intraday':>9}{'live->close':>12}{'admits live':>13}"
          f"{'universe->close':>17}")
    for name, value in INTRADAY_TIERS:
        c = matched_cutoff(li, lc, value)
        if c is None:
            print(f"{name:<22}{value:>9.2f}  no live event clears it")
            print("GATE: STOP")
            return 1
        mapped[name] = round(c, 2)
        u = matched_cutoff(ui, uc, value)
        print(f"{name:<22}{value:>9.2f}{mapped[name]:>12.2f}"
              f"{share(li, value):>6.1f}%->{share(lc, mapped[name]):>4.1f}%"
              f"{(f'{u:.2f}' if u is not None else 'n/a'):>17}")

    today_scores = [score(v, m, INTRADAY_SCORE_TARGET) for v, m in zip(li, lm, strict=True)]
    today_share = share(today_scores, SHORTLIST_SCORE)
    shares_by_target = {
        t: share([score(v, m, t) for v, m in zip(lc, lm, strict=True)], SHORTLIST_SCORE)
        for t in TARGET_GRID
    }
    target = matched_share_target(today_share, shares_by_target)
    mapped["SCORE_VRP_TARGET"] = target
    proposed_share = shares_by_target[target]
    print(f"{'SCORE_VRP_TARGET':<22}{INTRADAY_SCORE_TARGET:>9.2f}{target:>12.2f}"
          f"  score>={SHORTLIST_SCORE:.0f}: {today_share:.1f}% -> {proposed_share:.1f}%")
    print()

    for e in universe:
        e["score_today"] = score(e["vrp_intraday"], e["straddle"], INTRADAY_SCORE_TARGET)
        e["score_proposed"] = score(e["vrp_close"], e["straddle"], target)
    today_top = top_k(universe, "score_today", TOP_K_PCT)
    proposed_top = top_k(universe, "score_proposed", TOP_K_PCT)
    g0, g1, g2 = gate(len(rows), today_top["crush_pct"], proposed_top["crush_pct"],
                      today_share, proposed_share)

    print("-- decides nothing --")
    intraday_ladder = dict(INTRADAY_TIERS)
    close_ladder = mapped
    names = [t.name for t in Recommendation]
    counts = {(a, b): 0 for a in names for b in names}
    for v, c in zip(li, lc, strict=True):
        counts[(_tier(v, intraday_ladder), _tier(c, close_ladder))] += 1
    print("live tier counts, rows intraday tier, columns close tier:")
    print(f"  {'':<10}" + "".join(f"{b:>10}" for b in names))
    for a in names:
        print(f"  {a:<10}" + "".join(f"{counts[(a, b)]:>10}" for b in names))
    later = [e for e in universe if e["earnings_date"] >= UNIVERSE_SPLIT]
    if later:
        print(f"universe >= {UNIVERSE_SPLIT} (n={len(later)}) top {TOP_K_PCT}% crush: "
              f"today {top_k(later, 'score_today', TOP_K_PCT)['crush_pct']:.1f}%  "
              f"proposed {top_k(later, 'score_proposed', TOP_K_PCT)['crush_pct']:.1f}%")
    graded = live.load()["vrp"].graded
    for e in graded:
        e["score_today"] = score(e["vi"], e["impl"], INTRADAY_SCORE_TARGET)
        e["score_proposed"] = score(e["vc"], e["impl"], target)
    if graded:
        print(f"live graded reactions (n={len(graded)}) top {TOP_K_PCT}% crush: "
              f"today {100 * live.top_k_crush(graded, 'score_today')[0]:.1f}%  "
              f"proposed {100 * live.top_k_crush(graded, 'score_proposed')[0]:.1f}%")
    print(f"live share with close VRP >= {CONTEXT_CLOSE_FLOOR:.2f}: {share(lc, CONTEXT_CLOSE_FLOOR):.1f}%")
    print(f"universe top {TOP_K_PCT}% mean gross edge: today {today_top['edge']:+.2f}  "
          f"proposed {proposed_top['edge']:+.2f}")
    print()

    print(f"G0 live events >= {MIN_LIVE_EVENTS}: {'PASS' if g0 else 'FAIL'} ({len(rows)})")
    print(f"G1 universe top {TOP_K_PCT}% crush, proposed >= today: {'PASS' if g1 else 'FAIL'} "
          f"({proposed_top['crush_pct']:.1f}% vs {today_top['crush_pct']:.1f}%)")
    print(f"G2 live score>={SHORTLIST_SCORE:.0f} share within {SHARE_TOLERANCE_PTS:.0f} pts: "
          f"{'PASS' if g2 else 'FAIL'} ({proposed_share:.1f}% vs {today_share:.1f}%)")
    for name, value in mapped.items():
        print(f"CONSTANT {name} = {value}")
    passed = g0 and g1 and g2
    print(f"GATE: {'PASS' if passed else 'STOP'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
