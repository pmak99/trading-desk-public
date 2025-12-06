"""
ML Magnitude Predictor for 3.0.

Uses Random Forest model trained on historical features to predict
expected earnings move magnitude.
"""

import joblib
import pandas as pd
import numpy as np
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
import logging

from src.data.price_fetcher import PriceFetcher

logger = logging.getLogger(__name__)


@dataclass
class MagnitudePrediction:
    """ML magnitude prediction result."""
    ticker: str
    earnings_date: date
    predicted_move_pct: float
    prediction_confidence: float  # Based on model's internal metrics
    feature_count: int
    features_used: Dict[str, float]  # Top features and their values


class MLMagnitudePredictor:
    """
    Predict earnings move magnitude using Random Forest.

    Uses validated model from walk-forward cross-validation.
    R² = 0.26 on out-of-sample data.
    """

    def __init__(self, models_dir: Optional[Path] = None, db_path: Optional[Path] = None):
        self.models_dir = models_dir or Path(__file__).parent.parent.parent / "models" / "validated"
        self.db_path = db_path or Path(__file__).parent.parent.parent.parent / "2.0" / "data" / "ivcrush.db"

        # Load model and preprocessing
        self.model = joblib.load(self.models_dir / "rf_magnitude_validated.pkl")
        self.imputer = joblib.load(self.models_dir / "imputer_validated.pkl")

        # Load feature columns
        with open(self.models_dir / "feature_columns.txt") as f:
            self.feature_cols = [line.strip() for line in f if line.strip()]

        # Initialize price fetcher for volatility features
        self.price_fetcher = PriceFetcher()

        logger.info(f"Loaded ML magnitude predictor with {len(self.feature_cols)} features")

    def _get_historical_stats(self, ticker: str, as_of_date: date) -> Dict[str, float]:
        """Calculate historical move statistics for a ticker."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get historical moves before the earnings date
        cursor.execute("""
            SELECT close_move_pct, gap_move_pct, intraday_move_pct, earnings_date
            FROM historical_moves
            WHERE ticker = ? AND earnings_date < ?
            ORDER BY earnings_date DESC
            LIMIT 12
        """, (ticker, as_of_date.isoformat()))

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return {}

        # Calculate features for different lookback windows
        moves = [abs(row[0]) for row in rows]  # Use absolute values
        dates = [row[3] for row in rows]

        features = {}

        # Historical move statistics
        for window, name in [(2, '2q'), (4, '4q'), (8, '8q')]:
            if len(moves) >= window:
                subset = moves[:window]
                features[f'hist_{name}_mean'] = np.mean(subset)
                features[f'hist_{name}_median'] = np.median(subset)
                features[f'hist_{name}_std'] = np.std(subset)
                features[f'hist_{name}_min'] = np.min(subset)
                features[f'hist_{name}_max'] = np.max(subset)
                features[f'hist_{name}_count'] = len(subset)

        # Years of history
        if dates:
            first_date = dates[-1] if isinstance(dates[-1], date) else date.fromisoformat(dates[-1])
            features['years_of_history'] = (as_of_date - first_date).days / 365.0
            features['years_since_start'] = features['years_of_history']

        return features

    def _get_volatility_features(self, ticker: str, as_of_date: date) -> Dict[str, float]:
        """
        Calculate volatility features using Yahoo Finance data.

        Returns ATR, Bollinger Band width, and Historical Volatility
        for 10, 20, and 50 day windows.
        """
        vol_features = self.price_fetcher.calculate_volatility_features(ticker, as_of_date)

        if vol_features is None:
            return {}

        # Convert dataclass to dict with matching feature names
        features = {
            'atr_10d': vol_features.atr_10d,
            'atr_10d_pct': vol_features.atr_10d_pct,
            'atr_20d': vol_features.atr_20d,
            'atr_20d_pct': vol_features.atr_20d_pct,
            'atr_50d': vol_features.atr_50d,
            'atr_50d_pct': vol_features.atr_50d_pct,
            'bb_width_10d': vol_features.bb_width_10d,
            'bb_width_20d': vol_features.bb_width_20d,
            'bb_width_50d': vol_features.bb_width_50d,
            'hv_10d': vol_features.hv_10d,
            'hv_20d': vol_features.hv_20d,
            'hv_50d': vol_features.hv_50d,
            'hv_percentile_1y': vol_features.hv_percentile,
        }

        return features

    def _get_time_features(self, earnings_date: date) -> Dict[str, float]:
        """Calculate time-based features."""
        features = {}

        # Quarter
        quarter = (earnings_date.month - 1) // 3 + 1
        features['is_q1'] = 1 if quarter == 1 else 0
        features['is_q2'] = 1 if quarter == 2 else 0
        features['is_q3'] = 1 if quarter == 3 else 0
        features['is_q4'] = 1 if quarter == 4 else 0

        # Day of week
        dow = earnings_date.weekday()
        features['is_monday'] = 1 if dow == 0 else 0
        features['is_friday'] = 1 if dow == 4 else 0

        # Month cyclical encoding
        features['month_sin'] = np.sin(2 * np.pi * earnings_date.month / 12)
        features['month_cos'] = np.cos(2 * np.pi * earnings_date.month / 12)

        # Year info
        features['earnings_year'] = earnings_date.year
        features['earnings_month'] = earnings_date.month

        return features

    def predict(self, ticker: str, earnings_date: date) -> Optional[MagnitudePrediction]:
        """
        Predict earnings move magnitude for a ticker.

        Args:
            ticker: Stock symbol
            earnings_date: Upcoming earnings date

        Returns:
            MagnitudePrediction or None if insufficient data
        """
        try:
            # Gather features
            features = {}
            features.update(self._get_historical_stats(ticker, earnings_date))
            features.update(self._get_time_features(earnings_date))
            features.update(self._get_volatility_features(ticker, earnings_date))

            # Create feature vector
            feature_vector = []
            missing_features = []

            for col in self.feature_cols:
                if col in features:
                    feature_vector.append(features[col])
                else:
                    feature_vector.append(np.nan)
                    missing_features.append(col)

            if len(missing_features) > len(self.feature_cols) * 0.5:
                logger.warning(f"{ticker}: Too many missing features ({len(missing_features)}/{len(self.feature_cols)})")
                return None

            # Impute missing values
            X = np.array(feature_vector).reshape(1, -1)
            X_imputed = self.imputer.transform(X)

            # Predict
            predicted_move = self.model.predict(X_imputed)[0]

            # Calculate confidence based on feature availability
            confidence = 1.0 - (len(missing_features) / len(self.feature_cols))

            # Get top features used
            top_features = {}
            for i, col in enumerate(self.feature_cols):
                if col in features and not np.isnan(features[col]):
                    top_features[col] = features[col]

            return MagnitudePrediction(
                ticker=ticker,
                earnings_date=earnings_date,
                predicted_move_pct=predicted_move,
                prediction_confidence=confidence,
                feature_count=len(self.feature_cols) - len(missing_features),
                features_used=dict(list(top_features.items())[:10]),  # Top 10
            )

        except Exception as e:
            logger.error(f"Error predicting magnitude for {ticker}: {e}")
            return None
