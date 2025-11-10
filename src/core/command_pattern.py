"""
Command pattern for undoable operations.

Provides a framework for executing, undoing, and redoing operations,
useful for interactive analysis and data manipulation.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Any, Callable
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class Command(ABC):
    """
    Abstract base command for undoable operations.

    Implements the Command pattern for operations that can be undone/redone.
    """

    def __init__(self, description: str = ""):
        """
        Initialize command.

        Args:
            description: Human-readable description of the command
        """
        self.description = description
        self.executed_at: Optional[datetime] = None
        self.undone_at: Optional[datetime] = None

    @abstractmethod
    def execute(self) -> Any:
        """
        Execute the command.

        Returns:
            Result of the operation
        """
        pass

    @abstractmethod
    def undo(self) -> None:
        """Undo the command's effects."""
        pass

    def can_undo(self) -> bool:
        """Check if command can be undone."""
        return self.executed_at is not None and self.undone_at is None

    def log_execution(self) -> None:
        """Log command execution."""
        self.executed_at = datetime.now()
        logger.info(f"Executed: {self.description}")

    def log_undo(self) -> None:
        """Log command undo."""
        self.undone_at = datetime.now()
        logger.info(f"Undone: {self.description}")


class FunctionCommand(Command):
    """
    Simple command that wraps functions for execute and undo.

    Useful for quick command creation without subclassing.
    """

    def __init__(
        self,
        execute_func: Callable[[], Any],
        undo_func: Callable[[], None],
        description: str = ""
    ):
        """
        Initialize function command.

        Args:
            execute_func: Function to execute
            undo_func: Function to undo
            description: Command description
        """
        super().__init__(description)
        self._execute_func = execute_func
        self._undo_func = undo_func

    def execute(self) -> Any:
        """Execute the wrapped function."""
        result = self._execute_func()
        self.log_execution()
        return result

    def undo(self) -> None:
        """Undo using the wrapped function."""
        self._undo_func()
        self.log_undo()


class DataModificationCommand(Command):
    """
    Command for modifying data with automatic undo.

    Stores previous state for automatic rollback.
    """

    def __init__(
        self,
        target: Any,
        attribute: str,
        new_value: Any,
        description: str = ""
    ):
        """
        Initialize data modification command.

        Args:
            target: Object to modify
            attribute: Attribute name to modify
            new_value: New value to set
            description: Command description
        """
        super().__init__(description or f"Set {attribute} to {new_value}")
        self.target = target
        self.attribute = attribute
        self.new_value = new_value
        self.old_value: Optional[Any] = None

    def execute(self) -> Any:
        """Execute the modification."""
        # Store old value for undo
        self.old_value = getattr(self.target, self.attribute, None)

        # Set new value
        setattr(self.target, self.attribute, self.new_value)
        self.log_execution()

        return self.new_value

    def undo(self) -> None:
        """Restore previous value."""
        if self.old_value is not None:
            setattr(self.target, self.attribute, self.old_value)
        self.log_undo()


class CommandHistory:
    """
    Manages command execution history with undo/redo support.

    Maintains a stack of executed commands and provides undo/redo functionality.
    """

    def __init__(self, max_history: int = 50):
        """
        Initialize command history.

        Args:
            max_history: Maximum number of commands to keep in history
        """
        self.max_history = max_history
        self._executed: List[Command] = []
        self._undone: List[Command] = []

    def execute(self, command: Command) -> Any:
        """
        Execute a command and add to history.

        Args:
            command: Command to execute

        Returns:
            Result of command execution
        """
        result = command.execute()

        # Add to history
        self._executed.append(command)

        # Clear redo stack (can't redo after new command)
        self._undone.clear()

        # Limit history size
        if len(self._executed) > self.max_history:
            self._executed.pop(0)

        return result

    def undo(self) -> bool:
        """
        Undo the last executed command.

        Returns:
            True if undo successful, False if no commands to undo
        """
        if not self._executed:
            logger.warning("No commands to undo")
            return False

        command = self._executed.pop()

        if not command.can_undo():
            logger.warning(f"Command cannot be undone: {command.description}")
            return False

        command.undo()
        self._undone.append(command)
        return True

    def redo(self) -> bool:
        """
        Redo the last undone command.

        Returns:
            True if redo successful, False if no commands to redo
        """
        if not self._undone:
            logger.warning("No commands to redo")
            return False

        command = self._undone.pop()
        result = command.execute()
        self._executed.append(command)
        return True

    def can_undo(self) -> bool:
        """Check if undo is available."""
        return len(self._executed) > 0

    def can_redo(self) -> bool:
        """Check if redo is available."""
        return len(self._undone) > 0

    def clear_history(self) -> None:
        """Clear all command history."""
        self._executed.clear()
        self._undone.clear()
        logger.info("Command history cleared")

    def get_history(self) -> List[str]:
        """
        Get list of executed commands.

        Returns:
            List of command descriptions
        """
        return [cmd.description for cmd in self._executed]

    def get_redo_history(self) -> List[str]:
        """
        Get list of undone commands available for redo.

        Returns:
            List of command descriptions
        """
        return [cmd.description for cmd in self._undone]


# Example command implementations for trading operations

class FilterTickersCommand(Command):
    """Command to filter tickers with undo support."""

    def __init__(
        self,
        ticker_list: List[str],
        filter_func: Callable[[str], bool],
        description: str = "Filter tickers"
    ):
        super().__init__(description)
        self.ticker_list = ticker_list
        self.filter_func = filter_func
        self.original_list: Optional[List[str]] = None
        self.filtered_result: Optional[List[str]] = None

    def execute(self) -> List[str]:
        """Execute the filter."""
        self.original_list = self.ticker_list.copy()
        self.filtered_result = [t for t in self.ticker_list if self.filter_func(t)]
        self.log_execution()
        return self.filtered_result

    def undo(self) -> None:
        """Restore original list."""
        if self.original_list is not None:
            self.ticker_list.clear()
            self.ticker_list.extend(self.original_list)
        self.log_undo()


class UpdateScoreCommand(Command):
    """Command to update ticker scores with undo support."""

    def __init__(
        self,
        ticker_data: dict,
        new_score: float,
        description: str = "Update ticker score"
    ):
        super().__init__(description)
        self.ticker_data = ticker_data
        self.new_score = new_score
        self.old_score: Optional[float] = None

    def execute(self) -> float:
        """Update the score."""
        self.old_score = self.ticker_data.get('score')
        self.ticker_data['score'] = self.new_score
        self.log_execution()
        return self.new_score

    def undo(self) -> None:
        """Restore old score."""
        if self.old_score is not None:
            self.ticker_data['score'] = self.old_score
        self.log_undo()
