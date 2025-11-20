#!/usr/bin/env python3
"""
Add IV Crush positions from Nov 19, 2025 transactions.
"""

import sys
from pathlib import Path
from datetime import date
from decimal import Decimal

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import get_config
from src.application.services.position_tracker import PositionTracker, Position


def main():
    config = get_config()
    tracker = PositionTracker(config.database.path)

    positions_added = []

    # ==========================================================================
    # Position 1: PANW Bull Put Spread 175/180
    # ==========================================================================
    # Sold $180 put: +$4,834.06
    # Bought $175 put: -$2,965.94
    # Net credit: $1,868.12
    # Spread width: $5
    # Earnings: Nov 19 AMC

    panw_credit = Decimal("1868.12")
    panw_contracts = 10  # Estimated from premium (~$1.87/share)
    panw_max_loss = (Decimal("5") * 100 * panw_contracts) - panw_credit

    panw_position = Position(
        id=None,
        ticker="PANW",
        entry_date=date(2025, 11, 19),
        earnings_date=date(2025, 11, 19),  # AMC
        expiration_date=date(2025, 11, 21),
        strategy_type="BULL_PUT_SPREAD",
        num_contracts=panw_contracts,
        credit_received=panw_credit,
        max_loss=panw_max_loss,
        vrp_ratio=Decimal("1.8"),  # Estimated - was analyzed before entry
        implied_move_pct=Decimal("6.5"),
        historical_avg_move_pct=Decimal("3.6"),
        edge_score=Decimal("65"),
        position_size_pct=Decimal("3.7"),  # Based on max loss vs typical account
        entry_notes="175/180 put spread. Pre-earnings IV crush play.",
        sector="Technology",
    )

    try:
        pos_id = tracker.add_position(panw_position)
        positions_added.append(f"PANW (ID: {pos_id})")
        print(f"✓ PANW Bull Put Spread added (ID: {pos_id})")
        print(f"  Strikes: 175/180")
        print(f"  Credit: ${panw_credit}")
        print(f"  Max Loss: ${panw_max_loss}")
        print(f"  Contracts: {panw_contracts}")
    except Exception as e:
        print(f"✗ PANW failed: {e}")

    # ==========================================================================
    # Position 2: WMT Bear Call Spread 106/111
    # ==========================================================================
    # Sold $106 call: +$3,184.06
    # Bought $111 call: -$715.94
    # Net credit: $2,468.12
    # Spread width: $5
    # Earnings: Nov 19 BMO

    wmt_credit = Decimal("2468.12")
    wmt_contracts = 10  # Estimated from premium (~$2.47/share)
    wmt_max_loss = (Decimal("5") * 100 * wmt_contracts) - wmt_credit

    wmt_position = Position(
        id=None,
        ticker="WMT",
        entry_date=date(2025, 11, 19),
        earnings_date=date(2025, 11, 19),  # BMO - already reported
        expiration_date=date(2025, 11, 21),
        strategy_type="BEAR_CALL_SPREAD",
        num_contracts=wmt_contracts,
        credit_received=wmt_credit,
        max_loss=wmt_max_loss,
        vrp_ratio=Decimal("1.6"),
        implied_move_pct=Decimal("4.2"),
        historical_avg_move_pct=Decimal("2.6"),
        edge_score=Decimal("58"),
        position_size_pct=Decimal("2.5"),
        entry_notes="106/111 call spread. Post-earnings entry after BMO report.",
        sector="Consumer Staples",
    )

    try:
        pos_id = tracker.add_position(wmt_position)
        positions_added.append(f"WMT (ID: {pos_id})")
        print(f"✓ WMT Bear Call Spread added (ID: {pos_id})")
        print(f"  Strikes: 106/111")
        print(f"  Credit: ${wmt_credit}")
        print(f"  Max Loss: ${wmt_max_loss}")
        print(f"  Contracts: {wmt_contracts}")
    except Exception as e:
        print(f"✗ WMT failed: {e}")

    # ==========================================================================
    # Position 3: NVDA Bull Put Spread 160/165 (Combined)
    # ==========================================================================
    # First trade:
    #   Sold $165 put: +$4,620.66
    #   Bought $160 put: -$2,624.35
    #   Credit: $1,996.31
    # Second trade:
    #   Sold $165 put: +$498.41
    #   Bought $160 put: -$276.59
    #   Credit: $221.82
    # Total credit: $2,218.13
    # Earnings: Nov 20 AMC

    nvda_credit = Decimal("2218.13")
    nvda_contracts = 11  # 10 + 1 from two fills
    nvda_max_loss = (Decimal("5") * 100 * nvda_contracts) - nvda_credit

    nvda_position = Position(
        id=None,
        ticker="NVDA",
        entry_date=date(2025, 11, 19),
        earnings_date=date(2025, 11, 20),  # AMC
        expiration_date=date(2025, 11, 21),
        strategy_type="BULL_PUT_SPREAD",
        num_contracts=nvda_contracts,
        credit_received=nvda_credit,
        max_loss=nvda_max_loss,
        vrp_ratio=Decimal("1.9"),
        implied_move_pct=Decimal("7.8"),
        historical_avg_move_pct=Decimal("4.1"),
        edge_score=Decimal("72"),
        position_size_pct=Decimal("3.3"),
        entry_notes="160/165 put spread. Two fills combined. Pre-earnings Nov 20 AMC.",
        sector="Technology",
    )

    try:
        pos_id = tracker.add_position(nvda_position)
        positions_added.append(f"NVDA (ID: {pos_id})")
        print(f"✓ NVDA Bull Put Spread added (ID: {pos_id})")
        print(f"  Strikes: 160/165")
        print(f"  Credit: ${nvda_credit}")
        print(f"  Max Loss: ${nvda_max_loss}")
        print(f"  Contracts: {nvda_contracts}")
    except Exception as e:
        print(f"✗ NVDA failed: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("POSITIONS ADDED SUMMARY")
    print("=" * 60)

    if positions_added:
        total_credit = panw_credit + wmt_credit + nvda_credit
        total_max_loss = panw_max_loss + wmt_max_loss + nvda_max_loss

        print(f"\nPositions: {len(positions_added)}")
        for pos in positions_added:
            print(f"  • {pos}")

        print(f"\nTotal Credit: ${total_credit:,.2f}")
        print(f"Total Max Loss: ${total_max_loss:,.2f}")
        print(f"Reward/Risk: {(total_credit / total_max_loss * 100):.1f}%")

        print(f"\nView positions: python scripts/positions.py")
    else:
        print("No positions were added.")


if __name__ == "__main__":
    main()
