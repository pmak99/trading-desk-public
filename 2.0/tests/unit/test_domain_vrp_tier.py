"""The one VRP tier definition every caller imports.

Until 2026-09-14 the VRP ladder was written out in several places and they
disagreed: the engine and /analyze graded at 1.8/1.4/1.2 (CLAUDE.md §1), while
strategy selection branched at 2.0/1.5 and the rationale text called 2.0
"excellent" -- so VRP 1.9 was EXCELLENT by tier and not by the branch or the
text. The thresholds live in src/domain/vrp_tier.py, pinned here to the §1 table.
"""

import ast
import re
from datetime import date
from pathlib import Path

import pytest

from src.domain.enums import Recommendation
from src.domain.vrp_tier import (
    VRP_EXCELLENT_FROM,
    VRP_GOOD_FROM,
    VRP_MARGINAL_FROM,
    vrp_tier,
)

REPO = Path(__file__).resolve().parents[3]
TIERS = {"EXCELLENT", "GOOD", "MARGINAL", "SKIP"}
TIER = re.compile(r"(EXCELLENT|GOOD|MARGINAL|SKIP)\b")


class TestVrpTier:
    @pytest.mark.parametrize("ratio_fn,expected", [
        (lambda: VRP_EXCELLENT_FROM + 1.0, Recommendation.EXCELLENT),
        (lambda: VRP_EXCELLENT_FROM, Recommendation.EXCELLENT),
        (lambda: VRP_EXCELLENT_FROM - 1e-4, Recommendation.GOOD),
        (lambda: VRP_GOOD_FROM, Recommendation.GOOD),
        (lambda: VRP_GOOD_FROM - 1e-4, Recommendation.MARGINAL),
        (lambda: VRP_MARGINAL_FROM, Recommendation.MARGINAL),
        (lambda: VRP_MARGINAL_FROM - 1e-4, Recommendation.SKIP),
        (lambda: 0.0, Recommendation.SKIP),
    ])
    def test_boundaries(self, ratio_fn, expected):
        assert vrp_tier(ratio_fn()) is expected

    def test_the_ladder_is_close_to_close_calibrated(self):
        """Mapped from intraday 1.8/1.4/1.2 on live events (research/vrp_close_calibration.py)."""
        assert (VRP_EXCELLENT_FROM, VRP_GOOD_FROM, VRP_MARGINAL_FROM) == (0.99, 0.78, 0.71)


def test_engine_defaults_grade_on_the_same_thresholds():
    from src.application.metrics.vrp import VRPCalculator
    from src.config.config import ThresholdsConfig

    t = ThresholdsConfig()
    assert (t.vrp_excellent, t.vrp_good, t.vrp_marginal) == (
        VRP_EXCELLENT_FROM, VRP_GOOD_FROM, VRP_MARGINAL_FROM)
    assert VRPCalculator().thresholds == {
        "excellent": VRP_EXCELLENT_FROM, "good": VRP_GOOD_FROM, "marginal": VRP_MARGINAL_FROM,
    }


def test_environment_cannot_move_the_tiers():
    """VRP_THRESHOLD_MODE's CONSERVATIVE/AGGRESSIVE/LEGACY profiles and the
    VRP_EXCELLENT/GOOD/MARGINAL overrides let .env grade on a ladder §1 does
    not have. Nothing adjusts them live since the VIX regime factor went (2026-09-14)."""
    import os
    from unittest.mock import patch

    from src.config.config import Config

    env = {"TRADIER_API_KEY": "test", "VRP_THRESHOLD_MODE": "AGGRESSIVE",
           "VRP_EXCELLENT": "2.5", "VRP_GOOD": "2.0", "VRP_MARGINAL": "1.5"}
    with patch.dict(os.environ, env, clear=True):
        t = Config.from_env().thresholds
    assert (t.vrp_excellent, t.vrp_good, t.vrp_marginal) == (
        VRP_EXCELLENT_FROM, VRP_GOOD_FROM, VRP_MARGINAL_FROM)
    example = (REPO / "2.0/.env.example").read_text()
    assert not re.search(r"^#?\s*VRP_(THRESHOLD_MODE|EXCELLENT|GOOD|MARGINAL)\b", example, re.M)


def test_calculator_recommendation_is_the_domain_tier():
    from src.application.metrics.vrp import VRPCalculator
    from src.domain.types import HistoricalMove, ImpliedMove, Money, Percentage, Strike

    moves = [
        HistoricalMove(
            ticker="T", earnings_date=date(2025, m, 1), prev_close=Money(100.0),
            earnings_open=Money(100.0), earnings_high=Money(101.0), earnings_low=Money(99.0),
            earnings_close=Money(100.0), intraday_move_pct=Percentage(5.0),
            gap_move_pct=Percentage(5.0), close_move_pct=Percentage(5.0),
        )
        for m in (1, 4, 7, 10)
    ]
    calc = VRPCalculator(move_metric="close")
    for implied in (5.0 * (VRP_EXCELLENT_FROM + 0.1), 5.0 * (VRP_GOOD_FROM + 0.05),
                    5.0 * (VRP_MARGINAL_FROM + 0.01), 5.0 * (VRP_MARGINAL_FROM - 0.01)):
        implied_move = ImpliedMove(
            ticker="T", expiration=date(2026, 1, 16), stock_price=Money(100.0),
            atm_strike=Strike(100.0), straddle_cost=Money(implied),
            implied_move_pct=Percentage(implied), upper_bound=Money(100 + implied),
            lower_bound=Money(100 - implied),
        )
        result = calc.calculate("T", date(2026, 1, 16), implied_move, moves).value
        assert result.recommendation is vrp_tier(result.vrp_ratio), implied
        assert result.move_metric == "close"
        # The tier is reported with the quarters behind it (audit 3.3).
        assert result.historical_quarters == 4


