#!/usr/bin/env python3
"""
3.0 Async Earnings Trade Scanner

Parallel scanning of multiple tickers for improved performance.

Combines:
- VRP analysis (2.0 logic) for trade selection
- ML magnitude prediction for position sizing
- IV data logging for future model training

Usage:
    python scripts/scan_async.py [--tickers AAPL,MSFT] [--days 7] [--log-iv] [--workers 10]
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.tradier_async import AsyncTradierAPI, AsyncRetryError
from src.analysis.vrp import VRPCalculator
from src.analysis.ml_predictor import MLMagnitudePredictor
from src.analysis.scanner_core import (
    ScanResult,
    TradeRecommendation,
    get_earnings_calendar,
    find_next_expiration,
    log_iv_data,
    calculate_position_multiplier,
    assess_edge,
    get_default_db_path,
    generate_trade_recommendation,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def scan_ticker_async(
    ticker: str,
    earnings_date: date,
    api: AsyncTradierAPI,
    vrp_calc: VRPCalculator,
    ml_predictor: MLMagnitudePredictor,
    db_path: Path,
    log_iv: bool = False,
) -> Optional[ScanResult]:
    """Scan a single ticker for earnings trade opportunity (async)."""
    try:
        # Get expirations
        expirations = await api.get_expirations(ticker)
        expiration = find_next_expiration(expirations, earnings_date)

        if not expiration:
            logger.warning(f"{ticker}: No expiration found after {earnings_date}")
            return None

        # Calculate implied move
        implied_move = await api.calculate_implied_move(ticker, expiration)

        # Calculate VRP (sync - database operation)
        vrp_result = vrp_calc.calculate(
            ticker=ticker,
            expiration=expiration,
            implied_move_pct=implied_move.implied_move_pct,
        )

        if not vrp_result:
            logger.warning(f"{ticker}: Insufficient historical data for VRP")
            return None

        # ML prediction (sync - model inference)
        ml_prediction = ml_predictor.predict(ticker, earnings_date)

        # Log IV data if requested (sync - database operation)
        if log_iv:
            log_iv_data(db_path, ticker, earnings_date, expiration,
                       implied_move, vrp_result, ml_prediction)
            logger.debug(f"Logged IV data for {ticker}")

        # Calculate position multiplier
        position_mult = calculate_position_multiplier(
            vrp_result, ml_prediction, implied_move.implied_move_pct
        )

        # Assess edge
        edge_assessment = assess_edge(vrp_result, ml_prediction)

        # Generate trade recommendation with risk metrics
        trade_rec = generate_trade_recommendation(
            implied_move=implied_move,
            vrp_result=vrp_result,
            position_multiplier=position_mult,
        )

        return ScanResult(
            ticker=ticker,
            earnings_date=earnings_date,
            expiration=expiration,
            implied_move_pct=implied_move.implied_move_pct,
            historical_mean_pct=vrp_result.historical_mean_pct,
            vrp_ratio=vrp_result.vrp_ratio,
            vrp_recommendation=vrp_result.recommendation.value,
            ml_predicted_move_pct=ml_prediction.predicted_move_pct if ml_prediction else None,
            ml_confidence=ml_prediction.calibrated_confidence if ml_prediction else None,
            edge_assessment=edge_assessment,
            position_size_multiplier=position_mult,
            stock_price=implied_move.stock_price,
            atm_strike=implied_move.atm_strike,
            straddle_credit=implied_move.straddle_cost,
            trade_recommendation=trade_rec,
            ml_prediction_lower=ml_prediction.prediction_lower if ml_prediction else None,
            ml_prediction_upper=ml_prediction.prediction_upper if ml_prediction else None,
        )

    except AsyncRetryError as e:
        logger.error(f"{ticker}: API request failed after retries - {e}")
        return None
    except Exception as e:
        logger.error(f"{ticker}: Scan failed - {e}")
        return None


async def scan_all_tickers(
    tickers_to_scan: List[Tuple[str, date, str]],
    api: AsyncTradierAPI,
    vrp_calc: VRPCalculator,
    ml_predictor: MLMagnitudePredictor,
    db_path: Path,
    log_iv: bool,
    min_vrp: float,
) -> List[ScanResult]:
    """Scan all tickers in parallel."""
    tasks = []
    for ticker, earnings_date, timing in tickers_to_scan:
        if earnings_date is None:
            continue
        tasks.append(
            scan_ticker_async(
                ticker=ticker,
                earnings_date=earnings_date,
                api=api,
                vrp_calc=vrp_calc,
                ml_predictor=ml_predictor,
                db_path=db_path,
                log_iv=log_iv,
            )
        )

    # Execute all scans concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter successful results above min VRP
    valid_results = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Task failed with exception: {result}")
        elif result is not None and result.vrp_ratio >= min_vrp:
            valid_results.append(result)

    # Dedupe by ticker - keep only the best expiration (highest VRP) for each ticker
    ticker_best = {}
    for result in valid_results:
        if result.ticker not in ticker_best or result.vrp_ratio > ticker_best[result.ticker].vrp_ratio:
            ticker_best[result.ticker] = result

    return list(ticker_best.values())


def print_results(results: List[ScanResult], log_iv: bool, show_trades: bool = True) -> None:
    """Print scan results in human-readable format."""
    print("\n" + "=" * 80)
    print("3.0 ASYNC EARNINGS SCAN RESULTS")
    print("=" * 80)

    if not results:
        print("\nNo opportunities found above minimum VRP threshold.")
    else:
        for r in results:
            print(f"\n{'─' * 78}")
            print(f"  {r.ticker} | Earnings: {r.earnings_date} | Exp: {r.expiration}")
            print(f"{'─' * 78}")
            print(f"  Stock: ${r.stock_price:.2f}" if r.stock_price else "")
            print(f"  Implied Move: {r.implied_move_pct:.1f}%  |  Historical Mean: {r.historical_mean_pct:.1f}%")
            print(f"  VRP: {r.vrp_ratio:.1f}x ({r.vrp_recommendation.upper()})")

            if r.ml_predicted_move_pct:
                conf_pct = r.ml_confidence * 100 if r.ml_confidence else 0
                ml_range = ""
                if r.ml_prediction_lower and r.ml_prediction_upper:
                    ml_range = f" [{r.ml_prediction_lower:.1f}%-{r.ml_prediction_upper:.1f}%]"
                print(f"  ML Prediction: {r.ml_predicted_move_pct:.1f}%{ml_range} (conf: {conf_pct:.0f}%)")

            # Actionable trade info
            if show_trades and r.trade_recommendation:
                tr = r.trade_recommendation
                print(f"\n  📊 TRADE: SELL {r.ticker} {r.expiration.strftime('%b%d')} ${tr.atm_strike:.0f} Straddle")
                print(f"     Credit: ${tr.straddle_credit:.2f} ({r.implied_move_pct:.1f}% implied)")
                print(f"     Breakevens: ${tr.breakeven_lower:.2f} - ${tr.breakeven_upper:.2f}")
                print(f"     Max Profit: ${tr.max_profit:.0f}  |  Est. Max Loss: ${tr.max_loss:.0f}")
                print(f"     Suggested Size: {tr.suggested_size} contract(s)")

    print("\n" + "=" * 80)
    print(f"Total: {len(results)} opportunities")
    if log_iv:
        print("IV data logged to database for ML training")
    print("=" * 80)


async def main_async(args):
    """Main async entry point."""
    # Get database path
    db_path = get_default_db_path()

    # Initialize sync components
    try:
        vrp_calc = VRPCalculator(db_path=db_path)
        ml_predictor = MLMagnitudePredictor()
    except Exception as e:
        logger.error(f"Failed to initialize: {e}")
        sys.exit(1)

    # Get tickers to scan
    if args.tickers:
        tickers_to_scan = []
        for t in args.tickers.split(","):
            ticker = t.strip()
            # Try to get earnings date from calendar
            earnings_list = get_earnings_calendar(db_path, date.today(), date.today() + timedelta(days=30))
            matching = [e for e in earnings_list if e[0] == ticker]
            if matching:
                tickers_to_scan.append(matching[0])
            else:
                logger.warning(f"{ticker}: No earnings date found, skipping")
    else:
        # Get from earnings calendar
        start_date = date.today()
        end_date = start_date + timedelta(days=args.days)
        tickers_to_scan = get_earnings_calendar(db_path, start_date, end_date)

    if not tickers_to_scan:
        logger.info("No earnings found in date range")
        return []

    logger.info(f"Scanning {len(tickers_to_scan)} tickers with {args.workers} concurrent workers...")
    start_time = time.time()

    # Scan with async API
    async with AsyncTradierAPI(max_concurrent=args.workers) as api:
        results = await scan_all_tickers(
            tickers_to_scan=tickers_to_scan,
            api=api,
            vrp_calc=vrp_calc,
            ml_predictor=ml_predictor,
            db_path=db_path,
            log_iv=args.log_iv,
            min_vrp=args.min_vrp,
        )

    elapsed = time.time() - start_time
    logger.info(f"Scan completed in {elapsed:.1f}s ({len(tickers_to_scan)/elapsed:.1f} tickers/sec)")

    # Sort by VRP ratio
    results.sort(key=lambda x: x.vrp_ratio, reverse=True)

    return results


def main():
    parser = argparse.ArgumentParser(description="3.0 Async Earnings Trade Scanner")
    parser.add_argument("--tickers", type=str, help="Comma-separated tickers to scan")
    parser.add_argument("--days", type=int, default=7, help="Days ahead to scan")
    parser.add_argument("--log-iv", action="store_true", help="Log IV data for ML training")
    parser.add_argument("--min-vrp", type=float, default=1.5, help="Minimum VRP to show")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent workers")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Run async main
    results = asyncio.run(main_async(args))

    # Output
    if args.json:
        output = [asdict(r) for r in results]
        # Convert dates to strings
        for r in output:
            r['earnings_date'] = r['earnings_date'].isoformat()
            r['expiration'] = r['expiration'].isoformat()
        print(json.dumps(output, indent=2))
    else:
        print_results(results, args.log_iv)


if __name__ == "__main__":
    main()
