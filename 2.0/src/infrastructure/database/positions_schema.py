"""
Position tracking database schema and migrations.

Adds tables for tracking open positions, closed positions, and performance analytics.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def add_positions_tables(db_path: Path) -> None:
    """
    Add position tracking tables to existing database.

    Tables added:
    1. positions - Open and closed positions
    2. position_legs - Multi-leg strategies (iron condors, spreads)
    3. performance_metrics - Aggregated performance by various dimensions

    Args:
        db_path: Path to SQLite database file
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    cursor = conn.cursor()

    try:
        # Table 1: Positions (main tracking table)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                entry_date DATE NOT NULL,
                earnings_date DATE NOT NULL,
                expiration_date DATE NOT NULL,

                -- Strategy info
                strategy_type TEXT NOT NULL CHECK(
                    strategy_type IN ('STRADDLE', 'STRANGLE', 'IRON_CONDOR',
                                     'BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD',
                                     'IRON_BUTTERFLY', 'OTHER')
                ),
                num_contracts INTEGER NOT NULL,

                -- Entry thesis
                credit_received REAL NOT NULL,
                max_loss REAL NOT NULL,
                vrp_ratio REAL NOT NULL,
                implied_move_pct REAL NOT NULL,
                historical_avg_move_pct REAL NOT NULL,
                edge_score REAL,
                consistency_score REAL,
                skew_score REAL,

                -- Position sizing
                position_size_pct REAL NOT NULL,
                kelly_fraction REAL,

                -- Risk parameters
                stop_loss_amount REAL,
                target_profit_amount REAL,
                breakeven_move_pct REAL,

                -- Current status
                status TEXT NOT NULL DEFAULT 'OPEN' CHECK(
                    status IN ('OPEN', 'CLOSED', 'STOPPED', 'EXPIRED')
                ),
                current_pnl REAL DEFAULT 0,
                current_pnl_pct REAL DEFAULT 0,
                days_held INTEGER DEFAULT 0,

                -- Close information (if closed)
                close_date DATE,
                close_price REAL,
                actual_move_pct REAL,
                final_pnl REAL,
                final_pnl_pct REAL,
                win_loss TEXT CHECK(win_loss IN ('WIN', 'LOSS', NULL)),

                -- Notes and metadata
                entry_notes TEXT,
                exit_notes TEXT,
                sector TEXT,
                market_cap_category TEXT CHECK(
                    market_cap_category IN ('LARGE', 'MID', 'SMALL', NULL)
                ),

                -- Timestamps
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(ticker, entry_date, expiration_date)
            )
        ''')

        # Indexes for positions
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions(ticker)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_positions_entry_date ON positions(entry_date)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_positions_expiration ON positions(expiration_date)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(strategy_type)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_positions_vrp ON positions(vrp_ratio)'
        )

        # Table 2: Position Legs (for multi-leg strategies)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS position_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                leg_type TEXT NOT NULL CHECK(
                    leg_type IN ('LONG_CALL', 'SHORT_CALL', 'LONG_PUT', 'SHORT_PUT')
                ),
                strike REAL NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL,
                delta REAL,
                gamma REAL,
                theta REAL,
                vega REAL,
                implied_vol REAL,

                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_legs_position_id ON position_legs(position_id)'
        )

        # Table 3: Performance Metrics (aggregated analytics)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS performance_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_type TEXT NOT NULL,
                metric_key TEXT NOT NULL,

                -- Counts
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,

                -- Win rate
                win_rate REAL DEFAULT 0,

                -- P&L
                total_pnl REAL DEFAULT 0,
                avg_win REAL DEFAULT 0,
                avg_loss REAL DEFAULT 0,
                largest_win REAL DEFAULT 0,
                largest_loss REAL DEFAULT 0,

                -- Risk metrics
                sharpe_ratio REAL,
                max_drawdown REAL,
                avg_hold_time_days REAL,

                -- Last updated
                period_start DATE,
                period_end DATE,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(metric_type, metric_key)
            )
        ''')

        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_perf_type_key ON performance_metrics(metric_type, metric_key)'
        )

        conn.commit()
        logger.info("✓ Position tracking tables created successfully")

    except sqlite3.Error as e:
        logger.error(f"Failed to create position tracking tables: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_positions_tables(db_path: Path) -> bool:
    """
    Verify position tracking tables exist.

    Args:
        db_path: Path to SQLite database

    Returns:
        True if all position tables exist
    """
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        required_tables = ['positions', 'position_legs', 'performance_metrics']

        for table in required_tables:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            )
            if not cursor.fetchone():
                logger.error(f"Missing position table: {table}")
                conn.close()
                return False

        conn.close()
        logger.info("✓ Position tracking tables verified")
        return True

    except sqlite3.Error as e:
        logger.error(f"Position table verification failed: {e}")
        return False


if __name__ == "__main__":
    # Quick test
    from pathlib import Path
    db_path = Path("data/ivcrush.db")
    add_positions_tables(db_path)
    verify_positions_tables(db_path)
