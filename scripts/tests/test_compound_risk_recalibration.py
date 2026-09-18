import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from research import compound_risk_recalibration as crr  # noqa: E402
from research.compound_risk_recalibration import FULL, NEITHER, PARTIAL  # noqa: E402

from common.constants import FCST_ERN_IV_THRESHOLD  # noqa: E402
from src.domain.enums import DirectionalBias  # noqa: E402
from src.domain.types import SizingContext  # noqa: E402

ET = crr.ET


# --- definitions: the engine's ---------------------------------------------------------

@pytest.mark.parametrize("trr", [None, "LOW", "NORMAL", "HIGH"])
@pytest.mark.parametrize("alarm", [False, True])
@pytest.mark.parametrize("bias", [None] + [b.value for b in DirectionalBias])
def test_groups_are_sizing_contexts_own_verdict(trr, alarm, bias):
    """Every input combination: NEITHER exactly when the engine sees no compound risk,
    FULL exactly when it does and the fused skew is bearish."""
    ctx = SizingContext(trr_level=trr,
                        fcst_ern_iv_effect=FCST_ERN_IV_THRESHOLD if alarm else None,
                        fused_bias=DirectionalBias(bias) if bias else None)
    group = crr.classify(trr, alarm, bias)
    assert (group == NEITHER) == (not ctx.compound_risk_active)
    assert (group == FULL) == (ctx.compound_risk_active and ctx.bearish_fused_skew)


@pytest.mark.parametrize("trr,alarm,bias,expected", [
    ("HIGH", False, "bearish", FULL),
    ("HIGH", False, "strong_bearish", FULL),
    (None, True, "bearish", FULL),               # alarm + bearish skew
    ("HIGH", True, "strong_bearish", FULL),      # all three
    ("HIGH", True, "neutral", PARTIAL),          # TRR HIGH + alarm, skew not bearish
    ("HIGH", True, "weak_bearish", PARTIAL),     # weak_bearish is not "bearish fused skew"
    ("HIGH", False, "weak_bearish", NEITHER),
    ("HIGH", False, "neutral", NEITHER),
    ("NORMAL", False, "strong_bearish", NEITHER),
    (None, True, None, NEITHER),
])
def test_claude_md_table_cases(trr, alarm, bias, expected):
    assert crr.classify(trr, alarm, bias) == expected


def test_a_stored_alarm_reaches_the_engine_as_an_alarm():
    assert SizingContext(fcst_ern_iv_effect=FCST_ERN_IV_THRESHOLD).iv_effect_reduction


# --- the minimum N ---------------------------------------------------------------------

def test_power_calculation_hand_checked():
    """44% vs 80%, one-sided alpha 0.0125, power 0.80:
    z_a = 2.2414, z_b = 0.8416, pbar = 0.62
    (2.2414 * sqrt(2 * .62 * .38) + 0.8416 * sqrt(.44 * .56 + .80 * .20))^2 / .36^2
    = (1.5386 + 0.5365)^2 / 0.1296 = 33.2 -> 34
    """
    assert crr.required_n_per_group(0.44, 0.80, 0.0125, 0.80) == 34


def test_min_n_is_the_declared_power_calculation():
    assert crr.MIN_N_PER_GROUP == crr.required_n_per_group(
        crr.P_FULL_PLANNING, crr.P_NEITHER_PLANNING,
        crr.ALPHA_ONE_SIDED / crr.HOLM_TESTS, crr.POWER)
    assert crr.MIN_N_PER_GROUP == 68


def test_a_smaller_gap_needs_more_rows():
    assert crr.required_n_per_group(0.44, 0.60, 0.0125, 0.8) > \
        crr.required_n_per_group(0.44, 0.70, 0.0125, 0.8)


def test_no_sample_detects_a_zero_difference():
    with pytest.raises(ValueError):
        crr.required_n_per_group(0.5, 0.5, 0.025, 0.8)


# --- gating ----------------------------------------------------------------------------

def _graded(group, n, crush_every=1, trr=None, alarm=False):
    return [{"group": group, "crush": 1 if i % crush_every == 0 else 0, "gap": 5.0,
             "trr_level": trr, "sizing_alarm": alarm} for i in range(n)]


def test_below_min_n_prints_counts_and_no_rate():
    n = crr.MIN_N_PER_GROUP - 1
    v = crr.verdict(_graded(FULL, n) + _graded(NEITHER, 500), n_boot=100)
    full = v["groups"][FULL]
    assert full == {"n": n, "sufficient": False}
    assert v["tests"][FULL] == {"decision": "INSUFFICIENT"}


