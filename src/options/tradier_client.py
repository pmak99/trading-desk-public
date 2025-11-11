"""
Tradier API client for real IV data (ORATS integration).

Provides professional-grade options data:
    - Real IV Rank (vs yfinance RV proxy)
    - IV Percentile and Greeks
    - Options chains with liquidity metrics

Free with Tradier account. API: https://documentation.tradier.com/
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
from src.core.timezone_utils import get_eastern_now

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

        # Use requests.Session for connection pooling
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        # Initialize IV history tracker for IV Rank calculation
        self.iv_tracker = IVHistoryTracker()

    def is_available(self) -> bool:
        """Check if Tradier API is configured and accessible."""
        return self.access_token is not None

    def get_options_data(self, ticker: str, current_price: float = None, earnings_date: Optional[str] = None) -> Optional[Dict]:
        """
        Get comprehensive options data including real IV Rank.

        Fetches options chain once and extracts all needed metrics.

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

            # Get expiration date for earnings
            expiration = self._get_nearest_weekly_expiration(ticker, earnings_date)
            if not expiration:
                logger.warning(f"{ticker}: No expiration found")
                return None

            # OPTIMIZATION: Fetch options chain ONCE (was fetching twice before!)
            # This eliminates a duplicate API call per ticker
            options_chain = self._fetch_options_chain(ticker, expiration)
            if not options_chain:
                return None

            # Extract current IV from the chain
            iv_data = self._extract_current_iv(options_chain, current_price, ticker)

            # Extract liquidity metrics from the SAME chain
            liquidity_data = self._extract_liquidity_metrics(options_chain, current_price)

            # Combine data
            result = {
                'current_iv': iv_data.get('current_iv', 0),
                'expected_move_pct': liquidity_data.get('expected_move_pct', 0),
                'options_volume': liquidity_data.get('options_volume', 0),
                'open_interest': liquidity_data.get('open_interest', 0),
            }

            logger.info(f"{ticker}: Current IV = {result['current_iv']:.1f}% (real Tradier data)")

            return result

        except (KeyError, ValueError, TypeError) as e:
            # Data parsing/access errors - likely API response format changed
            logger.error(f"{ticker}: Tradier data parsing error (check API response format): {e}")
            return None
        except Exception as e:
            # Catch-all for unexpected errors (network issues, etc.)
            logger.error(f"{ticker}: Unexpected Tradier error: {e}")
            return None

    def _get_quote(self, ticker: str) -> Optional[float]:
        """Get current stock price from Tradier."""
        try:
            url = f"{self.endpoint}/v1/markets/quotes"
            params = {'symbols': ticker}

            # Use session for connection pooling
            response = self.session.get(url, params=params, timeout=10)
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

    def _fetch_options_chain(self, ticker: str, expiration: str) -> Optional[list]:
        """
        Fetch options chain from Tradier API (single API call).

        Args:
            ticker: Stock symbol
            expiration: Expiration date (YYYY-MM-DD)

        Returns:
            List of option contracts with Greeks, or None if failed
        """
        try:
            url = f"{self.endpoint}/v1/markets/options/chains"
            params = {
                'symbol': ticker,
                'expiration': expiration,
                'greeks': 'true'
            }

            # Use session for connection pooling
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()

            data = response.json()

            if 'options' not in data or 'option' not in data['options']:
                logger.warning(f"{ticker}: No options data in response")
                return None

            return data['options']['option']

        except Exception as e:
            logger.error(f"{ticker}: Failed to fetch options chain: {e}")
            return None

    def _extract_iv_rank(self, options_chain: list, current_price: float, ticker: str) -> Dict:
        """
        Extract current IV from already-fetched options chain.

        Args:
            options_chain: List of option contracts from Tradier
            current_price: Current stock price
            ticker: Stock ticker (for logging)

        Returns:
            Dict with current_iv
        """
        try:
            if not options_chain or not current_price:
                return {'current_iv': 0}

            # Find ATM options to get current IV
            atm_call, atm_put = self._find_atm_options(options_chain, current_price)

            # Extract real implied volatility from ATM options
            current_iv = 0
            if atm_call and 'greeks' in atm_call:
                # Use mid_iv from ATM call (most liquid and representative)
                # Tradier returns IV as decimal (e.g., 1.23 = 123%, 0.50 = 50%)
                current_iv = atm_call['greeks'].get('mid_iv', 0) * 100

            # Validate IV is in reasonable range (1-300%)
            if current_iv > 0 and (current_iv < 1 or current_iv > 300):
                logger.warning(f"{ticker}: Invalid ATM call IV {current_iv:.1f}% from Tradier (expected 1-300%)")

                # Try ATM put as fallback
                if atm_put and 'greeks' in atm_put:
                    put_iv = atm_put['greeks'].get('mid_iv', 0) * 100
                    if 1 <= put_iv <= 300:
                        logger.info(f"{ticker}: Using ATM put IV {put_iv:.1f}% as fallback")
                        current_iv = put_iv
                    else:
                        logger.warning(f"{ticker}: ATM put IV also invalid ({put_iv:.1f}%), skipping IV data")
                        current_iv = 0
                else:
                    logger.warning(f"{ticker}: No valid ATM put fallback available")
                    current_iv = 0

            # Record current IV for history (used for weekly IV change calculation)
            # Weekly IV Change (expansion) is the PRIMARY timing metric for 1-2 day pre-earnings strategy
            if current_iv > 0:
                self.iv_tracker.record_iv(ticker, current_iv)

            return {
                'current_iv': round(current_iv, 2)
            }

        except Exception as e:
            logger.error(f"{ticker}: Failed to extract IV data: {e}")
            return {'current_iv': 0}

    def _extract_liquidity_metrics(self, options_chain: list, current_price: float) -> Dict:
        """
        Extract liquidity metrics from already-fetched options chain.

        Args:
            options_chain: List of option contracts from Tradier
            current_price: Current stock price

        Returns:
            Dict with expected_move_pct, options_volume, open_interest
        """
        try:
            if not options_chain:
                return {'expected_move_pct': 0, 'options_volume': 0, 'open_interest': 0}

            # Calculate expected move from ATM straddle
            expected_move_pct = self._calculate_expected_move(options_chain, current_price)

            # Sum total volume and open interest
            total_volume = sum(opt.get('volume', 0) for opt in options_chain)
            total_oi = sum(opt.get('open_interest', 0) for opt in options_chain)

            return {
                'expected_move_pct': expected_move_pct,
                'options_volume': total_volume,
                'open_interest': total_oi
            }

        except Exception as e:
            logger.error(f"Failed to extract liquidity metrics: {e}")
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

            # Use session for connection pooling
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if 'expirations' in data and 'date' in data['expirations']:
                dates = data['expirations']['date']

                # Defensive check: Validate dates before iteration
                if not dates or not isinstance(dates, (list, tuple)):
                    logger.warning(f"{ticker}: No valid expiration dates returned (got {type(dates).__name__})")
                    return None

                today = get_eastern_now().date()

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
