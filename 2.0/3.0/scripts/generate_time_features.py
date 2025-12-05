"""Generate time-based features from earnings calendar.

Task 1.4: Calculate temporal patterns for machine learning feature engineering.

Features generated:
- Days since last earnings
- Days until next earnings
- Earnings frequency/regularity
- Seasonality patterns (month, quarter, day of week)
- Year and trend effects
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json


def load_config():
    """Load feature configuration."""
    config_path = Path(__file__).parent.parent / "config" / "feature_config.json"
    with open(config_path) as f:
        return json.load(f)


def extract_earnings_events(db_path: str) -> pd.DataFrame:
    """Extract earnings events from database."""
    conn = sqlite3.connect(db_path)

    query = """
    SELECT DISTINCT
        h.ticker,
        h.earnings_date,
        COALESCE(e.timing, 'UNKNOWN') as timing
    FROM historical_moves h
    LEFT JOIN earnings_calendar e
        ON h.ticker = e.ticker AND h.earnings_date = e.earnings_date
    ORDER BY h.ticker, h.earnings_date
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    df['earnings_date'] = pd.to_datetime(df['earnings_date'])

    return df


def calculate_earnings_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate days since last earnings and days until next earnings.

    Args:
        df: DataFrame with ticker and earnings_date

    Returns:
        DataFrame with gap features added
    """
    # Sort by ticker and date
    df = df.sort_values(['ticker', 'earnings_date']).reset_index(drop=True)

    # Calculate days since last earnings
    df['days_since_last_earnings'] = df.groupby('ticker')['earnings_date'].diff().dt.days

    # Calculate days until next earnings
    df['days_until_next_earnings'] = -df.groupby('ticker')['earnings_date'].diff(-1).dt.days

    # Calculate earnings frequency (average gap for this ticker)
    avg_gap = df.groupby('ticker')['days_since_last_earnings'].transform('mean')
    df['avg_earnings_gap'] = avg_gap

    # Earnings regularity (std of gaps / mean gap - lower is more regular)
    std_gap = df.groupby('ticker')['days_since_last_earnings'].transform('std')
    df['earnings_regularity'] = std_gap / avg_gap
    df['earnings_regularity'] = df['earnings_regularity'].fillna(0)

    # Classify earnings frequency
    def classify_frequency(avg_gap):
        if pd.isna(avg_gap):
            return 'unknown'
        elif avg_gap < 80:
            return 'monthly'
        elif avg_gap < 120:
            return 'quarterly'
        elif avg_gap < 200:
            return 'semi_annual'
        else:
            return 'annual'

    df['earnings_frequency'] = df['avg_earnings_gap'].apply(classify_frequency)

    return df


def add_seasonality_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add seasonality and calendar features.

    Args:
        df: DataFrame with earnings_date

    Returns:
        DataFrame with seasonality features added
    """
    # Extract date components
    df['earnings_month'] = df['earnings_date'].dt.month
    df['earnings_quarter'] = df['earnings_date'].dt.quarter
    df['earnings_day_of_week'] = df['earnings_date'].dt.dayofweek  # 0=Monday, 6=Sunday
    df['earnings_year'] = df['earnings_date'].dt.year

    # Cyclic encoding for month (to capture cyclical nature)
    df['month_sin'] = np.sin(2 * np.pi * df['earnings_month'] / 12)
    df['month_cos'] = np.cos(2 * np.pi * df['earnings_month'] / 12)

    # Quarter dummies (Q1 typically has different dynamics)
    df['is_q1'] = (df['earnings_quarter'] == 1).astype(int)
    df['is_q4'] = (df['earnings_quarter'] == 4).astype(int)

    # Day of week effects (earnings on Friday vs Monday can differ)
    df['is_monday'] = (df['earnings_day_of_week'] == 0).astype(int)
    df['is_friday'] = (df['earnings_day_of_week'] == 4).astype(int)

    # Year trend (to capture market evolution over time)
    # Normalize year to start from 0
    min_year = df['earnings_year'].min()
    df['years_since_start'] = df['earnings_year'] - min_year

    return df


