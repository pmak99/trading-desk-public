#!/usr/bin/env python3
"""
What every writer of a `strategies` row shares: its trade_type at creation and
how its quantity is counted from the legs. regroup_strategies.py (the /journal
grouper) and reconcile_journal.py import both.

The leg-grouping code that lived here served only backfill_strategies.py, which
nothing ran; both were removed 2026-09-14. regroup_strategies.py groups orphan
legs.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# The trade_type of a position created from imported legs. ROLL and REPAIR are
# set only with a parent_strategy_id, by hand, when an adjustment is identified;
# nothing at import can tell an adjustment from a new trade. Until 2026-09-13 no
# writer set it at all, leaving every IRA option and stock row NULL.
NEW_POSITION_TRADE_TYPE = "NEW"


def quantity_from_legs(strike_quantity_pairs: List[Tuple[Optional[float], float]]) -> float:
    """The strategy-level quantity for a multi-leg position is per-LEG, not
    summed across legs -- a spread trades matching quantity on every leg by
    construction, and CLAUDE.md's contract-count sizing rules (the 100-cap,
    the 51-100/101-200 buckets) assume this. Sum WITHIN each distinct strike
    first (combining that leg's own partial fills -- for STOCK, where every
    leg shares strike=None, this collapses to one group and behaves as a
    plain sum, which is correct: shares genuinely are additive). Then take
    the MAX across strikes, which guards against a rare partial
    exercise/assignment imbalance.

    Root-caused 2026-09-11: regroup_strategies' call sites previously did
    sum(leg.quantity for leg in legs) with no per-strike grouping, so an
    800-contract $145/$150 LULU spread (800 on each leg) stored quantity as
    1600. Affected 156 of 176 SPREAD rows and all 5 IRON_CONDOR rows.

    ⚠️ And the opposite error, until 2026-09-13: the since-deleted
    backfill_strategies took max(leg.quantity) over raw fills, so a leg filled
    as 60 + 40 stored 60.
    """
    by_strike: Dict[Optional[float], float] = defaultdict(float)
    for strike, qty in strike_quantity_pairs:
        by_strike[strike] += qty or 0
    return max(by_strike.values()) if by_strike else 0
