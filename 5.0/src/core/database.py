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
import uuid
from typing import Optional, List
from pathlib import Path

from .logging import log


class DatabaseCorruptedError(Exception):
    """Raised when database integrity check fails."""
    pass


class DatabaseSyncConflictError(Exception):
    """Raised when database sync conflict cannot be resolved."""
    pass


class DatabaseSync:
    """Sync SQLite to/from GCS with generation-based locking."""

    # Timeout for GCS operations (seconds)
    DOWNLOAD_TIMEOUT = 30
    UPLOAD_TIMEOUT = 60

    def __init__(self, bucket_name: str, blob_name: str = "ivcrush.db"):
        self.bucket_name = bucket_name
        self.blob_name = blob_name
        # Use instance-unique temp file to prevent multi-instance conflicts
        instance_id = str(uuid.uuid4())[:8]
        self.local_path = Path(tempfile.gettempdir()) / f"ivcrush_{instance_id}.db"
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

            # Validate database integrity after download
            self._validate_integrity()

            log("info", "Database downloaded", generation=self._generation)
        except DatabaseCorruptedError:
            # Re-raise corruption errors - don't swallow them
            raise
        except Exception as e:
            log("warn", "No existing database, starting fresh", error=str(e))
            self._generation = None

        return str(self.local_path)

    def _validate_integrity(self) -> None:
        """
        Validate downloaded database integrity.

        Raises:
            DatabaseCorruptedError: If integrity check fails
        """
        conn = sqlite3.connect(str(self.local_path))
        try:
            cursor = conn.execute("PRAGMA integrity_check")
            result = cursor.fetchone()[0]
            if result != "ok":
                raise DatabaseCorruptedError(
                    f"Database integrity check failed: {result}"
                )
        finally:
            conn.close()

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
    """
    Context manager for database operations with auto-sync.

    IMPORTANT: This context manager handles read-modify-write patterns with GCS.
    On conflict, it raises DatabaseSyncConflictError rather than silently losing
    changes. The caller should handle conflicts by re-reading and re-applying
    their changes.

    Thread Safety:
        Uses instance-unique temp files (see DatabaseSync.__init__).
        Assumes external synchronization for multi-threaded access.
    """

    def __init__(self, sync: DatabaseSync, max_retries: int = 3):
        self.sync = sync
        self.conn: Optional[sqlite3.Connection] = None
        self.max_retries = max_retries
        self._initial_generation: Optional[int] = None

    def __enter__(self) -> sqlite3.Connection:
        self.sync.download()
        self._initial_generation = self.sync._generation
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

                    # Conflict detected - another instance wrote first
                    old_generation = self.sync._generation
                    log("warn", "GCS conflict detected",
                        attempt=attempt + 1,
                        expected_generation=old_generation)

                    # Download to get new generation for potential retry
                    self.sync.download()
                    new_generation = self.sync._generation

                    # Verify generation actually changed (sanity check)
                    if new_generation == old_generation:
                        log("error", "Generation unchanged after conflict - possible corruption",
                            generation=new_generation)
                        raise DatabaseSyncConflictError(
                            f"Database conflict but generation unchanged ({new_generation}). "
                            "Possible database corruption or GCS issue."
                        )

                    # After download, our local changes are LOST because the file
                    # was replaced. We cannot simply retry upload - the caller must
                    # re-read and re-apply their changes. Raise immediately.
                    raise DatabaseSyncConflictError(
                        f"Database sync conflict after {attempt + 1} attempt(s). "
                        f"Generation changed from {old_generation} to {new_generation}. "
                        "Caller should re-read data and retry operation."
                    )

                # Should not reach here, but handle edge case
                raise DatabaseSyncConflictError(
                    f"Database sync failed after {self.max_retries} retries"
                )
        finally:
            # Always close connection, even if retries fail
            if self.conn:
                self.conn.close()
            # Clean up temp file to avoid disk space leaks
            if self.sync.local_path.exists():
                try:
                    self.sync.local_path.unlink()
                except OSError:
                    pass  # Best effort cleanup
