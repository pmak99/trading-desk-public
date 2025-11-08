"""
Options data client using yfinance (free API).

Provides options metrics for IV crush strategy:
- IV Rank & IV Percentile (using realized volatility as proxy)
- Expected move calculations from ATM straddle
- Options chain data (volume, OI, bid-ask spreads)
- Historical implied vs actual move analysis
- Options liquidity metrics

NOTE: Uses yfinance (free), not Alpha Vantage API.
IV Rank is calculated using realized volatility as a proxy for implied volatility.
Real IV Rank requires historical IV data which is not available in free APIs.
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import yfinance as yf

logger = logging.getLogger(__name__)


class OptionsDataClient:
    """Client for options data using yfinance (free)."""

    def __init__(self):
        """Initialize options data client (no API key needed for yfinance)."""
        self.calls_made = 0

    def get_options_data(self, ticker: str) -> Dict:
        """
        Get comprehensive options data for a ticker.

        This method fetches yfinance data. For better performance,
        use get_options_data_from_stock() if you already have the stock object.

        Args:
            ticker: Ticker symbol

        Returns:
            Dict with options metrics
        """
        logger.info(f"Fetching options data for {ticker}...")

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period='1y')
            current_price = stock.info.get('currentPrice', hist['Close'].iloc[-1])

            return self.get_options_data_from_stock(stock, ticker, hist, current_price)

        except Exception as e:
            logger.error(f"Error fetching options data for {ticker}: {e}")
            return {}

    def get_options_data_from_stock(
        self,
        stock,
        ticker: str,
        hist,
        current_price: float
    ) -> Dict:
        """
        Get comprehensive options data from already-fetched yfinance objects.

        This method avoids duplicate API calls by reusing data.

        Args:
            stock: yfinance Ticker object
            ticker: Ticker symbol
            hist: Historical price DataFrame (1y period)
            current_price: Current stock price

        Returns:
            Dict with:
            - iv_rank: IV Rank (0-100 percentile) - uses RV as proxy
            - iv_percentile: IV Percentile (0-100)
            - current_iv: Current implied volatility
            - expected_move: Expected move $ for next earnings
            - expected_move_pct: Expected move % for next earnings
            - iv_crush_ratio: Historical implied/actual ratio (>1 = edge)
            - options_volume: Total options volume
            - open_interest: Total open interest
            - bid_ask_spread_pct: Avg bid-ask spread %
        """
        logger.info(f"Calculating options metrics for {ticker}...")

        try:
            # Get current IV and calculate IV Rank (using historical data already fetched)
            iv_data = self._calculate_iv_rank_from_hist(hist, stock, ticker)

            # Get options chain for liquidity and expected move
            options_data = self._get_options_chain_data(stock, current_price)

            # Get historical earnings behavior
            earnings_data = self._get_historical_earnings_behavior(stock, ticker, hist)

            # Combine all data
            result = {
                **iv_data,
                **options_data,
                **earnings_data
            }

            logger.info(f"{ticker}: IV Rank={result.get('iv_rank', 'N/A')}%, "
                       f"Expected Move={result.get('expected_move_pct', 'N/A')}%")

            return result

        except Exception as e:
            logger.error(f"Error calculating options metrics for {ticker}: {e}")
            return {}

    def _calculate_iv_rank(self, stock, ticker: str) -> Dict:
        """
        Calculate IV Rank and IV Percentile.

        This method fetches historical data. Use _calculate_iv_rank_from_hist()
        if you already have the historical data.

        Args:
            stock: yfinance Ticker object
            ticker: Ticker symbol

        Returns:
            Dict with iv_rank, iv_percentile, current_iv
        """
        try:
            hist = stock.history(period='1y')
            if hist.empty:
                logger.warning(f"{ticker}: No historical data for IV calculation")
                return {'iv_rank': None, 'iv_percentile': None, 'current_iv': None}

            return self._calculate_iv_rank_from_hist(hist, stock, ticker)

        except Exception as e:
            logger.error(f"{ticker}: Error calculating IV Rank: {e}")
            return {'iv_rank': None, 'iv_percentile': None, 'current_iv': None}

    def _calculate_iv_rank_from_hist(self, hist, stock, ticker: str) -> Dict:
        """
        Calculate IV Rank from already-fetched historical data.

        NOTE: This uses REALIZED VOLATILITY as a proxy for IMPLIED VOLATILITY.
        Real IV Rank requires historical IV data which is not available in free APIs.

        IV Rank = (Current RV - 52w Low RV) / (52w High RV - 52w Low RV) * 100

        Args:
            hist: Historical price DataFrame (1y period)
            stock: yfinance Ticker object
            ticker: Ticker symbol

        Returns:
            Dict with iv_rank, iv_percentile, current_iv
        """
        try:
            if hist.empty:
                logger.warning(f"{ticker}: No historical data for IV calculation")
                return {'iv_rank': None, 'iv_percentile': None, 'current_iv': None}

            # Calculate 30-day realized volatility for each day
            hist['returns'] = hist['Close'].pct_change()
            hist['rv_30'] = hist['returns'].rolling(window=30).std() * (252 ** 0.5)

            current_rv = hist['rv_30'].iloc[-1]
            min_rv = hist['rv_30'].min()
            max_rv = hist['rv_30'].max()

            if max_rv == min_rv:
                iv_rank = 50  # Neutral if no range
            else:
                iv_rank = ((current_rv - min_rv) / (max_rv - min_rv)) * 100

            # Get implied volatility from options if available
            try:
                options = stock.options
                if options:
                    exp_date = options[0]  # Nearest expiration
                    chain = stock.option_chain(exp_date)

                    # Use ATM options for current IV
                    current_price = stock.info.get('currentPrice', hist['Close'].iloc[-1])
                    atm_calls = chain.calls[
                        (chain.calls['strike'] >= current_price * 0.95) &
                        (chain.calls['strike'] <= current_price * 1.05)
                    ]

                    if not atm_calls.empty and 'impliedVolatility' in atm_calls.columns:
                        current_iv = atm_calls['impliedVolatility'].mean()
                    else:
                        current_iv = current_rv
                else:
                    current_iv = current_rv
            except Exception as e:
                logger.debug(f"{ticker}: Using realized vol as IV: {e}")
                current_iv = current_rv

            return {
                'iv_rank': round(iv_rank, 1),
                'iv_percentile': round(iv_rank, 1),  # Same as IV Rank for this calculation
                'current_iv': round(current_iv, 4)
            }

        except Exception as e:
            logger.error(f"{ticker}: Error calculating IV Rank: {e}")
            return {'iv_rank': None, 'iv_percentile': None, 'current_iv': None}

    def _get_options_chain_data(self, stock, current_price: float) -> Dict:
        """
        Get options chain data for liquidity and expected move.

        Args:
            stock: yfinance Ticker object
            current_price: Current stock price (to avoid additional fetch)

        Returns:
            Dict with expected_move, options_volume, open_interest, bid_ask_spread_pct
        """
        try:
            options = stock.options
            if not options:
                logger.warning(f"No options chain available")
                return {
                    'expected_move': None,
                    'expected_move_pct': None,
                    'options_volume': 0,
                    'open_interest': 0,
                    'bid_ask_spread_pct': None
                }

            # Get nearest expiration (assume weekly or earnings cycle)
            exp_date = options[0]
            chain = stock.option_chain(exp_date)

            # Calculate expected move from ATM straddle
            # Find the closest strike to current price
            all_strikes = chain.calls['strike'].unique()
            closest_strike = min(all_strikes, key=lambda x: abs(x - current_price))

            atm_calls = chain.calls[chain.calls['strike'] == closest_strike]
            atm_puts = chain.puts[chain.puts['strike'] == closest_strike]

            if not atm_calls.empty and not atm_puts.empty:
                # Expected move = (ATM Call Price + ATM Put Price)
                # Use last price, or midpoint of bid/ask if last price is 0
                if atm_calls['lastPrice'].iloc[0] > 0:
                    call_price = atm_calls['lastPrice'].iloc[0]
                elif 'bid' in atm_calls.columns and 'ask' in atm_calls.columns:
                    call_price = (atm_calls['bid'].iloc[0] + atm_calls['ask'].iloc[0]) / 2
                else:
                    call_price = 0

                if atm_puts['lastPrice'].iloc[0] > 0:
                    put_price = atm_puts['lastPrice'].iloc[0]
                elif 'bid' in atm_puts.columns and 'ask' in atm_puts.columns:
                    put_price = (atm_puts['bid'].iloc[0] + atm_puts['ask'].iloc[0]) / 2
                else:
                    put_price = 0

                if call_price > 0 and put_price > 0:
                    expected_move = call_price + put_price
                    expected_move_pct = (expected_move / current_price) * 100
                else:
                    expected_move = None
                    expected_move_pct = None
            else:
                expected_move = None
                expected_move_pct = None

            # Calculate total options volume and open interest
            total_call_volume = chain.calls['volume'].sum() if 'volume' in chain.calls else 0
            total_put_volume = chain.puts['volume'].sum() if 'volume' in chain.puts else 0
            options_volume = total_call_volume + total_put_volume

            total_call_oi = chain.calls['openInterest'].sum() if 'openInterest' in chain.calls else 0
            total_put_oi = chain.puts['openInterest'].sum() if 'openInterest' in chain.puts else 0
            open_interest = total_call_oi + total_put_oi

            # Calculate average bid-ask spread %
            def calc_spread(df):
                if 'bid' in df.columns and 'ask' in df.columns:
                    df = df[(df['bid'] > 0) & (df['ask'] > 0)]
                    if not df.empty:
                        return ((df['ask'] - df['bid']) / df['ask']).mean()
                return None

            call_spread = calc_spread(chain.calls)
            put_spread = calc_spread(chain.puts)

            if call_spread is not None and put_spread is not None:
                bid_ask_spread_pct = (call_spread + put_spread) / 2
            elif call_spread is not None:
                bid_ask_spread_pct = call_spread
            elif put_spread is not None:
                bid_ask_spread_pct = put_spread
            else:
                bid_ask_spread_pct = None

            return {
                'expected_move': round(expected_move, 2) if expected_move else None,
                'expected_move_pct': round(expected_move_pct, 2) if expected_move_pct else None,
                'options_volume': int(options_volume),
                'open_interest': int(open_interest),
                'bid_ask_spread_pct': round(bid_ask_spread_pct, 4) if bid_ask_spread_pct else None
            }

        except Exception as e:
            logger.error(f"Error getting options chain data: {e}")
            return {
                'expected_move': None,
                'expected_move_pct': None,
                'options_volume': 0,
                'open_interest': 0,
                'bid_ask_spread_pct': None
            }

    def _get_historical_earnings_behavior(self, stock, ticker: str, hist=None) -> Dict:
        """
        Analyze historical earnings behavior: implied vs actual moves.

        This calculates the IV crush edge ratio:
        - Ratio > 1.0 means implied move consistently > actual move (GOOD for selling premium)
        - Ratio < 1.0 means actual move > implied (BAD - market underprices risk)

        Args:
            stock: yfinance Ticker object
            ticker: Ticker symbol
            hist: Optional pre-fetched historical data (2y period recommended)

        Returns:
            Dict with iv_crush_ratio, historical_moves list
        """
        try:
            # Get earnings dates
            earnings = stock.earnings_dates
            if earnings is None or earnings.empty:
                logger.warning(f"{ticker}: No earnings history available")
                return {'iv_crush_ratio': None, 'historical_moves': []}

            # Get historical price data if not provided
            if hist is None or hist.empty:
                hist = stock.history(period='2y')

            if hist.empty:
                return {'iv_crush_ratio': None, 'historical_moves': []}

            moves = []

            # Analyze last 4-8 quarters (user's criteria)
            for i, (date, _) in enumerate(earnings.head(8).iterrows()):
                try:
                    # Get price movement on earnings day
                    earnings_date = date.date() if hasattr(date, 'date') else date

                    # Find closest trading day
                    hist_dates = [d.date() for d in hist.index]
                    closest_idx = min(range(len(hist_dates)),
                                    key=lambda i: abs((hist_dates[i] - earnings_date).days))

                    if closest_idx > 0:
                        before_price = hist['Close'].iloc[closest_idx - 1]
                        after_price = hist['Close'].iloc[closest_idx]
                        actual_move_pct = abs((after_price - before_price) / before_price) * 100

                        moves.append({
                            'date': earnings_date.strftime('%Y-%m-%d'),
                            'actual_move_pct': round(actual_move_pct, 2)
                        })
                except Exception as e:
                    logger.debug(f"{ticker}: Error processing earnings date {date}: {e}")
                    continue

            # Calculate IV crush ratio (will be filled in when we have implied moves)
            # For now, return historical actual moves only
            if moves:
                avg_actual_move = sum(m['actual_move_pct'] for m in moves) / len(moves)
                return {
                    'iv_crush_ratio': None,  # Will calculate when we have implied moves
                    'historical_moves': moves,
                    'avg_actual_move_pct': round(avg_actual_move, 2),
                    'last_earnings_move': moves[0]['actual_move_pct'] if moves else None,
                    'earnings_beat_rate': None  # Not calculated yet
                }
            else:
                # IMPORTANT: Always include avg_actual_move_pct key even if 0
                # Otherwise ticker_filter.py will default to 0 and break IV crush ratio calc
                return {
                    'iv_crush_ratio': None,
                    'historical_moves': [],
                    'avg_actual_move_pct': None,  # Use None instead of missing key
                    'last_earnings_move': None,
                    'earnings_beat_rate': None
                }

        except Exception as e:
            logger.error(f"{ticker}: Error analyzing historical earnings: {e}")
            return {
                'iv_crush_ratio': None,
                'historical_moves': [],
                'avg_actual_move_pct': None,
                'last_earnings_move': None,
                'earnings_beat_rate': None
            }


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import sys

    logger.info("")
    logger.info('='*70)
    logger.info('OPTIONS DATA CLIENT (yfinance)')
    logger.info('='*70)
    logger.info("")

    # Test with a ticker
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else 'AAPL'

    client = OptionsDataClient()

    logger.info(f"Testing with ticker: {test_ticker}")
    logger.info("")

    options_data = client.get_options_data(test_ticker)

    if options_data:
        logger.info("OPTIONS DATA:")
        logger.info(f"  IV Rank: {options_data.get('iv_rank', 'N/A')}% (using RV as proxy)")
        logger.info(f"  IV Percentile: {options_data.get('iv_percentile', 'N/A')}%")
        logger.info(f"  Current IV: {options_data.get('current_iv', 'N/A')}")
        logger.info(f"  Expected Move: ${options_data.get('expected_move', 'N/A')} ({options_data.get('expected_move_pct', 'N/A')}%)")
        logger.info(f"  Options Volume: {options_data.get('options_volume', 'N/A'):,}")
        logger.info(f"  Open Interest: {options_data.get('open_interest', 'N/A'):,}")
        logger.info(f"  Bid-Ask Spread: {options_data.get('bid_ask_spread_pct', 'N/A')}")
        logger.info(f"  IV Crush Ratio: {options_data.get('iv_crush_ratio', 'N/A')}")
        logger.info(f"  Avg Actual Move: {options_data.get('avg_actual_move_pct', 'N/A')}%")

        if options_data.get('historical_moves'):
            logger.info(f"\nHistorical Earnings Moves (Last {len(options_data['historical_moves'])} quarters):")
            for move in options_data['historical_moves']:
                logger.info(f"  {move['date']}: {move['actual_move_pct']}%")
    else:
        logger.info("Failed to fetch options data")

    logger.info("")
    logger.info('='*70)
    logger.info(f"yfinance calls made: {client.calls_made} (unlimited free)")
    logger.info('='*70)
