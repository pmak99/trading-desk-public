"""
Prices repository for persisting historical price movements.

Supports both connection pooling (for production) and direct connections (for testing).
"""

import sqlite3
import logging
from datetime import date
from pathlib import Path
from typing import List, Optional
from contextlib import contextmanager
from src.domain.types import Money, Percentage, HistoricalMove
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

# TYPE_CHECKING import to avoid circular dependency
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.infrastructure.database.connection_pool import ConnectionPool

logger = logging.getLogger(__name__)

# Connection timeout for all database operations (30 seconds)
CONNECTION_TIMEOUT = 30


class PricesRepository:
    """
    Repository for historical price movements.

    Supports connection pooling for better concurrent performance.
    Falls back to direct connections if pool not provided (backward compatible).
    """

    def __init__(self, db_path: str | Path, pool: Optional['ConnectionPool'] = None):
        """
        Initialize repository.

        Args:
            db_path: Path to SQLite database
            pool: Optional connection pool (uses direct connections if None)
        """
        self.db_path = str(db_path)
        self.pool = pool

    @contextmanager
    def _get_connection(self):
        """
        Get database connection from pool or create direct connection.

        Yields:
            Database connection
        """
        if self.pool:
            # Use connection pool
            with self.pool.get_connection() as conn:
                yield conn
        else:
            # Fallback to direct connection (testing/legacy)
            conn = sqlite3.connect(self.db_path, timeout=CONNECTION_TIMEOUT)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def save_historical_move(
        self, move: HistoricalMove
    ) -> Result[None, AppError]:
        """
        Save historical earnings move to database.

        Args:
            move: HistoricalMove instance to save

        Returns:
            Result with None on success or AppError on failure
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    INSERT OR REPLACE INTO historical_moves
                    (ticker, earnings_date, prev_close, earnings_open,
                     earnings_high, earnings_low, earnings_close,
                     intraday_move_pct, gap_move_pct, close_move_pct,
                     volume_before, volume_earnings)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        move.ticker,
                        move.earnings_date.isoformat(),
                        float(move.prev_close.amount),
                        float(move.earnings_open.amount),
                        float(move.earnings_high.amount),
                        float(move.earnings_low.amount),
                        float(move.earnings_close.amount),
                        float(move.intraday_move_pct.value),
                        float(move.gap_move_pct.value),
                        float(move.close_move_pct.value),
                        move.volume_before,
                        move.volume_earnings,
                    ),
                )
                conn.commit()

            logger.debug(
                f"Saved move: {move.ticker} on {move.earnings_date} "
                f"({move.intraday_move_pct})"
            )
            return Ok(None)

        except sqlite3.Error as e:
            logger.error(f"Failed to save historical move: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def save_many(
        self, moves: List[HistoricalMove]
    ) -> Result[int, AppError]:
        """
        Save multiple historical moves (batch operation).

        Args:
            moves: List of HistoricalMove instances

        Returns:
            Result with count of saved moves or AppError
        """
        if not moves:
            return Ok(0)

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                data = [
                    (
                        move.ticker,
                        move.earnings_date.isoformat(),
                        float(move.prev_close.amount),
                        float(move.earnings_open.amount),
                        float(move.earnings_high.amount),
                        float(move.earnings_low.amount),
                        float(move.earnings_close.amount),
                        float(move.intraday_move_pct.value),
                        float(move.gap_move_pct.value),
                        float(move.close_move_pct.value),
                        move.volume_before,
                        move.volume_earnings,
                    )
                    for move in moves
                ]

                cursor.executemany(
                    '''
                    INSERT OR REPLACE INTO historical_moves
                    (ticker, earnings_date, prev_close, earnings_open,
                     earnings_high, earnings_low, earnings_close,
                     intraday_move_pct, gap_move_pct, close_move_pct,
                     volume_before, volume_earnings)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    data,
                )
                conn.commit()

            logger.info(f"Saved {len(moves)} historical moves")
            return Ok(len(moves))

        except sqlite3.Error as e:
            logger.error(f"Failed to save historical moves: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def get_historical_moves(
        self, ticker: str, limit: int = 12
    ) -> Result[List[HistoricalMove], AppError]:
        """
        Get past earnings moves for ticker.

        Args:
            ticker: Stock ticker symbol
            limit: Maximum number of past moves to retrieve

        Returns:
            Result with list of HistoricalMove or AppError
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT ticker, earnings_date, prev_close, earnings_open,
                           earnings_high, earnings_low, earnings_close,
                           intraday_move_pct, gap_move_pct, close_move_pct,
                           volume_before, volume_earnings
                    FROM historical_moves
                    WHERE ticker = ?
                    ORDER BY earnings_date DESC
                    LIMIT ?
                    ''',
                    (ticker, limit),
                )
                rows = cursor.fetchall()

            if not rows:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No historical moves for {ticker}",
                    )
                )

            moves = []
            for row in rows:
                move = HistoricalMove(
                    ticker=row[0],
                    earnings_date=date.fromisoformat(row[1]),
                    prev_close=Money(row[2]),
                    earnings_open=Money(row[3]),
                    earnings_high=Money(row[4]),
                    earnings_low=Money(row[5]),
                    earnings_close=Money(row[6]),
                    intraday_move_pct=Percentage(row[7]),
                    gap_move_pct=Percentage(row[8]),
                    close_move_pct=Percentage(row[9]),
                    volume_before=row[10],
                    volume_earnings=row[11],
                )
                moves.append(move)

            logger.debug(f"Retrieved {len(moves)} moves for {ticker}")
            return Ok(moves)

        except sqlite3.Error as e:
            logger.error(f"Failed to get historical moves: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def get_move_by_date(
        self, ticker: str, earnings_date: date
    ) -> Result[HistoricalMove, AppError]:
        """
        Get historical move for specific earnings date.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Specific earnings date

        Returns:
            Result with HistoricalMove or AppError
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT ticker, earnings_date, prev_close, earnings_open,
                           earnings_high, earnings_low, earnings_close,
                           intraday_move_pct, gap_move_pct, close_move_pct,
                           volume_before, volume_earnings
                    FROM historical_moves
                    WHERE ticker = ? AND earnings_date = ?
                    ''',
                    (ticker, earnings_date.isoformat()),
                )
                row = cursor.fetchone()

            if not row:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No move for {ticker} on {earnings_date}",
                    )
                )

            move = HistoricalMove(
                ticker=row[0],
                earnings_date=date.fromisoformat(row[1]),
                prev_close=Money(row[2]),
                earnings_open=Money(row[3]),
                earnings_high=Money(row[4]),
                earnings_low=Money(row[5]),
                earnings_close=Money(row[6]),
                intraday_move_pct=Percentage(row[7]),
                gap_move_pct=Percentage(row[8]),
                close_move_pct=Percentage(row[9]),
                volume_before=row[10],
                volume_earnings=row[11],
            )

            return Ok(move)

        except sqlite3.Error as e:
            logger.error(f"Failed to get move by date: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def count_moves(self, ticker: str) -> Result[int, AppError]:
        """
        Count historical moves for ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Result with count or AppError
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT COUNT(*) FROM historical_moves WHERE ticker = ?',
                    (ticker,),
                )
                count = cursor.fetchone()[0]

            return Ok(count)

        except sqlite3.Error as e:
            logger.error(f"Failed to count moves: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def delete_old_moves(
        self, days_old: int = 1095
    ) -> Result[int, AppError]:
        """
        Delete moves older than N days (default 3 years).

        Args:
            days_old: Delete moves older than this many days

        Returns:
            Result with count of deleted rows
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    DELETE FROM historical_moves
                    WHERE earnings_date < date('now', '-' || ? || ' days')
                    ''',
                    (days_old,),
                )
                deleted_count = cursor.rowcount
                conn.commit()

            logger.info(f"Deleted {deleted_count} old historical moves")
            return Ok(deleted_count)

        except sqlite3.Error as e:
            logger.error(f"Failed to delete old moves: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))
