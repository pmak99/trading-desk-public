"""
Earnings calendar repository for persisting earnings data.
"""

import sqlite3
import logging
from datetime import date
from pathlib import Path
from typing import List, Tuple
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.enums import EarningsTiming

logger = logging.getLogger(__name__)


class EarningsRepository:
    """Repository for earnings calendar data."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def save_earnings_event(
        self, ticker: str, earnings_date: date, timing: EarningsTiming
    ) -> Result[None, AppError]:
        """
        Save earnings event to database.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Date of earnings announcement
            timing: BMO, AMC, or DMH

        Returns:
            Result with None on success or AppError on failure
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    INSERT OR REPLACE INTO earnings_calendar
                    (ticker, earnings_date, timing, confirmed)
                    VALUES (?, ?, ?, 1)
                    ''',
                    (ticker, earnings_date.isoformat(), timing.value),
                )
                conn.commit()

            logger.debug(
                f"Saved earnings: {ticker} on {earnings_date} ({timing.value})"
            )
            return Ok(None)

        except sqlite3.Error as e:
            logger.error(f"Failed to save earnings event: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def get_earnings_history(
        self, ticker: str, limit: int = 12
    ) -> Result[List[Tuple[date, EarningsTiming]], AppError]:
        """
        Get past earnings dates for ticker.

        Args:
            ticker: Stock ticker symbol
            limit: Maximum number of past events to retrieve

        Returns:
            Result with list of (date, timing) tuples
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT earnings_date, timing
                    FROM earnings_calendar
                    WHERE ticker = ? AND earnings_date < date('now')
                    ORDER BY earnings_date DESC
                    LIMIT ?
                    ''',
                    (ticker, limit),
                )
                rows = cursor.fetchall()

            if not rows:
                return Err(
                    AppError(
                        ErrorCode.NODATA, f"No earnings history for {ticker}"
                    )
                )

            results = [
                (date.fromisoformat(row[0]), EarningsTiming(row[1]))
                for row in rows
            ]

            return Ok(results)

        except sqlite3.Error as e:
            logger.error(f"Failed to get earnings history: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def get_upcoming_earnings(
        self, days_ahead: int = 7
    ) -> Result[List[Tuple[str, date]], AppError]:
        """
        Get all earnings in next N days.

        Args:
            days_ahead: Number of days to look ahead

        Returns:
            Result with list of (ticker, date) tuples
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT ticker, earnings_date
                    FROM earnings_calendar
                    WHERE earnings_date BETWEEN date('now') AND date('now', '+' || ? || ' days')
                    ORDER BY earnings_date ASC
                    ''',
                    (days_ahead,),
                )
                rows = cursor.fetchall()

            if not rows:
                return Ok([])  # Empty list is valid

            results = [(row[0], date.fromisoformat(row[1])) for row in rows]

            return Ok(results)

        except sqlite3.Error as e:
            logger.error(f"Failed to get upcoming earnings: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def delete_old_earnings(self, days_old: int = 365) -> Result[int, AppError]:
        """
        Delete earnings events older than N days.

        Args:
            days_old: Delete events older than this many days

        Returns:
            Result with count of deleted rows
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    DELETE FROM earnings_calendar
                    WHERE earnings_date < date('now', '-' || ? || ' days')
                    ''',
                    (days_old,),
                )
                deleted_count = cursor.rowcount
                conn.commit()

            logger.info(f"Deleted {deleted_count} old earnings events")
            return Ok(deleted_count)

        except sqlite3.Error as e:
            logger.error(f"Failed to delete old earnings: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))
