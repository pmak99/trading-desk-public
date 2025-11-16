"""
Dashboard formatting utilities for terminal display.

Provides formatted output for:
- Position tracking
- Pre-trade risk analysis
- Performance analytics
"""

from decimal import Decimal
from typing import List
from datetime import date

from src.application.services.position_tracker import Position, PortfolioSummary
from src.application.services.pre_trade_risk import PreTradeRisk, StressScenario
from src.application.services.performance_analytics import (
    PerformanceReport,
    PerformanceMetrics,
    ParameterInsight,
)


# ANSI color codes
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


def format_positions_dashboard(positions: List[Position], summary: PortfolioSummary) -> str:
    """Format open positions dashboard."""
    if not positions:
        return f"\n{Colors.YELLOW}No open positions{Colors.END}\n"

    lines = []
    lines.append(f"\n{Colors.BOLD}{Colors.CYAN}╔══════════════════════════════════════════════════════════════════════════╗{Colors.END}")
    lines.append(f"{Colors.BOLD}{Colors.CYAN}║                         CURRENT POSITIONS                                ║{Colors.END}")
    lines.append(f"{Colors.BOLD}{Colors.CYAN}╚══════════════════════════════════════════════════════════════════════════╝{Colors.END}\n")

    # Table header
    lines.append(f"{Colors.BOLD}{'Ticker':<8} {'Entry':<10} {'Exp':<10} {'Days':<5} {'Credit':<10} {'P&L':<12} {'Status':<10}{Colors.END}")
    lines.append("─" * 80)

    # Positions
    for pos in positions:
        # Color code P&L
        if pos.current_pnl > 0:
            pnl_color = Colors.GREEN
        elif pos.current_pnl < 0:
            pnl_color = Colors.RED
        else:
            pnl_color = ""

        pnl_str = f"{pnl_color}${pos.current_pnl:,.0f} ({pos.current_pnl_pct:.0f}%){Colors.END}"

        # Status indicator
        if pos.target_profit_amount and pos.current_pnl >= pos.target_profit_amount:
            status = f"{Colors.GREEN}✓ Target{Colors.END}"
        elif pos.stop_loss_amount and pos.current_pnl <= -pos.stop_loss_amount:
            status = f"{Colors.RED}⚠ Stop Loss{Colors.END}"
        else:
            status = "Monitoring"

        lines.append(
            f"{pos.ticker:<8} "
            f"{pos.entry_date.strftime('%m/%d'):<10} "
            f"{pos.expiration_date.strftime('%m/%d'):<10} "
            f"{pos.days_held:<5} "
            f"${pos.credit_received:>8,.0f} "
            f"{pnl_str:<20} "  # Extra space for color codes
            f"{status}"
        )

    # Summary section
    lines.append("\n" + "─" * 80)
    lines.append(f"\n{Colors.BOLD}PORTFOLIO SUMMARY{Colors.END}")
    lines.append(f"Total Positions:       {summary.total_positions}")
    lines.append(f"Portfolio Exposure:    {summary.total_exposure_pct:.1f}% of account")
    lines.append(f"Capital at Risk:       ${summary.total_capital_at_risk:,.0f}")

    # Color code unrealized P&L
    if summary.unrealized_pnl > 0:
        pnl_color = Colors.GREEN
    elif summary.unrealized_pnl < 0:
        pnl_color = Colors.RED
    else:
        pnl_color = ""
    lines.append(f"Unrealized P&L:        {pnl_color}${summary.unrealized_pnl:,.0f}{Colors.END}")

    lines.append(f"Avg VRP Ratio:         {summary.avg_vrp_ratio:.2f}x")
    lines.append(f"Avg Days Held:         {summary.avg_days_held:.1f}")

    # Alerts
    if summary.positions_at_stop_loss:
        lines.append(f"\n{Colors.RED}{Colors.BOLD}⚠️  STOP LOSS ALERTS: {', '.join(summary.positions_at_stop_loss)}{Colors.END}")

    if summary.positions_at_target:
        lines.append(f"\n{Colors.GREEN}{Colors.BOLD}✓ AT TARGET PROFIT: {', '.join(summary.positions_at_target)}{Colors.END}")

    # Sector exposure
    if summary.sector_exposure:
        lines.append(f"\n{Colors.BOLD}SECTOR EXPOSURE{Colors.END}")
        for sector, exposure_pct in sorted(summary.sector_exposure.items(), key=lambda x: x[1], reverse=True):
            # Warn if concentration > 40%
            if exposure_pct > 40:
                sector_color = Colors.YELLOW
                warning = " ⚠️"
            else:
                sector_color = ""
                warning = ""
            lines.append(f"  {sector_color}{sector:<20} {exposure_pct:>5.1f}%{warning}{Colors.END}")

    lines.append("")
    return "\n".join(lines)


