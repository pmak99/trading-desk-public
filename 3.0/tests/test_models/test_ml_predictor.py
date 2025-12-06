"""
Unit tests for ML Magnitude Predictor.
"""

import pytest
import sqlite3
import tempfile
import numpy as np
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from dataclasses import dataclass

from src.analysis.ml_predictor import (
    MLMagnitudePredictor,
    MagnitudePrediction,
    get_db_connection,
)
from src.data.price_fetcher import VolatilityFeatures


@pytest.fixture
def temp_db():
    """Create a temporary database with test data."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create historical_moves table
    cursor.execute("""
        CREATE TABLE historical_moves (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            earnings_date TEXT NOT NULL,
            prev_close REAL,
            earnings_close REAL,
            close_move_pct REAL,
            gap_move_pct REAL,
            intraday_move_pct REAL
        )
    """)

    # Insert test data - 12 quarters of history
    base_date = date(2024, 10, 31)
    for i in range(12):
        earnings_date = base_date - timedelta(days=90 * i)
        move = 4.0 + (i % 3) * 0.5  # Vary between 4.0, 4.5, 5.0
        cursor.execute("""
            INSERT INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_close,
             close_move_pct, gap_move_pct, intraday_move_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('AAPL', earnings_date.isoformat(), 170.0, 177.0, move, move * 0.8, move * 0.3))

    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    db_path.unlink(missing_ok=True)


@pytest.fixture
def mock_volatility_features():
    """Create mock volatility features."""
    return VolatilityFeatures(
        ticker='AAPL',
        as_of_date=date.today(),
        atr_10d=2.5,
        atr_10d_pct=1.5,
        atr_20d=2.8,
        atr_20d_pct=1.6,
        atr_50d=3.0,
        atr_50d_pct=1.8,
        bb_width_10d=0.04,
        bb_width_20d=0.05,
        bb_width_50d=0.06,
        hv_10d=25.0,
        hv_20d=28.0,
        hv_50d=30.0,
        hv_percentile=65.0,
    )


@pytest.fixture
def mock_model():
    """Create a mock Random Forest model."""
    model = MagicMock()
    model.predict.return_value = np.array([4.5])  # Predict 4.5% move
    return model


@pytest.fixture
def mock_imputer():
    """Create a mock imputer."""
    imputer = MagicMock()
    imputer.transform.return_value = np.zeros((1, 58))  # 58 features
    return imputer


@pytest.fixture
def feature_columns():
    """Create test feature columns."""
    return [
        'hist_2q_mean', 'hist_2q_median', 'hist_2q_std', 'hist_2q_min', 'hist_2q_max', 'hist_2q_count',
        'hist_4q_mean', 'hist_4q_median', 'hist_4q_std', 'hist_4q_min', 'hist_4q_max', 'hist_4q_count',
        'hist_8q_mean', 'hist_8q_median', 'hist_8q_std', 'hist_8q_min', 'hist_8q_max', 'hist_8q_count',
        'years_of_history', 'years_since_start',
        'is_q1', 'is_q2', 'is_q3', 'is_q4',
        'is_monday', 'is_friday',
        'month_sin', 'month_cos',
        'earnings_year', 'earnings_month',
        'atr_10d', 'atr_10d_pct', 'atr_20d', 'atr_20d_pct', 'atr_50d', 'atr_50d_pct',
        'bb_width_10d', 'bb_width_20d', 'bb_width_50d',
        'hv_10d', 'hv_20d', 'hv_50d', 'hv_percentile_1y',
    ]


