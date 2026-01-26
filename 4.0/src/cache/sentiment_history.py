"""
Sentiment History for Backtesting and Value-Add Analysis

Permanent storage of pre-earnings sentiment with post-earnings outcomes.
Unlike sentiment_cache (3-hour TTL), this data never expires.

Purpose:
- Collect sentiment BEFORE earnings
- Record actual outcomes AFTER earnings
- Enable correlation analysis to validate AI sentiment value

Schema:
    sentiment_history (
        ticker, earnings_date,      -- Primary key
        collected_at, source,       -- When/how collected
        sentiment_text,             -- Raw sentiment analysis
        sentiment_score,            -- Normalized -1 to +1
        vrp_ratio, implied_move,    -- VRP context at collection time
        actual_move_pct,            -- Filled post-earnings
        actual_direction,           -- UP/DOWN
        prediction_correct,         -- Did sentiment predict direction?
        trade_outcome               -- WIN/LOSS if we traded
    )
"""

import re
import sqlite3
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

_db_lock = threading.Lock()


class SentimentDirection(Enum):
    """Sentiment direction classification."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


@dataclass
class SentimentRecord:
    """A single sentiment history record."""
    ticker: str
    earnings_date: str
    collected_at: datetime
    source: str
    sentiment_text: str
    sentiment_score: Optional[float]  # -1 (bearish) to +1 (bullish)
    sentiment_direction: SentimentDirection
    vrp_ratio: Optional[float]
    implied_move_pct: Optional[float]
    # Post-earnings fields (None until filled)
    actual_move_pct: Optional[float] = None
    actual_direction: Optional[str] = None  # UP/DOWN
    prediction_correct: Optional[bool] = None
    trade_outcome: Optional[str] = None  # WIN/LOSS/SKIP

    @property
    def has_outcome(self) -> bool:
        """Check if post-earnings outcome has been recorded."""
        return self.actual_move_pct is not None


class SentimentHistory:
    """
    Permanent sentiment storage for backtesting.

    Usage:
        history = SentimentHistory()

        # Before earnings - collect sentiment
        history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Analysts bullish on AI growth...",
            sentiment_score=0.7,
            vrp_ratio=8.2,
            implied_move_pct=12.5
        )

        # After earnings - record outcome
        history.record_outcome(
            ticker="NVDA",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="UP",
            trade_outcome="WIN"
        )

        # Analyze
        stats = history.get_accuracy_stats()
        print(f"Sentiment accuracy: {stats['accuracy']:.1%}")
    """

    VALID_SOURCES = {"perplexity", "websearch", "finnhub", "manual"}
    VALID_DIRECTIONS = {"UP", "DOWN"}  # Valid actual_direction values
    VALID_TRADE_OUTCOMES = {"WIN", "LOSS", "SKIP"}  # Valid trade_outcome values

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize history with optional custom database path."""
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "sentiment_cache.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sentiment_history (
                        ticker TEXT NOT NULL,
                        earnings_date TEXT NOT NULL,
                        collected_at TEXT NOT NULL,
                        source TEXT NOT NULL,
                        sentiment_text TEXT NOT NULL,
                        sentiment_score REAL,
                        sentiment_direction TEXT,
                        vrp_ratio REAL,
                        implied_move_pct REAL,
                        actual_move_pct REAL,
                        actual_direction TEXT,
                        prediction_correct INTEGER,
                        trade_outcome TEXT,
                        updated_at TEXT,
                        PRIMARY KEY (ticker, earnings_date)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_history_date
                    ON sentiment_history(earnings_date)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_history_outcome
                    ON sentiment_history(trade_outcome)
                """)
                conn.commit()

    def record_sentiment(
        self,
        ticker: str,
        earnings_date: str,
        source: str,
        sentiment_text: str,
        sentiment_score: Optional[float] = None,
        sentiment_direction: Optional[SentimentDirection] = None,
        vrp_ratio: Optional[float] = None,
        implied_move_pct: Optional[float] = None
    ) -> None:
        """
        Record pre-earnings sentiment.

        Args:
            ticker: Stock ticker (will be uppercased)
            earnings_date: Date string (YYYY-MM-DD format)
            source: "perplexity", "websearch", "finnhub", or "manual"
            sentiment_text: The sentiment analysis text
            sentiment_score: Optional normalized score (-1 to +1)
            sentiment_direction: Optional direction classification
            vrp_ratio: VRP ratio at time of collection
            implied_move_pct: Implied move at time of collection

        Raises:
            ValueError: If source is invalid
        """
        if source not in self.VALID_SOURCES:
            raise ValueError(f"Invalid source '{source}'. Must be one of: {self.VALID_SOURCES}")

        ticker = ticker.upper()
        if not ticker or not re.match(r'^[A-Z]{1,5}$', ticker):
            raise ValueError(f"Invalid ticker format: {ticker}")
        now = datetime.now(timezone.utc).isoformat()

        # Auto-detect direction from score if not provided
        if sentiment_direction is None and sentiment_score is not None:
            if sentiment_score >= 0.2:
                sentiment_direction = SentimentDirection.BULLISH
            elif sentiment_score < -0.2:
                sentiment_direction = SentimentDirection.BEARISH
            else:
                sentiment_direction = SentimentDirection.NEUTRAL

        direction_str = sentiment_direction.value if sentiment_direction else SentimentDirection.UNKNOWN.value

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sentiment_history
                    (ticker, earnings_date, collected_at, source, sentiment_text,
                     sentiment_score, sentiment_direction, vrp_ratio, implied_move_pct, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ticker, earnings_date, now, source, sentiment_text,
                    sentiment_score, direction_str, vrp_ratio, implied_move_pct, now
                ))
                conn.commit()

    def record_outcome(
        self,
        ticker: str,
        earnings_date: str,
        actual_move_pct: float,
        actual_direction: str,
        trade_outcome: Optional[str] = None
    ) -> bool:
        """
        Record post-earnings outcome.

        Args:
            ticker: Stock ticker
            earnings_date: Date string (YYYY-MM-DD format)
            actual_move_pct: Actual intraday move percentage
            actual_direction: "UP" or "DOWN"
            trade_outcome: "WIN", "LOSS", or "SKIP" (if we traded)

        Returns:
            True if record was updated, False if no matching sentiment found

        Raises:
            ValueError: If actual_direction or trade_outcome is invalid
        """
        ticker = ticker.upper()
        actual_direction = actual_direction.upper()

        # Validate actual_direction
        if actual_direction not in self.VALID_DIRECTIONS:
            raise ValueError(
                f"Invalid actual_direction '{actual_direction}'. "
                f"Must be one of: {self.VALID_DIRECTIONS}"
            )

        # Validate trade_outcome if provided
        if trade_outcome is not None:
            trade_outcome = trade_outcome.upper()
            if trade_outcome not in self.VALID_TRADE_OUTCOMES:
                raise ValueError(
                    f"Invalid trade_outcome '{trade_outcome}'. "
                    f"Must be one of: {self.VALID_TRADE_OUTCOMES}"
                )

        now = datetime.now(timezone.utc).isoformat()

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                # Get existing record to check prediction
                row = conn.execute("""
                    SELECT sentiment_direction FROM sentiment_history
                    WHERE ticker = ? AND earnings_date = ?
                """, (ticker, earnings_date)).fetchone()

                if not row:
                    return False

                # Calculate if prediction was correct
                sentiment_dir = row[0]
                prediction_correct = None
                if sentiment_dir in ("bullish", "bearish"):
                    predicted_up = sentiment_dir == "bullish"
                    actual_up = actual_direction == "UP"
                    prediction_correct = 1 if predicted_up == actual_up else 0

                conn.execute("""
                    UPDATE sentiment_history
                    SET actual_move_pct = ?,
                        actual_direction = ?,
                        prediction_correct = ?,
                        trade_outcome = ?,
                        updated_at = ?
                    WHERE ticker = ? AND earnings_date = ?
                """, (
                    actual_move_pct, actual_direction, prediction_correct,
                    trade_outcome, now, ticker, earnings_date
                ))
                conn.commit()
                return True

    def get(self, ticker: str, earnings_date: str) -> Optional[SentimentRecord]:
        """Get a specific sentiment record."""
        ticker = ticker.upper()

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("""
                    SELECT * FROM sentiment_history
                    WHERE ticker = ? AND earnings_date = ?
                """, (ticker, earnings_date)).fetchone()

                if not row:
                    return None

                return self._row_to_record(row)

    def get_pending_outcomes(self, before_date: Optional[str] = None) -> List[SentimentRecord]:
        """Get records that need outcome data filled in."""
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row

                if before_date:
                    rows = conn.execute("""
                        SELECT * FROM sentiment_history
                        WHERE actual_move_pct IS NULL
                        AND earnings_date < ?
                        ORDER BY earnings_date
                    """, (before_date,)).fetchall()
                else:
                    rows = conn.execute("""
                        SELECT * FROM sentiment_history
                        WHERE actual_move_pct IS NULL
                        ORDER BY earnings_date
                    """).fetchall()

                return [self._row_to_record(row) for row in rows]

    def get_by_date_range(
        self,
        start_date: str,
        end_date: str,
        with_outcomes_only: bool = False
    ) -> List[SentimentRecord]:
        """Get records within a date range."""
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row

                if with_outcomes_only:
                    rows = conn.execute("""
                        SELECT * FROM sentiment_history
                        WHERE earnings_date BETWEEN ? AND ?
                        AND actual_move_pct IS NOT NULL
                        ORDER BY earnings_date
                    """, (start_date, end_date)).fetchall()
                else:
                    rows = conn.execute("""
                        SELECT * FROM sentiment_history
                        WHERE earnings_date BETWEEN ? AND ?
                        ORDER BY earnings_date
                    """, (start_date, end_date)).fetchall()

                return [self._row_to_record(row) for row in rows]

    def get_accuracy_stats(self) -> Dict[str, Any]:
        """
        Calculate sentiment prediction accuracy statistics.

        Returns:
            Dictionary with accuracy metrics
        """
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row

                # Overall stats
                total = conn.execute("""
                    SELECT COUNT(*) as cnt FROM sentiment_history
                """).fetchone()['cnt']

                with_outcomes = conn.execute("""
                    SELECT COUNT(*) as cnt FROM sentiment_history
                    WHERE actual_move_pct IS NOT NULL
                """).fetchone()['cnt']

                # Prediction accuracy (only for bullish/bearish predictions)
                correct = conn.execute("""
                    SELECT COUNT(*) as cnt FROM sentiment_history
                    WHERE prediction_correct = 1
                """).fetchone()['cnt']

                predictions_made = conn.execute("""
                    SELECT COUNT(*) as cnt FROM sentiment_history
                    WHERE prediction_correct IS NOT NULL
                """).fetchone()['cnt']

                # By sentiment direction
                by_direction = {}
                for direction in ['bullish', 'bearish', 'neutral']:
                    row = conn.execute("""
                        SELECT
                            COUNT(*) as total,
                            SUM(CASE WHEN prediction_correct = 1 THEN 1 ELSE 0 END) as correct,
                            AVG(actual_move_pct) as avg_move
                        FROM sentiment_history
                        WHERE sentiment_direction = ?
                        AND actual_move_pct IS NOT NULL
                    """, (direction,)).fetchone()

                    by_direction[direction] = {
                        'total': row['total'] or 0,
                        'correct': row['correct'] or 0,
                        'avg_move': row['avg_move']
                    }

                # Trade outcomes
                trade_stats = {}
                for outcome in ['WIN', 'LOSS', 'SKIP']:
                    cnt = conn.execute("""
                        SELECT COUNT(*) as cnt FROM sentiment_history
                        WHERE trade_outcome = ?
                    """, (outcome,)).fetchone()['cnt']
                    trade_stats[outcome] = cnt

                return {
                    'total_records': total,
                    'with_outcomes': with_outcomes,
                    'pending_outcomes': total - with_outcomes,
                    'predictions_made': predictions_made,
                    'predictions_correct': correct,
                    'accuracy': correct / predictions_made if predictions_made > 0 else None,
                    'by_direction': by_direction,
                    'trade_outcomes': trade_stats
                }

    def _row_to_record(self, row: sqlite3.Row) -> SentimentRecord:
        """Convert database row to SentimentRecord."""
        direction = SentimentDirection.UNKNOWN
        if row['sentiment_direction']:
            try:
                direction = SentimentDirection(row['sentiment_direction'])
            except ValueError:
                logger.warning(
                    f"Unknown sentiment_direction '{row['sentiment_direction']}' "
                    f"for {row['ticker']} on {row['earnings_date']}, defaulting to UNKNOWN"
                )

        return SentimentRecord(
            ticker=row['ticker'],
            earnings_date=row['earnings_date'],
            collected_at=datetime.fromisoformat(row['collected_at']),
            source=row['source'],
            sentiment_text=row['sentiment_text'],
            sentiment_score=row['sentiment_score'],
            sentiment_direction=direction,
            vrp_ratio=row['vrp_ratio'],
            implied_move_pct=row['implied_move_pct'],
            actual_move_pct=row['actual_move_pct'],
            actual_direction=row['actual_direction'],
            prediction_correct=bool(row['prediction_correct']) if row['prediction_correct'] is not None else None,
            trade_outcome=row['trade_outcome']
        )

    def stats(self) -> Dict[str, Any]:
        """Get basic statistics about the history table."""
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row

                row = conn.execute("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(DISTINCT ticker) as unique_tickers,
                        MIN(earnings_date) as earliest,
                        MAX(earnings_date) as latest,
                        SUM(CASE WHEN actual_move_pct IS NOT NULL THEN 1 ELSE 0 END) as with_outcomes
                    FROM sentiment_history
                """).fetchone()

                by_source = {}
                for src_row in conn.execute("""
                    SELECT source, COUNT(*) as cnt
                    FROM sentiment_history
                    GROUP BY source
                """):
                    by_source[src_row['source']] = src_row['cnt']

                return {
                    'total_records': row['total'],
                    'unique_tickers': row['unique_tickers'],
                    'earliest_date': row['earliest'],
                    'latest_date': row['latest'],
                    'with_outcomes': row['with_outcomes'],
                    'pending_outcomes': row['total'] - (row['with_outcomes'] or 0),
                    'by_source': by_source
                }


