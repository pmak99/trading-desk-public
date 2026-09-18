"""Container() has no side effects; the entry points that write migrate, once.

Audit step 5.5 (2026-09-14). `Container.__init__` ran `MigrationManager.migrate()`,
so building one -- in a test, an MCP server, a read-only health check -- applied
pending migrations to whatever PGDATABASE named and wrote checksums into its
`schema_migrations`. Every local test run that built a Container did that to
production. Now construction touches no database, and each process that writes
through the engine calls `apply_migrations()` itself, once, before its work:

    entry point                         migrates   why
    scripts/jobs/telegram_bot.py        yes        long-lived; the first process a deploy restarts
    scripts/jobs/morning_digest.py      yes        writes (calendar rows, analysis_log, job_status)
    scripts/jobs/record_moves.py        --execute  writes moves and retags; report-only writes nothing
    scripts/scan.py (trade.sh scan...)  yes        writes analysis_log, auto-backfills
    scripts/analyze.py (trade.sh TKR)   yes        writes analysis_log
    scripts/store_bias_prediction.py    yes        writes bias_predictions (trade.sh TKR runs it first)
    scripts/health_check.py             no         reads; `trade.sh health` must not change the schema
    scripts/track_iv_weekly.py          no         Container only for the Tradier client; runs inside
                                                   /maintenance, after that run's migrate step
    scripts/mcp/finnhub_server.py       no         Container only for the Finnhub client; no writes

`test_every_container_constructor_is_classified` holds the table to the code.
"""

import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

ENGINE = Path(__file__).resolve().parents[2]
REPO = ENGINE.parent

from src.config.config import Config  # noqa: E402
from src.container import Container  # noqa: E402

MIGRATES = {
    "2.0/scripts/jobs/telegram_bot.py",
    "2.0/scripts/jobs/morning_digest.py",
    "2.0/scripts/jobs/record_moves.py",
    "2.0/scripts/scan.py",
    "2.0/scripts/analyze.py",
    "2.0/scripts/store_bias_prediction.py",
}
BUILDS_A_CONTAINER_WITHOUT_MIGRATING = {
    "2.0/scripts/health_check.py": "reads only",
    "2.0/scripts/track_iv_weekly.py": "Tradier client only; /maintenance migrated first",
    "scripts/mcp/finnhub_server.py": "Finnhub client only; writes nothing",
}


# --- construction -------------------------------------------------------------

@pytest.fixture
def no_pool():
    from src.infrastructure.database import pg_pool
    pg_pool.close_pool()
    yield pg_pool
    pg_pool.close_pool()


def test_constructing_a_container_opens_no_connection(no_pool, monkeypatch):
    import psycopg

    def refuse(*args, **kwargs):
        raise AssertionError("Container construction opened a database connection")

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(refuse))
    container = Container(Config.from_env(), skip_validation=True)
    # The lazily built pieces that need no network or database.
    for name in ("tradier", "finnhub", "cache", "cached_options_provider",
                 "liquidity_scorer", "vrp_calculator", "strategy_generator", "analyzer"):
        getattr(container, name)
    assert no_pool._pool is None


