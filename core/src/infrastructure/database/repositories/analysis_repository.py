"""
Analysis Repository - Track all analysis runs for meta-analysis.

Logs every ticker analysis to database with:
- Timestamp
- Ticker symbol
- VRP metrics
- Recommendation
- Strategy selected
- Market conditions

Enables later meta-analysis:
- Strategy performance tracking
- Pattern detection
- Parameter optimization
- Success rate by regime
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.domain.types import TickerAnalysis, Strategy, StrategyRecommendation
from src.application.metrics.market_conditions import MarketConditions
from src.infrastructure.database.repositories.base_repository import ResilientRepository

logger = logging.getLogger(__name__)


class AnalysisRepository(ResilientRepository):
    """
    Repository for persisting analysis results to database.

    Inherits resilient failure handling from ResilientRepository.
    Stores complete analysis metadata for later review and optimization.
    """

    def __init__(self, db_path: str | Path, pool=None, max_failures: int = 10):
        """
        Initialize repository with database path.

        Args:
            db_path: Path to SQLite database
            pool: Optional connection pool (uses direct connections if None)
            max_failures: Maximum consecutive failures before critical alert
        """
        super().__init__(db_path, pool=pool, max_failures=max_failures)

    def log_analysis(
        self,
        analysis: TickerAnalysis,
        market_conditions: Optional[MarketConditions] = None,
        selected_strategy: Optional[Strategy] = None,
    ) -> None:
        """
        Log analysis run to database.

        Args:
            analysis: Complete ticker analysis
            market_conditions: Market conditions at time of analysis (optional)
            selected_strategy: Strategy selected for execution (optional)
        """
        try:
            with self._get_connection() as conn:
                try:
                    cursor = conn.cursor()

                    # Extract key metrics
                    vrp_ratio = analysis.vrp.vrp_ratio
                    implied_move = float(analysis.implied_move.implied_move_pct.value)
                    historical_mean = float(analysis.vrp.historical_mean_move_pct.value)

                    # Market conditions
                    vix_level = None
                    vix_regime = None
                    if market_conditions:
                        vix_level = float(market_conditions.vix_level.value)
                        vix_regime = market_conditions.regime

                    # Strategy info
                    strategy_type = None
                    strategy_score = None
                    strategy_pop = None
                    strategy_rr = None
                    contracts = None
                    if selected_strategy:
                        strategy_type = selected_strategy.strategy_type.value
                        strategy_score = selected_strategy.overall_score
                        strategy_pop = selected_strategy.probability_of_profit
                        strategy_rr = selected_strategy.reward_risk_ratio
                        contracts = selected_strategy.contracts

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
                            historical_mean_pct,
                            vrp_ratio,
                            recommendation,
                            confidence,

                            -- Consistency
                            consistency_score,

                            -- Market conditions
                            vix_level,
                            vix_regime,

                            -- Strategy selected
                            strategy_type,
                            strategy_score,
                            strategy_pop,
                            strategy_rr,
                            contracts,

                            -- Additional context
                            raw_analysis
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            datetime.now().isoformat(),
                            analysis.ticker,
                            analysis.earnings_date.isoformat(),
                            analysis.expiration.isoformat(),
                            implied_move,
                            historical_mean,
                            vrp_ratio,
                            analysis.recommendation.value,
                            analysis.confidence,
                            consistency_score,
                            vix_level,
                            vix_regime,
                            strategy_type,
                            strategy_score,
                            strategy_pop,
                            strategy_rr,
                            contracts,
                            self._serialize_analysis(analysis),
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

    def get_recent_analyses(
        self,
        limit: int = 100,
        min_vrp: Optional[float] = None,
        recommendation: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve recent analyses from database.

        Args:
            limit: Maximum number of records to return
            min_vrp: Filter by minimum VRP ratio
            recommendation: Filter by recommendation type

        Returns:
            List of analysis records as dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                query = "SELECT * FROM analysis_log WHERE 1=1"
                params = []

                if min_vrp is not None:
                    query += " AND vrp_ratio >= ?"
                    params.append(min_vrp)

                if recommendation is not None:
                    query += " AND recommendation = ?"
                    params.append(recommendation)

                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)

                cursor.execute(query, params)

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Failed to retrieve analyses: {e}")
            return []

    def get_statistics_by_regime(self) -> Dict[str, Dict[str, Any]]:
        """
        Get analysis statistics grouped by VIX regime.

        Returns:
            Dict mapping regime name to statistics
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    SELECT
                        vix_regime,
                        COUNT(*) as count,
                        AVG(vrp_ratio) as avg_vrp,
                        AVG(strategy_score) as avg_score,
                        AVG(strategy_pop) as avg_pop,
                        COUNT(CASE WHEN recommendation IN ('excellent', 'good') THEN 1 END) as tradeable_count
                    FROM analysis_log
                    WHERE vix_regime IS NOT NULL
                    GROUP BY vix_regime
                    ORDER BY count DESC
                    """
                )

                results = {}
                for row in cursor.fetchall():
                    regime = row[0]
                    results[regime] = {
                        'count': row[1],
                        'avg_vrp': row[2],
                        'avg_score': row[3],
                        'avg_pop': row[4],
                        'tradeable_count': row[5],
                        'tradeable_pct': (row[5] / row[1] * 100) if row[1] > 0 else 0,
                    }

                return results

        except Exception as e:
            logger.error(f"Failed to get regime statistics: {e}")
            return {}

    def get_strategy_distribution(self) -> Dict[str, int]:
        """
        Get distribution of strategies selected.

        Returns:
            Dict mapping strategy type to count
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    SELECT strategy_type, COUNT(*) as count
                    FROM analysis_log
                    WHERE strategy_type IS NOT NULL
                    GROUP BY strategy_type
                    ORDER BY count DESC
                    """
                )

                return {row[0]: row[1] for row in cursor.fetchall()}

        except Exception as e:
            logger.error(f"Failed to get strategy distribution: {e}")
            return {}

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
