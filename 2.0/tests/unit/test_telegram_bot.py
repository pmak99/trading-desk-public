import sys
from datetime import date
from unittest.mock import Mock, call, create_autospec, patch

import pytest

from scripts.jobs.telegram_commands import _validate_ticker, handle_command
from src.domain.enums import EarningsTiming


def test_unknown_command_returns_help():
    out = handle_command("/nonsense", Mock())
    assert "whisper" in out.lower() and "analyze" in out.lower()


def test_whisper_command_builds_digest():
    with patch("scripts.jobs.telegram_commands._week_tickers", return_value=(["NVDA"], date(2026, 9, 14))), \
         patch("scripts.jobs.telegram_commands.collect_whisper_results") as mock_collect, \
         patch("scripts.jobs.telegram_commands.format_digest", return_value="DIGEST"):
        mock_collect.return_value = Mock(results=[{"ticker": "NVDA", "is_tradeable": True}])
        assert handle_command("/whisper", Mock()) == "DIGEST"


def test_command_is_case_insensitive_and_strips_botname():
    with patch("scripts.jobs.telegram_commands._week_tickers", return_value=([], date(2026, 9, 14))), \
         patch("scripts.jobs.telegram_commands.collect_whisper_results") as mock_collect, \
         patch("scripts.jobs.telegram_commands.format_digest", return_value="DIGEST"):
        mock_collect.return_value = Mock(results=[])
        assert handle_command("/Whisper@ivcrush_bot", Mock()) == "DIGEST"


def test_overlong_input_is_rejected_not_executed():
    out = handle_command("/analyze " + "A" * 5000, Mock())
    assert "too long" in out.lower()


def test_analyze_happy_path():
    with patch("scripts.jobs.telegram_commands.fetch_earnings_for_ticker",
               return_value=(date(2026, 9, 18), EarningsTiming.AMC)), \
         patch("scripts.jobs.telegram_commands.calculate_expiration_date", return_value=date(2026, 9, 19)), \
         patch("scripts.jobs.telegram_commands.analyze_ticker", return_value={"ticker": "NVDA"}), \
         patch("scripts.jobs.telegram_commands.format_ticker_line", return_value="LINE"):
        assert handle_command("/analyze NVDA", Mock()) == "LINE"


def test_analyze_forwards_the_event_timing():
    """Timing picks the implied-move expiration; the bot used to drop it, so a
    BMO report was priced like an AMC one."""
    with patch("scripts.jobs.telegram_commands.fetch_earnings_for_ticker",
               return_value=(date(2026, 10, 16), EarningsTiming.BMO)), \
         patch("scripts.jobs.telegram_commands.calculate_expiration_date", return_value=date(2026, 10, 23)), \
         patch("scripts.jobs.telegram_commands.analyze_ticker", return_value={"ticker": "JPM"}) as mock_analyze, \
         patch("scripts.jobs.telegram_commands.format_ticker_line", return_value="LINE"):
        handle_command("/analyze JPM", Mock())
    assert mock_analyze.call_args.kwargs["earnings_timing"] == EarningsTiming.BMO


@pytest.mark.parametrize("min_dte, expiration", [
    (3, date(2026, 10, 16)),     # Monday report: the same week's Friday, 4 DTE
    (5, date(2026, 10, 23)),     # 4 DTE is below MIN_DTE=5: the next Friday
])
def test_analyze_floors_the_expiration_at_the_configured_min_dte(min_dte, expiration):
    """The scan paths pass config.thresholds.min_dte to calculate_expiration_date;
    the bot called it without, so MIN_DTE never bound a Telegram /analyze."""
    container = Mock()
    container.config.thresholds.min_dte = min_dte
    with patch("scripts.jobs.telegram_commands.fetch_earnings_for_ticker",
               return_value=(date(2026, 10, 12), EarningsTiming.AMC)), \
         patch("scripts.jobs.telegram_commands.analyze_ticker", return_value={"ticker": "NVDA"}) as mock_analyze, \
         patch("scripts.jobs.telegram_commands.format_ticker_line", return_value="LINE"):
        handle_command("/analyze NVDA", container)
    assert mock_analyze.call_args.args[3] == expiration


