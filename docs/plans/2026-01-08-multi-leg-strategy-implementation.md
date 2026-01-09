# Multi-Leg Strategy Tracking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add multi-leg strategy tracking to accurately report win rates and enable strategy-type performance analysis.

**Architecture:** New `strategies` table linked to `trade_journal` via foreign key. Auto-detection with manual override. Mandatory backfill of 524 existing trades.

**Tech Stack:** SQLite, Python 3.11, pytest

---

## Task 1: Create Strategies Table

**Files:**
- Modify: `2.0/data/ivcrush.db` (via SQL)
- Create: `scripts/migrations/001_add_strategies.py`

**Step 1: Write migration script**

Create `scripts/migrations/001_add_strategies.py`:

```python
#!/usr/bin/env python3
"""Migration: Add strategies table for multi-leg tracking."""

import sqlite3
import sys
from pathlib import Path

def migrate(db_path: str, dry_run: bool = False) -> bool:
    """Create strategies table and add strategy_id to trade_journal."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if already migrated
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'")
    if cursor.fetchone():
        print("Migration already applied: strategies table exists")
        conn.close()
        return True

    if dry_run:
        print("DRY RUN - Would create strategies table and add strategy_id column")
        conn.close()
        return True

    try:
        # Create strategies table
        cursor.execute("""
            CREATE TABLE strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy_type TEXT NOT NULL CHECK(strategy_type IN ('SINGLE', 'SPREAD', 'IRON_CONDOR')),
                acquired_date DATE NOT NULL,
                sale_date DATE NOT NULL,
                days_held INTEGER,
                expiration DATE,
                quantity INTEGER,
                net_credit REAL,
                net_debit REAL,
                gain_loss REAL NOT NULL,
                is_winner BOOLEAN NOT NULL,
                earnings_date DATE,
                actual_move REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add strategy_id to trade_journal
        cursor.execute("""
            ALTER TABLE trade_journal ADD COLUMN strategy_id INTEGER REFERENCES strategies(id)
        """)

        # Create index for faster lookups
        cursor.execute("CREATE INDEX idx_trade_journal_strategy_id ON trade_journal(strategy_id)")
        cursor.execute("CREATE INDEX idx_strategies_symbol ON strategies(symbol)")
        cursor.execute("CREATE INDEX idx_strategies_sale_date ON strategies(sale_date)")

        conn.commit()
        print("Migration successful: created strategies table and added strategy_id column")
        return True

    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "2.0" / "data" / "ivcrush.db"

    dry_run = "--dry-run" in sys.argv
    success = migrate(str(db_path), dry_run=dry_run)
    sys.exit(0 if success else 1)
```

**Step 2: Run migration with dry-run**

```bash
mkdir -p scripts/migrations
python scripts/migrations/001_add_strategies.py --dry-run
```

Expected: "DRY RUN - Would create strategies table..."

**Step 3: Run migration**

```bash
python scripts/migrations/001_add_strategies.py
```

Expected: "Migration successful: created strategies table..."

**Step 4: Verify migration**

```bash
sqlite3 2.0/data/ivcrush.db ".schema strategies"
sqlite3 2.0/data/ivcrush.db "PRAGMA table_info(trade_journal)" | grep strategy_id
```

Expected: Table schema displayed, strategy_id column present

**Step 5: Commit**

```bash
git add scripts/migrations/001_add_strategies.py
git commit -m "feat: add strategies table for multi-leg tracking"
```

---

## Task 2: Create Strategy Grouping Module

**Files:**
- Create: `scripts/strategy_grouper.py`
- Test: `scripts/tests/test_strategy_grouper.py`

**Step 1: Write the failing test**

Create `scripts/tests/__init__.py`:
```python
# Test package for scripts
```

Create `scripts/tests/test_strategy_grouper.py`:

