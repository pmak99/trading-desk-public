"""The scoring-factor study must consume the shared core and the live scorer.

Two harnesses independently built their own event lists and diverged -- one of
them silently hardcoded VRP to a constant 1.4, making it unable to discriminate
between events at all while still printing plausible-looking weight
comparisons. These are cheap structural guards against that recurring.

The study moved from 2.0/scripts/ab_test_scoring_factors.py to research/ on
2026-09-14, when the never-live preset framework it took its weight dataclass
from (config/scoring_config.py, TickerScorer) was deleted.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "2.0" / "scripts" / "research" / "scoring_factors.py"

sys.path.insert(0, str(ROOT / "2.0" / "scripts"))
from research import scoring_factors as sf  # noqa: E402


def test_imports_the_shared_core():
    assert "from research.events import" in SCRIPT.read_text()


def test_does_not_reimplement_event_loading():
    assert "FROM historical_moves" not in SCRIPT.read_text(), (
        "event loading belongs in research/events.py -- a second copy is how "
        "the constant-1.4 divergence happened"
    )


def test_reports_net_edge():
    assert "net_edge" in SCRIPT.read_text(), (
        "edges must be net of execution costs; the local copy modelled none"
    )


def test_keeps_both_vrp_baselines():
    """The intraday-vs-close denominator comparison is a live experiment
    (CLAUDE.md: re-evaluate at >=300 events). Collapsing it to one series
    would silently end that experiment."""
    text = SCRIPT.read_text()
    assert 'baseline="intraday"' in text
    assert 'baseline="close"' in text
    assert "gap_aware_vrp" in text


def test_ranks_the_live_scan_score_alongside_the_weight_sets():
    """Every candidate weighting is read against what production ranks on,
    imported rather than restated (a restated copy is how the old A/B scripts
    drifted from the live scorer)."""
    text = SCRIPT.read_text()
    assert "from scripts.scan.quality_scorer import calculate_scan_quality_score" in text
    assert "src.config.scoring_config" not in text
    assert not (ROOT / "2.0" / "scripts" / "ab_test_scoring_factors.py").exists()


def test_live_score_reads_vrp_target_4_0():
    """D9: live_score must score against the frozen 4.0 target these results were
    recorded with (RESEARCH_RESULTS, CLAUDE.md "VRP denominator"), not whatever
    the live SCORE_VRP_TARGET is today -- a study's own numbers must stay
    reproducible even after the live target moves again."""
    event = {"vrp": 2.5, "straddle": 6.0}
    assert sf.live_score(event) == sf.calculate_scan_quality_score(
        {"vrp_ratio": event["vrp"], "implied_move_pct": event["straddle"],
         "liquidity_tier": sf.HISTORICAL_LIQUIDITY_TIER},
        vrp_target=4.0,
    )
