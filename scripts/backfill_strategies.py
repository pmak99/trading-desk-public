#!/usr/bin/env python3
"""
Backfill strategies from existing trade_journal entries.

Groups unlinked legs into strategies based on auto-detection,
then creates strategy records and links the legs.
"""

import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from strategy_grouper import group_legs_into_strategies, StrategyGroup, Confidence


@dataclass
class LegRecord:
    """Database record for a trade leg."""
    id: int
    symbol: str
    acquired_date: Optional[str]
    sale_date: str
    days_held: Optional[int]
    option_type: Optional[str]
    strike: Optional[float]
    expiration: Optional[str]
    quantity: Optional[int]
    cost_basis: float
    proceeds: float
    gain_loss: float
    is_winner: bool
    term: Optional[str]
    earnings_date: Optional[str]
    actual_move: Optional[float]


def load_unlinked_legs(db_path: str) -> List[Dict[str, Any]]:
    """Load all trade_journal entries without a strategy_id."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, symbol, acquired_date, sale_date, days_held, option_type,
                   strike, expiration, quantity, cost_basis, proceeds, gain_loss,
                   is_winner, term, earnings_date, actual_move
            FROM trade_journal
            WHERE strategy_id IS NULL
            ORDER BY symbol, sale_date, expiration
        """)

        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to load unlinked legs from {db_path}: {e}") from e


def create_strategy(
    db_path: str,
    symbol: str,
    strategy_type: str,
    acquired_date: str,
    sale_date: str,
    days_held: Optional[int],
    expiration: Optional[str],
    quantity: Optional[int],
    gain_loss: float,
    is_winner: bool,
    earnings_date: Optional[str] = None,
    actual_move: Optional[float] = None,
) -> int:
    """
    Create a strategy record and return its ID.

    Note: This function opens its own connection. For transactional operations,
    use create_strategy_with_conn() instead.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA foreign_keys=ON')
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO strategies
            (symbol, strategy_type, acquired_date, sale_date, days_held, expiration,
             quantity, gain_loss, is_winner, earnings_date, actual_move)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, strategy_type, acquired_date, sale_date, days_held, expiration,
            quantity, gain_loss, is_winner, earnings_date, actual_move
        ))

        strategy_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return strategy_id
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to create strategy for {symbol}: {e}") from e


def create_strategy_with_conn(
    conn: sqlite3.Connection,
    symbol: str,
    strategy_type: str,
    acquired_date: str,
    sale_date: str,
    days_held: Optional[int],
    expiration: Optional[str],
    quantity: Optional[int],
    gain_loss: float,
    is_winner: bool,
    earnings_date: Optional[str] = None,
    actual_move: Optional[float] = None,
) -> int:
    """
    Create a strategy record using an existing connection and return its ID.
    Does not commit - caller is responsible for transaction management.
    """
    try:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO strategies
            (symbol, strategy_type, acquired_date, sale_date, days_held, expiration,
             quantity, gain_loss, is_winner, earnings_date, actual_move)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, strategy_type, acquired_date, sale_date, days_held, expiration,
            quantity, gain_loss, is_winner, earnings_date, actual_move
        ))

        return cursor.lastrowid
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to create strategy for {symbol}: {e}") from e


def link_legs_to_strategy(db_path: str, leg_ids: List[int], strategy_id: int):
    """
    Link trade_journal legs to a strategy.

    Note: This function opens its own connection. For transactional operations,
    use link_legs_to_strategy_with_conn() instead.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA foreign_keys=ON')
        cursor = conn.cursor()

        cursor.executemany(
            "UPDATE trade_journal SET strategy_id = ? WHERE id = ?",
            [(strategy_id, leg_id) for leg_id in leg_ids]
        )

        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to link legs {leg_ids} to strategy {strategy_id}: {e}") from e


def link_legs_to_strategy_with_conn(
    conn: sqlite3.Connection,
    leg_ids: List[int],
    strategy_id: int
):
    """
    Link trade_journal legs to a strategy using an existing connection.
    Does not commit - caller is responsible for transaction management.
    """
    try:
        cursor = conn.cursor()

        cursor.executemany(
            "UPDATE trade_journal SET strategy_id = ? WHERE id = ?",
            [(strategy_id, leg_id) for leg_id in leg_ids]
        )
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to link legs {leg_ids} to strategy {strategy_id}: {e}") from e


