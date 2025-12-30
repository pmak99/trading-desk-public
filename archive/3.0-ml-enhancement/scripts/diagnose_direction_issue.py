"""Diagnose the 99.9% direction prediction accuracy anomaly.

This script investigates potential causes:
1. Class imbalance across time periods
2. Data leakage in features
3. Target variable encoding issues
4. Feature-target correlations
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json

print("=" * 60)
print("DIRECTION PREDICTION DIAGNOSTIC ANALYSIS")
print("=" * 60)

# Load features
data_dir = Path(__file__).parent.parent / "data" / "features"
features_df = pd.read_parquet(data_dir / 'all_features.parquet')
print(f"\nLoaded features: {features_df.shape}")
print(f"Date range: {features_df['earnings_date'].min()} to {features_df['earnings_date'].max()}")

# Target variables
target_magnitude = 'current_abs_move_pct'
target_direction = 'current_close_move_pct'

# Clean dataset
df_clean = features_df.dropna(subset=[target_magnitude, target_direction]).copy()
df_clean['direction_binary'] = (df_clean[target_direction] > 0).astype(int)

print(f"\nClean dataset: {len(df_clean)} rows")

# 1. Overall class distribution
print("\n" + "=" * 60)
print("1. OVERALL CLASS DISTRIBUTION")
print("=" * 60)

up_count = df_clean['direction_binary'].sum()
down_count = len(df_clean) - up_count
total = len(df_clean)

print(f"\nUp moves (1):   {up_count:,} ({up_count/total*100:.1f}%)")
print(f"Down moves (0): {down_count:,} ({down_count/total*100:.1f}%)")
print(f"Total:          {total:,}")

# 2. Class distribution by year
print("\n" + "=" * 60)
print("2. CLASS DISTRIBUTION BY YEAR")
print("=" * 60)

df_clean['year'] = df_clean['earnings_date'].dt.year
yearly_dist = df_clean.groupby('year')['direction_binary'].agg(['count', 'sum', 'mean'])
yearly_dist.columns = ['total', 'up_moves', 'up_pct']
yearly_dist['down_moves'] = yearly_dist['total'] - yearly_dist['up_moves']
yearly_dist['down_pct'] = 1 - yearly_dist['up_pct']

print("\nYear | Total | Up Moves | Down Moves | Up % | Down %")
print("-" * 60)
for year, row in yearly_dist.iterrows():
    print(f"{year} | {row['total']:5.0f} | {row['up_moves']:8.0f} | {row['down_moves']:10.0f} | {row['up_pct']*100:4.1f}% | {row['down_pct']*100:5.1f}%")

# Identify years with extreme imbalance
extreme_years = yearly_dist[(yearly_dist['up_pct'] > 0.95) | (yearly_dist['up_pct'] < 0.05)]
if len(extreme_years) > 0:
    print(f"\n⚠️  WARNING: {len(extreme_years)} year(s) with >95% or <5% up moves:")
    for year, row in extreme_years.iterrows():
        print(f"  {year}: {row['up_pct']*100:.1f}% up moves")

# 3. Check for data leakage - features highly correlated with target
print("\n" + "=" * 60)
print("3. FEATURE-TARGET CORRELATION ANALYSIS")
print("=" * 60)

# Get numeric features only
exclude_cols = ['ticker', 'earnings_date', target_magnitude, target_direction,
                'earnings_frequency', 'vix_regime', 'market_regime', 'direction_binary', 'year']
feature_cols = [col for col in df_clean.columns if col not in exclude_cols]

numeric_features = []
for col in feature_cols:
    if df_clean[col].dtype in ['int64', 'float64', 'int32', 'float32']:
        numeric_features.append(col)

print(f"\nAnalyzing {len(numeric_features)} numeric features...")

# Calculate correlation with direction_binary
correlations = []
for col in numeric_features:
    try:
        # Use only non-null values
        mask = df_clean[col].notna() & df_clean['direction_binary'].notna()
        if mask.sum() > 100:  # Need enough samples
            corr = df_clean.loc[mask, col].corr(df_clean.loc[mask, 'direction_binary'])
            correlations.append({
                'feature': col,
                'correlation': corr,
                'abs_correlation': abs(corr)
            })
    except:
        pass

corr_df = pd.DataFrame(correlations).sort_values('abs_correlation', ascending=False)

print("\nTop 15 features correlated with direction:")
print(corr_df[['feature', 'correlation']].head(15).to_string(index=False))

# Flag suspiciously high correlations
high_corr = corr_df[corr_df['abs_correlation'] > 0.5]
if len(high_corr) > 0:
    print(f"\n⚠️  WARNING: {len(high_corr)} feature(s) with |correlation| > 0.5:")
    for _, row in high_corr.head(10).iterrows():
        print(f"  {row['feature']}: {row['correlation']:.3f}")

# 4. Check 80/20 split used in final validation
print("\n" + "=" * 60)
print("4. FINAL VALIDATION SPLIT ANALYSIS (80/20)")
print("=" * 60)

df_sorted = df_clean.sort_values('earnings_date').reset_index(drop=True)
split_date = df_sorted['earnings_date'].quantile(0.8)

train_mask = df_sorted['earnings_date'] <= split_date
val_mask = df_sorted['earnings_date'] > split_date

train_data = df_sorted[train_mask]
val_data = df_sorted[val_mask]

print(f"\nSplit date: {split_date}")
print(f"\nTraining set ({len(train_data)} samples):")
print(f"  Date range: {train_data['earnings_date'].min()} to {train_data['earnings_date'].max()}")
print(f"  Up moves: {train_data['direction_binary'].sum()} ({train_data['direction_binary'].mean()*100:.1f}%)")
print(f"  Down moves: {(~train_data['direction_binary'].astype(bool)).sum()} ({(1-train_data['direction_binary'].mean())*100:.1f}%)")

print(f"\nValidation set ({len(val_data)} samples):")
print(f"  Date range: {val_data['earnings_date'].min()} to {val_data['earnings_date'].max()}")
print(f"  Up moves: {val_data['direction_binary'].sum()} ({val_data['direction_binary'].mean()*100:.1f}%)")
print(f"  Down moves: {(~val_data['direction_binary'].astype(bool)).sum()} ({(1-val_data['direction_binary'].mean())*100:.1f}%)")

# Check for class imbalance in validation set
val_up_pct = val_data['direction_binary'].mean()
if val_up_pct > 0.95 or val_up_pct < 0.05:
    print(f"\n⚠️  CRITICAL: Validation set has extreme class imbalance!")
    print(f"  {val_up_pct*100:.1f}% up moves in validation set")
    print(f"  This explains the 99.9% accuracy - model predicts majority class")

# 5. Examine actual magnitude distribution
print("\n" + "=" * 60)
print("5. MAGNITUDE DISTRIBUTION ANALYSIS")
print("=" * 60)

print(f"\nMagnitude statistics:")
print(f"  Mean: {df_clean[target_magnitude].mean():.2f}%")
print(f"  Median: {df_clean[target_magnitude].median():.2f}%")
print(f"  Std: {df_clean[target_magnitude].std():.2f}%")
print(f"  Min: {df_clean[target_magnitude].min():.2f}%")
print(f"  Max: {df_clean[target_magnitude].max():.2f}%")

print(f"\nDirection (close_move_pct) statistics:")
print(f"  Mean: {df_clean[target_direction].mean():.2f}%")
print(f"  Median: {df_clean[target_direction].median():.2f}%")
print(f"  Std: {df_clean[target_direction].std():.2f}%")
print(f"  Min: {df_clean[target_direction].min():.2f}%")
print(f"  Max: {df_clean[target_direction].max():.2f}%")

# 6. Summary and recommendations
print("\n" + "=" * 60)
print("SUMMARY AND RECOMMENDATIONS")
print("=" * 60)

issues_found = []

# Check for extreme class imbalance in validation
if val_up_pct > 0.95 or val_up_pct < 0.05:
    issues_found.append("VALIDATION_IMBALANCE")
    print("\n🔴 ISSUE 1: Extreme class imbalance in validation set")
    print(f"   The 80/20 split resulted in {val_up_pct*100:.1f}% up moves in validation")
    print(f"   Model achieves 99.9% by always predicting the majority class")
    print(f"   FIX: Use stratified splits or different validation strategy")

# Check for high feature-target correlation
if len(high_corr) > 0:
    issues_found.append("HIGH_CORRELATION")
    print(f"\n🟡 ISSUE 2: {len(high_corr)} feature(s) highly correlated with target")
    print(f"   Features with |corr| > 0.5 may contain leakage")
    print(f"   FIX: Review these features for potential lookahead bias")

# Check for temporal imbalance
if len(extreme_years) > 0:
    issues_found.append("TEMPORAL_IMBALANCE")
    print(f"\n🟡 ISSUE 3: {len(extreme_years)} year(s) with extreme class imbalance")
    print(f"   Some years have >95% or <5% up moves")
    print(f"   FIX: Consider time-aware sampling or different evaluation periods")

if len(issues_found) == 0:
    print("\n✅ No obvious issues detected - requires deeper investigation")

print(f"\nDiagnostic complete. Issues found: {', '.join(issues_found) if issues_found else 'None'}")
print("=" * 60)
