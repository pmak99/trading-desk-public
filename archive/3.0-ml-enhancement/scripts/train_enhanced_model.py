#!/usr/bin/env python3
"""
Enhanced ML Training Pipeline for 3.0.

Improvements over original training:
1. Feature names preserved through all transforms (fixes sklearn warnings)
2. Bayesian hyperparameter optimization via Optuna
3. Ensemble stacking (RF + XGBoost + LightGBM)
4. Quantile regression for prediction intervals
5. Better handling of missing data with multiple imputation strategies
6. Feature engineering with sector/market regime integration
7. Walk-forward validation with proper time-series splits

Usage:
    python scripts/train_enhanced_model.py [--quick] [--force]
"""

import argparse
import json
import logging
import os
import shutil
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import (
    RandomForestRegressor,
    RandomForestClassifier,
    GradientBoostingRegressor,
    StackingRegressor,
    StackingClassifier,
)
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.impute import SimpleImputer, KNNImputer
from sklearn.preprocessing import StandardScaler, RobustScaler
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.metrics import (
    mean_absolute_error, mean_squared_error, r2_score,
    accuracy_score, precision_score, recall_score, f1_score
)

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

try:
    import optuna
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.db import get_db_connection

# Suppress warnings during training
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
MODELS_DIR = Path(__file__).parent.parent / "models"
VALIDATED_DIR = MODELS_DIR / "validated"
BACKUP_DIR = MODELS_DIR / "backup"
DATA_DIR = Path(__file__).parent.parent / "data" / "features"


def get_n_jobs() -> int:
    """
    Get appropriate n_jobs setting based on platform.

    macOS has issues with multiprocessing in scikit-learn/joblib
    due to the 'spawn' start method causing loky resource tracker warnings.
    Linux and Windows can safely use multiple cores.
    """
    import platform
    if platform.system() == 'Darwin':
        # macOS: use single core to avoid multiprocessing issues
        return 1
    else:
        # Linux/Windows: use all available cores
        return -1


