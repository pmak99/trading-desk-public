"""
Historical Accuracy Tracker for 3.0 System.

Tracks prediction accuracy over time and provides metrics for model evaluation.
Logs predictions and compares to actual outcomes after earnings.
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

from src.utils.db import get_db_connection

logger = logging.getLogger(__name__)

__all__ = [
    'PredictionRecord',
    'AccuracyMetrics',
    'AccuracyTracker',
]


@dataclass
class PredictionRecord:
    """Record of a prediction and its outcome."""
    ticker: str
    earnings_date: date
    prediction_date: date

    # Predictions
    implied_move_pct: float
    historical_mean_pct: float
    ml_predicted_move_pct: Optional[float]
    vrp_ratio: float

    # Actual outcome (filled after earnings)
    actual_move_pct: Optional[float] = None
    direction: Optional[str] = None  # 'up', 'down'

    # Derived metrics
    prediction_error: Optional[float] = None  # |predicted - actual|
    vrp_accuracy: Optional[bool] = None  # Was VRP > 1.5 profitable?


@dataclass
class AccuracyMetrics:
    """Aggregated accuracy metrics."""
    total_predictions: int
    predictions_with_outcome: int
    ml_mae: Optional[float]  # Mean Absolute Error
    ml_rmse: Optional[float]  # Root Mean Squared Error
    vrp_win_rate: Optional[float]  # % of VRP > 1.5 trades that were profitable
    avg_profit_loss: Optional[float]  # Average P/L per trade
    sharpe_ratio: Optional[float]


class AccuracyTracker:
    """
    Track and analyze prediction accuracy over time.

    Creates tables to store predictions and outcomes,
    then calculates accuracy metrics.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Create tracking tables if they don't exist."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()

            # Predictions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prediction_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    earnings_date DATE NOT NULL,
                    prediction_date DATE NOT NULL,
                    implied_move_pct REAL NOT NULL,
                    historical_mean_pct REAL NOT NULL,
                    ml_predicted_move_pct REAL,
                    vrp_ratio REAL NOT NULL,
                    recommendation TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ticker, earnings_date, prediction_date)
                )
            """)

            # Outcomes table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prediction_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    earnings_date DATE NOT NULL,
                    actual_move_pct REAL NOT NULL,
                    direction TEXT NOT NULL,
                    close_before REAL,
                    close_after REAL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ticker, earnings_date)
                )
            """)

            conn.commit()

    def log_prediction(
        self,
        ticker: str,
        earnings_date: date,
        implied_move_pct: float,
        historical_mean_pct: float,
        ml_predicted_move_pct: Optional[float],
        vrp_ratio: float,
        recommendation: str,
    ) -> None:
        """
        Log a prediction for later accuracy tracking.

        Args:
            ticker: Stock symbol
            earnings_date: Earnings announcement date
            implied_move_pct: Implied move from options
            historical_mean_pct: Historical mean move
            ml_predicted_move_pct: ML model prediction
            vrp_ratio: VRP ratio
            recommendation: Trade recommendation
        """
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO prediction_log (
                    ticker, earnings_date, prediction_date,
                    implied_move_pct, historical_mean_pct,
                    ml_predicted_move_pct, vrp_ratio, recommendation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                earnings_date.isoformat(),
                date.today().isoformat(),
                implied_move_pct,
                historical_mean_pct,
                ml_predicted_move_pct,
                vrp_ratio,
                recommendation,
            ))
            conn.commit()
            logger.debug(f"Logged prediction for {ticker} earnings {earnings_date}")

    def log_outcome(
        self,
        ticker: str,
        earnings_date: date,
        actual_move_pct: float,
        direction: str,
        close_before: Optional[float] = None,
        close_after: Optional[float] = None,
    ) -> None:
        """
        Log actual outcome after earnings.

        Args:
            ticker: Stock symbol
            earnings_date: Earnings announcement date
            actual_move_pct: Actual move percentage (absolute)
            direction: 'up' or 'down'
            close_before: Close price before earnings
            close_after: Close price after earnings
        """
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO prediction_outcomes (
                    ticker, earnings_date, actual_move_pct,
                    direction, close_before, close_after
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                earnings_date.isoformat(),
                actual_move_pct,
                direction,
                close_before,
                close_after,
            ))
            conn.commit()
            logger.debug(f"Logged outcome for {ticker}: {actual_move_pct:.1f}% {direction}")

    def get_predictions_with_outcomes(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[PredictionRecord]:
        """
        Get predictions that have outcomes recorded.

        Args:
            start_date: Filter start date
            end_date: Filter end date

        Returns:
            List of PredictionRecord with outcomes
        """
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()

            query = """
                SELECT
                    p.ticker, p.earnings_date, p.prediction_date,
                    p.implied_move_pct, p.historical_mean_pct,
                    p.ml_predicted_move_pct, p.vrp_ratio,
                    o.actual_move_pct, o.direction
                FROM prediction_log p
                JOIN prediction_outcomes o
                    ON p.ticker = o.ticker AND p.earnings_date = o.earnings_date
                WHERE 1=1
            """
            params = []

            if start_date:
                query += " AND p.earnings_date >= ?"
                params.append(start_date.isoformat())
            if end_date:
                query += " AND p.earnings_date <= ?"
                params.append(end_date.isoformat())

            query += " ORDER BY p.earnings_date DESC"

            cursor.execute(query, params)
            rows = cursor.fetchall()

        records = []
        for row in rows:
            rec = PredictionRecord(
                ticker=row[0],
                earnings_date=date.fromisoformat(row[1]) if isinstance(row[1], str) else row[1],
                prediction_date=date.fromisoformat(row[2]) if isinstance(row[2], str) else row[2],
                implied_move_pct=row[3],
                historical_mean_pct=row[4],
                ml_predicted_move_pct=row[5],
                vrp_ratio=row[6],
                actual_move_pct=row[7],
                direction=row[8],
            )

            # Calculate derived metrics
            if rec.ml_predicted_move_pct and rec.actual_move_pct:
                rec.prediction_error = abs(rec.ml_predicted_move_pct - rec.actual_move_pct)

            # VRP accuracy: profitable if implied > actual (we sold overpriced options)
            if rec.actual_move_pct is not None:
                rec.vrp_accuracy = rec.implied_move_pct > rec.actual_move_pct

            records.append(rec)

        return records

    def calculate_metrics(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> AccuracyMetrics:
        """
        Calculate accuracy metrics for predictions with outcomes.

        Args:
            start_date: Filter start date
            end_date: Filter end date

        Returns:
            AccuracyMetrics with aggregated statistics
        """
        records = self.get_predictions_with_outcomes(start_date, end_date)

        if not records:
            return AccuracyMetrics(
                total_predictions=0,
                predictions_with_outcome=0,
                ml_mae=None,
                ml_rmse=None,
                vrp_win_rate=None,
                avg_profit_loss=None,
                sharpe_ratio=None,
            )

        # Count records with ML predictions
        ml_errors = [r.prediction_error for r in records if r.prediction_error is not None]

        ml_mae = None
        ml_rmse = None
        if ml_errors:
            import numpy as np
            ml_mae = np.mean(ml_errors)
            ml_rmse = np.sqrt(np.mean([e**2 for e in ml_errors]))

        # VRP win rate
        vrp_trades = [r for r in records if r.vrp_ratio >= 1.5]
        vrp_wins = [r for r in vrp_trades if r.vrp_accuracy]
        vrp_win_rate = len(vrp_wins) / len(vrp_trades) if vrp_trades else None

        # P/L calculation (simplified: credit received - move beyond breakeven)
        pnls = []
        for r in records:
            if r.actual_move_pct is not None:
                # Simplified P/L: implied premium - |actual move - implied move| if move exceeded
                if r.actual_move_pct <= r.implied_move_pct:
                    # Win: keep full premium
                    pnl = r.implied_move_pct
                else:
                    # Loss: lost the difference
                    pnl = r.implied_move_pct - (r.actual_move_pct - r.implied_move_pct)
                pnls.append(pnl)

        avg_profit_loss = None
        sharpe_ratio = None
        if pnls:
            import numpy as np
            avg_profit_loss = np.mean(pnls)
            if np.std(pnls) > 0:
                sharpe_ratio = (avg_profit_loss / np.std(pnls)) * np.sqrt(52)  # Annualized

        # Get total predictions (including those without outcomes)
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM prediction_log")
            total_predictions = cursor.fetchone()[0]

        return AccuracyMetrics(
            total_predictions=total_predictions,
            predictions_with_outcome=len(records),
            ml_mae=ml_mae,
            ml_rmse=ml_rmse,
            vrp_win_rate=vrp_win_rate,
            avg_profit_loss=avg_profit_loss,
            sharpe_ratio=sharpe_ratio,
        )

    def print_accuracy_report(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> None:
        """Print accuracy report to console."""
        metrics = self.calculate_metrics(start_date, end_date)

        print("\n" + "=" * 60)
        print("PREDICTION ACCURACY REPORT")
        print("=" * 60)

        print(f"\nTotal Predictions: {metrics.total_predictions}")
        print(f"With Outcomes: {metrics.predictions_with_outcome}")

        if metrics.ml_mae is not None:
            print(f"\nML Model Performance:")
            print(f"  MAE: {metrics.ml_mae:.2f}%")
            print(f"  RMSE: {metrics.ml_rmse:.2f}%")

        if metrics.vrp_win_rate is not None:
            print(f"\nVRP Strategy (VRP >= 1.5):")
            print(f"  Win Rate: {metrics.vrp_win_rate:.1%}")

        if metrics.avg_profit_loss is not None:
            print(f"\nP/L Metrics:")
            print(f"  Avg P/L: {metrics.avg_profit_loss:.2f}%")
            if metrics.sharpe_ratio is not None:
                print(f"  Sharpe Ratio: {metrics.sharpe_ratio:.2f}")

        print("\n" + "=" * 60)
