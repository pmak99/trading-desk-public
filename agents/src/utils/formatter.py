"""Output formatting utilities for agent results.

Provides ASCII table formatting and result presentation.
"""

from typing import List, Dict, Any
from datetime import datetime


def _format_earnings_date(date_str: str) -> str:
    """Format YYYY-MM-DD to 'Jan 28' style."""
    try:
        dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
        return dt.strftime('%b %d').replace(' 0', ' ')
    except (ValueError, TypeError):
        return date_str[:10] if date_str else 'N/A'


def _vrp_icon(vrp: float) -> str:
    """VRP tier icon matching sentiment format."""
    if vrp >= 1.8:
        return '\u2b50'  # star
    elif vrp >= 1.4:
        return '\u2713'  # checkmark
    elif vrp >= 1.2:
        return '\u25cb'  # circle
    return ''


def _liquidity_display(tier: str) -> str:
    """Format liquidity tier matching sentiment format."""
    mapping = {
        'EXCELLENT': 'EXCELLENT',
        'GOOD': 'GOOD',
        'WARNING': '\u26a0\ufe0f  WARNING',
        'REJECT': '\U0001f6ab REJECT',
    }
    return mapping.get(tier, tier or 'N/A')


def format_whisper_results(results: List[Dict[str, Any]]) -> str:
    """
    Format whisper orchestrator results as ASCII table matching sentiment output style.

    Args:
        results: List of ticker analysis results with explanations

    Returns:
        Formatted ASCII table string
    """
    if not results:
        return "No results to display."

    # Build table rows
    rows = []
    for i, r in enumerate(results, 1):
        vrp = r.get('vrp_ratio', 0.0)
        tier = r.get('liquidity_tier', '')
        score = r.get('score', 0)

        # TRR badge
        position_limits = r.get('position_limits') or {}
        trr = ''
        if position_limits.get('tail_risk_level') == 'HIGH':
            trr = '\u26a0\ufe0f'

        rows.append({
            '#': i,
            'ticker': r.get('ticker', 'N/A'),
            'earnings': _format_earnings_date(r.get('earnings_date', '')),
            'vrp': f"{vrp:.1f}x {_vrp_icon(vrp)}",
            'score': score,
            'liquidity': _liquidity_display(tier),
            'trr': trr,
        })

    # Calculate column widths
    cols = [
        ('#',         3,  'right'),
        ('TICKER',    8,  'left'),
        ('Earnings',  10, 'left'),
        ('VRP',       9,  'left'),
        ('Score',     5,  'right'),
        ('LIQUIDITY', 12, 'left'),
        ('TRR',       3,  'left'),
    ]
    keys = ['#', 'ticker', 'earnings', 'vrp', 'score', 'liquidity', 'trr']

    def _display_width(s: str) -> int:
        """Approximate terminal display width accounting for wide/emoji chars."""
        width = 0
        for ch in s:
            cp = ord(ch)
            # Emoji and wide characters take ~2 columns
            if cp > 0x1F000 or (0x2600 <= cp <= 0x27BF) or (0xFE00 <= cp <= 0xFE0F):
                width += 2
            elif 0x2500 <= cp <= 0x257F:
                width += 1  # Box-drawing chars are single-width
            else:
                width += 1
        return width

    def pad(val, width, align):
        s = str(val)
        display_w = _display_width(s)
        padding = max(0, width - display_w)
        if align == 'right':
            return ' ' * padding + s
        return s + ' ' * padding

    # Build header
    sep_parts = []
    hdr_parts = []
    for label, width, align in cols:
        hdr_parts.append(pad(label, width, align))
        sep_parts.append('\u2500' * width)

    header = '\u2502 ' + ' \u2502 '.join(hdr_parts) + ' \u2502'
    separator = '\u251c\u2500' + '\u2500\u253c\u2500'.join(sep_parts) + '\u2500\u2524'
    top_border = '\u250c\u2500' + '\u2500\u252c\u2500'.join(sep_parts) + '\u2500\u2510'
    bot_border = '\u2514\u2500' + '\u2500\u2534\u2500'.join(sep_parts) + '\u2500\u2518'

    lines = [top_border, header, separator]

    for row in rows:
        vals = []
        for key, (label, width, align) in zip(keys, cols):
            vals.append(pad(row[key], width, align))
        lines.append('\u2502 ' + ' \u2502 '.join(vals) + ' \u2502')

    lines.append(bot_border)

    # Legend
    lines.append('')
    lines.append(
        'Legend: VRP \u2b50 EXCELLENT (\u22651.8x) | \u2713 GOOD (\u22651.4x) | \u25cb MARGINAL (\u22651.2x)'
    )
    lines.append(
        '        TRR \u26a0\ufe0f = HIGH tail risk (max 50 contracts)'
    )

    return '\n'.join(lines)


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
