"""
Prices repository for persisting historical price movements.

Postgres via the shared pool.

⚠️ **The upsert here is a deliberate behaviour change, and it is a fix.**
SQLite's `INSERT OR REPLACE` deleted the conflicting row and re-inserted it, so
every column absent from the 12-column insert list reverted to its default.
Two of those columns are `pre_earnings_straddle_pct` and `ern_iv_effect` — the
frozen ORATS backfill, 8,452 rows of it, unrecoverable since the subscription
ended. Re-saving a move wiped them, and reassigned `id`. `ON CONFLICT ... DO
UPDATE` names the columns it touches and leaves the rest alone.

The insert statement and the row mapping are each defined once. They were
duplicated across two and three methods respectively, which is how the
omission above stayed identical in both copies.
"""

import logging
from datetime import date
from typing import TYPE_CHECKING, List, Optional

import psycopg

from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.types import HistoricalMove, Money, Percentage
from src.infrastructure.database.repositories.base_repository import BaseRepository

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

# Columns read back into a HistoricalMove.
_MOVE_COLUMNS = (
    "ticker", "earnings_date", "prev_close", "earnings_open",
    "earnings_high", "earnings_low", "earnings_close",
    "intraday_move_pct", "gap_move_pct", "close_move_pct",
    "volume_before", "volume_earnings",
)
_MOVE_SELECT = ", ".join(_MOVE_COLUMNS)

# Columns WRITTEN by a move save: the same list minus the three percentages.
# ⚠️ Those are GENERATED columns since migration 2 (2026-09-13), computed by
# Postgres from the row's own prices, and an INSERT that names them fails. That
# is the point: the stored-vs-prices invariant CLAUDE.md asks you to audit can
# no longer be broken by a writer, which is how 1,757 values came to be stored
# as abs() in Nov-Dec 2025 while the prices beside them said otherwise.
_MOVE_WRITE_COLUMNS = tuple(c for c in _MOVE_COLUMNS if not c.endswith("_move_pct"))

# Everything except the (ticker, earnings_date) conflict key. Deliberately does
# NOT include pre_earnings_straddle_pct, ern_iv_effect, created_at or id —
# see the module docstring.
_MOVE_UPDATE_SET = ",\n                ".join(
    f"{c} = EXCLUDED.{c}" for c in _MOVE_WRITE_COLUMNS[2:]
)

UPSERT_MOVE_SQL = f"""
    INSERT INTO historical_moves ({", ".join(_MOVE_WRITE_COLUMNS)})
    VALUES ({", ".join(["%s"] * len(_MOVE_WRITE_COLUMNS))})
    ON CONFLICT (ticker, earnings_date) DO UPDATE SET
                {_MOVE_UPDATE_SET}
"""


def _move_params(move: HistoricalMove) -> tuple:
    """Bind a HistoricalMove to UPSERT_MOVE_SQL's parameters.

    The move's own percentages are not sent: the database derives them from
    the prices, so a HistoricalMove whose percentages disagree with its prices
    is stored with the values the prices imply.
    """
    return (
        move.ticker,
        move.earnings_date,
        float(move.prev_close.amount),
        float(move.earnings_open.amount),
        float(move.earnings_high.amount),
        float(move.earnings_low.amount),
        float(move.earnings_close.amount),
        move.volume_before,
        move.volume_earnings,
    )