def test_analyze_no_upcoming_earnings():
    with patch("scripts.jobs.telegram_commands.fetch_earnings_for_ticker", return_value=None):
        out = handle_command("/analyze NVDA", Mock())
        assert "no upcoming earnings" in out.lower()


def test_analyze_insufficient_data():
    with patch("scripts.jobs.telegram_commands.fetch_earnings_for_ticker",
               return_value=(date(2026, 9, 18), EarningsTiming.AMC)), \
         patch("scripts.jobs.telegram_commands.calculate_expiration_date", return_value=date(2026, 9, 19)), \
         patch("scripts.jobs.telegram_commands.analyze_ticker", return_value=None):
        out = handle_command("/analyze NVDA", Mock())
        assert "could not analyze" in out.lower()


def test_analyze_rejects_invalid_ticker():
    out = handle_command("/analyze not-a-ticker!", Mock())
    assert "invalid ticker" in out.lower()


def test_validate_ticker_uppercases():
    assert _validate_ticker("nvda") == "NVDA"


def test_validate_ticker_rejects_bad_input():
    for bad in ("", "TOOLONG1", "12345", "AB CD"):
        with pytest.raises(ValueError):
            _validate_ticker(bad)


def test_chat_id_comparison_is_string_safe():
    """The one sharp edge: Telegram delivers chat.id as a JSON int; config.telegram.chat_id
    is Optional[str]. A naive == is int == str, always False -- the bot would silently
    ignore every message, including the owner's, and look dead rather than broken."""
    from scripts.jobs.telegram_commands import _is_authorized

    config = Mock()
    config.telegram.chat_id = "123456"
    assert _is_authorized({"message": {"chat": {"id": 123456}}}, config) is True
    assert _is_authorized({"message": {"chat": {"id": 999999}}}, config) is False


class _StopPolling(Exception):
    pass


BOT_TOKEN = "8227204547:AAFake-token_value"


def _poll_config():
    config = Mock()
    config.telegram.bot_token = BOT_TOKEN
    config.telegram.chat_id = "555"
    return config


@pytest.fixture
def offset_file(tmp_path, monkeypatch):
    """The bot's getUpdates offset, in tmp_path rather than 2.0/data/."""
    from scripts.jobs import telegram_commands

    path = tmp_path / ".telegram_offset"
    path.write_text("1")
    monkeypatch.setattr(telegram_commands, "OFFSET_FILE", path)
    return path


def test_a_failed_poll_is_logged_without_the_bot_token(caplog, offset_file):
    import requests

    from scripts.jobs import telegram_commands

    err = requests.HTTPError(
        f"502 Server Error: Bad Gateway for url: "
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset=1&timeout=30"
    )
    with patch.object(telegram_commands.requests, "get", side_effect=err) as get, \
         patch.object(telegram_commands.time, "sleep", side_effect=_StopPolling):
        with pytest.raises(_StopPolling):
            telegram_commands.poll_forever(Mock(), _poll_config())

    assert get.call_args.kwargs["params"]["offset"] == 1       # read back from the file
    assert BOT_TOKEN not in caplog.text
    assert "bot<redacted>/getUpdates" in caplog.text


def test_a_failed_reply_is_logged_without_the_bot_token(caplog, offset_file):
    import requests

    from scripts.jobs import telegram_commands

    updates = Mock()
    updates.json.return_value = {"result": [
        {"update_id": 7, "message": {"text": "/help", "chat": {"id": 555}}}]}
    err = requests.ConnectionError(f"Max retries exceeded with url: /bot{BOT_TOKEN}/sendMessage")
    with patch.object(telegram_commands, "handle_command", return_value="ok"), \
         patch.object(telegram_commands.requests, "get", side_effect=[updates, err]), \
         patch.object(telegram_commands.requests, "post", side_effect=err) as post, \
         patch.object(telegram_commands.time, "sleep", side_effect=_StopPolling):
        with pytest.raises(_StopPolling):
            telegram_commands.poll_forever(Mock(), _poll_config())

    post.assert_called_once()                   # the owner's chat is authorised
    assert offset_file.read_text() == "8"       # a failed reply still advances past the update
    assert BOT_TOKEN not in caplog.text
    assert "bot<redacted>/sendMessage" in caplog.text


