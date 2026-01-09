#!/usr/bin/env python3
"""
Strategy grouping logic for multi-leg options trades.

Groups individual trade legs into strategies (SINGLE, SPREAD, IRON_CONDOR)
based on matching criteria: symbol, dates, and expiration.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any, Protocol
from collections import defaultdict


class Confidence(Enum):
    """Confidence level for auto-detected groupings."""
    HIGH = "high"      # All criteria match, standard leg count
    MEDIUM = "medium"  # Most criteria match, might need review
    LOW = "low"        # Only some criteria match, needs review


class TradeLeg(Protocol):
    """Protocol for trade leg objects."""
    id: int
    symbol: str
    acquired_date: str
    sale_date: str
    expiration: Optional[str]
    option_type: Optional[str]
    strike: Optional[float]
    gain_loss: float


@dataclass
class StrategyGroup:
    """A group of legs that form a single strategy."""
    legs: List[Any]
    strategy_type: Optional[str]  # SINGLE, SPREAD, IRON_CONDOR, or None if unknown
    confidence: Confidence
    needs_review: bool = False

    @property
    def combined_pnl(self) -> float:
        """Sum of all leg P&Ls."""
        return sum(leg.gain_loss for leg in self.legs)

    @property
    def is_winner(self) -> bool:
        """Strategy is a winner if combined P&L is positive."""
        return self.combined_pnl > 0

    @property
    def symbol(self) -> str:
        """Symbol from first leg."""
        return self.legs[0].symbol if self.legs else ""

    @property
    def acquired_date(self) -> str:
        """Acquired date from first leg."""
        return self.legs[0].acquired_date if self.legs else ""

    @property
    def sale_date(self) -> str:
        """Sale date from first leg."""
        return self.legs[0].sale_date if self.legs else ""

    @property
    def expiration(self) -> Optional[str]:
        """Expiration from first leg."""
        return self.legs[0].expiration if self.legs else None


def classify_strategy_type(leg_count: int) -> Optional[str]:
    """
    Classify strategy type based on number of legs.

    Args:
        leg_count: Number of legs in the strategy

    Returns:
        Strategy type string or None if unknown
    """
    if leg_count == 1:
        return "SINGLE"
    elif leg_count == 2:
        return "SPREAD"
    elif leg_count == 4:
        return "IRON_CONDOR"
    else:
        return None  # 3, 5+ legs need manual review


def _make_grouping_key(leg: Any) -> tuple:
    """Create grouping key from leg attributes."""
    return (
        leg.symbol,
        leg.acquired_date,
        leg.sale_date,
        leg.expiration,
    )


def group_legs_into_strategies(legs: List[Any]) -> List[StrategyGroup]:
    """
    Group trade legs into strategies based on matching criteria.

    Grouping criteria (ALL must match for HIGH confidence):
    - Same symbol
    - Same acquired_date
    - Same sale_date
    - Same expiration

    Args:
        legs: List of trade leg objects with required attributes

    Returns:
        List of StrategyGroup objects
    """
    if not legs:
        return []

    # Group by key
    groups_by_key: Dict[tuple, List[Any]] = defaultdict(list)
    for leg in legs:
        key = _make_grouping_key(leg)
        groups_by_key[key].append(leg)

    # Convert to StrategyGroup objects
    result = []
    for key, group_legs in groups_by_key.items():
        leg_count = len(group_legs)
        strategy_type = classify_strategy_type(leg_count)

        if strategy_type is not None:
            # Known strategy type
            confidence = Confidence.HIGH
            needs_review = False
        else:
            # Unknown leg count (3, 5+)
            confidence = Confidence.LOW
            needs_review = True

        result.append(StrategyGroup(
            legs=group_legs,
            strategy_type=strategy_type,
            confidence=confidence,
            needs_review=needs_review,
        ))

    return result
