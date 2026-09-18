"""The one TRR definition every caller imports.

Until 2026-09-13 the ratio and its thresholds were written out in five places
(analyzer, compute_live_trr, store_bias_prediction, harvest_live, vrp), and
they had already drifted: harvest returned UNKNOWN for all-zero moves where the
engine returned LOW. The evidence behind CLAUDE.md §3 tiers trades with this
same function, so a threshold change moves the rule and its evidence together.
"""

from datetime import date
from pathlib import Path

from src.domain.tail_risk import (
    TRR_LOOKBACK_QUARTERS,
    tail_risk_level,
    tail_risk_ratio,
    trr_as_of,
    trr_level,
)


class TestTailRiskRatio:
    def test_max_over_mean_of_magnitudes(self):
        assert tail_risk_ratio([2.0, -2.0, 2.0, -12.0]) == 12.0 / 4.5

    def test_none_entries_are_ignored(self):
        assert tail_risk_ratio([3.0, None, 3.0, 3.0, 3.0]) == 1.0

    def test_fewer_than_four_moves_is_unknown(self):
        # max/mean can never exceed n, so below 4 quarters HIGH (>2.5) is
        # impossible or rare by arithmetic, not by evidence (audit 4.3, 2026-09-13).
        assert tail_risk_ratio([5.0]) is None
        assert tail_risk_ratio([]) is None
        assert tail_risk_ratio([None, None, None, None]) is None
        assert tail_risk_ratio([1.0, 1.0, 9.0]) is None
        assert tail_risk_ratio([1.0, 1.0, 1.0, 9.0]) == 9.0 / 3.0

    def test_all_zero_moves_is_zero_not_a_division_error(self):
        assert tail_risk_ratio([0.0, 0.0, 0.0, 0.0]) == 0.0

    def test_config_minimum_quarters_matches_the_trr_floor(self):
        """The VRP minimum and the TRR floor are one setting: .env.example and the
        config default must both say 4, so neither can drift back to 2."""
        import re

        from src.config.config import ThresholdsConfig
        from src.domain.tail_risk import TRR_MIN_QUARTERS

        assert TRR_MIN_QUARTERS == 4
        assert ThresholdsConfig().min_historical_quarters == TRR_MIN_QUARTERS
        example = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
        assert re.search(r"^MIN_HISTORICAL_QUARTERS=(\d+)", example, re.M).group(1) == str(TRR_MIN_QUARTERS)


class TestTrrLevel:
    def test_boundaries_match_claude_md(self):
        assert trr_level(2.51) == "HIGH"
        assert trr_level(2.5) == "NORMAL"
        assert trr_level(1.5) == "NORMAL"
        assert trr_level(1.49) == "LOW"

    def test_unknown_ratio_has_no_level(self):
        assert trr_level(None) is None

    def test_level_from_moves(self):
        assert tail_risk_level([2.0, 2.0, 2.0, 12.0]) == "HIGH"
        assert tail_risk_level([3.0, 4.0, 5.0, 10.0]) == "NORMAL"
        assert tail_risk_level([4.0, 5.0, 4.5, 5.5]) == "LOW"
        assert tail_risk_level([0.0, 0.0, 0.0, 0.0]) == "LOW"
        assert tail_risk_level([5.0, 5.0, 5.0]) is None


class TestTrrAsOf:
    """TRR as the engine would have computed it before a given event."""

    def test_uses_only_events_before_the_date(self):
        history = [(date(2024, 10, 1), 2.0), (date(2025, 1, 1), 2.0), (date(2025, 4, 1), 2.0),
                   (date(2025, 6, 1), 2.0), (date(2025, 7, 1), 20.0)]
        # The July move is the outcome being studied, not an input to it.
        assert trr_as_of(history, date(2025, 7, 1)) == 1.0

    def test_takes_the_most_recent_lookback_quarters(self):
        old = [(date(2010 + i, 1, 1), 50.0 if i == 0 else 1.0) for i in range(20)]
        assert TRR_LOOKBACK_QUARTERS == 12
        # The 50% outlier is 19 events back, outside the 12-event window.
        assert trr_as_of(old, date(2031, 1, 1)) == 1.0

    def test_order_of_history_does_not_matter(self):
        history = [(date(2025, 7, 1), 20.0), (date(2025, 1, 1), 1.0),
                   (date(2025, 4, 1), 1.0), (date(2024, 10, 1), 1.0)]
        assert trr_as_of(history, date(2026, 1, 1)) == 20.0 / (23.0 / 4)

    def test_too_little_history_is_unknown(self):
        assert trr_as_of([(date(2025, 1, 1), 3.0)], date(2025, 6, 1)) is None


def test_no_module_writes_out_its_own_tier_ladder():
    """A comparison against 2.5 or 1.5 followed by a tier name is a sixth copy."""
    import re

    repo = Path(__file__).resolve().parents[3]
    ladder = re.compile(r"(>|>=)\s*(2\.5|1\.5)\b")
    tier = re.compile(r"['\"](HIGH|NORMAL|LOW)['\"]")
    copies = []
    for root in ("2.0/src", "2.0/scripts", "scripts", "common"):
        for path in (repo / root).rglob("*.py"):
            if {"venv", "tests"} & set(path.parts) or path.name == "tail_risk.py":
                continue
            lines = path.read_text().splitlines()
            for i, line in enumerate(lines):
                if ladder.search(line) and tier.search(" ".join(lines[i:i + 3])):
                    copies.append(f"{path.relative_to(repo)}:{i + 1}")
    assert copies == []
