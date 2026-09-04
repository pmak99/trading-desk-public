"""Base handler for scan modes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from src.container import Container


@dataclass
class ScanResult:
    """Result from analyzing a ticker."""

    ticker: str
    ticker_name: Optional[str]
    earnings_date: str
    expiration_date: str
    stock_price: float
    implied_move_pct: str
    historical_mean_pct: Optional[str] = None
    vrp_ratio: Optional[float] = None
    edge_score: Optional[float] = None
    recommendation: Optional[str] = None
    is_tradeable: bool = False
    liquidity_tier: str = "UNKNOWN"
    directional_bias: str = "NEUTRAL"
    quality_score: float = 0.0
    status: str = "PENDING"


class ScanHandler(ABC):
    """Abstract base class for scan mode handlers."""

    def __init__(self, container: Container):
        self.container = container

    @abstractmethod
    def execute(self) -> List[ScanResult]:
        """Execute the scan and return results."""
        pass

    @abstractmethod
    def display_results(self, results: List[ScanResult]) -> None:
        """Display results to user."""
        pass
