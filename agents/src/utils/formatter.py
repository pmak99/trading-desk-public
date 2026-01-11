"""Output formatting utilities for agent results.

Provides ASCII table formatting and result presentation.
"""

from typing import List, Dict, Any
from tabulate import tabulate


def format_whisper_results(results: List[Dict[str, Any]]) -> str:
    """
    Format whisper orchestrator results as ASCII table.

    Args:
        results: List of ticker analysis results with explanations

    Returns:
        Formatted ASCII table string

    Example:
        results = [
            {
                "ticker": "NVDA",
                "vrp_ratio": 6.2,
                "score": 78,
                "liquidity_tier": "GOOD",
                "recommendation": "EXCELLENT",
                "explanation": "VRP is 6.2x because..."
            }
        ]
        print(format_whisper_results(results))
    """
    if not results:
        return "No results to display."

    # Prepare table data
    table_data = []
    for r in results:
        # Add emoji indicators
        liquidity_emoji = {
            'EXCELLENT': '✅',
            'GOOD': '✅',
            'WARNING': '⚠️',
            'REJECT': '🚫'
        }.get(r.get('liquidity_tier', ''), '')

        recommendation_emoji = {
            'EXCELLENT': '🟢',
            'GOOD': '🟡',
            'MARGINAL': '🟠',
            'SKIP': '⚪'
        }.get(r.get('recommendation', ''), '')

        row = [
            r.get('ticker', 'N/A'),
            f"{r.get('vrp_ratio', 0.0):.1f}x",
            f"{liquidity_emoji} {r.get('liquidity_tier', 'N/A')}",
            f"{recommendation_emoji} {r.get('recommendation', 'N/A')}",
            r.get('score', 0),
            r.get('explanation', 'No explanation')[:60] + '...'
            if len(r.get('explanation', '')) > 60
            else r.get('explanation', 'No explanation')
        ]
        table_data.append(row)

    headers = ['Ticker', 'VRP', 'Liquidity', 'Recommendation', 'Score', 'Explanation']

    return tabulate(table_data, headers=headers, tablefmt='grid')


def format_analyze_result(result: Dict[str, Any]) -> str:
    """
    Format single ticker analysis result as markdown report.

    Args:
        result: Ticker analysis result with all details

    Returns:
        Formatted markdown string

    Example:
        result = {
            "ticker": "NVDA",
            "vrp_ratio": 6.2,
            "explanation": {...},
            "anomalies": {...},
            "strategies": [...]
        }
        print(format_analyze_result(result))
    """
    ticker = result.get('ticker', 'N/A')
    vrp = result.get('vrp_ratio', 0.0)
    recommendation = result.get('recommendation', 'N/A')
    liquidity = result.get('liquidity_tier', 'N/A')
    score = result.get('score', 0)

    # Build markdown report
    lines = [
        f"# Analysis Report: {ticker}",
        "",
        "## Summary",
        f"- **VRP Ratio:** {vrp:.1f}x",
        f"- **Recommendation:** {recommendation}",
        f"- **Liquidity Tier:** {liquidity}",
        f"- **Composite Score:** {score}",
        ""
    ]

    # Add explanation if available
    if 'explanation' in result and result['explanation']:
        exp = result['explanation']
        lines.extend([
            "## Explanation",
            exp.get('explanation', 'No explanation available'),
            "",
            "### Key Factors",
        ])
        for factor in exp.get('key_factors', []):
            lines.append(f"- {factor}")
        lines.append("")
        lines.extend([
            "### Historical Context",
            exp.get('historical_context', 'No historical context available'),
            ""
        ])

    # Add anomaly warnings if available
    if 'anomalies' in result and result['anomalies']:
        anomaly_data = result['anomalies']
        if anomaly_data.get('anomalies'):
            lines.extend([
                "## ⚠️ Anomaly Warnings",
                ""
            ])
            for anomaly in anomaly_data['anomalies']:
                severity_emoji = '🚨' if anomaly['severity'] == 'critical' else '⚠️'
                lines.append(
                    f"- {severity_emoji} **{anomaly['type']}**: {anomaly['message']}"
                )
            lines.append("")
            lines.append(
                f"**Recommendation:** {anomaly_data.get('recommendation', 'N/A')}"
            )
            lines.append("")

    # Add strategies if available
    if 'strategies' in result and result['strategies']:
        lines.extend([
            "## Recommended Strategies",
            ""
        ])
        for i, strategy in enumerate(result['strategies'][:5], 1):  # Top 5
            lines.append(f"### {i}. {strategy.get('strategy_type', 'Unknown')}")
            lines.append(f"- **Max Profit:** ${strategy.get('max_profit', 0):.2f}")
            lines.append(f"- **Max Risk:** ${strategy.get('max_risk', 0):.2f}")
            lines.append(
                f"- **Win Probability:** {strategy.get('probability_of_profit', 0):.1f}%"
            )
            lines.append("")

    return "\n".join(lines)


def format_cross_ticker_warnings(warnings: List[str]) -> str:
    """
    Format cross-ticker intelligence warnings.

    Args:
        warnings: List of warning messages

    Returns:
        Formatted warning string

    Example:
        warnings = [
            "3 semiconductor stocks in same week (correlated risk)",
            "Total exposure exceeds $150K recommended limit"
        ]
        print(format_cross_ticker_warnings(warnings))
    """
    if not warnings:
        return ""

    lines = [
        "",
        "=" * 80,
        "⚠️  CROSS-TICKER INTELLIGENCE WARNINGS",
        "=" * 80,
    ]

    for warning in warnings:
        lines.append(f"• {warning}")

    lines.append("=" * 80)

    return "\n".join(lines)
