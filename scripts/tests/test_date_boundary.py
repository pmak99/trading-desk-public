"""The date type boundary between Postgres and the SQLite-era code.

This file exists because of a defect CLASS, not a bug. Postgres returns
`datetime.date` for a date column where SQLite returned a string, so every
`datetime.strptime(row[i], "%Y-%m-%d")` inherited from the SQLite era raises
`TypeError: strptime() argument 1 must be str`. Nine sites were found across
the port, on live paths including /harvest, /journal and store_bias_prediction.

Two things made it dangerous rather than merely broken:

  * Most sites were wrapped in `except ValueError`, which does NOT catch
    TypeError -- so they crash rather than degrade.
  * The two that DID catch TypeError returned None, which is worse: a silent
    wrong answer from a correlation the trader reads.

`common.db.as_date` is the single coercion point. These tests pin its
contract so a future caller cannot reintroduce a private copy that drifts.
"""
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from common.db import as_date  # noqa: E402


class TestAsDate:
    def test_a_date_passes_through(self):
        """What Postgres hands back for a date column."""
        assert as_date(date(2026, 9, 12)) == date(2026, 9, 12)

    def test_a_datetime_is_truncated(self):
        """timestamptz columns arrive as datetime; the date half is what
        earnings-date comparisons want."""
        assert as_date(datetime(2026, 9, 12, 21, 30)) == date(2026, 9, 12)

    def test_an_iso_string_still_works(self):
        """The CSV/journal path still hands over strings, and must keep
        working -- this is a widening, not a replacement."""
        assert as_date("2026-09-12") == date(2026, 9, 12)

    def test_an_iso_timestamp_string_works(self):
        assert as_date("2026-09-12T10:30:00.123456") == date(2026, 9, 12)

    def test_none_is_none_not_an_exception(self):
        """A nullable column must not raise -- several call sites feed
        optional dates straight in."""
        assert as_date(None) is None

    def test_garbage_is_none_not_an_exception(self):
        assert as_date("not a date") is None
        assert as_date(42) is None

    def test_never_raises(self):
        """The whole point: no caller should need a try block. The sites this
        replaced were wrapped in `except ValueError`, which would not have
        caught the TypeError that Postgres values actually produce."""
        for v in [None, "", "x", 0, 3.14, [], {}, object(), date.today(),
                  datetime.now(), "2026-13-45"]:
            as_date(v)   # must not raise


def test_there_is_exactly_one_definition():
    """Three private copies existed at one point (strategy_grouper,
    regroup_strategies, and the sites that inlined strptime). One definition
    is the fix; a private copy is how they drift apart again."""
    import subprocess
    out = subprocess.run(
        ["grep", "-rn", "def _as_date\\|def as_date", "--include=*.py",
         str(ROOT / "2.0" / "scripts"), str(ROOT / "scripts"),
         str(ROOT / "common"), str(ROOT / "2.0" / "src")],
        capture_output=True, text=True).stdout.strip().splitlines()
    defs = [line for line in out if "/venv/" not in line and "__pycache__" not in line
            and "/tests/" not in line]   # this file names the pattern in a literal
    assert len(defs) == 1, "expected one definition of as_date, found:\n" + "\n".join(defs)
    assert "common/db.py" in defs[0]
