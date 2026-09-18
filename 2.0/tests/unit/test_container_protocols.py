"""The container hands out its data providers as the domain's protocols.

Audit 5.2 (2026-09-14): src/domain/protocols.py defined the ports, but only four
modules used them and the container was typed against concrete classes, so
nothing held TradierAPI, the cache wrapper, MemoryCache or PricesRepository to
the protocols they were said to implement. Two did not: CachedOptionsDataProvider
took no `stock_price` hint, and PriceRepository.save_historical_move still named
a `ticker` argument the repository dropped.

Covered here: the options data provider (raw and cached), the cache, the
prices repository and the implied move calculator. The rest of the container stays concrete -- see
test_what_stays_concrete_is_listed.

A structural check, not mypy: every protocol method exists on the implementation,
takes the protocol's parameters by the same names in the same order with the same
defaults, and anything extra it takes is optional.
"""

import ast
import inspect
import typing
from pathlib import Path

import pytest

from src.application.metrics.implied_move import ImpliedMoveCalculator
from src.application.metrics.implied_move_interpolated import (
    ImpliedMoveCalculatorInterpolated,
)
from src.container import Container
from src.domain import protocols
from src.infrastructure.api.tradier import TradierAPI
from src.infrastructure.cache.memory_cache import CachedOptionsDataProvider, MemoryCache
from src.infrastructure.database.repositories.prices_repository import PricesRepository

ENGINE = Path(__file__).resolve().parents[2]

HANDED_OUT = {
    "tradier": protocols.OptionsDataProvider,
    "cached_options_provider": protocols.CachedOptionsProvider,
    "cache": protocols.CacheProvider,
    "prices_repository": protocols.PriceRepository,
    # Either calculator, per USE_INTERPOLATED_MOVE; neither subclasses the other,
    # so the concrete class it was typed as was wrong half the time (mypy, 2026-09-14).
    "implied_move_calculator": protocols.ImpliedMoveCalculator,
}

IMPLEMENTATIONS = [
    (protocols.OptionsDataProvider, TradierAPI),
    (protocols.RateLimitedOptionsProvider, TradierAPI),
    (protocols.OptionsDataProvider, CachedOptionsDataProvider),
    (protocols.CachedOptionsProvider, CachedOptionsDataProvider),
    (protocols.CacheProvider, MemoryCache),
    (protocols.PriceRepository, PricesRepository),
    (protocols.ImpliedMoveCalculator, ImpliedMoveCalculator),
    (protocols.ImpliedMoveCalculator, ImpliedMoveCalculatorInterpolated),
]


def protocol_methods(protocol):
    names = set()
    for klass in protocol.__mro__:
        if klass in (typing.Protocol, typing.Generic, object):
            continue
        names |= {n for n, v in vars(klass).items() if callable(v) and not n.startswith("_")}
    return sorted(names)


@pytest.mark.parametrize("name,protocol", sorted(HANDED_OUT.items()))
def test_the_container_hands_out_the_protocol(name, protocol):
    hints = typing.get_type_hints(getattr(Container, name).fget)
    assert hints["return"] is protocol


@pytest.mark.parametrize("protocol,implementation", IMPLEMENTATIONS,
                         ids=[f"{i.__name__}-{p.__name__}" for p, i in IMPLEMENTATIONS])
def test_the_implementation_conforms(protocol, implementation):
    for method in protocol_methods(protocol):
        assert hasattr(implementation, method), f"{implementation.__name__} lacks {method}"
        wanted = list(inspect.signature(getattr(protocol, method)).parameters.values())[1:]
        offered = list(inspect.signature(getattr(implementation, method)).parameters.values())[1:]
        assert [(p.name, p.default) for p in offered[:len(wanted)]] == \
               [(p.name, p.default) for p in wanted], f"{implementation.__name__}.{method}"
        extra = offered[len(wanted):]
        assert all(p.default is not inspect.Parameter.empty or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                   for p in extra), f"{implementation.__name__}.{method} requires {extra}"


# Modules that take an OptionsDataProvider and call it as `self.provider.<method>`.
PROVIDER_CONSUMERS = [
    "src/application/metrics/implied_move.py",
    "src/application/metrics/implied_move_interpolated.py",
    "src/application/metrics/skew_enhanced.py",
    "src/application/metrics/market_conditions.py",
]


@pytest.mark.parametrize("rel", PROVIDER_CONSUMERS)
def test_consumers_call_only_what_the_protocol_offers(rel):
    tree = ast.parse((ENGINE / rel).read_text(encoding="utf-8"))
    called = {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
        and node.value.attr == "provider" and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }
    assert called, f"{rel} no longer calls self.provider -- update this list"
    assert called <= set(protocol_methods(protocols.OptionsDataProvider)), called


def test_the_cache_wrapper_uses_a_price_hint_it_is_given():
    """The protocol's `stock_price` hint, honoured by the wrapper: the chain fetch
    gets the caller's price, and the cached quote is left alone."""
    from src.domain.errors import Ok
    from src.domain.types import Money
    from src.infrastructure.cache.memory_cache import CACHE_VERSION

    class Provider:
        def __init__(self):
            self.hints = []

        def get_option_chain(self, ticker, expiration, stock_price=None):
            self.hints.append(stock_price)
            return Ok(type("Chain", (), {"stock_price": Money(99)})())

    provider, cache = Provider(), MemoryCache()
    wrapper = CachedOptionsDataProvider(provider=provider, cache=cache)
    wrapper.get_option_chain("AAPL", "2026-09-18", stock_price=Money(101))
    assert provider.hints == [Money(101)]
    assert cache.get(f"{CACHE_VERSION}:stock_price:AAPL") is None

    # Without a hint the cached quote is still what it injects.
    cache.set(f"{CACHE_VERSION}:stock_price:MSFT", Ok(Money(50)))
    wrapper.get_option_chain("MSFT", "2026-09-18")
    assert provider.hints[-1] == Money(50)


# Container properties still typed as classes, and why. A property moved to a
# protocol comes off this list; a new concrete one goes on it.
STAYS_CONCRETE = {
    "finnhub": "EarningsDataProvider names get_earnings_date, which FinnhubAPI lacks",
    "earnings_repository": "the protocol lacks get_timing (analyze.py) and its "
                           "save_earnings_event signature differs",
    "db_pool": "a psycopg_pool ConnectionPool, no port",
    "vrp_calculator": "application service",
    "skew_analyzer": "application service (SkewAnalyzer protocol names analyze, "
                     "the class analyze_skew_curve)",
    "consistency_analyzer": "application service (ConsistencyAnalyzer protocol names "
                            "analyze, the class analyze_consistency)",
    "liquidity_scorer": "application service",
    "market_conditions_analyzer": "application service",
    "strategy_generator": "application service",
    "analyzer": "application service",
    "health_check_service": "application service",
    "analysis_repository": "no protocol",
    "concurrent_scanner": "utility",
}


def test_what_stays_concrete_is_listed():
    properties = {n for n, v in vars(Container).items() if isinstance(v, property)}
    assert properties == set(HANDED_OUT) | set(STAYS_CONCRETE)
