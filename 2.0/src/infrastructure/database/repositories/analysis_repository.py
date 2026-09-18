"""
Analysis Repository - Track all analysis runs for meta-analysis.

Logs every ticker analysis to database with:
- Timestamp
- Ticker symbol
- VRP metrics (both conventions, graded one from the VRPResult (vrp_convention))
- Recommendation
- Market conditions
- Term structure and recent-move context

⚠️ There is no strategy here, and no reader. The five strategy_* / contracts
columns and `confidence` were 100% NULL across 1,304 rows (May-Sep 2026): no
caller ever passed a selected strategy, and TickerAnalysis.confidence is never
set. Migration 2 (2026-09-13) dropped them, along with the three read methods
(recent analyses, regime statistics, strategy distribution), which had no
callers -- the regime one also still counted only EXCELLENT/GOOD as
tradeable, the MARGINAL-dropping bug CLAUDE.md warns about. Reads of this
table are SQL: Grafana panels and the A/B evaluator.
"""

import logging
from typing import TYPE_CHECKING, Optional

from common.timezone import now_et
from src.domain.enums import VRPConvention
from src.domain.market_conditions import MarketConditions
from src.domain.types import TickerAnalysis
from src.infrastructure.database.repositories.base_repository import ResilientRepository

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


class AnalysisRepository(ResilientRepository):
    """
    Repository for persisting analysis results to database.

    Inherits resilient failure handling from ResilientRepository.
    Stores complete analysis metadata for later review and optimization.
    """

    def __init__(self, pool: Optional['ConnectionPool'] = None, max_failures: int = 10):
        """
        Initialize repository.

        Args:
            pool: Connection pool (defaults to the process-wide pool)
            max_failures: Maximum consecutive failures before critical alert
        """
        super().__init__(pool=pool, max_failures=max_failures)

    def log_analysis(
        self,
        analysis: TickerAnalysis,
        market_conditions: Optional[MarketConditions] = None,
        term_slope_ratio: Optional[float] = None,
        recent_move_up_count: Optional[int] = None,
        recent_move_qtrs: Optional[int] = None,
        source: Optional[str] = None,
    ) -> None:
        """
        Log analysis run to database.

        Args:
            analysis: Complete ticker analysis
            market_conditions: Market conditions at time of analysis (optional)
            term_slope_ratio: Front ATM IV / ~30d-out ATM IV (>1 = backwardation)
            recent_move_up_count: Up-quarters out of the last (up to 4) prior
                quarters' close_move_pct — observational only, no predictive
                power found in a Sep 2026 OOS backtest
            recent_move_qtrs: How many prior quarters were actually available
            source: which command produced this row — 'analyze', 'scan',
                'whisper' or 'ticker'. NULL on rows written before
                migration 021.
        """
        try:
            with self._get_connection() as conn:
                try:
                    cursor = conn.cursor()

                    # Extract key metrics
                    implied_move = float(analysis.implied_move.implied_move_pct.value)
                    vrp = analysis.vrp
                    convention = VRPConvention(vrp.move_metric).value   # ValueError on e.g. "gap"
                    columns = {
                        "intraday": [analysis.historical_intraday_mean_pct, analysis.vrp_intraday_ratio],
                        "close": [analysis.historical_close_mean_pct, analysis.vrp_close_ratio],
                    }
                    # The graded convention's pair is the VRPResult's own, whatever the caller set.
                    columns[convention] = [float(vrp.historical_mean_move_pct.value), vrp.vrp_ratio]

                    # Market conditions
                    vix_level = None
                    vix_regime = None
                    if market_conditions:
                        vix_level = float(market_conditions.vix_level.value)
                        vix_regime = market_conditions.regime

                    # Consistency metrics
                    consistency_score = None
                    if analysis.consistency:
                        consistency_score = analysis.consistency.consistency_score

                    cursor.execute(
                        """
                        INSERT INTO analysis_log (
                            timestamp,
                            ticker,
                            earnings_date,
                            expiration,

                            -- Core metrics
                            implied_move_pct,
                            historical_intraday_mean_pct,
                            vrp_intraday_ratio,
                            recommendation,

                            -- Consistency
                            consistency_score,

                            -- Market conditions
                            vix_level,
                            vix_regime,

                            -- Close-to-close VRP pair (the intraday pair is above;
                            -- vrp_convention names which one graded recommendation)
                            historical_close_mean_pct,
                            vrp_close_ratio,

                            -- IV term-structure event-vol multiple
                            term_slope_ratio,

                            -- Recent-move streak (observational only)
                            recent_move_up_count,
                            recent_move_qtrs,

                            -- Additional context
                            raw_analysis,

                            -- Provenance (migration 021)
                            source,

                            -- Which convention graded recommendation (migration 6)
                            vrp_convention
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            # ⚠️ Was datetime.now().isoformat() -- a NAIVE ET
                            # wall clock. Against a timestamptz column that is
                            # parsed in the session TimeZone (UTC in the
                            # image), which would land every new row 4 hours
                            # early while still looking right in the CSV. This
                            # is the same trap the one-time loader guards
                            # against; now_et() is tz-aware, so psycopg sends
                            # the offset and the instant is unambiguous.
                            now_et(),
                            analysis.ticker,
                            analysis.earnings_date,
                            analysis.expiration,
                            implied_move,
                            columns["intraday"][0],
                            columns["intraday"][1],
                            analysis.recommendation.value,
                            consistency_score,
                            vix_level,
                            vix_regime,
                            columns["close"][0],
                            columns["close"][1],
                            term_slope_ratio,
                            recent_move_up_count,
                            recent_move_qtrs,
                            self._serialize_analysis(analysis),
                            source,
                            convention,
                        ),
                    )

                    conn.commit()
                    logger.debug(f"Logged analysis for {analysis.ticker} to database")

                    # Reset failure count on success (inherited from ResilientRepository)
                    self._record_success()

                except Exception:
                    # Rollback on any failure to prevent partial writes
                    conn.rollback()
                    raise

        except Exception as e:
            # Record failure using inherited method
            self._record_failure(e, f"log analysis for {analysis.ticker}")
            # Don't raise - logging failure shouldn't break analysis

    def _serialize_analysis(self, analysis: TickerAnalysis) -> str:
        """
        Serialize complete analysis to JSON for storage.

        Args:
            analysis: Ticker analysis

        Returns:
            JSON string representation
        """
        import json

        # Simple serialization - could be enhanced with full dataclass serialization
        data = {
            'ticker': analysis.ticker,
            'recommendation': analysis.recommendation.value,
            'vrp_ratio': analysis.vrp.vrp_ratio,
            'implied_move': float(analysis.implied_move.implied_move_pct.value),
        }

        return json.dumps(data)
