"""Smoke tests for scoring data types."""
from src.domain.scoring.types import ScoringResult


class TestScoringResult:
    def test_all_fields(self):
        r = ScoringResult(
            overall_score=85.0,
            profitability_score=70.0,
            risk_score=30.0,
            strategy_rationale="Excellent VRP edge, high POP",
        )
        assert r.overall_score == 85.0
        assert r.profitability_score == 70.0
        assert r.risk_score == 30.0
        assert r.strategy_rationale == "Excellent VRP edge, high POP"

    def test_is_dataclass(self):
        from dataclasses import fields
        field_names = {f.name for f in fields(ScoringResult)}
        assert "overall_score" in field_names
        assert "strategy_rationale" in field_names
