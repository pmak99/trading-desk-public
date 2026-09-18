"""Pure helpers of 2.0/scripts/research/vrp_close_calibration.py (no database).

The mapping must admit exactly as many events as the intraday literal, the score
target must match the shortlist share, the gate must be the pre-registered one, and
the intraday constants must stay frozen literals even after the live ones move.
"""

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from research import vrp_close_calibration as cal  # noqa: E402


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("cutoff", [1.2, 1.4, 1.8, 2.0])
def test_matched_cutoff_admits_the_same_number_of_events(seed, cutoff):
    rng = random.Random(seed)
    intraday = [rng.uniform(0.5, 6.0) for _ in range(500)]
    close = [v / rng.uniform(1.1, 2.2) for v in intraday]
    k = sum(1 for v in intraday if v >= cutoff)
    assert sum(1 for v in close if v >= cal.matched_cutoff(intraday, close, cutoff)) == k


def test_matched_cutoff_is_none_when_nothing_clears():
    assert cal.matched_cutoff([1.0, 1.1], [0.5, 0.6], 5.0) is None


def test_share_is_a_percentage_at_or_above():
    assert cal.share([0.5, 1.0, 1.5, 2.0], 1.0) == 75.0


def test_matched_share_target_picks_the_closest_share_ties_to_the_larger():
    shares = {2.00: 60.0, 2.10: 71.0, 2.20: 73.0, 2.30: 69.0}
    assert cal.matched_share_target(72.0, shares) == 2.20      # |73-72| = |71-72|: larger wins
    assert cal.matched_share_target(69.5, shares) == 2.30


@pytest.mark.parametrize("n,today_crush,prop_crush,today_share,prop_share,expected", [
    (246, 87.7, 87.7, 72.0, 72.0, (True, True, True)),
    (199, 87.7, 90.0, 72.0, 72.0, (False, True, True)),
    (246, 87.7, 87.6, 72.0, 72.0, (True, False, True)),
    (246, 87.7, 90.0, 72.0, 67.0, (True, True, True)),
    (246, 87.7, 90.0, 72.0, 66.9, (True, True, False)),
])
def test_gate_is_the_preregistered_one(n, today_crush, prop_crush, today_share, prop_share, expected):
    assert cal.gate(n, today_crush, prop_crush, today_share, prop_share) == expected


def test_the_intraday_constants_are_frozen_literals():
    """Imported live constants would re-map the close ladder as intraday after the switch."""
    assert cal.INTRADAY_TIERS == (("VRP_MARGINAL_FROM", 1.2), ("VRP_GOOD_FROM", 1.4),
                                  ("VRP_EXCELLENT_FROM", 1.8), ("target_vrp", 2.0))
    assert cal.INTRADAY_SCORE_TARGET == 4.0
