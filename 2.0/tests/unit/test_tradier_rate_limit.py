"""Tradier's rate limit and the client's handling of rejections.

Tradier (docs.tradier.com/docs/rate-limiting, checked 2026-09-13): market data
is 120 requests per minute, and "we aggregate limits over 1-minute intervals
starting with the first request and reset 1 minute later". The client-side
limiter was a token bucket at 120/min with a burst of 150, which lets 150 calls
through at once and ~270 through any 60 seconds -- and no burst setting fixes
that, because a bucket admits burst + a full minute of refill per window. A
429 then came back as a generic error and the scanner dropped the ticker: every
call was a bare requests.get with no retry, while the docstring promised
"retry logic and circuit breaker".
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.domain.errors import ErrorCode  # noqa: E402
from src.infrastructure.api.tradier import TradierAPI  # noqa: E402
from src.utils.rate_limiter import create_tradier_limiter  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.0)


def _max_in_any_window(stamps, width):
    worst, j = 0, 0
    for i, t in enumerate(stamps):
        while stamps[j] <= t - width:
            j += 1
        worst = max(worst, i - j + 1)
    return worst


def test_never_more_than_120_calls_in_any_60_seconds():
    clock = FakeClock()
    limiter = create_tradier_limiter(per_minute=120, clock=clock.time, sleep=clock.sleep)
    stamps = []
    for _ in range(1000):
        assert limiter.wait_for_token(timeout=None)
        stamps.append(clock.now)
        clock.sleep(0.01)                     # a fast caller: 100 calls/second if allowed

    assert _max_in_any_window(stamps, 60.0) <= 120
    # ...and it is a limit, not a slowdown: the full 120 are available per minute.
    assert stamps[119] - stamps[0] < 2.0
    assert (stamps[-1] - stamps[0]) / 60.0 < 1000 / 120 + 1


def test_a_wait_longer_than_the_timeout_gives_up():
    clock = FakeClock()
    limiter = create_tradier_limiter(per_minute=2, clock=clock.time, sleep=clock.sleep)
    assert limiter.wait_for_token(timeout=1) and limiter.wait_for_token(timeout=1)
    assert limiter.wait_for_token(timeout=1) is False


# --- the HTTP client, against a real local server ----------------------------

class _Script:
    """Responses served in order; the last one repeats."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []


def _serve(script):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            script.calls.append(time.monotonic())
            i = min(len(script.calls) - 1, len(script.responses) - 1)
            status, headers, body = script.responses[i]
            payload = body.encode()
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v() if callable(v) else v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


QUOTE = (200, {"Content-Type": "application/json"},
         '{"quotes": {"quote": {"symbol": "MU", "last": 101.5}}}')


@pytest.fixture
def tradier():
    servers = []

    def make(*responses, max_retries=3):
        script = _Script(*responses)
        server = _serve(script)
        servers.append(server)
        api = TradierAPI(api_key="k", base_url=f"http://127.0.0.1:{server.server_port}",
                         max_retries=max_retries)
        return api, script

    yield make
    for s in servers:
        s.shutdown()


def test_a_429_waits_for_retry_after_then_succeeds(tradier):
    api, script = tradier((429, {"Retry-After": "1"}, ""), QUOTE)
    result = api.get_stock_price("MU")
    assert result.is_ok and float(result.value.amount) == 101.5
    assert len(script.calls) == 2
    assert script.calls[1] - script.calls[0] >= 0.9


def test_a_429_without_retry_after_waits_until_the_window_expires(tradier):
    # Tradier documents X-Ratelimit-Expiry (epoch ms), not Retry-After.
    expiry = lambda: str(int((time.time() + 1.2) * 1000))  # noqa: E731
    api, script = tradier((429, {"X-Ratelimit-Expiry": expiry}, ""), QUOTE)
    assert api.get_stock_price("MU").is_ok
    assert script.calls[1] - script.calls[0] >= 1.0


def test_a_5xx_is_retried(tradier):
    """A quote's. A chain is sent once and handed back for a new slot (test_spot_age.py)."""
    api, script = tradier((503, {}, ""), QUOTE)
    assert api.get_stock_price("MU").is_ok
    assert len(script.calls) == 2


def test_a_429_that_never_clears_is_a_rate_limit_error(tradier):
    api, script = tradier((429, {"Retry-After": "0"}, ""), max_retries=1)
    result = api.get_stock_price("MU")
    assert result.is_err and result.error.code == ErrorCode.RATELIMIT
    assert len(script.calls) == 2


def test_a_404_is_not_retried(tradier):
    api, script = tradier((404, {}, ""))
    assert api.get_stock_price("MU").is_err
    assert len(script.calls) == 1


def test_the_clients_wait_budget_outlasts_a_full_window():
    """A scan that spends its 120 calls early must wait for the window to slide,
    not be refused: a refusal is Err(RATELIMIT), and the scanner drops the ticker
    (a simulated 60-ticker concurrent scan lost 30 at the old 30 s budget)."""
    from src.infrastructure.api.tradier import RATE_LIMIT_WAIT_TIMEOUT_SEC

    clock = FakeClock()
    limiter = create_tradier_limiter(per_minute=120, clock=clock.time, sleep=clock.sleep)
    for _ in range(120):
        assert limiter.wait_for_token(timeout=None)
    clock.sleep(1.0)                               # the 121st call, one second later
    assert limiter.wait_for_token(timeout=RATE_LIMIT_WAIT_TIMEOUT_SEC)