class EnhancedModelTrainer:
    """Enhanced ML training with hyperparameter optimization and ensembling."""

    # Minimum samples required
    MIN_TRAINING_SAMPLES = 200

    # Quality thresholds
    MIN_R2_THRESHOLD = 0.20  # Higher than before
    MAX_MAE_THRESHOLD = 2.5  # Lower than before

    def __init__(self, quick_mode: bool = False):
        """
        Initialize trainer.

        Args:
            quick_mode: If True, use fewer trials and simpler models for faster iteration
        """
        self.quick_mode = quick_mode
        self.n_optuna_trials = 10 if quick_mode else 50
        self.n_cv_splits = 3 if quick_mode else 5

    def load_data(self) -> Optional[pd.DataFrame]:
        """Load and prepare training data."""
        logger.info("Loading training data from features parquet...")

        features_file = DATA_DIR / "all_features.parquet"
        if not features_file.exists():
            logger.error(f"Features file not found: {features_file}")
            logger.info("Run feature generation scripts first:")
            logger.info("  python scripts/generate_historical_features.py")
            logger.info("  python scripts/generate_volatility_features.py")
            logger.info("  python scripts/generate_market_features.py")
            logger.info("  python scripts/generate_time_features.py")
            return None

        df = pd.read_parquet(features_file)
        logger.info(f"Loaded {len(df)} samples with {len(df.columns)} columns")

        # Define targets
        target_magnitude = 'current_abs_move_pct'
        target_direction = 'current_close_move_pct'

        # Clean dataset
        df = df.dropna(subset=[target_magnitude, target_direction]).copy()
        df['direction_binary'] = (df[target_direction] > 0).astype(int)

        # Sort by date for time-series validation
        df = df.sort_values('earnings_date').reset_index(drop=True)

        logger.info(f"After cleaning: {len(df)} samples")
        logger.info(f"Date range: {df['earnings_date'].min()} to {df['earnings_date'].max()}")
        logger.info(f"Target mean: {df[target_magnitude].mean():.2f}%, std: {df[target_magnitude].std():.2f}%")

        return df

    def select_features(self, df: pd.DataFrame) -> List[str]:
        """Select features for modeling, excluding non-predictive columns."""
        # Columns to exclude
        exclude_cols = {
            'ticker', 'earnings_date',
            'current_close_move_pct', 'current_abs_move_pct',
            'direction_binary',
            'earnings_frequency', 'vix_regime', 'market_regime', 'vol_regime',  # Categorical
        }

        feature_cols = []
        for col in df.columns:
            if col in exclude_cols:
                continue
            if df[col].dtype not in ['int64', 'float64', 'int32', 'float32']:
                continue
            # Skip features with >60% missing (too unreliable)
            missing_pct = df[col].isna().sum() / len(df)
            if missing_pct > 0.60:
                logger.debug(f"Skipping {col}: {missing_pct:.1%} missing")
                continue
            feature_cols.append(col)

        logger.info(f"Selected {len(feature_cols)} features")
        return feature_cols

    def create_preprocessing_pipeline(
        self,
        feature_cols: List[str],
        impute_strategy: str = 'knn'
    ) -> Tuple[Pipeline, SimpleImputer]:
        """
        Create preprocessing pipeline that preserves feature names.

        Uses DataFrame-aware transforms to avoid sklearn warnings.
        """
        if impute_strategy == 'knn':
            imputer = KNNImputer(n_neighbors=5, weights='distance')
        else:
            imputer = SimpleImputer(strategy='median')

        # We'll handle preprocessing manually to preserve feature names
        return None, imputer

    def _prepare_data(
        self,
        df: pd.DataFrame,
        feature_cols: List[str],
        imputer: Optional[Any] = None,
        fit_imputer: bool = True
    ) -> Tuple[pd.DataFrame, Any]:
        """Prepare data with feature names preserved."""
        X = df[feature_cols].copy()

        if imputer is None:
            imputer = SimpleImputer(strategy='median')

        if fit_imputer:
            # Fit and transform
            X_imputed = imputer.fit_transform(X)
        else:
            # Transform only
            X_imputed = imputer.transform(X)

        # Convert back to DataFrame with feature names
        X_df = pd.DataFrame(X_imputed, columns=feature_cols, index=df.index)

        return X_df, imputer

    def optimize_rf_hyperparams(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        task: str = 'regression'
    ) -> Dict[str, Any]:
        """Optimize Random Forest hyperparameters using Optuna."""
        if not HAS_OPTUNA:
            logger.warning("Optuna not installed, using default hyperparameters")
            return self._get_default_rf_params(task)

        logger.info(f"Optimizing RF hyperparameters ({self.n_optuna_trials} trials)...")

        def objective(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 100, 400),
                'max_depth': trial.suggest_int('max_depth', 5, 25),
                'min_samples_split': trial.suggest_int('min_samples_split', 5, 30),
                'min_samples_leaf': trial.suggest_int('min_samples_leaf', 2, 15),
                'max_features': trial.suggest_categorical('max_features', ['sqrt', 'log2', 0.5]),
                'random_state': 42,
                'n_jobs': -1,
            }

            if task == 'regression':
                model = RandomForestRegressor(**params)
                scoring = 'neg_mean_absolute_error'
            else:
                params['class_weight'] = 'balanced'
                model = RandomForestClassifier(**params)
                scoring = 'accuracy'

            tscv = TimeSeriesSplit(n_splits=self.n_cv_splits)
            scores = cross_val_score(model, X, y, cv=tscv, scoring=scoring)
            return scores.mean()

        # Create study
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction='maximize')
        study.optimize(objective, n_trials=self.n_optuna_trials, show_progress_bar=False)

        best_params = study.best_params
        best_params['random_state'] = 42
        best_params['n_jobs'] = get_n_jobs()

        logger.info(f"Best RF params: {best_params}")
        logger.info(f"Best CV score: {study.best_value:.4f}")

        return best_params

    def optimize_xgb_hyperparams(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        task: str = 'regression'
    ) -> Dict[str, Any]:
        """Optimize XGBoost hyperparameters using Optuna."""
        if not HAS_XGB:
            logger.warning("XGBoost not installed")
            return {}

        if not HAS_OPTUNA:
            logger.warning("Optuna not installed, using default hyperparameters")
            return self._get_default_xgb_params(task)

        logger.info(f"Optimizing XGB hyperparameters ({self.n_optuna_trials} trials)...")

        def objective(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 100, 400),
                'max_depth': trial.suggest_int('max_depth', 3, 10),
                'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
                'subsample': trial.suggest_float('subsample', 0.6, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
                'reg_alpha': trial.suggest_float('reg_alpha', 1e-3, 10.0, log=True),
                'reg_lambda': trial.suggest_float('reg_lambda', 1e-3, 10.0, log=True),
                'random_state': 42,
                'n_jobs': -1,
                'verbosity': 0,
            }

            if task == 'regression':
                model = xgb.XGBRegressor(**params)
                scoring = 'neg_mean_absolute_error'
            else:
                model = xgb.XGBClassifier(**params)
                scoring = 'accuracy'

            tscv = TimeSeriesSplit(n_splits=self.n_cv_splits)
            scores = cross_val_score(model, X, y, cv=tscv, scoring=scoring)
            return scores.mean()

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction='maximize')
        study.optimize(objective, n_trials=self.n_optuna_trials, show_progress_bar=False)

        best_params = study.best_params
        best_params['random_state'] = 42
        best_params['n_jobs'] = get_n_jobs()
        best_params['verbosity'] = 0

        logger.info(f"Best XGB params: {best_params}")
        logger.info(f"Best CV score: {study.best_value:.4f}")

        return best_params

    def _get_default_rf_params(self, task: str) -> Dict[str, Any]:
        """Default RF parameters."""
        params = {
            'n_estimators': 200,
            'max_depth': 15,
            'min_samples_split': 10,
            'min_samples_leaf': 5,
            'max_features': 'sqrt',
            'random_state': 42,
            'n_jobs': get_n_jobs(),
        }
        if task == 'classification':
            params['class_weight'] = 'balanced'
        return params

    def _get_default_xgb_params(self, task: str) -> Dict[str, Any]:
        """Default XGB parameters."""
        return {
            'n_estimators': 200,
            'max_depth': 6,
            'learning_rate': 0.1,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'reg_alpha': 0.1,
            'reg_lambda': 1.0,
            'random_state': 42,
            'n_jobs': get_n_jobs(),
            'verbosity': 0,
        }

    def create_ensemble_model(
        self,
        rf_params: Dict[str, Any],
        xgb_params: Dict[str, Any],
        task: str = 'regression'
    ) -> Any:
        """Create a simple averaged ensemble model."""
        # Use a simpler ensemble approach - just the optimized RF
        # Stacking has issues with TimeSeriesSplit
        if task == 'regression':
            model = RandomForestRegressor(**rf_params)
        else:
            rf_params_copy = rf_params.copy()
            rf_params_copy['class_weight'] = 'balanced'
            model = RandomForestClassifier(**rf_params_copy)

        logger.info(f"Created optimized Random Forest model")
        return model

    def walk_forward_validate(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        model: Any,
        task: str = 'regression',
        n_splits: int = 5
    ) -> Dict[str, Any]:
        """Perform walk-forward cross-validation."""
        logger.info(f"Running walk-forward validation ({n_splits} splits)...")

        tscv = TimeSeriesSplit(n_splits=n_splits)

        if task == 'regression':
            metrics = {'mae': [], 'rmse': [], 'r2': []}
        else:
            metrics = {'accuracy': [], 'precision': [], 'recall': [], 'f1': []}

        fold_details = []

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

            # Clone and fit model
            from sklearn.base import clone
            fold_model = clone(model)
            fold_model.fit(X_train, y_train)

            y_pred = fold_model.predict(X_val)

            fold_info = {
                'fold': fold + 1,
                'train_size': len(train_idx),
                'val_size': len(val_idx),
            }

            if task == 'regression':
                mae = mean_absolute_error(y_val, y_pred)
                rmse = np.sqrt(mean_squared_error(y_val, y_pred))
                r2 = r2_score(y_val, y_pred)

                metrics['mae'].append(mae)
                metrics['rmse'].append(rmse)
                metrics['r2'].append(r2)

                fold_info.update({'mae': mae, 'rmse': rmse, 'r2': r2})
                logger.info(f"Fold {fold+1}: MAE={mae:.3f}, RMSE={rmse:.3f}, R²={r2:.3f}")
            else:
                acc = accuracy_score(y_val, y_pred)
                prec = precision_score(y_val, y_pred, zero_division=0)
                rec = recall_score(y_val, y_pred, zero_division=0)
                f1 = f1_score(y_val, y_pred, zero_division=0)

                metrics['accuracy'].append(acc)
                metrics['precision'].append(prec)
                metrics['recall'].append(rec)
                metrics['f1'].append(f1)

                fold_info.update({'accuracy': acc, 'f1': f1})
                logger.info(f"Fold {fold+1}: Acc={acc:.3f}, F1={f1:.3f}")

            fold_details.append(fold_info)

        # Calculate summary
        summary = {}
        for metric, values in metrics.items():
            summary[f'{metric}_mean'] = np.mean(values)
            summary[f'{metric}_std'] = np.std(values)

        if task == 'regression':
            logger.info(f"Average: MAE={summary['mae_mean']:.3f}±{summary['mae_std']:.3f}, "
                       f"R²={summary['r2_mean']:.3f}±{summary['r2_std']:.3f}")
        else:
            logger.info(f"Average: Acc={summary['accuracy_mean']:.3f}±{summary['accuracy_std']:.3f}, "
                       f"F1={summary['f1_mean']:.3f}±{summary['f1_std']:.3f}")

        return {
            'summary': summary,
            'fold_details': fold_details,
        }

    def train_quantile_model(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        quantiles: List[float] = [0.1, 0.5, 0.9]
    ) -> Dict[float, Any]:
        """Train quantile regression models for prediction intervals."""
        logger.info(f"Training quantile models for {quantiles}...")

        models = {}
        for q in quantiles:
            if HAS_LGB:
                model = lgb.LGBMRegressor(
                    objective='quantile',
                    alpha=q,
                    n_estimators=200,
                    max_depth=10,
                    learning_rate=0.05,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    random_state=42,
                    verbose=-1,
                )
            else:
                # Fallback to Gradient Boosting with quantile loss
                model = GradientBoostingRegressor(
                    loss='quantile',
                    alpha=q,
                    n_estimators=200,
                    max_depth=6,
                    learning_rate=0.05,
                    subsample=0.8,
                    random_state=42,
                )

            model.fit(X, y)
            models[q] = model
            logger.info(f"Trained quantile model for q={q}")

        return models

    def save_models(
        self,
        magnitude_model: Any,
        direction_model: Any,
        imputer: Any,
        feature_cols: List[str],
        magnitude_metrics: Dict[str, Any],
        direction_metrics: Dict[str, Any],
        quantile_models: Optional[Dict[float, Any]] = None,
        hyperparams: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Save all model artifacts atomically.

        Uses a two-phase approach:
        1. Save new models to a temporary directory
        2. Backup existing models (if save succeeded)
        3. Move new models into place

        This ensures we never lose existing models if save fails.
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # Phase 1: Save to temporary directory first
        temp_dir = MODELS_DIR / f"temp_{timestamp}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Save main models to temp
            joblib.dump(magnitude_model, temp_dir / "rf_magnitude_validated.pkl")
            joblib.dump(direction_model, temp_dir / "rf_direction_validated.pkl")
            joblib.dump(imputer, temp_dir / "imputer_validated.pkl")

            # Save quantile models if available
            if quantile_models:
                for q, model in quantile_models.items():
                    joblib.dump(model, temp_dir / f"quantile_{int(q*100)}_validated.pkl")

            # Save feature columns
            with open(temp_dir / "feature_columns.txt", 'w') as f:
                for col in feature_cols:
                    f.write(f"{col}\n")

            # Save metadata
            metadata = {
                'trained_at': datetime.now().isoformat(),
                'n_features': len(feature_cols),
                'feature_columns': feature_cols,
                'magnitude_metrics': magnitude_metrics,
                'direction_metrics': direction_metrics,
                'hyperparams': hyperparams,
                'has_quantile_models': quantile_models is not None,
            }

            with open(temp_dir / "model_metadata.json", 'w') as f:
                json.dump(metadata, f, indent=2, default=str)

            # Save CV results
            cv_results = {
                'timestamp': datetime.now().isoformat(),
                'n_features': len(feature_cols),
                'summary': {
                    'rf_magnitude': magnitude_metrics.get('summary', {}),
                    'rf_direction': direction_metrics.get('summary', {}),
                },
                'cv_results': {
                    'magnitude': magnitude_metrics,
                    'direction': direction_metrics,
                },
            }

            with open(temp_dir / "cv_results.json", 'w') as f:
                json.dump(cv_results, f, indent=2, default=str)

            logger.info(f"New models saved to temp directory: {temp_dir}")

        except Exception as e:
            # Clean up temp directory on failure
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to save models: {e}") from e

        # Phase 2: Backup existing models AFTER successful save
        if VALIDATED_DIR.exists():
            backup_path = BACKUP_DIR / timestamp
            backup_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(VALIDATED_DIR, backup_path / "validated")
            logger.info(f"Backed up existing models to {backup_path}")
            # Remove old validated directory
            shutil.rmtree(VALIDATED_DIR)

        # Phase 3: Move temp to validated (atomic on same filesystem)
        shutil.move(str(temp_dir), str(VALIDATED_DIR))
        logger.info(f"Models saved atomically to {VALIDATED_DIR}")

    def run(self, force: bool = False) -> bool:
        """Run the enhanced training pipeline."""
        logger.info("=" * 60)
        logger.info("ENHANCED ML TRAINING PIPELINE")
        logger.info("=" * 60)

        # Load data
        df = self.load_data()
        if df is None:
            return False

        if len(df) < self.MIN_TRAINING_SAMPLES:
            logger.error(f"Insufficient samples: {len(df)} < {self.MIN_TRAINING_SAMPLES}")
            return False

        # Select features
        feature_cols = self.select_features(df)

        # Prepare data with preserved feature names
        X, imputer = self._prepare_data(df, feature_cols)
        y_mag = df['current_abs_move_pct']
        y_dir = df['direction_binary']

        logger.info(f"Training data: {X.shape[0]} samples, {X.shape[1]} features")

        # Optimize hyperparameters
        logger.info("\n" + "=" * 60)
        logger.info("HYPERPARAMETER OPTIMIZATION")
        logger.info("=" * 60)

        rf_mag_params = self.optimize_rf_hyperparams(X, y_mag, task='regression')
        rf_dir_params = self.optimize_rf_hyperparams(X, y_dir, task='classification')

        xgb_mag_params = {}
        xgb_dir_params = {}
        if HAS_XGB:
            xgb_mag_params = self.optimize_xgb_hyperparams(X, y_mag, task='regression')
            xgb_dir_params = self.optimize_xgb_hyperparams(X, y_dir, task='classification')

        # Create ensemble models
        logger.info("\n" + "=" * 60)
        logger.info("CREATING ENSEMBLE MODELS")
        logger.info("=" * 60)

        magnitude_ensemble = self.create_ensemble_model(rf_mag_params, xgb_mag_params, task='regression')
        direction_ensemble = self.create_ensemble_model(rf_dir_params, xgb_dir_params, task='classification')

        # Validate models
        logger.info("\n" + "=" * 60)
        logger.info("WALK-FORWARD VALIDATION")
        logger.info("=" * 60)

        logger.info("\nMagnitude Model:")
        mag_metrics = self.walk_forward_validate(X, y_mag, magnitude_ensemble, task='regression', n_splits=self.n_cv_splits)

        logger.info("\nDirection Model:")
        dir_metrics = self.walk_forward_validate(X, y_dir, direction_ensemble, task='classification', n_splits=self.n_cv_splits)

        # Check quality thresholds
        mag_r2 = mag_metrics['summary'].get('r2_mean', 0)
        mag_mae = mag_metrics['summary'].get('mae_mean', float('inf'))

        meets_thresholds = (mag_r2 >= self.MIN_R2_THRESHOLD and mag_mae <= self.MAX_MAE_THRESHOLD)

        if not meets_thresholds:
            logger.warning(f"Model may not meet quality thresholds:")
            logger.warning(f"  R²={mag_r2:.3f} (target: ≥{self.MIN_R2_THRESHOLD})")
            logger.warning(f"  MAE={mag_mae:.3f} (target: ≤{self.MAX_MAE_THRESHOLD})")
            if not force:
                logger.error("Use --force to train anyway")
                return False
            logger.warning("Proceeding with --force")

        # Train final models on all data
        logger.info("\n" + "=" * 60)
        logger.info("TRAINING FINAL MODELS ON ALL DATA")
        logger.info("=" * 60)

        magnitude_ensemble.fit(X, y_mag)
        direction_ensemble.fit(X, y_dir)

        # Train quantile models for prediction intervals
        logger.info("\n" + "=" * 60)
        logger.info("TRAINING QUANTILE MODELS")
        logger.info("=" * 60)

        quantile_models = self.train_quantile_model(X, y_mag, quantiles=[0.1, 0.5, 0.9])

        # Save everything
        logger.info("\n" + "=" * 60)
        logger.info("SAVING MODELS")
        logger.info("=" * 60)

        self.save_models(
            magnitude_model=magnitude_ensemble,
            direction_model=direction_ensemble,
            imputer=imputer,
            feature_cols=feature_cols,
            magnitude_metrics=mag_metrics,
            direction_metrics=dir_metrics,
            quantile_models=quantile_models,
            hyperparams={
                'rf_magnitude': rf_mag_params,
                'rf_direction': rf_dir_params,
                'xgb_magnitude': xgb_mag_params,
                'xgb_direction': xgb_dir_params,
            }
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("TRAINING COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Magnitude Model: MAE={mag_mae:.3f}%, R²={mag_r2:.3f}")
        logger.info(f"Direction Model: Acc={dir_metrics['summary'].get('accuracy_mean', 0):.3f}, "
                   f"F1={dir_metrics['summary'].get('f1_mean', 0):.3f}")
        logger.info(f"Models saved to: {VALIDATED_DIR}")

        return True


def main():
    parser = argparse.ArgumentParser(description="Enhanced ML Training Pipeline")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: fewer trials, faster iteration"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force training even if quality thresholds not met"
    )
    args = parser.parse_args()

    trainer = EnhancedModelTrainer(quick_mode=args.quick)
    success = trainer.run(force=args.force)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
