"""Generate market context features from market indices.

Task 1.3: Calculate market regime and correlation features for machine learning.

Features generated:
- VIX levels and percentiles (market fear gauge)
- SPY/QQQ trend and momentum (market direction)
- Sector ETF performance
- Stock correlation with market indices
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
except ImportError:
    print("Error: yfinance not installed. Run: pip install yfinance")
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
    WHERE earnings_date >= '2020-01-01'
    ORDER BY ticker, earnings_date
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    df['earnings_date'] = pd.to_datetime(df['earnings_date'])

    return df


def fetch_market_data(ticker: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """Fetch market index data from yfinance."""
    try:
        data = yf.Ticker(ticker)
        df = data.history(start=start_date, end=end_date, interval='1d')

        if df.empty:
            return pd.DataFrame()

        df = df.rename(columns={'Close': 'close', 'Volume': 'volume'})
        return df[['close', 'volume']]

    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return pd.DataFrame()


def calculate_vix_features(earnings_date: datetime) -> dict:
    """Calculate VIX-based market fear features.

    Args:
        earnings_date: Date of earnings announcement

    Returns:
        Dictionary of VIX features
    """
    features = {}

    # Fetch VIX data for 60 days before earnings
    start_date = earnings_date - timedelta(days=90)
    end_date = earnings_date - timedelta(days=1)

    vix_df = fetch_market_data('^VIX', start_date, end_date)

    if vix_df.empty or len(vix_df) < 20:
        features['vix_current'] = np.nan
        features['vix_20d_avg'] = np.nan
        features['vix_percentile_60d'] = np.nan
        features['vix_regime'] = np.nan
        return features

    # Current VIX level (day before earnings)
    current_vix = vix_df['close'].iloc[-1]
    features['vix_current'] = current_vix

    # 20-day average
    features['vix_20d_avg'] = vix_df['close'].tail(20).mean()

    # Percentile rank over 60 days
    if len(vix_df) >= 60:
        vix_60d = vix_df['close'].tail(60)
        percentile = (vix_60d <= current_vix).sum() / len(vix_60d) * 100
        features['vix_percentile_60d'] = percentile
    else:
        percentile = (vix_df['close'] <= current_vix).sum() / len(vix_df) * 100
        features['vix_percentile_60d'] = percentile

    # VIX regime classification
    if current_vix < 15:
        regime = 'low'  # Complacent market
    elif current_vix < 25:
        regime = 'normal'  # Typical volatility
    else:
        regime = 'high'  # Fear/stress in market

    features['vix_regime'] = regime

    return features


def calculate_market_trend(earnings_date: datetime, ticker: str) -> dict:
    """Calculate market trend and momentum features.

    Args:
        earnings_date: Date of earnings announcement
        ticker: Market index ticker (SPY or QQQ)

    Returns:
        Dictionary of trend features
    """
    features = {}
    prefix = ticker.lower()

    # Fetch market data for 60 days before earnings
    start_date = earnings_date - timedelta(days=90)
    end_date = earnings_date - timedelta(days=1)

    df = fetch_market_data(ticker, start_date, end_date)

    if df.empty or len(df) < 20:
        features[f'{prefix}_trend_20d'] = np.nan
        features[f'{prefix}_rsi_14d'] = np.nan
        features[f'{prefix}_vs_20ma'] = np.nan
        return features

    # 20-day trend (% change)
    if len(df) >= 20:
        trend_20d = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
        features[f'{prefix}_trend_20d'] = trend_20d
    else:
        features[f'{prefix}_trend_20d'] = np.nan

    # RSI (14-day)
    if len(df) >= 15:
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        features[f'{prefix}_rsi_14d'] = rsi.iloc[-1]
    else:
        features[f'{prefix}_rsi_14d'] = np.nan

    # Price vs 20-day moving average
    if len(df) >= 20:
        ma_20 = df['close'].tail(20).mean()
        vs_ma = (df['close'].iloc[-1] / ma_20 - 1) * 100
        features[f'{prefix}_vs_20ma'] = vs_ma
    else:
        features[f'{prefix}_vs_20ma'] = np.nan

    return features


def calculate_stock_correlation(ticker: str, earnings_date: datetime, market_ticker: str, window: int = 60) -> float:
    """Calculate correlation between stock and market index.

    Args:
        ticker: Stock ticker
        earnings_date: Date of earnings
        market_ticker: Market index ticker (SPY/QQQ)
        window: Lookback window in days

    Returns:
        Correlation coefficient, or NaN if insufficient data
    """
    start_date = earnings_date - timedelta(days=window + 30)
    end_date = earnings_date - timedelta(days=1)

    # Fetch stock data
    stock_df = fetch_market_data(ticker, start_date, end_date)

    # Fetch market data
    market_df = fetch_market_data(market_ticker, start_date, end_date)

    if stock_df.empty or market_df.empty:
        return np.nan

    # Align dates
    combined = pd.merge(stock_df['close'], market_df['close'],
                       left_index=True, right_index=True,
                       suffixes=('_stock', '_market'))

    if len(combined) < 20:
        return np.nan

    # Calculate returns
    stock_returns = combined['close_stock'].pct_change().dropna()
    market_returns = combined['close_market'].pct_change().dropna()

    if len(stock_returns) < 20:
        return np.nan

    # Calculate correlation
    correlation = stock_returns.corr(market_returns)

    return correlation


def generate_features_for_event(ticker: str, earnings_date: datetime) -> dict:
    """Generate market context features for a single earnings event.

    Args:
        ticker: Stock ticker
        earnings_date: Date of earnings

    Returns:
        Dictionary of features
    """
    features = {
        'ticker': ticker,
        'earnings_date': earnings_date
    }

    # VIX features
    vix_features = calculate_vix_features(earnings_date)
    features.update(vix_features)

    # SPY trend features
    spy_features = calculate_market_trend(earnings_date, 'SPY')
    features.update(spy_features)

    # QQQ trend features
    qqq_features = calculate_market_trend(earnings_date, 'QQQ')
    features.update(qqq_features)

    # Stock correlations
    features['spy_corr_60d'] = calculate_stock_correlation(ticker, earnings_date, 'SPY', 60)
    features['qqq_corr_60d'] = calculate_stock_correlation(ticker, earnings_date, 'QQQ', 60)

    # Market regime (combined SPY + VIX)
    if not pd.isna(features.get('spy_trend_20d')) and not pd.isna(features.get('vix_regime')):
        spy_trend = features['spy_trend_20d']
        vix_regime = features['vix_regime']

        if spy_trend > 5 and vix_regime == 'low':
            market_regime = 'strong_bull'
        elif spy_trend > 0 and vix_regime != 'high':
            market_regime = 'bull'
        elif spy_trend < -5 and vix_regime == 'high':
            market_regime = 'strong_bear'
        elif spy_trend < 0 and vix_regime != 'low':
            market_regime = 'bear'
        else:
            market_regime = 'neutral'
    else:
        market_regime = np.nan

    features['market_regime'] = market_regime

    return features


def main():
    """Main execution function."""
    print("=" * 60)
    print("Task 1.3: Generating Market Context Features")
    print("=" * 60)

    # Load configuration
    config = load_config()

    print(f"\nConfiguration:")
    print(f"  Market indices: VIX, SPY, QQQ")
    print(f"  Indicators: Trend, RSI, correlations, regime classification")

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
    print(f"\n[2/3] Calculating market context features...")
    print(f"  Note: This fetches market data and may take several minutes...")

    features_list = []

    for _, row in tqdm(events_df.iterrows(), total=len(events_df), desc="Processing"):
        features = generate_features_for_event(
            row['ticker'],
            row['earnings_date']
        )
        features_list.append(features)

    features_df = pd.DataFrame(features_list)

    # Save to parquet
    output_dir = Path(__file__).parent.parent / "data" / "features"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "market_features.parquet"

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
        if col not in ['ticker', 'earnings_date', 'vix_regime', 'market_regime']:
            non_nan_pct = (1 - features_df[col].isna().sum() / len(features_df)) * 100
            if non_nan_pct > 0:
                print(f"  {col}: {non_nan_pct:.1f}%")

    # Market regime distribution
    print("\nMarket Regime Distribution:")
    if 'market_regime' in features_df.columns:
        regime_counts = features_df['market_regime'].value_counts()
        for regime, count in regime_counts.items():
            pct = count / len(features_df) * 100
            print(f"  {regime}: {count} ({pct:.1f}%)")

    # Sample of features
    print("\nSample features (most recent):")
    sample = features_df.tail(3)
    print(sample[['ticker', 'earnings_date', 'vix_current', 'spy_trend_20d', 'spy_corr_60d', 'market_regime']].to_string(index=False))

    print("\n✅ Task 1.3 complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
