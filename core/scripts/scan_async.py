#!/usr/bin/env python3
"""
Async Ticker Scanner for IV Crush 2.0

High-performance async scanning using aiohttp and asyncio.gather()
for parallel ticker analysis.

Performance:
- Sync mode: ~1.1s per ticker (sequential API calls)
- Async mode: ~0.3s per ticker (parallel I/O)
- ~3-4x speedup over sync, ~1.5-2x over threading

Usage:
    # Scan specific tickers
    python scripts/scan_async.py --tickers LULU,AVGO,COST,ORCL

    # With custom workers (default 10)
    python scripts/scan_async.py --tickers LULU,AVGO,COST,ORCL --workers 15

    # Compare with sync mode
    python scripts/scan_async.py --tickers LULU,AVGO,COST,ORCL --compare
"""

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.api.tradier_async import AsyncTradierAPI, AsyncRetryError
from src.infrastructure.api.yfinance_async import AsyncYFinance
from src.application.metrics.implied_move_common import calculate_from_atm_chain
from src.application.metrics.vrp import VRPCalculator
from src.application.metrics.skew_enhanced import SkewAnalyzerEnhanced
from src.domain.types import OptionChain, Money, Strike, OptionQuote, Percentage
from src.domain.enums import EarningsTiming, Recommendation
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.config.config import Config

# Configure logging
logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# VRP thresholds from config
DEFAULT_VRP_EXCELLENT = 1.8
DEFAULT_VRP_GOOD = 1.4
DEFAULT_VRP_MARGINAL = 1.2

# Liquidity tier thresholds (matching scan.py)
LIQUIDITY_REJECT_MIN_OI = 20
LIQUIDITY_REJECT_MAX_SPREAD = 100.0
LIQUIDITY_WARNING_MIN_OI = 200
LIQUIDITY_WARNING_MAX_SPREAD = 15.0
LIQUIDITY_EXCELLENT_MIN_OI = 1000
LIQUIDITY_EXCELLENT_MAX_SPREAD = 8.0
LIQUIDITY_EXCELLENT_MIN_VOL = 100


# ============================================================================
# Liquidity Analysis
# ============================================================================

def classify_liquidity_tier(call: OptionQuote, put: OptionQuote) -> str:
    """
    Classify liquidity tier based on straddle metrics.

    Returns 'EXCELLENT', 'WARNING', or 'REJECT'
    """
    # Get metrics
    combined_oi = call.open_interest + put.open_interest
    combined_vol = call.volume + put.volume
    max_spread = max(call.spread_pct, put.spread_pct)

    # REJECT tier
    if combined_oi < LIQUIDITY_REJECT_MIN_OI:
        return 'REJECT'
    if max_spread > LIQUIDITY_REJECT_MAX_SPREAD:
        return 'REJECT'

    # EXCELLENT tier (all conditions must be met)
    if (combined_oi >= LIQUIDITY_EXCELLENT_MIN_OI and
        max_spread <= LIQUIDITY_EXCELLENT_MAX_SPREAD and
        combined_vol >= LIQUIDITY_EXCELLENT_MIN_VOL):
        return 'EXCELLENT'

    # WARNING tier (in between)
    if (combined_oi >= LIQUIDITY_WARNING_MIN_OI and
        max_spread <= LIQUIDITY_WARNING_MAX_SPREAD):
        return 'WARNING'

    return 'REJECT'


# ============================================================================
# Database Operations
# ============================================================================

