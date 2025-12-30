#!/usr/bin/env python3
"""
3.0 Earnings Trade Scanner (Sync Version)

Combines:
- VRP analysis (2.0 logic) for trade selection
- ML magnitude prediction for position sizing
- IV data logging for future model training

Usage:
    python scripts/scan.py [--tickers AAPL,MSFT] [--days 7] [--log-iv]
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.tradier import TradierAPI
from src.analysis.vrp import VRPCalculator
from src.analysis.ml_predictor import MLMagnitudePredictor
from src.analysis.scanner_core import (
    ScanResult,
    get_earnings_calendar,
    find_next_expiration,
    log_iv_data,
    calculate_position_multiplier,
    assess_edge,
    get_default_db_path,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def scan_ticker(
    ticker: str,
    earnings_date: date,
    api: TradierAPI,
    vrp_calc: VRPCalculator,
    ml_predictor: MLMagnitudePredictor,
    db_path: Path,
    log_iv: bool = False,
) -> Optional[ScanResult]:
    """Scan a single ticker for earnings trade opportunity."""
    try:
        # Get expirations
        expirations = api.get_expirations(ticker)
        expiration = find_next_expiration(expirations, earnings_date)

        if not expiration:
            logger.warning(f"{ticker}: No expiration found after {earnings_date}")
            return None

        # Calculate implied move
        implied_move = api.calculate_implied_move(ticker, expiration)

        # Calculate VRP
        vrp_result = vrp_calc.calculate(
            ticker=ticker,
            expiration=expiration,
            implied_move_pct=implied_move.implied_move_pct,
        )

        if not vrp_result:
            logger.warning(f"{ticker}: Insufficient historical data for VRP")
            return None

        # ML prediction
        ml_prediction = ml_predictor.predict(ticker, earnings_date)

        # Log IV data if requested
        if log_iv:
            log_iv_data(db_path, ticker, earnings_date, expiration,
                       implied_move, vrp_result, ml_prediction)
            logger.info(f"Logged IV data for {ticker}")

        # Calculate position multiplier
        position_mult = calculate_position_multiplier(
            vrp_result, ml_prediction, implied_move.implied_move_pct
        )

        # Assess edge
        edge_assessment = assess_edge(vrp_result, ml_prediction)

        return ScanResult(
            ticker=ticker,
            earnings_date=earnings_date,
            expiration=expiration,
            implied_move_pct=implied_move.implied_move_pct,
            historical_mean_pct=vrp_result.historical_mean_pct,
            vrp_ratio=vrp_result.vrp_ratio,
            vrp_recommendation=vrp_result.recommendation.value,
            ml_predicted_move_pct=ml_prediction.predicted_move_pct if ml_prediction else None,
            ml_confidence=ml_prediction.prediction_confidence if ml_prediction else None,
            edge_assessment=edge_assessment,
            position_size_multiplier=position_mult,
        )

    except Exception as e:
        logger.error(f"{ticker}: Scan failed - {e}")
        return None


def print_results(results: List[ScanResult], log_iv: bool) -> None:
    """Print scan results in human-readable format."""
    print("\n" + "=" * 80)
    print("3.0 EARNINGS SCAN RESULTS")
    print("=" * 80)

    if not results:
        print("\nNo opportunities found above minimum VRP threshold.")
    else:
        for r in results:
            print(f"\n{r.ticker} | Earnings: {r.earnings_date} | Exp: {r.expiration}")
            print(f"  Implied Move: {r.implied_move_pct:.1f}%")
            print(f"  Historical Mean: {r.historical_mean_pct:.1f}%")
            print(f"  VRP: {r.vrp_ratio:.1f}x ({r.vrp_recommendation.upper()})")
            if r.ml_predicted_move_pct:
                print(f"  ML Prediction: {r.ml_predicted_move_pct:.1f}% (conf: {r.ml_confidence:.0%})")
            print(f"  Edge: {r.edge_assessment}")
            print(f"  Position Multiplier: {r.position_size_multiplier:.2f}x")

    print("\n" + "=" * 80)
    print(f"Total: {len(results)} opportunities")
    if log_iv:
        print("IV data logged to database for ML training")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="3.0 Earnings Trade Scanner")
    parser.add_argument("--tickers", type=str, help="Comma-separated tickers to scan")
    parser.add_argument("--days", type=int, default=7, help="Days ahead to scan")
    parser.add_argument("--log-iv", action="store_true", help="Log IV data for ML training")
    parser.add_argument("--min-vrp", type=float, default=1.5, help="Minimum VRP to show")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Get database path
    db_path = get_default_db_path()

    # Initialize components
    try:
        api = TradierAPI()
        vrp_calc = VRPCalculator(db_path=db_path)
        ml_predictor = MLMagnitudePredictor()
    except Exception as e:
        logger.error(f"Failed to initialize: {e}")
        sys.exit(1)

    # Get tickers to scan
    if args.tickers:
        tickers_to_scan = [(t.strip(), None, None) for t in args.tickers.split(",")]
    else:
        # Get from earnings calendar
        start_date = date.today()
        end_date = start_date + timedelta(days=args.days)
        tickers_to_scan = get_earnings_calendar(db_path, start_date, end_date)

    if not tickers_to_scan:
        logger.info("No earnings found in date range")
        return

    logger.info(f"Scanning {len(tickers_to_scan)} tickers...")

    # Scan each ticker
    results = []
    for ticker, earnings_date, timing in tickers_to_scan:
        if earnings_date is None:
            # If no earnings date provided, try to get from calendar
            earnings_list = get_earnings_calendar(db_path, date.today(), date.today() + timedelta(days=30))
            matching = [e for e in earnings_list if e[0] == ticker]
            if matching:
                earnings_date = matching[0][1]
            else:
                logger.warning(f"{ticker}: No earnings date found")
                continue

        result = scan_ticker(
            ticker=ticker,
            earnings_date=earnings_date,
            api=api,
            vrp_calc=vrp_calc,
            ml_predictor=ml_predictor,
            db_path=db_path,
            log_iv=args.log_iv,
        )

        if result and result.vrp_ratio >= args.min_vrp:
            results.append(result)

    # Sort by VRP ratio
    results.sort(key=lambda x: x.vrp_ratio, reverse=True)

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
