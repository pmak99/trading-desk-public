"""Generate volatility features from price data.

Task 1.2: Calculate technical volatility indicators for machine learning feature engineering.

Features generated:
- ATR (Average True Range) for multiple windows [10, 20, 50 days]
- Bollinger Band width (normalized)
- Historical Volatility (HV) for multiple windows
- Volatility percentile ranks
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import json
from tqdm import tqdm
import warnings

warnings.filterwarnings('ignore')

try:
    import yfinance as yf
    from ta.volatility import BollingerBands, AverageTrueRange
except ImportError as e:
    print(f"Error: Required package not installed: {e}")
    print("Run: pip install yfinance ta")
    exit(1)


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
        ticker,
        earnings_date
    FROM historical_moves
    WHERE earnings_date >= '2020-01-01'  -- Focus on recent data
    ORDER BY ticker, earnings_date
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    df['earnings_date'] = pd.to_datetime(df['earnings_date'])

    return df


def fetch_price_data(ticker: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """Fetch daily price data from yfinance.

    Args:
        ticker: Stock ticker symbol
        start_date: Start date for data
        end_date: End date for data

    Returns:
        DataFrame with OHLCV data
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(start=start_date, end=end_date, interval='1d')

        if df.empty:
            return pd.DataFrame()

        # Standardize column names
        df = df.rename(columns={
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })

        return df[['open', 'high', 'low', 'close', 'volume']]

    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        return pd.DataFrame()


def calculate_atr(df: pd.DataFrame, window: int) -> float:
    """Calculate Average True Range for a given window.

    Args:
        df: Price dataframe with high, low, close
        window: Window size in days

    Returns:
        Latest ATR value, or NaN if insufficient data
    """
    if len(df) < window + 1:
        return np.nan

    try:
        atr = AverageTrueRange(
            high=df['high'],
            low=df['low'],
            close=df['close'],
            window=window
        )

        atr_values = atr.average_true_range()

        # Return latest non-NaN value
        valid_values = atr_values.dropna()
        if len(valid_values) > 0:
            return valid_values.iloc[-1]
        return np.nan

    except Exception as e:
        return np.nan


def calculate_bb_width(df: pd.DataFrame, window: int, std: float = 2.0) -> float:
    """Calculate Bollinger Band width (normalized by price).

    Args:
        df: Price dataframe with close
        window: Window size in days
        std: Number of standard deviations for bands

    Returns:
        BB width as percentage of price, or NaN if insufficient data
    """
    if len(df) < window:
        return np.nan

    try:
        bb = BollingerBands(
            close=df['close'],
            window=window,
            window_dev=std
        )

        upper = bb.bollinger_hband()
        lower = bb.bollinger_lband()
        middle = bb.bollinger_mavg()

        # Width = (upper - lower) / middle * 100 (as percentage)
        width = (upper - lower) / middle * 100

        valid_values = width.dropna()
        if len(valid_values) > 0:
            return valid_values.iloc[-1]
        return np.nan

    except Exception as e:
        return np.nan


def calculate_hv(df: pd.DataFrame, window: int) -> float:
    """Calculate Historical Volatility (annualized).

    Args:
        df: Price dataframe with close
        window: Window size in days

    Returns:
        Annualized HV as percentage, or NaN if insufficient data
    """
    if len(df) < window + 1:
        return np.nan

    try:
        # Calculate daily returns
        returns = df['close'].pct_change().dropna()

        if len(returns) < window:
            return np.nan

        # Get last 'window' returns
        recent_returns = returns.tail(window)

        # Annualized volatility (252 trading days per year)
        vol = recent_returns.std() * np.sqrt(252) * 100

        return vol

    except Exception as e:
        return np.nan


def calculate_volatility_percentile(current_vol: float, historical_vols: list, window: int = 252) -> float:
    """Calculate percentile rank of current volatility vs historical range.

    Args:
        current_vol: Current volatility value
        historical_vols: List of historical volatility values
        window: Window for percentile calculation

    Returns:
        Percentile rank (0-100), or NaN if insufficient data
    """
    if np.isnan(current_vol) or len(historical_vols) < 20:
        return np.nan

    try:
        # Get recent window of historical values
        recent_vols = [v for v in historical_vols[-window:] if not np.isnan(v)]

        if len(recent_vols) < 10:
            return np.nan

        # Calculate percentile
        percentile = sum(v <= current_vol for v in recent_vols) / len(recent_vols) * 100

        return percentile

    except Exception:
        return np.nan


