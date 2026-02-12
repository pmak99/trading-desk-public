# cloud/tests/test_database.py
"""Tests for DatabaseSync with GCS locking."""

import pytest
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from google.api_core.exceptions import PreconditionFailed

from src.core.database import DatabaseSync, DatabaseContext, DatabaseSyncConflictError


@pytest.fixture
def mock_gcs():
    """Mock GCS client and blob."""
    with patch('src.core.database.storage.Client') as mock_client:
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.generation = 12345

        mock_client.return_value.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        yield {
            'client': mock_client,
            'bucket': mock_bucket,
            'blob': mock_blob,
        }


def test_database_sync_download(mock_gcs):
    """download() retrieves database and stores generation."""
    sync = DatabaseSync(bucket_name="test-bucket")

    # Mock successful download
    mock_gcs['blob'].generation = 54321

    path = sync.download()

    assert path == str(sync.local_path)
    assert sync._generation == 54321
    mock_gcs['blob'].download_to_filename.assert_called_once()


def test_database_sync_download_fresh_db(mock_gcs):
    """download() handles missing database gracefully."""
    sync = DatabaseSync(bucket_name="test-bucket")

    # Mock missing blob
    mock_gcs['blob'].reload.side_effect = Exception("Not found")

    path = sync.download()

    assert path == str(sync.local_path)
    assert sync._generation is None


def test_database_sync_upload_success(mock_gcs):
    """upload() succeeds with matching generation."""
    sync = DatabaseSync(bucket_name="test-bucket")
    sync._generation = 12345

    # Create a temp file to upload
    sync.local_path.touch()

    result = sync.upload()

    assert result is True
    mock_gcs['blob'].upload_from_filename.assert_called_once()


def test_database_sync_upload_conflict(mock_gcs):
    """upload() raises DatabaseSyncConflictError on generation conflict."""
    sync = DatabaseSync(bucket_name="test-bucket")
    sync._generation = 12345

    # Create a temp file to upload
    sync.local_path.touch()

    # Mock conflict
    mock_gcs['blob'].upload_from_filename.side_effect = PreconditionFailed("conflict")

    with pytest.raises(DatabaseSyncConflictError, match="Upload conflict"):
        sync.upload()


def test_database_context_success(mock_gcs):
    """DatabaseContext downloads, provides connection, and uploads."""
    sync = DatabaseSync(bucket_name="test-bucket")

    # Create a real temp database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    sync.local_path = db_path

    try:
        with DatabaseContext(sync) as conn:
            # Can execute queries
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.commit()

        # Verify upload was called
        mock_gcs['blob'].upload_from_filename.assert_called()
    finally:
        db_path.unlink(missing_ok=True)


def test_database_context_raises_on_conflict(mock_gcs):
    """DatabaseContext raises DatabaseSyncConflictError on upload conflict.

    IMPORTANT: This is the correct behavior. upload() raises immediately on
    PreconditionFailed, so the caller knows data may have been lost and must
    re-read and re-apply changes.
    """
    sync = DatabaseSync(bucket_name="test-bucket")

    # Create a real temp database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    sync.local_path = db_path

    # Upload fails due to conflict
    mock_gcs['blob'].upload_from_filename.side_effect = PreconditionFailed("conflict")

    try:
        with pytest.raises(DatabaseSyncConflictError, match="Upload conflict"):
            with DatabaseContext(sync) as conn:
                conn.execute("SELECT 1")
    finally:
        db_path.unlink(missing_ok=True)


def test_database_context_conflict_includes_message(mock_gcs):
    """DatabaseContext conflict error includes descriptive message for debugging."""
    sync = DatabaseSync(bucket_name="test-bucket")

    # Create a real temp database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    sync.local_path = db_path

    # Upload fails due to conflict
    mock_gcs['blob'].upload_from_filename.side_effect = PreconditionFailed("conflict")

    try:
        with pytest.raises(DatabaseSyncConflictError) as exc_info:
            with DatabaseContext(sync) as conn:
                conn.execute("SELECT 1")

        # Error should mention upload conflict
        assert "Upload conflict" in str(exc_info.value)
        assert "another instance" in str(exc_info.value)
    finally:
        db_path.unlink(missing_ok=True)
