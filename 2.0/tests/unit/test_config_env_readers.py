"""Vendor keys and the ORATS switch are read from the environment in one place.

Audit step 5.4 (2026-09-14). `ORATS_ENABLED` was parsed in common/constants.py at
import time and defaulted to ENABLED when unset, a year after the subscription
died: a machine without the line in its .env would read the frozen 2026-06-24
position_limits snapshot into sizing. And six script sites read vendor keys with
their own `os.getenv(...)`, so a key's name lived in as many places as it had
readers. Now `APIConfig.from_env()` is the reader, `Config.from_env()` builds its
API section from it, and this file fails on a read anywhere else.

APIConfig.from_env() reads the environment as it is at the call, without loading
2.0/.env: library code that needs a key at call time (the Twelve Data fetch,
record_moves' Finnhub gate, TACO's fallbacks) uses it where a whole
Config.from_env() would also refill a variable the caller had removed.
"""

import re
from pathlib import Path

import pytest

from src.config.config import APIConfig, Config  # noqa: E402

REPO = Path(__file__).resolve().parents[3]

# Every environment variable holding a vendor credential, plus the ORATS switch.
READ_ONLY_IN_CONFIG = (
    "TRADIER_API_KEY", "FINNHUB_API_KEY", "TWELVE_DATA_KEY", "ORATS_ENABLED",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PERPLEXITY_API_KEY", "ORATS_API_KEY",
)
CONFIG_MODULE = REPO / "2.0" / "src" / "config" / "config.py"


@pytest.fixture
def empty_env(tmp_path):
    """A .env defining none of these, so Config.from_env()'s load_dotenv() of the
    real 2.0/.env cannot put a value back (see test_telegram_config.py)."""
    p = tmp_path / "empty.env"
    p.write_text("# nothing here\n")
    return str(p)


def test_orats_is_off_when_the_variable_is_unset(monkeypatch, empty_env):
    monkeypatch.delenv("ORATS_ENABLED", raising=False)
    assert Config.from_env(env_file=empty_env).api.orats_enabled is False
    assert APIConfig.from_env().orats_enabled is False
    assert APIConfig(tradier_api_key="").orats_enabled is False


@pytest.mark.parametrize("value, expected", [
    ("true", True), ("True", True), ("TRUE", True),
    ("false", False), ("", False), ("yes", False), ("1", False),
])
def test_only_true_turns_orats_on(monkeypatch, empty_env, value, expected):
    monkeypatch.setenv("ORATS_ENABLED", value)
    assert Config.from_env(env_file=empty_env).api.orats_enabled is expected


def test_the_orats_switch_left_common_constants():
    import common.constants as constants
    assert not hasattr(constants, "ORATS_ENABLED")


def test_keys_are_read_when_asked_not_at_import(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_KEY", "first")
    monkeypatch.setenv("FINNHUB_API_KEY", "fh")
    monkeypatch.setenv("TRADIER_API_KEY", "tr")
    assert (APIConfig.from_env().twelve_data_key, APIConfig.from_env().finnhub_key,
            APIConfig.from_env().tradier_api_key) == ("first", "fh", "tr")
    monkeypatch.setenv("TWELVE_DATA_KEY", "second")
    assert APIConfig.from_env().twelve_data_key == "second"
    monkeypatch.delenv("TWELVE_DATA_KEY")
    assert APIConfig.from_env().twelve_data_key == ""


def test_config_takes_its_api_section_from_the_one_reader(monkeypatch, empty_env):
    monkeypatch.setenv("TWELVE_DATA_KEY", "td")
    monkeypatch.setenv("ORATS_ENABLED", "true")
    assert Config.from_env(env_file=empty_env).api == APIConfig.from_env()


def test_the_key_values_never_appear_in_the_config_repr(monkeypatch, empty_env):
    """A logged Config (or a traceback's locals) must not carry a credential."""
    monkeypatch.setenv("TRADIER_API_KEY", "tradier-secret-123")
    monkeypatch.setenv("FINNHUB_API_KEY", "finnhub-secret-456")
    monkeypatch.setenv("TWELVE_DATA_KEY", "twelve-secret-789")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:telegram-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-id-000")
    config = Config.from_env(env_file=empty_env)
    secrets = ("tradier-secret-123", "finnhub-secret-456", "twelve-secret-789",
               "telegram-secret")
    # Compared as a list, so a failure names the leaked test value and never
    # prints the text around it.
    for text in (repr(config), str(config), repr(config.api), repr(config.telegram)):
        assert [s for s in secrets if s in text] == []


_DIRECT_READ = re.compile(
    r"""(?:getenv|environ\.get|environ\.pop|environ\[|setdefault)\s*\(?\s*["']({})["']""".format(
        "|".join(READ_ONLY_IN_CONFIG)))
# A read through a variable: `for k in ("TWELVE_DATA_KEY", "FINNHUB_API_KEY"):
# os.environ.get(k)` -- two of the names side by side. A name next to a value
# (`("TWELVE_DATA_KEY", api.twelve_data_key)`) is a label and passes.
_NAME_IN_A_COLLECTION = re.compile(
    r"""["']({0})["']\s*,\s*["']({0})["']""".format("|".join(READ_ONLY_IN_CONFIG)))


def _application_sources():
    for root in ("2.0/src", "2.0/scripts", "common", "scripts"):
        for path in sorted((REPO / root).rglob("*.py")):
            if {"venv", "tests"} & set(path.parts) or path == CONFIG_MODULE:
                continue
            yield path


def test_no_module_but_config_reads_a_key_or_the_orats_switch():
    offenders = []
    for path in _application_sources():
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if _DIRECT_READ.search(line) or _NAME_IN_A_COLLECTION.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert offenders == [], (
        "read vendor keys and ORATS_ENABLED through src/config/config.py "
        "(config.api.*, or APIConfig.from_env() where no Config is in hand)")


def test_the_scan_sees_the_shapes_it_is_meant_to_refuse():
    for line in ('key = os.getenv("TWELVE_DATA_KEY", "")',
                 "os.environ.get('FINNHUB_API_KEY')",
                 'os.environ["TRADIER_API_KEY"]',
                 'REQUIRED_KEYS = ("TWELVE_DATA_KEY", "FINNHUB_API_KEY")',
                 'ORATS_ENABLED: bool = os.getenv("ORATS_ENABLED", "true").lower() == "true"'):
        assert _DIRECT_READ.search(line) or _NAME_IN_A_COLLECTION.search(line), line
    for line in ('raise TwelveDataError("TWELVE_DATA_KEY not set")',
                 '# ORATS_ENABLED=false everywhere',
                 'for name, key in (("TWELVE_DATA_KEY", api.twelve_data_key),'):
        assert not (_DIRECT_READ.search(line) or _NAME_IN_A_COLLECTION.search(line)), line
