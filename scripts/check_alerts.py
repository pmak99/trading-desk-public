#!/usr/bin/env python3
"""
Check for trading alerts - high VRP opportunities and position status.
Integrates with scan results and Alpaca positions.
"""

import sys
import re
import subprocess
from pathlib import Path
from datetime import datetime


# Alert thresholds
VRP_EXCELLENT_THRESHOLD = 7.0
VRP_GOOD_THRESHOLD = 4.0


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def get_today_scan() -> str:
    """Run today's scan and capture output."""
    trade_script = Path(__file__).parent.parent / "2.0" / "trade.sh"
    today = datetime.now().strftime('%Y-%m-%d')

    try:
        result = subprocess.run(
            [str(trade_script), "scan", today],
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes for large scans
        )
        return strip_ansi(result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        return "Error: Scan timed out after 5 minutes"
    except Exception as e:
        return f"Error running scan: {e}"


def parse_scan_for_alerts(scan_output: str) -> list[dict]:
    """Parse scan output for alert-worthy opportunities."""
    alerts = []
    current_ticker = None
    current_data = {}

    lines = scan_output.split('\n')

    for line in lines:
        # Ticker header - matches "Analyzing TICKER" pattern
        ticker_match = re.search(r'Analyzing\s+([A-Z]{1,5})\s*$', line)
        if ticker_match:
            if current_ticker and current_data.get('vrp_ratio', 0) >= VRP_GOOD_THRESHOLD:
                alerts.append(current_data)
            current_ticker = ticker_match.group(1)
            current_data = {'ticker': current_ticker}
            continue

        # VRP Ratio
        vrp_match = re.search(r'VRP(?:\s+Ratio)?[:\s]+(\d+\.?\d*)x', line, re.I)
        if vrp_match and current_data:
            current_data['vrp_ratio'] = float(vrp_match.group(1))

        # VRP Recommendation/Tier
        rec_match = re.search(r'Recommendation[:\s]+(EXCELLENT|GOOD|MARGINAL|SKIP)', line, re.I)
        if rec_match and current_data:
            current_data['vrp_tier'] = rec_match.group(1).upper()

        # Implied move
        implied_match = re.search(r'Implied Move[:\s]+(\d+\.?\d*)%', line, re.I)
        if implied_match and current_data:
            current_data['implied_move'] = float(implied_match.group(1))

        # Liquidity Tier
        liq_match = re.search(r'Liquidity Tier[:\s]+(EXCELLENT|WARNING|REJECT)', line, re.I)
        if liq_match and current_data:
            current_data['liquidity'] = liq_match.group(1).upper().rstrip('*')

        # Recommended strategy
        strat_match = re.search(r'(Iron Condor|Iron Butterfly|Bull Put Spread|Bear Call Spread)', line, re.I)
        if strat_match and current_data:
            current_data['strategy'] = strat_match.group(1)

        # Mark tradeable
        if 'TRADEABLE OPPORTUNITY' in line and current_data:
            current_data['tradeable'] = True

    # Last ticker
    if current_ticker and current_data.get('vrp_ratio', 0) >= VRP_GOOD_THRESHOLD:
        alerts.append(current_data)

    return alerts


def format_alert(alert: dict) -> str:
    """Format an alert for display."""
    vrp = alert.get('vrp_ratio', 0)
    tier = alert.get('vrp_tier', 'UNKNOWN')

    # Determine urgency
    if vrp >= VRP_EXCELLENT_THRESHOLD and alert.get('liquidity') == 'EXCELLENT':
        emoji = "🚨"
        urgency = "HIGH PRIORITY"
    elif vrp >= VRP_GOOD_THRESHOLD:
        emoji = "📊"
        urgency = "OPPORTUNITY"
    else:
        emoji = "📋"
        urgency = "WATCHLIST"

    lines = [
        f"{emoji} {urgency}: {alert['ticker']}",
        f"   VRP: {vrp:.1f}x ({tier})",
    ]

    if 'implied_move' in alert:
        lines.append(f"   Implied Move: {alert['implied_move']:.1f}%")
    if 'liquidity' in alert:
        lines.append(f"   Liquidity: {alert['liquidity']}")
    if 'strategy' in alert:
        lines.append(f"   Strategy: {alert['strategy']}")

    return '\n'.join(lines)


def check_positions():
    """Check Alpaca positions (placeholder - use MCP in actual implementation)."""
    print("\n📊 POSITION CHECK")
    print("   Use Alpaca MCP tools to check current positions:")
    print("   - mcp__alpaca__alpaca_list_positions")
    print("   - mcp__alpaca__alpaca_account_overview")


def main():
    """Main entry point."""
    print("="*60)
    print("TRADING ALERT CHECK")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*60)

    # Run scan
    print("\nScanning for opportunities...")
    scan_output = get_today_scan()

    # Parse for alerts
    alerts = parse_scan_for_alerts(scan_output)

    # Filter and sort
    excellent_alerts = [a for a in alerts if a.get('vrp_ratio', 0) >= VRP_EXCELLENT_THRESHOLD]
    good_alerts = [a for a in alerts if VRP_GOOD_THRESHOLD <= a.get('vrp_ratio', 0) < VRP_EXCELLENT_THRESHOLD]

    # Display alerts
    if excellent_alerts:
        print(f"\n{'='*60}")
        print("🚨 HIGH-PRIORITY ALERTS (VRP >= 7.0x)")
        print("="*60)
        for alert in sorted(excellent_alerts, key=lambda x: x.get('vrp_ratio', 0), reverse=True):
            print(format_alert(alert))
            print()

    if good_alerts:
        print(f"\n{'='*60}")
        print("📊 WATCHLIST OPPORTUNITIES (VRP >= 4.0x)")
        print("="*60)
        for alert in sorted(good_alerts, key=lambda x: x.get('vrp_ratio', 0), reverse=True):
            print(format_alert(alert))
            print()

    if not excellent_alerts and not good_alerts:
        print("\n✓ No high-VRP opportunities found today")

    # Position check reminder
    check_positions()

    print("\n" + "="*60)
    print(f"Alert check complete. {len(excellent_alerts)} high-priority, {len(good_alerts)} watchlist.")


if __name__ == "__main__":
    main()
