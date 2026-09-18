"""
Market Conditions Analyzer - VIX Regime Classification.

Classifies current market volatility regime using VIX (CBOE Volatility Index),
for display and analysis_log. It adjusts nothing: no VRP tier, strategy or size.
The per-regime threshold multipliers, size multipliers and strategy guidance it
used to carry were removed 2026-09-14 — the thresholds after a pre-registered test
could not support them (scripts/research/vix_adaptive_thresholds.py), the size
multiplier because nothing ever applied it, and the guidance because it
recommended iron condors, which are banned.

Regime boundaries: src/domain/vix_regime.py. The MarketConditions value it
returns: src/domain/market_conditions.py.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from common.timezone import now_et
from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.market_conditions import MarketConditions
from src.domain.protocols import OptionsDataProvider
from src.domain.types import Percentage
from src.domain.vix_regime import classify_vix_regime

logger = logging.getLogger(__name__)


class MarketConditionsAnalyzer:
    """
    Analyze market conditions using VIX levels.

    VIX represents implied volatility of S&P 500 index options.
    It's often called the "fear gauge" as it spikes during market stress.

    Historical context:
    - Average VIX: ~17-20
    - 2017-2019: Often below 15 (complacent market)
    - COVID crash (Mar 2020): Peaked at 82
    - 2022-2023: Mostly 15-30 range
    """

    def __init__(
        self,
        provider: OptionsDataProvider,
        vix_symbol: str = "VIX",
    ):
        """
        Initialize market conditions analyzer.

        Args:
            provider: Options data provider (must support VIX queries)
            vix_symbol: Symbol for VIX (default "VIX")
        """
        self.provider = provider
        self.vix_symbol = vix_symbol
        self._cached_conditions: Optional[MarketConditions] = None
        self._cache_expires: Optional[datetime] = None
        self._cache_ttl_seconds: int = 900  # 15-minute TTL — one fetch per scan session
        self._cache_lock = threading.Lock()

    def get_current_conditions(self) -> Result[MarketConditions, AppError]:
        """
        Get current market conditions and VIX regime.

        Results are cached for 15 minutes so a scan of N tickers makes one
        VIX API call rather than N.

        Returns:
            Result with MarketConditions or AppError
        """
        with self._cache_lock:
            now = now_et()
            if self._cached_conditions and self._cache_expires and now < self._cache_expires:
                logger.debug("Returning cached VIX conditions (TTL not expired)")
                return Ok(self._cached_conditions)

            logger.info("Analyzing market conditions (VIX regime)")

            # Get VIX data
            vix_result = self._get_vix_level()
            if vix_result.is_err:
                return Err(vix_result.error)

            vix_level = vix_result.value

            # Classify regime
            regime = self._classify_regime(vix_level.value)

            conditions = MarketConditions(
                vix_level=vix_level,
                regime=regime,
                analysis_time=now_et(),
            )

            logger.info(f"Market conditions: VIX={vix_level.value:.1f}% ({regime})")

            self._cached_conditions = conditions
            self._cache_expires = now + timedelta(seconds=self._cache_ttl_seconds)

            return Ok(conditions)

    def _get_vix_level(self) -> Result[Percentage, AppError]:
        """
        Fetch current VIX level.

        Returns:
            Result with VIX as Percentage
        """
        # Try to get VIX from provider
        # Most providers support VIX as a regular ticker
        try:
            price_result = self.provider.get_stock_price(self.vix_symbol)
            if price_result.is_err:
                return Err(price_result.error)

            price = price_result.value
            vix_level = Percentage(float(price.amount))

            # Validate VIX level is reasonable (5-100 range)
            if vix_level.value < 5 or vix_level.value > 100:
                return Err(
                    AppError(
                        ErrorCode.INVALID,
                        f"VIX level {vix_level.value:.1f} outside valid range (5-100)"
                    )
                )

            return Ok(vix_level)

        except Exception as e:
            return Err(
                AppError(
                    ErrorCode.EXTERNAL,
                    f"Failed to fetch VIX: {str(e)}"
                )
            )

    def _classify_regime(self, vix_level: float) -> str:
        """
        Classify volatility regime based on VIX level.

        Delegates to shared utility for consistency across application.

        Args:
            vix_level: Current VIX level

        Returns:
            Regime name (very_low/low/normal/normal_high/elevated/elevated_high/high/extreme)
        """
        return classify_vix_regime(vix_level)
