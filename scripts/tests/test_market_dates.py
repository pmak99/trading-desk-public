"""The trading day is the date in New York, never the host's.

the server's clock is UTC. From 20:00 ET (19:00 in winter) to midnight its
`date.today()` is already tomorrow, and a naive `datetime.now()` is four or five
hours ahead of the market all day. Until 2026-09-14, 30 `date.today()` and 21
naive `datetime.now()` calls in the engine, scripts and common/ used the host
clock -- identical on the ET-local Mac, a day late on the server for the Telegram
bot's evening /whisper and /analyze and for weekly maintenance, which starts at
23:00 ET on Sunday (Monday in UTC).

`common.timezone.now_et()` / `today_et()` are the clock; code under 2.0/src
reads the same instant through `src.domain.types.now_et()`, which src/ can
import before anything has put common/ on sys.path. This file pins the helpers
at the evening boundary and fails on any new host-clock call in scanned code.
The allow-list holds the uses where the host's own clock is the point.
"""

import ast
from datetime import date, datetime, timezone
from pathlib import Path

import common.timezone as tz
import src.domain.types as domain_types
from common.timezone import now_et, today_et
from src.domain.market_calendar import get_market_status

REPO = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ["2.0/src", "2.0/scripts", "common", "scripts"]
SKIP_PARTS = {"tests", "venv", ".venv", "site-packages", "__pycache__", "node_modules"}

# (path, the call's source line) -> why the host clock is right there.
_EXPORT_DATE = ("stamps the export with the host date /export-report hands `trade.sh scan` "
                "(`date +%Y-%m-%d`); an ET date here could disagree with the scan it labels")
ALLOWED = {
    ("scripts/parse_fidelity_csv.py", "'generated_at': datetime.now().isoformat(),"):
        "export metadata: when this machine wrote the JSON file, not a market date",
    ("scripts/export_scan_results.py", "'scan_date': datetime.now().strftime('%Y-%m-%d')"): _EXPORT_DATE,
    ("scripts/export_scan_results.py", "'scan_date': datetime.now().strftime('%Y-%m-%d'),"): _EXPORT_DATE,
    ("scripts/export_scan_results.py", "date_str = datetime.now().strftime('%Y%m%d')"): _EXPORT_DATE,
}


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def find_host_clock_calls(source: str) -> list[tuple[int, str]]:
    """(line, call) for every date/datetime read of the host clock in one file.

    `datetime.now().timestamp()` is not one: an epoch is the same instant
    whatever the host's zone.
    """
    tree = ast.parse(source)
    epoch = {
        id(n.func.value) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "timestamp"
    }
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = _dotted(node.func.value).split(".")[-1]
        name = node.func.attr
        if owner not in {"date", "datetime", "dt", "_dt", "Timestamp"}:
            continue
        naive = not node.args and not node.keywords
        if (name == "today" and naive) or name == "utcnow" or (name == "now" and naive):
            if id(node) not in epoch:
                hits.append((node.lineno, node.col_offset, f"{owner}.{name}()"))
    return [(line, call) for line, _, call in sorted(hits)]


def _python_files():
    for d in SCANNED_DIRS:
        for path in sorted((REPO / d).rglob("*.py")):
            if SKIP_PARTS.isdisjoint(path.relative_to(REPO).parts):
                yield path


class _UtcHost(datetime):
    """A host whose clock reads UTC, frozen at one instant."""

    instant = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


def test_after_8pm_edt_the_trading_day_is_still_today(monkeypatch):
    monkeypatch.setattr(tz, "datetime", _UtcHost)
    monkeypatch.setattr(domain_types, "datetime", _UtcHost)
    assert _UtcHost.now().date() == date(2026, 9, 15)   # what date.today() said on the server
    assert today_et() == date(2026, 9, 14)
    assert (now_et().date(), now_et().hour) == (date(2026, 9, 14), 21)
    assert now_et() == now_et()


def test_after_7pm_est_the_trading_day_is_still_today(monkeypatch):
    monkeypatch.setattr(_UtcHost, "instant", datetime(2026, 12, 15, 0, 30, tzinfo=timezone.utc))
    monkeypatch.setattr(tz, "datetime", _UtcHost)
    assert today_et() == date(2026, 12, 14)
    assert get_market_status() == (False, "After Hours")  # Monday evening, not Tuesday pre-market
    monkeypatch.setattr(_UtcHost, "instant", datetime(2026, 12, 25, 0, 30, tzinfo=timezone.utc))
    # Christmas Eve's early close in New York, not Christmas Day in UTC.
    assert get_market_status() == (False, "After Hours (early close 13:00)")


def test_the_scan_sees_each_host_clock_shape():
    shipped = (
        "today = date.today()\n"
        "d = _dt.date.today()\n"
        "now = now or datetime.now()\n"
        "stamp = datetime.utcnow()\n"
        "ts = pd.Timestamp.now()\n"
    )
    assert [c for _, c in find_host_clock_calls(shipped)] == [
        "date.today()", "date.today()", "datetime.now()", "datetime.utcnow()", "Timestamp.now()"]


def test_the_scan_passes_zone_aware_reads_and_epochs():
    ok = (
        "a = datetime.now(ET)\n"
        "b = datetime.now(tz=timezone.utc)\n"
        "c = datetime.now().timestamp()\n"
        "d = today_et()\n"
        "e = date.fromisoformat('2026-09-14')\n"
    )
    assert find_host_clock_calls(ok) == []


def test_no_scanned_code_reads_the_host_clock():
    hits = []
    for path in _python_files():
        source = path.read_text()
        lines = source.splitlines()
        rel = str(path.relative_to(REPO))
        for line, call in find_host_clock_calls(source):
            hits.append(((rel, lines[line - 1].strip()), f"{rel}:{line}: {call}"))
    unexplained = [where for key, where in hits if key not in ALLOWED]
    assert unexplained == [], (
        "use common.timezone.today_et()/now_et() (now_et() in 2.0/src), "
        "or allow-list a genuinely host-local use:\n"
        + "\n".join(unexplained))
    stale = [key for key in ALLOWED if key not in {key for key, _ in hits}]
    assert stale == [], f"allow-list entries that no longer match a call: {stale}"


def test_now_et_is_the_only_market_clock():
    """One New York zone and one clock: common.timezone. A pytz zone used as
    `tzinfo=` silently applies New York's 1883 offset (-4:56), and each copy of the
    zone was another place a naive read could slip in."""
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    zone = re.compile(r"""(ZoneInfo|pytz\.timezone)\(\s*["']America/New_York["']""")
    allowed = {"common/timezone.py", "2.0/scripts/migrate_to_postgres.py"}  # the clock; a one-off loader
    copies = []
    for root in ("2.0/src", "2.0/scripts", "common", "scripts"):
        for path in (repo / root).rglob("*.py"):
            rel = path.relative_to(repo).as_posix()
            if "/tests/" in f"/{rel}" or rel in allowed:
                continue
            if zone.search(path.read_text()):
                copies.append(rel)
    assert copies == []