```python
"""Tests for strategy grouping logic."""

import pytest
from dataclasses import dataclass
from typing import Optional

# Import will fail until we create the module
from scripts.strategy_grouper import (
    group_legs_into_strategies,
    classify_strategy_type,
    StrategyGroup,
    Confidence,
)


@dataclass
class MockLeg:
    """Mock trade leg for testing."""
    id: int
    symbol: str
    acquired_date: str
    sale_date: str
    expiration: Optional[str]
    option_type: Optional[str]
    strike: Optional[float]
    gain_loss: float


class TestClassifyStrategyType:
    """Tests for strategy type classification."""

    def test_single_leg_is_single(self):
        assert classify_strategy_type(1) == "SINGLE"

    def test_two_legs_is_spread(self):
        assert classify_strategy_type(2) == "SPREAD"

    def test_four_legs_is_iron_condor(self):
        assert classify_strategy_type(4) == "IRON_CONDOR"

    def test_three_legs_returns_none(self):
        assert classify_strategy_type(3) is None

    def test_five_legs_returns_none(self):
        assert classify_strategy_type(5) is None


class TestGroupLegsIntoStrategies:
    """Tests for grouping logic."""

    def test_single_leg_groups_alone(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0)
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 1
        assert groups[0].strategy_type == "SINGLE"
        assert groups[0].confidence == Confidence.HIGH
        assert len(groups[0].legs) == 1

    def test_two_matching_legs_form_spread(self):
        legs = [
            MockLeg(1, "APLD", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 25.0, 12542.49),
            MockLeg(2, "APLD", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 23.0, -6561.51),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 1
        assert groups[0].strategy_type == "SPREAD"
        assert groups[0].confidence == Confidence.HIGH
        assert len(groups[0].legs) == 2
        assert groups[0].combined_pnl == pytest.approx(5980.98, rel=0.01)

    def test_four_matching_legs_form_iron_condor(self):
        legs = [
            MockLeg(1, "SPY", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 580.0, 100.0),
            MockLeg(2, "SPY", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 575.0, -50.0),
            MockLeg(3, "SPY", "2026-01-07", "2026-01-08", "2026-01-16", "CALL", 600.0, 100.0),
            MockLeg(4, "SPY", "2026-01-07", "2026-01-08", "2026-01-16", "CALL", 605.0, -50.0),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 1
        assert groups[0].strategy_type == "IRON_CONDOR"
        assert groups[0].confidence == Confidence.HIGH
        assert len(groups[0].legs) == 4

    def test_different_symbols_not_grouped(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0),
            MockLeg(2, "MSFT", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 400.0, 300.0),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 2
        assert all(g.strategy_type == "SINGLE" for g in groups)

    def test_different_dates_not_grouped(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0),
            MockLeg(2, "AAPL", "2026-01-08", "2026-01-09", "2026-01-16", "PUT", 145.0, 300.0),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 2

    def test_different_expirations_medium_confidence(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0),
            MockLeg(2, "AAPL", "2026-01-07", "2026-01-08", "2026-01-23", "PUT", 145.0, 300.0),
        ]
        groups = group_legs_into_strategies(legs)

        # Different expirations = don't group (could be calendar spread but we don't support)
        assert len(groups) == 2

    def test_three_legs_flagged_for_review(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0),
            MockLeg(2, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 145.0, -200.0),
            MockLeg(3, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 140.0, 100.0),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 1
        assert groups[0].strategy_type is None  # Unknown
        assert groups[0].confidence == Confidence.LOW
        assert groups[0].needs_review is True
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk
python -m pytest scripts/tests/test_strategy_grouper.py -v
```

Expected: FAIL with "ModuleNotFoundError: No module named 'scripts.strategy_grouper'"

**Step 3: Write minimal implementation**

Create `scripts/strategy_grouper.py`:

