"""Sync Telegram sender.

5.0's sender was async httpx; 2.0 is synchronous and requests-based, so this is
a rewrite in 2.0's idiom rather than a port. The property that matters most is
the last one: a notification failure must never take down the job that was
trying to report — including the failure report itself.

requests.post is patched at the module that imports it, not globally, so these
tests cannot leak into anything else in the suite.
"""

from unittest.mock import Mock, patch

import pytest

from src.infrastructure.notifications import TelegramSender

TOKEN = "123:abc"
CHAT = "555"
TARGET = "src.infrastructure.notifications.telegram.requests.post"


def _ok():
    r = Mock()
    r.status_code = 200
    r.json.return_value = {"ok": True}
    return r


@pytest.fixture
def sender():
    return TelegramSender(TOKEN, CHAT)


def test_send_posts_to_telegram_api(sender):
    with patch(TARGET, return_value=_ok()) as post:
        assert sender.send("hello") is True

    assert post.call_args[0][0] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = post.call_args[1]["json"]
    assert payload["chat_id"] == CHAT
    assert payload["text"] == "hello"
    assert payload["parse_mode"] == "HTML"


def test_send_truncates_over_telegram_limit(sender):
    with patch(TARGET, return_value=_ok()) as post:
        sender.send("x" * 5000)

    sent = post.call_args[1]["json"]["text"]
    assert len(sent) <= 4096
    assert sent.endswith("…")


def test_send_does_not_truncate_at_the_limit(sender):
    """Exactly 4096 chars is legal — off-by-one here would corrupt every digest."""
    exact = "y" * 4096
    with patch(TARGET, return_value=_ok()) as post:
        sender.send(exact)

    assert post.call_args[1]["json"]["text"] == exact


def test_send_returns_false_on_http_error(sender):
    bad = Mock()
    bad.status_code = 400
    bad.text = "Bad Request: chat not found"

    with patch(TARGET, return_value=bad):
        assert sender.send("hello") is False


def test_send_returns_false_on_exception_never_raises(sender):
    with patch(TARGET, side_effect=OSError("network down")):
        assert sender.send("hello") is False


def test_send_error_prefixes_context(sender):
    with patch(TARGET, return_value=_ok()) as post:
        sender.send_error("boom", context="morning_digest")

    text = post.call_args[1]["json"]["text"]
    assert "morning_digest" in text
    assert "boom" in text


def test_send_error_escapes_html_in_the_error_text(sender):
    """Tracebacks contain <module> and generics; unescaped they break HTML parse
    mode and Telegram rejects the whole message — losing the error report."""
    with patch(TARGET, return_value=_ok()) as post:
        sender.send_error("List<int> failed in <module>", context="job:x")

    text = post.call_args[1]["json"]["text"]
    assert "&lt;module&gt;" in text
    assert "<module>" not in text


def test_send_error_never_raises_even_when_transport_dies(sender):
    with patch(TARGET, side_effect=OSError("network down")):
        assert sender.send_error("boom", context="job:x") is False


# ---------------------------------------------------------------------------
# Added after code review: truncation measured the wrong unit and cut markup.
# ---------------------------------------------------------------------------

def test_truncation_budget_is_utf16_units_not_code_points(sender):
    """Telegram's 4096 limit is UTF-16 code units. The digest interpolates
    astral-plane emoji (green circle = 2 units each), so a message of 4096 code
    POINTS can be ~4110 units and be rejected with MESSAGE_TOO_LONG."""
    text = "\U0001f7e2" * 3000  # 3000 code points, 6000 UTF-16 units

    with patch(TARGET, return_value=_ok()) as post:
        sender.send(text)

    sent = post.call_args[1]["json"]["text"]
    utf16_units = len(sent.encode("utf-16-le")) // 2
    assert utf16_units <= 4096


def test_truncation_never_splits_a_surrogate_pair(sender):
    with patch(TARGET, return_value=_ok()) as post:
        sender.send("\U0001f7e2" * 3000)
    # Re-encoding round-trips only if no surrogate pair was severed.
    sent = post.call_args[1]["json"]["text"]
    assert sent.encode("utf-16-le").decode("utf-16-le") == sent


def test_send_error_keeps_its_pre_tags_when_the_traceback_is_huge(sender):
    """The body is truncated before wrapping, so <pre>...</pre> stays balanced.
    Cutting the finished message instead lost the closing tag and Telegram
    rejected it with 'Can't parse entities' — losing the error report itself."""
    with patch(TARGET, return_value=_ok()) as post:
        sender.send_error("E" * 20000, context="job:digest")

    sent = post.call_args[1]["json"]["text"]
    assert sent.count("<pre>") == 1
    assert sent.endswith("</pre>")
    assert "job:digest" in sent
    assert len(sent.encode("utf-16-le")) // 2 <= 4096


def test_send_error_does_not_cut_inside_an_html_entity(sender):
    """Escaping turns '<' into '&lt;'; a cut mid-entity is also a parse error."""
    with patch(TARGET, return_value=_ok()) as post:
        sender.send_error("<" * 20000, context="job:x")

    sent = post.call_args[1]["json"]["text"]
    body = sent.split("<pre>")[1].rsplit("</pre>")[0]
    assert "&" not in body or body.endswith(";") or ";" in body.rsplit("&", 1)[-1]


def test_a_transport_error_is_logged_without_the_bot_token(sender, caplog):
    """Telegram puts the token in the request path and requests quotes the whole
    URL in its exception text, so the raw error would write the token to the log."""
    import requests

    err = requests.ConnectionError(
        f"HTTPSConnectionPool(host='api.telegram.org', port=443): "
        f"Max retries exceeded with url: /bot{TOKEN}/sendMessage"
    )
    with patch(TARGET, side_effect=err):
        assert sender.send("hello") is False

    assert TOKEN not in caplog.text
    assert "bot<redacted>/sendMessage" in caplog.text
