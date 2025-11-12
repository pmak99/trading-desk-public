"""
Database schema initialization for IV Crush 2.0.

Creates SQLite database with proper indexes and constraints.
"""

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def init_database(db_path: Path) -> None:
    """
    Initialize database schema.

    Creates 5 tables:
    1. earnings_calendar - Earnings events
    2. historical_moves - Historical price movements
    3. ticker_metadata - Ticker info (sector, market cap)
    4. analysis_log - Analysis results log
    5. rate_limits - API rate limit tracking

    Args:
        db_path: Path to SQLite database file
    """
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    try:
        # Table 1: Earnings Calendar
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS earnings_calendar (
                ticker TEXT NOT NULL,
                earnings_date DATE NOT NULL,
                timing TEXT NOT NULL CHECK(timing IN ('BMO', 'AMC', 'DMH', 'UNKNOWN')),
                confirmed BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, earnings_date)
            )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings_calendar(earnings_date)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_earnings_ticker ON earnings_calendar(ticker)'
        )

        # Table 2: Historical Moves
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historical_moves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                earnings_date DATE NOT NULL,
                prev_close REAL NOT NULL,
                earnings_open REAL NOT NULL,
                earnings_high REAL NOT NULL,
                earnings_low REAL NOT NULL,
                earnings_close REAL NOT NULL,
                intraday_move_pct REAL NOT NULL,
                gap_move_pct REAL NOT NULL,
                close_move_pct REAL NOT NULL,
                volume_before INTEGER,
                volume_earnings INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ticker, earnings_date)
            )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_moves_ticker ON historical_moves(ticker)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_moves_date ON historical_moves(earnings_date)'
        )

        # Table 3: Ticker Metadata
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticker_metadata (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                sector TEXT,
                industry TEXT,
                market_cap REAL,
                avg_volume INTEGER,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Table 4: Analysis Log
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                earnings_date DATE NOT NULL,
                expiration DATE NOT NULL,
                implied_move_pct REAL NOT NULL,
                historical_mean_pct REAL NOT NULL,
                vrp_ratio REAL NOT NULL,
                edge_score REAL NOT NULL,
                recommendation TEXT NOT NULL,
                confidence REAL,
                analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_analysis_ticker ON analysis_log(ticker)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_analysis_date ON analysis_log(analyzed_at)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_analysis_recommendation ON analysis_log(recommendation)'
        )

        # Table 5: Rate Limits (for tracking API usage)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rate_limits (
                service TEXT NOT NULL,
                window_start DATETIME NOT NULL,
                window_type TEXT NOT NULL CHECK(window_type IN ('minute', 'hour', 'day')),
                request_count INTEGER DEFAULT 0,
                PRIMARY KEY (service, window_start, window_type)
            )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_rate_limits_service ON rate_limits(service, window_start)'
        )

        # Table 6: Cache (L2 persistent cache - Phase 2)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_cache_timestamp ON cache(timestamp)'
        )

        conn.commit()
        logger.info(f"✓ Database initialized: {db_path}")

    except sqlite3.Error as e:
        logger.error(f"Database initialization failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_database(db_path: Path) -> bool:
    """
    Verify database schema is correct.

    Args:
        db_path: Path to SQLite database

    Returns:
        True if all tables exist with correct structure
    """
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        required_tables = [
            'earnings_calendar',
            'historical_moves',
            'ticker_metadata',
            'analysis_log',
            'rate_limits',
            'cache',
        ]

        for table in required_tables:
            cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            )
            if not cursor.fetchone():
                logger.error(f"Missing table: {table}")
                return False

        conn.close()
        logger.info("✓ Database schema verified")
        return True

    except sqlite3.Error as e:
        logger.error(f"Database verification failed: {e}")
        return False


def drop_all_tables(db_path: Path) -> None:
    """
    Drop all tables. USE WITH CAUTION.
    Only for testing or complete reset.

    Args:
        db_path: Path to SQLite database
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    tables = [
        'earnings_calendar',
        'historical_moves',
        'ticker_metadata',
        'analysis_log',
        'rate_limits',
        'cache',
    ]

    try:
        for table in tables:
            cursor.execute(f'DROP TABLE IF EXISTS {table}')
        conn.commit()
        logger.warning(f"All tables dropped from {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    # Quick test
    from src.config.config import get_config

    config = get_config()
    init_database(config.database.path)
    verify_database(config.database.path)
