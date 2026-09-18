import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "2.0" / "scripts" / "research" / "percentile_vrp.py"
sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from research.percentile_vrp import percentile_rank, resolution_pts  # noqa: E402


def test_implied_above_every_prior_move_is_the_top_percentile():
    assert percentile_rank(10.0, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)


def test_implied_below_every_prior_move_is_the_bottom_percentile():
    assert percentile_rank(0.5, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(0.0)


def test_midpoint_implied_is_the_middle_percentile():
    assert percentile_rank(2.5, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(0.5)


def test_percentile_is_invariant_to_a_fat_tail_outlier():
    """The whole point: one blowup quarter must not move the estimate.

    A mean denominator is dragged upward by the 40% quarter; a percentile
    only cares that 3 of 4 prior moves are below the implied move.
    """
    tight = percentile_rank(3.5, [1.0, 2.0, 3.0, 4.0])
    fat = percentile_rank(3.5, [1.0, 2.0, 3.0, 40.0])
    assert tight == fat


def test_ratio_vrp_is_NOT_invariant_to_the_same_outlier():
    """Contrast case, so the test above is meaningful rather than trivially
    true. The ratio metric this challenges moves a lot on the same data."""
    tight = 3.5 / (sum([1.0, 2.0, 3.0, 4.0]) / 4)
    fat = 3.5 / (sum([1.0, 2.0, 3.0, 40.0]) / 4)
    assert abs(tight - fat) > 1.0


def test_resolution_reflects_sample_size():
    assert resolution_pts(12) == pytest.approx(100 / 12)
    assert resolution_pts(4) > resolution_pts(12)


def test_empty_history_is_rejected():
    with pytest.raises(ValueError):
        percentile_rank(1.0, [])


def test_non_positive_n_is_rejected():
    with pytest.raises(ValueError):
        resolution_pts(0)


def test_the_main_block_loads_the_intraday_baseline():
    """D9: the percentile-VRP study audits intraday's selection edge -- its
    numbers are what close's constants were calibrated to match (RESEARCH_RESULTS
    §7). Read from the AST rather than calling load_events for real: the
    __main__ block has no isolable main() and the call is a database read."""
    tree = ast.parse(SCRIPT.read_text())
    baselines = [
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "load_events"
        for kw in node.keywords
        if kw.arg == "baseline"
    ]
    assert baselines == ["intraday"]
