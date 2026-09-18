"""The scan's liquidity check assumes the position the engine would size.

The hybrid check turns a position size into required open interest (1x / 2x /
5x contracts for REJECT / WARNING / GOOD). It sized that position from a
hardcoded $20,000 max-loss budget, in six places, while the engine sizes from
RISK_BUDGET_PER_TRADE (250,000) x KELLY_FRACTION (0.25) capped at
SIZING_DEFAULT_CAP (50). A $5-wide spread read as 57 contracts and a $20-wide
one as 14 when the engine sizes them at 50 (the cap) and 44 -- 35 since
2026-09-16, when both began holding max loss to MAX_LOSS_WARN_THRESHOLD. Now one
formula, in src/domain/types.py, serves the sizer and the scan.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.application.metrics.liquidity.hybrid import (  # noqa: E402
    calculate_dynamic_thresholds,
    classify_hybrid_tier,
)
from src.application.metrics.liquidity.score import LiquidityThresholds  # noqa: E402
from src.application.services.strategy.sizing import (
    calculate_contracts_kelly,  # noqa: E402
)
from src.config.config import Config, StrategyConfig  # noqa: E402
from src.domain.types import Money, SizingContext, full_size_contracts  # noqa: E402

ENGINE_ROOT = Path(__file__).resolve().parents[2]


def test_default_risk_budget_matches_env_example_and_claude_md(monkeypatch):
    env_example = (ENGINE_ROOT / ".env.example").read_text()
    documented = float(re.search(r"^RISK_BUDGET_PER_TRADE=(\d+)", env_example, re.M).group(1))
    assert documented == 250000
    assert StrategyConfig().risk_budget_per_trade == documented
    monkeypatch.delenv("RISK_BUDGET_PER_TRADE", raising=False)
    assert Config.from_env().strategy.risk_budget_per_trade == documented


@pytest.mark.parametrize("max_loss", [175.0, 350.0, 700.0, 1400.0, 3500.0, 7000.0, 90_000.0])
def test_full_size_is_what_the_sizer_gives_at_full_quality_with_no_flag(max_loss):
    """One source: the scan's position is the sizer's, POP/EV scale 1.0, no flag."""
    config = StrategyConfig()
    sizer = calculate_contracts_kelly(
        config,
        max_profit=Money(max_loss),           # EV far above 5%: ev_scale 1.0
        max_loss=Money(max_loss),
        probability_of_profit=0.95,           # pop_scale 1.0
        sizing_context=SizingContext.from_strategy_config(config),
    )
    assert full_size_contracts(config, max_loss) == sizer


def test_full_size_follows_the_sizing_knobs():
    config = StrategyConfig()
    assert full_size_contracts(config, 350.0) == 50                    # 178 capped at 50
    # 62,500 / 3,500 = 17, $59,500 of max loss: held to the $50,000 warning line
    assert full_size_contracts(config, 3500.0) == 14
    assert full_size_contracts(StrategyConfig(max_loss_warn_threshold=0.0), 3500.0) == 17
    assert full_size_contracts(StrategyConfig(max_loss_warn_threshold=70000.0), 3500.0) == 17
    tuned = StrategyConfig(sizing_default_cap=30)
    assert full_size_contracts(tuned, 350.0) == 30
    ceiling = StrategyConfig(sizing_default_cap=80, sizing_base_cap=60)
    assert full_size_contracts(ceiling, 350.0) == 60
    smaller = StrategyConfig(risk_budget_per_trade=20000.0)
    assert full_size_contracts(smaller, 3500.0) == 1                   # floored at KELLY_MIN


@pytest.mark.parametrize("price,width", [
    (15.0, 2.5), (50.0, 5.0), (150.0, 10.0), (300.0, 20.0), (700.0, 50.0), (1500.0, 100.0),
])
def test_dynamic_thresholds_size_the_engines_position(price, width):
    config = StrategyConfig()
    t = calculate_dynamic_thresholds(price, config)
    contracts = full_size_contracts(config, (width - width * 0.30) * 100)
    assert t["spread_width"] == width
    assert t["contracts"] == contracts
    assert (t["min_oi"], t["warning_oi"], t["good_oi"]) == (contracts, 2 * contracts, 5 * contracts)


def test_dynamic_thresholds_need_the_sizing_config():
    from datetime import date

    from src.domain.types import Money as _Money
    from src.domain.types import OptionChain

    chain = OptionChain(ticker="X", expiration=date(2026, 10, 16),
                        stock_price=_Money(100.0), calls={}, puts={})
    with pytest.raises(ValueError, match="StrategyConfig"):
        classify_hybrid_tier(chain, 5.0, LiquidityThresholds())


def test_scan_check_sizes_from_the_container_config(monkeypatch):
    from scripts.scan import market_data

    monkeypatch.setattr(market_data, "_hybrid_liquidity_cache", {})
    container = MagicMock()
    container.cached_options_provider.get_option_chain.return_value.is_err = False
    container.liquidity_scorer.classify_hybrid_tier_market_aware.return_value = (
        "GOOD", True, "open", {"method": "C", "call_strike": 1.0, "put_strike": 1.0,
                               "call_oi": 1, "put_oi": 1, "min_oi": 1},
    )
    from datetime import date
    market_data.check_liquidity_hybrid("X", date(2026, 10, 16), 5.0, container)

    kwargs = container.liquidity_scorer.classify_hybrid_tier_market_aware.call_args.kwargs
    assert kwargs["strategy_config"] is container.config.strategy


def test_no_hardcoded_scan_budget_remains():
    """The $20k literal lived in six places; a budget parameter is how it came back."""
    offenders = []
    for root in ("src", "scripts"):
        for path in (ENGINE_ROOT / root).rglob("*.py"):
            if "max_loss_budget" in path.read_text():
                offenders.append(str(path.relative_to(ENGINE_ROOT)))
    assert offenders == []