@pytest.mark.pg
def test_a_pending_migration_stays_pending_until_the_entry_point_applies_it(
        pg_schema, tmp_path, monkeypatch, no_pool):
    """On a database with a migration waiting: Container() leaves it, and
    schema_migrations, untouched; apply_migrations() applies it."""
    from common.db import connect
    from src.infrastructure.database.migrations import apply_migrations
    from src.infrastructure.database.migrations import migration_manager as mm

    monkeypatch.setattr(mm, "MIGRATIONS_DIR", tmp_path)
    pending = mm.BASELINE_VERSION + 1
    (tmp_path / f"{pending:03d}_widgets.sql").write_text("CREATE TABLE widget (id int);\n")

    def present():
        with connect() as conn:
            return conn.execute(
                "SELECT to_regclass('schema_migrations') IS NOT NULL AS migrations_table,"
                "       to_regclass('widget') IS NOT NULL AS widget").fetchone()

    Container(Config.from_env(), skip_validation=True)
    assert present() == (False, False)

    assert apply_migrations() == 1
    assert present() == (True, True)
    with connect() as conn:
        versions = [r.version for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [mm.BASELINE_VERSION, pending]
    no_pool.close_pool()                  # before pg_schema drops the schema it points at


@pytest.mark.pg
def test_the_repos_migrations_leave_an_empty_database_at_the_baseline(pg_schema, no_pool):
    """What every CI Container() used to exercise by accident: on an empty
    database the real migrations directory seeds BASELINE_VERSION and finds
    nothing to replay onto the baseline."""
    from common.db import connect
    from src.infrastructure.database.migrations import apply_migrations
    from src.infrastructure.database.migrations.migration_manager import (
        BASELINE_VERSION,
    )

    assert apply_migrations() == 0
    with connect() as conn:
        assert [r.version for r in conn.execute("SELECT version FROM schema_migrations")] \
            == [BASELINE_VERSION]
    no_pool.close_pool()


def test_an_edited_applied_migration_stops_the_caller_loudly(monkeypatch, caplog):
    from src.infrastructure.database.migrations import migration_manager as mm

    class Manager:
        def __init__(self, conn):
            pass

        def migrate(self):
            raise RuntimeError("migration 2 (schema_audit_2026_09) changed after it was applied")

    pool = MagicMock()
    monkeypatch.setattr(mm, "MigrationManager", Manager)
    with patch("src.infrastructure.database.pg_pool.get_pool", return_value=pool):
        with pytest.raises(RuntimeError, match="changed after it was applied"):
            mm.apply_migrations()
    assert any(r.levelname == "ERROR" and "changed after it was applied" in r.getMessage()
               for r in caplog.records)


# --- injection replaces with_mock_tradier -----------------------------------------

def test_tradier_and_cache_are_injected_through_the_constructor():
    from src.infrastructure.cache.memory_cache import MemoryCache

    tradier, cache = Mock(), MemoryCache()
    container = Container(Config.from_env(), skip_validation=True, tradier=tradier, cache=cache)
    assert container.tradier is tradier
    assert container.cache is cache
    assert container.cached_options_provider.provider is tradier
    assert container.market_conditions_analyzer.provider is tradier


def test_the_global_container_and_the_mock_setters_are_gone():
    import src.container as module

    for name in ("get_container", "reset_container", "_container"):
        assert not hasattr(module, name), name
    for name in ("with_mock_tradier", "with_mock_cache", "create_from_env", "_run_migrations"):
        assert not hasattr(Container, name), name


# --- which entry points migrate -------------------------------------------------

_CONSTRUCTS = re.compile(r"(?<![\w.])Container\(")
_MIGRATES = re.compile(r"(?<![\w.])apply_migrations\(\)")


def _sources():
    """Application code, without the container and the migrations package (the
    two definitions, whose docstrings name both calls)."""
    for root in ("2.0/scripts", "2.0/src", "scripts", "common"):
        for path in sorted((REPO / root).rglob("*.py")):
            rel = str(path.relative_to(REPO))
            if {"venv", "tests"} & set(path.parts) or rel == "2.0/src/container.py" \
                    or rel.startswith("2.0/src/infrastructure/database/migrations/"):
                continue
            yield rel, path.read_text()


def test_every_container_constructor_is_classified():
    constructs = {rel for rel, text in _sources() if _CONSTRUCTS.search(text)}
    assert constructs == (MIGRATES - {"2.0/scripts/jobs/record_moves.py"}) | set(
        BUILDS_A_CONTAINER_WITHOUT_MIGRATING)

    calls = {rel: len(_MIGRATES.findall(text)) for rel, text in _sources()}
    assert {rel for rel, n in calls.items() if n} == MIGRATES
    assert all(calls[rel] == 1 for rel in MIGRATES), {rel: calls[rel] for rel in MIGRATES}


def _counter():
    calls = []
    return calls, (lambda: calls.append(1) or 0)


def test_the_bot_migrates_once_before_it_builds_the_container():
    from scripts.jobs import telegram_bot as bot

    order = []
    config = Mock()
    config.telegram.is_configured = True
    with patch.object(bot.Config, "from_env", return_value=config), \
         patch("src.infrastructure.database.migrations.apply_migrations",
               side_effect=lambda: order.append("migrate")), \
         patch("src.container.Container", side_effect=lambda c: order.append("container")), \
         patch("scripts.jobs.telegram_commands.poll_forever"):
        assert bot.main() == 0
    assert order == ["migrate", "container"]


def test_the_bot_does_not_touch_the_database_when_telegram_is_not_configured():
    from scripts.jobs import telegram_bot as bot

    config = Mock()
    config.telegram.is_configured = False
    with patch.object(bot.Config, "from_env", return_value=config), \
         patch("src.infrastructure.database.migrations.apply_migrations") as migrate:
        assert bot.main() == 1
    migrate.assert_not_called()


@pytest.mark.parametrize("argv", [["--dry-run"], []])
def test_the_digest_migrates_once(argv):
    from datetime import date

    from scripts.jobs import morning_digest as digest

    calls, migrate = _counter()
    sender = Mock()
    sender.send.return_value = True
    with patch.object(digest, "apply_migrations", side_effect=migrate), \
         patch.object(digest, "Container"), \
         patch.object(digest, "_week_tickers", return_value=(["AAPL"], date(2026, 9, 14))), \
         patch.object(digest, "build_digest_text", return_value="digest"), \
         patch.object(digest, "run_job", side_effect=lambda name, fn, **kw: (fn(), 0)[1]):
        assert digest.main(argv, sender=sender) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("argv, expected", [(["--execute"], 1), ([], 0)])
def test_record_moves_migrates_once_and_only_when_it_writes(monkeypatch, argv, expected):
    from scripts.jobs import record_moves as rm

    monkeypatch.setenv("TWELVE_DATA_KEY", "k")
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.delenv("RECORD_MOVES_PUSH_URL", raising=False)
    calls, migrate = _counter()
    with patch.object(rm, "apply_migrations", side_effect=migrate), \
         patch.object(rm, "connect"), \
         patch.object(rm, "record_moves", return_value=Mock()), \
         patch.object(rm, "format_summary", return_value=""), \
         patch.object(rm, "notify"), \
         patch.object(rm, "counts_line", return_value=""), \
         patch.object(rm, "run_job", side_effect=lambda name, fn, **kw: (fn(), 0)[1]):
        assert rm.main(argv, sender=Mock()) == 0
    assert len(calls) == expected


def _load_script(name):
    """A 2.0/scripts entry file as a module, the way `python scripts/<name>.py` runs it."""
    scripts = str(ENGINE / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(f"_entry_{name}", ENGINE / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scan_migrates_once(monkeypatch):
    scan = _load_script("scan")
    calls, migrate = _counter()
    args = Mock(harvest=False, scan_date=None, whisper_week="", sequential=False,
                expiration_offset=0, skip_weekly_filter=False, log_level="WARNING")
    with patch.object(scan, "parse_args", return_value=args), \
         patch.object(scan, "setup_logging"), \
         patch.object(scan, "register_shutdown_callback"), \
         patch.object(scan, "apply_migrations", side_effect=migrate), \
         patch.object(scan, "Container"), \
         patch.object(scan, "whisper_mode", return_value=0):
        assert scan.main() == 0
    assert len(calls) == 1


def test_analyze_migrates_once(monkeypatch):
    analyze = _load_script("analyze")
    calls, migrate = _counter()
    container = Mock()
    container.analyzer.analyze.return_value = Mock(is_err=True, error="stop here")
    monkeypatch.setattr(sys, "argv", ["analyze.py", "NVDA", "--earnings-date", "2026-11-19",
                                      "--expiration", "2026-11-20"])
    with patch.object(analyze, "setup_logging"), \
         patch.object(analyze, "apply_migrations", side_effect=migrate), \
         patch.object(analyze, "Container", return_value=container):
        assert analyze.main() == 1
    assert len(calls) == 1
    container.analyzer.analyze.assert_called_once()


def test_store_bias_prediction_migrates_once(monkeypatch):
    import scripts.store_bias_prediction as sbp
    from src.domain.errors import Ok

    calls, migrate = _counter()
    monkeypatch.setattr(sys, "argv", ["store_bias_prediction.py", "--all"])
    container = Mock()
    container.earnings_repository.get_upcoming_earnings.return_value = Ok([])
    with patch.object(sbp, "setup_logging"), \
         patch.object(sbp, "apply_migrations", side_effect=migrate), \
         patch.object(sbp, "Container", return_value=container):
        assert sbp.main() == 1                       # no events: nothing to analyze
    assert len(calls) == 1


@pytest.mark.parametrize("name", ["health_check", "track_iv_weekly"])
def test_the_read_only_entry_points_import_no_migrator(name):
    text = (ENGINE / "scripts" / f"{name}.py").read_text()
    assert "apply_migrations" not in text and "MigrationManager" not in text