```python
#!/usr/bin/env python3
"""
Strategy grouping logic for multi-leg options trades.

Groups individual trade legs into strategies (SINGLE, SPREAD, IRON_CONDOR)
based on matching criteria: symbol, dates, and expiration.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any, Protocol
from collections import defaultdict


class Confidence(Enum):
    """Confidence level for auto-detected groupings."""
    HIGH = "high"      # All criteria match, standard leg count
    MEDIUM = "medium"  # Most criteria match, might need review
    LOW = "low"        # Only some criteria match, needs review


class TradeLeg(Protocol):
    """Protocol for trade leg objects."""
    id: int
    symbol: str
    acquired_date: str
    sale_date: str
    expiration: Optional[str]
    option_type: Optional[str]
    strike: Optional[float]
    gain_loss: float


@dataclass
class StrategyGroup:
    """A group of legs that form a single strategy."""
    legs: List[Any]
    strategy_type: Optional[str]  # SINGLE, SPREAD, IRON_CONDOR, or None if unknown
    confidence: Confidence
    needs_review: bool = False

    @property
    def combined_pnl(self) -> float:
        """Sum of all leg P&Ls."""
        return sum(leg.gain_loss for leg in self.legs)

    @property
    def is_winner(self) -> bool:
        """Strategy is a winner if combined P&L is positive."""
        return self.combined_pnl > 0

    @property
    def symbol(self) -> str:
        """Symbol from first leg."""
        return self.legs[0].symbol if self.legs else ""

    @property
    def acquired_date(self) -> str:
        """Acquired date from first leg."""
        return self.legs[0].acquired_date if self.legs else ""

    @property
    def sale_date(self) -> str:
        """Sale date from first leg."""
        return self.legs[0].sale_date if self.legs else ""

    @property
    def expiration(self) -> Optional[str]:
        """Expiration from first leg."""
        return self.legs[0].expiration if self.legs else None


def classify_strategy_type(leg_count: int) -> Optional[str]:
    """
    Classify strategy type based on number of legs.

    Args:
        leg_count: Number of legs in the strategy

    Returns:
        Strategy type string or None if unknown
    """
    if leg_count == 1:
        return "SINGLE"
    elif leg_count == 2:
        return "SPREAD"
    elif leg_count == 4:
        return "IRON_CONDOR"
    else:
        return None  # 3, 5+ legs need manual review


def _make_grouping_key(leg: Any) -> tuple:
    """Create grouping key from leg attributes."""
    return (
        leg.symbol,
        leg.acquired_date,
        leg.sale_date,
        leg.expiration,
    )


def group_legs_into_strategies(legs: List[Any]) -> List[StrategyGroup]:
    """
    Group trade legs into strategies based on matching criteria.

    Grouping criteria (ALL must match for HIGH confidence):
    - Same symbol
    - Same acquired_date
    - Same sale_date
    - Same expiration

    Args:
        legs: List of trade leg objects with required attributes

    Returns:
        List of StrategyGroup objects
    """
    if not legs:
        return []

    # Group by key
    groups_by_key: Dict[tuple, List[Any]] = defaultdict(list)
    for leg in legs:
        key = _make_grouping_key(leg)
        groups_by_key[key].append(leg)

    # Convert to StrategyGroup objects
    result = []
    for key, group_legs in groups_by_key.items():
        leg_count = len(group_legs)
        strategy_type = classify_strategy_type(leg_count)

        if strategy_type is not None:
            # Known strategy type
            confidence = Confidence.HIGH
            needs_review = False
        else:
            # Unknown leg count (3, 5+)
            confidence = Confidence.LOW
            needs_review = True

        result.append(StrategyGroup(
            legs=group_legs,
            strategy_type=strategy_type,
            confidence=confidence,
            needs_review=needs_review,
        ))

    return result
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest scripts/tests/test_strategy_grouper.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add scripts/strategy_grouper.py scripts/tests/
git commit -m "feat: add strategy grouping module with auto-detection"
```

---

## Task 3: Create Backfill Script

**Files:**
- Create: `scripts/backfill_strategies.py`
- Test: `scripts/tests/test_backfill_strategies.py`

**Step 1: Write the failing test**

Add to `scripts/tests/test_backfill_strategies.py`:

