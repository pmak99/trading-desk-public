"""Pure helpers of 2.0/scripts/research/ab_vrp_power.py.

No database: the loaders are the backtest's and the live evaluator's own, covered
where they live. What matters here is that the simulated decision is the live
evaluator's -- the same top-K statistic and the same MIGRATE rule -- so the power
the script prints is the power of the gate CLAUDE.md quotes, not of a lookalike.
"""

import random
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

import ab_evaluate_live as live  # noqa: E402
from research import ab_vrp_power as pw  # noqa: E402


def _events(n, seed):
    rng = random.Random(seed)
    return [{"vi": rng.random() * 4, "vc": rng.random() * 3, "crush": rng.random() < 0.7}
            for _ in range(n)]


@pytest.mark.parametrize("n,seed", [(7, 1), (40, 2), (222, 3), (301, 4)])
def test_top_k_delta_is_the_evaluators_statistic(n, seed):
    events = _events(n, seed)
    expected = 100 * (live.top_k_crush(events, "vc")[0] - live.top_k_crush(events, "vi")[0])
    got = pw.top_k_delta(np.array([e["vi"] for e in events]), np.array([e["vc"] for e in events]),
                         np.array([e["crush"] for e in events], dtype=float))
    assert float(got) == pytest.approx(expected)


@pytest.mark.parametrize("delta", [-2.0, 0.0, 2.9, 3.0, 4.5, 12.0])
@pytest.mark.parametrize("p", [0.0, 0.005, 0.0125, 0.013, 0.025, 0.03, 0.4])
def test_migrates_is_vrp_decision_past_the_floor(delta, p):
    for factor in (1, 2):
        verdict = live.vrp_decision({"delta": delta}, live.MIN_EVENTS, min(1.0, factor * p))
        assert pw.migrates(delta, p, factor) == (verdict == "MIGRATE")


def test_identical_rankings_never_migrate():
    rng = np.random.default_rng(0)
    vi = rng.random(2000)
    crush = (rng.random(2000) < 0.7).astype(float)
    decisions = pw.draw_decisions(vi, vi.copy(), crush, 300, rng, draws=20, boots=200)
    assert all(d == 0.0 for d, _ in decisions)
    assert pw.power(decisions, 1) == 0.0


def test_a_close_ranking_that_knows_the_outcome_migrates():
    rng = np.random.default_rng(0)
    crush = (rng.random(4000) < 0.6).astype(float)
    vi = rng.random(4000)                        # uninformative
    vc = crush + 0.1 * rng.random(4000)          # every crushed event ranks first
    decisions = pw.draw_decisions(vi, vc, crush, 300, rng, draws=20, boots=200)
    assert pw.power(decisions, 2) == 1.0


def test_normal_power_is_alpha_at_no_effect_and_rises_with_it():
    assert pw.normal_power(0.0, 5.0, 0.025) == pytest.approx(0.025)
    assert pw.normal_power(3.0, 6.0, 0.0125) < pw.normal_power(3.0, 1.0, 0.0125) < 1.0
    assert pw.normal_power(30.0, 1.0, 0.0125) == pytest.approx(1.0)