def run_backfill(db_path: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Run the backfill process.

    Returns:
        Dict with statistics: strategies_created, legs_linked, needs_review
    """
    legs_data = load_unlinked_legs(db_path)

    if not legs_data:
        return {
            'strategies_created': 0,
            'legs_linked': 0,
            'needs_review': [],
        }

    # Convert to LegRecord objects for grouper
    legs = [
        LegRecord(
            id=d['id'],
            symbol=d['symbol'],
            acquired_date=d['acquired_date'],
            sale_date=d['sale_date'],
            days_held=d['days_held'],
            option_type=d['option_type'],
            strike=d['strike'],
            expiration=d['expiration'],
            quantity=d['quantity'],
            cost_basis=d['cost_basis'],
            proceeds=d['proceeds'],
            gain_loss=d['gain_loss'],
            is_winner=bool(d['is_winner']),
            term=d['term'],
            earnings_date=d['earnings_date'],
            actual_move=d['actual_move'],
        )
        for d in legs_data
    ]

    # Group legs into strategies
    groups = group_legs_into_strategies(legs)

    strategies_created = 0
    legs_linked = 0
    needs_review = []

    for group in groups:
        if group.needs_review:
            needs_review.append({
                'symbol': group.symbol,
                'leg_count': len(group.legs),
                'leg_ids': [leg.id for leg in group.legs],
                'combined_pnl': group.combined_pnl,
            })
            continue

        if dry_run:
            strategies_created += 1
            legs_linked += len(group.legs)
            continue

        # Determine quantity (max across legs for normalization)
        quantities = [leg.quantity for leg in group.legs if leg.quantity]
        quantity = max(quantities) if quantities else None

        # Use first leg's earnings data
        first_leg = group.legs[0]

        # Calculate days_held
        days_held = first_leg.days_held

        # Create strategy and link legs in a single transaction
        try:
            conn = sqlite3.connect(db_path)
            # CRITICAL: Enable foreign key constraints
            conn.execute('PRAGMA foreign_keys=ON')
            try:
                # Create strategy (does not commit)
                strategy_id = create_strategy_with_conn(
                    conn,
                    symbol=group.symbol,
                    strategy_type=group.strategy_type,
                    acquired_date=group.acquired_date,
                    sale_date=group.sale_date,
                    days_held=days_held,
                    expiration=group.expiration,
                    quantity=quantity,
                    gain_loss=group.combined_pnl,
                    is_winner=group.is_winner,
                    earnings_date=first_leg.earnings_date,
                    actual_move=first_leg.actual_move,
                )

                # Link legs (does not commit)
                leg_ids = [leg.id for leg in group.legs]
                link_legs_to_strategy_with_conn(conn, leg_ids, strategy_id)

                # Commit the entire transaction
                conn.commit()

                strategies_created += 1
                legs_linked += len(group.legs)

            except Exception as e:
                # Rollback on any error
                conn.rollback()
                raise RuntimeError(
                    f"Failed to backfill strategy for {group.symbol} "
                    f"(legs: {[leg.id for leg in group.legs]}): {e}"
                ) from e
            finally:
                conn.close()

        except RuntimeError:
            # Re-raise RuntimeError to propagate to caller
            raise

    return {
        'strategies_created': strategies_created,
        'legs_linked': legs_linked,
        'needs_review': needs_review,
    }


def print_backfill_report(stats: Dict[str, Any], dry_run: bool):
    """Print a formatted report of the backfill results."""
    prefix = "[DRY RUN] " if dry_run else ""

    print(f"\n{prefix}Backfill Results:")
    print(f"  Strategies created: {stats['strategies_created']}")
    print(f"  Legs linked: {stats['legs_linked']}")

    if stats['needs_review']:
        print(f"\n{prefix}Needs Manual Review ({len(stats['needs_review'])} items):")
        for item in stats['needs_review']:
            print(f"  - {item['symbol']}: {item['leg_count']} legs, P&L: ${item['combined_pnl']:,.2f}")


def main():
    import argparse

    project_root = Path(__file__).parent.parent
    default_db = project_root / "core" / "data" / "ivcrush.db"

    parser = argparse.ArgumentParser(description='Backfill strategies from trade journal')
    parser.add_argument('--db', default=str(default_db), help='Database path')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')

    args = parser.parse_args()

    print(f"Database: {args.db}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")

    stats = run_backfill(args.db, dry_run=args.dry_run)
    print_backfill_report(stats, args.dry_run)


if __name__ == "__main__":
    main()
