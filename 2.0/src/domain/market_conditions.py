"""
Market conditions: the VIX level and its regime at one moment.

A value, not behaviour. MarketConditionsAnalyzer
(src/application/metrics/market_conditions.py) fetches and caches it;
analysis_log's writer (src/infrastructure/database/repositories/analysis_repository.py)
stores it. It lived beside the analyzer until 2026-09-14, which made the
infrastructure layer import the application layer to name the type (audit 5.2).

Regime boundaries: src/domain/vix_regime.py.
"""

from dataclasses import dataclass
from datetime import datetime

from src.domain.types import Percentage


@dataclass(frozen=True)
class MarketConditions:
    """
    Current market conditions and volatility regime.

    Attributes:
        vix_level: Current VIX level (%)
        regime: Volatility regime classification
        analysis_time: When conditions were assessed

    Readers use vix_level and regime only (analysis_log, the scan's VIX line).
    A 0-100 regime_score and a level-based "trend" were computed until
    2026-09-14 and read by nothing.
    """
    vix_level: Percentage
    regime: str
    analysis_time: datetime
