"""A chain is priced on a quote no older than PRICE_TTL_SECONDS when its request goes out.

A cached quote becomes the next chain's spot, and the spot prices the straddle and
places the strikes against option quotes fetched with the chain (memory_cache.py).
CachedOptionsDataProvider read the quote, then called TradierAPI.get_option_chain,
which waited on the shared limiter before issuing the request; without a cached
quote, TradierAPI quoted and then waited for the chain's slot. The limiter is not
first come, first served, so that wait was bounded only by the 120 s budget: a
simulated 5-worker scan priced a chain on a 106 s old spot (test_price_rewarm.py).
Since 2026-09-14 the chain's slot is taken first and the quote read after it.

A real TradierAPI and CachedOptionsDataProvider share one fake clock. The HTTP
session answers each quote with the clock time as its price, so a chain's
stock_price says when its quote was taken, and it records when each request went
out. The limiter admits every call, holding chosen ones for chosen seconds.

A chain Tradier refuses (429) is the same problem after the slot: the session
waited Retry-After and resent the chain on the spot it had. A server error (5xx),
an answer that never came and a lost connection were the same again, resent on
the backoff or after each 10 s timeout. The last tests run a real session against
a local server to show each is re-priced instead.
"""

import json
import socket
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.domain import protocols
from src.domain.errors import ErrorCode
from src.domain.types import Money
from src.infrastructure.api.tradier import CHAIN_RESEND_WAIT_SECONDS, TradierAPI
from src.infrastructure.cache.memory_cache import (
    PRICE_TTL_SECONDS,
    RESENDS_PER_CHAIN,
    CachedOptionsDataProvider,
    MemoryCache,
)

EXPIRY = date(2026, 10, 16)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body, self.headers = body, {}
        self.content = json.dumps(body).encode()

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeSession:
    """Quotes are priced at the clock; every request is logged as (kind, time)."""

    def __init__(self, clock):
        self.clock, self.requests = clock, []

    def get(self, url, params=None, headers=None, timeout=None):
        kind = "chain" if url.endswith("/markets/options/chains") else "quote"
        self.requests.append((kind, self.clock.now))
        if kind == "quote":
            return FakeResponse({"quotes": {"quote": {"symbol": params["symbols"],
                                                      "last": self.clock.now}}})
        return FakeResponse({"options": {"option": [
            {"strike": 100.0, "option_type": side, "bid": 1.0, "ask": 1.1}
            for side in ("call", "put")]}})


class HoldingLimiter:
    """Admits every call; the n-th admission (from 0) waits holds[n] seconds first."""

    def __init__(self, clock, holds):
        self.clock, self.holds, self.admissions = clock, holds, 0

    def wait_for_token(self, tokens=1, timeout=None):
        self.clock.now += self.holds(self.admissions)
        self.admissions += 1
        return True


def _wire(holds):
    clock = FakeClock()
    limiter = HoldingLimiter(clock, holds)
    api = TradierAPI("key", rate_limiter=limiter)
    api.session = FakeSession(clock)
    provider = CachedOptionsDataProvider(api, MemoryCache(ttl_seconds=30, clock=clock))
    return api, limiter, provider


def _chain_requests(api):
    return [t for kind, t in api.session.requests if kind == "chain"]


def test_a_quote_that_ages_past_its_ttl_waiting_for_the_chain_slot_is_not_used():
    """Quoted at t=0, the chain's slot comes at t=40: the chain went out on the 40 s
    old quote. The slot now buys a fresh quote and the chain takes the next one."""
    api, limiter, provider = _wire(lambda n: 40.0 if n == 1 else 0.0)
    provider.get_stock_price("AAPL")

    chain = provider.get_option_chain("AAPL", EXPIRY).value

    (sent,) = _chain_requests(api)
    assert sent - float(chain.stock_price.amount) <= PRICE_TTL_SECONDS
    assert [kind for kind, _ in api.session.requests] == ["quote", "quote", "chain"]
    assert limiter.admissions == len(api.session.requests)   # one slot, one request