class TestMLMagnitudePredictor:
    """Tests for MLMagnitudePredictor class."""

    def test_init_missing_model_files(self, temp_db):
        """Test initialization fails gracefully with missing model files."""
        with pytest.raises(RuntimeError, match="Model files not found"):
            MLMagnitudePredictor(
                models_dir=Path("/nonexistent/path"),
                db_path=temp_db,
            )

    @patch('src.analysis.ml_predictor.joblib.load')
    @patch('builtins.open')
    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_get_historical_stats(self, mock_open, mock_joblib, temp_db):
        """Test historical stats calculation."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)
        predictor.db_path = temp_db
        predictor.feature_cols = []
        predictor.model = MagicMock()
        predictor.imputer = MagicMock()
        predictor.price_fetcher = MagicMock()

        stats = predictor._get_historical_stats('AAPL', date(2025, 1, 15))

        assert 'hist_2q_mean' in stats
        assert 'hist_4q_mean' in stats
        assert 'hist_8q_mean' in stats
        assert 'years_of_history' in stats
        assert stats['hist_2q_count'] == 2
        assert stats['hist_4q_count'] == 4
        assert stats['hist_8q_count'] == 8

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_get_historical_stats_no_data(self, temp_db):
        """Test historical stats with no data."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)
        predictor.db_path = temp_db

        stats = predictor._get_historical_stats('UNKNOWN', date(2025, 1, 15))
        assert stats == {}

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_get_time_features_q1(self):
        """Test time features for Q1."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)

        features = predictor._get_time_features(date(2025, 2, 15))

        assert features['is_q1'] == 1
        assert features['is_q2'] == 0
        assert features['is_q3'] == 0
        assert features['is_q4'] == 0
        assert features['earnings_year'] == 2025
        assert features['earnings_month'] == 2

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_get_time_features_monday(self):
        """Test time features for Monday."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)

        # Dec 9, 2024 is a Monday
        features = predictor._get_time_features(date(2024, 12, 9))

        assert features['is_monday'] == 1
        assert features['is_friday'] == 0

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_get_time_features_friday(self):
        """Test time features for Friday."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)

        # Dec 13, 2024 is a Friday
        features = predictor._get_time_features(date(2024, 12, 13))

        assert features['is_monday'] == 0
        assert features['is_friday'] == 1

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_get_time_features_cyclical_encoding(self):
        """Test month cyclical encoding."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)

        jan_features = predictor._get_time_features(date(2025, 1, 15))
        jul_features = predictor._get_time_features(date(2025, 7, 15))

        # January and July should have opposite sin values
        assert jan_features['month_sin'] > 0
        assert jul_features['month_sin'] < 0

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_get_volatility_features(self, mock_volatility_features):
        """Test volatility feature extraction."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)
        predictor.price_fetcher = MagicMock()
        predictor.price_fetcher.calculate_volatility_features.return_value = mock_volatility_features

        features = predictor._get_volatility_features('AAPL', date(2025, 1, 15))

        assert features['atr_10d'] == 2.5
        assert features['atr_10d_pct'] == 1.5
        assert features['bb_width_20d'] == 0.05
        assert features['hv_20d'] == 28.0
        assert features['hv_percentile_1y'] == 65.0

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_get_volatility_features_none(self):
        """Test volatility features when data unavailable."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)
        predictor.price_fetcher = MagicMock()
        predictor.price_fetcher.calculate_volatility_features.return_value = None

        features = predictor._get_volatility_features('AAPL', date(2025, 1, 15))
        assert features == {}

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_predict_success(
        self, temp_db, mock_model, mock_imputer, feature_columns, mock_volatility_features
    ):
        """Test successful prediction."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)
        predictor.db_path = temp_db
        predictor.model = mock_model
        predictor.imputer = mock_imputer
        predictor.feature_cols = feature_columns
        predictor.price_fetcher = MagicMock()
        predictor.price_fetcher.calculate_volatility_features.return_value = mock_volatility_features

        result = predictor.predict('AAPL', date(2025, 1, 15))

        assert result is not None
        assert isinstance(result, MagnitudePrediction)
        assert result.ticker == 'AAPL'
        assert result.earnings_date == date(2025, 1, 15)
        assert result.predicted_move_pct == 4.5
        assert result.prediction_confidence > 0
        assert result.feature_count > 0

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_predict_too_many_missing_features(
        self, temp_db, mock_model, mock_imputer
    ):
        """Test prediction fails with too many missing features."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)
        predictor.db_path = temp_db
        predictor.model = mock_model
        predictor.imputer = mock_imputer
        # Use feature columns that won't be found
        predictor.feature_cols = ['nonexistent_feature_' + str(i) for i in range(100)]
        predictor.price_fetcher = MagicMock()
        predictor.price_fetcher.calculate_volatility_features.return_value = None

        result = predictor.predict('UNKNOWN', date(2025, 1, 15))

        assert result is None

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_predict_handles_exception(self, temp_db):
        """Test prediction handles exceptions gracefully."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)
        predictor.db_path = temp_db
        predictor.model = MagicMock()
        predictor.model.predict.side_effect = ValueError("Model error")
        predictor.imputer = MagicMock()
        predictor.feature_cols = ['some_feature']
        predictor.price_fetcher = MagicMock()
        predictor.price_fetcher.calculate_volatility_features.return_value = None

        result = predictor.predict('AAPL', date(2025, 1, 15))

        assert result is None

    @patch.object(MLMagnitudePredictor, '__init__', lambda x, **kwargs: None)
    def test_confidence_calculation(
        self, temp_db, mock_model, mock_imputer, mock_volatility_features
    ):
        """Test confidence is based on feature availability."""
        predictor = MLMagnitudePredictor.__new__(MLMagnitudePredictor)
        predictor.db_path = temp_db
        predictor.model = mock_model
        predictor.imputer = mock_imputer
        predictor.feature_cols = ['hist_2q_mean', 'hist_4q_mean', 'atr_10d', 'nonexistent']
        predictor.price_fetcher = MagicMock()
        predictor.price_fetcher.calculate_volatility_features.return_value = mock_volatility_features

        result = predictor.predict('AAPL', date(2025, 1, 15))

        assert result is not None
        # 3 out of 4 features available = 75% confidence
        assert result.prediction_confidence == 0.75
        assert result.feature_count == 3


class TestMagnitudePrediction:
    """Tests for MagnitudePrediction dataclass."""

    def test_magnitude_prediction_creation(self):
        """Test creating MagnitudePrediction instance."""
        prediction = MagnitudePrediction(
            ticker='AAPL',
            earnings_date=date(2025, 1, 15),
            predicted_move_pct=4.5,
            prediction_confidence=0.85,
            feature_count=50,
            features_used={'hist_2q_mean': 4.0, 'atr_10d': 2.5},
        )

        assert prediction.ticker == 'AAPL'
        assert prediction.predicted_move_pct == 4.5
        assert prediction.prediction_confidence == 0.85
        assert prediction.feature_count == 50
        assert len(prediction.features_used) == 2


class TestDatabaseConnection:
    """Tests for database connection context manager."""

    def test_get_db_connection(self, temp_db):
        """Test database connection context manager."""
        with get_db_connection(temp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM historical_moves")
            count = cursor.fetchone()[0]
            assert count == 12

    def test_get_db_connection_closes(self, temp_db):
        """Test connection is closed after context."""
        conn_ref = None
        with get_db_connection(temp_db) as conn:
            conn_ref = conn
            assert conn is not None

        # Connection should be closed after context
        with pytest.raises(Exception):
            conn_ref.execute("SELECT 1")
