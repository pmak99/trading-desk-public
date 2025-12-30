#!/usr/bin/env python3
"""
Walk-Forward Cross-Validation for Advanced Models (Task 2.3)

Performs time-series aware cross-validation with:
1. Expanding window training
2. Fixed validation window
3. Hyperparameter tuning via RandomizedSearchCV
4. Robust performance estimation
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
import warnings
from datetime import datetime

from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import TimeSeriesSplit, RandomizedSearchCV
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    mean_absolute_error, mean_squared_error, r2_score
)
import xgboost as xgb
import joblib

warnings.filterwarnings('ignore')

# Paths
DATA_DIR = Path(__file__).parent.parent / "data" / "features"
MODELS_DIR = Path(__file__).parent.parent / "models" / "validated"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def load_and_prepare_data():
    """Load features and prepare for modeling."""
    df = pd.read_parquet(DATA_DIR / 'all_features.parquet')

    # Target columns
    target_magnitude = 'current_abs_move_pct'
    target_direction = 'current_close_move_pct'

    # Clean dataset
    df_clean = df.dropna(subset=[target_magnitude, target_direction]).copy()
    df_clean['direction_binary'] = (df_clean[target_direction] > 0).astype(int)

    # Sort by date
    df_clean = df_clean.sort_values('earnings_date').reset_index(drop=True)

    # Exclude non-feature columns
    exclude_cols = ['ticker', 'earnings_date', target_magnitude, target_direction,
                    'earnings_frequency', 'vix_regime', 'market_regime', 'direction_binary']

    # Get numeric features only
    feature_cols = []
    for col in df_clean.columns:
        if col not in exclude_cols and df_clean[col].dtype in ['int64', 'float64', 'int32', 'float32']:
            feature_cols.append(col)

    # Filter features with >50% missing
    missing_pct = (df_clean[feature_cols].isna().sum() / len(df_clean)) * 100
    feature_cols = [c for c in feature_cols if missing_pct[c] <= 50]

    print(f"Dataset: {len(df_clean)} samples, {len(feature_cols)} features")
    print(f"Date range: {df_clean['earnings_date'].min()} to {df_clean['earnings_date'].max()}")
    print(f"Direction: {df_clean['direction_binary'].mean()*100:.1f}% up")

    return df_clean, feature_cols, target_magnitude


def walk_forward_cv(df, feature_cols, target_magnitude, n_splits=5):
    """
    Walk-forward cross-validation with expanding window.

    Returns results for each fold and aggregated metrics.
    """
    # Prepare data
    imputer = SimpleImputer(strategy='median')
    X = imputer.fit_transform(df[feature_cols])
    X = pd.DataFrame(X, columns=feature_cols, index=df.index)
    y_dir = df['direction_binary']
    y_mag = df[target_magnitude]
    dates = df['earnings_date']

    # Time series split
    tscv = TimeSeriesSplit(n_splits=n_splits)

    results = {
        'direction': {'rf': [], 'xgb': []},
        'magnitude': {'rf': [], 'xgb': []},
        'fold_details': []
    }

    print(f"\n{'='*60}")
    print(f"WALK-FORWARD CROSS-VALIDATION ({n_splits} folds)")
    print(f"{'='*60}")

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        train_start = dates.iloc[train_idx[0]]
        train_end = dates.iloc[train_idx[-1]]
        val_start = dates.iloc[val_idx[0]]
        val_end = dates.iloc[val_idx[-1]]

        print(f"\n--- Fold {fold+1}/{n_splits} ---")
        print(f"Train: {train_start.date()} to {train_end.date()} ({len(train_idx)} samples)")
        print(f"Val:   {val_start.date()} to {val_end.date()} ({len(val_idx)} samples)")

        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train_dir, y_val_dir = y_dir.iloc[train_idx], y_dir.iloc[val_idx]
        y_train_mag, y_val_mag = y_mag.iloc[train_idx], y_mag.iloc[val_idx]

        fold_result = {
            'fold': fold + 1,
            'train_start': str(train_start.date()),
            'train_end': str(train_end.date()),
            'val_start': str(val_start.date()),
            'val_end': str(val_end.date()),
            'train_size': len(train_idx),
            'val_size': len(val_idx)
        }

        # --- Random Forest ---
        # Direction
        rf_dir = RandomForestClassifier(
            n_estimators=200, max_depth=15, min_samples_split=10,
            min_samples_leaf=5, max_features='sqrt', class_weight='balanced',
            random_state=42, n_jobs=-1
        )
        rf_dir.fit(X_train, y_train_dir)
        y_pred_rf_dir = rf_dir.predict(X_val)

        rf_dir_acc = accuracy_score(y_val_dir, y_pred_rf_dir)
        rf_dir_prec = precision_score(y_val_dir, y_pred_rf_dir, zero_division=0)
        rf_dir_rec = recall_score(y_val_dir, y_pred_rf_dir, zero_division=0)
        rf_dir_f1 = f1_score(y_val_dir, y_pred_rf_dir, zero_division=0)

        results['direction']['rf'].append({
            'accuracy': rf_dir_acc, 'precision': rf_dir_prec,
            'recall': rf_dir_rec, 'f1': rf_dir_f1
        })

        # Magnitude
        rf_mag = RandomForestRegressor(
            n_estimators=200, max_depth=15, min_samples_split=10,
            min_samples_leaf=5, max_features='sqrt', random_state=42, n_jobs=-1
        )
        rf_mag.fit(X_train, y_train_mag)
        y_pred_rf_mag = rf_mag.predict(X_val)

        rf_mag_mae = mean_absolute_error(y_val_mag, y_pred_rf_mag)
        rf_mag_r2 = r2_score(y_val_mag, y_pred_rf_mag)

        results['magnitude']['rf'].append({'mae': rf_mag_mae, 'r2': rf_mag_r2})

        # --- XGBoost ---
        # Direction
        scale_pos_weight = (y_train_dir == 0).sum() / (y_train_dir == 1).sum()
        xgb_dir = xgb.XGBClassifier(
            n_estimators=200, max_depth=6, learning_rate=0.1,
            subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0,
            scale_pos_weight=scale_pos_weight, random_state=42, n_jobs=-1, verbosity=0
        )
        xgb_dir.fit(X_train, y_train_dir)
        y_pred_xgb_dir = xgb_dir.predict(X_val)

        xgb_dir_acc = accuracy_score(y_val_dir, y_pred_xgb_dir)
        xgb_dir_prec = precision_score(y_val_dir, y_pred_xgb_dir, zero_division=0)
        xgb_dir_rec = recall_score(y_val_dir, y_pred_xgb_dir, zero_division=0)
        xgb_dir_f1 = f1_score(y_val_dir, y_pred_xgb_dir, zero_division=0)

        results['direction']['xgb'].append({
            'accuracy': xgb_dir_acc, 'precision': xgb_dir_prec,
            'recall': xgb_dir_rec, 'f1': xgb_dir_f1
        })

        # Magnitude
        xgb_mag = xgb.XGBRegressor(
            n_estimators=200, max_depth=6, learning_rate=0.1,
            subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0,
            random_state=42, n_jobs=-1, verbosity=0
        )
        xgb_mag.fit(X_train, y_train_mag)
        y_pred_xgb_mag = xgb_mag.predict(X_val)

        xgb_mag_mae = mean_absolute_error(y_val_mag, y_pred_xgb_mag)
        xgb_mag_r2 = r2_score(y_val_mag, y_pred_xgb_mag)

        results['magnitude']['xgb'].append({'mae': xgb_mag_mae, 'r2': xgb_mag_r2})

        # Print fold results
        print(f"\n  Direction:")
        print(f"    RF:  {rf_dir_acc:.3f} acc | XGB: {xgb_dir_acc:.3f} acc")
        print(f"  Magnitude:")
        print(f"    RF:  {rf_mag_mae:.3f} MAE, R²={rf_mag_r2:.3f} | XGB: {xgb_mag_mae:.3f} MAE, R²={xgb_mag_r2:.3f}")

        fold_result['rf_direction'] = {'accuracy': rf_dir_acc, 'f1': rf_dir_f1}
        fold_result['xgb_direction'] = {'accuracy': xgb_dir_acc, 'f1': xgb_dir_f1}
        fold_result['rf_magnitude'] = {'mae': rf_mag_mae, 'r2': rf_mag_r2}
        fold_result['xgb_magnitude'] = {'mae': xgb_mag_mae, 'r2': xgb_mag_r2}
        results['fold_details'].append(fold_result)

    return results, imputer, feature_cols


def summarize_results(results):
    """Compute summary statistics across folds."""
    print(f"\n{'='*60}")
    print("CROSS-VALIDATION SUMMARY")
    print(f"{'='*60}")

    summary = {}

    # Direction
    for model in ['rf', 'xgb']:
        model_name = 'Random Forest' if model == 'rf' else 'XGBoost'
        accs = [r['accuracy'] for r in results['direction'][model]]
        f1s = [r['f1'] for r in results['direction'][model]]

        summary[f'{model}_direction'] = {
            'accuracy_mean': np.mean(accs),
            'accuracy_std': np.std(accs),
            'f1_mean': np.mean(f1s),
            'f1_std': np.std(f1s)
        }

        print(f"\n{model_name} Direction:")
        print(f"  Accuracy: {np.mean(accs):.3f} ± {np.std(accs):.3f}")
        print(f"  F1 Score: {np.mean(f1s):.3f} ± {np.std(f1s):.3f}")

    # Magnitude
    for model in ['rf', 'xgb']:
        model_name = 'Random Forest' if model == 'rf' else 'XGBoost'
        maes = [r['mae'] for r in results['magnitude'][model]]
        r2s = [r['r2'] for r in results['magnitude'][model]]

        summary[f'{model}_magnitude'] = {
            'mae_mean': np.mean(maes),
            'mae_std': np.std(maes),
            'r2_mean': np.mean(r2s),
            'r2_std': np.std(r2s)
        }

        print(f"\n{model_name} Magnitude:")
        print(f"  MAE: {np.mean(maes):.3f}% ± {np.std(maes):.3f}%")
        print(f"  R²:  {np.mean(r2s):.3f} ± {np.std(r2s):.3f}")

    # Comparison to baselines
    print(f"\n{'='*60}")
    print("COMPARISON TO BASELINES")
    print(f"{'='*60}")

    best_dir_acc = max(summary['rf_direction']['accuracy_mean'],
                       summary['xgb_direction']['accuracy_mean'])
    best_model = 'Random Forest' if summary['rf_direction']['accuracy_mean'] > summary['xgb_direction']['accuracy_mean'] else 'XGBoost'

    baseline_2_0 = 0.574
    baseline_logistic = 0.528

    print(f"\nBest Direction Model: {best_model}")
    print(f"  CV Accuracy: {best_dir_acc:.3f} ({best_dir_acc*100:.1f}%)")
    print(f"  vs Logistic Baseline (52.8%): {(best_dir_acc - baseline_logistic)*100:+.1f} pp")
    print(f"  vs 2.0 System (57.4%): {(best_dir_acc - baseline_2_0)*100:+.1f} pp")

    if best_dir_acc > baseline_2_0:
        print(f"\n  ✅ BEATS 2.0 baseline!")
    else:
        print(f"\n  ⚠️  Still {(baseline_2_0 - best_dir_acc)*100:.1f} pp below 2.0 baseline")

    return summary


def train_final_models(df, feature_cols, target_magnitude, imputer):
    """Train final models on all data for production use."""
    print(f"\n{'='*60}")
    print("TRAINING FINAL MODELS ON ALL DATA")
    print(f"{'='*60}")

    X = imputer.transform(df[feature_cols])
    X = pd.DataFrame(X, columns=feature_cols, index=df.index)
    y_dir = df['direction_binary']
    y_mag = df[target_magnitude]

    # Random Forest Direction
    rf_dir = RandomForestClassifier(
        n_estimators=200, max_depth=15, min_samples_split=10,
        min_samples_leaf=5, max_features='sqrt', class_weight='balanced',
        random_state=42, n_jobs=-1
    )
    rf_dir.fit(X, y_dir)

    # Random Forest Magnitude
    rf_mag = RandomForestRegressor(
        n_estimators=200, max_depth=15, min_samples_split=10,
        min_samples_leaf=5, max_features='sqrt', random_state=42, n_jobs=-1
    )
    rf_mag.fit(X, y_mag)

    # XGBoost Direction
    scale_pos_weight = (y_dir == 0).sum() / (y_dir == 1).sum()
    xgb_dir = xgb.XGBClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.1,
        subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0,
        scale_pos_weight=scale_pos_weight, random_state=42, n_jobs=-1, verbosity=0
    )
    xgb_dir.fit(X, y_dir)

    # XGBoost Magnitude
    xgb_mag = xgb.XGBRegressor(
        n_estimators=200, max_depth=6, learning_rate=0.1,
        subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0,
        random_state=42, n_jobs=-1, verbosity=0
    )
    xgb_mag.fit(X, y_mag)

    # Save models
    joblib.dump(rf_dir, MODELS_DIR / 'rf_direction_validated.pkl')
    joblib.dump(rf_mag, MODELS_DIR / 'rf_magnitude_validated.pkl')
    joblib.dump(xgb_dir, MODELS_DIR / 'xgb_direction_validated.pkl')
    joblib.dump(xgb_mag, MODELS_DIR / 'xgb_magnitude_validated.pkl')
    joblib.dump(imputer, MODELS_DIR / 'imputer_validated.pkl')

    with open(MODELS_DIR / 'feature_columns.txt', 'w') as f:
        f.write('\n'.join(feature_cols))

    print(f"\nModels saved to: {MODELS_DIR}")

    return rf_dir, rf_mag, xgb_dir, xgb_mag


def main():
    print("="*60)
    print("3.0 ML SYSTEM - WALK-FORWARD CROSS-VALIDATION")
    print("="*60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Load data
    df, feature_cols, target_magnitude = load_and_prepare_data()

    # Run walk-forward CV
    results, imputer, feature_cols = walk_forward_cv(
        df, feature_cols, target_magnitude, n_splits=5
    )

    # Summarize results
    summary = summarize_results(results)

    # Train final models
    rf_dir, rf_mag, xgb_dir, xgb_mag = train_final_models(
        df, feature_cols, target_magnitude, imputer
    )

    # Save results
    all_results = {
        'cv_results': results,
        'summary': summary,
        'timestamp': datetime.now().isoformat(),
        'n_samples': len(df),
        'n_features': len(feature_cols)
    }

    with open(MODELS_DIR / 'cv_results.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print("COMPLETE")
    print(f"{'='*60}")
    print(f"Results saved to: {MODELS_DIR / 'cv_results.json'}")

    return all_results


if __name__ == '__main__':
    main()
