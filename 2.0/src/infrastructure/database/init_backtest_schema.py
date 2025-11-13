"""
Database schema for backtest results.

Adds tables to store backtest runs and individual trades for analysis.
"""

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def add_backtest_tables(db_path: Path) -> None:
    """
    Add backtest result tables to existing database.

    Creates 2 new tables:
    1. backtest_runs - Aggregate results per configuration
    2. backtest_trades - Individual trade details

    Args:
        db_path: Path to SQLite database file
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    cursor = conn.cursor()

    try:
        # Table 1: Backtest Runs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY,
                config_name TEXT NOT NULL,
                config_description TEXT,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                total_opportunities INTEGER NOT NULL,
                qualified_opportunities INTEGER NOT NULL,
                selected_trades INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                total_pnl REAL NOT NULL,
                avg_pnl_per_trade REAL NOT NULL,
                sharpe_ratio REAL NOT NULL,
                max_drawdown REAL NOT NULL,
                avg_score_winners REAL,
                avg_score_losers REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_backtest_config ON backtest_runs(config_name)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_backtest_date ON backtest_runs(start_date, end_date)'
        )

        # Table 2: Backtest Trades
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                config_name TEXT NOT NULL,
                ticker TEXT NOT NULL,
                earnings_date DATE NOT NULL,
                composite_score REAL NOT NULL,
                rank INTEGER NOT NULL,
                selected BOOLEAN NOT NULL,
                avg_historical_move REAL NOT NULL,
                consistency REAL NOT NULL,
                historical_std REAL NOT NULL,
                actual_move REAL NOT NULL,
                simulated_pnl REAL NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
            )
        ''')

        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_trades_run ON backtest_trades(run_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_trades_ticker ON backtest_trades(ticker)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_trades_selected ON backtest_trades(selected)'
        )

        conn.commit()
        logger.info(f"✓ Backtest tables added to {db_path}")

    except sqlite3.Error as e:
        logger.error(f"Failed to add backtest tables: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def save_backtest_results_to_db(db_path: Path, results) -> None:
    """
    Save backtest results to database.

    Args:
        db_path: Path to database
        results: BacktestResult objects
    """
    from src.application.services.backtest_engine import BacktestResult

    conn = sqlite3.connect(str(db_path), timeout=30)
    cursor = conn.cursor()

    try:
        for result in results:
            # Save run
            cursor.execute('''
                INSERT OR REPLACE INTO backtest_runs
                (run_id, config_name, config_description, start_date, end_date,
                 total_opportunities, qualified_opportunities, selected_trades,
                 win_rate, total_pnl, avg_pnl_per_trade, sharpe_ratio, max_drawdown,
                 avg_score_winners, avg_score_losers)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                result.run_id,
                result.config_name,
                result.config_description,
                str(result.start_date),
                str(result.end_date),
                result.total_opportunities,
                result.qualified_opportunities,
                result.selected_trades,
                result.win_rate,
                result.total_pnl,
                result.avg_pnl_per_trade,
                result.sharpe_ratio,
                result.max_drawdown,
                result.avg_score_winners,
                result.avg_score_losers,
            ))

            # Save trades
            for trade in result.trades:
                cursor.execute('''
                    INSERT INTO backtest_trades
                    (run_id, config_name, ticker, earnings_date, composite_score,
                     rank, selected, avg_historical_move, consistency, historical_std,
                     actual_move, simulated_pnl)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    result.run_id,
                    result.config_name,
                    trade.ticker,
                    str(trade.earnings_date),
                    trade.composite_score,
                    trade.rank,
                    trade.selected,
                    trade.avg_historical_move,
                    trade.consistency,
                    trade.historical_std,
                    trade.actual_move,
                    trade.simulated_pnl,
                ))

        conn.commit()
        logger.info(f"✓ Saved {len(results)} backtest runs to database")

    except sqlite3.Error as e:
        logger.error(f"Failed to save backtest results: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    # Quick test
    from pathlib import Path
    db_path = Path("data/ivcrush.db")
    if db_path.exists():
        add_backtest_tables(db_path)
        print("Backtest tables added successfully")
    else:
        print(f"Database not found: {db_path}")