def test_a_chain_without_a_cached_quote_is_priced_when_its_request_goes_out():
    """TradierAPI's own quote-then-chain had the same wait between the two calls."""
    api, limiter, provider = _wire(lambda n: 40.0 if n == 1 else 0.0)

    chain = provider.get_option_chain("AAPL", EXPIRY).value

    (sent,) = _chain_requests(api)
    assert sent - float(chain.stock_price.amount) <= PRICE_TTL_SECONDS
    assert limiter.admissions == len(api.session.requests)


def test_a_quote_still_fresh_when_the_slot_comes_prices_the_chain():
    """The cached quote still saves its call when it outlives the wait."""
    api, limiter, provider = _wire(lambda n: 20.0 if n == 1 else 0.0)
    provider.get_stock_price("AAPL")

    chain = provider.get_option_chain("AAPL", EXPIRY).value

    assert [kind for kind, _ in api.session.requests] == ["quote", "chain"]
    assert _chain_requests(api)[0] - float(chain.stock_price.amount) == 20.0
    assert limiter.admissions == 2


def test_a_limiter_that_outlasts_every_quote_refuses_the_chain():
    """Each quote ages out before the next slot: after a few, the chain is refused
    as the limiter refuses a call, never sent on a stale spot."""
    api, limiter, provider = _wire(lambda n: 40.0)

    result = provider.get_option_chain("AAPL", EXPIRY)

    assert result.is_err and result.error.code == ErrorCode.RATELIMIT
    assert _chain_requests(api) == []
    assert limiter.admissions == len(api.session.requests)


def test_an_admitted_chain_call_must_carry_its_spot():
    """One slot cannot pay for a quote and a chain."""
    api, limiter, _ = _wire(lambda n: 0.0)
    result = api.get_option_chain("AAPL", EXPIRY, admitted=True)
    assert result.is_err and result.error.code == ErrorCode.INVALID
    assert api.session.requests == [] and limiter.admissions == 0


def test_tradier_is_the_rate_limited_provider_the_cache_admits_through():
    assert isinstance(TradierAPI("key"), protocols.RateLimitedOptionsProvider)


# --- a chain Tradier refuses (429) is re-priced, not resent on its old spot --------
#
# TradierAPI's session retried every 429 after Retry-After (up to 65 s, 3 times).
# For an admitted chain that resend carried the spot read before the wait. These
# run a real TradierAPI session against a local server; the server prices quotes at
# the shared fake clock, and the client's `sleep` advances it.

CHAIN_BODY = json.dumps({"options": {"option": [
    {"strike": 100.0, "option_type": side, "bid": 1.0, "ask": 1.1} for side in ("call", "put")]}})

# A chain answer that never comes: the server advances the clock by the client's
# production timeout, and holds the socket past the test client's real one.
STALL = "stall"
STALL_SECONDS = 10.0
TEST_CLIENT_TIMEOUT = 0.25


class TradierServer:
    """Quotes priced at the clock; chains answered from `chain_statuses` in order
    (the last repeats): a status -- a 429 carrying Retry-After -- or STALL. Logs
    (kind, clock time)."""

    def __init__(self, clock, chain_statuses, retry_after="2"):
        self.clock, self.requests = clock, []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                chain = self.path.startswith("/markets/options/chains")
                server.requests.append(("chain" if chain else "quote", server.clock.now))
                if chain:
                    sent = sum(1 for kind, _ in server.requests if kind == "chain")
                    status = chain_statuses[min(sent, len(chain_statuses)) - 1]
                    if status == STALL:
                        server.clock.now += STALL_SECONDS
                        time.sleep(TEST_CLIENT_TIMEOUT * 3)
                        return
                    body = CHAIN_BODY if status == 200 else ""
                else:
                    status = 200
                    body = json.dumps({"quotes": {"quote": {"symbol": "AAPL",
                                                            "last": server.clock.now}}})
                payload = body.encode()
                self.send_response(status)
                if status == 429:
                    self.send_header("Retry-After", retry_after)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def kinds(self):
        return [kind for kind, _ in self.requests]


