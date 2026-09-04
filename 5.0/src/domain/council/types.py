"""Council dataclasses."""

from dataclasses import dataclass, field
from typing import Dict, Any, List


@dataclass
class CouncilMember:
    """Single council member result."""
    name: str
    weight: float
    score: float = 0.0       # -1.0 to +1.0
    direction: str = "neutral"
    status: str = ""          # "fresh", "cached", "33 analysts", etc.
    failed: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CouncilResult:
    """Full council consensus result."""
    ticker: str
    earnings_date: str
    timing: str
    price: float
    members: List[CouncilMember]
    consensus_score: float
    consensus_direction: str
    agreement: str            # HIGH/MEDIUM/LOW
    agreement_count: int
    active_count: int
    modifier: float
    base_score: float         # 2.0 score
    final_score: float        # 4.0 score
    direction: str            # final from 3-rule system
    skew_bias: str
    rule_applied: str
    tail_risk: Dict[str, Any]
    risk_flags: List[str]
    status: str               # "success" or "insufficient_data"