def test_neither_below_min_n_blocks_every_verdict():
    v = crr.verdict(_graded(FULL, 500) + _graded(NEITHER, crr.MIN_N_PER_GROUP - 1), n_boot=100)
    assert v["tests"][FULL]["decision"] == "INSUFFICIENT"
    assert "crush_pct" not in v["groups"][NEITHER]


def test_empty_sample_is_insufficient_everywhere():
    v = crr.verdict([], n_boot=10)
    assert all(t == {"decision": "INSUFFICIENT"} for t in v["tests"].values())
    assert all(g["n"] == 0 for g in v["groups"].values())


def test_full_that_crushes_far_less_reads_lower():
    n = crr.MIN_N_PER_GROUP
    # FULL crushes 1 in 3, NEITHER always
    v = crr.verdict(_graded(FULL, n, crush_every=3) + _graded(NEITHER, 300), n_boot=2000)
    t = v["tests"][FULL]
    assert t["decision"] == "LOWER"
    assert t["gap_pts"] > 60
    # PARTIAL is unevaluable, keeps its Holm seat at p = 1
    assert v["tests"][PARTIAL] == {"decision": "INSUFFICIENT"}
    assert t["p_holm"] == pytest.approx(min(1.0, 2 * t["p"]))


def test_full_that_crushes_like_neither_is_not_separable():
    n = crr.MIN_N_PER_GROUP
    v = crr.verdict(_graded(FULL, n, crush_every=2) + _graded(NEITHER, 300, crush_every=2),
                    n_boot=2000)
    assert v["tests"][FULL]["decision"] == "NOT SEPARABLE"


# --- sample construction ---------------------------------------------------------------

def _history(ticker="AAA"):
    """12 prior quarters (one 20% gap: TRR HIGH), then the reaction on 2026-10-22 (AMC)."""
    rows = [{"earnings_date": date(2023, 7, 20) + timedelta(days=91 * i),
             "gap": 20.0 if i == 5 else 2.0, "cm": 3.0, "timing": "AMC"} for i in range(12)]
    rows.append({"earnings_date": date(2026, 10, 22), "gap": -4.0, "cm": -6.0, "timing": "AMC"})
    return {ticker: rows}


def _pred(ts, bias="bearish", alarm=False, ticker="AAA", ed=date(2026, 10, 22)):
    return {"ticker": ticker, "earnings_date": ed, "predicted_at": ts,
            "fused_bias": bias, "directional_bias": "neutral", "sizing_alarm": alarm}


def _analysis(ts, impl, ticker="AAA", ed=date(2026, 10, 22)):
    return {"timestamp": ts, "earnings_date": ed, "impl": impl}


def test_graded_row_carries_group_crush_and_gap():
    ts = datetime(2026, 10, 22, 14, 0, tzinfo=ET)          # AMC: pre-open until 10-23 09:30
    s = crr.build_sample([_pred(ts)], _history(), {"AAA": [_analysis(ts + timedelta(minutes=2), 7.5)]})
    [e] = s["graded"]
    assert e["trr_level"] == "HIGH"
    assert e["group"] == FULL
    assert e["crush"] == 1                                   # 7.5 > |-6.0|
    assert e["gap"] == pytest.approx(4.0)


def test_the_reactions_own_move_is_not_in_its_trr():
    """Twelve flat 2% gaps before a 90% reaction gap: TRR 1.0 (LOW) from the prior
    quarters, 90 / 9.33 = 9.6 (HIGH) if the reaction leaked into its own window."""
    hist = _history()
    hist["AAA"][5]["gap"] = 2.0
    hist["AAA"][-1]["gap"] = 90.0
    ts = datetime(2026, 10, 22, 14, 0, tzinfo=ET)
    s = crr.build_sample([_pred(ts, bias="neutral")], hist, {"AAA": [_analysis(ts, 7.5)]})
    assert s["graded"][0]["trr_level"] == "LOW"


def test_post_open_prediction_is_not_graded():
    ts = datetime(2026, 10, 23, 10, 0, tzinfo=ET)           # after the AMC reaction opened
    s = crr.build_sample([_pred(ts)], _history(), {"AAA": [_analysis(ts - timedelta(hours=20), 7.5)]})
    assert not s["graded"] and len(s["post_open"]) == 1


