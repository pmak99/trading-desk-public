"""
Database sync with GCS using generation-based locking.

CRITICAL: Uses GCS object generation numbers for optimistic locking.
This prevents race conditions when multiple Cloud Run instances try to write.
"""

from google.cloud import storage
from google.api_core.exceptions import PreconditionFailed
import sqlite3
import tempfile
import shutil
from typing import Optional, List
from pathlib import Path

from .logging import log


class DatabaseSync:
    """Sync SQLite to/from GCS with generation-based locking."""

    # Timeout for GCS operations (seconds)
    DOWNLOAD_TIMEOUT = 30
    UPLOAD_TIMEOUT = 60

    def __init__(self, bucket_name: str, blob_name: str = "ivcrush.db"):
        self.bucket_name = bucket_name
        self.blob_name = blob_name
        self.local_path = Path(tempfile.gettempdir()) / "ivcrush.db"
        self._generation: Optional[int] = None
        self._client = storage.Client()

    def download(self) -> str:
        """
        Download database from GCS.

        Returns:
            Local path to downloaded database
        """
        bucket = self._client.bucket(self.bucket_name)
        blob = bucket.blob(self.blob_name)

        try:
            # Get current metadata with timeout to avoid hanging on network issues
            blob.reload(timeout=self.DOWNLOAD_TIMEOUT)
            self._generation = blob.generation
            blob.download_to_filename(
                str(self.local_path),
                timeout=self.DOWNLOAD_TIMEOUT
            )
            log("info", "Database downloaded", generation=self._generation)
        except Exception as e:
            log("warn", "No existing database, starting fresh", error=str(e))
            self._generation = None

        return str(self.local_path)

    def upload(self) -> bool:
        """
        Upload database to GCS with generation-based locking.

        Uses if_generation_match to ensure no concurrent writes.

        Returns:
            True if upload succeeded, False if conflict
        """
        bucket = self._client.bucket(self.bucket_name)
        blob = bucket.blob(self.blob_name)

        try:
            if self._generation is not None:
                # Optimistic lock: only succeed if generation matches
                blob.upload_from_filename(
                    str(self.local_path),
                    if_generation_match=self._generation,
                    timeout=self.UPLOAD_TIMEOUT
                )
            else:
                # First upload - no generation to match
                blob.upload_from_filename(
                    str(self.local_path),
                    if_generation_match=0,  # Only succeed if doesn't exist
                    timeout=self.UPLOAD_TIMEOUT
                )

            blob.reload(timeout=self.UPLOAD_TIMEOUT)
            self._generation = blob.generation
            log("info", "Database uploaded", generation=self._generation)
            return True

        except PreconditionFailed:
            log("error", "Database upload conflict - another instance wrote first")
            return False

    def get_connection(self) -> sqlite3.Connection:
        """Get SQLite connection to local database."""
        return sqlite3.connect(str(self.local_path))


class DatabaseContext:
    """Context manager for database operations with auto-sync."""

    def __init__(self, sync: DatabaseSync, max_retries: int = 3):
        self.sync = sync
        self.conn: Optional[sqlite3.Connection] = None
        self.max_retries = max_retries
        self._changes_sql: List[str] = []  # Track changes for retry

    def __enter__(self) -> sqlite3.Connection:
        self.sync.download()
        self.conn = self.sync.get_connection()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Close connection AFTER all retry logic (fix for connection leak)
        try:
            if exc_type is None:
                # Try upload with retries on conflict
                for attempt in range(self.max_retries):
                    if self.sync.upload():
                        return  # Success

                    # Conflict - download latest and notify caller
                    log("warn", "GCS conflict, attempt retry", attempt=attempt + 1)
                    self.sync.download()

                # All retries failed
                raise RuntimeError(
                    f"Database sync conflict after {self.max_retries} retries - "
                    "another instance is writing frequently"
                )
        finally:
            # Always close connection, even if retries fail
            if self.conn:
                self.conn.close()
