"""
Tests for strategy/sizing.py — Kelly fraction and contract sizing.
"""
import pytest
from src.application.services.strategy.sizing import (
    calculate_kelly_fraction,
    effective_cap,
    apply_kelly_caps,
    calculate_contracts,
    calculate_contracts_kelly,
)
from src.config.config import StrategyConfig
from src.domain.types import Money, SizingContext


@pytest.fixture
def config():
    return StrategyConfig()


class TestCalculateKellyFraction:
    def test_high_pop_high_ev_returns_full_scale(self):
        ev_pct, pop_scale, ev_scale = calculate_kelly_fraction(
            win_rate=0.90, avg_win=100.0, avg_loss=400.0
        )
        assert pop_scale == 1.0
        assert ev_scale == 1.0

    def test_low_pop_returns_floor_scale(self):
        ev_pct, pop_scale, ev_scale = calculate_kelly_fraction(
            win_rate=0.60, avg_win=100.0, avg_loss=400.0
        )
        assert pop_scale == 0.3

    def test_mid_pop_interpolates(self):
        # At 80% POP: 0.5 + (0.80 - 0.70) * 2.5 = 0.5 + 0.25 = 0.75
        ev_pct, pop_scale, ev_scale = calculate_kelly_fraction(
            win_rate=0.80, avg_win=200.0, avg_loss=400.0
        )
        assert abs(pop_scale - 0.75) < 0.01

    def test_negative_ev_below_floor_returns_min_ev_scale(self):
        # Terrible EV: win_rate=0.1, avg_win=100, avg_loss=1000
        ev_pct, pop_scale, ev_scale = calculate_kelly_fraction(
            win_rate=0.10, avg_win=100.0, avg_loss=1000.0
        )
        assert ev_scale == 0.3  # floor
        assert ev_pct < -0.02

    def test_positive_ev_returns_high_ev_scale(self):
        # 80% win, $200 profit, $400 loss → EV = 160 - 80 = 80; ev_pct = 80/400 = 0.20
        ev_pct, pop_scale, ev_scale = calculate_kelly_fraction(
            win_rate=0.80, avg_win=200.0, avg_loss=400.0
        )
        assert ev_scale == 1.0  # 20% EV → 1.0

    def test_zero_avg_loss_returns_zero_ev_pct(self):
        ev_pct, pop_scale, ev_scale = calculate_kelly_fraction(
            win_rate=0.75, avg_win=100.0, avg_loss=0.0
        )
        assert ev_pct == 0.0


class TestEffectiveCap:
    def test_no_sizing_context_uses_config_max(self, config):
        assert effective_cap(config, None) == config.max_contracts

    def test_sizing_context_high_trr_returns_50(self, config):
        ctx = SizingContext(trr_level='HIGH')
        assert effective_cap(config, ctx) == 50

    def test_sizing_context_normal_trr_returns_100(self, config):
        ctx = SizingContext(trr_level='NORMAL')
        assert effective_cap(config, ctx) == 100

    def test_sizing_context_iv_effect_reduction_halves_cap(self, config):
        ctx = SizingContext(trr_level='NORMAL', fcst_ern_iv_effect=2.5)
        # 100 // 2 = 50
        assert effective_cap(config, ctx) == 50


class TestApplyKellyCaps:
    def test_clamps_below_min(self, config):
        result = apply_kelly_caps(config, raw_contracts=0, sizing_context=None)
        assert result >= config.kelly_min_contracts

    def test_clamps_above_max(self, config):
        result = apply_kelly_caps(config, raw_contracts=999, sizing_context=None)
        assert result == config.max_contracts

    def test_sizing_context_applies_cap(self, config):
        ctx = SizingContext(trr_level='HIGH')
        result = apply_kelly_caps(config, raw_contracts=100, sizing_context=ctx)
        assert result == 50


class TestCalculateContracts:
    def test_basic_sizing(self, config):
        # $20,000 risk budget / $400 max loss = 50 contracts
        result = calculate_contracts(config, Money(400.0))
        assert result == 50

    def test_too_large_capped(self, config):
        # $20,000 / $10 = 2000 → capped at max_contracts (100)
        result = calculate_contracts(config, Money(10.0))
        assert result == config.max_contracts

    def test_zero_max_loss_returns_zero(self, config):
        result = calculate_contracts(config, Money(0.0))
        assert result == 0


class TestCalculateContractsKelly:
    def test_invalid_max_loss_returns_min(self, config):
        result = calculate_contracts_kelly(config, Money(100.0), Money(0.0), 0.75)
        assert result == config.kelly_min_contracts

    def test_invalid_max_profit_returns_min(self, config):
        result = calculate_contracts_kelly(config, Money(0.0), Money(400.0), 0.75)
        assert result == config.kelly_min_contracts

    def test_invalid_pop_returns_min(self, config):
        result = calculate_contracts_kelly(config, Money(100.0), Money(400.0), 1.5)
        assert result == config.kelly_min_contracts

    def test_good_trade_returns_positive_contracts(self, config):
        result = calculate_contracts_kelly(
            config, max_profit=Money(100.0), max_loss=Money(400.0),
            probability_of_profit=0.75
        )
        assert result >= config.kelly_min_contracts

    def test_very_bad_ev_returns_min(self, config):
        # 10% win rate, $10 profit, $1000 loss → terrible EV
        result = calculate_contracts_kelly(
            config, max_profit=Money(10.0), max_loss=Money(1000.0),
            probability_of_profit=0.10
        )
        assert result == config.kelly_min_contracts