def test_no_reaction_yet_is_pending():
    ts = datetime(2026, 12, 1, 12, 0, tzinfo=ET)
    s = crr.build_sample([_pred(ts, ed=date(2027, 1, 20))], _history(), {})
    assert len(s["pending"]) == 1 and not s["graded"]


def test_no_pre_open_analysis_is_not_graded():
    ts = datetime(2026, 10, 22, 14, 0, tzinfo=ET)
    late = _analysis(datetime(2026, 10, 23, 11, 0, tzinfo=ET), 3.0)
    s = crr.build_sample([_pred(ts)], _history(), {"AAA": [late]})
    assert not s["graded"] and len(s["no_implied"]) == 1


def test_implied_move_comes_from_the_analysis_nearest_the_prediction():
    ts = datetime(2026, 10, 22, 14, 0, tzinfo=ET)
    analyses = {"AAA": [_analysis(ts - timedelta(days=3), 5.0),
                        _analysis(ts + timedelta(minutes=1), 7.5),
                        _analysis(ts + timedelta(hours=4), 9.0)]}
    s = crr.build_sample([_pred(ts)], _history(), analyses)
    assert s["graded"][0]["impl"] == 7.5


def test_one_row_per_reaction_latest_prediction_wins():
    t1 = datetime(2026, 10, 20, 14, 0, tzinfo=ET)
    t2 = datetime(2026, 10, 22, 14, 0, tzinfo=ET)
    preds = [_pred(t1, bias="neutral", ed=date(2026, 10, 15)),   # stale calendar date
             _pred(t2, bias="bearish")]
    s = crr.build_sample(preds, _history(), {"AAA": [_analysis(t1, 7.5), _analysis(t2, 7.5)]})
    assert s["duplicates"] == 1
    [e] = s["graded"]
    assert e["fused_bias"] == "bearish"


def test_missing_fused_bias_falls_back_to_directional_bias():
    ts = datetime(2026, 10, 22, 14, 0, tzinfo=ET)
    p = _pred(ts, bias=None)
    p["directional_bias"] = "strong_bearish"
    s = crr.build_sample([p], _history(), {"AAA": [_analysis(ts, 7.5)]})
    assert s["graded"][0]["group"] == FULL


# --- forecast --------------------------------------------------------------------------

def test_forecast_weeks_to_min_n():
    graded = _graded(FULL, 2) + _graded(NEITHER, 24)          # 26 in 13 weeks = 2/week
    f = crr.forecast(graded, weeks=13, start=datetime(2026, 9, 14, tzinfo=ET), min_n=68)
    assert f["per_week"] == pytest.approx(2.0)
    weeks, when = f["groups"][FULL]["observed"]
    assert weeks == pytest.approx(68 / (2.0 * 2 / 26))        # 442 weeks
    assert when == (datetime(2026, 9, 14, tzinfo=ET) + timedelta(weeks=weeks)).date()


def test_forecast_with_no_row_in_a_group_uses_rule_of_three():
    graded = _graded(NEITHER, 30)
    f = crr.forecast(graded, weeks=10, start=datetime(2026, 9, 14, tzinfo=ET), min_n=68)
    g = f["groups"][PARTIAL]
    assert g["observed"] == (None, None)
    assert g["upper_share"] == pytest.approx(0.1)
    assert g["upper"][0] == pytest.approx(68 / (3.0 * 0.1))


def test_forecast_on_an_empty_window_never_arrives():
    f = crr.forecast([], weeks=13)
    assert all(g["observed"] == (None, None) and g["upper"] == (None, None)
               and g["ceiling"] == (None, None) for g in f["groups"].values())


def test_forecast_ceiling_ignores_skew():
    """10 TRR-HIGH rows of 40, no alarm: FULL could be at most 25% of rows under any
    classifier, PARTIAL none at all, whatever the observed skew shares."""
    graded = _graded(NEITHER, 10, trr="HIGH") + _graded(NEITHER, 30, trr="NORMAL")
    f = crr.forecast(graded, weeks=10, start=datetime(2026, 9, 14, tzinfo=ET), min_n=68)
    assert f["groups"][FULL]["ceiling_share"] == pytest.approx(0.25)
    assert f["groups"][FULL]["ceiling"][0] == pytest.approx(68 / (4.0 * 0.25))
    assert f["groups"][PARTIAL]["ceiling"] == (None, None)
    graded[0]["sizing_alarm"] = True
    f = crr.forecast(graded, weeks=10, min_n=68)
    assert f["groups"][PARTIAL]["ceiling_share"] == pytest.approx(1 / 40)
