"""
Error handling using Result[T, Error] pattern.

This module provides a functional error handling approach that makes errors
explicit in type signatures and prevents silent failures.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Callable,
    Generic,
    Literal,
    NoReturn,
    Optional,
    TypeAlias,
    TypeVar,
    Union,
)

logger = logging.getLogger(__name__)


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
    # The call got no usable answer and was not sent again, so the same call made
    # again may succeed; the caller decides whether to make it. Set only where a
    # client hands its retry to the caller: a Tradier option chain, which
    # CachedOptionsDataProvider re-prices on a new slot (tradier.CHAINS_PATH).
    retryable: bool = False

    def __str__(self):
        ctx = f" | {self.context}" if self.context else ""
        return f"{self.code.value}: {self.message}{ctx}"


T = TypeVar('T')
U = TypeVar('U')
E = TypeVar('E', bound=AppError)
F = TypeVar('F', bound=AppError)


@dataclass
class Ok(Generic[T]):
    """The success half of Result: a value, and no error."""

    value: T

    @property
    def is_ok(self) -> Literal[True]:
        return True

    @property
    def is_err(self) -> Literal[False]:
        return False

    @property
    def error(self) -> None:
        return None

    def unwrap(self) -> T:
        return self.value

    def unwrap_err(self) -> NoReturn:
        raise Exception("Result is Ok, not Err")

    def unwrap_or(self, default: object) -> T:
        return self.value

    def map(self, func: Callable[[T], U]) -> 'Result[U, AppError]':
        """
        Transform the value, catching what the function raises.

        Catches expected calculation errors (ValueError, TypeError, ArithmeticError, ZeroDivisionError)
        and converts them to CALCULATION errors. Logs unexpected exceptions as warnings before
        converting them.
        """
        try:
            return Ok(func(self.value))
        except (ValueError, TypeError, ArithmeticError, ZeroDivisionError) as e:
            # Expected calculation errors - convert to CALCULATION error
            return Err(AppError(ErrorCode.CALCULATION, str(e)))
        except Exception as e:
            # Unexpected error - log as warning before converting
            logger.warning(
                f"Unexpected error in Result.map: {type(e).__name__}: {str(e)}",
                exc_info=True
            )
            return Err(AppError(ErrorCode.CALCULATION, f"Unexpected error: {str(e)}"))

    def and_then(self, func: Callable[[T], 'Result[U, F]']) -> 'Result[U, F]':
        """Chain operations that return Results."""
        return func(self.value)


@dataclass
class Err(Generic[E]):
    """The failure half of Result: an error, and no value."""

    error: E

    @property
    def is_ok(self) -> Literal[False]:
        return False

    @property
    def is_err(self) -> Literal[True]:
        return True

    @property
    def value(self) -> None:
        return None

    def unwrap(self) -> NoReturn:
        raise Exception(str(self.error))

    def unwrap_err(self) -> E:
        return self.error

    def unwrap_or(self, default: U) -> U:
        return default

    def map(self, func: Callable[[Any], Any]) -> 'Err[E]':
        """Propagate the error; the function is never called."""
        return Err(self.error)

    def and_then(self, func: Callable[[Any], Any]) -> 'Err[E]':
        """Propagate the error; the function is never called."""
        return Err(self.error)


# Result[T, E] is either Ok(value) or Err(error), never both, and forces explicit
# error handling at call sites:
#
#     result = calculate_something()
#     if result.is_err:
#         return Err(result.error)      # result is Err[E] here
#     value = result.value              # and Ok[T] here: value is T, not T | None
#
# is_ok and is_err are literal True/False on each class, which is what lets a
# type checker narrow the union on them. Until 2026-09-14 Result was a single
# dataclass with `value: Optional[T]`, so no check narrowed anything and every
# `.value` read as possibly None. Build results with Ok(...) and Err(...);
# isinstance checks take (Ok, Err), since a subscripted Union is not a class.
Result: TypeAlias = Union[Ok[T], Err[E]]
