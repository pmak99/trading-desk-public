"""scripts/track_iv_weekly.py must share the Tradier limiter, and price its chains
the way every other path does.

It built `TradierAPI(api_key=...)` itself, so it ran with rate_limiter=None --
outside the shared limiter, and without the retry the Container's client gets
-- while CLAUDE.md says every Tradier call goes through one. Then it took the
Container's raw client, whose get_option_chain quotes and only then waits for the
chain's slot, so the spot that picks the ATM strike aged by that wait (until
2026-09-14). It now fetches through the container's cached provider, which takes
the chain's slot first and reads the quote after it.
"""

import json
import sys
import types
from datetime import date

import scripts.track_iv_weekly as tiv  # noqa: E402
from src.infrastructure.api.tradier import TradierAPI  # noqa: E402
from src.infrastructure.cache.memory_cache import (  # noqa: E402
    PRICE_TTL_SECONDS,
    CachedOptionsDataProvider,
    MemoryCache,
)
from src.utils.rate_limiter import SlidingWindowRateLimiter  # noqa: E402

TODAY = date(2026, 9, 14)


def _run_main(monkeypatch, container=None):
    seen = {}

    def fake_fetch(tickers, api, conn, today, dry_run=False):
        seen["api"] = api
        return {}

    monkeypatch.setenv("TRADIER_API_KEY", "test-key")
    monkeypatch.setattr(tiv, "connect", lambda: types.SimpleNamespace(close=lambda: None))
    if container is None:
        monkeypatch.setattr(tiv, "fetch_and_store", fake_fetch)
    else:
        monkeypatch.setattr(tiv, "Container", lambda *a, **kw: container)
    monkeypatch.setattr(sys, "argv", ["track_iv_weekly.py", "--tickers", "SPY"])
    assert tiv.main() == 0
    return seen


def test_the_snapshot_uses_the_shared_rate_limited_client_through_the_cache(monkeypatch):
    api = _run_main(monkeypatch)["api"]
    assert isinstance(api, CachedOptionsDataProvider)
    assert isinstance(api.provider, TradierAPI)
    assert isinstance(api.provider.rate_limiter, SlidingWindowRateLimiter)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Response:
    status_code = 200

    def __init__(self, body):
        self.body, self.headers, self.content = body, {}, json.dumps(body).encode()

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class _Session:
    """Expirations, quotes priced at the clock, and chains; logs (kind, time)."""

    def __init__(self, clock):
        self.clock, self.requests = clock, []

    def get(self, url, params=None, headers=None, timeout=None):
        kind = url.rsplit("/", 1)[-1]
        self.requests.append((kind, self.clock.now))
        if kind == "expirations":
            return _Response({"expirations": {"date": ["2026-10-16"]}})
        if kind == "quotes":
            return _Response({"quotes": {"quote": {"symbol": "SPY", "last": self.clock.now}}})
        return _Response({"options": {"option": [
            {"strike": 1000.0, "option_type": side, "bid": 1.0, "ask": 1.1,
             "greeks": {"mid_iv": 0.2}} for side in ("call", "put")]}})


class _Limiter:
    """Admits every call; the chain's first slot (the third admission) comes 40 s late."""

    def __init__(self, clock):
        self.clock, self.admissions = clock, 0

    def wait_for_token(self, tokens=1, timeout=None):
        self.clock.now += 40.0 if self.admissions == 2 else 0.0
        self.admissions += 1
        return True


def test_the_snapshot_chain_is_priced_on_a_quote_taken_after_its_slot(monkeypatch):
    clock = _Clock()
    api = TradierAPI("key", rate_limiter=_Limiter(clock))
    api.session = _Session(clock)
    container = types.SimpleNamespace(
        tradier=api, cached_options_provider=CachedOptionsDataProvider(api, MemoryCache(clock=clock)))
    monkeypatch.setattr(tiv, "today_et", lambda: TODAY)
    monkeypatch.setattr(tiv, "_hv20_now", lambda ticker: 15.0)

    _run_main(monkeypatch, container)

    requests = api.session.requests
    chain_at = next(t for kind, t in requests if kind == "chains")
    quoted_at = max(t for kind, t in requests if kind == "quotes" and t <= chain_at)
    assert chain_at - quoted_at <= PRICE_TTL_SECONDS
