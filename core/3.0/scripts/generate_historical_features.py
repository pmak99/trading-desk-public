"""Generate historical move features from ivcrush.db.

Task 1.1: Extract historical earnings moves and calculate rolling statistics
for machine learning feature engineering.

Features generated:
- Rolling statistics (mean, std, min, max, median) for lookback periods [1, 2, 4, 8] quarters
- Trend analysis (move magnitude increasing/decreasing)
- Sample size and data quality indicators
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


def extract_historical_moves(db_path: str) -> pd.DataFrame:
    """Extract all historical moves from database."""
    conn = sqlite3.connect(db_path)

    query = """
    SELECT
        ticker,
        earnings_date,
        prev_close,
        earnings_close,
        intraday_move_pct,
        gap_move_pct,
        close_move_pct,
        volume_before,
        volume_earnings
    FROM historical_moves
    ORDER BY ticker, earnings_date
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    # Convert date to datetime
    df['earnings_date'] = pd.to_datetime(df['earnings_date'])

    # Calculate absolute move (for volatility features)
    df['abs_close_move_pct'] = df['close_move_pct'].abs()

    return df


def calculate_rolling_stats(df: pd.DataFrame, lookback_quarters: list, statistics: list) -> pd.DataFrame:
    """Calculate rolling statistics for each ticker over specified lookback periods.

    Args:
        df: DataFrame with historical moves
        lookback_quarters: List of quarters to look back (e.g., [1, 2, 4, 8])
        statistics: List of statistics to calculate (e.g., ['mean', 'std', 'min', 'max', 'median'])

    Returns:
        DataFrame with rolling statistics as features
    """
    features = []

    for ticker in df['ticker'].unique():
        ticker_df = df[df['ticker'] == ticker].sort_values('earnings_date').reset_index(drop=True)

        # For each earnings event, calculate features based on PRIOR history only
        for idx in range(len(ticker_df)):
            row = ticker_df.iloc[idx]

            feature_dict = {
                'ticker': ticker,
                'earnings_date': row['earnings_date'],
                'current_close_move_pct': row['close_move_pct'],
                'current_abs_move_pct': row['abs_close_move_pct']
            }

            # Calculate rolling statistics for each lookback period
            for quarters in lookback_quarters:
                # Get prior N quarters (exclude current row)
                if idx == 0:
                    # First event - no history
                    prior_moves = pd.Series(dtype=float)
                else:
                    start_idx = max(0, idx - quarters)
                    prior_moves = ticker_df.iloc[start_idx:idx]['abs_close_move_pct']

                # Calculate statistics
                for stat in statistics:
                    if len(prior_moves) > 0:
                        if stat == 'mean':
                            value = prior_moves.mean()
                        elif stat == 'std':
                            value = prior_moves.std() if len(prior_moves) > 1 else 0.0
                        elif stat == 'min':
                            value = prior_moves.min()
                        elif stat == 'max':
                            value = prior_moves.max()
                        elif stat == 'median':
                            value = prior_moves.median()
                        else:
                            value = np.nan
                    else:
                        value = np.nan

                    feature_dict[f'hist_{quarters}q_{stat}'] = value

                # Add sample size
                feature_dict[f'hist_{quarters}q_count'] = len(prior_moves)

            # Calculate trend (is magnitude increasing or decreasing?)
            if idx >= 4:  # Need at least 4 prior quarters for trend
                recent_4q = ticker_df.iloc[idx-4:idx]['abs_close_move_pct'].mean()
                if idx >= 8:
                    older_4q = ticker_df.iloc[idx-8:idx-4]['abs_close_move_pct'].mean()
                    feature_dict['trend_8q_vs_4q'] = (recent_4q - older_4q) / older_4q if older_4q > 0 else np.nan
                else:
                    feature_dict['trend_8q_vs_4q'] = np.nan
            else:
                feature_dict['trend_8q_vs_4q'] = np.nan

            # Volatility of volatility (how consistent are moves?)
            if idx >= 8:
                recent_std = ticker_df.iloc[idx-4:idx]['abs_close_move_pct'].std()
                older_std = ticker_df.iloc[idx-8:idx-4]['abs_close_move_pct'].std()
                feature_dict['vol_of_vol'] = (recent_std - older_std) / older_std if older_std > 0 else np.nan
            else:
                feature_dict['vol_of_vol'] = np.nan

            features.append(feature_dict)

    return pd.DataFrame(features)


