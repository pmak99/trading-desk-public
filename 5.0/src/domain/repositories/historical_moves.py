"""Repository for historical earnings moves and earnings calendar."""

import sqlite3
from typing import Dict, Any, List, Optional

from src.core.logging import log
from src.domain.repositories.connection_pool import (
    _normalize_ticker, validate_date, validate_limit, validate_days,
    TICKER_PATTERN, DATE_PATTERN, get_pool,
)

# Matches 2.0's NEXT_QUARTER_THRESHOLD_DAYS convention: a confirmed row within
# this many days of a proposed new date is treated as "probably the same
# event" (protect it from a single-source overwrite); further out is treated
# as a genuinely different, later earnings event (don't block it).
CONFIRMED_EVENT_WINDOW_DAYS = 45


class HistoricalMovesRepository:
    """Repository for historical earnings moves."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._pool = get_pool(db_path)

    def get_moves(self, ticker: str, limit: int = 12) -> List[Dict[str, Any]]:
        """
        Get past earnings moves for ticker.

        Args:
            ticker: Stock symbol (1-5 uppercase letters)
            limit: Max moves to return (1-100, default 12)

        Returns:
            List of move dicts with gap_move_pct, intraday_move_pct, etc.

        Raises:
            ValueError: If ticker or limit is invalid
        """
        ticker = _normalize_ticker(ticker)
        limit = validate_limit(limit)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct,
                       prev_close, earnings_close,
                       CASE WHEN gap_move_pct >= 0 THEN 'UP' ELSE 'DOWN' END as direction
                FROM historical_moves
                WHERE ticker = ?
                ORDER BY earnings_date DESC
                LIMIT ?
                """,
                (ticker, limit)
            )
            rows = cursor.fetchall()
            # Map column names for compatibility
            results = []
            for row in rows:
                d = dict(row)
                d['close_before'] = d.pop('prev_close', None)
                d['close_after'] = d.pop('earnings_close', None)
                results.append(d)
            return results

    def get_moves_batch(self, tickers: List[str], limit: int = 12) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get historical moves for multiple tickers in a single query.

        Reduces N+1 query pattern (30 separate queries → 1 batch query).
        Returns dict mapping ticker to list of moves.

        Args:
            tickers: List of stock symbols (empty list returns empty dict)
            limit: Max moves per ticker (1-100, default 12)

        Returns:
            Dict mapping ticker -> list of move dicts

        Example:
            moves = repo.get_moves_batch(["AAPL", "NVDA", "MSFT"])
            aapl_moves = moves.get("AAPL", [])
        """
        # Early return for empty tickers list to avoid constructing
        # invalid SQL with empty IN() clause
        if not tickers:
            return {}

        # Validate all tickers
        validated_tickers = [_normalize_ticker(t) for t in tickers]
        limit = validate_limit(limit)

        if not validated_tickers:
            return {}

        # Build placeholders for IN clause
        placeholders = ",".join("?" for _ in validated_tickers)

        with self._pool.get_connection() as conn:
            # Use window function to get top N moves per ticker
            cursor = conn.execute(
                f"""
                WITH ranked AS (
                    SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct,
                           prev_close, earnings_close,
                           CASE WHEN gap_move_pct >= 0 THEN 'UP' ELSE 'DOWN' END as direction,
                           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY earnings_date DESC) as rn
                    FROM historical_moves
                    WHERE ticker IN ({placeholders})
                )
                SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct,
                       prev_close, earnings_close, direction
                FROM ranked
                WHERE rn <= ?
                ORDER BY ticker, earnings_date DESC
                """,
                (*validated_tickers, limit)
            )
            rows = cursor.fetchall()

            # Group by ticker
            result: Dict[str, List[Dict[str, Any]]] = {t: [] for t in validated_tickers}
            for row in rows:
                d = dict(row)
                ticker = d["ticker"]
                d['close_before'] = d.pop('prev_close', None)
                d['close_after'] = d.pop('earnings_close', None)
                if ticker in result:
                    result[ticker].append(d)

            return result

    def get_average_move(self, ticker: str, metric: str = "intraday") -> Optional[float]:
        """
        Get average absolute move for VRP calculation.

        Args:
            ticker: Stock symbol
            metric: "intraday" (default, matches 2.0) or "gap"

        Returns:
            Average absolute move percent, or None if no data
        """
        ticker = _normalize_ticker(ticker)
        moves = self.get_moves(ticker)
        if not moves:
            return None

        # Use intraday_move_pct by default (matches 2.0 behavior)
        move_key = "intraday_move_pct" if metric == "intraday" else "gap_move_pct"
        abs_moves = [abs(m[move_key]) for m in moves if m.get(move_key)]
        if not abs_moves:
            return None

        return sum(abs_moves) / len(abs_moves)

    def get_next_earnings(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get next upcoming earnings date for ticker from calendar.

        Args:
            ticker: Stock symbol

        Returns:
            Dict with earnings_date, timing (BMO/AMC), or None if not found
        """
        ticker = _normalize_ticker(ticker)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT earnings_date, timing
                FROM earnings_calendar
                WHERE ticker = ? AND earnings_date >= date('now')
                ORDER BY earnings_date ASC
                LIMIT 1
                """,
                (ticker,)
            )
            row = cursor.fetchone()
            if row:
                return {"earnings_date": row["earnings_date"], "timing": row["timing"]}
            return None

    def upsert_earnings_calendar(self, earnings: List[Dict[str, Any]]) -> int:
        """
        Upsert earnings calendar records from Finnhub (bulk, single-source).

        Args:
            earnings: List of earnings records with symbol, report_date, timing

        Returns:
            Number of records upserted

        Skips (does not overwrite) any ticker whose existing row is already
        confirmed=1 with a DIFFERENT date/timing than this single-source
        Finnhub read proposes. Fixed 2026-07-27: this job runs weekly and
        previously did an unconditional INSERT OR REPLACE from Finnhub
        alone with zero cross-check -- worse than the sibling bug already
        fixed in 2.0's sync_earnings_calendar.py (that one at least had a
        conflict-detection concept to bypass; this had none at all). A
        confirmed=1 row only exists because 2.0's sync process already
        cross-validated it against a second source (Yahoo Finance) before
        marking it confirmed -- this job has no second source of its own
        (bulk Finnhub only), so the safe move is to never silently
        overwrite that already-vetted state, not to re-implement
        corroboration here from scratch. New/unconfirmed tickers are
        unaffected and still get written as before.
        """
        if not earnings:
            return 0

        count = 0
        skipped_confirmed = 0
        with self._pool.get_connection() as conn:
            for record in earnings:
                try:
                    ticker = record.get("symbol", "").upper().strip()
                    report_date = record.get("report_date", "")
                    timing = record.get("timing") or "UNKNOWN"  # Finnhub doesn't provide timing here

                    # Skip invalid records
                    if not ticker or not report_date:
                        continue
                    if not TICKER_PATTERN.match(ticker):
                        continue
                    if not DATE_PATTERN.match(report_date):
                        continue

                    existing = conn.execute(
                        "SELECT earnings_date, timing, confirmed FROM earnings_calendar "
                        "WHERE ticker = ? AND earnings_date = ?",
                        (ticker, report_date),
                    ).fetchone()

                    if existing is None:
                        # No row at this exact date -- check if a DIFFERENT
                        # confirmed row exists for a nearby/other date that
                        # this write would leave stale-but-untouched (fine,
                        # INSERT OR REPLACE only ever touches the exact PK)
                        # or would create a conflicting duplicate for dedup
                        # to sort out later. Only block when the ticker's
                        # nearest confirmed row looks like the SAME event
                        # (within CONFIRMED_EVENT_WINDOW_DAYS) -- a confirmed
                        # row for an unrelated, more-distant quarter must
                        # not block a genuinely new, later event from being
                        # added.
                        confirmed_row = conn.execute(
                            "SELECT earnings_date, timing FROM earnings_calendar "
                            "WHERE ticker = ? AND confirmed = 1 "
                            "AND earnings_date >= date('now') "
                            "AND ABS(julianday(earnings_date) - julianday(?)) <= ? "
                            "ORDER BY earnings_date ASC LIMIT 1",
                            (ticker, report_date, CONFIRMED_EVENT_WINDOW_DAYS),
                        ).fetchone()
                        if confirmed_row is not None and confirmed_row["earnings_date"] != report_date:
                            skipped_confirmed += 1
                            log(
                                "info", "Skipped single-source overwrite of confirmed date",
                                ticker=ticker, confirmed_date=confirmed_row["earnings_date"],
                                finnhub_date=report_date,
                            )
                            continue
                    elif existing["confirmed"] and (
                        existing["earnings_date"] != report_date or existing["timing"] != timing
                    ):
                        skipped_confirmed += 1
                        log(
                            "info", "Skipped single-source overwrite of confirmed date",
                            ticker=ticker, confirmed_date=existing["earnings_date"],
                            finnhub_date=report_date,
                        )
                        continue

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO earnings_calendar
                        (ticker, earnings_date, timing, confirmed, updated_at)
                        VALUES (?, ?, ?, 0, datetime('now'))
                        """,
                        (ticker, report_date, timing)
                    )
                    count += 1
                except Exception as e:
                    log("debug", "Skipped earnings record", ticker=ticker, error=str(e))
                    continue

            conn.commit()
            log(
                "info", "Upserted earnings calendar",
                count=count, skipped_confirmed=skipped_confirmed,
            )
        return count

    def get_earnings_by_date(self, date: str) -> List[Dict[str, Any]]:
        """
        Get all earnings for a specific date from database.

        Args:
            date: Target date (YYYY-MM-DD)

        Returns:
            List of dicts with symbol, report_date, timing, name
        """
        date = validate_date(date)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ticker as symbol, earnings_date as report_date, timing
                FROM earnings_calendar
                WHERE earnings_date = ?
                ORDER BY ticker
                """,
                (date,)
            )
            return [
                {
                    "symbol": row["symbol"],
                    "report_date": row["report_date"],
                    "timing": row["timing"],
                    "name": "",  # Not stored in DB
                }
                for row in cursor.fetchall()
            ]

    def get_upcoming_earnings(self, start_date: str, days: int = 5) -> List[Dict[str, Any]]:
        """
        Get earnings for next N days from database.

        Args:
            start_date: Start date in YYYY-MM-DD format (typically today in ET)
            days: Number of days to look ahead (default 5, max 365)

        Returns:
            List of dicts with symbol, report_date, timing, name
        """
        start_date = validate_date(start_date)
        days = validate_days(days)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ticker as symbol, earnings_date as report_date, timing
                FROM earnings_calendar
                WHERE earnings_date >= ?
                  AND earnings_date <= date(?, '+' || ? || ' days')
                ORDER BY earnings_date, ticker
                """,
                (start_date, start_date, days)
            )
            return [
                {
                    "symbol": row["symbol"],
                    "report_date": row["report_date"],
                    "timing": row["timing"],
                    "name": "",  # Not stored in DB
                }
                for row in cursor.fetchall()
            ]

    def count_moves(self, ticker: str) -> int:
        """Count historical moves for ticker."""
        ticker = _normalize_ticker(ticker)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM historical_moves WHERE ticker = ?",
                (ticker,)
            )
            return cursor.fetchone()[0]

    def get_tracked_tickers(self) -> set:
        """
        Get set of all tickers with historical moves data.

        Used to filter Alpha Vantage earnings to only include tickers
        we can actually analyze (have VRP data for).

        Returns:
            Set of ticker symbols (uppercase)
        """
        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT ticker FROM historical_moves"
            )
            return {row["ticker"] for row in cursor.fetchall()}

    def save_move(self, move: Dict[str, Any]) -> bool:
        """
        Save a historical move record.

        Args:
            move: Dict with ticker, earnings_date, gap_move_pct, etc.

        Returns:
            True if saved successfully

        Raises:
            ValueError: If ticker or date is invalid
            sqlite3.Error: On database errors (except duplicates)
        """
        ticker = _normalize_ticker(move["ticker"])
        earnings_date = validate_date(move["earnings_date"])

        with self._pool.get_connection() as conn:
            try:
                # Map to actual database schema columns
                prev_close = move.get("prev_close") or move.get("close_before")
                earnings_close = move.get("earnings_close") or move.get("close_after")

                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_moves
                    (ticker, earnings_date, gap_move_pct, intraday_move_pct,
                     prev_close, earnings_open, earnings_high, earnings_low,
                     earnings_close, close_move_pct)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticker,
                        earnings_date,
                        move.get("gap_move_pct"),
                        move.get("intraday_move_pct"),
                        prev_close,
                        move.get("earnings_open"),
                        move.get("earnings_high"),
                        move.get("earnings_low"),
                        earnings_close,
                        move.get("close_move_pct"),
                    )
                )
                conn.commit()
                log("debug", "Saved move", ticker=ticker, date=earnings_date)
                return True
            except sqlite3.IntegrityError:
                # Duplicate is OK - idempotent operation
                log("debug", "Move already exists", ticker=ticker, date=earnings_date)
                return True
            except sqlite3.Error as e:
                log("error", "Failed to save move", error=str(e), ticker=ticker)
                raise  # Re-raise for caller to handle

    def get_position_limits(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get position limits and tail risk data for ticker.

        Returns:
            Dict with tail_risk_ratio, tail_risk_level, max_contracts, max_notional,
            or None if not found or table doesn't exist
        """
        ticker = _normalize_ticker(ticker)

        with self._pool.get_connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT ticker, tail_risk_ratio, tail_risk_level,
                           max_contracts, max_notional, avg_move, max_move, num_quarters
                    FROM position_limits
                    WHERE ticker = ?
                    """,
                    (ticker,)
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "ticker": row["ticker"],
                        "tail_risk_ratio": row["tail_risk_ratio"],
                        "tail_risk_level": row["tail_risk_level"],
                        "max_contracts": row["max_contracts"],
                        "max_notional": row["max_notional"],
                        "avg_move": row["avg_move"],
                        "max_move": row["max_move"],
                        "num_quarters": row["num_quarters"],
                    }
                return None
            except sqlite3.OperationalError as e:
                # Table might not exist yet - gracefully return None
                if "no such table" in str(e):
                    log("debug", "position_limits table not found", ticker=ticker)
                    return None
                raise
