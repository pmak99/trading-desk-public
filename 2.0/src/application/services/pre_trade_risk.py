"""
Pre-trade risk analysis for decision support.

Analyzes portfolio impact BEFORE manual execution, showing:
- Portfolio exposure if trade is taken
- Sector concentration
- Correlation with existing positions
- Stress scenarios
- Historical context for this setup
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Dict
from pathlib import Path

from src.application.services.position_tracker import PositionTracker, Position

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StressScenario:
    """Result of a stress test scenario."""
    scenario_name: str
    description: str
    estimated_pnl: Decimal
    probability: Optional[str] = None


@dataclass(frozen=True)
class PreTradeRisk:
    """Complete pre-trade risk analysis."""
    # Proposed trade details
    ticker: str
    position_size_pct: Decimal
    max_loss: Decimal
    credit: Decimal
    vrp_ratio: Decimal

    # Portfolio impact
    current_exposure_pct: Decimal
    new_total_exposure_pct: Decimal
    exposure_increase_pct: Decimal

    # Sector analysis
    sector: Optional[str]
    current_sector_exposure_pct: Decimal
    new_sector_exposure_pct: Decimal
    sector_concentration_warning: bool

    # Correlation
    correlated_positions: List[str]
    max_correlation: Optional[Decimal]
    correlation_warning: bool

    # Risk scenarios
    stress_scenarios: List[StressScenario]
    max_portfolio_loss: Decimal

    # Historical context
    similar_trades_count: int
    similar_trades_win_rate: Optional[Decimal]
    similar_trades_avg_pnl: Optional[Decimal]

    # Recommendation
    recommendation: str  # "PROCEED", "CAUTION", "REJECT"
    warnings: List[str]
    notes: List[str]


class PreTradeRiskAnalyzer:
    """Analyzes portfolio impact before taking a trade."""

    # Risk thresholds
    MAX_TOTAL_EXPOSURE_PCT = Decimal("20")  # 20% max portfolio exposure
    MAX_SECTOR_EXPOSURE_PCT = Decimal("40")  # 40% max in any sector
    HIGH_CORRELATION_THRESHOLD = Decimal("0.70")  # 70% correlation is concerning

    # Sector correlation lookup (simplified - could be enhanced)
    SECTOR_CORRELATIONS = {
        "Technology": ["Technology"],
        "Consumer": ["Consumer Cyclical", "Consumer Defensive"],
        "Financial": ["Financial Services", "Financial"],
        "Healthcare": ["Healthcare"],
        "Communication": ["Communication Services"],
        "Energy": ["Energy"],
        "Industrials": ["Industrials"],
        "Utilities": ["Utilities"],
        "Real Estate": ["Real Estate"],
        "Materials": ["Basic Materials", "Materials"],
    }

    def __init__(self, db_path: Path):
        """
        Initialize pre-trade risk analyzer.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.tracker = PositionTracker(db_path)

    def analyze(
        self,
        ticker: str,
        position_size_pct: Decimal,
        max_loss: Decimal,
        credit: Decimal,
        vrp_ratio: Decimal,
        sector: Optional[str] = None,
        implied_move_pct: Optional[Decimal] = None,
        historical_avg_move_pct: Optional[Decimal] = None,
    ) -> PreTradeRisk:
        """
        Perform complete pre-trade risk analysis.

        Args:
            ticker: Stock ticker
            position_size_pct: Proposed position size as % of account
            max_loss: Maximum loss on this trade
            credit: Credit received
            vrp_ratio: VRP ratio
            sector: Stock sector
            implied_move_pct: Implied move percentage
            historical_avg_move_pct: Historical average move percentage

        Returns:
            PreTradeRisk analysis
        """
        # Get current portfolio state
        summary = self.tracker.get_portfolio_summary()
        open_positions = self.tracker.get_open_positions()

        # Calculate portfolio impact
        current_exposure_pct = summary.total_exposure_pct
        new_total_exposure_pct = current_exposure_pct + position_size_pct
        exposure_increase_pct = position_size_pct

        # Sector analysis
        current_sector_exposure_pct = Decimal("0")
        if sector:
            current_sector_exposure_pct = summary.sector_exposure.get(sector, Decimal("0"))
        new_sector_exposure_pct = current_sector_exposure_pct + position_size_pct
        sector_concentration_warning = new_sector_exposure_pct > self.MAX_SECTOR_EXPOSURE_PCT

        # Correlation analysis
        correlated_positions = self._find_correlated_positions(ticker, sector, open_positions)
        max_correlation = self._estimate_max_correlation(ticker, sector, open_positions)
        correlation_warning = max_correlation and max_correlation > self.HIGH_CORRELATION_THRESHOLD

        # Stress scenarios
        stress_scenarios = self._generate_stress_scenarios(
            ticker=ticker,
            credit=credit,
            max_loss=max_loss,
            open_positions=open_positions,
            implied_move_pct=implied_move_pct,
        )

        # Max portfolio loss if this and correlated positions fail
        max_portfolio_loss = self._calculate_max_portfolio_loss(
            new_trade_max_loss=max_loss,
            correlated_positions=[p for p in open_positions if p.ticker in correlated_positions]
        )

        # Historical context
        similar_trades = self._get_similar_trades(ticker, vrp_ratio)
        similar_trades_count = len(similar_trades)
        similar_trades_win_rate = self._calculate_win_rate(similar_trades)
        similar_trades_avg_pnl = self._calculate_avg_pnl(similar_trades)

        # Generate recommendation and warnings
        recommendation, warnings, notes = self._generate_recommendation(
            new_total_exposure_pct=new_total_exposure_pct,
            sector_concentration_warning=sector_concentration_warning,
            correlation_warning=correlation_warning,
            max_portfolio_loss=max_portfolio_loss,
            vrp_ratio=vrp_ratio,
            similar_trades_win_rate=similar_trades_win_rate,
        )

        return PreTradeRisk(
            ticker=ticker,
            position_size_pct=position_size_pct,
            max_loss=max_loss,
            credit=credit,
            vrp_ratio=vrp_ratio,
            current_exposure_pct=current_exposure_pct,
            new_total_exposure_pct=new_total_exposure_pct,
            exposure_increase_pct=exposure_increase_pct,
            sector=sector,
            current_sector_exposure_pct=current_sector_exposure_pct,
            new_sector_exposure_pct=new_sector_exposure_pct,
            sector_concentration_warning=sector_concentration_warning,
            correlated_positions=correlated_positions,
            max_correlation=max_correlation,
            correlation_warning=correlation_warning,
            stress_scenarios=stress_scenarios,
            max_portfolio_loss=max_portfolio_loss,
            similar_trades_count=similar_trades_count,
            similar_trades_win_rate=similar_trades_win_rate,
            similar_trades_avg_pnl=similar_trades_avg_pnl,
            recommendation=recommendation,
            warnings=warnings,
            notes=notes,
        )

    def _find_correlated_positions(
        self,
        ticker: str,
        sector: Optional[str],
        open_positions: List[Position]
    ) -> List[str]:
        """Find positions correlated with proposed trade."""
        correlated = []

        for pos in open_positions:
            # Same ticker = 100% correlated
            if pos.ticker == ticker:
                correlated.append(pos.ticker)
            # Same sector = potentially correlated
            elif sector and pos.sector == sector:
                correlated.append(pos.ticker)

        return correlated

    def _estimate_max_correlation(
        self,
        ticker: str,
        sector: Optional[str],
        open_positions: List[Position]
    ) -> Optional[Decimal]:
        """Estimate maximum correlation with existing positions."""
        max_corr = Decimal("0")

        for pos in open_positions:
            if pos.ticker == ticker:
                return Decimal("1.0")  # Same ticker = 100% correlation
            elif sector and pos.sector == sector:
                # Same sector = estimate 60-70% correlation
                max_corr = max(max_corr, Decimal("0.65"))

        return max_corr if max_corr > 0 else None

    def _generate_stress_scenarios(
        self,
        ticker: str,
        credit: Decimal,
        max_loss: Decimal,
        open_positions: List[Position],
        implied_move_pct: Optional[Decimal] = None,
    ) -> List[StressScenario]:
        """Generate stress test scenarios."""
        scenarios = []

        # Scenario 1: Base case (IV crush as expected)
        base_case_pnl = credit * Decimal("0.6")  # Capture 60% of credit
        scenarios.append(StressScenario(
            scenario_name="Base Case",
            description="IV crushes as expected, close at 60% profit",
            estimated_pnl=base_case_pnl,
            probability="High (70-80%)"
        ))

        # Scenario 2: Stock moves to breakeven
        if implied_move_pct:
            scenarios.append(StressScenario(
                scenario_name="Breakeven Move",
                description=f"Stock moves {implied_move_pct:.1f}% (at breakeven)",
                estimated_pnl=Decimal("0"),
                probability="Low (10-15%)"
            ))

        # Scenario 3: Max loss
        scenarios.append(StressScenario(
            scenario_name="Max Loss",
            description="Stock moves beyond breakeven, full loss",
            estimated_pnl=-max_loss,
            probability="Low (5-10%)"
        ))

        # Scenario 4: Multiple positions fail
        if len(open_positions) > 0:
            total_at_risk = sum(p.max_loss for p in open_positions) + max_loss
            scenarios.append(StressScenario(
                scenario_name="Portfolio Stress",
                description=f"This trade + all {len(open_positions)} open positions fail",
                estimated_pnl=-total_at_risk,
                probability="Very Low (<1%)"
            ))

        return scenarios

    def _calculate_max_portfolio_loss(
        self,
        new_trade_max_loss: Decimal,
        correlated_positions: List[Position]
    ) -> Decimal:
        """Calculate max portfolio loss if correlated positions fail together."""
        total_max_loss = new_trade_max_loss

        for pos in correlated_positions:
            # Assume correlated positions could fail together
            total_max_loss += pos.max_loss

        return total_max_loss

    def _get_similar_trades(
        self,
        ticker: str,
        vrp_ratio: Decimal
    ) -> List[Position]:
        """Get historical trades similar to this setup."""
        # Get closed positions for this ticker
        all_closed = self.tracker.get_closed_positions(limit=100)

        # Filter to similar VRP ratios (within 0.3)
        vrp_tolerance = Decimal("0.3")
        similar = [
            p for p in all_closed
            if p.ticker == ticker and
            abs(p.vrp_ratio - vrp_ratio) <= vrp_tolerance
        ]

        return similar

    def _calculate_win_rate(self, positions: List[Position]) -> Optional[Decimal]:
        """Calculate win rate from list of positions."""
        if not positions:
            return None

        wins = sum(1 for p in positions if p.win_loss == "WIN")
        return Decimal(wins) / Decimal(len(positions)) * 100

    def _calculate_avg_pnl(self, positions: List[Position]) -> Optional[Decimal]:
        """Calculate average P&L from list of positions."""
        if not positions:
            return None

        total_pnl = sum(p.final_pnl for p in positions if p.final_pnl)
        return total_pnl / len(positions)

    def _generate_recommendation(
        self,
        new_total_exposure_pct: Decimal,
        sector_concentration_warning: bool,
        correlation_warning: bool,
        max_portfolio_loss: Decimal,
        vrp_ratio: Decimal,
        similar_trades_win_rate: Optional[Decimal],
    ) -> tuple[str, List[str], List[str]]:
        """Generate recommendation and warnings."""
        warnings = []
        notes = []

        # Check exposure limits
        if new_total_exposure_pct > self.MAX_TOTAL_EXPOSURE_PCT:
            warnings.append(
                f"Portfolio exposure would exceed {self.MAX_TOTAL_EXPOSURE_PCT}% "
                f"(new total: {new_total_exposure_pct:.1f}%)"
            )

        # Check sector concentration
        if sector_concentration_warning:
            warnings.append(
                f"Sector concentration would exceed {self.MAX_SECTOR_EXPOSURE_PCT}%"
            )

        # Check correlation
        if correlation_warning:
            warnings.append(
                "High correlation with existing positions - concentrated risk"
            )

        # Check portfolio stress - only warn if multiple correlated positions
        # Single position at $20K is expected, warn if correlated positions add significant risk
        if max_portfolio_loss > Decimal("25000"):  # Only warn if >$25K (indicates correlated positions)
            warnings.append(
                f"Max portfolio loss if correlated trades fail: ${max_portfolio_loss:,.0f}"
            )

        # Check VRP ratio
        if vrp_ratio < Decimal("1.5"):
            warnings.append(
                f"VRP ratio {vrp_ratio:.2f} is below recommended 1.5 threshold"
            )

        # Check historical performance
        if similar_trades_win_rate and similar_trades_win_rate < 70:
            warnings.append(
                f"Historical win rate on similar trades: {similar_trades_win_rate:.0f}% (below 70% target)"
            )

        # Positive notes
        if vrp_ratio >= Decimal("2.0"):
            notes.append(f"✓ Strong VRP ratio ({vrp_ratio:.2f}x)")

        if similar_trades_win_rate and similar_trades_win_rate >= 80:
            notes.append(f"✓ Strong historical win rate ({similar_trades_win_rate:.0f}%)")

        if new_total_exposure_pct <= self.MAX_TOTAL_EXPOSURE_PCT * Decimal("0.8"):
            notes.append(f"✓ Portfolio exposure well within limits ({new_total_exposure_pct:.1f}%)")

        # Final recommendation
        if len(warnings) == 0:
            recommendation = "PROCEED"
        elif len(warnings) <= 2:
            # 1-2 warnings is acceptable with caution
            recommendation = "CAUTION"
            notes.append("Consider reducing position size or managing existing positions first")
        elif len(warnings) <= 3 and vrp_ratio >= Decimal("1.8"):
            # 3 warnings okay if VRP is strong
            recommendation = "CAUTION"
            notes.append("Multiple risk factors present - proceed with caution")
        else:
            recommendation = "REJECT"
            notes.append("Too many risk factors - skip this trade or close existing positions")

        return recommendation, warnings, notes
