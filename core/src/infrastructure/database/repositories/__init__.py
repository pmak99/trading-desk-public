"""
Database repositories for IV Crush 2.0.

Provides data access abstractions using the Repository pattern:
- BaseRepository: Common connection management and error handling
- ResilientRepository: Non-critical operations with failure tracking
- EarningsRepository: Earnings calendar data
- PricesRepository: Historical price movements
- AnalysisRepository: Analysis run logging for meta-analysis
"""

from src.infrastructure.database.repositories.base_repository import (
    BaseRepository,
    ResilientRepository,
)
from src.infrastructure.database.repositories.earnings_repository import EarningsRepository
from src.infrastructure.database.repositories.prices_repository import PricesRepository
from src.infrastructure.database.repositories.analysis_repository import AnalysisRepository

__all__ = [
    "BaseRepository",
    "ResilientRepository",
    "EarningsRepository",
    "PricesRepository",
    "AnalysisRepository",
]
