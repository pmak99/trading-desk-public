"""
Earnings calendar repository for persisting earnings data.

Postgres via the shared pool. Two conversions here are behaviour changes, not
translations:

- `INSERT OR REPLACE` became `ON CONFLICT (ticker, earnings_date) DO UPDATE`.
  SQLite's form deleted the row and re-inserted it, so `created_at` — a column
  this statement never lists — silently reset on every re-save. The upsert
  preserves it.
- `earnings_date` is a real `date` column now, so it comes back as a
  `datetime.date` and the `date.fromisoformat(row[0])` reparse is gone.
"""

import logging
from datetime import date
from typing import TYPE_CHECKING, List, Optional, Tuple

import psycopg

from src.domain.enums import EarningsTiming
from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.infrastructure.database.repositories.base_repository import BaseRepository

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


class EarningsRepository(BaseRepository):
    """
    Repository for earnings calendar data.

    Inherits connection management from BaseRepository.
    """

    def __init__(self, pool: Optional['ConnectionPool'] = None):
        """
        Initialize repository.

        Args:
            pool: Connection pool (defaults to the process-wide pool)
        """
        super().__init__(pool)

    def save_earnings_event(
        self, ticker: str, earnings_date: date, timing: EarningsTiming,
        update_validation_timestamp: bool = True
    ) -> Result[None, AppError]:
        """
        Save earnings event to database, marked `confirmed`.

        Only a date two sources agree on may come here: `confirmed` is what the
        calendar dedup trusts over an unconfirmed row. The one caller, the calendar
        sync, checks `ValidationResult.corroborated` first (since 2026-09-15).

        Args:
            ticker: Stock ticker symbol
            earnings_date: Date of earnings announcement
            timing: BMO, AMC, or DMH
            update_validation_timestamp: If True, update last_validated_at to now

        Returns:
            Result with None on success or AppError on failure
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if update_validation_timestamp:
                    cursor.execute(
                        '''
                        INSERT INTO earnings_calendar
                            (ticker, earnings_date, timing, confirmed,
                             last_validated_at, updated_at)
                        VALUES (%s, %s, %s, true, now(), now())
                        ON CONFLICT (ticker, earnings_date) DO UPDATE SET
                            timing = EXCLUDED.timing,
                            confirmed = true,
                            last_validated_at = now(),
                            updated_at = now()
                        ''',
                        (ticker, earnings_date, timing.value),
                    )
                else:
                    cursor.execute(
                        '''
                        INSERT INTO earnings_calendar
                            (ticker, earnings_date, timing, confirmed, updated_at)
                        VALUES (%s, %s, %s, true, now())
                        ON CONFLICT (ticker, earnings_date) DO UPDATE SET
                            timing = EXCLUDED.timing,
                            confirmed = true,
                            updated_at = now()
                        ''',
                        (ticker, earnings_date, timing.value),
                    )
                conn.commit()

            logger.debug(
                f"Saved earnings: {ticker} on {earnings_date} ({timing.value})"
            )
            return Ok(None)

        except psycopg.Error as e:
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
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT earnings_date, timing
                    FROM earnings_calendar
                    WHERE ticker = %s AND earnings_date < CURRENT_DATE
                    ORDER BY earnings_date DESC
                    LIMIT %s
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

            results = [(row[0], EarningsTiming(row[1])) for row in rows]

            return Ok(results)

        except psycopg.Error as e:
            logger.error(f"Failed to get earnings history: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def get_timing(
        self, ticker: str, earnings_date: date
    ) -> Result[EarningsTiming, AppError]:
        """
        Stored BMO/AMC/DMH/UNKNOWN for one event.

        /analyze prices the implied move at a timing-dependent expiration
        (metrics/implied_move_expiration.py) and its CLI carries no timing.

        Returns:
            Result with the stored timing, or NODATA when the calendar has no
            row for that ticker on that date
        """
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT timing FROM earnings_calendar "
                    "WHERE ticker = %s AND earnings_date = %s",
                    (ticker, earnings_date),
                ).fetchone()
        except psycopg.Error as e:
            return self._db_error(e, "get earnings timing")

        if row is None:
            return Err(AppError(
                ErrorCode.NODATA,
                f"No earnings_calendar row for {ticker} on {earnings_date}",
            ))
        return Ok(EarningsTiming(row.timing))

    def get_upcoming_earnings(
        self, days_ahead: int = 7
    ) -> Result[List[Tuple[str, date]], AppError]:
        """
        Get all earnings in next N days.

        Args:
            days_ahead: Number of days to look ahead (must be >= 0)

        Returns:
            Result with list of (ticker, date) tuples
        """
        if days_ahead < 0:
            return Err(AppError(
                ErrorCode.INVALID,
                f"days_ahead must be non-negative, got {days_ahead}"
            ))

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT ticker, earnings_date
                    FROM earnings_calendar
                    WHERE earnings_date BETWEEN CURRENT_DATE
                      AND (CURRENT_DATE + (%s || ' days')::interval)::date
                    ORDER BY earnings_date ASC
                    ''',
                    (days_ahead,),
                )
                rows = cursor.fetchall()

            if not rows:
                return Ok([])  # Empty list is valid

            results = [(row[0], row[1]) for row in rows]

            return Ok(results)

        except psycopg.Error as e:
            return self._db_error(e, "get upcoming earnings")

    def delete_old_earnings(self, days_old: int = 365) -> Result[int, AppError]:
        """
        Delete earnings events older than N days.

        Args:
            days_old: Delete events older than this many days

        Returns:
            Result with count of deleted rows
        """
        return self._execute_delete(
            '''
            DELETE FROM earnings_calendar
            WHERE earnings_date < (CURRENT_DATE - (%s || ' days')::interval)::date
            ''',
            (days_old,),
            operation="delete old earnings",
        )
