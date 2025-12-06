"""
Shared scanner logic for 3.0 ML Earnings Scanner.

Contains common data structures and functions used by both
sync (scan.py) and async (scan_async.py) scanners.
"""

import os
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass

from src.utils.db import get_db_connection
from src.api.tradier import ImpliedMove
from src.analysis.vrp import VRPResult, Recommendation
from src.analysis.ml_predictor import MagnitudePrediction

__all__ = [
    'TradeRecommendation',
    'ScanResult',
    'get_earnings_calendar',
    'find_next_expiration',
    'log_iv_data',
    'calculate_position_multiplier',
    'assess_edge',
    'get_default_db_path',
    'generate_trade_recommendation',
]


@dataclass
class TradeRecommendation:
    """Actionable trade recommendation with specific strikes."""
    strategy: str  # 'short_straddle', 'short_strangle', 'iron_condor'
    stock_price: float
    atm_strike: float
    call_strike: Optional[float]  # For strangle/condor
    put_strike: Optional[float]   # For strangle/condor
    straddle_credit: float
    max_profit: float
    max_loss: float  # For defined risk trades
    breakeven_upper: float
    breakeven_lower: float
    suggested_size: int  # Number of contracts based on position sizing


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

    # Actionable Trade Info (optional - populated by enhanced scanner)
    stock_price: Optional[float] = None
    atm_strike: Optional[float] = None
    straddle_credit: Optional[float] = None
    trade_recommendation: Optional[TradeRecommendation] = None

    # ML Prediction Intervals (optional)
    ml_prediction_lower: Optional[float] = None  # 10th percentile
    ml_prediction_upper: Optional[float] = None  # 90th percentile


def get_default_db_path() -> Path:
    """Get default database path from environment or default location."""
    base_dir = Path(__file__).parent.parent.parent
    default_db = base_dir.parent / "2.0" / "data" / "ivcrush.db"
    return Path(os.getenv('DB_PATH', str(default_db)))


def get_earnings_calendar(
    db_path: Path,
    start_date: date,
    end_date: date
) -> List[Tuple[str, date, str]]:
    """
    Get upcoming earnings from database.

    Args:
        db_path: Path to database
        start_date: Start of date range
        end_date: End of date range

    Returns:
        List of (ticker, earnings_date, timing) tuples
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ticker, earnings_date, timing
            FROM earnings_calendar
            WHERE earnings_date BETWEEN ? AND ?
            ORDER BY earnings_date
        """, (start_date.isoformat(), end_date.isoformat()))
        results = cursor.fetchall()

    return [
        (row[0], date.fromisoformat(row[1]) if isinstance(row[1], str) else row[1], row[2])
        for row in results
    ]


def find_next_expiration(expirations: List[date], earnings_date: date) -> Optional[date]:
    """
    Find the first expiration on or after earnings date.

    Args:
        expirations: List of available expiration dates
        earnings_date: Target earnings date

    Returns:
        First expiration >= earnings_date, or None if not found
    """
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
) -> None:
    """
    Log IV and analysis data for future ML training.

    Args:
        db_path: Path to database
        ticker: Stock symbol
        earnings_date: Earnings date
        expiration: Option expiration date
        implied_move: Implied move calculation result
        vrp_result: VRP analysis result
        ml_prediction: ML prediction result (optional)
    """
    with get_db_connection(db_path) as conn:
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


