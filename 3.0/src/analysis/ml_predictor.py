"""
ML Magnitude Predictor for 3.0.

Uses Random Forest model trained on historical features to predict
expected earnings move magnitude.
"""

import os
import time
import warnings
import joblib
import numpy as np
import pandas as pd
from datetime import date
from pathlib import Path
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
import logging

# Suppress sklearn feature name warnings from DecisionTree estimators inside RF
warnings.filterwarnings('ignore', message='X has feature names, but DecisionTreeRegressor was fitted without feature names')
warnings.filterwarnings('ignore', message='X does not have valid feature names')

from src.data.price_fetcher import PriceFetcher, VolatilityFeatures
from src.utils.db import get_db_connection

logger = logging.getLogger(__name__)

# Volatility feature cache: {ticker: (features_dict, timestamp)}
_volatility_cache: Dict[str, Tuple[Dict[str, float], float]] = {}
VOLATILITY_CACHE_TTL = 3600  # 1 hour TTL

__all__ = [
    'MagnitudePrediction',
    'MLMagnitudePredictor',
]


@dataclass
class MagnitudePrediction:
    """ML magnitude prediction result."""
    ticker: str
    earnings_date: date
    predicted_move_pct: float
    prediction_confidence: float  # Based on model's internal metrics
    feature_count: int
    features_used: Dict[str, float]  # Top features and their values
    tree_std: Optional[float] = None  # Std dev across trees (uncertainty)
    calibrated_confidence: Optional[float] = None  # Calibrated confidence score
    # Prediction intervals from quantile regression
    prediction_lower: Optional[float] = None  # 10th percentile
    prediction_upper: Optional[float] = None  # 90th percentile


