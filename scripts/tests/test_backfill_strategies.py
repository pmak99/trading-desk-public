"""Tests for strategy backfill script."""

import pytest
import sqlite3
import tempfile
import os

from scripts.backfill_strategies import (
    load_unlinked_legs,
    create_strategy,
    link_legs_to_strategy,
    run_backfill,
)


@pytest.fixture
def test_db():
    """Create a temporary test database with schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    # Create tables
    cursor.execute("""
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            acquired_date DATE NOT NULL,
            sale_date DATE NOT NULL,
            days_held INTEGER,
            expiration DATE,
            quantity INTEGER,
            net_credit REAL,
            net_debit REAL,
            gain_loss REAL NOT NULL,
            is_winner BOOLEAN NOT NULL,
            earnings_date DATE,
            actual_move REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE trade_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            acquired_date DATE,
            sale_date DATE NOT NULL,
            days_held INTEGER,
            option_type TEXT,
            strike REAL,
            expiration DATE,
            quantity INTEGER,
            cost_basis REAL NOT NULL,
            proceeds REAL NOT NULL,
            gain_loss REAL NOT NULL,
            is_winner BOOLEAN NOT NULL,
            term TEXT,
            wash_sale_amount REAL DEFAULT 0,
            earnings_date DATE,
            actual_move REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            strategy_id INTEGER REFERENCES strategies(id)
        )
    """)

    conn.commit()
    conn.close()

    yield path

    os.close(fd)
    os.unlink(path)


class TestLoadUnlinkedLegs:
    def test_loads_legs_without_strategy_id(self, test_db):
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, sale_date, cost_basis, proceeds, gain_loss, is_winner)
            VALUES ('AAPL', '2026-01-08', 100, 150, 50, 1)
        """)
        conn.commit()
        conn.close()

        legs = load_unlinked_legs(test_db)
        assert len(legs) == 1
        assert legs[0]['symbol'] == 'AAPL'

    def test_excludes_linked_legs(self, test_db):
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()

        # Create a strategy
        cursor.execute("""
            INSERT INTO strategies
            (symbol, strategy_type, acquired_date, sale_date, gain_loss, is_winner)
            VALUES ('AAPL', 'SINGLE', '2026-01-07', '2026-01-08', 50, 1)
        """)
        strategy_id = cursor.lastrowid

        # Linked leg
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, sale_date, cost_basis, proceeds, gain_loss, is_winner, strategy_id)
            VALUES ('AAPL', '2026-01-08', 100, 150, 50, 1, ?)
        """, (strategy_id,))

        # Unlinked leg
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, sale_date, cost_basis, proceeds, gain_loss, is_winner)
            VALUES ('MSFT', '2026-01-08', 200, 250, 50, 1)
        """)

        conn.commit()
        conn.close()

        legs = load_unlinked_legs(test_db)
        assert len(legs) == 1
        assert legs[0]['symbol'] == 'MSFT'


class TestCreateStrategy:
    def test_creates_strategy_record(self, test_db):
        strategy_id = create_strategy(
            test_db,
            symbol="APLD",
            strategy_type="SPREAD",
            acquired_date="2026-01-07",
            sale_date="2026-01-08",
            days_held=1,
            expiration="2026-01-16",
            quantity=200,
            gain_loss=5980.98,
            is_winner=True,
        )

        assert strategy_id is not None

        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
        row = cursor.fetchone()
        conn.close()

        assert row is not None


class TestRunBackfill:
    def test_groups_spread_legs(self, test_db):
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()

        # Insert APLD spread legs
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, acquired_date, sale_date, days_held, option_type, strike, expiration,
             quantity, cost_basis, proceeds, gain_loss, is_winner, term)
            VALUES
            ('APLD', '2026-01-07', '2026-01-08', 1, 'PUT', 25.0, '2026-01-16',
             200, 2452.75, 14995.24, 12542.49, 1, 'SHORT'),
            ('APLD', '2026-01-07', '2026-01-08', 1, 'PUT', 23.0, '2026-01-16',
             200, 7804.76, 1243.25, -6561.51, 0, 'SHORT')
        """)
        conn.commit()
        conn.close()

        stats = run_backfill(test_db, dry_run=False)

        assert stats['strategies_created'] == 1
        assert stats['legs_linked'] == 2

        # Verify strategy
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT strategy_type, gain_loss, is_winner FROM strategies")
        row = cursor.fetchone()
        conn.close()

        assert row[0] == 'SPREAD'
        assert abs(row[1] - 5980.98) < 0.01
        assert row[2] == 1  # Winner
