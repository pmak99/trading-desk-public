"""
Strategy Backtester - Validate IV crush strategies on historical earnings.

Tests strategies like:
- Iron Condors
- Credit Spreads
- Straddles/Strangles

Calculates:
- Win rate
- Average P&L
- Max drawdown
- Risk-adjusted returns
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)


class EarningsBacktester:
    """Backtest IV crush strategies on historical earnings data."""

    def __init__(self):
        """Initialize backtester."""
        self.results_cache = {}

    def backtest_ticker(self, ticker: str, strategy_type: str = "iron_condor",
                       lookback_years: int = 2) -> Dict:
        """
        Backtest a strategy on historical earnings for a ticker.

        Args:
            ticker: Stock ticker symbol
            strategy_type: Type of strategy ("iron_condor", "credit_spread", "straddle")
            lookback_years: Years of history to test

        Returns:
            Dict with:
            - total_trades: int
            - winners: int
            - losers: int
            - win_rate: float (%)
            - avg_pnl: float ($)
            - total_pnl: float ($)
            - max_drawdown: float ($)
            - trades: List of individual trade results
        """
        logger.info(f"{ticker}: Starting backtest ({strategy_type}, {lookback_years} years)")

        try:
            # Get historical earnings dates
            earnings_dates = self._get_historical_earnings(ticker, lookback_years)

            if not earnings_dates:
                logger.warning(f"{ticker}: No historical earnings found")
                return self._empty_result(ticker)

            logger.info(f"{ticker}: Found {len(earnings_dates)} historical earnings")

            # Backtest each earnings event
            trades = []
            for earnings_date in earnings_dates:
                trade_result = self._backtest_single_earnings(
                    ticker, earnings_date, strategy_type
                )
                if trade_result:
                    trades.append(trade_result)

            if not trades:
                logger.warning(f"{ticker}: No valid backtest trades")
                return self._empty_result(ticker)

            # Calculate aggregate statistics
            winners = [t for t in trades if t['pnl'] > 0]
            losers = [t for t in trades if t['pnl'] <= 0]

            total_pnl = sum(t['pnl'] for t in trades)
            avg_pnl = total_pnl / len(trades) if trades else 0
            win_rate = (len(winners) / len(trades) * 100) if trades else 0

            # Calculate max drawdown
            cumulative_pnl = 0
            peak = 0
            max_dd = 0
            for trade in trades:
                cumulative_pnl += trade['pnl']
                peak = max(peak, cumulative_pnl)
                drawdown = peak - cumulative_pnl
                max_dd = max(max_dd, drawdown)

            result = {
                'ticker': ticker,
                'strategy_type': strategy_type,
                'total_trades': len(trades),
                'winners': len(winners),
                'losers': len(losers),
                'win_rate': round(win_rate, 1),
                'avg_pnl': round(avg_pnl, 2),
                'total_pnl': round(total_pnl, 2),
                'max_drawdown': round(max_dd, 2),
                'avg_winner': round(sum(t['pnl'] for t in winners) / len(winners), 2) if winners else 0,
                'avg_loser': round(sum(t['pnl'] for t in losers) / len(losers), 2) if losers else 0,
                'trades': trades
            }

            logger.info(f"{ticker}: Backtest complete - {result['total_trades']} trades, "
                       f"{result['win_rate']:.1f}% win rate, ${result['total_pnl']:.0f} total P&L")

            return result

        except Exception as e:
            logger.error(f"{ticker}: Backtest failed: {e}")
            return self._empty_result(ticker)

    def _get_historical_earnings(self, ticker: str, years: int) -> List[datetime]:
        """
        Get historical earnings dates for a ticker.

        Args:
            ticker: Stock ticker
            years: Years of history

        Returns:
            List of earnings dates (datetime objects)
        """
        try:
            stock = yf.Ticker(ticker)

            # Get earnings dates from yfinance
            earnings_dates = []

            # Try to get from calendar (if available)
            if hasattr(stock, 'calendar') and stock.calendar is not None:
                # yfinance calendar has upcoming earnings
                pass

            # Get from earnings history
            if hasattr(stock, 'earnings_dates') and stock.earnings_dates is not None:
                try:
                    earnings_df = stock.earnings_dates
                    if not earnings_df.empty:
                        # Filter to past earnings only
                        cutoff_date = datetime.now() - timedelta(days=years*365)

                        for idx in earnings_df.index:
                            if isinstance(idx, pd.Timestamp):
                                earnings_date = idx.to_pydatetime()
                                if cutoff_date <= earnings_date <= datetime.now():
                                    earnings_dates.append(earnings_date)
                except Exception as e:
                    logger.debug(f"{ticker}: Could not parse earnings_dates: {e}")

            # If no earnings dates from API, estimate quarterly from history
            if not earnings_dates:
                logger.debug(f"{ticker}: No API earnings dates, estimating quarterly")
                earnings_dates = self._estimate_quarterly_earnings(ticker, years)

            # Sort by date (oldest first)
            earnings_dates.sort()

            return earnings_dates

        except Exception as e:
            logger.warning(f"{ticker}: Failed to get earnings history: {e}")
            return []

    def _estimate_quarterly_earnings(self, ticker: str, years: int) -> List[datetime]:
        """
        Estimate quarterly earnings dates from price volatility.

        Strategy: Look for large 1-day moves (likely earnings)

        Args:
            ticker: Stock ticker
            years: Years to look back

        Returns:
            List of estimated earnings dates
        """
        try:
            stock = yf.Ticker(ticker)

            # Get historical price data
            end_date = datetime.now()
            start_date = end_date - timedelta(days=years*365)
            hist = stock.history(start=start_date, end=end_date)

            if hist.empty:
                return []

            # Calculate daily returns
            hist['return'] = hist['Close'].pct_change().abs()

            # Find days with >5% moves (likely earnings)
            threshold = 0.05
            large_moves = hist[hist['return'] > threshold]

            # Convert to datetime objects
            earnings_dates = [idx.to_pydatetime() for idx in large_moves.index]

            # Filter to roughly quarterly (remove duplicates within 60 days)
            filtered_dates = []
            for date in earnings_dates:
                if not filtered_dates or (date - filtered_dates[-1]).days > 60:
                    filtered_dates.append(date)

            logger.debug(f"{ticker}: Estimated {len(filtered_dates)} earnings from price moves")
            return filtered_dates

        except Exception as e:
            logger.warning(f"{ticker}: Failed to estimate earnings: {e}")
            return []

    def _backtest_single_earnings(self, ticker: str, earnings_date: datetime,
                                  strategy_type: str) -> Optional[Dict]:
        """
        Backtest a single earnings event.

        Strategy:
        1. Entry: 1 week before earnings
        2. Exit: 1 day after earnings
        3. Calculate P&L based on IV collapse

        Args:
            ticker: Stock ticker
            earnings_date: Date of earnings
            strategy_type: Strategy to test

        Returns:
            Trade result dict or None if data unavailable
        """
        try:
            # Get historical data around earnings
            entry_date = earnings_date - timedelta(days=7)
            exit_date = earnings_date + timedelta(days=1)

            stock = yf.Ticker(ticker)

            # Get price data
            hist = stock.history(start=entry_date - timedelta(days=5),
                               end=exit_date + timedelta(days=5))

            if hist.empty or len(hist) < 2:
                logger.debug(f"{ticker}: Insufficient price data for {earnings_date.date()}")
                return None

            # Get entry and exit prices
            entry_price = self._get_closest_price(hist, entry_date)
            exit_price = self._get_closest_price(hist, exit_date)
            earnings_price = self._get_closest_price(hist, earnings_date)

            if not all([entry_price, exit_price, earnings_price]):
                return None

            # Calculate actual move
            actual_move_pct = abs((earnings_price - entry_price) / entry_price) * 100

            # Simulate strategy P&L
            if strategy_type == "iron_condor":
                pnl = self._calculate_iron_condor_pnl(
                    entry_price, exit_price, actual_move_pct
                )
            elif strategy_type == "credit_spread":
                pnl = self._calculate_credit_spread_pnl(
                    entry_price, exit_price, actual_move_pct
                )
            elif strategy_type == "straddle":
                pnl = self._calculate_straddle_pnl(
                    entry_price, exit_price, actual_move_pct
                )
            else:
                logger.warning(f"Unknown strategy type: {strategy_type}")
                return None

            return {
                'earnings_date': earnings_date.strftime('%Y-%m-%d'),
                'entry_price': round(entry_price, 2),
                'exit_price': round(exit_price, 2),
                'actual_move_pct': round(actual_move_pct, 2),
                'pnl': round(pnl, 2),
                'strategy': strategy_type
            }

        except Exception as e:
            logger.debug(f"{ticker}: Failed to backtest {earnings_date.date()}: {e}")
            return None

    def _get_closest_price(self, hist_df: pd.DataFrame, target_date: datetime) -> Optional[float]:
        """Get the closest price to target date from historical data."""
        try:
            # Find closest date
            dates = hist_df.index
            closest_idx = min(range(len(dates)),
                            key=lambda i: abs((dates[i].to_pydatetime() - target_date).days))

            return float(hist_df.iloc[closest_idx]['Close'])

        except Exception:
            return None

    def _calculate_iron_condor_pnl(self, entry_price: float, exit_price: float,
                                   actual_move_pct: float) -> float:
        """
        Calculate P&L for iron condor strategy.

        Simplified model:
        - Sell condor at ~10% wide wings
        - Credit: ~2% of stock price
        - Max loss: Width of wings - credit
        - Profit if stock stays within wings

        Args:
            entry_price: Stock price at entry
            exit_price: Stock price at exit
            actual_move_pct: Actual move percentage

        Returns:
            P&L in dollars (per share equivalent)
        """
        wing_width_pct = 10  # 10% wings
        credit_pct = 2  # 2% credit received

        credit = entry_price * (credit_pct / 100)
        max_loss = entry_price * (wing_width_pct / 100) - credit

        # If move exceeds wings, lose max
        if actual_move_pct > wing_width_pct:
            return -max_loss
        else:
            # Keep most of credit (assuming IV crush reduces cost)
            return credit * 0.8  # 80% of credit retained after IV crush

    def _calculate_credit_spread_pnl(self, entry_price: float, exit_price: float,
                                     actual_move_pct: float) -> float:
        """
        Calculate P&L for credit spread (put or call spread).

        Simplified model:
        - Sell spread at ~5% wide
        - Credit: ~1.5% of stock price
        - Max loss: Width - credit

        Args:
            entry_price: Stock price at entry
            exit_price: Stock price at exit
            actual_move_pct: Actual move percentage

        Returns:
            P&L in dollars (per share equivalent)
        """
        spread_width_pct = 5
        credit_pct = 1.5

        credit = entry_price * (credit_pct / 100)
        max_loss = entry_price * (spread_width_pct / 100) - credit

        # Assume neutral direction (50/50 puts vs calls)
        # If move > spread width, 50% chance of max loss
        if actual_move_pct > spread_width_pct:
            return -max_loss * 0.5  # 50% chance wrong direction
        else:
            return credit * 0.75

    def _calculate_straddle_pnl(self, entry_price: float, exit_price: float,
                               actual_move_pct: float) -> float:
        """
        Calculate P&L for long straddle (betting on big move).

        Simplified model:
        - Buy ATM call + put
        - Cost: ~4% of stock price
        - Profit if move > cost

        Args:
            entry_price: Stock price at entry
            exit_price: Stock price at exit
            actual_move_pct: Actual move percentage

        Returns:
            P&L in dollars (per share equivalent)
        """
        straddle_cost_pct = 4  # ~4% of stock price

        cost = entry_price * (straddle_cost_pct / 100)

        # Profit = move - cost
        move_value = entry_price * (actual_move_pct / 100)

        return move_value - cost

    def _empty_result(self, ticker: str) -> Dict:
        """Return empty result structure."""
        return {
            'ticker': ticker,
            'strategy_type': 'unknown',
            'total_trades': 0,
            'winners': 0,
            'losers': 0,
            'win_rate': 0.0,
            'avg_pnl': 0.0,
            'total_pnl': 0.0,
            'max_drawdown': 0.0,
            'avg_winner': 0.0,
            'avg_loser': 0.0,
            'trades': []
        }

    def backtest_multiple_tickers(self, tickers: List[str],
                                  strategy_type: str = "iron_condor",
                                  lookback_years: int = 2) -> Dict:
        """
        Backtest multiple tickers and aggregate results.

        Args:
            tickers: List of ticker symbols
            strategy_type: Strategy to test
            lookback_years: Years of history

        Returns:
            Dict with aggregate statistics and per-ticker results
        """
        logger.info(f"Backtesting {len(tickers)} tickers ({strategy_type})")

        results = {}
        all_trades = []

        for ticker in tickers:
            result = self.backtest_ticker(ticker, strategy_type, lookback_years)
            results[ticker] = result
            all_trades.extend(result.get('trades', []))

        # Aggregate statistics
        total_trades = sum(r['total_trades'] for r in results.values())
        total_winners = sum(r['winners'] for r in results.values())
        total_losers = sum(r['losers'] for r in results.values())
        total_pnl = sum(r['total_pnl'] for r in results.values())

        aggregate = {
            'total_tickers': len(tickers),
            'total_trades': total_trades,
            'winners': total_winners,
            'losers': total_losers,
            'win_rate': round((total_winners / total_trades * 100) if total_trades > 0 else 0, 1),
            'total_pnl': round(total_pnl, 2),
            'avg_pnl_per_trade': round(total_pnl / total_trades, 2) if total_trades > 0 else 0,
            'per_ticker_results': results
        }

        logger.info(f"Backtest complete: {total_trades} trades across {len(tickers)} tickers, "
                   f"{aggregate['win_rate']:.1f}% win rate, ${aggregate['total_pnl']:.0f} total P&L")

        return aggregate


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import sys

    logger.info("")
    logger.info('='*70)
    logger.info('EARNINGS STRATEGY BACKTESTER')
    logger.info('='*70)
    logger.info("")

    test_tickers = sys.argv[1:] if len(sys.argv) > 1 else ['AAPL', 'NVDA', 'TSLA']
    strategy = "iron_condor"

    logger.info(f"Backtesting: {', '.join(test_tickers)}")
    logger.info(f"Strategy: {strategy}")
    logger.info(f"Lookback: 2 years")
    logger.info("")

    backtester = EarningsBacktester()
    results = backtester.backtest_multiple_tickers(test_tickers, strategy, lookback_years=2)

    logger.info("")
    logger.info("AGGREGATE RESULTS:")
    logger.info('='*70)
    logger.info(f"Total Trades: {results['total_trades']}")
    logger.info(f"Win Rate: {results['win_rate']:.1f}%")
    logger.info(f"Total P&L: ${results['total_pnl']:.2f}")
    logger.info(f"Avg P&L per Trade: ${results['avg_pnl_per_trade']:.2f}")
    logger.info("")

    logger.info("PER-TICKER RESULTS:")
    logger.info('='*70)
    for ticker, result in results['per_ticker_results'].items():
        logger.info(f"{ticker:6s} - {result['total_trades']:3d} trades, "
                   f"{result['win_rate']:5.1f}% win rate, ${result['total_pnl']:8.2f} P&L")

    logger.info("")
    logger.info('='*70)
