"""
VRP (Volatility Risk Premium) Calculator for 3.0.
Simplified version borrowed from 2.0.

VRP = Implied Move / Historical Mean Move

High VRP = market expects more movement than history suggests = edge for sellers
"""

import os
import numpy as np
from datetime import date
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass
from enum import Enum

from src.utils.db import get_db_connection

__all__ = [
    'Recommendation',
    'HistoricalMove',
    'VRPResult',
    'VRPCalculator',
]


class Recommendation(Enum):
    EXCELLENT = "excellent"  # VRP >= 7.0x (historical thresholds)
    GOOD = "good"            # VRP >= 4.0x
    MARGINAL = "marginal"    # VRP >= 1.5x
    SKIP = "skip"            # VRP < 1.5x


@dataclass
class HistoricalMove:
    """Single historical earnings move."""
    earnings_date: date
    prev_close: float
    earnings_close: float
    close_move_pct: float  # Absolute value
    gap_move_pct: float
    intraday_move_pct: float


@dataclass
class VRPResult:
    """VRP calculation result."""
    ticker: str
    expiration: date
    implied_move_pct: float
    historical_mean_pct: float
    historical_median_pct: float
    historical_std_pct: float
    vrp_ratio: float
    edge_score: float
    recommendation: Recommendation
    quarters_of_data: int


class VRPCalculator:
    """
    Calculate VRP (Volatility Risk Premium).

    Uses historical moves from ivcrush.db and compares to implied move.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        threshold_excellent: float = 7.0,
        threshold_good: float = 4.0,
        threshold_marginal: float = 1.5,
        min_quarters: int = 4,
        move_metric: str = "close",  # "close", "intraday", or "gap"
    ):
        default_db = Path(__file__).parent.parent.parent.parent / "2.0" / "data" / "ivcrush.db"
        self.db_path = db_path or Path(os.getenv('DB_PATH', str(default_db)))
        self.thresholds = {
            'excellent': threshold_excellent,
            'good': threshold_good,
            'marginal': threshold_marginal,
        }
        self.min_quarters = min_quarters
        self.move_metric = move_metric

    def get_historical_moves(self, ticker: str, limit: int = 12) -> List[HistoricalMove]:
        """Fetch historical earnings moves from database."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT earnings_date, prev_close, earnings_close,
                       close_move_pct, gap_move_pct, intraday_move_pct
                FROM historical_moves
                WHERE ticker = ?
                ORDER BY earnings_date DESC
                LIMIT ?
            """, (ticker, limit))

            moves = []
            for row in cursor.fetchall():
                moves.append(HistoricalMove(
                    earnings_date=date.fromisoformat(row[0]) if isinstance(row[0], str) else row[0],
                    prev_close=row[1],
                    earnings_close=row[2],
                    close_move_pct=abs(row[3]),  # Ensure absolute
                    gap_move_pct=abs(row[4]) if row[4] else 0,
                    intraday_move_pct=abs(row[5]) if row[5] else 0,
                ))

        return moves

    def calculate(
        self,
        ticker: str,
        expiration: date,
        implied_move_pct: float,
    ) -> Optional[VRPResult]:
        """
        Calculate VRP for a ticker.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date
            implied_move_pct: Implied move from ATM straddle

        Returns:
            VRPResult or None if insufficient data
        """
        # Get historical moves
        moves = self.get_historical_moves(ticker, limit=12)

        if len(moves) < self.min_quarters:
            return None

        # Extract move percentages based on metric
        if self.move_metric == "close":
            pcts = [m.close_move_pct for m in moves]
        elif self.move_metric == "intraday":
            pcts = [m.intraday_move_pct for m in moves]
        elif self.move_metric == "gap":
            pcts = [m.gap_move_pct for m in moves]
        else:
            pcts = [m.close_move_pct for m in moves]

        # Calculate statistics
        mean_pct = np.mean(pcts)
        median_pct = np.median(pcts)
        std_pct = np.std(pcts)

        if mean_pct <= 0:
            return None

        # VRP ratio
        vrp_ratio = implied_move_pct / mean_pct

        # Edge score (penalize high variance)
        mad = np.median(np.abs(np.array(pcts) - median_pct))
        consistency = mad / median_pct if median_pct > 0 else 1
        edge_score = vrp_ratio / (1 + consistency)

        # Recommendation
        if vrp_ratio >= self.thresholds['excellent']:
            recommendation = Recommendation.EXCELLENT
        elif vrp_ratio >= self.thresholds['good']:
            recommendation = Recommendation.GOOD
        elif vrp_ratio >= self.thresholds['marginal']:
            recommendation = Recommendation.MARGINAL
        else:
            recommendation = Recommendation.SKIP

        return VRPResult(
            ticker=ticker,
            expiration=expiration,
            implied_move_pct=implied_move_pct,
            historical_mean_pct=mean_pct,
            historical_median_pct=median_pct,
            historical_std_pct=std_pct,
            vrp_ratio=vrp_ratio,
            edge_score=edge_score,
            recommendation=recommendation,
            quarters_of_data=len(moves),
        )