def test_vrp_result_serializes_with_its_quarter_count():
    from src.domain.types import Percentage, VRPResult
    from src.utils.serialization import deserialize, serialize

    vrp = VRPResult(
        ticker="T", expiration=date(2026, 1, 16), implied_move_pct=Percentage(9.0),
        historical_mean_move_pct=Percentage(5.0), vrp_ratio=1.8, edge_score=1.2,
        recommendation=Recommendation.EXCELLENT, historical_quarters=7,
    )
    assert deserialize(serialize(vrp)) == vrp


def _ladder_copies(source: str) -> list:
    """Line numbers of VRP tier ladders written out in one module's code.

    Three shapes have shipped: a VRP compared with a tier threshold (bias.py's
    `vrp.vrp_ratio >= 2.0`), a ratio compared with 1.8/1.4/1.2 in a function that
    names a tier (analyze.py's vrp_tier), and a (low, high, "TIER") table
    (ab_evaluate_live's term_bands). Strings and comments are not code.
    """
    ever_used = {1.8, 1.4, 1.2, 2.0, 1.5, VRP_EXCELLENT_FROM, VRP_GOOD_FROM, VRP_MARGINAL_FROM}
    claude_md = {1.8, 1.4, 1.2, VRP_EXCELLENT_FROM, VRP_GOOD_FROM, VRP_MARGINAL_FROM}
    tree = ast.parse(source)

    def numbers(nodes):
        return {n.value for n in nodes if isinstance(n, ast.Constant)
                and isinstance(n.value, float) and n.value in ever_used}

    def names_a_tier(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and TIER.match(n.value):
                return True
            if isinstance(n, ast.Attribute) and n.attr in TIERS:
                return True
        return False

    hits = set()
    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes:
        tiered = names_a_tier(scope) if scope is not tree else False
        for node in ast.walk(scope):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                found = numbers(operands)
                if not found:
                    continue
                text = " ".join(ast.get_source_segment(source, o) or "" for o in operands)
                if "vrp" in text.lower() or (tiered and found & claude_md):
                    hits.add(node.lineno)
            elif isinstance(node, (ast.Tuple, ast.List)) and numbers(node.elts) and names_a_tier(node):
                hits.add(node.lineno)
    return sorted(hits)


def test_no_module_writes_out_its_own_vrp_ladder():
    copies = []
    for root in ("2.0/src", "2.0/scripts", "scripts", "common"):
        for path in (REPO / root).rglob("*.py"):
            if {"venv", "tests"} & set(path.parts) or path.name == "vrp_tier.py":
                continue
            copies += [f"{path.relative_to(REPO)}:{line}" for line in _ladder_copies(path.read_text())]
    assert copies == []


@pytest.mark.parametrize("source", [
    "def f(vrp):\n    if vrp.vrp_ratio >= 2.0:\n        return 1\n",
    "def f(ratio):\n    if ratio >= 1.8:\n        return 'EXCELLENT'\n    return 'SKIP'\n",
    "BANDS = ((0, 1.2, 'SKIP <1.2'), (1.2, 1.4, 'MARGINAL'))\n",
    f"def f(vrp):\n    if vrp.vrp_ratio >= {VRP_EXCELLENT_FROM}:\n        return 1\n",
])
def test_ladder_guard_sees_the_shapes_that_shipped(source):
    assert _ladder_copies(source)


def test_ladder_guard_ignores_prose_and_other_ratios():
    source = 'def g(fcst):\n    """VRP >= 1.8 is EXCELLENT."""\n    return fcst >= 2.0\n'
    assert _ladder_copies(source) == []


def test_dashboards_rebuild_no_vrp_ladder():
    """queries.sql cannot import vrp_tier, so it must not grade: tiers come from
    analysis_log.recommendation, graded at write time in its own convention."""
    sql = (REPO / "2.0/dashboards/queries.sql").read_text()
    assert not re.findall(r"vrp\w*_ratio\s*(?:>=|<=|>|<)\s*[\d.]+", sql)


def test_dashboards_rebuild_no_trr_ladder():
    """queries.sql cannot import tail_risk, so it must not tier: TRR levels come
    from the domain module. Mirrors test_dashboards_rebuild_no_vrp_ladder above,
    which guards the VRP ladder in this same file -- TRR had no such guard, and
    trading_trr_at_entry duplicates tail_risk.py's 2.5 / 1.5 today.

    trading_trr_at_entry was the one exemption until 2026-09-17, when it began
    reading rule_evidence_snapshot instead. There is now no ladder in this file
    at all, and no exemption.
    """
    sql = (REPO / "2.0/dashboards/queries.sql").read_text()
    blocks = re.split(r"(?m)^(?=-- name: )", sql)[1:]
    offenders = sorted(
        re.match(r"-- name: (\S+)", b).group(1)
        for b in blocks
        if re.search(r"trr\w*\s*(?:>=|<=|>|<)\s*[\d.]+", b)
    )
    assert not offenders, f"TRR ladder rebuilt in queries.sql: {offenders}"


def test_the_live_calculator_grades_on_close_to_close_and_env_cannot_change_it():
    import os
    from unittest.mock import patch

    from src.config.config import Config
    from src.container import Container

    with patch.dict(os.environ, {"TRADIER_API_KEY": "test", "VRP_MOVE_METRIC": "intraday"}, clear=True):
        config = Config.from_env()
    assert config.algorithms.vrp_move_metric == "close"
    assert Container(config).vrp_calculator.move_metric == "close"
    assert not re.search(r"^#?\s*VRP_MOVE_METRIC\b", (REPO / "2.0/.env.example").read_text(), re.M)