def add_timing_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add earnings timing features (BMO, AMC, etc).

    Args:
        df: DataFrame with timing column

    Returns:
        DataFrame with timing features added
    """
    # Convert timing to categorical features
    df['is_bmo'] = (df['timing'] == 'BMO').astype(int)
    df['is_amc'] = (df['timing'] == 'AMC').astype(int)

    return df


def calculate_ticker_history_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate features related to ticker's historical pattern.

    Args:
        df: DataFrame with ticker and earnings_date

    Returns:
        DataFrame with history features added
    """
    # Count of historical earnings for this ticker
    df['historical_earnings_count'] = df.groupby('ticker').cumcount()

    # Years of data available for this ticker (as of this earnings date)
    first_date = df.groupby('ticker')['earnings_date'].transform('first')
    df['years_of_history'] = (df['earnings_date'] - first_date).dt.days / 365.25

    return df


def main():
    """Main execution function."""
    print("=" * 60)
    print("Task 1.4: Generating Time-Based Features")
    print("=" * 60)

    # Database path
    db_path = Path(__file__).parent.parent.parent / "data" / "ivcrush.db"
    print(f"\nDatabase: {db_path}")

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        return

    # Extract earnings events
    print("\n[1/6] Extracting earnings events...")
    df = extract_earnings_events(str(db_path))
    print(f"  Loaded {len(df):,} earnings events")
    print(f"  Date range: {df['earnings_date'].min()} to {df['earnings_date'].max()}")
    print(f"  Unique tickers: {df['ticker'].nunique()}")

    # Calculate earnings gaps
    print("\n[2/6] Calculating earnings gaps...")
    df = calculate_earnings_gaps(df)
    print(f"  Average gap between earnings: {df['days_since_last_earnings'].mean():.1f} days")

    # Add seasonality features
    print("\n[3/6] Adding seasonality features...")
    df = add_seasonality_features(df)

    # Add timing features
    print("\n[4/6] Adding earnings timing features...")
    df = add_timing_features(df)

    # Add ticker history features
    print("\n[5/6] Adding ticker history features...")
    df = calculate_ticker_history_features(df)

    # Select final columns for output
    feature_columns = [
        'ticker', 'earnings_date',
        'days_since_last_earnings', 'days_until_next_earnings',
        'avg_earnings_gap', 'earnings_regularity', 'earnings_frequency',
        'earnings_month', 'earnings_quarter', 'earnings_year',
        'month_sin', 'month_cos',
        'is_q1', 'is_q4',
        'earnings_day_of_week', 'is_monday', 'is_friday',
        'is_bmo', 'is_amc',
        'years_since_start', 'historical_earnings_count', 'years_of_history'
    ]

    features_df = df[feature_columns].copy()

    # Save to parquet
    output_dir = Path(__file__).parent.parent / "data" / "features"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "time_features.parquet"

    print(f"\n[6/6] Saving features to {output_path.name}...")
    features_df.to_parquet(output_path, index=False)

    # Summary statistics
    print("\n" + "=" * 60)
    print("Feature Generation Summary")
    print("=" * 60)
    print(f"\nOutput: {output_path}")
    print(f"Rows: {len(features_df):,}")
    print(f"Columns: {len(features_df.columns)}")
    print(f"File size: {output_path.stat().st_size / 1024:.1f} KB")

    # Data quality
    print("\nData Quality (non-NaN coverage):")
    for col in features_df.columns:
        if col not in ['ticker', 'earnings_date', 'earnings_frequency']:
            non_nan_pct = (1 - features_df[col].isna().sum() / len(features_df)) * 100
            if non_nan_pct < 100 and non_nan_pct > 0:
                print(f"  {col}: {non_nan_pct:.1f}%")

    # Frequency distribution
    print("\nEarnings Frequency Distribution:")
    freq_counts = features_df['earnings_frequency'].value_counts()
    for freq, count in freq_counts.items():
        pct = count / len(features_df) * 100
        print(f"  {freq}: {count} ({pct:.1f}%)")

    # Timing distribution
    print("\nEarnings Timing Distribution:")
    bmo_count = features_df['is_bmo'].sum()
    amc_count = features_df['is_amc'].sum()
    print(f"  BMO (Before Market Open): {bmo_count} ({bmo_count/len(features_df)*100:.1f}%)")
    print(f"  AMC (After Market Close): {amc_count} ({amc_count/len(features_df)*100:.1f}%)")

    # Sample of features
    print("\nSample features (most recent):")
    sample = features_df.tail(3)
    print(sample[['ticker', 'earnings_date', 'days_since_last_earnings', 'earnings_quarter', 'earnings_frequency']].to_string(index=False))

    print("\n✅ Task 1.4 complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
