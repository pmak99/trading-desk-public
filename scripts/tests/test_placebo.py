import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "2.0" / "scripts" / "research" / "placebo.py"
sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from research.placebo import shuffle_outcomes, top_k_crush  # noqa: E402


def _events(n=100):
    # VRP ascending, crush deterministically tied to VRP: a real signal.
    out = []
    for i in range(n):
        straddle = 1.0 + i * 0.1
        actual = 1.0
        out.append({
            "ticker": f"T{i}", "straddle": straddle, "actual": actual,
            "vrp": straddle, "crushed": 1 if straddle > actual else 0,
            "edge": straddle - actual,
        })
    return out


def test_shuffle_is_reproducible_for_a_given_seed():
    e = _events()
    a = [x["crushed"] for x in shuffle_outcomes(e, seed=7)]
    b = [x["crushed"] for x in shuffle_outcomes(e, seed=7)]
    assert a == b


def test_different_seeds_give_different_shuffles():
    e = _events()
    a = [x["crushed"] for x in shuffle_outcomes(e, seed=7)]
    b = [x["crushed"] for x in shuffle_outcomes(e, seed=8)]
    assert a != b


def test_shuffle_does_not_mutate_the_input():
    e = _events()
    before = [x["crushed"] for x in e]
    shuffle_outcomes(e, seed=7)
    assert [x["crushed"] for x in e] == before


def test_shuffle_preserves_the_overall_crush_rate():
    """Reassignment, not resampling: the marginal outcome distribution is
    unchanged, so any top-K difference comes from the pairing alone."""
    e = _events()
    s = shuffle_outcomes(e, seed=7)
    assert sum(x["crushed"] for x in s) == sum(x["crushed"] for x in e)


def test_shuffle_preserves_each_events_own_vrp():
    """Only outcomes move. If VRP travelled with them the ranking would be
    unchanged and the placebo would trivially match the real result."""
    e = _events()
    s = shuffle_outcomes(e, seed=7)
    assert [x["vrp"] for x in s] == [x["vrp"] for x in e]


def test_top_k_returns_the_requested_fraction():
    rate, m = top_k_crush(_events(100), "vrp", pct=20)
    assert m == 20
    assert 0.0 <= rate <= 1.0


def test_top_k_on_a_real_signal_beats_the_base_rate():
    e = _events()
    top, _ = top_k_crush(e, "vrp", pct=20)
    base = sum(x["crushed"] for x in e) / len(e)
    assert top >= base


def test_the_main_block_loads_the_intraday_baseline():
    """D9: the placebo test audits intraday's selection edge -- its numbers are
    what close's constants were calibrated to match (RESEARCH_RESULTS §7). Read
    from the AST rather than calling load_events for real: the __main__ block
    has no isolable main() and the call is a database read."""
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
