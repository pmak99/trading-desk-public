"""6.0 Agent implementations.

Agents are worker units that perform specific tasks like analyzing tickers,
fetching sentiment, detecting anomalies, and generating explanations.
"""

from .base import BaseAgent
from .ticker_analysis import TickerAnalysisAgent
from .sentiment_fetch import SentimentFetchAgent
from .anomaly import AnomalyDetectionAgent
from .explanation import ExplanationAgent
from .health import HealthCheckAgent
from .sector_fetch import SectorFetchAgent
from .data_quality import DataQualityAgent
from .pattern_recognition import PatternRecognitionAgent

__all__ = [
    'BaseAgent',
    'TickerAnalysisAgent',
    'SentimentFetchAgent',
    'AnomalyDetectionAgent',
    'ExplanationAgent',
    'HealthCheckAgent',
    'SectorFetchAgent',
    'DataQualityAgent',
    'PatternRecognitionAgent',
]
