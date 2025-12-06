#!/usr/bin/env python3
"""
3.0 Earnings Trade Scanner

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
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.tradier import TradierAPI, ImpliedMove
from src.analysis.vrp import VRPCalculator, VRPResult, Recommendation
from src.analysis.ml_predictor import MLMagnitudePredictor, MagnitudePrediction

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Complete scan result for a ticker."""
    ticker: str
    earnings_date: date
    expiration: date

    # VRP Analysis
    implied_move_pct: float
    historical_mean_pct: float
    vrp_ratio: float
    vrp_recommendation: str

    # ML Prediction
    ml_predicted_move_pct: Optional[float]
    ml_confidence: Optional[float]

    # Combined Analysis
    edge_assessment: str
    position_size_multiplier: float


def get_earnings_calendar(db_path: Path, start_date: date, end_date: date) -> List[tuple]:
    """Get upcoming earnings from database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ticker, earnings_date, timing
        FROM earnings_calendar
        WHERE earnings_date BETWEEN ? AND ?
        ORDER BY earnings_date
    """, (start_date.isoformat(), end_date.isoformat()))

    results = cursor.fetchall()
    conn.close()

    return [(row[0], date.fromisoformat(row[1]) if isinstance(row[1], str) else row[1], row[2])
            for row in results]


def find_next_expiration(expirations: List[date], earnings_date: date) -> Optional[date]:
    """Find the first expiration on or after earnings date."""
    for exp in sorted(expirations):
        if exp >= earnings_date:
            return exp
    return None


def log_iv_data(
    db_path: Path,
    ticker: str,
    earnings_date: date,
    expiration: date,
    implied_move: ImpliedMove,
    vrp_result: VRPResult,
    ml_prediction: Optional[MagnitudePrediction]
):
    """Log IV and analysis data for future ML training."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS iv_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            earnings_date DATE NOT NULL,
            expiration DATE NOT NULL,
            scan_date DATE NOT NULL,
            stock_price REAL NOT NULL,
            atm_strike REAL NOT NULL,
            call_mid REAL NOT NULL,
            put_mid REAL NOT NULL,
            straddle_cost REAL NOT NULL,
            implied_move_pct REAL NOT NULL,
            historical_mean_pct REAL NOT NULL,
            historical_median_pct REAL,
            historical_std_pct REAL,
            vrp_ratio REAL NOT NULL,
            edge_score REAL,
            recommendation TEXT NOT NULL,
            quarters_of_data INTEGER,
            ml_predicted_move_pct REAL,
            ml_confidence REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, earnings_date, scan_date)
        )
    """)

    # Insert data
    cursor.execute("""
        INSERT OR REPLACE INTO iv_log (
            ticker, earnings_date, expiration, scan_date,
            stock_price, atm_strike, call_mid, put_mid, straddle_cost,
            implied_move_pct, historical_mean_pct, historical_median_pct,
            historical_std_pct, vrp_ratio, edge_score, recommendation,
            quarters_of_data, ml_predicted_move_pct, ml_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker,
        earnings_date.isoformat(),
        expiration.isoformat(),
        date.today().isoformat(),
        implied_move.stock_price,
        implied_move.atm_strike,
        implied_move.call_mid,
        implied_move.put_mid,
        implied_move.straddle_cost,
        implied_move.implied_move_pct,
        vrp_result.historical_mean_pct,
        vrp_result.historical_median_pct,
        vrp_result.historical_std_pct,
        vrp_result.vrp_ratio,
        vrp_result.edge_score,
        vrp_result.recommendation.value,
        vrp_result.quarters_of_data,
        ml_prediction.predicted_move_pct if ml_prediction else None,
        ml_prediction.prediction_confidence if ml_prediction else None,
    ))

    conn.commit()
    conn.close()
    logger.info(f"Logged IV data for {ticker}")


def calculate_position_multiplier(
    vrp_result: VRPResult,
    ml_prediction: Optional[MagnitudePrediction],
    implied_move_pct: float
) -> float:
    """
    Calculate position size multiplier based on VRP and ML prediction.

    Returns multiplier 0.5 - 2.0 based on confidence.
    """
    base = 1.0

    # VRP adjustment
    if vrp_result.recommendation == Recommendation.EXCELLENT:
        base *= 1.5
    elif vrp_result.recommendation == Recommendation.GOOD:
        base *= 1.2
    elif vrp_result.recommendation == Recommendation.MARGINAL:
        base *= 0.8
    else:
        base *= 0.5

    # ML confidence adjustment
    if ml_prediction:
        # If ML predicts larger move than implied, be more conservative
        ml_vs_implied = ml_prediction.predicted_move_pct / implied_move_pct
        if ml_vs_implied > 1.2:  # ML predicts 20%+ larger move
            base *= 0.8  # Reduce position
        elif ml_vs_implied < 0.8:  # ML predicts smaller move
            base *= 1.1  # Slightly increase position

        # Confidence adjustment
        base *= (0.5 + 0.5 * ml_prediction.prediction_confidence)

    return max(0.5, min(2.0, base))


def assess_edge(vrp_result: VRPResult, ml_prediction: Optional[MagnitudePrediction]) -> str:
    """Generate edge assessment string."""
    if vrp_result.recommendation == Recommendation.SKIP:
        return "NO EDGE - Skip"

    assessment = f"VRP {vrp_result.vrp_ratio:.1f}x ({vrp_result.recommendation.value})"

    if ml_prediction:
        ml_vs_hist = ml_prediction.predicted_move_pct / vrp_result.historical_mean_pct
        if ml_vs_hist > 1.2:
            assessment += f" | ML predicts LARGER move ({ml_prediction.predicted_move_pct:.1f}% vs {vrp_result.historical_mean_pct:.1f}% hist)"
        elif ml_vs_hist < 0.8:
            assessment += f" | ML predicts SMALLER move ({ml_prediction.predicted_move_pct:.1f}% vs {vrp_result.historical_mean_pct:.1f}% hist)"
        else:
            assessment += f" | ML confirms historical ({ml_prediction.predicted_move_pct:.1f}%)"

    return assessment


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


def main():
    parser = argparse.ArgumentParser(description="3.0 Earnings Trade Scanner")
    parser.add_argument("--tickers", type=str, help="Comma-separated tickers to scan")
    parser.add_argument("--days", type=int, default=7, help="Days ahead to scan")
    parser.add_argument("--log-iv", action="store_true", help="Log IV data for ML training")
    parser.add_argument("--min-vrp", type=float, default=1.5, help="Minimum VRP to show")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Paths
    base_dir = Path(__file__).parent.parent
    db_path = base_dir.parent / "2.0" / "data" / "ivcrush.db"

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
        print("\n" + "="*80)
        print("3.0 EARNINGS SCAN RESULTS")
        print("="*80)

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

        print("\n" + "="*80)
        print(f"Total: {len(results)} opportunities")
        if args.log_iv:
            print("IV data logged to database for ML training")
        print("="*80)


if __name__ == "__main__":
    main()
