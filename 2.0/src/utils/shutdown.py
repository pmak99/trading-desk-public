"""
Graceful shutdown handler for CLI scripts.

Handles SIGTERM and SIGINT to ensure clean shutdown:
- Flushes database connections
- Closes connection pools
- Saves in-flight work
- Logs shutdown event
"""

import signal
import sys
import atexit
import logging
from typing import Callable, List

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """
    Handles graceful shutdown on SIGTERM/SIGINT.

    Executes registered cleanup callbacks in reverse registration order.
    """

    def __init__(self):
        self.shutdown_callbacks: List[Callable[[], None]] = []
        self.is_shutting_down = False

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        # Register atexit for Python shutdown
        atexit.register(self._cleanup)

    def register_callback(self, callback: Callable[[], None]) -> None:
        """
        Register cleanup callback.

        Args:
            callback: Function to call during shutdown
        """
        self.shutdown_callbacks.append(callback)
        logger.debug(f"Registered shutdown callback: {callback.__name__}")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        signal_name = signal.Signals(signum).name
        logger.info(f"Received {signal_name} signal, initiating graceful shutdown...")

        if self.is_shutting_down:
            logger.warning("Shutdown already in progress, ignoring signal")
            return

        self.is_shutting_down = True
        self._cleanup()
        sys.exit(0)

    def _cleanup(self):
        """Execute all cleanup callbacks."""
        if self.is_shutting_down:
            return  # Avoid duplicate cleanup

        self.is_shutting_down = True

        logger.info(f"Executing {len(self.shutdown_callbacks)} cleanup callbacks...")

        # Execute callbacks in reverse order (LIFO)
        for callback in reversed(self.shutdown_callbacks):
            try:
                logger.debug(f"Running shutdown callback: {callback.__name__}")
                callback()
            except Exception as e:
                logger.error(f"Error in shutdown callback {callback.__name__}: {e}")

        logger.info("Graceful shutdown complete")


# Global shutdown handler (singleton)
_shutdown_handler: GracefulShutdown = None


def get_shutdown_handler() -> GracefulShutdown:
    """
    Get global shutdown handler (singleton).

    Returns:
        GracefulShutdown instance
    """
    global _shutdown_handler

    if _shutdown_handler is None:
        _shutdown_handler = GracefulShutdown()

    return _shutdown_handler


def register_shutdown_callback(callback: Callable[[], None]) -> None:
    """
    Register cleanup callback for shutdown.

    Args:
        callback: Function to call during shutdown
    """
    handler = get_shutdown_handler()
    handler.register_callback(callback)