```python
"""Tests for strategy backfill script."""

import pytest
import sqlite3
import tempfile
import os

from scripts.backfill_strategies import (
    load_unlinked_legs,
    create_strategy,
    link_legs_to_strategy,
    run_backfill,
)


@pytest.fixture
def test_db():
    """Create a temporary test database with schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    # Create tables
    cursor.execute("""
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            acquired_date DATE NOT NULL,
            sale_date DATE NOT NULL,
            days_held INTEGER,
            expiration DATE,
            quantity INTEGER,
            net_credit REAL,
            net_debit REAL,
            gain_loss REAL NOT NULL,
            is_winner BOOLEAN NOT NULL,
            earnings_date DATE,
            actual_move REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE trade_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            acquired_date DATE,
            sale_date DATE NOT NULL,
            days_held INTEGER,
            option_type TEXT,
            strike REAL,
            expiration DATE,
            quantity INTEGER,
            cost_basis REAL NOT NULL,
            proceeds REAL NOT NULL,
            gain_loss REAL NOT NULL,
            is_winner BOOLEAN NOT NULL,
            term TEXT,
            wash_sale_amount REAL DEFAULT 0,
            earnings_date DATE,
            actual_move REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            strategy_id INTEGER REFERENCES strategies(id)
        )
    """)

    conn.commit()
    conn.close()

    yield path

    os.close(fd)
    os.unlink(path)


class TestLoadUnlinkedLegs:
    def test_loads_legs_without_strategy_id(self, test_db):
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, sale_date, cost_basis, proceeds, gain_loss, is_winner)
            VALUES ('AAPL', '2026-01-08', 100, 150, 50, 1)
        """)
        conn.commit()
        conn.close()

        legs = load_unlinked_legs(test_db)
        assert len(legs) == 1
        assert legs[0]['symbol'] == 'AAPL'

    def test_excludes_linked_legs(self, test_db):
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()

        # Create a strategy
        cursor.execute("""
            INSERT INTO strategies
            (symbol, strategy_type, acquired_date, sale_date, gain_loss, is_winner)
            VALUES ('AAPL', 'SINGLE', '2026-01-07', '2026-01-08', 50, 1)
        """)
        strategy_id = cursor.lastrowid

        # Linked leg
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, sale_date, cost_basis, proceeds, gain_loss, is_winner, strategy_id)
            VALUES ('AAPL', '2026-01-08', 100, 150, 50, 1, ?)
        """, (strategy_id,))

        # Unlinked leg
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, sale_date, cost_basis, proceeds, gain_loss, is_winner)
            VALUES ('MSFT', '2026-01-08', 200, 250, 50, 1)
        """)

        conn.commit()
        conn.close()

        legs = load_unlinked_legs(test_db)
        assert len(legs) == 1
        assert legs[0]['symbol'] == 'MSFT'


class TestCreateStrategy:
    def test_creates_strategy_record(self, test_db):
        strategy_id = create_strategy(
            test_db,
            symbol="APLD",
            strategy_type="SPREAD",
            acquired_date="2026-01-07",
            sale_date="2026-01-08",
            days_held=1,
            expiration="2026-01-16",
            quantity=200,
            gain_loss=5980.98,
            is_winner=True,
        )

        assert strategy_id is not None

        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
        row = cursor.fetchone()
        conn.close()

        assert row is not None


class TestRunBackfill:
    def test_groups_spread_legs(self, test_db):
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()

        # Insert APLD spread legs
        cursor.execute("""
            INSERT INTO trade_journal
            (symbol, acquired_date, sale_date, days_held, option_type, strike, expiration,
             quantity, cost_basis, proceeds, gain_loss, is_winner, term)
            VALUES
            ('APLD', '2026-01-07', '2026-01-08', 1, 'PUT', 25.0, '2026-01-16',
             200, 2452.75, 14995.24, 12542.49, 1, 'SHORT'),
            ('APLD', '2026-01-07', '2026-01-08', 1, 'PUT', 23.0, '2026-01-16',
             200, 7804.76, 1243.25, -6561.51, 0, 'SHORT')
        """)
        conn.commit()
        conn.close()

        stats = run_backfill(test_db, dry_run=False)

        assert stats['strategies_created'] == 1
        assert stats['legs_linked'] == 2

        # Verify strategy
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT strategy_type, gain_loss, is_winner FROM strategies")
        row = cursor.fetchone()
        conn.close()

        assert row[0] == 'SPREAD'
        assert abs(row[1] - 5980.98) < 0.01
        assert row[2] == 1  # Winner
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest scripts/tests/test_backfill_strategies.py -v
```

Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

