"""
Technical Analysis - Support/resistance, trends, and volume analysis.

Provides context for earnings trades:
- Key support/resistance levels
- Recent price trends
- Volume patterns
- RSI and momentum indicators

Uses free pandas and yfinance (no paid APIs required).
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)


class TechnicalAnalyzer:
    """Technical analysis for earnings trade context."""

    def analyze_ticker(self, ticker: str, lookback_days: int = 180) -> Dict:
        """
        Perform technical analysis on a ticker.

        Args:
            ticker: Stock ticker symbol
            lookback_days: Days of history to analyze (default: 180)

        Returns:
            Dict with:
            - support_levels: List of support prices
            - resistance_levels: List of resistance prices
            - current_trend: "bullish", "bearish", "neutral"
            - rsi: Relative Strength Index (0-100)
            - volume_trend: "increasing", "decreasing", "stable"
            - price_vs_support: Distance to nearest support (%)
            - price_vs_resistance: Distance to nearest resistance (%)
            - volatility_20d: 20-day historical volatility (%)
        """
        logger.info(f"{ticker}: Performing technical analysis...")

        try:
            # Get historical data
            stock = yf.Ticker(ticker)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days)

            hist = stock.history(start=start_date, end=end_date)

            if hist.empty:
                logger.warning(f"{ticker}: No historical data found")
                return self._empty_result(ticker)

            current_price = float(hist['Close'].iloc[-1])

            # Calculate support and resistance
            support_levels, resistance_levels = self._calculate_support_resistance(hist)

            # Calculate trend
            trend = self._calculate_trend(hist)

            # Calculate RSI
            rsi = self._calculate_rsi(hist)

            # Calculate volume trend
            volume_trend = self._calculate_volume_trend(hist)

            # Calculate volatility
            volatility_20d = self._calculate_volatility(hist, window=20)

            # Distance to nearest levels
            price_vs_support = self._calculate_distance_to_level(
                current_price, support_levels, direction="below"
            )
            price_vs_resistance = self._calculate_distance_to_level(
                current_price, resistance_levels, direction="above"
            )

            result = {
                'ticker': ticker,
                'current_price': round(current_price, 2),
                'support_levels': [round(s, 2) for s in support_levels[:3]],
                'resistance_levels': [round(r, 2) for r in resistance_levels[:3]],
                'current_trend': trend,
                'rsi': round(rsi, 1) if rsi else None,
                'volume_trend': volume_trend,
                'price_vs_support': round(price_vs_support, 2) if price_vs_support else None,
                'price_vs_resistance': round(price_vs_resistance, 2) if price_vs_resistance else None,
                'volatility_20d': round(volatility_20d, 2) if volatility_20d else None
            }

            logger.info(f"{ticker}: Technical analysis complete - {trend} trend, "
                       f"RSI={result['rsi']}, Vol={result['volatility_20d']}%")

            return result

        except Exception as e:
            logger.error(f"{ticker}: Technical analysis failed: {e}")
            return self._empty_result(ticker)

    def _calculate_support_resistance(self, hist: pd.DataFrame) -> tuple:
        """
        Calculate support and resistance levels using local minima/maxima.

        Args:
            hist: Historical price DataFrame

        Returns:
            Tuple of (support_levels, resistance_levels)
        """
        try:
            # Find local minima (support) and maxima (resistance)
            window = 20

            # Calculate local minima
            local_min = hist['Low'][(hist['Low'].shift(1) > hist['Low']) &
                                    (hist['Low'].shift(-1) > hist['Low'])]

            # Calculate local maxima
            local_max = hist['High'][(hist['High'].shift(1) < hist['High']) &
                                     (hist['High'].shift(-1) < hist['High'])]

            # Cluster nearby levels (within 2%)
            support_levels = self._cluster_levels(local_min.tolist())
            resistance_levels = self._cluster_levels(local_max.tolist())

            # Sort by proximity to current price
            current_price = float(hist['Close'].iloc[-1])

            # Support below current price
            support_levels = sorted([s for s in support_levels if s < current_price],
                                   reverse=True)

            # Resistance above current price
            resistance_levels = sorted([r for r in resistance_levels if r > current_price])

            return support_levels[:5], resistance_levels[:5]

        except Exception as e:
            logger.debug(f"Failed to calculate support/resistance: {e}")
            return [], []

    def _cluster_levels(self, levels: List[float], threshold_pct: float = 2.0) -> List[float]:
        """
        Cluster nearby price levels into single representative levels.

        Args:
            levels: List of price levels
            threshold_pct: Cluster within this % (default: 2%)

        Returns:
            List of clustered levels
        """
        if not levels:
            return []

        clustered = []
        levels = sorted(levels)

        current_cluster = [levels[0]]

        for level in levels[1:]:
            # Check if within threshold of cluster average
            cluster_avg = sum(current_cluster) / len(current_cluster)

            if abs(level - cluster_avg) / cluster_avg * 100 <= threshold_pct:
                # Add to current cluster
                current_cluster.append(level)
            else:
                # Start new cluster
                clustered.append(sum(current_cluster) / len(current_cluster))
                current_cluster = [level]

        # Add last cluster
        if current_cluster:
            clustered.append(sum(current_cluster) / len(current_cluster))

        return clustered

    def _calculate_trend(self, hist: pd.DataFrame) -> str:
        """
        Determine current price trend.

        Args:
            hist: Historical price DataFrame

        Returns:
            "bullish", "bearish", or "neutral"
        """
        try:
            # Compare recent periods
            # Short term: 20 days
            # Medium term: 50 days

            if len(hist) < 50:
                return "neutral"

            sma_20 = hist['Close'].rolling(window=20).mean().iloc[-1]
            sma_50 = hist['Close'].rolling(window=50).mean().iloc[-1]
            current_price = hist['Close'].iloc[-1]

            # Bullish: Price > SMA20 > SMA50
            if current_price > sma_20 > sma_50:
                return "bullish"

            # Bearish: Price < SMA20 < SMA50
            if current_price < sma_20 < sma_50:
                return "bearish"

            return "neutral"

        except Exception as e:
            logger.debug(f"Failed to calculate trend: {e}")
            return "neutral"

    def _calculate_rsi(self, hist: pd.DataFrame, window: int = 14) -> Optional[float]:
        """
        Calculate Relative Strength Index.

        Args:
            hist: Historical price DataFrame
            window: RSI window (default: 14)

        Returns:
            RSI value (0-100), or None if insufficient data
        """
        try:
            if len(hist) < window + 1:
                return None

            # Calculate price changes
            delta = hist['Close'].diff()

            # Separate gains and losses
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)

            # Calculate average gains and losses
            avg_gain = gain.rolling(window=window).mean()
            avg_loss = loss.rolling(window=window).mean()

            # Calculate RS and RSI
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

            return float(rsi.iloc[-1])

        except Exception as e:
            logger.debug(f"Failed to calculate RSI: {e}")
            return None

    def _calculate_volume_trend(self, hist: pd.DataFrame) -> str:
        """
        Determine volume trend.

        Args:
            hist: Historical price DataFrame

        Returns:
            "increasing", "decreasing", or "stable"
        """
        try:
            if len(hist) < 40:
                return "stable"

            # Compare recent volume to historical average
            recent_volume = hist['Volume'].iloc[-20:].mean()
            historical_volume = hist['Volume'].iloc[-60:-20].mean()

            if historical_volume == 0:
                return "stable"

            ratio = recent_volume / historical_volume

            if ratio > 1.2:
                return "increasing"
            elif ratio < 0.8:
                return "decreasing"
            else:
                return "stable"

        except Exception as e:
            logger.debug(f"Failed to calculate volume trend: {e}")
            return "stable"

    def _calculate_volatility(self, hist: pd.DataFrame, window: int = 20) -> Optional[float]:
        """
        Calculate historical volatility (annualized).

        Args:
            hist: Historical price DataFrame
            window: Lookback window (default: 20 days)

        Returns:
            Annualized volatility (%), or None if insufficient data
        """
        try:
            if len(hist) < window:
                return None

            # Calculate daily returns
            returns = hist['Close'].pct_change()

            # Calculate standard deviation of returns
            volatility = returns.rolling(window=window).std().iloc[-1]

            # Annualize (252 trading days)
            annualized_vol = volatility * (252 ** 0.5) * 100

            return float(annualized_vol)

        except Exception as e:
            logger.debug(f"Failed to calculate volatility: {e}")
            return None

    def _calculate_distance_to_level(self, current_price: float,
                                     levels: List[float],
                                     direction: str = "below") -> Optional[float]:
        """
        Calculate distance to nearest support/resistance level.

        Args:
            current_price: Current stock price
            levels: List of price levels
            direction: "below" for support, "above" for resistance

        Returns:
            Distance as percentage, or None if no levels
        """
        try:
            if not levels:
                return None

            nearest_level = levels[0]
            distance_pct = ((nearest_level - current_price) / current_price) * 100

            return abs(distance_pct)

        except Exception:
            return None

    def _empty_result(self, ticker: str) -> Dict:
        """Return empty result structure."""
        return {
            'ticker': ticker,
            'current_price': 0,
            'support_levels': [],
            'resistance_levels': [],
            'current_trend': 'unknown',
            'rsi': None,
            'volume_trend': 'unknown',
            'price_vs_support': None,
            'price_vs_resistance': None,
            'volatility_20d': None
        }


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import sys

    logger.info("")
    logger.info('='*70)
    logger.info('TECHNICAL ANALYSIS MODULE')
    logger.info('='*70)
    logger.info("")

    test_tickers = sys.argv[1:] if len(sys.argv) > 1 else ['AAPL', 'NVDA', 'TSLA']

    analyzer = TechnicalAnalyzer()

    for ticker in test_tickers:
        logger.info(f"\nAnalyzing {ticker}...")
        result = analyzer.analyze_ticker(ticker)

        logger.info(f"\nTECHNICAL ANALYSIS - {ticker}")
        logger.info('='*70)
        logger.info(f"Current Price: ${result['current_price']:.2f}")
        logger.info(f"Trend: {result['current_trend'].upper()}")
        logger.info(f"RSI: {result['rsi']}")
        logger.info(f"20-day Volatility: {result['volatility_20d']}%")
        logger.info(f"Volume Trend: {result['volume_trend']}")

        if result['support_levels']:
            logger.info(f"\nSupport Levels:")
            for i, level in enumerate(result['support_levels'], 1):
                logger.info(f"  {i}. ${level:.2f}")
            logger.info(f"Distance to Support: {result['price_vs_support']:.1f}%")

        if result['resistance_levels']:
            logger.info(f"\nResistance Levels:")
            for i, level in enumerate(result['resistance_levels'], 1):
                logger.info(f"  {i}. ${level:.2f}")
            logger.info(f"Distance to Resistance: {result['price_vs_resistance']:.1f}%")

    logger.info("")
    logger.info('='*70)
