"""
Unit tests for BaseRepository and ResilientRepository.

Tests:
- Connection management (direct and pooled)
- Error handling utilities
- Query execution helpers
- Failure tracking and recovery
"""

import pytest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.infrastructure.database.repositories.base_repository import (
    BaseRepository,
    ResilientRepository,
    DEFAULT_CONNECTION_TIMEOUT,
)
from src.domain.errors import Result, ErrorCode


class ConcreteRepository(BaseRepository):
    """Concrete implementation for testing BaseRepository."""

    def create_test_table(self):
        """Create a test table."""
        with self._get_connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT)"
            )
            conn.commit()

    def insert_item(self, name: str):
        """Insert using base class helper."""
        return self._execute_insert(
            "INSERT INTO test (name) VALUES (?)",
            (name,),
            operation="insert test item",
        )

    def get_items(self):
        """Get all items using base class helper."""
        return self._execute_query(
            "SELECT id, name FROM test",
            fetch_all=True,
        )


class TestBaseRepository:
    """Tests for BaseRepository."""

    def test_init_with_path_string(self):
        """Test initialization with string path."""
        repo = ConcreteRepository("/tmp/test.db")
        assert repo.db_path == "/tmp/test.db"
        assert repo.pool is None
        assert repo.timeout == DEFAULT_CONNECTION_TIMEOUT

    def test_init_with_path_object(self):
        """Test initialization with Path object."""
        path = Path("/tmp/test.db")
        repo = ConcreteRepository(path)
        assert repo.db_path == "/tmp/test.db"

    def test_init_with_custom_timeout(self):
        """Test initialization with custom timeout."""
        repo = ConcreteRepository("/tmp/test.db", timeout=60)
        assert repo.timeout == 60

    def test_get_connection_creates_direct_connection(self):
        """Test that _get_connection creates a direct SQLite connection."""
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            repo = ConcreteRepository(f.name)
            with repo._get_connection() as conn:
                assert isinstance(conn, sqlite3.Connection)
                # Verify connection is functional
                conn.execute("SELECT 1")

    def test_get_connection_uses_pool_when_available(self):
        """Test that _get_connection uses pool when provided."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.get_connection.return_value.__enter__ = lambda _: mock_conn
        mock_pool.get_connection.return_value.__exit__ = MagicMock(return_value=False)

        repo = ConcreteRepository("/tmp/test.db", pool=mock_pool)
        with repo._get_connection() as conn:
            assert conn is mock_conn

        mock_pool.get_connection.assert_called_once()

    def test_db_error_returns_err_with_dberror_code(self):
        """Test _db_error returns proper error result."""
        repo = ConcreteRepository("/tmp/test.db")
        error = sqlite3.OperationalError("disk full")

        result = repo._db_error(error, "insert")

        assert isinstance(result, Result)
        assert result.is_err
        assert result.error.code == ErrorCode.DBERROR
        assert "disk full" in result.error.message

    def test_no_data_error_returns_err_with_nodata_code(self):
        """Test _no_data_error returns proper error result."""
        repo = ConcreteRepository("/tmp/test.db")

        result = repo._no_data_error("No items found")

        assert isinstance(result, Result)
        assert result.is_err
        assert result.error.code == ErrorCode.NODATA
        assert result.error.message == "No items found"

    def test_execute_insert_success(self):
        """Test _execute_insert on success."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            repo = ConcreteRepository(f.name)
            repo.create_test_table()

            result = repo.insert_item("test")

            assert isinstance(result, Result)
            assert result.is_ok
            assert result.value is None

    def test_execute_query_fetch_all(self):
        """Test _execute_query with fetch_all."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            repo = ConcreteRepository(f.name)
            repo.create_test_table()
            repo.insert_item("item1")
            repo.insert_item("item2")

            result = repo.get_items()

            assert isinstance(result, Result)
            assert result.is_ok
            assert len(result.value) == 2
            assert result.value[0][1] == "item1"
            assert result.value[1][1] == "item2"

    def test_execute_batch_success(self):
        """Test _execute_batch for bulk inserts."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            repo = ConcreteRepository(f.name)
            repo.create_test_table()

            result = repo._execute_batch(
                "INSERT INTO test (name) VALUES (?)",
                [("a",), ("b",), ("c",)],
                operation="batch insert",
            )

            assert isinstance(result, Result)
            assert result.is_ok
            assert result.value == 3

    def test_execute_batch_empty_list(self):
        """Test _execute_batch with empty list returns 0."""
        repo = ConcreteRepository("/tmp/test.db")

        result = repo._execute_batch(
            "INSERT INTO test (name) VALUES (?)",
            [],
        )

        assert isinstance(result, Result)
        assert result.is_ok
        assert result.value == 0

    def test_execute_delete_returns_count(self):
        """Test _execute_delete returns deleted row count."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            repo = ConcreteRepository(f.name)
            repo.create_test_table()
            repo.insert_item("delete_me")
            repo.insert_item("keep_me")

            result = repo._execute_delete(
                "DELETE FROM test WHERE name = ?",
                ("delete_me",),
            )

            assert isinstance(result, Result)
            assert result.is_ok
            assert result.value == 1


class TestResilientRepository:
    """Tests for ResilientRepository."""

    def test_init_sets_failure_tracking(self):
        """Test initialization sets up failure tracking."""
        repo = ResilientRepository("/tmp/test.db", max_failures=5)

        assert repo.failure_count == 0
        assert repo.max_failures == 5
        assert repo.is_healthy is True

    def test_record_success_resets_counter(self):
        """Test _record_success resets failure counter."""
        repo = ResilientRepository("/tmp/test.db")
        repo.failure_count = 5

        repo._record_success()

        assert repo.failure_count == 0

    def test_record_failure_increments_counter(self):
        """Test _record_failure increments failure counter."""
        repo = ResilientRepository("/tmp/test.db", max_failures=5)

        repo._record_failure(Exception("test"), "operation")

        assert repo.failure_count == 1
        assert repo.is_healthy is True

    def test_record_failure_logs_critical_at_threshold(self):
        """Test critical log when reaching failure threshold."""
        repo = ResilientRepository("/tmp/test.db", max_failures=3)
        repo.failure_count = 2  # One below threshold

        with patch("src.infrastructure.database.repositories.base_repository.logger") as mock_logger:
            repo._record_failure(Exception("db crash"), "write data")

            mock_logger.critical.assert_called_once()
            assert "3 consecutive times" in mock_logger.critical.call_args[0][0]

    def test_is_healthy_true_below_threshold(self):
        """Test is_healthy is True when below threshold."""
        repo = ResilientRepository("/tmp/test.db", max_failures=5)
        repo.failure_count = 4

        assert repo.is_healthy is True

    def test_is_healthy_false_at_threshold(self):
        """Test is_healthy is False at or above threshold."""
        repo = ResilientRepository("/tmp/test.db", max_failures=5)
        repo.failure_count = 5

        assert repo.is_healthy is False

    def test_inherits_from_base_repository(self):
        """Test ResilientRepository inherits from BaseRepository."""
        repo = ResilientRepository("/tmp/test.db")

        assert isinstance(repo, BaseRepository)
        assert hasattr(repo, "_get_connection")
        assert hasattr(repo, "_db_error")
        assert hasattr(repo, "_execute_query")