@pytest.fixture
def refusing_tradier():
    servers = []

    def make(chain_statuses, holds=lambda n: 0.0, retry_after="2"):
        clock = FakeClock()
        server = TradierServer(clock, chain_statuses, retry_after)
        servers.append(server)
        limiter = HoldingLimiter(clock, holds)
        api = TradierAPI("key", base_url=f"http://127.0.0.1:{server.http.server_port}",
                         rate_limiter=limiter)
        api.timeout = TEST_CLIENT_TIMEOUT
        api.sleep = lambda seconds: setattr(clock, "now", clock.now + seconds)
        provider = CachedOptionsDataProvider(api, MemoryCache(ttl_seconds=30, clock=clock))
        return server, limiter, api, provider

    yield make
    for server in servers:
        server.http.shutdown()


def test_an_admitted_chain_refused_429_is_returned_not_resent_by_the_session(refusing_tradier):
    server, _, api, _ = refusing_tradier([429, 200])

    result = api.get_option_chain("AAPL", EXPIRY, stock_price=Money(100.0), admitted=True)

    assert result.is_err and result.error.code == ErrorCode.RATELIMIT
    assert server.kinds() == ["chain"]
    assert server.clock.now == 1002.0            # waited out Retry-After before returning


def test_a_chain_refused_after_its_wait_aged_the_quote_goes_out_on_a_fresh_one(refusing_tradier):
    """Quoted at t=1000, the chain's slot comes at t=1029 and Tradier answers 429,
    Retry-After 2. The session resent the chain at t=1031 on the 31 s old quote. Now
    the refusal comes back to the cache, whose next slot buys a quote and prices the
    chain on it."""
    server, limiter, _, provider = refusing_tradier([429, 200], holds=lambda n: 29.0 if n == 1 else 0.0)

    chain = provider.get_option_chain("AAPL", EXPIRY).value

    assert server.kinds() == ["quote", "chain", "quote", "chain"]
    sent = [t for kind, t in server.requests if kind == "chain"][-1]
    assert sent == 1031.0 and float(chain.stock_price.amount) == 1031.0
    assert limiter.admissions == len(server.requests)     # every resend took its own slot


def test_a_quote_still_fresh_after_the_wait_prices_the_resent_chain(refusing_tradier):
    server, limiter, _, provider = refusing_tradier([429, 200])

    chain = provider.get_option_chain("AAPL", EXPIRY).value

    assert server.kinds() == ["quote", "chain", "chain"]
    assert float(chain.stock_price.amount) == 1000.0 and server.clock.now == 1002.0
    assert limiter.admissions == 3


def test_a_chain_tradier_keeps_refusing_is_rate_limited_after_a_bounded_number_of_sends(refusing_tradier):
    server, limiter, _, provider = refusing_tradier([429], retry_after="0")

    result = provider.get_option_chain("AAPL", EXPIRY)

    assert result.is_err and result.error.code == ErrorCode.RATELIMIT
    assert server.kinds().count("chain") == 1 + RESENDS_PER_CHAIN
    assert limiter.admissions == len(server.requests)


# --- a chain that gets a server error, no answer or no connection is re-priced too --
#
# The chain adapter retried a 5xx, a read timeout or a lost connection three times
# on urllib3's backoff (0, 2, 4 s): after three 10 s timeouts the last send went out
# on a spot 36 s older than the first, and none of the resends took a slot.

def _refused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]          # closed on exit: nothing listens there


def test_an_admitted_chain_that_gets_a_server_error_is_returned_not_resent_by_the_session(refusing_tradier):
    server, _, api, _ = refusing_tradier([503, 200])

    result = api.get_option_chain("AAPL", EXPIRY, stock_price=Money(100.0), admitted=True)

    assert server.kinds() == ["chain"]
    assert result.is_err and result.error.code == ErrorCode.EXTERNAL and result.error.retryable
    assert server.clock.now == 1000.0 + CHAIN_RESEND_WAIT_SECONDS


def test_an_admitted_chain_whose_answer_never_comes_is_returned_not_resent_by_the_session(refusing_tradier):
    server, _, api, _ = refusing_tradier([STALL, 200])

    result = api.get_option_chain("AAPL", EXPIRY, stock_price=Money(100.0), admitted=True)

    assert server.kinds() == ["chain"]
    assert result.is_err and result.error.code == ErrorCode.TIMEOUT and result.error.retryable
    assert server.clock.now == 1000.0 + STALL_SECONDS    # the timeout was the wait


