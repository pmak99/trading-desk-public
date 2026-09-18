"""/harvest fetches chains through container.cached_options_provider.

Found in the 2026-09-13 audit (3.4): harvest's live IV30 (`harvest_live._fetch_live_iv30`)
and its credit check (`market_data.validate_harvest_chain`) called the raw
`container.tradier` client, while the scan's liquidity checks beside them went
through the cache. Scan code must fetch through the cached provider (CLAUDE.md,
Rate limiting): the same (ticker, expiration) chain is then one Tradier call, not
one per caller.

The cache suits a live screen: keyed by ticker and expiration, only an Ok result
cached (a failed fetch is retried on the next call), a chain at most 60 s old and
a listing at most 1 h (per-entry TTLs, test_memory_cache.py; until 2026-09-14
every entry lived the cache-wide 30 s).
"""

from datetime import date, timedelta

from src.domain.errors import AppError, Err, ErrorCode, Ok  # noqa: E402
from src.infrastructure.cache.memory_cache import (  # noqa: E402
    CachedOptionsDataProvider,
    MemoryCache,
)


class FakeTradier:
    def __init__(self, chain, expirations=()):
        self.chain, self.expirations = chain, list(expirations)
        self.chain_calls, self.expiration_calls = [], []

    def get_option_chain(self, ticker, expiration, stock_price=None):
        self.chain_calls.append((ticker, expiration))
        if self.chain is None:
            return Err(AppError(ErrorCode.NODATA, "no chain"))
        return Ok(self.chain)

    def get_expirations(self, ticker):
        self.expiration_calls.append(ticker)
        return Ok(self.expirations)


class HarvestContainer:
    """Only what harvest reads. The raw client is a trap."""

    def __init__(self, tradier):
        self.cached_options_provider = CachedOptionsDataProvider(
            provider=tradier, cache=MemoryCache(ttl_seconds=30))

    @property
    def tradier(self):
        raise AssertionError("harvest used the raw Tradier client; fetch through "
                             "container.cached_options_provider")


def test_the_credit_check_fetches_through_the_cache(sample_option_chain):
    from scripts.scan.market_data import validate_harvest_chain

    tradier = FakeTradier(sample_option_chain)
    container = HarvestContainer(tradier)
    expiry = sample_option_chain.expiration

    first = validate_harvest_chain("AAPL", expiry, container)
    second = validate_harvest_chain("AAPL", expiry, container)

    assert first["error"] is None and first["stock_price"] == 100.0
    assert second == first
    assert tradier.chain_calls == [("AAPL", expiry)]


def test_a_failed_chain_is_not_cached(sample_option_chain):
    from scripts.scan.market_data import validate_harvest_chain

    tradier = FakeTradier(None)
    container = HarvestContainer(tradier)
    expiry = sample_option_chain.expiration

    assert validate_harvest_chain("AAPL", expiry, container)["error"]
    tradier.chain = sample_option_chain
    assert validate_harvest_chain("AAPL", expiry, container)["error"] is None
    assert len(tradier.chain_calls) == 2


def test_live_iv30_fetches_expirations_and_chain_through_the_cache(sample_option_chain):
    from scripts.scan import harvest_live

    today = date(2026, 5, 24)
    tradier = FakeTradier(sample_option_chain, expirations=[today + timedelta(days=30)])
    container = HarvestContainer(tradier)

    iv = harvest_live._fetch_live_iv30(container.cached_options_provider, "AAPL", today)
    again = harvest_live._fetch_live_iv30(container.cached_options_provider, "AAPL", today)

    assert iv == again == 35.0
    assert tradier.expiration_calls == ["AAPL"]
    assert tradier.chain_calls == [("AAPL", today + timedelta(days=30))]


def test_the_live_candidate_fetch_hands_the_cached_provider_to_every_chain_fetch(monkeypatch):
    from scripts.scan import harvest_live

    class Conn:
        def execute(self, *args):
            class Rows(list):
                def fetchone(self):
                    return None
            return Rows()

        def close(self):
            pass

    container = HarvestContainer(FakeTradier(None))
    seen = []
    monkeypatch.setattr(harvest_live, "connect", Conn)
    monkeypatch.setattr(harvest_live, "_fetch_closes_yf", lambda symbol, period: [])
    monkeypatch.setattr(harvest_live, "_fetch_live_iv30",
                        lambda api, ticker, today: seen.append(api))

    harvest_live.get_live_harvest_candidates(container)

    assert seen and all(api is container.cached_options_provider for api in seen)