class MLMagnitudePredictor:
    """
    Predict earnings move magnitude using Random Forest.

    Uses validated model from walk-forward cross-validation.
    R² = 0.26 on out-of-sample data.
    """

    def __init__(self, models_dir: Optional[Path] = None, db_path: Optional[Path] = None):
        self.models_dir = models_dir or Path(__file__).parent.parent.parent / "models" / "validated"
        default_db = Path(__file__).parent.parent.parent.parent / "2.0" / "data" / "ivcrush.db"
        self.db_path = db_path or Path(os.getenv('DB_PATH', str(default_db)))

        # Load model and preprocessing with error handling
        try:
            self.model = joblib.load(self.models_dir / "rf_magnitude_validated.pkl")
            self.imputer = joblib.load(self.models_dir / "imputer_validated.pkl")
        except FileNotFoundError as e:
            raise RuntimeError(
                f"Model files not found at {self.models_dir}. "
                "Run model training first or check models_dir path."
            ) from e

        # Load feature columns
        try:
            with open(self.models_dir / "feature_columns.txt") as f:
                self.feature_cols = [line.strip() for line in f if line.strip()]
        except FileNotFoundError as e:
            raise RuntimeError(
                f"Feature columns file not found at {self.models_dir}/feature_columns.txt"
            ) from e

        # Initialize price fetcher for volatility features
        self.price_fetcher = PriceFetcher()

        # Extract feature importances if available
        self._feature_importances: Optional[Dict[str, float]] = None
        if hasattr(self.model, 'feature_importances_'):
            importances = self.model.feature_importances_
            self._feature_importances = dict(zip(self.feature_cols, importances))

        # Load quantile models for prediction intervals if available
        self._quantile_models = {}
        for q in [10, 50, 90]:
            quantile_path = self.models_dir / f"quantile_{q}_validated.pkl"
            if quantile_path.exists():
                try:
                    self._quantile_models[q] = joblib.load(quantile_path)
                    logger.debug(f"Loaded quantile model for q={q}")
                except Exception as e:
                    logger.warning(f"Failed to load quantile model q={q}: {e}")

        logger.info(f"Loaded ML magnitude predictor with {len(self.feature_cols)} features"
                   f"{' and ' + str(len(self._quantile_models)) + ' quantile models' if self._quantile_models else ''}")

    def _get_historical_stats(self, ticker: str, as_of_date: date) -> Dict[str, float]:
        """Calculate historical move statistics for a ticker."""
        with get_db_connection(self.db_path) as conn:
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

        Uses TTL-based caching (1 hour) to reduce API calls.
        """
        global _volatility_cache

        cache_key = f"{ticker}_{as_of_date.isoformat()}"
        current_time = time.time()

        # Check cache
        if cache_key in _volatility_cache:
            cached_features, cached_time = _volatility_cache[cache_key]
            if current_time - cached_time < VOLATILITY_CACHE_TTL:
                logger.debug(f"{ticker}: Using cached volatility features")
                return cached_features

        # Fetch fresh data
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

        # Cache the result
        _volatility_cache[cache_key] = (features, current_time)
        logger.debug(f"{ticker}: Cached volatility features (TTL: {VOLATILITY_CACHE_TTL}s)")

        # Cleanup old cache entries (keep only last 100)
        if len(_volatility_cache) > 100:
            sorted_keys = sorted(_volatility_cache.keys(),
                                key=lambda k: _volatility_cache[k][1])
            for old_key in sorted_keys[:50]:
                del _volatility_cache[old_key]

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

    def _get_sector_features(self, ticker: str, earnings_date: date) -> Dict[str, float]:
        """
        Get sector and industry features for a ticker.

        Uses Yahoo Finance to fetch sector data and sector-level volatility.
        """
        try:
            from src.data.sector_data import get_sector_features
            return get_sector_features(ticker, earnings_date)
        except ImportError:
            logger.debug("Sector data module not available")
            return {}
        except Exception as e:
            logger.debug(f"Error getting sector features for {ticker}: {e}")
            return {}

    def _get_market_features(self, earnings_date: date) -> Dict[str, float]:
        """
        Get market regime features (VIX, SPY trend, breadth).

        Captures overall market conditions that affect earnings volatility.
        """
        try:
            from src.data.market_regime import get_market_features
            return get_market_features(earnings_date)
        except ImportError:
            logger.debug("Market regime module not available")
            return {}
        except Exception as e:
            logger.debug(f"Error getting market features: {e}")
            return {}

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
            # Gather features from multiple sources
            features = {}
            features.update(self._get_historical_stats(ticker, earnings_date))
            features.update(self._get_time_features(earnings_date))
            features.update(self._get_volatility_features(ticker, earnings_date))
            features.update(self._get_sector_features(ticker, earnings_date))
            features.update(self._get_market_features(earnings_date))

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

            # Impute missing values - use DataFrame to preserve feature names
            X_df = pd.DataFrame([feature_vector], columns=self.feature_cols)
            X_imputed = self.imputer.transform(X_df)
            X_imputed_df = pd.DataFrame(X_imputed, columns=self.feature_cols)

            # Predict
            predicted_move = self.model.predict(X_imputed_df)[0]

            # Calculate tree-level uncertainty (Random Forest specific)
            tree_std = None
            if hasattr(self.model, 'estimators_'):
                tree_predictions = np.array([
                    tree.predict(X_imputed_df)[0]
                    for tree in self.model.estimators_
                ])
                tree_std = np.std(tree_predictions)

            # Calculate confidence based on feature availability
            feature_availability = 1.0 - (len(missing_features) / len(self.feature_cols))

            # Calibrated confidence: combine feature availability with tree agreement
            calibrated_confidence = self._calculate_calibrated_confidence(
                feature_availability, tree_std, predicted_move
            )

            # Get prediction intervals from quantile models
            prediction_lower = None
            prediction_upper = None
            if self._quantile_models:
                try:
                    if 10 in self._quantile_models:
                        prediction_lower = float(self._quantile_models[10].predict(X_imputed_df)[0])
                    if 90 in self._quantile_models:
                        prediction_upper = float(self._quantile_models[90].predict(X_imputed_df)[0])
                except Exception as e:
                    logger.debug(f"Error getting prediction intervals: {e}")

            # Get top features used (sorted by importance if available)
            top_features = {}
            for col in self.feature_cols:
                if col in features and not np.isnan(features[col]):
                    top_features[col] = features[col]

            # Sort by importance if available
            if self._feature_importances:
                top_features = dict(sorted(
                    top_features.items(),
                    key=lambda x: self._feature_importances.get(x[0], 0),
                    reverse=True
                )[:10])
            else:
                top_features = dict(list(top_features.items())[:10])

            return MagnitudePrediction(
                ticker=ticker,
                earnings_date=earnings_date,
                predicted_move_pct=predicted_move,
                prediction_confidence=feature_availability,
                feature_count=len(self.feature_cols) - len(missing_features),
                features_used=top_features,
                tree_std=tree_std,
                calibrated_confidence=calibrated_confidence,
                prediction_lower=prediction_lower,
                prediction_upper=prediction_upper,
            )

        except Exception as e:
            logger.error(f"Error predicting magnitude for {ticker}: {e}")
            return None

    def _calculate_calibrated_confidence(
        self,
        feature_availability: float,
        tree_std: Optional[float],
        predicted_move: float
    ) -> float:
        """
        Calculate calibrated confidence score.

        Combines:
        1. Feature availability (0-1) - weight: 40%
        2. Tree agreement (lower std = higher confidence) - weight: 40%
        3. Prediction magnitude (extreme predictions = lower confidence) - weight: 20%

        Returns:
            Calibrated confidence score (0-1)
        """
        # Component 1: Feature availability (0-1)
        feature_score = feature_availability

        # Component 2: Tree agreement (0-1)
        tree_score = 0.5  # Default if no tree info
        if tree_std is not None and predicted_move > 0:
            # Coefficient of variation (std / mean)
            cv = tree_std / abs(predicted_move)
            # Typical CV ranges from 0.1 (high agreement) to 1.0+ (low agreement)
            # Map CV to score: CV=0 -> 1.0, CV=0.5 -> 0.5, CV>=1.0 -> 0.0
            tree_score = max(0.0, min(1.0, 1.0 - cv))

        # Component 3: Prediction reasonableness (0-1)
        # Most earnings moves are 2-8%, extreme predictions are less reliable
        magnitude_score = 1.0
        abs_move = abs(predicted_move)
        if abs_move > 20:
            magnitude_score = 0.4
        elif abs_move > 15:
            magnitude_score = 0.6
        elif abs_move > 10:
            magnitude_score = 0.8
        elif abs_move < 1:
            magnitude_score = 0.7  # Very small predictions are also suspect

        # Weighted combination
        confidence = (
            0.40 * feature_score +
            0.40 * tree_score +
            0.20 * magnitude_score
        )

        return min(1.0, max(0.0, confidence))

    def get_feature_importances(self) -> Optional[Dict[str, float]]:
        """
        Get feature importances from the model.

        Returns:
            Dict mapping feature names to importance scores, or None
        """
        return self._feature_importances

    def get_top_features(self, n: int = 10) -> Optional[Dict[str, float]]:
        """
        Get top N most important features.

        Args:
            n: Number of features to return

        Returns:
            Dict of top features sorted by importance
        """
        if not self._feature_importances:
            return None

        sorted_features = sorted(
            self._feature_importances.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return dict(sorted_features[:n])

    def log_feature_importances(self) -> None:
        """Log feature importances for debugging."""
        if not self._feature_importances:
            logger.warning("No feature importances available")
            return

        logger.info("Feature Importances (Top 15):")
        top_features = self.get_top_features(15)
        for i, (name, importance) in enumerate(top_features.items(), 1):
            logger.info(f"  {i:2d}. {name}: {importance:.4f}")
