#!/usr/bin/env python3
"""
Feature Selection for 3.0 ML System

Uses multiple methods to identify the most important features:
1. Random Forest feature importance
2. Recursive Feature Elimination (RFE)
3. Correlation-based filtering
4. Statistical tests (mutual information)
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
import warnings
from datetime import datetime

from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import (
    RFE, mutual_info_classif, SelectKBest
)
from sklearn.impute import SimpleImputer
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
import xgboost as xgb

warnings.filterwarnings('ignore')

DATA_DIR = Path(__file__).parent.parent / "data" / "features"
OUTPUT_DIR = Path(__file__).parent.parent / "models" / "feature_selection"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_data():
    """Load and prepare data."""
    df = pd.read_parquet(DATA_DIR / 'all_features.parquet')

    target_magnitude = 'current_abs_move_pct'
    target_direction = 'current_close_move_pct'

    df_clean = df.dropna(subset=[target_magnitude, target_direction]).copy()
    df_clean['direction_binary'] = (df_clean[target_direction] > 0).astype(int)
    df_clean = df_clean.sort_values('earnings_date').reset_index(drop=True)

    exclude_cols = ['ticker', 'earnings_date', target_magnitude, target_direction,
                    'earnings_frequency', 'vix_regime', 'market_regime', 'direction_binary']

    feature_cols = [col for col in df_clean.columns
                    if col not in exclude_cols
                    and df_clean[col].dtype in ['int64', 'float64', 'int32', 'float32']]

    # Filter >50% missing
    missing_pct = (df_clean[feature_cols].isna().sum() / len(df_clean)) * 100
    feature_cols = [c for c in feature_cols if missing_pct[c] <= 50]

    print(f"Dataset: {len(df_clean)} samples, {len(feature_cols)} features")
    return df_clean, feature_cols


def rf_importance_selection(X, y, feature_cols, top_n=30):
    """Select top features by Random Forest importance."""
    print("\n" + "="*60)
    print("1. RANDOM FOREST IMPORTANCE")
    print("="*60)

    rf = RandomForestClassifier(
        n_estimators=200, max_depth=15, min_samples_split=10,
        random_state=42, n_jobs=-1, class_weight='balanced'
    )
    rf.fit(X, y)

    importance_df = pd.DataFrame({
        'feature': feature_cols,
        'importance': rf.feature_importances_
    }).sort_values('importance', ascending=False)

    print(f"\nTop {top_n} features by RF importance:")
    for i, row in importance_df.head(top_n).iterrows():
        print(f"  {row['feature']:35s} {row['importance']:.4f}")

    top_features = importance_df.head(top_n)['feature'].tolist()
    return top_features, importance_df


def mutual_info_selection(X, y, feature_cols, top_n=30):
    """Select top features by mutual information."""
    print("\n" + "="*60)
    print("2. MUTUAL INFORMATION")
    print("="*60)

    mi_scores = mutual_info_classif(X, y, random_state=42)

    mi_df = pd.DataFrame({
        'feature': feature_cols,
        'mi_score': mi_scores
    }).sort_values('mi_score', ascending=False)

    print(f"\nTop {top_n} features by mutual information:")
    for i, row in mi_df.head(top_n).iterrows():
        print(f"  {row['feature']:35s} {row['mi_score']:.4f}")

    top_features = mi_df.head(top_n)['feature'].tolist()
    return top_features, mi_df


def rfe_selection(X, y, feature_cols, top_n=30):
    """Select features using Recursive Feature Elimination."""
    print("\n" + "="*60)
    print("3. RECURSIVE FEATURE ELIMINATION")
    print("="*60)

    # Use a smaller estimator for RFE (faster)
    rf = RandomForestClassifier(
        n_estimators=50, max_depth=10,
        random_state=42, n_jobs=-1, class_weight='balanced'
    )

    rfe = RFE(estimator=rf, n_features_to_select=top_n, step=5)
    rfe.fit(X, y)

    rfe_df = pd.DataFrame({
        'feature': feature_cols,
        'selected': rfe.support_,
        'ranking': rfe.ranking_
    }).sort_values('ranking')

    selected = rfe_df[rfe_df['selected']]['feature'].tolist()
    print(f"\nSelected {len(selected)} features via RFE:")
    for feat in selected[:20]:
        print(f"  {feat}")
    if len(selected) > 20:
        print(f"  ... and {len(selected) - 20} more")

    return selected, rfe_df


def correlation_filter(df, feature_cols, threshold=0.95):
    """Remove highly correlated features."""
    print("\n" + "="*60)
    print("4. CORRELATION FILTERING")
    print("="*60)

    corr_matrix = df[feature_cols].corr().abs()
    upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))

    # Find features to drop
    to_drop = []
    for column in upper.columns:
        highly_corr = upper.index[upper[column] > threshold].tolist()
        if highly_corr:
            to_drop.extend(highly_corr)

    to_drop = list(set(to_drop))
    filtered = [c for c in feature_cols if c not in to_drop]

    print(f"\nDropped {len(to_drop)} highly correlated features (>{threshold}):")
    for feat in to_drop[:10]:
        print(f"  {feat}")
    if len(to_drop) > 10:
        print(f"  ... and {len(to_drop) - 10} more")

    print(f"\nRemaining: {len(filtered)} features")
    return filtered, to_drop


def evaluate_feature_sets(X_full, y, feature_cols, feature_sets, n_splits=5):
    """Evaluate different feature sets with cross-validation."""
    print("\n" + "="*60)
    print("FEATURE SET EVALUATION")
    print("="*60)

    tscv = TimeSeriesSplit(n_splits=n_splits)
    results = {}

    for name, features in feature_sets.items():
        print(f"\nEvaluating: {name} ({len(features)} features)")

        # Get indices of selected features
        feature_indices = [feature_cols.index(f) for f in features if f in feature_cols]
        X_subset = X_full[:, feature_indices]

        # RF
        rf = RandomForestClassifier(
            n_estimators=200, max_depth=15, min_samples_split=10,
            random_state=42, n_jobs=-1, class_weight='balanced'
        )
        rf_scores = cross_val_score(rf, X_subset, y, cv=tscv, scoring='accuracy')

        # XGBoost
        xgb_clf = xgb.XGBClassifier(
            n_estimators=200, max_depth=6, learning_rate=0.1,
            random_state=42, n_jobs=-1, verbosity=0
        )
        xgb_scores = cross_val_score(xgb_clf, X_subset, y, cv=tscv, scoring='accuracy')

        results[name] = {
            'n_features': len(features),
            'rf_mean': np.mean(rf_scores),
            'rf_std': np.std(rf_scores),
            'xgb_mean': np.mean(xgb_scores),
            'xgb_std': np.std(xgb_scores)
        }

        print(f"  RF:  {np.mean(rf_scores):.3f} ± {np.std(rf_scores):.3f}")
        print(f"  XGB: {np.mean(xgb_scores):.3f} ± {np.std(xgb_scores):.3f}")

    return results


def main():
    print("="*60)
    print("3.0 ML SYSTEM - FEATURE SELECTION")
    print("="*60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Load data
    df, feature_cols = load_data()

    # Prepare features
    imputer = SimpleImputer(strategy='median')
    X = imputer.fit_transform(df[feature_cols])
    y = df['direction_binary'].values

    print(f"\nFeature matrix: {X.shape}")

    # 1. Random Forest importance
    rf_top, rf_importance_df = rf_importance_selection(X, y, feature_cols, top_n=25)

    # 2. Mutual Information
    mi_top, mi_df = mutual_info_selection(X, y, feature_cols, top_n=25)

    # 3. RFE
    rfe_selected, rfe_df = rfe_selection(X, y, feature_cols, top_n=25)

    # 4. Correlation filtering
    corr_filtered, dropped = correlation_filter(df, feature_cols, threshold=0.95)

    # Find consensus features (appear in multiple methods)
    from collections import Counter
    all_selected = rf_top + mi_top + rfe_selected
    feature_counts = Counter(all_selected)
    consensus_features = [f for f, count in feature_counts.most_common() if count >= 2][:25]

    print("\n" + "="*60)
    print("CONSENSUS FEATURES (appear in 2+ methods)")
    print("="*60)
    for feat in consensus_features:
        count = feature_counts[feat]
        print(f"  {feat:35s} ({count}/3 methods)")

    # Evaluate feature sets
    feature_sets = {
        'all_features': feature_cols,
        'rf_top25': rf_top,
        'mi_top25': mi_top,
        'rfe_top25': rfe_selected,
        'consensus': consensus_features,
        'corr_filtered': corr_filtered
    }

    eval_results = evaluate_feature_sets(X, y, feature_cols, feature_sets)

    # Find best feature set
    best_set = max(eval_results.items(), key=lambda x: x[1]['rf_mean'])
    print("\n" + "="*60)
    print("BEST FEATURE SET")
    print("="*60)
    print(f"  Name: {best_set[0]}")
    print(f"  Features: {best_set[1]['n_features']}")
    print(f"  RF Accuracy: {best_set[1]['rf_mean']:.3f} ± {best_set[1]['rf_std']:.3f}")
    print(f"  XGB Accuracy: {best_set[1]['xgb_mean']:.3f} ± {best_set[1]['xgb_std']:.3f}")

    # Save results
    results = {
        'rf_importance': rf_importance_df.to_dict('records'),
        'mutual_info': mi_df.to_dict('records'),
        'consensus_features': consensus_features,
        'evaluation': eval_results,
        'best_feature_set': best_set[0],
        'best_features': feature_sets[best_set[0]],
        'timestamp': datetime.now().isoformat()
    }

    with open(OUTPUT_DIR / 'feature_selection_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)

    # Save best features list
    with open(OUTPUT_DIR / 'best_features.txt', 'w') as f:
        f.write('\n'.join(feature_sets[best_set[0]]))

    print(f"\n{'='*60}")
    print("COMPLETE")
    print(f"{'='*60}")
    print(f"Results saved to: {OUTPUT_DIR}")

    return results


if __name__ == '__main__':
    main()
