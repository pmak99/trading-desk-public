import math
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from research import small_sample_vrp as ssv  # noqa: E402


def _t_cdf(q: float, df: int, steps: int = 20000) -> float:
    """0.5 + integral of the Student t density from 0 to q, by Simpson's rule."""
    c = math.gamma((df + 1) / 2) / (math.sqrt(df * math.pi) * math.gamma(df / 2))
    f = lambda x: c * (1 + x * x / df) ** (-(df + 1) / 2)  # noqa: E731
    h = q / steps
    s = f(0) + f(q) + sum((4 if i % 2 else 2) * f(i * h) for i in range(1, steps))
    return 0.5 + s * h / 3


@pytest.mark.parametrize("df", sorted(ssv.T_QUANTILE_90))
def test_t_table_is_the_90th_percentile(df):
    assert _t_cdf(ssv.T_QUANTILE_90[df], df) == pytest.approx(ssv.UCB_CONFIDENCE, abs=2e-6)


def test_the_table_covers_the_lookback():
    # 12-quarter lookback, 2-move minimum for a standard deviation
    assert set(ssv.T_QUANTILE_90) == set(range(1, 12))


def test_ucb_hand_computed():
    """2, 4, 6, 8: mean 5, sd sqrt(20/3) = 2.581989, t(0.90, 3) = 1.637744.

    UCB = 5 + 1.637744 * 2.581989 / sqrt(4) = 7.114318; straddle 10 -> 1.405616,
    against raw VRP 10 / 5 = 2.0.
    """
    assert ssv.ucb_mean([2.0, 4.0, 6.0, 8.0]) == pytest.approx(7.114318, abs=1e-6)
    assert ssv.ucb_vrp(10.0, [2.0, 4.0, 6.0, 8.0]) == pytest.approx(1.405616, abs=1e-6)


def test_ucb_penalises_a_short_history_more():
    """Same values, three times the quarters: the bound tightens toward the mean."""
    short = ssv.ucb_mean([2.0, 4.0, 6.0, 8.0])
    long_ = ssv.ucb_mean([2.0, 4.0, 6.0, 8.0] * 3)
    assert 5.0 < long_ < short


def test_ucb_equals_the_mean_when_moves_agree():
    assert ssv.ucb_mean([3.0, 3.0, 3.0, 3.0]) == pytest.approx(3.0)


def test_ucb_refuses_what_it_cannot_bound():
    with pytest.raises(ValueError):
        ssv.ucb_mean([3.0])
    with pytest.raises(ValueError):
        ssv.ucb_mean([3.0] * 13)   # df 12: beyond the 12-quarter lookback


def test_shrunk_hand_computed():
    """(4 * 5 + 4 * 10 * 0.8) / (4 + 4) = 6.5; straddle 10 -> 1.538462."""
    assert ssv.shrunk_mean(10.0, [2.0, 4.0, 6.0, 8.0], u=0.8) == pytest.approx(6.5)
    assert ssv.shrunk_vrp(10.0, [2.0, 4.0, 6.0, 8.0], u=0.8) == pytest.approx(10 / 6.5)


def test_shrinkage_weight_follows_n_over_n_plus_k():
    # prior 10 * 1.0 = 10, sample mean 2: weight on the sample is n / (n + 4)
    for n in (4, 8, 12):
        got = ssv.shrunk_mean(10.0, [2.0] * n, u=1.0)
        w = n / (n + 4)
        assert got == pytest.approx(w * 2.0 + (1 - w) * 10.0)


def test_shrinkage_keeps_raw_order_at_one_depth():
    """Documented property: inside one history depth it cannot reorder events."""
    u = 0.7
    events = [(8.0, [2.0, 3.0, 4.0, 5.0]), (6.0, [1.0, 1.5, 2.0, 2.5]), (12.0, [6.0] * 4)]
    raw = sorted(events, key=lambda e: -e[0] / (sum(e[1]) / len(e[1])))
    shr = sorted(events, key=lambda e: -ssv.shrunk_vrp(e[0], e[1], u))
    assert raw == shr


def test_universe_prior_is_the_mean_denominator_to_straddle_ratio():
    events = [{"mean_prior": 5.0, "straddle": 10.0}, {"mean_prior": 3.0, "straddle": 3.0}]
    assert ssv.universe_prior(events) == pytest.approx(0.75)