def format_pre_trade_risk(risk: PreTradeRisk) -> str:
    """Format pre-trade risk analysis."""
    lines = []
    lines.append(f"\n{Colors.BOLD}{Colors.MAGENTA}╔══════════════════════════════════════════════════════════════════════════╗{Colors.END}")
    lines.append(f"{Colors.BOLD}{Colors.MAGENTA}║                     PRE-TRADE RISK ANALYSIS                              ║{Colors.END}")
    lines.append(f"{Colors.BOLD}{Colors.MAGENTA}╚══════════════════════════════════════════════════════════════════════════╝{Colors.END}\n")

    # Trade details
    lines.append(f"{Colors.BOLD}PROPOSED TRADE: {risk.ticker}{Colors.END}")
    lines.append(f"Position Size:         {risk.position_size_pct:.1f}% of account (${risk.max_loss:,.0f} max loss)")
    lines.append(f"Credit:                ${risk.credit:,.0f}")
    lines.append(f"VRP Ratio:             {risk.vrp_ratio:.2f}x")

    # Portfolio impact
    lines.append(f"\n{Colors.BOLD}PORTFOLIO IMPACT{Colors.END}")
    lines.append(f"Current Exposure:      {risk.current_exposure_pct:.1f}%")
    lines.append(f"New Total Exposure:    {risk.new_total_exposure_pct:.1f}% "
                f"({Colors.CYAN}+{risk.exposure_increase_pct:.1f}%{Colors.END})")

    if risk.new_total_exposure_pct > 20:
        lines.append(f"{Colors.YELLOW}⚠️  Exceeds 20% portfolio limit{Colors.END}")

    # Sector analysis
    if risk.sector:
        lines.append(f"\n{Colors.BOLD}SECTOR ANALYSIS ({risk.sector}){Colors.END}")
        lines.append(f"Current Sector Exposure: {risk.current_sector_exposure_pct:.1f}%")
        lines.append(f"New Sector Exposure:     {risk.new_sector_exposure_pct:.1f}% "
                    f"({Colors.CYAN}+{risk.position_size_pct:.1f}%{Colors.END})")

        if risk.sector_concentration_warning:
            lines.append(f"{Colors.YELLOW}⚠️  High sector concentration (>40%){Colors.END}")

    # Correlation
    if risk.correlated_positions:
        lines.append(f"\n{Colors.BOLD}CORRELATION ANALYSIS{Colors.END}")
        lines.append(f"Correlated Positions:  {', '.join(risk.correlated_positions)}")
        if risk.max_correlation:
            lines.append(f"Max Correlation:       {risk.max_correlation:.0%}")
        if risk.correlation_warning:
            lines.append(f"{Colors.YELLOW}⚠️  High correlation with existing positions{Colors.END}")

    # Stress scenarios
    if risk.stress_scenarios:
        lines.append(f"\n{Colors.BOLD}STRESS SCENARIOS{Colors.END}")
        for scenario in risk.stress_scenarios:
            pnl_color = Colors.GREEN if scenario.estimated_pnl > 0 else Colors.RED
            prob_str = f" ({scenario.probability})" if scenario.probability else ""
            lines.append(
                f"  {scenario.scenario_name:<20} "
                f"{pnl_color}${scenario.estimated_pnl:>8,.0f}{Colors.END}"
                f"{prob_str}"
            )

    lines.append(f"\nMax Portfolio Loss:    {Colors.RED}${risk.max_portfolio_loss:,.0f}{Colors.END} "
                f"(if correlated positions fail)")

    # Historical context
    if risk.similar_trades_count > 0:
        lines.append(f"\n{Colors.BOLD}HISTORICAL CONTEXT{Colors.END}")
        lines.append(f"Similar Trades:        {risk.similar_trades_count}")
        if risk.similar_trades_win_rate:
            wr_color = Colors.GREEN if risk.similar_trades_win_rate >= 75 else Colors.YELLOW
            lines.append(f"Historical Win Rate:   {wr_color}{risk.similar_trades_win_rate:.0f}%{Colors.END}")
        if risk.similar_trades_avg_pnl:
            pnl_color = Colors.GREEN if risk.similar_trades_avg_pnl > 0 else Colors.RED
            lines.append(f"Avg P&L:               {pnl_color}${risk.similar_trades_avg_pnl:,.0f}{Colors.END}")

    # Warnings
    if risk.warnings:
        lines.append(f"\n{Colors.BOLD}{Colors.YELLOW}⚠️  WARNINGS{Colors.END}")
        for warning in risk.warnings:
            lines.append(f"  • {warning}")

    # Notes
    if risk.notes:
        lines.append(f"\n{Colors.BOLD}NOTES{Colors.END}")
        for note in risk.notes:
            lines.append(f"  {note}")

    # Recommendation
    lines.append(f"\n{Colors.BOLD}{'─' * 80}{Colors.END}")
    if risk.recommendation == "PROCEED":
        rec_color = Colors.GREEN
        rec_icon = "✓"
    elif risk.recommendation == "CAUTION":
        rec_color = Colors.YELLOW
        rec_icon = "⚠️ "
    else:  # REJECT
        rec_color = Colors.RED
        rec_icon = "✗"

    lines.append(f"\n{rec_color}{Colors.BOLD}{rec_icon} RECOMMENDATION: {risk.recommendation}{Colors.END}\n")

    return "\n".join(lines)