Create `scripts/backfill_strategies.py`:

```python
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
    conn = sqlite3.connect(db_path)
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
    """Create a strategy record and return its ID."""
    conn = sqlite3.connect(db_path)
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


def link_legs_to_strategy(db_path: str, leg_ids: List[int], strategy_id: int):
    """Link trade_journal legs to a strategy."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.executemany(
        "UPDATE trade_journal SET strategy_id = ? WHERE id = ?",
        [(strategy_id, leg_id) for leg_id in leg_ids]
    )

    conn.commit()
    conn.close()


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

        # Create strategy
        strategy_id = create_strategy(
            db_path,
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

        # Link legs
        leg_ids = [leg.id for leg in group.legs]
        link_legs_to_strategy(db_path, leg_ids, strategy_id)

        strategies_created += 1
        legs_linked += len(group.legs)

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
    default_db = project_root / "2.0" / "data" / "ivcrush.db"

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
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest scripts/tests/test_backfill_strategies.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add scripts/backfill_strategies.py scripts/tests/test_backfill_strategies.py
git commit -m "feat: add backfill script for existing trades"
```

---

## Task 4: Fix Current APLD Trade Data

**Files:**
- Modify: `2.0/data/ivcrush.db` (via backfill)

The current APLD trade is stored as a single combined row. We need to:
1. Delete the combined row
2. Insert the individual legs
3. Run backfill to create proper strategy

**Step 1: Delete combined APLD trade**

```bash
sqlite3 2.0/data/ivcrush.db "DELETE FROM trade_journal WHERE symbol='APLD' AND sale_date='2026-01-08'"
```

**Step 2: Insert individual legs**

```bash
sqlite3 2.0/data/ivcrush.db "
INSERT INTO trade_journal
(symbol, acquired_date, sale_date, days_held, option_type, strike, expiration,
 quantity, cost_basis, proceeds, gain_loss, is_winner, term)
VALUES
('APLD', '2026-01-07', '2026-01-08', 1, 'PUT', 25.0, '2026-01-16',
 200, 2452.75, 14995.24, 12542.49, 1, 'SHORT'),
('APLD', '2026-01-07', '2026-01-08', 1, 'PUT', 23.0, '2026-01-16',
 200, 7804.76, 1243.25, -6561.51, 0, 'SHORT')
"
```

**Step 3: Verify legs inserted**

```bash
sqlite3 2.0/data/ivcrush.db "SELECT id, symbol, strike, gain_loss FROM trade_journal WHERE symbol='APLD' AND sale_date='2026-01-08'"
```

Expected: Two rows with strikes 25.0 and 23.0

**Step 4: Run backfill dry-run**

```bash
python scripts/backfill_strategies.py --dry-run
```

Expected: Shows strategy creation preview

**Step 5: Commit data fix**

```bash
git add -A
git commit -m "fix: restore APLD individual legs for proper strategy tracking"
```

---

## Task 5: Run Full Backfill

**Files:**
- Modify: `2.0/data/ivcrush.db`

**Step 1: Run migration if not done**

```bash
python scripts/migrations/001_add_strategies.py
```

**Step 2: Run backfill dry-run**

```bash
python scripts/backfill_strategies.py --dry-run
```

Review output for any issues.

**Step 3: Run actual backfill**

```bash
python scripts/backfill_strategies.py
```

**Step 4: Verify results**