@pytest.mark.parametrize("n,expected", [(3, None), (4, "small"), (7, "small"),
                                        (8, "control"), (12, "control")])
def test_strata_boundaries(n, expected):
    assert ssv.stratum({"prior_moves": [1.0] * n}) == expected


def test_tiers_match_the_trade_rules():
    assert [ssv.tier(v) for v in (1.19, 1.2, 1.39, 1.4, 1.79, 1.8)] == [
        "SKIP", "MARGINAL", "MARGINAL", "GOOD", "GOOD", "EXCELLENT"]


def test_auc():
    ev = [{"s": s, "crushed": c} for s, c in ((1, 0), (2, 0), (3, 1), (4, 1))]
    assert ssv.auc(ev, "s") == pytest.approx(1.0)
    for e in ev:
        e["s"] = -e["s"]
    assert ssv.auc(ev, "s") == pytest.approx(0.0)
    for e in ev:
        e["s"] = 1
    assert ssv.auc(ev, "s") == pytest.approx(0.5)


def test_top_k_breaks_ties_by_input_order():
    ev = [{"k": 1.0, "i": i} for i in range(10)]
    assert [e["i"] for e in ssv.top_k(ev, "k")] == [0, 1]


def _stats(delta, lo):
    return {"delta": delta, "lo": lo}


@pytest.mark.parametrize("small,control,p_adj,expected", [
    (_stats(3.0, 0.5), _stats(0.0, -1.0), 0.025, "ADOPT"),
    (_stats(2.9, 0.5), _stats(0.0, -1.0), 0.001, "FAIL"),          # below the effect bar
    (_stats(5.0, 1.0), _stats(0.0, -1.0), 0.026, "FAIL"),          # not significant after Holm
    (_stats(5.0, 1.0), _stats(3.0, 0.1), 0.001, "UNIVERSE-WIDE"),  # control passes too
    (_stats(5.0, 1.0), _stats(2.0, 0.1), 0.001, "ADOPT"),          # control positive but below bar
    (_stats(5.0, 1.0), _stats(-1.0, -3.0), 0.001, "HARMS CONTROL"),
    (_stats(5.0, 1.0), _stats(-1.0, -2.9), 0.001, "ADOPT"),
])
def test_decide(small, control, p_adj, expected):
    assert ssv.decide(small, control, p_adj) == expected


def _event(i, n, straddle, mean_move, crushed):
    moves = [mean_move * (0.5 + (j % 3) * 0.5) for j in range(n)]
    m = sum(moves) / n
    return {"ticker": f"T{i}", "earnings_date": date(2025, 1, 1 + i % 28),
            "straddle": straddle, "prior_moves": moves, "mean_prior": m,
            "vrp": straddle / m, "crushed": crushed, "edge": straddle - 1.0,
            "actual": 1.0}


def test_verdict_runs_end_to_end_on_synthetic_events():
    events = [_event(i, 4 + i % 9, 5.0 + i % 7, 2.0 + (i * 7) % 5, (i * 13) % 4 != 0)
              for i in range(120)]
    v = ssv.verdict(events, n_boot=200, seed=1)
    assert v["small"]["n"] + v["control"]["n"] == 120
    assert set(v["decisions"]) == {"ucb", "shrunk"}
    assert all(d in {"FAIL", "ADOPT", "UNIVERSE-WIDE", "HARMS CONTROL"}
               for d in v["decisions"].values())
    for side in ("small", "control"):
        for key in ("ucb", "shrunk"):
            d = v[side][key]
            assert d["lo"] <= d["hi"]
            assert 0.0 <= d["p_le0"] <= 1.0


def test_an_estimator_that_ranks_like_raw_has_no_effect():
    events = [_event(i, 5, 10.0, 1.0 + i, i % 2 == 0) for i in range(30)]
    for e in events:
        e["ucb"] = e["shrunk"] = e["vrp"]
    diffs = ssv.paired_bootstrap(events, n_boot=50, seed=3)
    assert set(diffs["ucb"]) == {0.0} and set(diffs["shrunk"]) == {0.0}
