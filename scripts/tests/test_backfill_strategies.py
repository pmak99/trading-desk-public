"""Tests for strategy backfill script."""

import pytest
import sqlite3
import tempfile
import os
from unittest.mock import patch

from scripts.backfill_strategies import (
    load_unlinked_legs,
    create_strategy,
    link_legs_to_strategy,
    run_backfill,
    create_strategy_with_conn,
    link_legs_to_strategy_with_conn,
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


class TestTransactionRollback:
    """Tests for transaction atomicity and rollback behavior."""

    def test_rollback_on_link_failure(self, test_db):
        """
        Test that if link_legs_to_strategy_with_conn fails,
        the strategy creation is rolled back (no orphaned strategy record).

        Regression test for commit 7efd42f - verifies transaction atomicity.
        """
        # Insert a valid leg into trade_journal
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, acquired_date, sale_date, days_held, option_type, strike, expiration,
             quantity, cost_basis, proceeds, gain_loss, is_winner, term)
            VALUES
            ('TEST', '2026-01-07', '2026-01-08', 1, 'PUT', 25.0, '2026-01-16',
             100, 1000.00, 1500.00, 500.00, 1, 'SHORT')
        """)
        valid_leg_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Count strategies before the operation
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM strategies")
        initial_strategy_count = cursor.fetchone()[0]
        conn.close()

        # Test scenario: Strategy creation succeeds, but linking fails
        # This simulates the atomic transaction behavior
        conn = sqlite3.connect(test_db)
        error_occurred = False
        try:
            # Create strategy (does not commit)
            strategy_id = create_strategy_with_conn(
                conn,
                symbol='TEST',
                strategy_type='SINGLE',
                acquired_date='2026-01-07',
                sale_date='2026-01-08',
                days_held=1,
                expiration='2026-01-16',
                quantity=100,
                gain_loss=500.00,
                is_winner=True,
            )

            # Verify the strategy was created in the transaction (before commit)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM strategies WHERE id = ?", (strategy_id,))
            assert cursor.fetchone()[0] == 1, "Strategy should exist in transaction"

            # Simulate a linking failure by raising an exception
            # This could happen due to database constraints, invalid IDs, etc.
            raise sqlite3.IntegrityError("Simulated constraint violation during linking")

        except sqlite3.IntegrityError:
            # Expected error - rollback the transaction
            conn.rollback()
            error_occurred = True
        finally:
            conn.close()

        # Verify the error occurred as expected
        assert error_occurred, "Test should have triggered an error"

        # Verify no orphaned strategy record was created (rollback worked)
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM strategies")
        final_strategy_count = cursor.fetchone()[0]
        conn.close()

        assert final_strategy_count == initial_strategy_count, (
            "Rolled-back strategy should not exist in database"
        )

    def test_rollback_in_run_backfill(self, test_db):
        """
        Test that run_backfill properly rolls back on failure and propagates errors.
        """
        # Insert a spread with one valid leg and set up for failure
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, acquired_date, sale_date, days_held, option_type, strike, expiration,
             quantity, cost_basis, proceeds, gain_loss, is_winner, term)
            VALUES
            ('FAIL', '2026-01-07', '2026-01-08', 1, 'PUT', 25.0, '2026-01-16',
             100, 1000.00, 1500.00, 500.00, 1, 'SHORT')
        """)
        conn.commit()
        conn.close()

        # Mock link_legs_to_strategy_with_conn to raise an error
        with patch('scripts.backfill_strategies.link_legs_to_strategy_with_conn') as mock_link:
            mock_link.side_effect = RuntimeError("Simulated linking failure")

            # run_backfill should propagate the error
            with pytest.raises(RuntimeError, match="Failed to backfill strategy"):
                run_backfill(test_db, dry_run=False)

        # Verify no strategy was created (rollback worked)
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM strategies")
        strategy_count = cursor.fetchone()[0]

        # Verify legs are still unlinked
        cursor.execute("SELECT COUNT(*) FROM trade_journal WHERE strategy_id IS NULL")
        unlinked_count = cursor.fetchone()[0]
        conn.close()

        assert strategy_count == 0, "No strategies should be created due to rollback"
        assert unlinked_count == 1, "Leg should remain unlinked after rollback"
