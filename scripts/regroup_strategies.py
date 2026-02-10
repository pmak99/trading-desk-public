#!/usr/bin/env python3
"""
Regroup orphan trade legs into strategies based on improved matching.

Groups by symbol + expiration (allowing different close dates for spread legs),
then identifies spread patterns based on distinct strikes.
"""

import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime


@dataclass
class LegRecord:
    """Database record for a trade leg."""
    id: int
    symbol: str
    acquired_date: Optional[str]
    sale_date: str
    option_type: Optional[str]
    strike: Optional[float]
    expiration: Optional[str]
    quantity: int
    cost_basis: float
    proceeds: float
    gain_loss: float
    is_winner: bool
    earnings_date: Optional[str]
    actual_move: Optional[float]

    @property
    def is_short(self) -> bool:
        """Short position if received more than paid (proceeds > cost_basis)."""
        return self.proceeds > self.cost_basis

    @property
    def direction(self) -> str:
        return "SHORT" if self.is_short else "LONG"


def load_orphan_legs(db_path: str) -> List[LegRecord]:
    """Load all trade_journal entries without a strategy_id."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, symbol, acquired_date, sale_date, option_type,
               strike, expiration, quantity, cost_basis, proceeds, gain_loss,
               is_winner, earnings_date, actual_move
        FROM trade_journal
        WHERE strategy_id IS NULL
          AND option_type IS NOT NULL
        ORDER BY symbol, expiration, strike
    """)

    legs = []
    for row in cursor.fetchall():
        legs.append(LegRecord(
            id=row['id'],
            symbol=row['symbol'],
            acquired_date=row['acquired_date'],
            sale_date=row['sale_date'],
            option_type=row['option_type'],
            strike=row['strike'],
            expiration=row['expiration'],
            quantity=row['quantity'] or 0,
            cost_basis=row['cost_basis'],
            proceeds=row['proceeds'],
            gain_loss=row['gain_loss'],
            is_winner=bool(row['is_winner']),
            earnings_date=row['earnings_date'],
            actual_move=row['actual_move'],
        ))

    conn.close()
    return legs



def group_legs_by_expiration(legs: List[LegRecord]) -> Dict[Tuple[str, str, str], List[LegRecord]]:
    """
    Group legs by (symbol, expiration, option_type).

    This allows spread legs that closed on different days to be grouped together.
    """
    groups = defaultdict(list)
    for leg in legs:
        key = (leg.symbol, leg.expiration or "", leg.option_type or "")
        groups[key].append(leg)
    return dict(groups)


def match_spread_legs(legs: List[LegRecord]) -> List[List[LegRecord]]:
    """
    Match spread legs by quantity and direction.

    For a valid spread, we need:
    - Same quantity on both strikes
    - One short, one long (or at least opposite directions)

    Returns list of matched leg groups.
    """
    if len(legs) < 2:
        return [legs]

    strikes = sorted(set(leg.strike for leg in legs if leg.strike))
    if len(strikes) != 2:
        # Not a simple 2-leg spread, return as-is for now
        return [legs]

    # Group by strike
    by_strike = defaultdict(list)
    for leg in legs:
        by_strike[leg.strike].append(leg)

    low_strike_legs = by_strike[strikes[0]]
    high_strike_legs = by_strike[strikes[1]]

    # Simple case: equal counts on each strike
    if len(low_strike_legs) == len(high_strike_legs):
        # Pair them up
        spreads = []
        for low, high in zip(low_strike_legs, high_strike_legs):
            spreads.append([low, high])
        return spreads

    # Complex case: unequal counts - group all together as one strategy
    return [legs]


