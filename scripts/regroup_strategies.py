#!/usr/bin/env python3
"""
Regroup orphan trade legs into strategies based on improved matching.

Groups by symbol + expiration (allowing different close dates for spread legs),
then identifies spread patterns based on distinct strikes.

Also groups stock legs (option_type IS NULL) into STOCK strategies,
with deduplication for multiple-import artifacts.
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


def load_orphan_legs(db_path: str, account_type: str = 'TAXABLE') -> List[LegRecord]:
    """Load option trade_journal entries without a strategy_id for the given account_type."""
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
          AND account_type = ?
        ORDER BY symbol, expiration, strike
    """, (account_type,))

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


def load_stock_orphans(db_path: str, account_type: str) -> List[LegRecord]:
    """
    Load stock trade_journal entries without a strategy_id for the given account_type.

    Deduplicates exact duplicate rows (artifact of multiple CSV imports) by keeping
    the lowest id per (symbol, acquired_date, sale_date, quantity, cost_basis, proceeds).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Select the canonical row (lowest id) per duplicate group
    cursor.execute("""
        SELECT id, symbol, acquired_date, sale_date, option_type,
               strike, expiration, quantity, cost_basis, proceeds, gain_loss,
               is_winner, earnings_date, actual_move
        FROM trade_journal
        WHERE strategy_id IS NULL
          AND option_type IS NULL
          AND account_type = ?
          AND id IN (
              SELECT MIN(id)
              FROM trade_journal
              WHERE strategy_id IS NULL
                AND option_type IS NULL
                AND account_type = ?
              GROUP BY symbol,
                       COALESCE(acquired_date, ''),
                       sale_date,
                       quantity,
                       ROUND(cost_basis, 2),
                       ROUND(proceeds, 2)
          )
        ORDER BY symbol, sale_date
    """, (account_type, account_type))

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


def group_stock_legs(legs: List[LegRecord]) -> Dict[Tuple[str, str], List[LegRecord]]:
    """
    Group stock legs by (symbol, sale_date).

    Each sale event (cover/assignment/close) becomes one STOCK strategy.
    Multiple lots closed on the same day are aggregated together.
    """
    groups = defaultdict(list)
    for leg in legs:
        key = (leg.symbol, leg.sale_date)
        groups[key].append(leg)
    return dict(groups)


def consolidate_duplicate_stocks(db_path: str, dry_run: bool = False) -> int:
    """
    Merge duplicate STOCK strategies that were created across multiple import sessions.

    Each CSV import adds new trade_journal rows (different lots/cost_basis), and
    regroup_strategies creates a fresh STOCK strategy from each batch of orphans,
    leaving the previous strategies intact. This results in N copies of each stock
    position, one per import session.

    Groups by (symbol, sale_date, account_type) — keeps lowest id, sums all legs
    once, corrects gain_loss and quantity. Runs across all account types.

    Returns count of strategies deleted.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT account_type, symbol, sale_date,
               GROUP_CONCAT(id ORDER BY id) ids_str,
               COUNT(*) copies,
               MIN(id) keep_id
        FROM strategies
        WHERE strategy_type = 'STOCK'
        GROUP BY account_type, symbol, sale_date
        HAVING COUNT(*) > 1
    """)
    groups = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not groups:
        return 0

    print(f"\nFound {len(groups)} duplicate stock group(s):")
    for g in groups:
        print(f"  {g['account_type']:8} {g['symbol']:6} {g['sale_date']}  "
              f"→ {g['copies']} copies  (ids: {g['ids_str']})")

    if dry_run:
        return sum(g['copies'] - 1 for g in groups)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    total_deleted = 0

    try:
        for g in groups:
            ids = [int(x) for x in g['ids_str'].split(',')]
            keep = g['keep_id']
            others = [i for i in ids if i != keep]

            ph = ','.join('?' * len(ids))
            cursor.execute(
                f"SELECT COALESCE(SUM(quantity), 0) q, COALESCE(SUM(gain_loss), 0) pnl "
                f"FROM trade_journal WHERE strategy_id IN ({ph})",
                ids,
            )
            row = cursor.fetchone()
            # gain_loss from trade_journal legs = ground truth; strategies.gain_loss may differ
            # Use average of strategy-level gain_loss (which was set correctly from trade_journal)
            cursor.execute(
                f"SELECT AVG(gain_loss) avg_pnl FROM strategies WHERE id IN ({ph})",
                ids,
            )
            avg_row = cursor.fetchone()
            correct_pnl = avg_row['avg_pnl']
            total_qty = row['q'] // g['copies']  # legs were triple-linked; divide back
            is_winner = 1 if correct_pnl > 0 else 0

            cursor.execute(
                "UPDATE strategies SET gain_loss=?, is_winner=?, quantity=? WHERE id=?",
                (correct_pnl, is_winner, total_qty, keep),
            )
            for oid in others:
                cursor.execute(
                    "UPDATE trade_journal SET strategy_id=? WHERE strategy_id=?",
                    (keep, oid),
                )
                cursor.execute("DELETE FROM strategies WHERE id=?", (oid,))
                total_deleted += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return total_deleted


def _classify_by_strike_count(distinct_strike_count: int) -> str:
    if distinct_strike_count <= 1:
        return "SINGLE"
    elif distinct_strike_count == 4:
        return "IRON_CONDOR"
    else:
        return "SPREAD"


def consolidate_fragmented_spreads(db_path: str, dry_run: bool = False, account_type: str = 'TAXABLE') -> int:
    """
    Merge option strategies that represent the same position but were fragmented
    into separate strategy rows — one per strike, or one per import session.

    Two ways this happens:
    1. Old per-fill pairing logic split a single multi-strike position into N
       single-strike strategies (each individually typed SINGLE) instead of one
       SPREAD/IRON_CONDOR.
    2. run_regrouping() only ever links currently-orphaned legs into a NEW
       strategy — it never checks whether a strategy already exists for the
       same (symbol, expiration, option_type, acquired_date, account_type).
       A position whose legs arrive across two separate CSV import sessions
       (e.g. partially closed on day 1, rest closed on day 2) therefore ends
       up as two strategies instead of one, every time.

    Groups by (symbol, acquired_date, expiration, account_type) using each
    strategy's linked trade_journal legs, restricted to strategies whose legs
    are ALL the same option_type (so genuine mixed PUT+CALL positions, e.g.
    historical STRANGLE trades, are left untouched). Any group with >1
    strategy gets merged: pnl and quantity summed, strategy_type re-derived
    from the total distinct strikes across the merged legs, lowest id kept.

    Safety: a group containing a REPAIR or ROLL trade_type is NEVER
    auto-merged — those are deliberately-linked follow-up trades against a
    separate NEW position (tracked via campaign_id/parent_strategy_id, see
    CLAUDE.md "Campaign chains"), not fill fragments of the same entry.
    Merging them would blend two economically distinct trades into one row
    and corrupt the REPAIR/ROLL win-rate evidence behind the "never repair"
    rule. Such groups are printed for manual review and skipped.

    For groups that do merge, campaign_id/trade_type/earnings_date/actual_move
    are coalesced onto the surviving row from whichever fragment has them
    (fragmentation can leave metadata on only one side, e.g. a NEW position
    fragmented into two rows where only one carries the campaign_id linking
    it to a REPAIR), acquired_date/sale_date are widened to the full min/max
    span and days_held recomputed, and any OTHER strategy's parent_strategy_id
    pointing at a row being deleted is repointed at the surviving id.

    Returns count of strategies deleted (merged away).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.id, s.symbol, s.acquired_date, s.sale_date, s.expiration, s.account_type,
               s.gain_loss, s.quantity, s.trade_type, s.campaign_id,
               s.earnings_date, s.actual_move,
               GROUP_CONCAT(DISTINCT j.option_type) AS opt_types,
               GROUP_CONCAT(DISTINCT CASE WHEN j.strike IS NOT NULL AND j.strike != 0
                             THEN CAST(ROUND(j.strike, 4) AS TEXT) END) AS strikes_raw
        FROM strategies s
        JOIN trade_journal j ON j.strategy_id = s.id
        WHERE s.expiration IS NOT NULL
          AND s.account_type = ?
        GROUP BY s.id
        ORDER BY s.id
    """, (account_type,))

    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Group strategies with the same identity, skipping mixed-option-type
    # strategies (real strangles/combos) entirely.
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        opt_types = set((row['opt_types'] or '').split(','))
        if len(opt_types) != 1:
            continue
        option_type = next(iter(opt_types))
        key = (row['symbol'], row['acquired_date'], row['expiration'], row['account_type'], option_type)
        groups[key].append(row)

    candidates = {k: v for k, v in groups.items() if len(v) > 1}
    if not candidates:
        return 0

    fragmented = {}
    skipped = {}
    for key, strats in candidates.items():
        trade_types = set(s['trade_type'] for s in strats if s['trade_type'])
        if trade_types & {'REPAIR', 'ROLL'}:
            skipped[key] = strats
        else:
            fragmented[key] = strats

    def _all_strikes(strats: List[dict]) -> List[str]:
        vals = set()
        for s in strats:
            if s['strikes_raw']:
                vals.update(s['strikes_raw'].split(','))
        return sorted(vals, key=float)

    if skipped:
        print(f"\nSkipped {len(skipped)} group(s) containing REPAIR/ROLL trades "
              f"(campaign-linked, not fill fragments — needs manual review):")
        for (symbol, acq, exp, acct, option_type), strats in skipped.items():
            ids = sorted(s['id'] for s in strats)
            print(f"  {symbol} {option_type} {acq} exp:{exp} ids:{ids} strikes:{_all_strikes(strats)}")

    if not fragmented:
        return 0

    print(f"\nFound {len(fragmented)} fragmented spread group(s):")
    for (symbol, acq, exp, acct, option_type), strats in fragmented.items():
        combined_pnl = sum(s['gain_loss'] for s in strats)
        ids = sorted(s['id'] for s in strats)
        print(f"  {symbol} {option_type} {acq} exp:{exp} ids:{ids} strikes:{_all_strikes(strats)} "
              f"→ {len(strats)} strategies → 1  (${combined_pnl:,.2f})")

    if dry_run:
        return sum(len(v) - 1 for v in fragmented.values())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    total_deleted = 0

    try:
        for strats in fragmented.values():
            keep = min(strats, key=lambda s: s['id'])
            others = [s for s in strats if s['id'] != keep['id']]
            all_ids = [keep['id']] + [s['id'] for s in others]

            combined_pnl = sum(s['gain_loss'] for s in strats)
            is_winner = 1 if combined_pnl > 0 else 0

            campaign_id = next((s['campaign_id'] for s in strats if s['campaign_id']), None)
            trade_type = next((s['trade_type'] for s in strats if s['trade_type']), None)
            earnings_date = next((s['earnings_date'] for s in strats if s['earnings_date']), None)
            actual_move = next((s['actual_move'] for s in strats if s['actual_move'] is not None), None)
            acquired_date = min((s['acquired_date'] for s in strats if s['acquired_date']), default=None)
            sale_date = max((s['sale_date'] for s in strats if s['sale_date']), default=None)

            days_held = None
            if acquired_date and sale_date:
                try:
                    acq_dt = datetime.strptime(acquired_date, "%Y-%m-%d")
                    sale_dt = datetime.strptime(sale_date, "%Y-%m-%d")
                    days_held = abs((sale_dt - acq_dt).days)
                except ValueError:
                    pass

            total_qty = sum(s['quantity'] or 0 for s in strats)
            final_type = _classify_by_strike_count(len(_all_strikes(strats)))

            cursor.execute(
                """UPDATE strategies
                   SET gain_loss=?, is_winner=?, quantity=?, strategy_type=?,
                       campaign_id=?, trade_type=?, earnings_date=?, actual_move=?,
                       acquired_date=?, sale_date=?, days_held=?
                   WHERE id=?""",
                (combined_pnl, is_winner, total_qty, final_type,
                 campaign_id, trade_type, earnings_date, actual_move,
                 acquired_date, sale_date, days_held,
                 keep['id']),
            )

            for oid in [s['id'] for s in others]:
                cursor.execute(
                    "UPDATE trade_journal SET strategy_id=? WHERE strategy_id=?",
                    (keep['id'], oid),
                )
                # Repoint any strategy that referenced the row being deleted
                # as its parent (e.g. a REPAIR/ROLL row's parent_strategy_id)
                # so campaign chains don't dangle.
                cursor.execute(
                    "UPDATE strategies SET parent_strategy_id=? WHERE parent_strategy_id=?",
                    (keep['id'], oid),
                )
                cursor.execute("DELETE FROM strategies WHERE id=?", (oid,))
                total_deleted += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return total_deleted


def create_strategy_and_link(
    conn: sqlite3.Connection,
    legs: List[LegRecord],
    strategy_type: str,
    account_type: str = 'TAXABLE',
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
         quantity, gain_loss, is_winner, earnings_date, actual_move, account_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        account_type,
    ))

    strategy_id = cursor.lastrowid

    # Link all legs (including duplicate rows that share the same position)
    # For stock legs: also link duplicate rows that were excluded from canonical dedup
    # so they don't remain as orphans
    for leg in legs:
        cursor.execute(
            "UPDATE trade_journal SET strategy_id = ? WHERE id = ?",
            (strategy_id, leg.id)
        )

    return strategy_id


def link_stock_duplicates(conn: sqlite3.Connection, legs: List[LegRecord], strategy_id: int) -> int:
    """
    Link duplicate stock rows (import artifacts) to the same strategy.

    Finds all rows matching the canonical legs by (symbol, acquired_date, sale_date,
    quantity, cost_basis, proceeds) and links them, so they don't stay as orphans.
    Returns count of additional rows linked.
    """
    cursor = conn.cursor()
    linked = 0
    for leg in legs:
        cursor.execute("""
            UPDATE trade_journal
            SET strategy_id = ?
            WHERE strategy_id IS NULL
              AND option_type IS NULL
              AND symbol = ?
              AND COALESCE(acquired_date, '') = COALESCE(?, '')
              AND sale_date = ?
              AND quantity = ?
              AND ROUND(cost_basis, 2) = ROUND(?, 2)
              AND ROUND(proceeds, 2) = ROUND(?, 2)
        """, (
            strategy_id,
            leg.symbol,
            leg.acquired_date,
            leg.sale_date,
            leg.quantity,
            leg.cost_basis,
            leg.proceeds,
        ))
        linked += cursor.rowcount
    return linked


def run_regrouping(db_path: str, dry_run: bool = False, account_type: str = 'TAXABLE') -> Dict[str, Any]:
    """
    Run the regrouping process for option legs.

    Returns stats on strategies created.
    """
    legs = load_orphan_legs(db_path, account_type)

    if not legs:
        return {
            'orphan_legs_found': 0,
            'strategies_created': 0,
            'legs_linked': 0,
            'by_type': {},
            'details': [],
        }

    print(f"Found {len(legs)} orphan option legs ({account_type})")

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
            # Consolidate all fills at the same (symbol, expiration, option_type)
            # into one strategy — avoids fragmenting multi-fill spreads into N pairs.
            matched_groups = [group_legs]

            for matched_legs in matched_groups:
                leg_count = len(matched_legs)
                combined_pnl = sum(leg.gain_loss for leg in matched_legs)

                # Determine strategy type from strike structure
                distinct_strikes = len(set(leg.strike for leg in matched_legs if leg.strike))
                final_type = _classify_by_strike_count(distinct_strikes)

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
                    strategy_id = create_strategy_and_link(conn, matched_legs, final_type, account_type)
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


def run_stock_grouping(db_path: str, dry_run: bool = False, account_type: str = 'TAXABLE') -> Dict[str, Any]:
    """
    Group stock orphan legs into STOCK strategies.

    Deduplicates import artifacts and groups by (symbol, sale_date).
    """
    legs = load_stock_orphans(db_path, account_type)

    if not legs:
        return {
            'orphan_legs_found': 0,
            'strategies_created': 0,
            'legs_linked': 0,
            'duplicates_linked': 0,
            'by_type': {},
            'details': [],
        }

    print(f"Found {len(legs)} unique orphan stock legs ({account_type})")

    groups = group_stock_legs(legs)
    print(f"Grouped into {len(groups)} stock close events")

    stats = {
        'orphan_legs_found': len(legs),
        'strategies_created': 0,
        'legs_linked': 0,
        'duplicates_linked': 0,
        'by_type': defaultdict(int),
        'details': [],
    }

    if dry_run:
        conn = None
    else:
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA foreign_keys=ON')

    try:
        for (symbol, sale_date), group_legs in groups.items():
            combined_pnl = sum(leg.gain_loss for leg in group_legs)
            leg_count = len(group_legs)

            detail = {
                'symbol': symbol,
                'sale_date': sale_date,
                'leg_count': leg_count,
                'leg_ids': [leg.id for leg in group_legs],
                'strategy_type': 'STOCK',
                'combined_pnl': round(combined_pnl, 2),
            }
            stats['details'].append(detail)

            if dry_run:
                stats['strategies_created'] += 1
                stats['legs_linked'] += leg_count
                stats['by_type']['STOCK'] += 1
            else:
                strategy_id = create_strategy_and_link(conn, group_legs, 'STOCK', account_type)
                dups = link_stock_duplicates(conn, group_legs, strategy_id)
                stats['strategies_created'] += 1
                stats['legs_linked'] += leg_count
                stats['duplicates_linked'] += dups
                stats['by_type']['STOCK'] += 1

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


def fix_mistagged_strategies(db_path: str, dry_run: bool = False) -> int:
    """
    Fix strategies that were tagged TAXABLE but have only IRA trade_journal legs.

    Returns count of strategies fixed.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find strategies where ALL linked legs are IRA but strategy is TAXABLE
    cursor.execute("""
        SELECT s.id, s.symbol, s.sale_date, s.gain_loss
        FROM strategies s
        WHERE s.account_type = 'TAXABLE'
          AND s.id IN (
              SELECT strategy_id FROM trade_journal
              WHERE strategy_id IS NOT NULL
              GROUP BY strategy_id
              HAVING COUNT(*) = SUM(CASE WHEN account_type = 'IRA' THEN 1 ELSE 0 END)
          )
    """)

    rows = cursor.fetchall()
    count = len(rows)

    if count == 0:
        conn.close()
        return 0

    print(f"Found {count} mistagged TAXABLE strategies with all-IRA legs:")
    for row in rows:
        print(f"  id={row['id']} {row['symbol']} {row['sale_date']} ${row['gain_loss']:.2f}")

    if not dry_run:
        ids = [row['id'] for row in rows]
        placeholders = ','.join('?' * len(ids))
        cursor.execute(
            f"UPDATE strategies SET account_type='IRA' WHERE id IN ({placeholders})",
            ids
        )
        conn.commit()
        print(f"  -> Fixed {count} strategies to account_type='IRA'")

    conn.close()
    return count