# Convenience functions for slash commands
def record_sentiment(
    ticker: str,
    earnings_date: str,
    source: str,
    sentiment_text: str,
    sentiment_score: Optional[float] = None,
    sentiment_direction: Optional[SentimentDirection] = None,
    vrp_ratio: Optional[float] = None,
    implied_move_pct: Optional[float] = None
) -> None:
    """Quick helper to record sentiment."""
    history = SentimentHistory()
    history.record_sentiment(
        ticker=ticker,
        earnings_date=earnings_date,
        source=source,
        sentiment_text=sentiment_text,
        sentiment_score=sentiment_score,
        sentiment_direction=sentiment_direction,
        vrp_ratio=vrp_ratio,
        implied_move_pct=implied_move_pct
    )


def record_outcome(
    ticker: str,
    earnings_date: str,
    actual_move_pct: float,
    actual_direction: str,
    trade_outcome: Optional[str] = None
) -> bool:
    """Quick helper to record outcome."""
    history = SentimentHistory()
    return history.record_outcome(
        ticker=ticker,
        earnings_date=earnings_date,
        actual_move_pct=actual_move_pct,
        actual_direction=actual_direction,
        trade_outcome=trade_outcome
    )


def get_pending_outcomes() -> List[SentimentRecord]:
    """Quick helper to get records needing outcomes."""
    history = SentimentHistory()
    return history.get_pending_outcomes()


def get_sentiment_stats() -> str:
    """Get formatted sentiment history stats for display."""
    history = SentimentHistory()
    stats = history.stats()
    accuracy = history.get_accuracy_stats()

    accuracy_str = f"{accuracy['accuracy']:.1%}" if accuracy['accuracy'] else "N/A"

    return f"""Sentiment History:
  Total records: {stats['total_records']}
  Unique tickers: {stats['unique_tickers']}
  Date range: {stats['earliest_date'] or 'N/A'} to {stats['latest_date'] or 'N/A'}
  With outcomes: {stats['with_outcomes']}
  Pending outcomes: {stats['pending_outcomes']}
  Prediction accuracy: {accuracy_str}
  By source: {stats['by_source']}"""
