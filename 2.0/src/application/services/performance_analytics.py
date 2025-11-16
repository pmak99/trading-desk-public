"""
Performance analytics for learning from actual trading results.

Analyzes closed positions to identify:
- Which VRP thresholds actually work
- Best/worst tickers
- Best/worst strategy types
- Performance by market regime
- Parameter optimization insights
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Dict, Optional
from pathlib import Path
from collections import defaultdict

from src.application.services.position_tracker import PositionTracker, Position

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerformanceMetrics:
    """Performance metrics for a group of trades."""
    category: str
    key: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    total_pnl: Decimal
    avg_pnl: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    sharpe_ratio: Optional[Decimal] = None


@dataclass(frozen=True)
class ParameterInsight:
    """Insight about a specific parameter value."""
    parameter_name: str
    parameter_value: str
    win_rate: Decimal
    avg_pnl: Decimal
    sample_size: int
    recommendation: str  # "INCREASE", "DECREASE", "MAINTAIN", "INSUFFICIENT_DATA"
    explanation: str


@dataclass(frozen=True)
class PerformanceReport:
    """Complete performance analysis report."""
    total_closed_trades: int
    overall_win_rate: Decimal
    overall_pnl: Decimal
    by_vrp_bucket: List[PerformanceMetrics]
    by_ticker: List[PerformanceMetrics]
    by_strategy: List[PerformanceMetrics]
    by_sector: List[PerformanceMetrics]
    parameter_insights: List[ParameterInsight]
    top_performers: List[str]
    bottom_performers: List[str]
    recommendations: List[str]


class PerformanceAnalytics:
    """Analyzes trading performance to identify patterns and optimize parameters."""

    # Minimum sample sizes for statistical significance
    MIN_SAMPLE_SIZE = 5  # Minimum trades to draw conclusions

    # VRP buckets for analysis
    VRP_BUCKETS = [
        ("Very High", Decimal("2.5"), Decimal("10")),
        ("High", Decimal("2.0"), Decimal("2.5")),
        ("Good", Decimal("1.5"), Decimal("2.0")),
        ("Marginal", Decimal("1.0"), Decimal("1.5")),
        ("Low", Decimal("0"), Decimal("1.0")),
    ]

    def __init__(self, db_path: Path):
        """
        Initialize performance analytics.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.tracker = PositionTracker(db_path)

    def generate_report(
        self,
        lookback_days: Optional[int] = None
    ) -> PerformanceReport:
        """
        Generate comprehensive performance report.

        Args:
            lookback_days: Number of days to look back (None = all time)

        Returns:
            PerformanceReport with detailed analytics
        """
        # Get closed positions
        closed_positions = self.tracker.get_closed_positions(limit=1000)

        if not closed_positions:
            return self._empty_report()

        # Overall metrics
        total_closed_trades = len(closed_positions)
        winning_trades = sum(1 for p in closed_positions if p.win_loss == "WIN")
        overall_win_rate = Decimal(winning_trades) / Decimal(total_closed_trades) * 100
        overall_pnl = sum(p.final_pnl for p in closed_positions if p.final_pnl)

        # Performance by VRP bucket
        by_vrp_bucket = self._analyze_by_vrp_bucket(closed_positions)

        # Performance by ticker
        by_ticker = self._analyze_by_ticker(closed_positions)

        # Performance by strategy
        by_strategy = self._analyze_by_strategy(closed_positions)

        # Performance by sector
        by_sector = self._analyze_by_sector(closed_positions)

        # Parameter insights
        parameter_insights = self._generate_parameter_insights(
            by_vrp_bucket=by_vrp_bucket
        )

        # Top/bottom performers
        top_performers = self._get_top_performers(by_ticker, limit=5)
        bottom_performers = self._get_bottom_performers(by_ticker, limit=5)

        # Recommendations
        recommendations = self._generate_recommendations(
            by_vrp_bucket=by_vrp_bucket,
            by_ticker=by_ticker,
            by_strategy=by_strategy,
            parameter_insights=parameter_insights,
        )

        return PerformanceReport(
            total_closed_trades=total_closed_trades,
            overall_win_rate=overall_win_rate,
            overall_pnl=overall_pnl,
            by_vrp_bucket=by_vrp_bucket,
            by_ticker=by_ticker,
            by_strategy=by_strategy,
            by_sector=by_sector,
            parameter_insights=parameter_insights,
            top_performers=top_performers,
            bottom_performers=bottom_performers,
            recommendations=recommendations,
        )

    def _analyze_by_vrp_bucket(
        self,
        positions: List[Position]
    ) -> List[PerformanceMetrics]:
        """Analyze performance by VRP ratio buckets."""
        # Group positions by VRP bucket
        buckets: Dict[str, List[Position]] = defaultdict(list)

        for pos in positions:
            for bucket_name, min_vrp, max_vrp in self.VRP_BUCKETS:
                if min_vrp <= pos.vrp_ratio < max_vrp:
                    buckets[bucket_name].append(pos)
                    break

        # Calculate metrics for each bucket
        metrics = []
        for bucket_name, _, _ in self.VRP_BUCKETS:
            bucket_positions = buckets.get(bucket_name, [])
            if bucket_positions:
                metrics.append(
                    self._calculate_metrics("VRP_Bucket", bucket_name, bucket_positions)
                )

        # Sort by VRP (highest first)
        return sorted(metrics, key=lambda m: m.key, reverse=True)

    def _analyze_by_ticker(
        self,
        positions: List[Position]
    ) -> List[PerformanceMetrics]:
        """Analyze performance by ticker."""
        # Group by ticker
        by_ticker: Dict[str, List[Position]] = defaultdict(list)
        for pos in positions:
            by_ticker[pos.ticker].append(pos)

        # Calculate metrics for each ticker
        metrics = []
        for ticker, ticker_positions in by_ticker.items():
            if len(ticker_positions) >= 2:  # At least 2 trades to include
                metrics.append(
                    self._calculate_metrics("Ticker", ticker, ticker_positions)
                )

        # Sort by win rate, then P&L
        return sorted(metrics, key=lambda m: (m.win_rate, m.total_pnl), reverse=True)

    def _analyze_by_strategy(
        self,
        positions: List[Position]
    ) -> List[PerformanceMetrics]:
        """Analyze performance by strategy type."""
        # Group by strategy
        by_strategy: Dict[str, List[Position]] = defaultdict(list)
        for pos in positions:
            by_strategy[pos.strategy_type].append(pos)

        # Calculate metrics
        metrics = []
        for strategy, strategy_positions in by_strategy.items():
            metrics.append(
                self._calculate_metrics("Strategy", strategy, strategy_positions)
            )

        # Sort by total P&L
        return sorted(metrics, key=lambda m: m.total_pnl, reverse=True)

    def _analyze_by_sector(
        self,
        positions: List[Position]
    ) -> List[PerformanceMetrics]:
        """Analyze performance by sector."""
        # Group by sector
        by_sector: Dict[str, List[Position]] = defaultdict(list)
        for pos in positions:
            if pos.sector:
                by_sector[pos.sector].append(pos)

        # Calculate metrics
        metrics = []
        for sector, sector_positions in by_sector.items():
            if len(sector_positions) >= 3:  # At least 3 trades
                metrics.append(
                    self._calculate_metrics("Sector", sector, sector_positions)
                )

        # Sort by win rate
        return sorted(metrics, key=lambda m: m.win_rate, reverse=True)

    def _calculate_metrics(
        self,
        category: str,
        key: str,
        positions: List[Position]
    ) -> PerformanceMetrics:
        """Calculate performance metrics for a group of positions."""
        total_trades = len(positions)
        winning_trades = sum(1 for p in positions if p.win_loss == "WIN")
        losing_trades = total_trades - winning_trades

        win_rate = (Decimal(winning_trades) / Decimal(total_trades) * 100) if total_trades > 0 else Decimal("0")

        winners = [p for p in positions if p.win_loss == "WIN" and p.final_pnl]
        losers = [p for p in positions if p.win_loss == "LOSS" and p.final_pnl]

        total_pnl = sum(p.final_pnl for p in positions if p.final_pnl)
        avg_pnl = total_pnl / Decimal(total_trades) if total_trades > 0 else Decimal("0")

        avg_win = sum(p.final_pnl for p in winners) / len(winners) if winners else Decimal("0")
        avg_loss = sum(p.final_pnl for p in losers) / len(losers) if losers else Decimal("0")

        largest_win = max((p.final_pnl for p in winners), default=Decimal("0"))
        largest_loss = min((p.final_pnl for p in losers), default=Decimal("0"))

        return PerformanceMetrics(
            category=category,
            key=key,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
        )

    def _generate_parameter_insights(
        self,
        by_vrp_bucket: List[PerformanceMetrics]
    ) -> List[ParameterInsight]:
        """Generate insights about parameter optimization."""
        insights = []

        # Analyze VRP threshold
        vrp_insight = self._analyze_vrp_threshold(by_vrp_bucket)
        if vrp_insight:
            insights.append(vrp_insight)

        return insights

    def _analyze_vrp_threshold(
        self,
        by_vrp_bucket: List[PerformanceMetrics]
    ) -> Optional[ParameterInsight]:
        """Analyze optimal VRP threshold."""
        if not by_vrp_bucket:
            return None

        # Find bucket with best performance (win rate > 75% and good sample size)
        good_buckets = [
            b for b in by_vrp_bucket
            if b.win_rate >= 75 and b.total_trades >= self.MIN_SAMPLE_SIZE
        ]

        if not good_buckets:
            return ParameterInsight(
                parameter_name="VRP Threshold",
                parameter_value="Current",
                win_rate=Decimal("0"),
                avg_pnl=Decimal("0"),
                sample_size=0,
                recommendation="INSUFFICIENT_DATA",
                explanation="Not enough trades to recommend VRP threshold changes"
            )

        # Best bucket is highest VRP with good performance
        best_bucket = good_buckets[0]

        # Determine recommendation
        if best_bucket.key == "Very High" and best_bucket.win_rate > 85:
            recommendation = "INCREASE"
            explanation = f"VRP >2.5 trades have {best_bucket.win_rate:.0f}% win rate. Raise threshold to 2.0 minimum."
        elif best_bucket.key == "High" and best_bucket.win_rate > 85:
            recommendation = "MAINTAIN"
            explanation = f"VRP 2.0-2.5 trades performing well ({best_bucket.win_rate:.0f}% WR). Current threshold is optimal."
        elif best_bucket.key == "Good":
            recommendation = "INCREASE"
            explanation = f"VRP 1.5-2.0 trades only {best_bucket.win_rate:.0f}% WR. Consider raising threshold to 1.8."
        else:
            recommendation = "MAINTAIN"
            explanation = "Current VRP threshold appears appropriate."

        return ParameterInsight(
            parameter_name="VRP Threshold",
            parameter_value=best_bucket.key,
            win_rate=best_bucket.win_rate,
            avg_pnl=best_bucket.avg_pnl,
            sample_size=best_bucket.total_trades,
            recommendation=recommendation,
            explanation=explanation
        )

    def _get_top_performers(
        self,
        by_ticker: List[PerformanceMetrics],
        limit: int
    ) -> List[str]:
        """Get top performing tickers."""
        # Filter to tickers with good win rates and sample size
        good_tickers = [
            t for t in by_ticker
            if t.win_rate >= 75 and t.total_trades >= 3
        ]

        # Sort by total P&L
        sorted_tickers = sorted(good_tickers, key=lambda t: t.total_pnl, reverse=True)

        return [f"{t.key} ({t.win_rate:.0f}% WR, ${t.total_pnl:,.0f})" for t in sorted_tickers[:limit]]

    def _get_bottom_performers(
        self,
        by_ticker: List[PerformanceMetrics],
        limit: int
    ) -> List[str]:
        """Get worst performing tickers."""
        # Filter to tickers with enough trades
        tickers_with_data = [t for t in by_ticker if t.total_trades >= 2]

        # Sort by win rate (lowest first)
        sorted_tickers = sorted(tickers_with_data, key=lambda t: (t.win_rate, t.total_pnl))

        return [f"{t.key} ({t.win_rate:.0f}% WR, ${t.total_pnl:,.0f})" for t in sorted_tickers[:limit]]

    def _generate_recommendations(
        self,
        by_vrp_bucket: List[PerformanceMetrics],
        by_ticker: List[PerformanceMetrics],
        by_strategy: List[PerformanceMetrics],
        parameter_insights: List[ParameterInsight],
    ) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []

        # VRP threshold recommendations
        for insight in parameter_insights:
            if insight.recommendation in ["INCREASE", "DECREASE"]:
                recommendations.append(f"📊 {insight.explanation}")

        # Strategy recommendations
        if by_strategy:
            best_strategy = max(by_strategy, key=lambda s: s.win_rate)
            if best_strategy.win_rate >= 80 and best_strategy.total_trades >= 5:
                recommendations.append(
                    f"📈 {best_strategy.key} strategy has {best_strategy.win_rate:.0f}% win rate. "
                    f"Consider allocating more to this strategy."
                )

        # Ticker recommendations
        losing_tickers = [t for t in by_ticker if t.win_rate < 50 and t.total_trades >= 3]
        if losing_tickers:
            tickers_str = ", ".join(t.key for t in losing_tickers[:3])
            recommendations.append(
                f"⚠️ Consider blacklisting poor performers: {tickers_str}"
            )

        # Overall performance
        if by_vrp_bucket:
            high_vrp_bucket = next((b for b in by_vrp_bucket if b.key in ["Very High", "High"]), None)
            if high_vrp_bucket and high_vrp_bucket.win_rate > 85:
                recommendations.append(
                    f"✓ High VRP trades (>2.0) have {high_vrp_bucket.win_rate:.0f}% win rate. "
                    f"Focus on these opportunities."
                )

        if not recommendations:
            recommendations.append("✓ Continue current approach. Performance metrics look good.")

        return recommendations

    def _empty_report(self) -> PerformanceReport:
        """Return empty report when no data available."""
        return PerformanceReport(
            total_closed_trades=0,
            overall_win_rate=Decimal("0"),
            overall_pnl=Decimal("0"),
            by_vrp_bucket=[],
            by_ticker=[],
            by_strategy=[],
            by_sector=[],
            parameter_insights=[],
            top_performers=[],
            bottom_performers=[],
            recommendations=["No closed trades yet. Start trading to see analytics."]
        )
