"""Trade and Strategy dataclasses for the Fidelity journal parser."""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class Trade:
    """Represents a single closed trade"""
    symbol: str
    description: str
    quantity: int
    acquired_date: Optional[str]
    sale_date: str
    cost_basis: float
    proceeds: float
    gain_loss: float
    term: str  # SHORT or LONG
    wash_sale_amount: float = 0.0

    # Parsed option details
    option_type: Optional[str] = None  # PUT or CALL
    strike: Optional[float] = None
    expiration: Optional[str] = None
    underlying: Optional[str] = None

    # VRP correlation (populated later)
    earnings_date: Optional[str] = None
    vrp_ratio: Optional[float] = None
    implied_move: Optional[float] = None
    actual_move: Optional[float] = None
    beat_implied: Optional[bool] = None

    @property
    def is_option(self) -> bool:
        return self.option_type is not None

    @property
    def is_winner(self) -> bool:
        return self.gain_loss > 0

    @property
    def days_held(self) -> Optional[int]:
        if not self.acquired_date or not self.sale_date:
            return None
        try:
            acq = datetime.strptime(self.acquired_date, '%Y-%m-%d')
            sale = datetime.strptime(self.sale_date, '%Y-%m-%d')
            return (sale - acq).days
        except (ValueError, TypeError):
            return None

    @property
    def close_date(self) -> Optional[str]:
        # Credit trades: Fidelity inverts dates — acquired > sale means acquired is the close
        if self.acquired_date and self.sale_date and self.acquired_date > self.sale_date:
            return self.acquired_date
        return self.sale_date


@dataclass
class Strategy:
    """Represents a grouped multi-leg strategy for in-memory reporting only.

    strategy_type values: SINGLE, SPREAD, STOCK.
    STOCK is not DB-compatible (violates strategies CHECK constraint) — never
    pass Strategy objects directly to create_strategy_and_link or any DB write.
    """
    symbol: str
    strategy_type: str  # SINGLE, SPREAD, STOCK
    option_type: Optional[str]
    expiration: Optional[str]
    gain_loss: float
    legs: List['Trade']
    earnings_date: Optional[str] = None
    actual_move: Optional[float] = None

    @property
    def is_winner(self) -> bool:
        return self.gain_loss > 0

    @property
    def close_date(self) -> Optional[str]:
        dates = [l.close_date for l in self.legs if l.close_date]
        return max(dates) if dates else None
