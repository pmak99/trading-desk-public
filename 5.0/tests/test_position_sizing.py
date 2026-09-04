# 5.0/tests/test_position_sizing.py
import pytest
from src.domain.position_sizing import calculate_position_size, half_kelly

def test_half_kelly_formula():
    """Half-Kelly = 0.5 * (bp - q) / b where b=odds, p=win_rate, q=1-p."""
    # Win rate 60%, risk/reward 1:2 (lose $1 to win $2)
    fraction = half_kelly(win_rate=0.60, risk_reward=0.5)
    # Kelly = (0.6 * 2 - 0.4) / 2 = 0.8 / 2 = 0.4
    # Half-Kelly = 0.2
    assert abs(fraction - 0.20) < 0.01

def test_half_kelly_negative_edge():
    """Negative edge returns 0 (don't trade)."""
    fraction = half_kelly(win_rate=0.30, risk_reward=2.0)
    assert fraction == 0.0

def test_calculate_position_size_basic():
    """Calculate contracts based on account and risk."""
    size = calculate_position_size(
        account_value=100000,
        max_risk_per_contract=500,
        win_rate=0.60,
        risk_reward=0.5,
    )
    # Half-Kelly ~0.2, so risk $20k, at $500/contract = 40 contracts
    # But capped at max 5% of account = 10 contracts
    assert size <= 20  # Reasonable cap

def test_calculate_position_size_respects_max():
    """Position size respects maximum percentage."""
    size = calculate_position_size(
        account_value=100000,
        max_risk_per_contract=100,
        win_rate=0.60,
        risk_reward=0.5,
        max_position_pct=0.02,  # 2% max
    )
    # 2% of $100k = $2k risk, at $100/contract = 20 contracts max
    assert size <= 20

def test_calculate_position_size_minimum():
    """Always returns at least 1 if edge exists."""
    size = calculate_position_size(
        account_value=10000,
        max_risk_per_contract=5000,
        win_rate=0.55,
        risk_reward=1.0,
    )
    assert size >= 1


# ── Compound risk cap (Jul 2026 — parity with 2.0 SizingContext) ──────────

from src.domain.position_sizing import apply_compound_risk_cap


def test_compound_cap_high_trr_plus_bearish():
    pl = {"max_contracts": 50, "max_notional": 25000}
    out = apply_compound_risk_cap(pl, "HIGH", "bearish")
    assert out["max_contracts"] == 25
    assert out["compound_risk_active"] is True


def test_compound_cap_strong_bearish_also_fires():
    out = apply_compound_risk_cap({"max_contracts": 50}, "HIGH", "strong_bearish")
    assert out["max_contracts"] == 25


def test_compound_cap_weak_bearish_does_not_fire():
    out = apply_compound_risk_cap({"max_contracts": 50}, "HIGH", "weak_bearish")
    assert out["max_contracts"] == 50
    assert out["compound_risk_active"] is False


def test_compound_cap_normal_trr_bearish_no_cap():
    out = apply_compound_risk_cap({"max_contracts": 100}, "NORMAL", "bearish")
    assert out["max_contracts"] == 100
    assert out["compound_risk_active"] is False


def test_compound_cap_never_raises_existing_limit():
    # Frozen DB row with a tighter limit stays tighter
    out = apply_compound_risk_cap({"max_contracts": 10}, "HIGH", "bearish")
    assert out["max_contracts"] == 10
    assert out["compound_risk_active"] is True


def test_compound_cap_none_position_limits_passthrough():
    assert apply_compound_risk_cap(None, "HIGH", "bearish") is None


def test_compound_cap_none_bias_no_cap():
    out = apply_compound_risk_cap({"max_contracts": 50}, "HIGH", None)
    assert out["max_contracts"] == 50
    assert out["compound_risk_active"] is False


# ── max_contracts_cap wiring (Jul 27 2026) ──────────────────────────────
# Fixed: position_limits["max_contracts"] (TRR/compound-risk caps, computed
# correctly above) was never actually passed into calculate_position_size --
# a HIGH-TRR or compound-risk position could size above its own displayed
# cap, bounded only by max_position_pct (5% of account by default).


def test_max_contracts_cap_binds_when_lower_than_kelly():
    # Kelly/max_position_pct alone would allow well above 25 here.
    size = calculate_position_size(
        account_value=1_000_000,
        max_risk_per_contract=100,
        win_rate=0.60,
        risk_reward=0.5,
        max_contracts_cap=25,
    )
    assert size == 25


