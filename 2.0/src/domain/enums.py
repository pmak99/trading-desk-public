"""
Enumerations for domain concepts.
"""

from enum import Enum


class EarningsTiming(Enum):
    """When earnings are announced relative to market hours."""

    BMO = "BMO"  # Before Market Open (pre-market announcement)
    AMC = "AMC"  # After Market Close (post-market announcement)
    DMH = "DMH"  # During Market Hours (rare)
    UNKNOWN = "UNKNOWN"


class OptionType(Enum):
    """Option contract type."""

    CALL = "call"
    PUT = "put"


class Action(Enum):
    """Trading action recommendation."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    SKIP = "SKIP"


class Recommendation(Enum):
    """Quality rating for VRP opportunities."""

    EXCELLENT = "excellent"  # VRP ratio >= 2.0x
    GOOD = "good"            # VRP ratio >= 1.5x
    MARGINAL = "marginal"    # VRP ratio >= 1.2x
    SKIP = "skip"            # VRP ratio < 1.2x


class MarketState(Enum):
    """Current market state."""

    PREMARKET = "premarket"    # 4:00 AM - 9:30 AM ET
    REGULAR = "regular"        # 9:30 AM - 4:00 PM ET
    AFTERHOURS = "afterhours"  # 4:00 PM - 8:00 PM ET
    CLOSED = "closed"          # 8:00 PM - 4:00 AM ET


class ExpirationCycle(Enum):
    """Option expiration cycle type."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class SettlementType(Enum):
    """Option settlement timing."""

    AM = "AM"  # Settled at market open
    PM = "PM"  # Settled at market close
