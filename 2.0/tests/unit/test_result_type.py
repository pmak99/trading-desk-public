"""Result is Ok[T] | Err[E]: `.value` is T only once the checker has narrowed it.

Until 2026-09-14 Result was one dataclass whose `value` was declared
`Optional[T]` and handed back as `T`, so mypy could not tell a checked result
from an unchecked one: `if result.is_err: return ...` narrowed nothing. Ok and
Err are now separate classes whose is_ok/is_err are literal True/False, so the
same check narrows the union -- which alone took `mypy src` from 213 errors to
102. Runtime behaviour is pinned here too, because every Result in the engine
goes through these few lines.
"""

import textwrap
from pathlib import Path

import pytest

from src.domain.errors import AppError, Err, ErrorCode, Ok, Result

ENGINE = Path(__file__).resolve().parents[2]


class TestRuntime:
    def test_ok_carries_a_value_and_no_error(self):
        r = Ok(42)
        assert (r.is_ok, r.is_err, r.value, r.error) == (True, False, 42, None)
        assert r.unwrap() == 42
        assert r.unwrap_or(0) == 42

    def test_ok_of_none_is_still_ok(self):
        r = Ok(None)
        assert r.is_ok and not r.is_err and r.value is None

    def test_err_carries_an_error_and_no_value(self):
        e = AppError(ErrorCode.NODATA, "nothing")
        r = Err(e)
        assert (r.is_ok, r.is_err, r.value, r.error) == (False, True, None, e)
        assert r.unwrap_err() is e
        assert r.unwrap_or(7) == 7

    def test_unwrapping_the_wrong_side_raises_what_it_always_raised(self):
        with pytest.raises(Exception, match="^NODATA: nothing$"):
            Err(AppError(ErrorCode.NODATA, "nothing")).unwrap()
        with pytest.raises(Exception, match="^Result is Ok, not Err$"):
            Ok(1).unwrap_err()

    def test_map_transforms_ok_and_passes_err_through(self):
        assert Ok(21).map(lambda x: x * 2) == Ok(42)
        e = AppError(ErrorCode.INVALID, "bad")
        assert Err(e).map(lambda x: x * 2) == Err(e)

    def test_map_turns_an_exception_into_a_calculation_error(self):
        expected = Ok(1).map(lambda x: x / 0)
        assert expected.is_err and expected.error.code == ErrorCode.CALCULATION
        assert expected.error.message == "division by zero"
        unexpected = Ok(1).map(lambda x: {}[x])
        assert unexpected.is_err and unexpected.error.code == ErrorCode.CALCULATION
        assert unexpected.error.message == "Unexpected error: 1"

    def test_and_then_chains_results(self):
        e = AppError(ErrorCode.INVALID, "bad")
        assert Ok(2).and_then(lambda x: Ok(x + 1)) == Ok(3)
        assert Ok(2).and_then(lambda x: Err(e)) == Err(e)
        assert Err(e).and_then(lambda x: Ok(x + 1)) == Err(e)

    def test_ok_and_err_never_compare_equal(self):
        assert Ok(None) != Err(None)

    def test_the_alias_is_subscriptable_at_runtime(self):
        """Annotations are evaluated at def time; Result[X, AppError] must not raise."""
        def f() -> Result[int, AppError]:
            return Ok(1)
        assert f().value == 1


NARROWING = textwrap.dedent('''
    from src.domain.errors import AppError, Err, ErrorCode, Ok, Result

    def get() -> Result[int, AppError]:
        return Ok(1)

    def early_return() -> Result[int, AppError]:
        r = get()
        if r.is_err:
            reveal_type(r.error)
            return Err(r.error)
        reveal_type(r.value)
        return Ok(r.value + 1)

    def negated() -> int:
        r = get()
        if not r.is_ok:
            return 0
        return r.value + 1

    def unchecked() -> int:
        r = get()
        return r.value + 1
''')


def test_the_checker_narrows_on_is_ok_and_is_err(tmp_path):
    api = pytest.importorskip("mypy.api")
    probe = tmp_path / "probe.py"
    probe.write_text(NARROWING)
    out, err, _ = api.run([
        str(probe), "--strict-optional", "--no-incremental",
        "--cache-dir", str(tmp_path / ".mypy_cache"),
        "--config-file", str(ENGINE / "pyproject.toml"),
    ])
    lines = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith(str(probe))]
    assert 'note: Revealed type is "src.domain.errors.AppError"' in lines, out + err
    assert 'note: Revealed type is "int"' in lines, out + err
    # The only error: the result nobody checked, where value may be None.
    errors = [line for line in lines if line.startswith("error:")]
    assert errors == ['error: Unsupported operand types for + ("None" and "int")  [operator]'], out + err