def test_max_contracts_cap_does_not_raise_below_kelly_result():
    # Cap higher than what Kelly/max_position_pct would produce -- cap must
    # not inflate the result upward.
    size = calculate_position_size(
        account_value=10_000,
        max_risk_per_contract=5000,
        win_rate=0.55,
        risk_reward=1.0,
        max_contracts_cap=100,
    )
    assert size < 100


def test_no_cap_preserves_prior_behavior():
    # Default (None) must behave exactly as before this fix.
    with_cap_none = calculate_position_size(
        account_value=100000, max_risk_per_contract=500,
        win_rate=0.60, risk_reward=0.5, max_contracts_cap=None,
    )
    without_cap_arg = calculate_position_size(
        account_value=100000, max_risk_per_contract=500,
        win_rate=0.60, risk_reward=0.5,
    )
    assert with_cap_none == without_cap_arg


def test_max_contracts_cap_applies_after_minimum_floor():
    # A cap below min_contracts would be a contradiction (0 vs min 1) --
    # cap wins, since it represents a hard risk ceiling, not a suggestion.
    size = calculate_position_size(
        account_value=100000, max_risk_per_contract=100,
        win_rate=0.60, risk_reward=0.5,
        min_contracts=5, max_contracts_cap=2,
    )
    assert size == 2


# ── VRP/liquidity 50% reduction (Rule 4, Jul 27 2026) ───────────────────
# Parity with 2.0's SizingContext.vrp_marginal / liquidity_warning. Fixed
# alongside the missing cap wiring above and the missing tradeable gate in
# analyze.py -- this rule existed only in documentation before.

from src.domain.position_sizing import apply_vrp_liquidity_reduction, is_tradeable_tier


def test_vrp_marginal_halves_max_contracts():
    out = apply_vrp_liquidity_reduction({"max_contracts": 100}, "MARGINAL", "GOOD")
    assert out["max_contracts"] == 50
    assert out["vrp_liquidity_reduction_active"] is True


def test_liquidity_warning_halves_max_contracts():
    out = apply_vrp_liquidity_reduction({"max_contracts": 100}, "EXCELLENT", "WARNING")
    assert out["max_contracts"] == 50
    assert out["vrp_liquidity_reduction_active"] is True


def test_neither_condition_no_reduction():
    out = apply_vrp_liquidity_reduction({"max_contracts": 100}, "EXCELLENT", "GOOD")
    assert out["max_contracts"] == 100
    assert out["vrp_liquidity_reduction_active"] is False


def test_both_conditions_single_reduction_not_stacked():
    out = apply_vrp_liquidity_reduction({"max_contracts": 100}, "MARGINAL", "WARNING")
    assert out["max_contracts"] == 50  # not 25


def test_vrp_reduction_skipped_when_compound_risk_already_active():
    # Rules 1-2 (compound risk) are first-match-wins per CLAUDE.md --
    # Rule 4 must not further reduce an already-tighter compound cap.
    pl = {"max_contracts": 25, "compound_risk_active": True}
    out = apply_vrp_liquidity_reduction(pl, "MARGINAL", "WARNING")
    assert out["max_contracts"] == 25  # unchanged, not 12


def test_vrp_reduction_combines_with_trr_high_cap():
    # TRR HIGH alone caps at 50 (compound risk NOT active); MARGINAL VRP
    # then halves that -> 25. Sequential, not the compound-risk path.
    pl = {"max_contracts": 50, "compound_risk_active": False}
    out = apply_vrp_liquidity_reduction(pl, "MARGINAL", "GOOD")
    assert out["max_contracts"] == 25


def test_vrp_reduction_none_passthrough():
    assert apply_vrp_liquidity_reduction(None, "MARGINAL", "WARNING") is None


def test_vrp_reduction_missing_max_contracts_does_not_crash():
    out = apply_vrp_liquidity_reduction({}, "MARGINAL", "GOOD")
    assert out["vrp_liquidity_reduction_active"] is True
    assert "max_contracts" not in out  # nothing to halve, left absent


def test_is_tradeable_tier_excellent_good_marginal_true():
    assert is_tradeable_tier("EXCELLENT") is True
    assert is_tradeable_tier("GOOD") is True
    assert is_tradeable_tier("MARGINAL") is True


def test_is_tradeable_tier_skip_false():
    assert is_tradeable_tier("SKIP") is False


def test_is_tradeable_tier_unknown_value_false():
    assert is_tradeable_tier("garbage") is False
    assert is_tradeable_tier(None) is False