def test_a_stranger_gets_no_reply_but_the_update_is_consumed(offset_file):
    from scripts.jobs import telegram_commands

    updates = Mock()
    updates.json.return_value = {"result": [
        {"update_id": 41, "message": {"text": "/whisper", "chat": {"id": 999}}}]}
    with patch.object(telegram_commands, "handle_command") as handle, \
         patch.object(telegram_commands.requests, "get", side_effect=[updates, ConnectionError()]), \
         patch.object(telegram_commands.requests, "post") as post, \
         patch.object(telegram_commands.time, "sleep", side_effect=_StopPolling):
        with pytest.raises(_StopPolling):
            telegram_commands.poll_forever(Mock(), _poll_config())

    handle.assert_not_called()
    post.assert_not_called()
    assert offset_file.read_text() == "42"


def test_analyze_logs_as_a_single_ticker_analysis_not_a_scan():
    """analysis_log.source is how the A/B rows tell entry points apart; a bot
    /analyze is one ticker on demand, the same as ticker mode."""
    with patch("scripts.jobs.telegram_commands.fetch_earnings_for_ticker",
               return_value=(date(2026, 10, 16), EarningsTiming.BMO)), \
         patch("scripts.jobs.telegram_commands.calculate_expiration_date", return_value=date(2026, 10, 23)), \
         patch("scripts.jobs.telegram_commands.analyze_ticker", return_value={"ticker": "JPM"}) as mock_analyze, \
         patch("scripts.jobs.telegram_commands.format_ticker_line", return_value="LINE"):
        handle_command("/analyze JPM", Mock())
    assert mock_analyze.call_args.kwargs["source"] == "ticker"


# --- a start that fails before the first poll ------------------------------------------
#
# Everything main() does before polling can raise: importing the engine, the migration
# check, building the Container. The process exits, and systemd (Restart=always) starts
# it again 10 s later, indefinitely. Each test below is a sequence of those starts.

CHECKSUM = RuntimeError("Failed to apply database migrations: migration 2 "
                        "(schema_audit_2026_09) changed after it was applied")
CONFIGURATION = RuntimeError("1 configuration error(s):\n  - TRADIER_API_KEY is required")
# The engine module the bot imports at start: scan, analysis and formatters behind it.
BROKEN_IMPORT = "scripts.scan.workflows.ticker_analysis"


@pytest.fixture
def start_failure_file(tmp_path, monkeypatch):
    """The bot's start-failure marker, in tmp_path rather than 2.0/data/."""
    from scripts.jobs import telegram_bot

    path = tmp_path / ".telegram_start_failure"
    monkeypatch.setattr(telegram_bot, "START_FAILURE_FILE", path)
    return path


@pytest.fixture
def sender(start_failure_file):
    """The TelegramSender the bot builds; its send_error delivers unless told not to."""
    from src.infrastructure.notifications import TelegramSender

    cls = create_autospec(TelegramSender)
    cls.return_value.send_error.return_value = True
    with patch("src.infrastructure.notifications.TelegramSender", cls):
        yield cls


def _start(error=None, at="migrate"):
    """One start of the service: main(), failing at step `at` -- "import", "migrate"
    or "container". `error` is what the migration check or Container raises (a fresh
    exception each call, as a new process would raise); no error is a clean start.
    "import" takes none: it breaks a module the engine imports, so main()'s import of
    the engine raises."""
    from scripts.jobs import telegram_bot

    fails = error is not None or at == "import"
    config = _poll_config()
    config.telegram.is_configured = True
    with patch.object(telegram_bot.Config, "from_env", return_value=config), \
         patch("src.infrastructure.database.migrations.apply_migrations",
               side_effect=error if at == "migrate" else None) as migrate, \
         patch("src.container.Container",
               side_effect=error if at == "container" else None) as container, \
         patch("scripts.jobs.telegram_commands.poll_forever") as poll, \
         pytest.MonkeyPatch.context() as mp:
        if at == "import":
            mp.delitem(sys.modules, "scripts.jobs.telegram_commands")
            mp.setitem(sys.modules, BROKEN_IMPORT, None)
        if not fails:
            assert telegram_bot.main() == 0
        else:
            with pytest.raises(ImportError if at == "import" else type(error)) as exc:
                telegram_bot.main()
            if error is not None:
                assert exc.value is error             # exits non-zero, with the cause

    steps = ["import", "migrate", "container", "poll"]
    reached = steps[:steps.index(at) + 1] if fails else steps
    assert migrate.call_args_list == ([call()] if "migrate" in reached else [])
    assert container.call_args_list == ([call(config)] if "container" in reached else [])
    assert poll.call_args_list == ([call(container.return_value, config)]
                                   if "poll" in reached else [])


