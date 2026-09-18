"""The Telegram bot's commands and its long-poll loop -- /whisper and /analyze on the
authoritative engine.

Replaces 5.0's `POST /telegram` webhook (5.0/src/api/routers/webhooks.py). Every inbound
`/analyze` reply on that path came from 5.0's simplified scoring, which CLAUDE.md's
"Known divergence" note declares non-authoritative -- the inbound-side analogue of the
outbound digest divergence the Sep 8-9 migration measured and fixed on the digest side.

Scope: /whisper and /analyze TICKER only. /council (agentic, not a plain function),
/health (redundant with job_status), and /dashboard (Grafana replaces it) are out of
scope -- see docs/superpowers/specs/2026-09-09-telegram-inbound-bot-design.md and
the job-migration decision notes.

The process starts in scripts/jobs/telegram_bot.py, which imports this module inside
main() so that a failure to import the engine is Telegrammed like any other failed
start.
"""

import logging
import re
import time
from datetime import timedelta
from pathlib import Path

import requests

from scripts.jobs.morning_digest import _week_tickers
from scripts.scan.earnings_fetcher import fetch_earnings_for_ticker
from scripts.scan.quality_scorer import _precalculate_quality_scores
from scripts.scan.workflows.ticker_analysis import analyze_ticker
from scripts.scan.workflows.whisper_mode import collect_whisper_results
from src.application.formatters.telegram import (
    format_digest,
    format_ticker_line,
)
from src.config.config import Config
from src.container import Container
from src.domain.trading_expiration import calculate_expiration_date
from src.infrastructure.notifications import redact_bot_token

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]

MAX_MESSAGE_LEN = 200
MAX_DIGEST_ROWS = 10
POLL_TIMEOUT_SEC = 30
OFFSET_FILE = _ROOT / "data" / ".telegram_offset"

HELP_TEXT = (
    "Commands:\n"
    "/whisper — today's top VRP candidates\n"
    "/analyze TICKER — deep dive on one ticker"
)

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def _validate_ticker(raw: str) -> str:
    ticker = raw.strip().upper()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker: {raw!r}")
    return ticker


def _handle_whisper(container: Container) -> str:
    tickers, monday = _week_tickers(container)
    run = collect_whisper_results(container, tickers, monday, monday + timedelta(days=6))
    tradeable = [r for r in run.results if r.get("is_tradeable")]
    if tradeable:
        _precalculate_quality_scores(tradeable)
    return format_digest(run.results, title="Whisper", max_rows=MAX_DIGEST_ROWS)


def _handle_analyze(container: Container, raw_ticker: str) -> str:
    try:
        ticker = _validate_ticker(raw_ticker)
    except ValueError:
        return "Invalid ticker. Use 1-5 letters (e.g., NVDA)."

    resolved = fetch_earnings_for_ticker(container, ticker)
    if resolved is None:
        return f"No upcoming earnings found for {ticker}."
    earnings_date, timing = resolved

    # MIN_DTE as the scan paths pass it; the bot omitted it until 2026-09-14,
    # so a configured floor above the default 3 never bound a Telegram /analyze.
    expiration = calculate_expiration_date(
        earnings_date, timing, min_dte=container.config.thresholds.min_dte
    )
    # Timing picks the implied-move expiration; it was dropped here, so every
    # BMO /analyze priced the next day's expiration.
    result = analyze_ticker(container, ticker, earnings_date, expiration,
                            earnings_timing=timing, source="ticker")
    if result is None:
        return f"Could not analyze {ticker} — insufficient data."
    return format_ticker_line(result, rank=1)


def handle_command(text: str, container: Container) -> str:
    """Pure function: raw Telegram message text in, formatted reply out.

    No I/O beyond what the two command paths need -- this is the unit under
    test. poll_forever() is a thin, largely-untestable wrapper around this
    plus the Telegram transport, kept deliberately small.
    """
    if len(text) > MAX_MESSAGE_LEN:
        return "Message too long."

    parts = text.strip().split(maxsplit=1)
    if not parts:
        return HELP_TEXT

    # Telegram appends @botname in group chats: "/whisper@ivcrush_bot".
    command = parts[0].split("@", 1)[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command == "/whisper":
        return _handle_whisper(container)
    if command == "/analyze":
        if not arg:
            return "Usage: /analyze TICKER"
        return _handle_analyze(container, arg)
    return HELP_TEXT


def _is_authorized(update: dict, config: Config) -> bool:
    """The only authentication this bot has.

    Telegram delivers chat.id as a JSON number (Python int after parsing);
    TelegramConfig.chat_id is Optional[str]. A naive == is int == str, always
    False -- the bot would silently ignore every message, including the
    owner's, and look dead rather than broken. str() on both sides is
    mandatory.
    """
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    return chat_id is not None and str(chat_id) == str(config.telegram.chat_id)


def _read_offset() -> int:
    if OFFSET_FILE.exists():
        return int(OFFSET_FILE.read_text().strip() or 0)
    return 0


def _write_offset(offset: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))


def poll_forever(container: Container, config: Config) -> None:
    """Long-poll Telegram's getUpdates. Not unit-tested -- see the design
    spec's Testing section: this is a thin transport wrapper around
    handle_command(), verified by the one live end-to-end check at cutover.
    """
    base = f"https://api.telegram.org/bot{config.telegram.bot_token}"
    offset = _read_offset()

    while True:
        try:
            resp = requests.get(
                f"{base}/getUpdates",
                params={"offset": offset, "timeout": POLL_TIMEOUT_SEC},
                timeout=POLL_TIMEOUT_SEC + 10,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except Exception as e:
            logger.error(f"getUpdates failed: {redact_bot_token(e)}")
            time.sleep(10)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            if not _is_authorized(update, config):
                continue
            text = (update.get("message") or {}).get("text", "")
            try:
                reply = handle_command(text, container)
            except Exception as e:
                logger.error(f"handle_command failed for {text!r}: {e}")
                reply = "Something went wrong processing that command."
            try:
                requests.post(
                    f"{base}/sendMessage",
                    json={
                        "chat_id": config.telegram.chat_id,
                        "text": reply,
                        "parse_mode": "HTML",
                    },
                    timeout=15,
                )
            except Exception as e:
                logger.error(f"sendMessage failed: {redact_bot_token(e)}")

        if updates:
            _write_offset(offset)
