"""Scoring result data type."""
from dataclasses import dataclass


@dataclass
class ScoringResult:
    """Result of scoring a single strategy."""
    overall_score: float
    profitability_score: float
    risk_score: float
    strategy_rationale: str