def _again(error):
    return RuntimeError(str(error))


def test_a_refused_start_is_telegrammed_once_and_still_exits(sender, start_failure_file):
    _start(CHECKSUM)

    sender.assert_called_once_with(BOT_TOKEN, "555")
    sender.return_value.send_error.assert_called_once()
    body = sender.return_value.send_error.call_args.args[0]
    assert str(CHECKSUM) in body
    assert start_failure_file.exists()


def test_the_same_failure_on_every_restart_after_that_sends_nothing(sender):
    for _ in range(3):
        _start(_again(CHECKSUM))
    assert sender.return_value.send_error.call_count == 1


def test_a_different_failure_is_telegrammed(sender):
    _start(CHECKSUM)
    _start(RuntimeError("Failed to apply database migrations: "
                        "relation \"widget\" already exists"))
    assert sender.return_value.send_error.call_count == 2


def test_a_start_that_passes_the_check_clears_the_marker(sender, start_failure_file):
    """So the same failure coming back after a fix alerts again."""
    _start(CHECKSUM)
    _start()
    assert not start_failure_file.exists()

    _start(_again(CHECKSUM))
    assert sender.return_value.send_error.call_count == 2


def test_an_undelivered_alert_is_retried_on_the_next_start(sender, start_failure_file):
    sender.return_value.send_error.return_value = False     # Telegram unreachable
    _start(CHECKSUM)
    assert not start_failure_file.exists()

    sender.return_value.send_error.return_value = True
    _start(_again(CHECKSUM))
    _start(_again(CHECKSUM))
    assert sender.return_value.send_error.call_count == 2
    assert start_failure_file.exists()


def test_a_clean_start_with_no_marker_to_clear_still_starts(start_failure_file):
    _start()
    assert not start_failure_file.exists()


def test_a_container_that_cannot_be_built_is_telegrammed_once(sender, start_failure_file):
    _start(CONFIGURATION, at="container")
    for _ in range(2):
        _start(_again(CONFIGURATION), at="container")

    sender.return_value.send_error.assert_called_once()
    assert str(CONFIGURATION) in sender.return_value.send_error.call_args.args[0]
    assert start_failure_file.exists()


def test_an_engine_that_cannot_be_imported_is_telegrammed_once(sender, start_failure_file):
    for _ in range(3):
        _start(at="import")

    sender.return_value.send_error.assert_called_once()
    body = sender.return_value.send_error.call_args.args[0]
    assert BROKEN_IMPORT in body
    assert start_failure_file.exists()

    _start()                                    # the fix lands: the marker clears
    assert not start_failure_file.exists()


def test_the_entry_module_imports_nothing_that_can_break_the_alert():
    """`python -m scripts.jobs.telegram_bot` runs the module's top level before
    main(); an import that fails there exits with no alert. So the module loads
    Config (the one reader of the bot token) and nothing else first-party: the engine,
    the migrator and the Container are imported inside main(), TelegramSender inside
    the alert. Config's own imports are held to src.domain and common by the
    import-linter layers contract, so those are the only other first-party modules."""
    import os
    import subprocess
    from pathlib import Path

    engine = Path(__file__).resolve().parents[2]
    paths = [str(engine.parent), str(engine), os.environ.get("PYTHONPATH")]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in paths if p))
    code = ("import sys; import scripts.jobs.telegram_bot; "
            "print('\\n'.join(sorted(m for m in sys.modules "
            "if m.split('.')[0] in ('src', 'scripts', 'common'))))")
    r = subprocess.run([sys.executable, "-c", code], cwd=engine, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    loaded = set(r.stdout.split())
    entry = {"scripts", "scripts.jobs", "scripts.jobs.telegram_bot", "src"}
    beneath_config = ("src.config", "src.domain", "common")
    engine = {m for m in loaded - entry
              if not any(m == b or m.startswith(b + ".") for b in beneath_config)}
    assert engine == set(), engine
    assert "src.config.config" in loaded