def get_historical_moves(db_path: Path, ticker: str, limit: int = 12) -> List:
    """
    Fetch historical moves from database.

    Returns list of HistoricalMove objects.
    """
    from src.domain.types import HistoricalMove

    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT earnings_date, prev_close, earnings_open, earnings_high,
                       earnings_low, earnings_close, close_move_pct,
                       intraday_move_pct, gap_move_pct
                FROM historical_moves
                WHERE ticker = ?
                ORDER BY earnings_date DESC
                LIMIT ?
                ''',
                (ticker, limit)
            )

            moves = []
            for row in cursor.fetchall():
                moves.append(HistoricalMove(
                    ticker=ticker,
                    earnings_date=date.fromisoformat(row[0]),
                    prev_close=Money(float(row[1])) if row[1] else Money(0),
                    earnings_open=Money(float(row[2])) if row[2] else Money(0),
                    earnings_high=Money(float(row[3])) if row[3] else Money(0),
                    earnings_low=Money(float(row[4])) if row[4] else Money(0),
                    earnings_close=Money(float(row[5])) if row[5] else Money(0),
                    close_move_pct=Percentage(float(row[6])) if row[6] else Percentage(0),
                    intraday_move_pct=Percentage(float(row[7])) if row[7] else Percentage(0),
                    gap_move_pct=Percentage(float(row[8])) if row[8] else Percentage(0),
                ))

            return moves

    except Exception as e:
        logger.warning(f"{ticker}: Failed to fetch historical moves: {e}")
        return []


def get_earnings_for_ticker(db_path: Path, ticker: str) -> Optional[Tuple[date, EarningsTiming]]:
    """
    Get next earnings date for ticker from database.
    """
    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT earnings_date, timing
                FROM earnings_calendar
                WHERE ticker = ? AND earnings_date >= date('now')
                ORDER BY earnings_date ASC
                LIMIT 1
                ''',
                (ticker,)
            )
            row = cursor.fetchone()
            if row:
                return (date.fromisoformat(row[0]), EarningsTiming(row[1]))
        return None
    except Exception as e:
        logger.warning(f"{ticker}: Failed to fetch earnings date: {e}")
        return None


# ============================================================================
# Expiration Calculation
# ============================================================================

def calculate_expiration_date(earnings_date: date, timing: EarningsTiming) -> date:
    """Calculate expiration date based on earnings timing."""
    weekday = earnings_date.weekday()

    # Thursday or Friday earnings -> next week Friday
    if weekday in [3, 4]:
        if weekday == 3:  # Thursday
            return earnings_date + timedelta(days=8)
        else:  # Friday
            return earnings_date + timedelta(days=7)

    # Mon/Tue/Wed -> Friday of same week
    days_until_friday = (4 - weekday) % 7
    if days_until_friday == 0:
        days_until_friday = 7
    return earnings_date + timedelta(days=days_until_friday)


# ============================================================================
# Async Ticker Analysis
# ============================================================================

