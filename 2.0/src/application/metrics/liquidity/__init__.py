"""LiquidityScorer — thin coordinator delegating to focused liquidity modules."""
import logging
from typing import Optional, Tuple, Dict, Any

from src.application.metrics.liquidity.score import LiquidityScore, LiquidityThresholds
from src.application.metrics.liquidity import scoring, hybrid
from src.config.config import ThresholdsConfig

logger = logging.getLogger(__name__)

__all__ = ["LiquidityScorer", "LiquidityScore"]


class LiquidityScorer:
    """
    Scores option liquidity using multiple factors.

    Thin coordinator: all computation is in liquidity.scoring and liquidity.hybrid.
    Public interface and constructor signature are identical to the original monolith.
    """

    def __init__(
        self,
        min_oi: int = 10,
        warning_oi: int = 50,
        good_oi: int = 100,
        excellent_oi: int = 200,
        min_volume: int = 0,
        good_volume: int = 100,
        excellent_volume: int = 250,
        max_spread_pct: float = 25.0,
        warning_spread_pct: float = 18.0,
        good_spread_pct: float = 12.0,
        excellent_spread_pct: float = 12.0,
    ):
        # Store as attributes for backward compat (tests may read self.min_oi etc.)
        self.min_oi = min_oi
        self.warning_oi = warning_oi
        self.good_oi = good_oi
        self.excellent_oi = excellent_oi
        self.min_volume = min_volume
        self.good_volume = good_volume
        self.excellent_volume = excellent_volume
        self.max_spread_pct = max_spread_pct
        self.warning_spread_pct = warning_spread_pct
        self.good_spread_pct = good_spread_pct
        self.excellent_spread_pct = excellent_spread_pct
        self.oi_weight = 0.40
        self.volume_weight = 0.30
        self.spread_weight = 0.25
        self.depth_weight = 0.05

        self._t = LiquidityThresholds(
            min_oi=min_oi, warning_oi=warning_oi, good_oi=good_oi,
            excellent_oi=excellent_oi, min_volume=min_volume,
            good_volume=good_volume, excellent_volume=excellent_volume,
            max_spread_pct=max_spread_pct, warning_spread_pct=warning_spread_pct,
            good_spread_pct=good_spread_pct, excellent_spread_pct=excellent_spread_pct,
        )

    @classmethod
    def from_config(cls, config: ThresholdsConfig) -> 'LiquidityScorer':
        return cls(
            min_oi=config.liquidity_reject_min_oi,
            warning_oi=config.liquidity_warning_min_oi,
            good_oi=config.liquidity_good_min_oi,
            excellent_oi=config.liquidity_excellent_min_oi,
            min_volume=config.liquidity_reject_min_volume,
            good_volume=config.liquidity_reject_min_volume,
            excellent_volume=config.liquidity_excellent_min_volume,
            max_spread_pct=config.liquidity_reject_max_spread_pct,
            warning_spread_pct=config.liquidity_warning_max_spread_pct,
            good_spread_pct=config.liquidity_good_max_spread_pct,
            excellent_spread_pct=config.liquidity_excellent_max_spread_pct,
        )

    # --- Public API ---

    def calculate_spread_pct(self, option):
        return scoring.calculate_spread_pct(option)

    def score_option(self, option):
        return scoring.score_option(option, self._t)

    def score_strategy_legs(self, legs):
        return scoring.score_strategy_legs(legs, self._t)

    def classify_option_tier(self, option):
        return scoring.classify_option_tier(option, self._t)

    def classify_option_tier_oi_only(self, option):
        return scoring.classify_option_tier_oi_only(option, self._t)

    def classify_straddle_tier(self, call, put):
        return scoring.classify_straddle_tier(call, put, self._t)

    def classify_straddle_tier_market_aware(self, call, put):
        return scoring.classify_straddle_tier_market_aware(call, put, self._t)

    def calculate_dynamic_thresholds(self, stock_price, max_loss_budget=20000.0, credit_ratio=0.30):
        return hybrid.calculate_dynamic_thresholds(stock_price, max_loss_budget, credit_ratio)

    def classify_hybrid_tier(self, chain, implied_move_pct, stock_price=None,
                             max_loss_budget=20000.0, use_dynamic_thresholds=True):
        return hybrid.classify_hybrid_tier(
            chain, implied_move_pct, self._t, stock_price, max_loss_budget, use_dynamic_thresholds
        )

    def classify_hybrid_tier_market_aware(self, chain, implied_move_pct, stock_price=None,
                                          max_loss_budget=20000.0, use_dynamic_thresholds=True):
        return hybrid.classify_hybrid_tier_market_aware(
            chain, implied_move_pct, self._t, stock_price, max_loss_budget, use_dynamic_thresholds
        )

    # --- Backward-compat shims (private methods called directly by existing tests) ---

    def _classify_tier(self, oi, volume, spread_pct):
        return scoring.classify_tier(oi, volume, spread_pct, self._t)

    def _classify_tier_oi_only(self, oi, spread_pct):
        return scoring.classify_tier_oi_only(oi, spread_pct, self._t)

    def _score_open_interest(self, oi):
        return scoring.score_open_interest(oi, self._t)

    def _score_volume(self, volume):
        return scoring.score_volume(volume, self._t)

    def _score_spread(self, spread_pct):
        return scoring.score_spread(spread_pct, self._t)

    def _score_depth(self, bid_size, ask_size):
        return scoring.score_depth(bid_size, ask_size)

    def _redistribute_weights_without_depth(self):
        return scoring.redistribute_weights_without_depth(self._t)

    def _find_strike_outside_move(self, chain, implied_move_pct, is_call):
        return hybrid.find_strike_outside_move(chain, implied_move_pct, is_call)

    def _find_delta_strike(self, chain, target_delta, is_call):
        return hybrid.find_delta_strike(chain, target_delta, is_call)
