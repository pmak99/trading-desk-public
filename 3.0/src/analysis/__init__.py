"""
Analysis modules for 3.0 ML Earnings Scanner.
"""

from src.analysis.vrp import (
    Recommendation,
    HistoricalMove,
    VRPResult,
    VRPCalculator,
)
from src.analysis.ml_predictor import (
    MagnitudePrediction,
    MLMagnitudePredictor,
)
from src.analysis.scanner_core import (
    TradeRecommendation,
    ScanResult,
    get_earnings_calendar,
    find_next_expiration,
    log_iv_data,
    calculate_position_multiplier,
    assess_edge,
    get_default_db_path,
    generate_trade_recommendation,
)
from src.analysis.date_validator import (
    EarningsSource,
    EarningsTiming,
    EarningsDateInfo,
    ValidationResult,
    EarningsDateValidator,
)
from src.analysis.accuracy_tracker import (
    PredictionRecord,
    AccuracyMetrics,
    AccuracyTracker,
)

__all__ = [
    # VRP
    'Recommendation',
    'HistoricalMove',
    'VRPResult',
    'VRPCalculator',
    # ML Predictor
    'MagnitudePrediction',
    'MLMagnitudePredictor',
    # Scanner Core
    'TradeRecommendation',
    'ScanResult',
    'get_earnings_calendar',
    'find_next_expiration',
    'log_iv_data',
    'calculate_position_multiplier',
    'assess_edge',
    'get_default_db_path',
    'generate_trade_recommendation',
    # Date Validator
    'EarningsSource',
    'EarningsTiming',
    'EarningsDateInfo',
    'ValidationResult',
    'EarningsDateValidator',
    # Accuracy Tracker
    'PredictionRecord',
    'AccuracyMetrics',
    'AccuracyTracker',
]