async def analyze_ticker_async(
    ticker: str,
    earnings_date: date,
    expiration_date: date,
    tradier_api: AsyncTradierAPI,
    yf_api: AsyncYFinance,
    db_path: Path,
    vrp_calc: VRPCalculator,
) -> Optional[Dict]:
    """
    Analyze a single ticker asynchronously.

    Parallelizes all independent API calls for maximum performance.
    """
    try:
        logger.debug(f"{ticker}: Starting async analysis")
        start_time = time.perf_counter()

        # Find actual expiration (may be adjusted)
        exp_result = await tradier_api.find_nearest_expiration(ticker, expiration_date)
        if exp_result.is_err:
            logger.warning(f"{ticker}: No expiration found: {exp_result.error}")
            return None

        actual_expiration = exp_result.value

        # Parallel fetch: option chain AND ticker info
        chain_task = tradier_api.get_option_chain(ticker, actual_expiration)
        info_task = yf_api.get_ticker_info(ticker)

        chain_result, ticker_info = await asyncio.gather(chain_task, info_task)

        if chain_result.is_err:
            logger.warning(f"{ticker}: No option chain: {chain_result.error}")
            return None

        chain = chain_result.value
        market_cap_millions, company_name = ticker_info

        # Calculate implied move from chain
        im_result = calculate_from_atm_chain(
            chain, ticker, actual_expiration, validate_straddle_cost=True
        )
        if im_result.is_err:
            logger.warning(f"{ticker}: Implied move calculation failed: {im_result.error}")
            return None

        implied_move = im_result.value

        # Get historical moves (sync - database is fast)
        historical_moves = get_historical_moves(db_path, ticker, limit=12)
        if len(historical_moves) < 4:
            logger.warning(f"{ticker}: Insufficient historical data ({len(historical_moves)} quarters)")
            return {
                'ticker': ticker,
                'ticker_name': company_name,
                'status': 'NO_HISTORICAL_DATA',
                'tradeable': False,
            }

        # Calculate VRP
        vrp_result = vrp_calc.calculate(
            ticker=ticker,
            expiration=actual_expiration,
            implied_move=implied_move,
            historical_moves=historical_moves,
        )

        if vrp_result.is_err:
            logger.warning(f"{ticker}: VRP calculation failed: {vrp_result.error}")
            return None

        vrp = vrp_result.value

        # Check liquidity tier
        atm_strike = chain.atm_strike()
        atm_call = chain.calls.get(atm_strike)
        atm_put = chain.puts.get(atm_strike)

        liquidity_tier = 'UNKNOWN'
        if atm_call and atm_put:
            liquidity_tier = classify_liquidity_tier(atm_call, atm_put)

        # Get directional bias (would need skew analyzer - simplified here)
        directional_bias = 'NEUTRAL'

        elapsed = time.perf_counter() - start_time
        logger.debug(f"{ticker}: Analysis complete in {elapsed:.2f}s")

        return {
            'ticker': ticker,
            'ticker_name': company_name,
            'earnings_date': str(earnings_date),
            'expiration_date': str(actual_expiration),
            'stock_price': float(implied_move.stock_price.amount),
            'implied_move_pct': str(vrp.implied_move_pct),
            'historical_mean_pct': str(vrp.historical_mean_move_pct),
            'vrp_ratio': float(vrp.vrp_ratio),
            'edge_score': float(vrp.edge_score),
            'recommendation': vrp.recommendation.value,
            'is_tradeable': vrp.is_tradeable,
            'liquidity_tier': liquidity_tier,
            'directional_bias': directional_bias,
            'status': 'SUCCESS',
            'analysis_time_ms': elapsed * 1000,
        }

    except AsyncRetryError as e:
        logger.error(f"{ticker}: API retry limit exceeded: {e}")
        return None
    except Exception as e:
        logger.error(f"{ticker}: Analysis error: {e}", exc_info=True)
        return None


async def scan_tickers_async(
    tickers: List[str],
    db_path: Path,
    api_key: str,
    max_workers: int = 10,
) -> Tuple[List[Dict], float]:
    """
    Scan multiple tickers in parallel using async.

    Returns:
        Tuple of (results list, total time in seconds)
    """
    start_time = time.perf_counter()

    # Initialize VRP calculator
    vrp_calc = VRPCalculator()

    results = []

    async with AsyncTradierAPI(api_key, max_concurrent=max_workers) as tradier_api:
        async with AsyncYFinance(max_workers=min(5, max_workers)) as yf_api:

            # Build list of analysis tasks
            tasks = []
            ticker_earnings_map = {}

            for ticker in tickers:
                earnings_info = get_earnings_for_ticker(db_path, ticker)
                if not earnings_info:
                    logger.info(f"{ticker}: No upcoming earnings")
                    continue

                earnings_date, timing = earnings_info
                expiration_date = calculate_expiration_date(earnings_date, timing)
                ticker_earnings_map[ticker] = (earnings_date, expiration_date)

                tasks.append(
                    analyze_ticker_async(
                        ticker=ticker,
                        earnings_date=earnings_date,
                        expiration_date=expiration_date,
                        tradier_api=tradier_api,
                        yf_api=yf_api,
                        db_path=db_path,
                        vrp_calc=vrp_calc,
                    )
                )

            if not tasks:
                return [], time.perf_counter() - start_time

            # Execute all analyses in parallel
            logger.info(f"Scanning {len(tasks)} tickers with {max_workers} workers...")
            analysis_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Collect results
            for result in analysis_results:
                if isinstance(result, Exception):
                    logger.error(f"Task failed: {result}")
                elif result is not None:
                    results.append(result)

    total_time = time.perf_counter() - start_time
    return results, total_time


