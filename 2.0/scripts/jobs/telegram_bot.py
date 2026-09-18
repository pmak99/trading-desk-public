"""Inbound Telegram bot (long-polling) — /whisper and /analyze on the authoritative engine.

This module is the process: it starts the bot, and Telegrams a start that fails. The
commands and the poll loop are scripts/jobs/telegram_commands.py.

Its top level imports Config -- the one reader of the bot token -- and nothing else
first-party. `python -m` runs a module's imports before main(), so an engine import
failing here would exit with nothing but a journal line; imported inside main(), it is
alerted like any other failed start. tests/unit/test_telegram_bot.py holds the module
to that.

Run:
    PYTHONPATH=<2.0> python -m scripts.jobs.telegram_bot
"""

import hashlib
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config.config import Config  # noqa: E402

logger = logging.getLogger(__name__)

# Hash of the start failure last sent to Telegram; see _alert_start_failure.
START_FAILURE_FILE = _ROOT / "data" / ".telegram_start_failure"


def _alert_start_failure(config: Config, error: Exception) -> None:
    """Telegram a failed start -- once per distinct error.

    The process exits and systemd restarts it every 10 s for as long as the cause
    lasts, so the only record used to be the journal, a line per attempt. The
    marker holds a hash of the error last delivered: the same error on a later
    start sends nothing, a different one sends, and a start that reaches polling
    removes the marker, so a failure that returns after a fix alerts again. It is
    written only once Telegram accepts the message; an undelivered alert is
    retried on the next start. TelegramSender is its only import.
    """
    from src.infrastructure.notifications import TelegramSender

    digest = hashlib.sha256(str(error).encode()).hexdigest()
    if START_FAILURE_FILE.exists() and START_FAILURE_FILE.read_text().strip() == digest:
        logger.info("Start failure already sent to Telegram; not sending it again")
        return
    sender = TelegramSender(config.telegram.bot_token, config.telegram.chat_id)
    sent = sender.send_error(
        f"{error}\n\nsystemd keeps restarting the bot, and it starts once this is fixed. "
        "The same error is not sent again.",
        context="Telegram bot cannot start",
    )
    if sent:
        START_FAILURE_FILE.parent.mkdir(parents=True, exist_ok=True)
        START_FAILURE_FILE.write_text(digest)


def main() -> int:
    config = Config.from_env()
    if not config.telegram.is_configured:
        logger.error("Telegram not configured; cannot start bot")
        return 1
    # Everything before the first poll can fail a start, and each failure is
    # Telegrammed once: importing the engine (scan, analysis, formatters), the
    # migration check, building the Container. The migration runs here and never
    # again: a restart is how a deploy reaches this process, so this is where a
    # pending migration lands outside the Monday maintenance run, and an applied
    # migration's edited file raises. systemd retries every 10 s, each attempt
    # logging why. Container() itself does not migrate.
    try:
        from scripts.jobs.telegram_commands import poll_forever
        from src.container import Container
        from src.infrastructure.database.migrations import apply_migrations

        apply_migrations()
        container = Container(config)
    except Exception as e:
        _alert_start_failure(config, e)
        raise
    START_FAILURE_FILE.unlink(missing_ok=True)
    poll_forever(container, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
