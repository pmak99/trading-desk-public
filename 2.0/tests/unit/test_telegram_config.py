"""Telegram configuration section.

Telegram lived only in 5.0 until Sep 2026; 2.0 had no notification config at
all. These tests pin the two properties the job runner depends on: the values
come from the environment, and their ABSENCE is not an error — every existing
CLI path constructs Config.from_env() and must keep working on a machine with
no Telegram credentials.

Note the empty env_file: Config.from_env() calls load_dotenv() on 2.0/.env by
default, which now carries real TELEGRAM_* values, so a test asserting absence
has to be pointed at a file that does not define them or it silently tests
nothing.
"""

import pytest

from src.config.config import Config, TelegramConfig


@pytest.fixture
def empty_env(tmp_path):
    """An .env with no TELEGRAM_* keys, so absence is actually testable."""
    p = tmp_path / "empty.env"
    p.write_text("# no telegram keys here\n")
    return str(p)


def test_telegram_config_reads_env(monkeypatch, empty_env):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "8328755835")

    config = Config.from_env(env_file=empty_env)

    assert config.telegram.bot_token == "123:abc"
    assert config.telegram.chat_id == "8328755835"


def test_telegram_config_absent_is_none(monkeypatch, empty_env):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    config = Config.from_env(env_file=empty_env)

    assert config.telegram.bot_token is None
    assert config.telegram.chat_id is None


def test_is_configured_requires_both(monkeypatch, empty_env):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    config = Config.from_env(env_file=empty_env)

    assert config.telegram.bot_token == "123:abc"
    assert config.telegram.is_configured is False


def test_is_configured_true_when_both_present():
    assert TelegramConfig(bot_token="t", chat_id="c").is_configured is True


def test_empty_string_is_treated_as_absent():
    """A key present-but-blank in .env must not read as configured."""
    assert TelegramConfig(bot_token="", chat_id="").is_configured is False
