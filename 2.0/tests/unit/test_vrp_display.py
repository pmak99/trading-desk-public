"""/analyze's VRP lines: close-to-close with its tier, intraday as a reference with no tier."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from analyze import _log_vrp_intraday_reference, _vrp_ratio_line  # noqa: E402

from src.domain.enums import Recommendation
from src.domain.types import Percentage, VRPResult
from src.domain.vrp_tier import VRP_EXCELLENT_FROM, VRP_GOOD_FROM


def _vrp(ratio, quarters=None):
    return VRPResult(ticker="T", expiration=date(2026, 10, 16), implied_move_pct=Percentage(5.0),
                     historical_mean_move_pct=Percentage(5.0 / ratio), vrp_ratio=ratio,
                     edge_score=0.5, recommendation=Recommendation.GOOD,
                     historical_quarters=quarters, move_metric="close")


def test_the_graded_line_names_close_to_close_and_its_tier():
    good = round((VRP_GOOD_FROM + VRP_EXCELLENT_FROM) / 2, 2)
    assert _vrp_ratio_line("(close-to-close)", _vrp(good, 12)) == \
        f"VRP Ratio (close-to-close): {good:.2f}x  [GOOD · 12 quarters]"


def test_the_quarter_count_is_omitted_when_unknown():
    assert _vrp_ratio_line("(close-to-close)", _vrp(VRP_EXCELLENT_FROM + 0.5)).endswith("[EXCELLENT]")


def test_the_intraday_reference_prints_no_tier():
    lines = []
    _log_vrp_intraday_reference(lines.append, 2.28, 2.2)
    assert lines == ["  VRP Ratio (intraday, reference only): 2.28x",
                     "  Historical Mean (intraday): 2.2%"]
    assert not any(t in " ".join(lines) for t in ("EXCELLENT", "GOOD", "MARGINAL", "SKIP"))
