"""Telegram Bot API client (synchronous).

5.0's sender was async httpx. 2.0 is a synchronous, requests-based codebase
whose scan path is thread-pool rather than asyncio, so this is deliberately a
rewrite in 2.0's idiom rather than a port — do not reintroduce httpx/asyncio
here just to match the 5.0 file.

Every method swallows its own exceptions and returns bool. A failed
notification must never abort the job that was trying to report, and
send_error() in particular is usually called from an exception handler that
already has a real failure to surface.
"""

import html
import logging
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Telegram rejects sendMessage bodies over 4096 UTF-16 code units.
TELEGRAM_MAX_CHARS = 4096
REQUEST_TIMEOUT_SEC = 15
API_BASE = "https://api.telegram.org"

# Telegram carries the bot token in the request path, and requests quotes the
# full URL in its exception text -- so a logged transport error would write the
# token to the journal (it did, on the server, until 2026-09-14). Every log line that
# can hold a Telegram URL or error goes through redact_bot_token().
_BOT_TOKEN_IN_PATH = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


def redact_bot_token(text: object) -> str:
    """The text with any bot token in a Telegram path replaced by <redacted>."""
    return _BOT_TOKEN_IN_PATH.sub("bot<redacted>", str(text))


class TelegramSender:
    """Posts messages to one chat. Construct from Config.telegram."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    @staticmethod
    def _utf16_len(text: str) -> int:
        """Length in UTF-16 code units, which is what Telegram counts.

        len() counts code points. The digest interpolates astral-plane emoji
        (🟢 is 2 UTF-16 units), so a 4096-code-point message can be ~4110 units
        and be rejected with MESSAGE_TOO_LONG.
        """
        return len(text.encode("utf-16-le")) // 2

    @classmethod
    def _fit(cls, text: str, budget: int) -> str:
        """Longest prefix of `text` fitting `budget` UTF-16 units.

        Trims whole characters, so a surrogate pair is never severed, and backs
        off a dangling HTML entity — escaping turns '<' into '&lt;', and a cut
        mid-entity is a parse error just as surely as a cut mid-tag.
        """
        if cls._utf16_len(text) <= budget:
            return text

        out = text
        while out and cls._utf16_len(out) > budget:
            # Drop roughly the overshoot, at least one char, always whole chars.
            over = cls._utf16_len(out) - budget
            out = out[: -max(1, over)]

        tail = out.rsplit("&", 1)
        if len(tail) == 2 and ";" not in tail[1]:
            out = tail[0]

        return out

    def _truncate(self, text: str) -> str:
        """Last-resort guard for plain messages. Structured senders should fit
        their BODY before wrapping it in markup — see send_error."""
        if self._utf16_len(text) <= TELEGRAM_MAX_CHARS:
            return text
        return self._fit(text, TELEGRAM_MAX_CHARS - 1) + "…"

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send one message. Returns True only on a 200 from Telegram."""
        url = f"{API_BASE}/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": self._truncate(text),
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SEC)
        except Exception as e:
            # Bare Exception is intentional: DNS, TLS, proxy and timeout errors
            # all surface differently and none of them justify killing the job.
            logger.error(f"Telegram send failed: {type(e).__name__}: {redact_bot_token(e)}")
            return False

        if resp.status_code != 200:
            logger.error(
                f"Telegram send rejected {resp.status_code}: {redact_bot_token(resp.text[:200])}"
            )
            return False

        return True

    def send_error(self, error_msg: str, context: str = "") -> bool:
        """Report a failure. Escapes its inputs — tracebacks routinely contain
        `<module>` and generic types, which would otherwise break HTML parse
        mode and make Telegram reject the very message reporting the error."""
        header = f"⚠️ <b>{html.escape(context or 'error')}</b>"

        # Fit the BODY, then wrap. Truncating the finished message instead cut
        # inside the markup: a long traceback lost its closing </pre> and
        # Telegram rejected the whole thing with "Can't parse entities" — so the
        # report about the original failure was itself lost.
        overhead = self._utf16_len(f"{header}\n<pre></pre>")
        body = self._fit(html.escape(error_msg),
                         max(0, TELEGRAM_MAX_CHARS - overhead))

        return self.send(f"{header}\n<pre>{body}</pre>")