```bash
sqlite3 2.0/data/ivcrush.db "
SELECT strategy_type, COUNT(*) as count,
       ROUND(SUM(gain_loss), 2) as total_pnl,
       ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate
FROM strategies
GROUP BY strategy_type
"
```

**Step 5: Verify no orphan legs**

```bash
sqlite3 2.0/data/ivcrush.db "SELECT COUNT(*) FROM trade_journal WHERE strategy_id IS NULL"
```

Expected: 0 (all legs linked) or list of items needing manual review

**Step 6: Commit**

```bash
git add -A
git commit -m "feat: complete backfill of 524 trades into strategies"
```

---

## Task 6: Update Stats Queries

**Files:**
- Create: `scripts/journal_stats.py`

**Step 1: Create stats script**

```python
#!/usr/bin/env python3
"""Query strategy-level statistics from the database."""

import sqlite3
import sys
from pathlib import Path


def print_strategy_stats(db_path: str):
    """Print strategy-level performance statistics."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("\n" + "=" * 70)
    print("STRATEGY PERFORMANCE (Accurate Win Rates)")
    print("=" * 70)

    # By strategy type
    cursor.execute("""
        SELECT strategy_type,
               COUNT(*) as trades,
               SUM(CASE WHEN is_winner THEN 1 ELSE 0 END) as winners,
               ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
               ROUND(SUM(gain_loss), 2) as total_pnl
        FROM strategies
        GROUP BY strategy_type
        ORDER BY total_pnl DESC
    """)

    print("\nBy Strategy Type:")
    print(f"{'Type':<15} {'Trades':>8} {'Winners':>8} {'Win Rate':>10} {'P&L':>15}")
    print("-" * 60)

    for row in cursor.fetchall():
        strategy_type, trades, winners, win_rate, total_pnl = row
        print(f"{strategy_type:<15} {trades:>8} {winners:>8} {win_rate:>9.1f}% ${total_pnl:>14,.2f}")

    # Monthly
    cursor.execute("""
        SELECT strftime('%Y-%m', sale_date) as month,
               COUNT(*) as trades,
               ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
               ROUND(SUM(gain_loss), 2) as pnl
        FROM strategies
        GROUP BY month
        ORDER BY month DESC
        LIMIT 12
    """)

    print("\nMonthly Performance (Last 12 Months):")
    print(f"{'Month':<10} {'Trades':>8} {'Win Rate':>10} {'P&L':>15}")
    print("-" * 50)

    for row in cursor.fetchall():
        month, trades, win_rate, pnl = row
        print(f"{month:<10} {trades:>8} {win_rate:>9.1f}% ${pnl:>14,.2f}")

    # Overall
    cursor.execute("""
        SELECT COUNT(*) as trades,
               ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
               ROUND(SUM(gain_loss), 2) as total_pnl
        FROM strategies
    """)

    row = cursor.fetchone()
    print(f"\nOverall: {row[0]} trades, {row[1]}% win rate, ${row[2]:,.2f} P&L")

    conn.close()


if __name__ == "__main__":
    project_root = Path(__file__).parent.parent
    db_path = project_root / "2.0" / "data" / "ivcrush.db"
    print_strategy_stats(str(db_path))
```

**Step 2: Run stats**

```bash
python scripts/journal_stats.py
```

**Step 3: Commit**

```bash
git add scripts/journal_stats.py
git commit -m "feat: add strategy-level stats reporting"
```

---

## Task 7: Add Review Commands (Optional Enhancement)

**Files:**
- Create: `scripts/journal_review.py`

This task adds the interactive review/override commands. Implementation deferred - can be added when needed.

---

## Summary

After completing tasks 1-6:

1. ✅ `strategies` table created with proper schema
2. ✅ `trade_journal.strategy_id` foreign key added
3. ✅ Strategy grouping module with auto-detection
4. ✅ Backfill script for existing 524 trades
5. ✅ APLD trade fixed with proper legs
6. ✅ Stats queries for accurate win rates

**Verification:**
- All trades linked to strategies (no orphans)
- Win rates calculated at strategy level
- Monthly and strategy-type breakdowns available
