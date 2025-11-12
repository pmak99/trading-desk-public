"""
Error handling using Result[T, Error] pattern.

This module provides a functional error handling approach that makes errors
explicit in type signatures and prevents silent failures.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TypeVar, Generic, Callable, Any, Optional


class ErrorCode(Enum):
    """Error codes for categorizing failures."""

    RATELIMIT = "RATELIMIT"
    NODATA = "NODATA"
    INVALID = "INVALID"
    TIMEOUT = "TIMEOUT"
    EXTERNAL = "EXTERNAL"
    DBERROR = "DBERROR"
    CALCULATION = "CALCULATION"
    CONFIGURATION = "CONFIGURATION"


@dataclass
class AppError:
    """Application error with code, message, and context."""

    code: ErrorCode
    message: str
    context: Optional[dict] = None

    def __str__(self):
        ctx = f" | {self.context}" if self.context else ""
        return f"{self.code.value}: {self.message}{ctx}"


T = TypeVar('T')
E = TypeVar('E', bound=AppError)


@dataclass
class Result(Generic[T, E]):
    """
    Result type for functional error handling.

    A Result is either Ok(value) or Err(error), never both.
    Forces explicit error handling at call sites.

    Examples:
        result = calculate_something()
        if result.is_ok:
            value = result.value
        else:
            handle_error(result.error)
    """

    value: Optional[T] = None
    error: Optional[E] = None

    @classmethod
    def Ok(cls, value: T) -> 'Result[T, AppError]':
        """Create a successful result."""
        return Result(value=value)

    @classmethod
    def Err(cls, error: AppError) -> 'Result[T, AppError]':
        """Create an error result."""
        return Result(error=error)

    @property
    def is_ok(self) -> bool:
        """True if result is Ok."""
        return self.value is not None and self.error is None

    @property
    def is_err(self) -> bool:
        """True if result is Err."""
        return self.error is not None

    def unwrap(self) -> T:
        """
        Get the value or raise exception if error.
        Use only when you're certain result is Ok.
        """
        if self.is_err:
            raise Exception(str(self.error))
        return self.value

    def unwrap_err(self) -> E:
        """
        Get the error or raise exception if Ok.
        Use for testing or when you expect an error.
        """
        if self.is_ok:
            raise Exception("Result is Ok, not Err")
        return self.error

    def unwrap_or(self, default: T) -> T:
        """Get value or return default if error."""
        return self.value if self.is_ok else default

    def map(self, func: Callable[[T], Any]) -> 'Result[Any, E]':
        """Transform the value if Ok, otherwise propagate error."""
        if self.is_err:
            return Result.Err(self.error)
        try:
            return Result.Ok(func(self.value))
        except Exception as e:
            return Result.Err(AppError(ErrorCode.CALCULATION, str(e)))

    def and_then(self, func: Callable[[T], 'Result[Any, E]']) -> 'Result[Any, E]':
        """Chain operations that return Results."""
        if self.is_err:
            return Result.Err(self.error)
        return func(self.value)


# Convenience aliases
Ok = Result.Ok
Err = Result.Err