def generate_features_for_event(ticker: str, earnings_date: datetime, windows: list) -> dict:
    """Generate volatility features for a single earnings event.

    Args:
        ticker: Stock ticker
        earnings_date: Date of earnings
        windows: List of window sizes for indicators

    Returns:
        Dictionary of features
    """
    features = {
        'ticker': ticker,
        'earnings_date': earnings_date
    }

    # Fetch price data: need enough history for largest window + some buffer
    lookback_days = max(windows) + 100  # Extra buffer for HV percentile
    start_date = earnings_date - timedelta(days=lookback_days)
    # Get data up to but not including earnings date (avoid lookahead bias)
    end_date = earnings_date - timedelta(days=1)

    df = fetch_price_data(ticker, start_date, end_date)

    if df.empty or len(df) < min(windows):
        # Insufficient data - return NaN features
        for window in windows:
            features[f'atr_{window}d'] = np.nan
            features[f'atr_{window}d_pct'] = np.nan
            features[f'bb_width_{window}d'] = np.nan
            features[f'hv_{window}d'] = np.nan

        features['hv_10d_percentile'] = np.nan
        features['hv_20d_percentile'] = np.nan
        features['vol_regime'] = np.nan

        return features

    # Calculate indicators for each window
    for window in windows:
        # ATR
        atr = calculate_atr(df, window)
        features[f'atr_{window}d'] = atr

        # ATR as percentage of price (normalized)
        if not np.isnan(atr) and len(df) > 0:
            current_price = df['close'].iloc[-1]
            features[f'atr_{window}d_pct'] = (atr / current_price) * 100
        else:
            features[f'atr_{window}d_pct'] = np.nan

        # Bollinger Band width
        features[f'bb_width_{window}d'] = calculate_bb_width(df, window)

        # Historical Volatility
        features[f'hv_{window}d'] = calculate_hv(df, window)

    # Volatility percentile ranks (how elevated is current vol?)
    if 'hv_10d' in features and not np.isnan(features['hv_10d']):
        # Calculate rolling HV over full history for percentile
        all_hv_10 = []
        for i in range(10, len(df)):
            window_df = df.iloc[:i+1]
            hv = calculate_hv(window_df, 10)
            if not np.isnan(hv):
                all_hv_10.append(hv)

        features['hv_10d_percentile'] = calculate_volatility_percentile(
            features['hv_10d'], all_hv_10
        )
    else:
        features['hv_10d_percentile'] = np.nan

    if 'hv_20d' in features and not np.isnan(features['hv_20d']):
        all_hv_20 = []
        for i in range(20, len(df)):
            window_df = df.iloc[:i+1]
            hv = calculate_hv(window_df, 20)
            if not np.isnan(hv):
                all_hv_20.append(hv)

        features['hv_20d_percentile'] = calculate_volatility_percentile(
            features['hv_20d'], all_hv_20
        )
    else:
        features['hv_20d_percentile'] = np.nan

    # Volatility regime classification
    if not np.isnan(features.get('hv_20d_percentile', np.nan)):
        pct = features['hv_20d_percentile']
        if pct >= 75:
            features['vol_regime'] = 'high'
        elif pct >= 25:
            features['vol_regime'] = 'normal'
        else:
            features['vol_regime'] = 'low'
    else:
        features['vol_regime'] = np.nan

    return features


def main():
    """Main execution function."""
    print("=" * 60)
    print("Task 1.2: Generating Volatility Features")
    print("=" * 60)

    # Load configuration
    config = load_config()
    vol_config = config['volatility_features']

    if not vol_config['enabled']:
        print("Volatility features disabled in config. Skipping.")
        return

    windows = vol_config['windows']

    print(f"\nConfiguration:")
    print(f"  Windows: {windows} days")
    print(f"  Indicators: ATR, Bollinger Bands, Historical Volatility")

    # Database path
    db_path = Path(__file__).parent.parent.parent / "data" / "ivcrush.db"
    print(f"\nDatabase: {db_path}")

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        return

    # Extract earnings events
    print("\n[1/3] Extracting earnings events...")
    events_df = extract_earnings_events(str(db_path))
    print(f"  Found {len(events_df):,} earnings events")
    print(f"  Date range: {events_df['earnings_date'].min()} to {events_df['earnings_date'].max()}")
    print(f"  Unique tickers: {events_df['ticker'].nunique()}")

    # Generate features for each event
    print(f"\n[2/3] Calculating volatility features...")
    print(f"  Note: This fetches price data from yfinance and may take several minutes...")

    features_list = []

    for _, row in tqdm(events_df.iterrows(), total=len(events_df), desc="Processing"):
        features = generate_features_for_event(
            row['ticker'],
            row['earnings_date'],
            windows
        )
        features_list.append(features)

    features_df = pd.DataFrame(features_list)

    # Save to parquet
    output_dir = Path(__file__).parent.parent / "data" / "features"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "volatility_features.parquet"

    print(f"\n[3/3] Saving features to {output_path.name}...")
    features_df.to_parquet(output_path, index=False)

    # Summary statistics
    print("\n" + "=" * 60)
    print("Feature Generation Summary")
    print("=" * 60)
    print(f"\nOutput: {output_path}")
    print(f"Rows: {len(features_df):,}")
    print(f"Columns: {len(features_df.columns)}")
    print(f"File size: {output_path.stat().st_size / 1024:.1f} KB")

    # Data quality statistics
    print("\nData Quality (non-NaN coverage):")
    for col in features_df.columns:
        if col not in ['ticker', 'earnings_date', 'vol_regime']:
            non_nan_pct = (1 - features_df[col].isna().sum() / len(features_df)) * 100
            if non_nan_pct > 0:
                print(f"  {col}: {non_nan_pct:.1f}%")

    # Sample of features
    print("\nSample features (most recent):")
    sample = features_df.tail(3)
    print(sample[['ticker', 'earnings_date', 'atr_20d_pct', 'hv_20d', 'bb_width_20d', 'vol_regime']].to_string(index=False))

    print("\n✅ Task 1.2 complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