def print_report(stats: Dict[str, Any], dry_run: bool, label: str = ""):
    """Print formatted report."""
    prefix = "[DRY RUN] " if dry_run else ""
    header = f" {label}" if label else ""

    print(f"\n{prefix}Regrouping Results{header}:")
    print(f"  Orphan legs found: {stats['orphan_legs_found']}")
    print(f"  Strategies created: {stats['strategies_created']}")
    print(f"  Legs linked: {stats['legs_linked']}")
    if stats.get('duplicates_linked'):
        print(f"  Duplicate rows linked: {stats['duplicates_linked']}")

    print(f"\n{prefix}By Strategy Type:")
    for stype, count in sorted(stats['by_type'].items()):
        print(f"  {stype}: {count}")

    # Show some examples
    print(f"\n{prefix}Sample Groupings (first 10):")
    for detail in stats['details'][:10]:
        pnl = detail['combined_pnl']
        winner = "WIN" if pnl > 0 else "LOSS"
        symbol = detail['symbol']
        stype = detail['strategy_type']
        legs = detail['leg_count']
        if 'expiration' in detail:
            print(f"  {symbol} {detail.get('option_type','')} exp:{detail['expiration']} "
                  f"- {stype} ({legs} legs) ${pnl:,.2f} [{winner}]")
        else:
            print(f"  {symbol} {detail.get('sale_date','')} "
                  f"- {stype} ({legs} legs) ${pnl:,.2f} [{winner}]")


