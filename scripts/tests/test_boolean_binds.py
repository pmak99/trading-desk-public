"""No writer may bind an integer to a boolean column.

Migration 2 (2026-09-13) turned seven 0/1 integer columns into booleans, and
Postgres rejects `1`/`0` for them. Two writers were missed by the sweep that
followed -- the journal import (`is_winner`) and the bias validator
(`prediction_correct = 1 if correct else 0`) -- and both failed at runtime
while every read path stayed green, because nothing executed the write.

A per-writer pg_schema test is the real guarantee. This is the cheap net under
it: a static scan for the two shapes the missed writers had.

  1. `1 if x else 0` (or `0 if x else 1`) inside an `execute(...)` call.
  2. `<boolean column> = 0|1` inside a SQL string, in code or in the slash
     command files, which nothing else executes.

The boolean columns are read from the baseline schema, so a new one is covered
the day it is added.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "2.0" / "sql" / "baseline_schema.sql"

SCANNED_DIRS = ["2.0/src", "2.0/scripts", "2.0/tests", "scripts", "common", "tests"]
SKIP_PARTS = {"venv", ".venv", "site-packages", "__pycache__", "node_modules"}
EXECUTE_NAMES = {"execute", "executemany"}


def boolean_columns() -> set[str]:
    return set(re.findall(r'"(\w+)"\s+boolean\b', BASELINE.read_text()))


def _literal_pattern(columns: set[str]) -> re.Pattern:
    names = "|".join(sorted(columns))
    return re.compile(rf"\b({names})\s*(=|<>|!=)\s*[01]\b")


def _is_zero_one(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value in (0, 1)


def find_integer_boolean_binds(source: str, columns: set[str]) -> list[tuple[int, str]]:
    """(line, reason) for every suspect bind in one Python source file."""
    tree = ast.parse(source)
    literal = _literal_pattern(columns)
    # Docstrings and other bare string statements are prose, not SQL.
    prose = {id(n.value) for n in ast.walk(tree)
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    hits = []
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in EXECUTE_NAMES:
                for arg in [*node.args, *(k.value for k in node.keywords)]:
                    for sub in ast.walk(arg):
                        if (isinstance(sub, ast.IfExp)
                                and _is_zero_one(sub.body) and _is_zero_one(sub.orelse)):
                            hits.append((sub.lineno, "0/1 conditional bound in execute()"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = literal.search(node.value)
            if m:
                hits.append((node.lineno, f"SQL literal {m.group(0)!r} on a boolean column"))
    return hits


def _python_files():
    for d in SCANNED_DIRS:
        for path in (REPO / d).rglob("*.py"):
            if SKIP_PARTS.isdisjoint(path.parts) and path.resolve() != Path(__file__).resolve():
                yield path


def test_the_scan_catches_the_bias_validator_bug():
    """The exact shape that shipped, so the scan is known to see it."""
    shipped = (
        "cursor.execute('UPDATE bias_predictions SET prediction_correct = %s WHERE id = %s',\n"
        "               (1 if correct else 0, prediction_id))\n"
    )
    assert find_integer_boolean_binds(shipped, {"prediction_correct"})


def test_the_scan_catches_a_sql_literal():
    shipped = "conn.execute(\"SELECT count(*) FROM strategies WHERE is_winner = 1\")\n"
    assert find_integer_boolean_binds(shipped, {"is_winner"})


def test_the_scan_ignores_python_keywords_and_in_memory_flags():
    ok = (
        "_insert(db, 'AAPL', d, confirmed=1)\n"
        "e['crush'] = 1 if e['impl'] > 3 else 0\n"
        "cursor.execute('UPDATE t SET prediction_correct = %s', (correct,))\n"
    )
    assert find_integer_boolean_binds(ok, {"confirmed", "prediction_correct"}) == []


def test_no_python_writer_binds_an_integer_to_a_boolean():
    columns = boolean_columns()
    assert columns, "baseline schema has no boolean columns -- the parse is broken"
    hits = [
        f"{path.relative_to(REPO)}:{line}: {reason}"
        for path in _python_files()
        for line, reason in find_integer_boolean_binds(path.read_text(), columns)
    ]
    assert hits == [], "\n".join(hits)


def test_no_command_file_compares_a_boolean_column_to_an_integer():
    literal = _literal_pattern(boolean_columns())
    hits = [
        f"{path.relative_to(REPO)}:{n}: {line.strip()}"
        for path in sorted((REPO / ".claude" / "commands").glob("*.md"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if literal.search(line)
    ]
    assert hits == [], "\n".join(hits)