def _row_to_move(row) -> HistoricalMove:
    """Build a HistoricalMove from a _MOVE_SELECT row.

    `earnings_date` is a real `date` column now, so it arrives as a
    `datetime.date` and the old `date.fromisoformat(row[1])` reparse is gone.
    """
    return HistoricalMove(
        ticker=row[0],
        earnings_date=row[1],
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


class PricesRepository(BaseRepository):
    """
    Repository for historical price movements.

    Inherits connection management from BaseRepository.
    """

    def __init__(self, pool: Optional['ConnectionPool'] = None):
        """
        Initialize repository.

        Args:
            pool: Connection pool (defaults to the process-wide pool)
        """
        super().__init__(pool)

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
                cursor.execute(UPSERT_MOVE_SQL, _move_params(move))
                conn.commit()

            logger.debug(
                f"Saved move: {move.ticker} on {move.earnings_date} "
                f"({move.intraday_move_pct})"
            )
            return Ok(None)

        except psycopg.Error as e:
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
                cursor.executemany(
                    UPSERT_MOVE_SQL, [_move_params(m) for m in moves]
                )
                conn.commit()

            logger.info(f"Saved {len(moves)} historical moves")
            return Ok(len(moves))

        except psycopg.Error as e:
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
                    f'''
                    SELECT {_MOVE_SELECT}
                    FROM historical_moves
                    WHERE ticker = %s
                    ORDER BY earnings_date DESC
                    LIMIT %s
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

            moves = [_row_to_move(row) for row in rows]

            logger.debug(f"Retrieved {len(moves)} moves for {ticker}")
            return Ok(moves)

        except psycopg.Error as e:
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
                    f'''
                    SELECT {_MOVE_SELECT}
                    FROM historical_moves
                    WHERE ticker = %s AND earnings_date = %s
                    ''',
                    (ticker, earnings_date),
                )
                row = cursor.fetchone()

            if not row:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No move for {ticker} on {earnings_date}",
                    )
                )

            return Ok(_row_to_move(row))

        except psycopg.Error as e:
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
                    'SELECT COUNT(*) FROM historical_moves WHERE ticker = %s',
                    (ticker,),
                )
                count = cursor.fetchone()[0]

            return Ok(count)

        except psycopg.Error as e:
            logger.error(f"Failed to count moves: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))

    def get_historical_moves_batch(
        self, tickers: List[str], limit: int = 12
    ) -> Result[dict[str, List[HistoricalMove]], AppError]:
        """
        Get historical moves for multiple tickers in a single query.

        Eliminates N+1 pattern: fetches all tickers in 1 query using
        a window function instead of N separate queries.

        Args:
            tickers: List of stock ticker symbols
            limit: Maximum moves per ticker (default: 12)

        Returns:
            Result with dict mapping ticker -> list of HistoricalMove
        """
        if not tickers:
            return Ok({})

        try:
            upper_tickers = [t.upper() for t in tickers]

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f'''
                    WITH ranked AS (
                        SELECT {_MOVE_SELECT},
                               ROW_NUMBER() OVER (
                                   PARTITION BY ticker ORDER BY earnings_date DESC
                               ) as rn
                        FROM historical_moves
                        WHERE ticker = ANY(%s)
                    )
                    SELECT {_MOVE_SELECT}
                    FROM ranked
                    WHERE rn <= %s
                    ORDER BY ticker, earnings_date DESC
                    ''',
                    (upper_tickers, limit),
                )
                rows = cursor.fetchall()

            # Group results by ticker
            result: dict[str, List[HistoricalMove]] = {t: [] for t in upper_tickers}
            for row in rows:
                move = _row_to_move(row)
                if move.ticker in result:
                    result[move.ticker].append(move)

            logger.debug(
                f"Batch retrieved moves for {len(tickers)} tickers "
                f"({sum(len(v) for v in result.values())} total moves)"
            )
            return Ok(result)

        except psycopg.Error as e:
            logger.error(f"Failed to get batch historical moves: {e}")
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
                    WHERE earnings_date
                          < (CURRENT_DATE - (%s || ' days')::interval)::date
                    ''',
                    (days_old,),
                )
                deleted_count = cursor.rowcount
                conn.commit()

            logger.info(f"Deleted {deleted_count} old historical moves")
            return Ok(deleted_count)

        except psycopg.Error as e:
            logger.error(f"Failed to delete old moves: {e}")
            return Err(AppError(ErrorCode.DBERROR, str(e)))