def main():
    import argparse

    project_root = Path(__file__).parent.parent
    default_db = project_root / "2.0" / "data" / "ivcrush.db"

    parser = argparse.ArgumentParser(description='Regroup orphan legs into strategies')
    parser.add_argument('--db', default=str(default_db), help='Database path')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    parser.add_argument('--account-type', default='TAXABLE', choices=['TAXABLE', 'IRA'],
                        help='Account type tag for created strategies (default: TAXABLE)')
    parser.add_argument('--fix-mistagged', action='store_true',
                        help='Fix strategies tagged TAXABLE but with all-IRA legs')

    args = parser.parse_args()

    print(f"Database: {args.db}")
    print(f"Account type: {args.account_type}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")

    # Step 1: Fix any mistagged strategies first
    if args.fix_mistagged or True:  # always run this check
        print("\n--- Checking for mistagged strategies ---")
        fixed = fix_mistagged_strategies(args.db, dry_run=args.dry_run)
        if fixed == 0:
            print("  No mistagged strategies found.")

    # Step 2a: Consolidate duplicate STOCK strategies (same symbol+sale_date created across sessions)
    print(f"\n--- Consolidating Duplicate Stock Strategies (all accounts) ---")
    stock_deleted = consolidate_duplicate_stocks(args.db, dry_run=args.dry_run)
    if stock_deleted == 0:
        print("  No duplicate stock strategies found.")
    else:
        print(f"  Merged {stock_deleted} duplicate stock strategies.")

    # Step 2b: Consolidate any fragmented spread strategies from previous imports
    print(f"\n--- Consolidating Fragmented Spreads ({args.account_type}) ---")
    deleted = consolidate_fragmented_spreads(args.db, dry_run=args.dry_run, account_type=args.account_type)
    if deleted == 0:
        print("  No fragmented spreads found.")
    else:
        print(f"  Merged {deleted} duplicate spread strategies.")

    # Step 3: Regroup option legs
    print(f"\n--- Option Legs ({args.account_type}) ---")
    option_stats = run_regrouping(args.db, dry_run=args.dry_run, account_type=args.account_type)
    print_report(option_stats, args.dry_run, f"Options ({args.account_type})")

    # Step 3b: Re-run fragment consolidation now that new strategies exist —
    # catches a position whose legs arrived across two separate import
    # sessions (Step 3 always creates a fresh strategy for newly-orphaned
    # legs; it never checks for a pre-existing strategy of the same position).
    print(f"\n--- Re-checking for Cross-Session Fragments ({args.account_type}) ---")
    post_deleted = consolidate_fragmented_spreads(args.db, dry_run=args.dry_run, account_type=args.account_type)
    if post_deleted == 0:
        print("  No cross-session fragments found.")
    else:
        print(f"  Merged {post_deleted} cross-session duplicate strategies.")

    # Step 4: Regroup stock legs
    print(f"\n--- Stock Legs ({args.account_type}) ---")
    stock_stats = run_stock_grouping(args.db, dry_run=args.dry_run, account_type=args.account_type)
    print_report(stock_stats, args.dry_run, f"Stock ({args.account_type})")

    # Summary
    total_created = option_stats['strategies_created'] + stock_stats['strategies_created']
    total_linked = option_stats['legs_linked'] + stock_stats['legs_linked']
    print(f"\nTotal strategies created: {total_created}")
    print(f"Total legs linked: {total_linked}")


if __name__ == "__main__":
    main()