def add_data_quality_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicators for data quality and feature reliability."""
    # Minimum data threshold for reliable statistics
    df['has_1q_data'] = df['hist_1q_count'] >= 1
    df['has_2q_data'] = df['hist_2q_count'] >= 2
    df['has_4q_data'] = df['hist_4q_count'] >= 4
    df['has_8q_data'] = df['hist_8q_count'] >= 8

    # Overall data quality score (0-1)
    df['data_quality_score'] = (
        df['has_1q_data'].astype(int) * 0.1 +
        df['has_2q_data'].astype(int) * 0.2 +
        df['has_4q_data'].astype(int) * 0.3 +
        df['has_8q_data'].astype(int) * 0.4
    )

    return df


def main():
    """Main execution function."""
    print("=" * 60)
    print("Task 1.1: Generating Historical Move Features")
    print("=" * 60)

    # Load configuration
    config = load_config()
    hist_config = config['historical_features']

    if not hist_config['enabled']:
        print("Historical features disabled in config. Skipping.")
        return

    lookback_quarters = hist_config['lookback_quarters']
    statistics = hist_config['statistics']

    print(f"\nConfiguration:")
    print(f"  Lookback quarters: {lookback_quarters}")
    print(f"  Statistics: {statistics}")
    print(f"  Trend analysis: {hist_config['trend_analysis']}")

    # Database path (relative to 3.0 directory)
    db_path = Path(__file__).parent.parent.parent / "data" / "ivcrush.db"
    print(f"\nDatabase: {db_path}")

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        return

    # Extract historical moves
    print("\n[1/4] Extracting historical moves from database...")
    df = extract_historical_moves(str(db_path))
    print(f"  Loaded {len(df):,} historical earnings moves")
    print(f"  Date range: {df['earnings_date'].min()} to {df['earnings_date'].max()}")
    print(f"  Unique tickers: {df['ticker'].nunique()}")

    # Calculate rolling statistics
    print("\n[2/4] Calculating rolling statistics...")
    features_df = calculate_rolling_stats(df, lookback_quarters, statistics)
    print(f"  Generated {len(features_df):,} feature rows")
    print(f"  Feature columns: {len(features_df.columns)}")

    # Add data quality indicators
    print("\n[3/4] Adding data quality indicators...")
    features_df = add_data_quality_indicators(features_df)

    # Save to parquet
    output_dir = Path(__file__).parent.parent / "data" / "features"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "historical_features.parquet"

    print(f"\n[4/4] Saving features to {output_path.name}...")
    features_df.to_parquet(output_path, index=False)

    # Summary statistics
    print("\n" + "=" * 60)
    print("Feature Generation Summary")
    print("=" * 60)
    print(f"\nOutput: {output_path}")
    print(f"Rows: {len(features_df):,}")
    print(f"Columns: {len(features_df.columns)}")
    print(f"File size: {output_path.stat().st_size / 1024:.1f} KB")

    print("\nData Quality Distribution:")
    print(f"  Has 1Q+ data: {features_df['has_1q_data'].sum():,} ({features_df['has_1q_data'].mean()*100:.1f}%)")
    print(f"  Has 2Q+ data: {features_df['has_2q_data'].sum():,} ({features_df['has_2q_data'].mean()*100:.1f}%)")
    print(f"  Has 4Q+ data: {features_df['has_4q_data'].sum():,} ({features_df['has_4q_data'].mean()*100:.1f}%)")
    print(f"  Has 8Q+ data: {features_df['has_8q_data'].sum():,} ({features_df['has_8q_data'].mean()*100:.1f}%)")

    print(f"\nAverage data quality score: {features_df['data_quality_score'].mean():.3f}")

    # Sample of features
    print("\nSample features (ticker with most data):")
    sample_ticker = df.groupby('ticker').size().idxmax()
    sample = features_df[features_df['ticker'] == sample_ticker].tail(3)
    print(f"\nTicker: {sample_ticker}")
    print(sample[['earnings_date', 'hist_4q_mean', 'hist_4q_std', 'hist_8q_mean', 'data_quality_score']].to_string(index=False))

    print("\n✅ Task 1.1 complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