def test_an_admitted_chain_that_cannot_connect_is_returned_for_a_resend():
    api = TradierAPI("key", base_url=f"http://127.0.0.1:{_refused_port()}")
    waits = []
    api.sleep = waits.append

    result = api.get_option_chain("AAPL", EXPIRY, stock_price=Money(100.0), admitted=True)

    assert result.is_err and result.error.code == ErrorCode.EXTERNAL and result.error.retryable
    assert waits == [CHAIN_RESEND_WAIT_SECONDS]


def test_a_chain_that_gets_a_server_error_after_its_wait_aged_the_quote_goes_out_on_a_fresh_one(refusing_tradier):
    """Quoted at t=1000, the chain's slot comes at t=1029 and Tradier answers 500.
    The session resent the chain at once on the 29 s old quote. Now the error comes
    back to the cache after the backoff, the quote has aged out, and the next slot
    buys one."""
    server, limiter, _, provider = refusing_tradier([500, 200], holds=lambda n: 29.0 if n == 1 else 0.0)

    chain = provider.get_option_chain("AAPL", EXPIRY).value

    assert server.kinds() == ["quote", "chain", "quote", "chain"]
    sent = [t for kind, t in server.requests if kind == "chain"][-1]
    assert sent == float(chain.stock_price.amount) == 1029.0 + CHAIN_RESEND_WAIT_SECONDS
    assert limiter.admissions == len(server.requests)     # every resend took its own slot


def test_a_chain_whose_answer_never_came_goes_out_again_on_a_fresh_quote(refusing_tradier):
    """Quoted at t=1000, the chain's slot comes at t=1025 and no answer comes within
    the 10 s timeout. The session resent it at t=1035 on the 35 s old quote."""
    server, limiter, _, provider = refusing_tradier([STALL, 200], holds=lambda n: 25.0 if n == 1 else 0.0)

    chain = provider.get_option_chain("AAPL", EXPIRY).value

    assert server.kinds() == ["quote", "chain", "quote", "chain"]
    sent = [t for kind, t in server.requests if kind == "chain"][-1]
    assert sent == 1035.0 and float(chain.stock_price.amount) == 1035.0
    assert limiter.admissions == len(server.requests)


def test_a_quote_still_fresh_after_a_server_error_prices_the_resent_chain(refusing_tradier):
    server, limiter, _, provider = refusing_tradier([502, 200])

    chain = provider.get_option_chain("AAPL", EXPIRY).value

    assert server.kinds() == ["quote", "chain", "chain"]
    assert float(chain.stock_price.amount) == 1000.0
    assert limiter.admissions == 3


def test_a_chain_tradier_keeps_failing_with_a_server_error_gives_up_after_a_bounded_number_of_sends(refusing_tradier):
    server, limiter, _, provider = refusing_tradier([503])

    result = provider.get_option_chain("AAPL", EXPIRY)

    assert result.is_err and result.error.code == ErrorCode.EXTERNAL
    assert server.kinds().count("chain") == 1 + RESENDS_PER_CHAIN
    assert limiter.admissions == len(server.requests)


def test_refusals_server_errors_and_timeouts_share_one_bound(refusing_tradier):
    server, limiter, _, provider = refusing_tradier([429, 503, STALL, 504], retry_after="0")

    result = provider.get_option_chain("AAPL", EXPIRY)

    assert result.is_err and result.error.code == ErrorCode.EXTERNAL
    assert server.kinds().count("chain") == 1 + RESENDS_PER_CHAIN
    assert limiter.admissions == len(server.requests)


def test_a_chain_error_a_resend_cannot_fix_is_returned_after_one_send(refusing_tradier):
    server, _, _, provider = refusing_tradier([404])

    result = provider.get_option_chain("AAPL", EXPIRY)

    assert result.is_err and result.error.code == ErrorCode.EXTERNAL and not result.error.retryable
    assert server.kinds() == ["quote", "chain"]
