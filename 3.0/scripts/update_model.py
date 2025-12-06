#!/usr/bin/env python3
"""
Online Learning Pipeline for 3.0 ML Model.

Retrains the magnitude prediction model with new earnings outcomes.
Uses walk-forward validation to ensure model quality before deployment.

Usage:
    python scripts/update_model.py [--validate-only] [--force]
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.db import get_db_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
MODELS_DIR = Path(__file__).parent.parent / "models"
VALIDATED_DIR = MODELS_DIR / "validated"
BACKUP_DIR = MODELS_DIR / "backup"
DATA_DIR = Path(__file__).parent.parent.parent / "2.0" / "data"


class ModelTrainer:
    """Train and validate magnitude prediction model."""

    # Minimum samples required for training
    MIN_TRAINING_SAMPLES = 100

    # Validation thresholds
    MIN_R2_THRESHOLD = 0.15  # Minimum R² to accept model
    MAX_MAE_THRESHOLD = 5.0  # Maximum MAE (percentage points)

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DATA_DIR / "ivcrush.db"

    def load_training_data(self) -> Optional[pd.DataFrame]:
        """Load training data from database."""
        logger.info("Loading training data...")

        try:
            with get_db_connection(self.db_path) as conn:
                # Load historical moves with features
                query = """
                    SELECT
                        h.ticker,
                        h.earnings_date,
                        h.close_move_pct,
                        h.gap_move_pct,
                        h.intraday_move_pct
                    FROM historical_moves h
                    WHERE h.close_move_pct IS NOT NULL
                    ORDER BY h.earnings_date
                """
                df = pd.read_sql_query(query, conn)

            if df.empty:
                logger.error("No training data found")
                return None

            # Use absolute move for regression target
            df['target'] = df['intraday_move_pct'].abs()

            logger.info(f"Loaded {len(df)} samples from {df['ticker'].nunique()} tickers")
            return df

        except Exception as e:
            logger.error(f"Failed to load training data: {e}")
            return None

    def engineer_features(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
        """
        Engineer features from raw data.

        Returns DataFrame with features and list of feature column names.
        """
        logger.info("Engineering features...")

        df = df.copy()
        df['earnings_date'] = pd.to_datetime(df['earnings_date'])

        # Sort by ticker and date for rolling calculations
        df = df.sort_values(['ticker', 'earnings_date'])

        # Historical move statistics (per ticker)
        for window in [2, 4, 8]:
            df[f'hist_{window}q_mean'] = df.groupby('ticker')['target'].transform(
                lambda x: x.shift(1).rolling(window, min_periods=1).mean()
            )
            df[f'hist_{window}q_std'] = df.groupby('ticker')['target'].transform(
                lambda x: x.shift(1).rolling(window, min_periods=1).std()
            )
            df[f'hist_{window}q_max'] = df.groupby('ticker')['target'].transform(
                lambda x: x.shift(1).rolling(window, min_periods=1).max()
            )

        # Time features
        df['quarter'] = df['earnings_date'].dt.quarter
        df['is_q4'] = (df['quarter'] == 4).astype(int)
        df['day_of_week'] = df['earnings_date'].dt.dayofweek
        df['is_friday'] = (df['day_of_week'] == 4).astype(int)
        df['month'] = df['earnings_date'].dt.month
        df['year'] = df['earnings_date'].dt.year

        # Cyclical encoding
        df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
        df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)

        # Drop rows with NaN target
        df = df.dropna(subset=['target'])

        # Feature columns
        feature_cols = [
            'hist_2q_mean', 'hist_2q_std', 'hist_2q_max',
            'hist_4q_mean', 'hist_4q_std', 'hist_4q_max',
            'hist_8q_mean', 'hist_8q_std', 'hist_8q_max',
            'is_q4', 'is_friday', 'month_sin', 'month_cos', 'year'
        ]

        logger.info(f"Engineered {len(feature_cols)} features for {len(df)} samples")
        return df, feature_cols

    def walk_forward_validate(
        self,
        df: pd.DataFrame,
        feature_cols: List[str],
        n_splits: int = 5
    ) -> Dict[str, float]:
        """
        Perform walk-forward cross-validation.

        Returns dict of average metrics across folds.
        """
        logger.info(f"Running walk-forward validation with {n_splits} splits...")

        df = df.sort_values('earnings_date')
        n = len(df)
        fold_size = n // (n_splits + 1)

        metrics = {'r2': [], 'mae': [], 'rmse': []}

        for i in range(n_splits):
            train_end = fold_size * (i + 1)
            test_start = train_end
            test_end = train_end + fold_size

            train_df = df.iloc[:train_end]
            test_df = df.iloc[test_start:test_end]

            if len(train_df) < 50 or len(test_df) < 10:
                continue

            X_train = train_df[feature_cols]
            y_train = train_df['target']
            X_test = test_df[feature_cols]
            y_test = test_df['target']

            # Impute missing values
            imputer = SimpleImputer(strategy='median')
            X_train_imp = imputer.fit_transform(X_train)
            X_test_imp = imputer.transform(X_test)

            # Train model
            model = RandomForestRegressor(
                n_estimators=100,
                max_depth=10,
                min_samples_split=10,
                min_samples_leaf=5,
                random_state=42,
                n_jobs=-1
            )
            model.fit(X_train_imp, y_train)

            # Predict
            y_pred = model.predict(X_test_imp)

            # Calculate metrics
            metrics['r2'].append(r2_score(y_test, y_pred))
            metrics['mae'].append(mean_absolute_error(y_test, y_pred))
            metrics['rmse'].append(np.sqrt(mean_squared_error(y_test, y_pred)))

            logger.info(
                f"Fold {i+1}: R²={metrics['r2'][-1]:.3f}, "
                f"MAE={metrics['mae'][-1]:.2f}, RMSE={metrics['rmse'][-1]:.2f}"
            )

        avg_metrics = {
            'r2': np.mean(metrics['r2']),
            'mae': np.mean(metrics['mae']),
            'rmse': np.mean(metrics['rmse']),
            'n_folds': len(metrics['r2'])
        }

        logger.info(
            f"Average: R²={avg_metrics['r2']:.3f}, "
            f"MAE={avg_metrics['mae']:.2f}, RMSE={avg_metrics['rmse']:.2f}"
        )

        return avg_metrics

    def train_final_model(
        self,
        df: pd.DataFrame,
        feature_cols: List[str]
    ) -> Tuple[RandomForestRegressor, SimpleImputer]:
        """Train final model on all data."""
        logger.info("Training final model on all data...")

        X = df[feature_cols]
        y = df['target']

        # Impute missing values
        imputer = SimpleImputer(strategy='median')
        X_imp = imputer.fit_transform(X)

        # Train model
        model = RandomForestRegressor(
            n_estimators=100,
            max_depth=10,
            min_samples_split=10,
            min_samples_leaf=5,
            random_state=42,
            n_jobs=-1
        )
        model.fit(X_imp, y)

        logger.info(f"Model trained with {len(X)} samples")
        return model, imputer

    def save_model(
        self,
        model: RandomForestRegressor,
        imputer: SimpleImputer,
        feature_cols: List[str],
        metrics: Dict[str, float]
    ) -> None:
        """Save model and artifacts."""
        # Backup existing model
        if VALIDATED_DIR.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = BACKUP_DIR / timestamp
            backup_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(VALIDATED_DIR, backup_path / "validated")
            logger.info(f"Backed up existing model to {backup_path}")

        # Save new model
        VALIDATED_DIR.mkdir(parents=True, exist_ok=True)

        joblib.dump(model, VALIDATED_DIR / "rf_magnitude_validated.pkl")
        joblib.dump(imputer, VALIDATED_DIR / "imputer_validated.pkl")

        with open(VALIDATED_DIR / "feature_columns.txt", 'w') as f:
            for col in feature_cols:
                f.write(f"{col}\n")

        with open(VALIDATED_DIR / "model_metadata.json", 'w') as f:
            json.dump({
                'trained_at': datetime.now().isoformat(),
                'n_features': len(feature_cols),
                'metrics': metrics,
                'feature_columns': feature_cols,
            }, f, indent=2)

        logger.info(f"Model saved to {VALIDATED_DIR}")

    def run(self, validate_only: bool = False, force: bool = False) -> bool:
        """
        Run the training pipeline.

        Args:
            validate_only: Only validate, don't train new model
            force: Force training even if metrics don't meet threshold

        Returns:
            True if successful
        """
        # Load data
        df = self.load_training_data()
        if df is None:
            return False

        if len(df) < self.MIN_TRAINING_SAMPLES:
            logger.error(
                f"Insufficient training samples: {len(df)} < {self.MIN_TRAINING_SAMPLES}"
            )
            return False

        # Engineer features
        df, feature_cols = self.engineer_features(df)

        # Validate
        metrics = self.walk_forward_validate(df, feature_cols)

        # Check thresholds
        meets_thresholds = (
            metrics['r2'] >= self.MIN_R2_THRESHOLD and
            metrics['mae'] <= self.MAX_MAE_THRESHOLD
        )

        if not meets_thresholds:
            logger.warning(
                f"Model does not meet quality thresholds: "
                f"R²={metrics['r2']:.3f} (min: {self.MIN_R2_THRESHOLD}), "
                f"MAE={metrics['mae']:.2f} (max: {self.MAX_MAE_THRESHOLD})"
            )
            if not force:
                logger.error("Use --force to train anyway")
                return False
            logger.warning("Proceeding with --force")

        if validate_only:
            logger.info("Validation only mode - skipping training")
            return True

        # Train final model
        model, imputer = self.train_final_model(df, feature_cols)

        # Save
        self.save_model(model, imputer, feature_cols, metrics)

        logger.info("Model update complete!")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Update ML magnitude prediction model"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only run validation, don't train new model"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force training even if metrics don't meet thresholds"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to database"
    )
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    trainer = ModelTrainer(db_path=db_path)

    success = trainer.run(
        validate_only=args.validate_only,
        force=args.force
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