def create_strategy_and_link(
    conn: sqlite3.Connection,
    legs: List[LegRecord],
    strategy_type: str,
) -> int:
    """Create a strategy record and link all legs to it."""
    cursor = conn.cursor()

    # Calculate aggregates
    combined_pnl = sum(leg.gain_loss for leg in legs)
    is_winner = combined_pnl > 0
    total_quantity = sum(leg.quantity for leg in legs)

    # Determine chronological open/close dates
    # Fidelity credit trades have inverted dates (sale < acquired),
    # so we normalize: acquired_date = earliest, sale_date = latest
    all_dates = []
    for leg in legs:
        if leg.acquired_date:
            all_dates.append(leg.acquired_date)
        if leg.sale_date:
            all_dates.append(leg.sale_date)

    acquired_date = min(all_dates) if all_dates else None
    sale_date = max(all_dates) if all_dates else legs[0].sale_date

    # Calculate days held
    days_held = None
    if acquired_date and sale_date:
        try:
            acq = datetime.strptime(acquired_date, "%Y-%m-%d")
            sale = datetime.strptime(sale_date, "%Y-%m-%d")
            days_held = abs((sale - acq).days)
        except ValueError:
            pass

    # Get first leg's data for symbol, expiration, earnings info
    first_leg = legs[0]

    # Insert strategy
    cursor.execute("""
        INSERT INTO strategies
        (symbol, strategy_type, acquired_date, sale_date, days_held, expiration,
         quantity, gain_loss, is_winner, earnings_date, actual_move)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        first_leg.symbol,
        strategy_type,
        acquired_date,
        sale_date,
        days_held,
        first_leg.expiration,
        total_quantity,
        combined_pnl,
        is_winner,
        first_leg.earnings_date,
        first_leg.actual_move,
    ))

    strategy_id = cursor.lastrowid

    # Link all legs
    for leg in legs:
        cursor.execute(
            "UPDATE trade_journal SET strategy_id = ? WHERE id = ?",
            (strategy_id, leg.id)
        )

    return strategy_id


def run_regrouping(db_path: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Run the regrouping process.

    Returns stats on strategies created.
    """
    legs = load_orphan_legs(db_path)

    if not legs:
        return {
            'orphan_legs_found': 0,
            'strategies_created': 0,
            'legs_linked': 0,
            'by_type': {},
            'details': [],
        }

    print(f"Found {len(legs)} orphan legs")

    # Group by symbol + expiration + option_type
    groups = group_legs_by_expiration(legs)
    print(f"Grouped into {len(groups)} expiration groups")

    stats = {
        'orphan_legs_found': len(legs),
        'strategies_created': 0,
        'legs_linked': 0,
        'by_type': defaultdict(int),
        'details': [],
    }

    if dry_run:
        conn = None
    else:
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA foreign_keys=ON')

    try:
        for (symbol, expiration, option_type), group_legs in groups.items():
            # For groups with 2+ distinct strikes and >2 legs, try to match spread pairs
            distinct_group_strikes = len(set(leg.strike for leg in group_legs if leg.strike))
            if distinct_group_strikes == 2 and len(group_legs) > 2:
                matched_groups = match_spread_legs(group_legs)
            else:
                matched_groups = [group_legs]

            for matched_legs in matched_groups:
                leg_count = len(matched_legs)
                combined_pnl = sum(leg.gain_loss for leg in matched_legs)

                # Determine strategy type from strike structure
                distinct_strikes = len(set(leg.strike for leg in matched_legs if leg.strike))
                if distinct_strikes <= 1:
                    # All legs at same strike = multiple fills of a SINGLE
                    final_type = "SINGLE"
                elif distinct_strikes == 4:
                    final_type = "IRON_CONDOR"
                else:
                    final_type = "SPREAD"

                detail = {
                    'symbol': symbol,
                    'expiration': expiration,
                    'option_type': option_type,
                    'leg_count': leg_count,
                    'leg_ids': [leg.id for leg in matched_legs],
                    'strategy_type': final_type,
                    'combined_pnl': round(combined_pnl, 2),
                }
                stats['details'].append(detail)

                if dry_run:
                    stats['strategies_created'] += 1
                    stats['legs_linked'] += leg_count
                    stats['by_type'][final_type] += 1
                else:
                    strategy_id = create_strategy_and_link(conn, matched_legs, final_type)
                    stats['strategies_created'] += 1
                    stats['legs_linked'] += leg_count
                    stats['by_type'][final_type] += 1

        if conn and not dry_run:
            conn.commit()

    except Exception as e:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

    return stats


def print_report(stats: Dict[str, Any], dry_run: bool):
    """Print formatted report."""
    prefix = "[DRY RUN] " if dry_run else ""

    print(f"\n{prefix}Regrouping Results:")
    print(f"  Orphan legs found: {stats['orphan_legs_found']}")
    print(f"  Strategies created: {stats['strategies_created']}")
    print(f"  Legs linked: {stats['legs_linked']}")

    print(f"\n{prefix}By Strategy Type:")
    for stype, count in sorted(stats['by_type'].items()):
        print(f"  {stype}: {count}")

    # Show some examples
    print(f"\n{prefix}Sample Groupings (first 10):")
    for detail in stats['details'][:10]:
        pnl = detail['combined_pnl']
        winner = "WIN" if pnl > 0 else "LOSS"
        print(f"  {detail['symbol']} {detail['option_type']} exp:{detail['expiration']} "
              f"- {detail['strategy_type']} ({detail['leg_count']} legs) "
              f"${pnl:,.2f} [{winner}]")


def main():
    import argparse

    project_root = Path(__file__).parent.parent
    default_db = project_root / "2.0" / "data" / "ivcrush.db"

    parser = argparse.ArgumentParser(description='Regroup orphan legs into strategies')
    parser.add_argument('--db', default=str(default_db), help='Database path')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')

    args = parser.parse_args()

    print(f"Database: {args.db}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")

    stats = run_regrouping(args.db, dry_run=args.dry_run)
    print_report(stats, args.dry_run)


if __name__ == "__main__":
    main()
