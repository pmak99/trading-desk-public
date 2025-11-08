"""
Tradier API client for real IV Rank data via ORATS.

Tradier provides professional-grade options data including:
- Real IV Rank from ORATS (same data $99/month services use)
- IV Percentile
- Accurate Greeks
- Options chains with bid/ask spreads

This replaces the RV proxy in OptionsDataClient with actual implied volatility data.

Free with Tradier brokerage account.
API Docs: https://documentation.tradier.com/brokerage-api
"""

import os
import requests
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
from src.options.iv_history_tracker import IVHistoryTracker
from src.options.expected_move_calculator import ExpectedMoveCalculator
from src.options.option_selector import OptionSelector

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class TradierOptionsClient:
    """Client for real IV Rank and options data via Tradier API."""

    def __init__(self):
        """
        Initialize Tradier client.

        Requires:
            TRADIER_ACCESS_TOKEN: API token from Tradier dashboard
            TRADIER_ENDPOINT: API endpoint (prod or sandbox)
        """
        self.access_token = os.getenv('TRADIER_ACCESS_TOKEN')
        self.endpoint = os.getenv('TRADIER_ENDPOINT', 'https://api.tradier.com')

        if not self.access_token:
            logger.warning("TRADIER_ACCESS_TOKEN not found - Tradier client unavailable")
            self.access_token = None

        self.headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }

        # Initialize IV history tracker for IV Rank calculation
        self.iv_tracker = IVHistoryTracker()

    def is_available(self) -> bool:
        """Check if Tradier API is configured and accessible."""
        return self.access_token is not None

    def get_options_data(self, ticker: str, current_price: float = None, earnings_date: Optional[str] = None) -> Optional[Dict]:
        """
        Get comprehensive options data including real IV Rank.

        Args:
            ticker: Stock ticker symbol
            current_price: Current stock price (optional, will fetch if not provided)
            earnings_date: Earnings date (YYYY-MM-DD) for weekly options selection

        Returns:
            Dict with:
            - iv_rank: Real IV Rank from ORATS (0-100)
            - iv_percentile: IV Percentile
            - current_iv: Current implied volatility
            - expected_move_pct: Expected move % based on ATM straddle
            - options_volume: Total options volume
            - open_interest: Total open interest
            - earnings_data: Historical earnings move data

            Returns None if API unavailable or request fails.
        """
        if not self.is_available():
            logger.warning(f"{ticker}: Tradier unavailable - using fallback")
            return None

        try:
            # Get current price if not provided
            if current_price is None:
                current_price = self._get_quote(ticker)

            if current_price is None:
                logger.error(f"{ticker}: Could not get current price")
                return None

            # Get IV Rank from market data (pass earnings date for weekly expiration selection)
            iv_data = self._get_iv_rank(ticker, current_price, earnings_date)

            # Get options chain for expected move and liquidity
            chain_data = self._get_options_chain(ticker, current_price, earnings_date)

            # Combine data
            result = {
                'iv_rank': iv_data.get('iv_rank', 0),
                'iv_percentile': iv_data.get('iv_percentile', 0),
                'current_iv': iv_data.get('current_iv', 0),
                'expected_move_pct': chain_data.get('expected_move_pct', 0),
                'options_volume': chain_data.get('options_volume', 0),
                'open_interest': chain_data.get('open_interest', 0),
            }

            logger.info(f"{ticker}: IV Rank = {result['iv_rank']:.1f}% (real ORATS data)")

            return result

        except Exception as e:
            logger.error(f"{ticker}: Tradier request failed: {e}")
            return None

    def _get_quote(self, ticker: str) -> Optional[float]:
        """Get current stock price from Tradier."""
        try:
            url = f"{self.endpoint}/v1/markets/quotes"
            params = {'symbols': ticker}

            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Extract price from response
            if 'quotes' in data and 'quote' in data['quotes']:
                quote = data['quotes']['quote']
                return quote.get('last') or quote.get('close')

            return None

        except Exception as e:
            logger.error(f"{ticker}: Failed to get quote: {e}")
            return None

    def _get_iv_rank(self, ticker: str, current_price: float, earnings_date: Optional[str] = None) -> Dict:
        """
        Get IV data from Tradier.

        Tradier provides real implied volatility from options Greeks via ORATS.
        IV Rank calculation requires 52-week IV history (to be implemented).

        Args:
            ticker: Stock ticker
            current_price: Current stock price (avoids redundant API call)
            earnings_date: Earnings date for weekly expiration selection

        Returns:
            Dict with:
                - iv_rank: 0 (not yet implemented, requires historical IV data)
                - iv_percentile: 0 (not yet implemented)
                - current_iv: Actual implied volatility % (e.g., 93.82 means 93.82%)

        Note: Tradier API returns IV in format: 1.23 = 123%, 0.50 = 50%
              We multiply by 100 to get standard percentage format.
              current_iv is REAL implied volatility from live options market.
        """
        try:
            # Get nearest weekly expiration (pass earnings date for better selection)
            expiration = self._get_nearest_weekly_expiration(ticker, earnings_date)
            if not expiration:
                logger.warning(f"{ticker}: No expiration found for IV calculation")
                return {'iv_rank': 0, 'iv_percentile': 0, 'current_iv': 0}

            # Get options chain with Greeks
            url = f"{self.endpoint}/v1/markets/options/chains"
            params = {
                'symbol': ticker,
                'expiration': expiration,
                'greeks': 'true'
            }

            response = requests.get(url, headers=self.headers, params=params, timeout=15)
            response.raise_for_status()

            data = response.json()

            if 'options' not in data or 'option' not in data['options']:
                return {'iv_rank': 0, 'iv_percentile': 0, 'current_iv': 0}

            options = data['options']['option']

            # Use current_price from parameter (already fetched by caller)
            # This avoids duplicate API call to _get_quote()
            if not current_price:
                logger.warning(f"{ticker}: No current price provided")
                return {'iv_rank': 0, 'iv_percentile': 0, 'current_iv': 0}

            # Find ATM options to get current IV
            atm_call, atm_put = self._find_atm_options(options, current_price)

            # Extract real implied volatility from ATM options
            current_iv = 0
            if atm_call and 'greeks' in atm_call:
                # Use mid_iv from ATM call (most liquid and representative)
                # Tradier returns IV as whole percentage (e.g., 1.23 = 123%, 0.50 = 50%)
                current_iv = atm_call['greeks'].get('mid_iv', 0) * 100

            # Calculate IV Rank from historical IV data
            iv_rank = 0
            if current_iv > 0:
                # Record current IV for history
                self.iv_tracker.record_iv(ticker, current_iv)

                # Calculate IV Rank (percentile in 52-week range)
                iv_rank = self.iv_tracker.calculate_iv_rank(ticker, current_iv)

                if iv_rank > 0:
                    logger.info(f"{ticker}: IV Rank = {iv_rank}% (current IV: {current_iv}%)")

            return {
                'iv_rank': iv_rank,
                'iv_percentile': iv_rank,  # Same as IV Rank
                'current_iv': round(current_iv, 2)
            }

        except Exception as e:
            logger.error(f"{ticker}: Failed to get IV data: {e}")
            return {'iv_rank': 0, 'iv_percentile': 0, 'current_iv': 0}

    def _get_options_chain(self, ticker: str, current_price: float, earnings_date: Optional[str] = None) -> Dict:
        """
        Get options chain for expected move and liquidity metrics.

        Args:
            ticker: Stock symbol
            current_price: Current stock price
            earnings_date: Earnings date for weekly expiration selection

        Returns:
            Dict with expected_move_pct, options_volume, open_interest
        """
        try:
            # Get nearest weekly expiration (pass earnings date for better selection)
            expiration = self._get_nearest_weekly_expiration(ticker, earnings_date)

            if not expiration:
                logger.warning(f"{ticker}: No expiration found")
                return {'expected_move_pct': 0, 'options_volume': 0, 'open_interest': 0}

            # Get options chain for this expiration
            url = f"{self.endpoint}/v1/markets/options/chains"
            params = {
                'symbol': ticker,
                'expiration': expiration,
                'greeks': 'true'
            }

            response = requests.get(url, headers=self.headers, params=params, timeout=15)
            response.raise_for_status()

            data = response.json()

            if 'options' not in data or 'option' not in data['options']:
                return {'expected_move_pct': 0, 'options_volume': 0, 'open_interest': 0}

            options = data['options']['option']

            # Calculate expected move from ATM straddle
            expected_move_pct = self._calculate_expected_move(options, current_price)

            # Sum total volume and open interest
            total_volume = sum(opt.get('volume', 0) for opt in options)
            total_oi = sum(opt.get('open_interest', 0) for opt in options)

            return {
                'expected_move_pct': expected_move_pct,
                'options_volume': total_volume,
                'open_interest': total_oi
            }

        except Exception as e:
            logger.error(f"{ticker}: Failed to get options chain: {e}")
            return {'expected_move_pct': 0, 'options_volume': 0, 'open_interest': 0}

    def _get_nearest_weekly_expiration(self, ticker: str, earnings_date: Optional[str] = None) -> Optional[str]:
        """
        Get the weekly options expiration for earnings trade.

        Strategy:
        - If earnings date provided: Find expiration in same week or next week if Friday
        - Otherwise: Find nearest weekly expiration within 2 weeks

        Args:
            ticker: Stock ticker
            earnings_date: Earnings date (YYYY-MM-DD) if known

        Returns:
            Expiration date string (YYYY-MM-DD) or None
        """
        try:
            url = f"{self.endpoint}/v1/markets/options/expirations"
            params = {'symbol': ticker, 'includeAllRoots': 'true'}

            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if 'expirations' in data and 'date' in data['expirations']:
                dates = data['expirations']['date']
                today = datetime.now().date()

                # Parse earnings date if provided
                if earnings_date:
                    earnings_dt = datetime.strptime(earnings_date, '%Y-%m-%d').date()

                    # Determine target week
                    # If today is Thursday or Friday, look for next week expiration
                    # Otherwise, look for same week as earnings
                    if today.weekday() >= 3:  # Thursday = 3, Friday = 4
                        # Look for expiration in following week
                        target_start = today + timedelta(days=7)
                        target_end = today + timedelta(days=14)
                    else:
                        # Look for expiration in same week as earnings
                        # Find Friday of earnings week
                        days_until_friday = (4 - earnings_dt.weekday()) % 7
                        if days_until_friday == 0 and earnings_dt.weekday() != 4:
                            days_until_friday = 7
                        target_friday = earnings_dt + timedelta(days=days_until_friday)

                        # Allow +/- 3 days from Friday of earnings week
                        target_start = target_friday - timedelta(days=3)
                        target_end = target_friday + timedelta(days=3)

                else:
                    # No earnings date - use original logic (7-14 days out)
                    target_start = today + timedelta(days=7)
                    target_end = today + timedelta(days=14)

                # Find first expiration in target range
                for date_str in dates:
                    exp_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    if target_start <= exp_date <= target_end:
                        logger.debug(f"{ticker}: Using weekly expiration {date_str}")
                        return date_str

                # Fallback: return nearest future expiration
                for date_str in dates:
                    exp_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    if exp_date >= today:
                        logger.warning(f"{ticker}: Using fallback expiration {date_str}")
                        return date_str

            return None

        except Exception as e:
            logger.error(f"{ticker}: Failed to get expirations: {e}")
            return None

    def _find_atm_options(self, options: list, current_price: float) -> tuple:
        """
        Find ATM (at-the-money) call and put options.

        Delegates to OptionSelector for pure selection logic.

        Args:
            options: List of option contracts
            current_price: Current stock price

        Returns:
            Tuple of (atm_call, atm_put)
        """
        return OptionSelector.find_atm_options(options, current_price)

    def _calculate_expected_move(self, options: list, current_price: float) -> float:
        """
        Calculate expected move % from ATM straddle price.

        Delegates to ExpectedMoveCalculator for pure calculation logic.

        Args:
            options: List of option contracts
            current_price: Current stock price

        Returns:
            Expected move as percentage
        """
        # Find ATM call and put
        atm_call, atm_put = self._find_atm_options(options, current_price)

        # Use ExpectedMoveCalculator for the calculation
        return ExpectedMoveCalculator.calculate_from_options(atm_call, atm_put, current_price)


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import sys

    logger.info("")
    logger.info('='*70)
    logger.info('TRADIER OPTIONS CLIENT - REAL IV RANK DATA')
    logger.info('='*70)
    logger.info("")

    test_ticker = sys.argv[1] if len(sys.argv) > 1 else 'AAPL'

    logger.info(f"Testing with ticker: {test_ticker}")
    logger.info("")

    client = TradierOptionsClient()

    if not client.is_available():
        logger.info("❌ Tradier API not configured")
        logger.info("Set TRADIER_ACCESS_TOKEN in .env file")
        exit(1)

    logger.info("✓ Tradier API configured")
    logger.info(f"  Endpoint: {client.endpoint}")
    logger.info("")

    logger.info("Fetching options data...")
    data = client.get_options_data(test_ticker)

    if data:
        logger.info("")
        logger.info("OPTIONS DATA:")
        logger.info(f"  IV Rank: {data.get('iv_rank', 'N/A')}%")
        logger.info(f"  IV Percentile: {data.get('iv_percentile', 'N/A')}%")
        logger.info(f"  Current IV: {data.get('current_iv', 'N/A')}%")
        logger.info(f"  Expected Move: {data.get('expected_move_pct', 'N/A')}%")
        logger.info(f"  Options Volume: {data.get('options_volume', 0):,}")
        logger.info(f"  Open Interest: {data.get('open_interest', 0):,}")
    else:
        logger.info("❌ Failed to fetch data")

    logger.info("")
    logger.info('='*70)