def format_performance_report(report: PerformanceReport) -> str:
    """Format performance analytics report."""
    lines = []
    lines.append(f"\n{Colors.BOLD}{Colors.BLUE}╔══════════════════════════════════════════════════════════════════════════╗{Colors.END}")
    lines.append(f"{Colors.BOLD}{Colors.BLUE}║                    PERFORMANCE ANALYTICS                                 ║{Colors.END}")
    lines.append(f"{Colors.BOLD}{Colors.BLUE}╚══════════════════════════════════════════════════════════════════════════╝{Colors.END}\n")

    # Overall performance
    lines.append(f"{Colors.BOLD}OVERALL PERFORMANCE{Colors.END}")
    lines.append(f"Total Closed Trades:   {report.total_closed_trades}")

    wr_color = Colors.GREEN if report.overall_win_rate >= 75 else Colors.YELLOW
    lines.append(f"Win Rate:              {wr_color}{report.overall_win_rate:.1f}%{Colors.END}")

    pnl_color = Colors.GREEN if report.overall_pnl > 0 else Colors.RED
    lines.append(f"Total P&L:             {pnl_color}${report.overall_pnl:,.0f}{Colors.END}")

    # Performance by VRP bucket
    if report.by_vrp_bucket:
        lines.append(f"\n{Colors.BOLD}PERFORMANCE BY VRP RATIO{Colors.END}")
        lines.append(f"{'Bucket':<15} {'Trades':<8} {'Win Rate':<12} {'Avg P&L':<12} {'Total P&L':<12}")
        lines.append("─" * 80)
        for metric in report.by_vrp_bucket:
            wr_color = Colors.GREEN if metric.win_rate >= 75 else Colors.YELLOW
            pnl_color = Colors.GREEN if metric.avg_pnl > 0 else Colors.RED

            lines.append(
                f"{metric.key:<15} "
                f"{metric.total_trades:<8} "
                f"{wr_color}{metric.win_rate:>6.1f}%{Colors.END}     "
                f"{pnl_color}${metric.avg_pnl:>8,.0f}{Colors.END}  "
                f"${metric.total_pnl:>9,.0f}"
            )

    # Parameter insights
    if report.parameter_insights:
        lines.append(f"\n{Colors.BOLD}PARAMETER INSIGHTS{Colors.END}")
        for insight in report.parameter_insights:
            lines.append(f"\n{insight.parameter_name}: {insight.parameter_value}")
            lines.append(f"  Win Rate:      {insight.win_rate:.0f}% ({insight.sample_size} trades)")
            lines.append(f"  Avg P&L:       ${insight.avg_pnl:,.0f}")
            lines.append(f"  Recommendation: {Colors.BOLD}{insight.recommendation}{Colors.END}")
            lines.append(f"  {insight.explanation}")

    # Top performers
    if report.top_performers:
        lines.append(f"\n{Colors.BOLD}{Colors.GREEN}TOP PERFORMERS{Colors.END}")
        for i, performer in enumerate(report.top_performers, 1):
            lines.append(f"  {i}. {performer}")

    # Bottom performers
    if report.bottom_performers:
        lines.append(f"\n{Colors.BOLD}{Colors.RED}BOTTOM PERFORMERS{Colors.END}")
        for i, performer in enumerate(report.bottom_performers, 1):
            lines.append(f"  {i}. {performer}")

    # Recommendations
    if report.recommendations:
        lines.append(f"\n{Colors.BOLD}{Colors.CYAN}RECOMMENDATIONS{Colors.END}")
        for rec in report.recommendations:
            lines.append(f"  {rec}")

    lines.append("")
    return "\n".join(lines)


def format_metrics_table(metrics: List[PerformanceMetrics]) -> str:
    """Format a table of performance metrics."""
    if not metrics:
        return "No data available"

    lines = []
    lines.append(f"{'Category':<15} {'Key':<15} {'Trades':<8} {'Win%':<8} {'Total P&L':<12}")
    lines.append("─" * 70)

    for metric in metrics:
        wr_color = Colors.GREEN if metric.win_rate >= 75 else Colors.YELLOW
        pnl_color = Colors.GREEN if metric.total_pnl > 0 else Colors.RED

        lines.append(
            f"{metric.category:<15} "
            f"{metric.key:<15} "
            f"{metric.total_trades:<8} "
            f"{wr_color}{metric.win_rate:>5.1f}%{Colors.END} "
            f"{pnl_color}${metric.total_pnl:>9,.0f}{Colors.END}"
        )

    return "\n".join(lines)