# ============================================================================
# Display Results
# ============================================================================

def display_results(results: List[Dict], total_time: float, mode: str) -> None:
    """Display scan results in formatted output."""
    print("\n" + "=" * 80)
    print(f"{mode} SCAN RESULTS")
    print("=" * 80)

    tradeable = [r for r in results if r.get('is_tradeable', False)]
    success_count = len([r for r in results if r.get('status') == 'SUCCESS'])
    skip_count = len([r for r in results if r.get('status') == 'NO_HISTORICAL_DATA'])

    print(f"\n📊 Analysis Results:")
    print(f"   ✓ Successfully Analyzed: {success_count}")
    print(f"   ⏭️  Skipped (No Data): {skip_count}")
    print(f"   ⏱️  Total Time: {total_time:.2f}s")
    if results:
        print(f"   📈 Avg Time/Ticker: {total_time/len(results)*1000:.0f}ms")

    if tradeable:
        print(f"\n" + "=" * 80)
        print(f"✅ {len(tradeable)} TRADEABLE OPPORTUNITIES")
        print("=" * 80)

        # Sort by VRP ratio
        tradeable.sort(key=lambda x: x['vrp_ratio'], reverse=True)

        print(f"\n   {'#':<3} {'Ticker':<8} {'Name':<20} {'VRP':<8} {'Implied':<10} {'Edge':<7} {'Rec':<15} {'Liq':<10}")
        print(f"   {'-'*3} {'-'*8} {'-'*20} {'-'*8} {'-'*10} {'-'*7} {'-'*15} {'-'*10}")

        for i, r in enumerate(tradeable, 1):
            ticker = r['ticker']
            name = (r.get('ticker_name') or '')[:20]
            vrp = f"{r['vrp_ratio']:.2f}x"
            implied = r['implied_move_pct']
            edge = f"{r['edge_score']:.2f}"
            rec = r['recommendation'].upper()
            liq = r.get('liquidity_tier', 'UNKNOWN')

            liq_icon = "✓" if liq == "EXCELLENT" else ("⚠️ " if liq == "WARNING" else "❌")
            print(f"   {i:<3} {ticker:<8} {name:<20} {vrp:<8} {implied:<10} {edge:<7} {rec:<15} {liq_icon}{liq:<8}")

    else:
        print(f"\n⏭️  No tradeable opportunities found")

    print("\n" + "=" * 80)


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Async Ticker Scanner for IV Crush 2.0")
    parser.add_argument("--tickers", type=str, required=True, help="Comma-separated tickers")
    parser.add_argument("--workers", type=int, default=10, help="Max concurrent workers (default: 10)")
    parser.add_argument("--compare", action="store_true", help="Compare with sync mode")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",")]

    # Get config
    config = Config.from_env()
    api_key = config.api.tradier_api_key
    db_path = config.database.path

    if not api_key:
        print("Error: TRADIER_API_KEY not set")
        sys.exit(1)

    print("=" * 80)
    print("ASYNC TICKER SCANNER")
    print("=" * 80)
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Workers: {args.workers}")
    print()

    # Run async scan
    results, total_time = asyncio.run(
        scan_tickers_async(tickers, db_path, api_key, args.workers)
    )

    display_results(results, total_time, "ASYNC")

    # Compare with sync if requested
    if args.compare:
        print("\n" + "=" * 80)
        print("COMPARISON: Running sync mode...")
        print("=" * 80)

        from scripts.scan import ticker_mode
        from src.container import Container

        container = Container(config)

        sync_start = time.perf_counter()
        ticker_mode(container, tickers, parallel=False)
        sync_time = time.perf_counter() - sync_start

        print(f"\n📊 COMPARISON:")
        print(f"   Async: {total_time:.2f}s ({total_time/len(tickers)*1000:.0f}ms/ticker)")
        print(f"   Sync:  {sync_time:.2f}s ({sync_time/len(tickers)*1000:.0f}ms/ticker)")
        print(f"   Speedup: {sync_time/total_time:.1f}x")


if __name__ == "__main__":
    main()