def calculate_position_multiplier(
    vrp_result: VRPResult,
    ml_prediction: Optional[MagnitudePrediction],
    implied_move_pct: float
) -> float:
    """
    Calculate position size multiplier based on VRP and ML prediction.

    Args:
        vrp_result: VRP analysis result
        ml_prediction: ML prediction result (optional)
        implied_move_pct: Implied move percentage

    Returns:
        Multiplier between 0.5 and 2.0 based on confidence
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
    if ml_prediction and implied_move_pct > 0:
        # If ML predicts larger move than implied, be more conservative
        ml_vs_implied = ml_prediction.predicted_move_pct / implied_move_pct
        if ml_vs_implied > 1.2:  # ML predicts 20%+ larger move
            base *= 0.8  # Reduce position
        elif ml_vs_implied < 0.8:  # ML predicts smaller move
            base *= 1.1  # Slightly increase position

        # Confidence adjustment
        base *= (0.5 + 0.5 * ml_prediction.prediction_confidence)

    return max(0.5, min(2.0, base))


def assess_edge(
    vrp_result: VRPResult,
    ml_prediction: Optional[MagnitudePrediction]
) -> str:
    """
    Generate edge assessment string.

    Args:
        vrp_result: VRP analysis result
        ml_prediction: ML prediction result (optional)

    Returns:
        Human-readable edge assessment
    """
    if vrp_result.recommendation == Recommendation.SKIP:
        return "NO EDGE - Skip"

    assessment = f"VRP {vrp_result.vrp_ratio:.1f}x ({vrp_result.recommendation.value})"

    if ml_prediction and vrp_result.historical_mean_pct > 0:
        ml_vs_hist = ml_prediction.predicted_move_pct / vrp_result.historical_mean_pct
        if ml_vs_hist > 1.2:
            assessment += (
                f" | ML predicts LARGER move "
                f"({ml_prediction.predicted_move_pct:.1f}% vs "
                f"{vrp_result.historical_mean_pct:.1f}% hist)"
            )
        elif ml_vs_hist < 0.8:
            assessment += (
                f" | ML predicts SMALLER move "
                f"({ml_prediction.predicted_move_pct:.1f}% vs "
                f"{vrp_result.historical_mean_pct:.1f}% hist)"
            )
        else:
            assessment += f" | ML confirms historical ({ml_prediction.predicted_move_pct:.1f}%)"

    return assessment


def generate_trade_recommendation(
    implied_move: ImpliedMove,
    vrp_result: VRPResult,
    position_multiplier: float,
    base_contracts: int = 1,
    max_risk_per_trade: float = 5000.0,
) -> TradeRecommendation:
    """
    Generate actionable trade recommendation with specific strikes.

    Args:
        implied_move: Implied move calculation with prices
        vrp_result: VRP analysis result
        position_multiplier: Position size multiplier from VRP/ML
        base_contracts: Base number of contracts
        max_risk_per_trade: Maximum risk per trade in dollars

    Returns:
        TradeRecommendation with specific trade parameters
    """
    stock_price = implied_move.stock_price
    atm_strike = implied_move.atm_strike
    straddle_credit = implied_move.straddle_cost

    # Calculate breakevens
    breakeven_upper = atm_strike + straddle_credit
    breakeven_lower = atm_strike - straddle_credit

    # For short straddle, max profit is the credit received
    max_profit = straddle_credit * 100  # Per contract

    # Max loss is theoretically unlimited for straddle, but we estimate based on 2x expected move
    expected_move_dollars = stock_price * (vrp_result.historical_mean_pct / 100)
    estimated_max_loss = (2 * expected_move_dollars - straddle_credit) * 100

    # Calculate suggested size based on risk and position multiplier
    adjusted_contracts = max(1, int(base_contracts * position_multiplier))

    # Cap based on max risk per trade
    if estimated_max_loss > 0:
        max_contracts_by_risk = int(max_risk_per_trade / estimated_max_loss)
        adjusted_contracts = min(adjusted_contracts, max(1, max_contracts_by_risk))

    return TradeRecommendation(
        strategy='short_straddle',
        stock_price=stock_price,
        atm_strike=atm_strike,
        call_strike=atm_strike,  # Same for straddle
        put_strike=atm_strike,   # Same for straddle
        straddle_credit=straddle_credit,
        max_profit=max_profit * adjusted_contracts,
        max_loss=estimated_max_loss * adjusted_contracts,
        breakeven_upper=breakeven_upper,
        breakeven_lower=breakeven_lower,
        suggested_size=adjusted_contracts,
    )
