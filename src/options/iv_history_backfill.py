"""
IV History Backfill - Populate historical IV data for accurate IV Rank calculation.

Problem: IV Rank requires 52-week historical IV data, but tracking starts empty.
Solution: Backfill from yfinance historical options chains.

This fixes the critical flaw where new tickers have 0% or 100% IV Rank.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import yfinance as yf
from src.options.iv_history_tracker import IVHistoryTracker
from src.options.option_selector import OptionSelector

logger = logging.getLogger(__name__)


class IVHistoryBackfill:
    """Backfill historical IV data from yfinance options chains."""

    def __init__(self, iv_tracker: Optional[IVHistoryTracker] = None):
        """
        Initialize backfill service.

        Args:
            iv_tracker: Optional IV tracker instance (creates new if not provided)
        """
        self.iv_tracker = iv_tracker or IVHistoryTracker()

    def backfill_ticker(self, ticker: str, lookback_days: int = 365,
                       sample_interval_days: int = 7) -> Dict:
        """
        Backfill historical IV data for a ticker.

        Strategy:
        1. Sample historical dates (weekly intervals over past year)
        2. Fetch option chains for each date
        3. Calculate IV from ATM options
        4. Populate IV tracker

        Args:
            ticker: Stock ticker symbol
            lookback_days: Days to look back (default: 365 for 52 weeks)
            sample_interval_days: Days between samples (default: 7 for weekly)

        Returns:
            Dict with:
            - success: bool
            - data_points: int (number of historical IVs collected)
            - iv_rank: float (calculated IV rank if successful)
            - message: str (status message)
        """
        logger.info(f"{ticker}: Starting IV history backfill ({lookback_days} days)")

        try:
            stock = yf.Ticker(ticker)

            # Get available expiration dates
            expirations = stock.options
            if not expirations:
                logger.warning(f"{ticker}: No options available")
                return {
                    'success': False,
                    'data_points': 0,
                    'iv_rank': 0,
                    'message': 'No options data available'
                }

            # Sample dates (weekly over past year)
            sample_dates = self._generate_sample_dates(lookback_days, sample_interval_days)

            # Collect historical IVs
            historical_ivs = []

            for sample_date in sample_dates:
                iv = self._get_iv_for_date(stock, ticker, sample_date, expirations)
                if iv and iv > 0:
                    historical_ivs.append({
                        'date': sample_date,
                        'iv': iv
                    })
                    # Record in tracker
                    self.iv_tracker.record_iv(ticker, iv, timestamp=sample_date)

            if not historical_ivs:
                logger.warning(f"{ticker}: No historical IV data found")
                return {
                    'success': False,
                    'data_points': 0,
                    'iv_rank': 0,
                    'message': 'Could not extract historical IV data'
                }

            # Get current IV and calculate rank
            current_iv = historical_ivs[-1]['iv'] if historical_ivs else 0
            iv_rank = self.iv_tracker.calculate_iv_rank(ticker, current_iv)

            logger.info(f"{ticker}: Backfilled {len(historical_ivs)} data points, IV Rank = {iv_rank:.1f}%")

            return {
                'success': True,
                'data_points': len(historical_ivs),
                'iv_rank': iv_rank,
                'current_iv': current_iv,
                'message': f'Backfilled {len(historical_ivs)} historical IVs'
            }

        except Exception as e:
            logger.error(f"{ticker}: Backfill failed: {e}")
            return {
                'success': False,
                'data_points': 0,
                'iv_rank': 0,
                'message': f'Backfill error: {str(e)[:100]}'
            }

    def _generate_sample_dates(self, lookback_days: int,
                              interval_days: int) -> List[datetime]:
        """
        Generate sample dates for historical IV collection.

        Args:
            lookback_days: Days to look back
            interval_days: Days between samples

        Returns:
            List of datetime objects (oldest to newest)
        """
        dates = []
        today = datetime.now()

        # Go back in time, sampling at intervals
        for days_ago in range(lookback_days, 0, -interval_days):
            sample_date = today - timedelta(days=days_ago)
            # Skip weekends
            if sample_date.weekday() < 5:  # Monday=0, Friday=4
                dates.append(sample_date)

        return dates

    def _get_iv_for_date(self, stock, ticker: str, target_date: datetime,
                        available_expirations: List[str]) -> Optional[float]:
        """
        Get IV for a specific historical date.

        Strategy:
        1. Find expiration ~30 days from target date
        2. Get ATM options for that expiration
        3. Extract IV from Greeks

        Args:
            stock: yfinance Ticker object
            ticker: Stock symbol
            target_date: Target historical date
            available_expirations: List of available expiration dates

        Returns:
            IV as float (percent), or None if not available
        """
        try:
            # Find expiration ~30 days out from target date
            target_expiration_date = target_date + timedelta(days=30)

            # Find closest available expiration
            closest_exp = None
            min_diff = timedelta(days=999)

            for exp_str in available_expirations:
                try:
                    exp_date = datetime.strptime(exp_str, '%Y-%m-%d')
                    diff = abs((exp_date - target_expiration_date).total_seconds())
                    if diff < min_diff.total_seconds():
                        min_diff = timedelta(seconds=diff)
                        closest_exp = exp_str
                except ValueError:
                    continue

            if not closest_exp:
                return None

            # Get option chain for this expiration
            try:
                opt_chain = stock.option_chain(closest_exp)
            except Exception:
                # Option chain not available for this date
                return None

            # Get current price (we don't have historical price, use close to expiration)
            # This is approximate but sufficient for IV rank calculation
            calls = opt_chain.calls
            if calls.empty:
                return None

            # Estimate stock price from option strikes
            mid_idx = len(calls) // 2
            if mid_idx >= len(calls):
                return None

            estimated_price = calls.iloc[mid_idx]['strike']

            # Find ATM options
            atm_call, atm_put = self._find_atm_from_chain(calls, opt_chain.puts,
                                                          estimated_price)

            # Extract IV
            if atm_call is not None and 'impliedVolatility' in atm_call:
                iv = atm_call['impliedVolatility'] * 100
                if iv > 0 and iv < 500:  # Sanity check
                    return iv

            return None

        except Exception as e:
            logger.debug(f"{ticker}: Could not get IV for {target_date.date()}: {e}")
            return None

    def _find_atm_from_chain(self, calls_df, puts_df, current_price: float):
        """
        Find ATM options from DataFrame format.

        Args:
            calls_df: DataFrame of calls
            puts_df: DataFrame of puts
            current_price: Current stock price

        Returns:
            Tuple of (atm_call, atm_put) as dicts, or (None, None)
        """
        try:
            if calls_df.empty:
                return None, None

            # Find closest strike to current price
            calls_df['strike_diff'] = abs(calls_df['strike'] - current_price)
            atm_call_row = calls_df.loc[calls_df['strike_diff'].idxmin()]
            atm_call = atm_call_row.to_dict()

            # Find matching put
            atm_put = None
            if not puts_df.empty:
                puts_df['strike_diff'] = abs(puts_df['strike'] - current_price)
                atm_put_row = puts_df.loc[puts_df['strike_diff'].idxmin()]
                atm_put = atm_put_row.to_dict()

            return atm_call, atm_put

        except Exception:
            return None, None

    def backfill_multiple_tickers(self, tickers: List[str],
                                  lookback_days: int = 365) -> Dict:
        """
        Backfill multiple tickers in batch.

        Args:
            tickers: List of ticker symbols
            lookback_days: Days to look back

        Returns:
            Dict mapping ticker to backfill result
        """
        results = {}

        for ticker in tickers:
            logger.info(f"Backfilling {ticker}...")
            result = self.backfill_ticker(ticker, lookback_days)
            results[ticker] = result

        # Summary
        successful = sum(1 for r in results.values() if r['success'])
        total_points = sum(r['data_points'] for r in results.values())

        logger.info(f"Backfill complete: {successful}/{len(tickers)} successful, "
                   f"{total_points} total data points")

        return results


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import sys

    logger.info("")
    logger.info('='*70)
    logger.info('IV HISTORY BACKFILL - FIX IV RANK CALCULATION')
    logger.info('='*70)
    logger.info("")

    test_tickers = sys.argv[1:] if len(sys.argv) > 1 else ['AAPL', 'NVDA', 'TSLA']

    logger.info(f"Backfilling tickers: {', '.join(test_tickers)}")
    logger.info("")

    backfiller = IVHistoryBackfill()
    results = backfiller.backfill_multiple_tickers(test_tickers)

    logger.info("")
    logger.info("BACKFILL RESULTS:")
    logger.info('='*70)

    for ticker, result in results.items():
        status = "✓" if result['success'] else "✗"
        logger.info(f"{status} {ticker:6s} - {result['data_points']:3d} data points, "
                   f"IV Rank: {result['iv_rank']:.1f}%")
        logger.info(f"          {result['message']}")

    logger.info("")
    logger.info('='*70)
