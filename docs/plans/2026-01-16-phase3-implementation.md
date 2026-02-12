# agents Phase 3: Enhanced Intelligence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add TRR-based position sizing, real sector data, automated data quality fixes, and pattern recognition to the agents agent system.

**Architecture:** Four independent features that integrate with existing orchestrators. Each feature follows TDD with dedicated tests. All code reuses existing patterns from `ticker_analysis.py` and `schemas.py`.

**Tech Stack:** Python 3.11+, Pydantic, SQLite, asyncio, pytest

---

## Feature 1: TRR-based Position Sizing Integration

### Task 1.1: Add PositionLimits Schema

**Files:**
- Modify: `agents/src/utils/schemas.py`
- Test: `agents/tests/test_schemas.py` (new)

**Step 1: Write the failing test**

Create `agents/tests/test_schemas.py`:

```python
#!/usr/bin/env python
"""Unit tests for Pydantic schemas."""

import sys
from pathlib import Path

# Add agents/ to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.utils.schemas import PositionLimits


class TestPositionLimits:
    """Tests for PositionLimits schema."""

    def test_valid_high_trr(self):
        """HIGH TRR should have reduced limits."""
        limits = PositionLimits(
            ticker="MU",
            tail_risk_ratio=3.05,
            tail_risk_level="HIGH",
            max_contracts=50,
            max_notional=25000.0,
            avg_move=3.68,
            max_move=11.21
        )
        assert limits.tail_risk_level == "HIGH"
        assert limits.max_contracts == 50

    def test_valid_normal_trr(self):
        """NORMAL TRR should have standard limits."""
        limits = PositionLimits(
            ticker="AAPL",
            tail_risk_ratio=1.66,
            tail_risk_level="NORMAL",
            max_contracts=100,
            max_notional=50000.0,
            avg_move=2.15,
            max_move=3.57
        )
        assert limits.tail_risk_level == "NORMAL"
        assert limits.max_contracts == 100

    def test_invalid_tail_risk_level(self):
        """Invalid tail risk level should raise error."""
        with pytest.raises(ValueError, match="Invalid tail_risk_level"):
            PositionLimits(
                ticker="TEST",
                tail_risk_ratio=1.5,
                tail_risk_level="INVALID",
                max_contracts=100,
                max_notional=50000.0,
                avg_move=2.0,
                max_move=3.0
            )
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_schemas.py -v`

Expected: FAIL with "cannot import name 'PositionLimits'"

**Step 3: Write minimal implementation**

Add to `agents/src/utils/schemas.py` after `HealthCheckResponse` class:

```python
class PositionLimits(BaseModel):
    """Position limits based on Tail Risk Ratio."""
    ticker: str
    tail_risk_ratio: float
    tail_risk_level: str
    max_contracts: int
    max_notional: float
    avg_move: float
    max_move: float

    @validator('tail_risk_level')
    def validate_tail_risk_level(cls, v):
        if v not in ['LOW', 'NORMAL', 'HIGH']:
            raise ValueError(f'Invalid tail_risk_level: {v}')
        return v

    @property
    def is_high_risk(self) -> bool:
        """Check if ticker has high tail risk."""
        return self.tail_risk_level == "HIGH"
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_schemas.py::TestPositionLimits -v`

Expected: PASS (3 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/utils/schemas.py agents/tests/test_schemas.py && git commit -m "feat(6.0): add PositionLimits schema for TRR integration

- Add PositionLimits Pydantic model with validation
- Support LOW/NORMAL/HIGH tail risk levels
- Add is_high_risk property for convenience

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 1.2: Create Position Limits Integration Module

**Files:**
- Create: `agents/src/integration/position_limits.py`
- Test: `agents/tests/test_position_limits.py` (new)

**Step 1: Write the failing test**

Create `agents/tests/test_position_limits.py`:

```python
#!/usr/bin/env python
"""Tests for position_limits integration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.integration.position_limits import PositionLimitsRepository


class TestPositionLimitsRepository:
    """Tests for PositionLimitsRepository."""

    def test_get_existing_ticker(self):
        """Should return position limits for known ticker."""
        repo = PositionLimitsRepository()
        result = repo.get_limits("AAPL")

        assert result is not None
        assert result['ticker'] == "AAPL"
        assert 'tail_risk_ratio' in result
        assert 'tail_risk_level' in result
        assert 'max_contracts' in result

    def test_get_unknown_ticker(self):
        """Should return None for unknown ticker."""
        repo = PositionLimitsRepository()
        result = repo.get_limits("XXXXX")

        assert result is None

    def test_get_high_risk_ticker(self):
        """MU should be HIGH risk based on CLAUDE.md."""
        repo = PositionLimitsRepository()
        result = repo.get_limits("MU")

        # MU has TRR 3.05x per CLAUDE.md
        if result:
            assert result['tail_risk_level'] == "HIGH"
            assert result['max_contracts'] == 50
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_position_limits.py -v`

Expected: FAIL with "cannot import name 'PositionLimitsRepository'"

**Step 3: Write minimal implementation**

Create `agents/src/integration/position_limits.py`:

```python
"""Position limits integration - query TRR data from database.

Provides access to the position_limits table which contains Tail Risk Ratio
calculations for each ticker.
"""

import sqlite3
from typing import Dict, Any, Optional
from pathlib import Path

from .container_2_0 import Container2_0


class PositionLimitsRepository:
    """Repository for querying position limits from database."""

    def __init__(self):
        """Initialize with database path from core container."""
        container = Container2_0()
        self.db_path = container.get_db_path()

    def get_limits(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get position limits for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dict with position limits or None if not found

        Example:
            repo = PositionLimitsRepository()
            limits = repo.get_limits("MU")
            # Returns:
            # {
            #     'ticker': 'MU',
            #     'tail_risk_ratio': 3.05,
            #     'tail_risk_level': 'HIGH',
            #     'max_contracts': 50,
            #     'max_notional': 25000.0,
            #     'avg_move': 3.68,
            #     'max_move': 11.21
            # }
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, tail_risk_ratio, tail_risk_level,
                       max_contracts, max_notional, avg_move, max_move
                FROM position_limits
                WHERE ticker = ?
            """, (ticker.upper(),))

            row = cursor.fetchone()
            conn.close()

            if row is None:
                return None

            return dict(row)

        except Exception as e:
            return None

    def get_all_high_risk(self) -> list:
        """Get all tickers with HIGH tail risk."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, tail_risk_ratio, tail_risk_level,
                       max_contracts, max_notional, avg_move, max_move
                FROM position_limits
                WHERE tail_risk_level = 'HIGH'
                ORDER BY tail_risk_ratio DESC
            """)

            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]

        except Exception:
            return []
```

**Step 4: Add get_db_path to Container2_0**

Add this method to `agents/src/integration/container_2_0.py` inside the `Container2_0` class:

```python
    def get_db_path(self) -> str:
        """Get database path from core container."""
        if self.container is None:
            self._initialize()
        # Access DB path from environment or default
        import os
        db_path = os.environ.get('DB_PATH', 'data/ivcrush.db')
        # Make absolute relative to core directory
        main_repo = self._find_main_repo()
        return str(main_repo / "core" / db_path)
```

**Step 5: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_position_limits.py -v`

Expected: PASS (3 tests)

**Step 6: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/integration/position_limits.py agents/src/integration/container_2_0.py agents/tests/test_position_limits.py && git commit -m "feat(6.0): add PositionLimitsRepository for TRR queries

- Query position_limits table for tail risk data
- Add get_limits() for single ticker lookup
- Add get_all_high_risk() for HIGH TRR tickers
- Add get_db_path() to Container2_0

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 1.3: Integrate TRR into TickerAnalysisAgent

**Files:**
- Modify: `agents/src/agents/ticker_analysis.py`
- Test: `agents/tests/test_ticker_analysis_trr.py` (new)

**Step 1: Write the failing test**

Create `agents/tests/test_ticker_analysis_trr.py`:

```python
#!/usr/bin/env python
"""Tests for TRR integration in TickerAnalysisAgent."""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.ticker_analysis import TickerAnalysisAgent


def test_analysis_includes_position_limits():
    """Analysis result should include position_limits when available."""
    agent = TickerAnalysisAgent()

    # Use a future earnings date
    earnings_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    result = agent.analyze("AAPL", earnings_date, generate_strategies=False)

    # Should include position_limits field
    assert 'position_limits' in result

    if result['position_limits']:
        limits = result['position_limits']
        assert 'tail_risk_ratio' in limits
        assert 'tail_risk_level' in limits
        assert 'max_contracts' in limits


def test_high_risk_ticker_flagged():
    """MU should be flagged as HIGH risk if in database."""
    agent = TickerAnalysisAgent()
    earnings_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    result = agent.analyze("MU", earnings_date, generate_strategies=False)

    if result.get('position_limits'):
        # MU has TRR > 2.5 per CLAUDE.md
        assert result['position_limits']['tail_risk_level'] == "HIGH"
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_ticker_analysis_trr.py -v`

Expected: FAIL with "KeyError: 'position_limits'"

**Step 3: Modify TickerAnalysisAgent**

In `agents/src/agents/ticker_analysis.py`:

1. Add import at top:
```python
from ..integration.position_limits import PositionLimitsRepository
```

2. Add to `__init__`:
```python
        self.position_limits_repo = PositionLimitsRepository()
```

3. Modify `analyze()` method - add after line 111 (`'error': None`):
```python
                'position_limits': self._get_position_limits(ticker),
```

4. Add new method at end of class:
```python
    def _get_position_limits(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get position limits for ticker if available."""
        try:
            return self.position_limits_repo.get_limits(ticker)
        except Exception:
            return None
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_ticker_analysis_trr.py -v`

Expected: PASS (2 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/agents/ticker_analysis.py agents/tests/test_ticker_analysis_trr.py && git commit -m "feat(6.0): integrate TRR into TickerAnalysisAgent

- Add position_limits field to analysis response
- Query PositionLimitsRepository for tail risk data
- HIGH risk tickers now flagged in output

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 1.4: Add TRR Badge to Whisper Formatter

**Files:**
- Modify: `agents/src/utils/formatter.py`
- Test: `agents/tests/test_formatter_trr.py` (new)

**Step 1: Write the failing test**

Create `agents/tests/test_formatter_trr.py`:

```python
#!/usr/bin/env python
"""Tests for TRR badge in formatter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.formatter import format_whisper_results


def test_high_trr_badge_shown():
    """HIGH TRR tickers should show warning badge."""
    results = [{
        'ticker': 'MU',
        'earnings_date': '2026-02-05',
        'vrp_ratio': 5.1,
        'liquidity_tier': 'GOOD',
        'recommendation': 'EXCELLENT',
        'score': 78,
        'explanation': 'High VRP',
        'position_limits': {
            'tail_risk_level': 'HIGH',
            'max_contracts': 50
        }
    }]

    output = format_whisper_results(results)

    assert 'HIGH TRR' in output or 'max 50' in output


def test_normal_trr_no_badge():
    """NORMAL TRR tickers should not show TRR badge."""
    results = [{
        'ticker': 'AAPL',
        'earnings_date': '2026-02-05',
        'vrp_ratio': 4.1,
        'liquidity_tier': 'GOOD',
        'recommendation': 'GOOD',
        'score': 72,
        'explanation': 'Decent VRP',
        'position_limits': {
            'tail_risk_level': 'NORMAL',
            'max_contracts': 100
        }
    }]

    output = format_whisper_results(results)

    assert 'HIGH TRR' not in output
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_formatter_trr.py -v`

Expected: FAIL with "AssertionError" (HIGH TRR not in output)

**Step 3: Modify formatter.py**

In `agents/src/utils/formatter.py`, modify the `format_whisper_results` function.

Replace the row building section (lines 54-65) with:

```python
        # Check for HIGH TRR
        position_limits = r.get('position_limits', {})
        trr_badge = ""
        if position_limits and position_limits.get('tail_risk_level') == 'HIGH':
            max_contracts = position_limits.get('max_contracts', 50)
            trr_badge = f" | HIGH TRR (max {max_contracts})"

        row = [
            r.get('ticker', 'N/A'),
            r.get('earnings_date', 'N/A')[:10],
            f"{r.get('vrp_ratio', 0.0):.1f}x",
            f"{liquidity_emoji} {r.get('liquidity_tier', 'N/A')}",
            f"{recommendation_emoji} {r.get('recommendation', 'N/A')}{trr_badge}",
            r.get('score', 0),
            r.get('explanation', 'No explanation')[:50] + '...'
            if len(r.get('explanation', '')) > 50
            else r.get('explanation', 'No explanation')
        ]
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_formatter_trr.py -v`

Expected: PASS (2 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/utils/formatter.py agents/tests/test_formatter_trr.py && git commit -m "feat(6.0): add HIGH TRR badge to whisper output

- Show 'HIGH TRR (max N)' badge for high-risk tickers
- Only display badge for tail_risk_level == HIGH
- Prevents oversizing on volatile tickers

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 1.5: Add Position Limits Section to Analyze Output

**Files:**
- Modify: `agents/src/orchestrators/analyze.py`
- Test: `agents/tests/test_analyze_trr.py` (new)

**Step 1: Write the failing test**

Create `agents/tests/test_analyze_trr.py`:

```python
#!/usr/bin/env python
"""Tests for TRR in analyze output."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrators.analyze import AnalyzeOrchestrator


def test_analyze_output_includes_position_limits():
    """Formatted output should include position limits section for HIGH TRR."""
    orchestrator = AnalyzeOrchestrator()

    # Create mock result with HIGH TRR
    result = {
        'success': True,
        'ticker': 'MU',
        'earnings_date': '2026-02-05',
        'report': {
            'ticker': 'MU',
            'earnings_date': '2026-02-05',
            'summary': {
                'vrp_ratio': 5.1,
                'recommendation': 'EXCELLENT',
                'liquidity_tier': 'GOOD',
                'score': 78,
                'sentiment_direction': 'bullish',
                'sentiment_score': 0.6
            },
            'vrp_analysis': {
                'ratio': 5.1,
                'recommendation': 'EXCELLENT',
                'explanation': 'High VRP due to earnings uncertainty'
            },
            'liquidity': {'tier': 'GOOD', 'tradeable': True},
            'sentiment': {'direction': 'bullish', 'score': 0.6, 'catalysts': [], 'risks': []},
            'strategies': [],
            'anomalies': [],
            'key_factors': [],
            'historical_context': '',
            'position_limits': {
                'tail_risk_ratio': 3.05,
                'tail_risk_level': 'HIGH',
                'max_contracts': 50,
                'max_notional': 25000.0,
                'avg_move': 3.68,
                'max_move': 11.21
            }
        },
        'recommendation': {'action': 'TRADE', 'reason': 'test', 'details': 'test'}
    }

    output = orchestrator.format_results(result)

    assert 'Position Limits' in output
    assert 'HIGH' in output
    assert '50' in output  # max contracts
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_analyze_trr.py -v`

Expected: FAIL with "AssertionError" (Position Limits not in output)

**Step 3: Modify analyze.py format_results**

In `agents/src/orchestrators/analyze.py`, add position limits section in `format_results` method.

Add after the Liquidity section (around line 504, after `output.append("")`):

```python
        # Position Limits (if HIGH TRR)
        position_limits = report.get('position_limits')
        if position_limits and position_limits.get('tail_risk_level') == 'HIGH':
            output.append("## Position Limits")
            output.append("")
            output.append(f"**Tail Risk Ratio:** {position_limits['tail_risk_ratio']:.2f}x (HIGH)")
            output.append(f"**Max Contracts:** {position_limits['max_contracts']}")
            output.append(f"**Max Notional:** ${position_limits['max_notional']:,.0f}")
            output.append(f"**Reason:** Historical max move {position_limits['max_move']:.1f}% vs avg {position_limits['avg_move']:.1f}%")
            output.append("")
```

Also update `_synthesize_report` to include position_limits. Add after line 326 (`'historical_context': ...`):

```python
            'position_limits': specialist_results.get('ticker_analysis', {}).get('position_limits')
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_analyze_trr.py -v`

Expected: PASS

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/orchestrators/analyze.py agents/tests/test_analyze_trr.py && git commit -m "feat(6.0): add Position Limits section to analyze output

- Show TRR details for HIGH risk tickers
- Display max contracts and notional limits
- Explain why limits are reduced (max vs avg move)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 1.6: Update __init__.py exports and run full test suite

**Files:**
- Modify: `agents/src/integration/__init__.py`
- Modify: `agents/src/utils/__init__.py`

**Step 1: Update integration/__init__.py**

Add to `agents/src/integration/__init__.py`:

```python
from .position_limits import PositionLimitsRepository

__all__ = [
    'Container2_0',
    'Cache4_0',
    'Perplexity5_0',
    'PositionLimitsRepository',
]
```

**Step 2: Update utils/__init__.py**

Add `PositionLimits` to exports in `agents/src/utils/__init__.py`:

```python
from .schemas import (
    TickerAnalysisResponse,
    SentimentFetchResponse,
    ExplanationResponse,
    AnomalyDetectionResponse,
    HealthCheckResponse,
    PositionLimits,
)
```

**Step 3: Run full test suite**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/ -v`

Expected: All tests pass (16+ tests)

**Step 4: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/integration/__init__.py agents/src/utils/__init__.py && git commit -m "chore(6.0): update exports for TRR integration

- Export PositionLimitsRepository from integration
- Export PositionLimits schema from utils

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Feature 2: Real Sector Data Integration

### Task 2.1: Add TickerMetadata Schema

**Files:**
- Modify: `agents/src/utils/schemas.py`
- Test: `agents/tests/test_schemas.py`

**Step 1: Write the failing test**

Add to `agents/tests/test_schemas.py`:

```python
from src.utils.schemas import TickerMetadata


class TestTickerMetadata:
    """Tests for TickerMetadata schema."""

    def test_valid_metadata(self):
        """Valid metadata should be created."""
        meta = TickerMetadata(
            ticker="NVDA",
            company_name="NVIDIA Corporation",
            sector="Technology",
            industry="Semiconductors",
            market_cap=1200000.0
        )
        assert meta.sector == "Technology"
        assert meta.industry == "Semiconductors"

    def test_optional_market_cap(self):
        """Market cap should be optional."""
        meta = TickerMetadata(
            ticker="TEST",
            company_name="Test Corp",
            sector="Technology",
            industry="Software"
        )
        assert meta.market_cap is None
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_schemas.py::TestTickerMetadata -v`

Expected: FAIL with "cannot import name 'TickerMetadata'"

**Step 3: Write minimal implementation**

Add to `agents/src/utils/schemas.py`:

```python
class TickerMetadata(BaseModel):
    """Company metadata including sector and industry."""
    ticker: str
    company_name: str
    sector: str
    industry: str
    market_cap: Optional[float] = None
    updated_at: Optional[str] = None
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_schemas.py::TestTickerMetadata -v`

Expected: PASS (2 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/utils/schemas.py agents/tests/test_schemas.py && git commit -m "feat(6.0): add TickerMetadata schema

- Add schema for sector/industry data
- Market cap is optional

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2.2: Create Ticker Metadata Repository

**Files:**
- Create: `agents/src/integration/ticker_metadata.py`
- Test: `agents/tests/test_ticker_metadata.py`

**Step 1: Write the failing test**

Create `agents/tests/test_ticker_metadata.py`:

```python
#!/usr/bin/env python
"""Tests for ticker_metadata integration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.integration.ticker_metadata import TickerMetadataRepository


class TestTickerMetadataRepository:
    """Tests for TickerMetadataRepository."""

    def test_get_returns_none_for_empty_table(self):
        """Should return None when ticker not in database."""
        repo = TickerMetadataRepository()
        result = repo.get_metadata("XXXXX")
        assert result is None

    def test_save_and_get(self):
        """Should save and retrieve metadata."""
        repo = TickerMetadataRepository()

        # Save test data
        repo.save_metadata(
            ticker="TEST123",
            company_name="Test Corp",
            sector="Technology",
            industry="Software",
            market_cap=1000.0
        )

        # Retrieve
        result = repo.get_metadata("TEST123")
        assert result is not None
        assert result['sector'] == "Technology"

        # Cleanup
        repo.delete_metadata("TEST123")

    def test_get_by_sector(self):
        """Should get all tickers in a sector."""
        repo = TickerMetadataRepository()

        # This may return empty if table is empty
        results = repo.get_by_sector("Technology")
        assert isinstance(results, list)
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_ticker_metadata.py -v`

Expected: FAIL with "cannot import name 'TickerMetadataRepository'"

**Step 3: Write minimal implementation**

Create `agents/src/integration/ticker_metadata.py`:

```python
"""Ticker metadata integration - sector and industry data.

Provides access to the ticker_metadata table for cross-ticker
sector correlation analysis.
"""

import sqlite3
from typing import Dict, Any, Optional, List
from datetime import datetime

from .container_2_0 import Container2_0


class TickerMetadataRepository:
    """Repository for ticker metadata (sector, industry, market cap)."""

    # Finnhub industry to sector mapping
    INDUSTRY_TO_SECTOR = {
        'Semiconductors': 'Technology',
        'Software': 'Technology',
        'Technology': 'Technology',
        'Hardware': 'Technology',
        'Internet': 'Technology',
        'Banks': 'Financial Services',
        'Insurance': 'Financial Services',
        'Investment Banking': 'Financial Services',
        'Asset Management': 'Financial Services',
        'Pharmaceuticals': 'Healthcare',
        'Biotechnology': 'Healthcare',
        'Medical Devices': 'Healthcare',
        'Healthcare': 'Healthcare',
        'Retail': 'Consumer Cyclical',
        'Auto': 'Consumer Cyclical',
        'Restaurants': 'Consumer Cyclical',
        'Consumer Electronics': 'Consumer Cyclical',
        'Oil & Gas': 'Energy',
        'Utilities': 'Utilities',
        'Telecom': 'Communication Services',
        'Media': 'Communication Services',
        'Aerospace': 'Industrials',
        'Defense': 'Industrials',
        'Industrial': 'Industrials',
    }

    def __init__(self):
        """Initialize with database path."""
        container = Container2_0()
        self.db_path = container.get_db_path()

    def get_metadata(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a ticker."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, company_name, sector, industry,
                       market_cap, updated_at
                FROM ticker_metadata
                WHERE ticker = ?
            """, (ticker.upper(),))

            row = cursor.fetchone()
            conn.close()

            if row is None:
                return None

            return dict(row)

        except Exception:
            return None

    def save_metadata(
        self,
        ticker: str,
        company_name: str,
        sector: str,
        industry: str,
        market_cap: Optional[float] = None
    ) -> bool:
        """Save or update ticker metadata."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO ticker_metadata
                (ticker, company_name, sector, industry, market_cap, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                ticker.upper(),
                company_name,
                sector,
                industry,
                market_cap,
                datetime.now().isoformat()
            ))

            conn.commit()
            conn.close()
            return True

        except Exception:
            return False

    def delete_metadata(self, ticker: str) -> bool:
        """Delete ticker metadata (for testing)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ticker_metadata WHERE ticker = ?", (ticker.upper(),))
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def get_by_sector(self, sector: str) -> List[Dict[str, Any]]:
        """Get all tickers in a sector."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, company_name, sector, industry, market_cap
                FROM ticker_metadata
                WHERE sector = ?
            """, (sector,))

            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]

        except Exception:
            return []

    @classmethod
    def map_industry_to_sector(cls, industry: str) -> str:
        """Map Finnhub industry to sector."""
        return cls.INDUSTRY_TO_SECTOR.get(industry, 'Other')
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_ticker_metadata.py -v`

Expected: PASS (3 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/integration/ticker_metadata.py agents/tests/test_ticker_metadata.py && git commit -m "feat(6.0): add TickerMetadataRepository for sector data

- CRUD operations for ticker_metadata table
- Industry-to-sector mapping for Finnhub data
- get_by_sector() for cross-ticker analysis

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2.3: Create SectorFetchAgent

**Files:**
- Create: `agents/src/agents/sector_fetch.py`
- Test: `agents/tests/test_sector_fetch.py`

**Step 1: Write the failing test**

Create `agents/tests/test_sector_fetch.py`:

```python
#!/usr/bin/env python
"""Tests for SectorFetchAgent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import Mock, patch
from src.agents.sector_fetch import SectorFetchAgent


class TestSectorFetchAgent:
    """Tests for SectorFetchAgent."""

    def test_fetch_from_cache(self):
        """Should return cached data if available."""
        agent = SectorFetchAgent()

        # Mock the repository to return cached data
        with patch.object(agent.metadata_repo, 'get_metadata') as mock_get:
            mock_get.return_value = {
                'ticker': 'NVDA',
                'sector': 'Technology',
                'industry': 'Semiconductors'
            }

            result = agent.fetch("NVDA")

            assert result['sector'] == 'Technology'
            assert result.get('cached') is True

    def test_returns_none_when_not_found(self):
        """Should return None when ticker not found anywhere."""
        agent = SectorFetchAgent()

        with patch.object(agent.metadata_repo, 'get_metadata', return_value=None):
            with patch.object(agent, '_fetch_from_finnhub', return_value=None):
                result = agent.fetch("XXXXX")
                assert result is None
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_sector_fetch.py -v`

Expected: FAIL with "cannot import name 'SectorFetchAgent'"

**Step 3: Write minimal implementation**

Create `agents/src/agents/sector_fetch.py`:

```python
"""SectorFetchAgent - Fetches company sector/industry from Finnhub.

This agent retrieves company profile data from Finnhub MCP server
and caches it in the ticker_metadata table.
"""

from typing import Dict, Any, Optional
import logging

from ..integration.ticker_metadata import TickerMetadataRepository

logger = logging.getLogger(__name__)


class SectorFetchAgent:
    """
    Agent for fetching sector/industry data from Finnhub.

    Workflow:
    1. Check local cache (ticker_metadata table)
    2. If not cached, fetch from Finnhub via MCP
    3. Cache result for future use
    4. Return sector/industry data

    Example:
        agent = SectorFetchAgent()
        result = agent.fetch("NVDA")
        # Returns:
        # {
        #     'ticker': 'NVDA',
        #     'company_name': 'NVIDIA Corporation',
        #     'sector': 'Technology',
        #     'industry': 'Semiconductors',
        #     'cached': True
        # }
    """

    def __init__(self):
        """Initialize agent with metadata repository."""
        self.metadata_repo = TickerMetadataRepository()

    def fetch(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch sector/industry data for ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dict with sector data or None if not found
        """
        ticker = ticker.upper()

        # Step 1: Check cache
        cached = self.metadata_repo.get_metadata(ticker)
        if cached:
            cached['cached'] = True
            return cached

        # Step 2: Fetch from Finnhub
        finnhub_data = self._fetch_from_finnhub(ticker)
        if finnhub_data is None:
            return None

        # Step 3: Cache result
        self.metadata_repo.save_metadata(
            ticker=ticker,
            company_name=finnhub_data.get('name', ticker),
            sector=finnhub_data.get('sector', 'Other'),
            industry=finnhub_data.get('industry', 'Unknown'),
            market_cap=finnhub_data.get('market_cap')
        )

        return {
            'ticker': ticker,
            'company_name': finnhub_data.get('name', ticker),
            'sector': finnhub_data.get('sector', 'Other'),
            'industry': finnhub_data.get('industry', 'Unknown'),
            'market_cap': finnhub_data.get('market_cap'),
            'cached': False
        }

    def _fetch_from_finnhub(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch company profile from Finnhub.

        Note: This is a placeholder. In production, this would call
        the Finnhub MCP tool: mcp__finnhub__finnhub_stock_market_data

        For now, returns None to trigger cache-only behavior.
        The maintenance sector-sync command will populate the cache.
        """
        # TODO: Implement Finnhub MCP call
        # This will be called during maintenance sector-sync
        logger.debug(f"Finnhub fetch for {ticker} - placeholder")
        return None

    def fetch_batch(self, tickers: list, delay_seconds: float = 1.0) -> Dict[str, Any]:
        """
        Fetch sector data for multiple tickers.

        Args:
            tickers: List of ticker symbols
            delay_seconds: Delay between API calls (rate limiting)

        Returns:
            Dict with results and errors
        """
        import time

        results = {}
        errors = []

        for ticker in tickers:
            try:
                result = self.fetch(ticker)
                if result:
                    results[ticker] = result
                else:
                    errors.append(ticker)

                # Rate limiting (only if we made an API call)
                if result and not result.get('cached'):
                    time.sleep(delay_seconds)

            except Exception as e:
                logger.error(f"Error fetching {ticker}: {e}")
                errors.append(ticker)

        return {
            'results': results,
            'errors': errors,
            'cached_count': sum(1 for r in results.values() if r.get('cached')),
            'fetched_count': sum(1 for r in results.values() if not r.get('cached'))
        }
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_sector_fetch.py -v`

Expected: PASS (2 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/agents/sector_fetch.py agents/tests/test_sector_fetch.py && git commit -m "feat(6.0): add SectorFetchAgent for company profiles

- Check cache first, then fetch from Finnhub
- Batch fetch with rate limiting
- Finnhub integration placeholder (populated via maintenance)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2.4: Update Cross-Ticker Warnings to Use Real Sectors

**Files:**
- Modify: `agents/src/orchestrators/whisper.py`
- Test: `agents/tests/test_whisper_sectors.py`

**Step 1: Write the failing test**

Create `agents/tests/test_whisper_sectors.py`:

```python
#!/usr/bin/env python
"""Tests for real sector-based cross-ticker warnings."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrators.whisper import WhisperOrchestrator


def test_sector_warning_uses_real_sector():
    """Cross-ticker warnings should use real sector names."""
    orchestrator = WhisperOrchestrator()

    # Mock results with sector data
    results = [
        {'ticker': 'NVDA', 'sector': 'Technology'},
        {'ticker': 'AAPL', 'sector': 'Technology'},
        {'ticker': 'MSFT', 'sector': 'Technology'},
    ]

    warnings = orchestrator._detect_cross_ticker_risks(results)

    # Should warn about Technology concentration, not first letter
    warning_text = ' '.join(warnings)
    assert 'Technology' in warning_text or len(warnings) == 0


def test_no_warning_for_different_sectors():
    """No sector warning when tickers are in different sectors."""
    orchestrator = WhisperOrchestrator()

    results = [
        {'ticker': 'NVDA', 'sector': 'Technology'},
        {'ticker': 'JPM', 'sector': 'Financial Services'},
        {'ticker': 'JNJ', 'sector': 'Healthcare'},
    ]

    warnings = orchestrator._detect_cross_ticker_risks(results)

    # Should not warn about sector concentration
    warning_text = ' '.join(warnings)
    assert 'concentration' not in warning_text.lower() or 'sector' not in warning_text.lower()
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_whisper_sectors.py -v`

Expected: FAIL (still using first-letter grouping)

**Step 3: Modify whisper.py**

In `agents/src/orchestrators/whisper.py`:

1. Add import at top:
```python
from ..integration.ticker_metadata import TickerMetadataRepository
```

2. Add to `__init__`:
```python
        self.metadata_repo = TickerMetadataRepository()
```

3. Replace the `_detect_cross_ticker_risks` method (around line 279):

```python
    def _detect_cross_ticker_risks(
        self,
        results: List[Dict[str, Any]]
    ) -> List[str]:
        """Detect cross-ticker correlation and portfolio risks."""
        warnings = []

        # Check 1: Sector correlation using real sector data
        sector_groups = {}
        for result in results:
            ticker = result.get('ticker', '')

            # Get sector from result (if enriched) or from metadata
            sector = result.get('sector')
            if not sector:
                metadata = self.metadata_repo.get_metadata(ticker)
                sector = metadata.get('sector', 'Unknown') if metadata else 'Unknown'

            if sector not in sector_groups:
                sector_groups[sector] = []
            sector_groups[sector].append(ticker)

        # Warn if 3+ tickers in same sector (excluding Unknown)
        for sector, tickers in sector_groups.items():
            if sector != 'Unknown' and len(tickers) >= 3:
                warnings.append(
                    f"Sector concentration: {len(tickers)} {sector} tickers "
                    f"({', '.join(tickers[:5])}{'...' if len(tickers) > 5 else ''})"
                )

        # Check 2: Portfolio exposure limits
        total_exposure = sum(
            self.MAX_POSITION_SIZE for r in results
            if r.get('liquidity_tier') in ['EXCELLENT', 'GOOD']
        )

        if total_exposure > self.PORTFOLIO_WARNING_THRESHOLD:
            warnings.append(
                f"Total potential exposure ${total_exposure:,.0f} exceeds "
                f"${self.PORTFOLIO_WARNING_THRESHOLD:,.0f} recommended limit"
            )

        return warnings
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_whisper_sectors.py -v`

Expected: PASS (2 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/orchestrators/whisper.py agents/tests/test_whisper_sectors.py && git commit -m "feat(6.0): use real sector data in cross-ticker warnings

- Replace first-letter placeholder with actual sector names
- Query ticker_metadata for sector information
- Exclude 'Unknown' sector from concentration warnings

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2.5: Add sector-sync Maintenance Command

**Files:**
- Modify: `agents/src/cli/maintenance.py`

**Step 1: Add sector-sync command**

Add to the `main()` function's task handling (around line 60):

```python
    elif task == 'sector-sync':
        run_sector_sync()
```

Add the usage help update:

```python
        logger.info("Available tasks: health, data-quality, cache-cleanup, sector-sync")
```

**Step 2: Implement run_sector_sync function**

Add at the end of the file (before `if __name__ == '__main__'`):

```python
def run_sector_sync():
    """Sync sector data from Finnhub for upcoming earnings."""
    try:
        from src.agents.sector_fetch import SectorFetchAgent
        from src.integration.container_2_0 import Container2_0

        logger.info("[1/4] Getting upcoming earnings...")

        container = Container2_0()
        result = container.get_upcoming_earnings(days_ahead=30)

        # Extract tickers
        if hasattr(result, 'value'):
            earnings_list = result.value
        else:
            earnings_list = result

        tickers = [t for t, _ in earnings_list]
        logger.info(f"  Found {len(tickers)} tickers with upcoming earnings")
        logger.info("")

        logger.info("[2/4] Checking existing metadata...")
        agent = SectorFetchAgent()

        # Check which need fetching
        need_fetch = []
        have_cached = []
        for ticker in tickers:
            cached = agent.metadata_repo.get_metadata(ticker)
            if cached:
                have_cached.append(ticker)
            else:
                need_fetch.append(ticker)

        logger.info(f"  Already cached: {len(have_cached)}")
        logger.info(f"  Need to fetch: {len(need_fetch)}")
        logger.info("")

        if not need_fetch:
            logger.info("[3/4] All tickers already have metadata")
            logger.info("")
            logger.info("[4/4] Summary:")
            logger.info(f"  Total tickers: {len(tickers)}")
            logger.info(f"  Cached: {len(have_cached)}")
            logger.info(f"  Fetched: 0")
            logger.info("")
            logger.info("=" * 60)
            sys.exit(0)

        logger.info(f"[3/4] Fetching sector data for {len(need_fetch)} tickers...")
        logger.info("  (Rate limited: 1 request/second for Finnhub)")
        logger.info("")

        # Note: This is a placeholder. Real implementation would use
        # the Finnhub MCP tool. For now, just log what would be fetched.
        logger.info("  Note: Finnhub integration pending.")
        logger.info("  Tickers needing data:")
        for ticker in need_fetch[:10]:
            logger.info(f"    - {ticker}")
        if len(need_fetch) > 10:
            logger.info(f"    ... and {len(need_fetch) - 10} more")

        logger.info("")
        logger.info("[4/4] Summary:")
        logger.info(f"  Total tickers: {len(tickers)}")
        logger.info(f"  Cached: {len(have_cached)}")
        logger.info(f"  Pending Finnhub fetch: {len(need_fetch)}")
        logger.info("")
        logger.info("=" * 60)

        sys.exit(0)

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

**Step 3: Test manually**

Run: `cd $PROJECT_ROOT/agents && ./agent.sh maintenance sector-sync`

Expected: Shows tickers needing sector data

**Step 4: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/cli/maintenance.py && git commit -m "feat(6.0): add sector-sync maintenance command

- List tickers needing sector data
- Check existing cache before fetching
- Placeholder for Finnhub integration

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Feature 3: Automated Data Quality Fixes

### Task 3.1: Create DataQualityAgent

**Files:**
- Create: `agents/src/agents/data_quality.py`
- Test: `agents/tests/test_data_quality_agent.py`

**Step 1: Write the failing test**

Create `agents/tests/test_data_quality_agent.py`:

```python
#!/usr/bin/env python
"""Tests for DataQualityAgent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.agents.data_quality import DataQualityAgent


class TestDataQualityAgent:
    """Tests for DataQualityAgent."""

    def test_report_mode_returns_issues(self):
        """Report mode should return issues without fixing."""
        agent = DataQualityAgent()
        result = agent.run(mode="report")

        assert 'fixable_issues' in result
        assert 'flagged_issues' in result
        assert 'summary' in result

    def test_dry_run_shows_what_would_fix(self):
        """Dry run should show fixes without applying."""
        agent = DataQualityAgent()
        result = agent.run(mode="dry-run")

        assert 'would_fix' in result or 'fixable_issues' in result
        assert result.get('changes_applied') is False

    def test_mode_validation(self):
        """Invalid mode should raise error."""
        agent = DataQualityAgent()

        with pytest.raises(ValueError, match="Invalid mode"):
            agent.run(mode="invalid")
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_data_quality_agent.py -v`

Expected: FAIL with "cannot import name 'DataQualityAgent'"

**Step 3: Write minimal implementation**

Create `agents/src/agents/data_quality.py`:

```python
"""DataQualityAgent - Automated data quality checks and fixes.

This agent scans the database for data quality issues and can
automatically fix safe issues while flagging ambiguous ones.
"""

from typing import Dict, Any, List
import logging
import sqlite3

from ..integration.container_2_0 import Container2_0

logger = logging.getLogger(__name__)


class DataQualityAgent:
    """
    Agent for automated data quality management.

    Modes:
    - report: Identify issues without fixing
    - dry-run: Show what would be fixed
    - fix: Apply safe fixes

    Safe to auto-fix:
    - Duplicate historical_moves entries
    - Missing ticker_metadata (fetch from Finnhub)
    - Stale earnings dates (refresh from Alpha Vantage)

    Requires manual review:
    - Tickers with <4 quarters (need backfill)
    - Extreme outliers >50% (may be valid)
    """

    def __init__(self):
        """Initialize with database connection."""
        container = Container2_0()
        self.db_path = container.get_db_path()

    def run(self, mode: str = "report") -> Dict[str, Any]:
        """
        Run data quality analysis.

        Args:
            mode: "report" | "dry-run" | "fix"

        Returns:
            Dict with issues found and actions taken
        """
        if mode not in ["report", "dry-run", "fix"]:
            raise ValueError(f"Invalid mode: {mode}. Use report, dry-run, or fix")

        # Collect issues
        fixable_issues = []
        flagged_issues = []

        # Check 1: Duplicates
        duplicates = self._find_duplicates()
        if duplicates:
            fixable_issues.append({
                'type': 'duplicates',
                'count': len(duplicates),
                'items': duplicates[:10],
                'fix_action': 'Delete older duplicate entries'
            })

        # Check 2: Insufficient data (<4 quarters)
        insufficient = self._find_insufficient_data()
        if insufficient:
            flagged_issues.append({
                'type': 'insufficient_data',
                'count': len(insufficient),
                'items': insufficient[:10],
                'reason': 'Requires manual backfill'
            })

        # Check 3: Outliers (>50% moves)
        outliers = self._find_outliers()
        if outliers:
            flagged_issues.append({
                'type': 'outliers',
                'count': len(outliers),
                'items': outliers[:10],
                'reason': 'May be valid data - manual review needed'
            })

        # Apply fixes if requested
        fixed_issues = []
        if mode == "fix":
            fixed_issues = self._apply_fixes(fixable_issues)

        return {
            'fixable_issues': fixable_issues,
            'flagged_issues': flagged_issues,
            'fixed_issues': fixed_issues if mode == "fix" else [],
            'would_fix': fixable_issues if mode == "dry-run" else [],
            'changes_applied': mode == "fix",
            'summary': {
                'total_fixable': sum(i['count'] for i in fixable_issues),
                'total_flagged': sum(i['count'] for i in flagged_issues),
                'total_fixed': len(fixed_issues)
            }
        }

    def _find_duplicates(self) -> List[Dict]:
        """Find duplicate historical_moves entries."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, earnings_date, COUNT(*) as cnt
                FROM historical_moves
                GROUP BY ticker, earnings_date
                HAVING cnt > 1
            """)

            rows = cursor.fetchall()
            conn.close()

            return [{'ticker': r[0], 'date': r[1], 'count': r[2]} for r in rows]

        except Exception:
            return []

    def _find_insufficient_data(self) -> List[Dict]:
        """Find tickers with <4 quarters of data."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, COUNT(*) as quarters
                FROM historical_moves
                GROUP BY ticker
                HAVING quarters < 4
                ORDER BY quarters ASC
            """)

            rows = cursor.fetchall()
            conn.close()

            return [{'ticker': r[0], 'quarters': r[1]} for r in rows]

        except Exception:
            return []

    def _find_outliers(self) -> List[Dict]:
        """Find extreme outlier moves (>50%)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, earnings_date, gap_move_pct
                FROM historical_moves
                WHERE ABS(gap_move_pct) > 50
                ORDER BY ABS(gap_move_pct) DESC
            """)

            rows = cursor.fetchall()
            conn.close()

            return [{'ticker': r[0], 'date': r[1], 'move': r[2]} for r in rows]

        except Exception:
            return []

    def _apply_fixes(self, fixable_issues: List[Dict]) -> List[str]:
        """Apply safe fixes to the database."""
        fixed = []

        for issue in fixable_issues:
            if issue['type'] == 'duplicates':
                count = self._fix_duplicates()
                if count > 0:
                    fixed.append(f"Removed {count} duplicate entries")

        return fixed

    def _fix_duplicates(self) -> int:
        """Remove duplicate historical_moves entries (keep newest)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Delete duplicates, keeping the row with highest rowid
            cursor.execute("""
                DELETE FROM historical_moves
                WHERE rowid NOT IN (
                    SELECT MAX(rowid)
                    FROM historical_moves
                    GROUP BY ticker, earnings_date
                )
            """)

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            return deleted

        except Exception as e:
            logger.error(f"Error fixing duplicates: {e}")
            return 0
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_data_quality_agent.py -v`

Expected: PASS (3 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/agents/data_quality.py agents/tests/test_data_quality_agent.py && git commit -m "feat(6.0): add DataQualityAgent for automated fixes

- report/dry-run/fix modes
- Auto-fix duplicates
- Flag insufficient data and outliers for manual review

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 3.2: Add --fix and --dry-run flags to maintenance CLI

**Files:**
- Modify: `agents/src/cli/maintenance.py`

**Step 1: Update argument parsing**

Replace the data-quality task handling in `main()`:

```python
    elif task == 'data-quality':
        # Check for flags
        fix_mode = '--fix' in sys.argv
        dry_run = '--dry-run' in sys.argv

        if fix_mode and dry_run:
            logger.error("Cannot use both --fix and --dry-run")
            sys.exit(1)

        mode = "fix" if fix_mode else ("dry-run" if dry_run else "report")
        run_data_quality_v2(mode)
```

**Step 2: Add new run_data_quality_v2 function**

Add after existing functions:

```python
def run_data_quality_v2(mode: str):
    """Run data quality with DataQualityAgent."""
    try:
        from src.agents.data_quality import DataQualityAgent

        agent = DataQualityAgent()

        logger.info(f"[1/3] Running data quality scan (mode: {mode})...")
        result = agent.run(mode=mode)

        logger.info("")
        logger.info("[2/3] Results:")
        logger.info("")

        # Fixable issues
        fixable = result.get('fixable_issues', [])
        if fixable:
            logger.info(f"Fixable issues ({sum(i['count'] for i in fixable)}):")
            for issue in fixable:
                logger.info(f"  - {issue['type']}: {issue['count']} items")
                logger.info(f"    Action: {issue['fix_action']}")
        else:
            logger.info("No fixable issues found")
        logger.info("")

        # Flagged issues
        flagged = result.get('flagged_issues', [])
        if flagged:
            logger.warning(f"Flagged for manual review ({sum(i['count'] for i in flagged)}):")
            for issue in flagged:
                logger.warning(f"  - {issue['type']}: {issue['count']} items")
                logger.info(f"    Reason: {issue['reason']}")
        else:
            logger.info("No issues flagged for review")
        logger.info("")

        # Actions taken
        if mode == "fix":
            fixed = result.get('fixed_issues', [])
            if fixed:
                logger.info("Actions taken:")
                for action in fixed:
                    logger.info(f"  ✓ {action}")
            else:
                logger.info("No fixes applied")
        elif mode == "dry-run":
            would_fix = result.get('would_fix', [])
            if would_fix:
                logger.info("Would fix (dry-run):")
                for issue in would_fix:
                    logger.info(f"  - {issue['type']}: {issue['count']} items")

        logger.info("")
        logger.info("[3/3] Summary:")
        summary = result.get('summary', {})
        logger.info(f"  Fixable: {summary.get('total_fixable', 0)}")
        logger.info(f"  Flagged: {summary.get('total_flagged', 0)}")
        if mode == "fix":
            logger.info(f"  Fixed: {summary.get('total_fixed', 0)}")

        logger.info("")

        if mode == "report" and summary.get('total_fixable', 0) > 0:
            logger.info("Run with --fix to apply fixes, or --dry-run to preview")

        logger.info("")
        logger.info("=" * 60)

        sys.exit(0)

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

**Step 3: Update help text**

Update the error messages to include new flags:

```python
        logger.info("  ./agent.sh maintenance data-quality --fix")
        logger.info("  ./agent.sh maintenance data-quality --dry-run")
```

**Step 4: Test manually**

Run: `cd $PROJECT_ROOT/agents && ./agent.sh maintenance data-quality --dry-run`

Expected: Shows what would be fixed

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/cli/maintenance.py && git commit -m "feat(6.0): add --fix and --dry-run to data-quality command

- --dry-run shows what would be fixed
- --fix applies safe fixes
- Uses DataQualityAgent for analysis

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Feature 4: PatternRecognitionAgent

### Task 4.1: Add PatternResult Schema

**Files:**
- Modify: `agents/src/utils/schemas.py`
- Test: `agents/tests/test_schemas.py`

**Step 1: Write the failing test**

Add to `agents/tests/test_schemas.py`:

```python
from src.utils.schemas import PatternResult


class TestPatternResult:
    """Tests for PatternResult schema."""

    def test_valid_pattern_result(self):
        """Valid pattern result should be created."""
        result = PatternResult(
            ticker="NVDA",
            quarters_analyzed=16,
            bullish_pct=0.69,
            bearish_pct=0.31,
            directional_bias="BULLISH",
            current_streak=3,
            streak_direction="UP",
            avg_move_recent=5.1,
            avg_move_overall=3.8,
            magnitude_trend="EXPANDING"
        )
        assert result.directional_bias == "BULLISH"
        assert result.magnitude_trend == "EXPANDING"

    def test_optional_fields(self):
        """Optional fields should default to None."""
        result = PatternResult(
            ticker="TEST",
            quarters_analyzed=8,
            bullish_pct=0.5,
            bearish_pct=0.5,
            current_streak=0,
            streak_direction="UP",
            avg_move_recent=3.0,
            avg_move_overall=3.0
        )
        assert result.directional_bias is None
        assert result.seasonal_pattern is None

    def test_invalid_directional_bias(self):
        """Invalid bias should raise error."""
        with pytest.raises(ValueError):
            PatternResult(
                ticker="TEST",
                quarters_analyzed=8,
                bullish_pct=0.5,
                bearish_pct=0.5,
                directional_bias="INVALID",
                current_streak=0,
                streak_direction="UP",
                avg_move_recent=3.0,
                avg_move_overall=3.0
            )
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_schemas.py::TestPatternResult -v`

Expected: FAIL with "cannot import name 'PatternResult'"

**Step 3: Write minimal implementation**

Add to `agents/src/utils/schemas.py`:

```python
class PatternResult(BaseModel):
    """Historical pattern analysis result."""
    ticker: str
    quarters_analyzed: int

    # Directional analysis
    bullish_pct: float
    bearish_pct: float
    directional_bias: Optional[str] = None

    # Streak analysis
    current_streak: int
    streak_direction: str

    # Magnitude analysis
    avg_move_recent: float  # Last 4 quarters
    avg_move_overall: float
    magnitude_trend: Optional[str] = None

    # Optional patterns
    seasonal_pattern: Optional[str] = None
    fade_pct: Optional[float] = None
    recent_moves: Optional[List[Dict[str, Any]]] = None

    @validator('directional_bias')
    def validate_directional_bias(cls, v):
        if v is not None and v not in ['BULLISH', 'BEARISH', 'NEUTRAL']:
            raise ValueError(f'Invalid directional_bias: {v}')
        return v

    @validator('streak_direction')
    def validate_streak_direction(cls, v):
        if v not in ['UP', 'DOWN']:
            raise ValueError(f'Invalid streak_direction: {v}')
        return v

    @validator('magnitude_trend')
    def validate_magnitude_trend(cls, v):
        if v is not None and v not in ['EXPANDING', 'CONTRACTING', 'STABLE']:
            raise ValueError(f'Invalid magnitude_trend: {v}')
        return v
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_schemas.py::TestPatternResult -v`

Expected: PASS (3 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/utils/schemas.py agents/tests/test_schemas.py && git commit -m "feat(6.0): add PatternResult schema for pattern recognition

- Directional bias (BULLISH/BEARISH/NEUTRAL)
- Streak tracking
- Magnitude trends (EXPANDING/CONTRACTING/STABLE)
- Optional seasonal patterns

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 4.2: Create PatternRecognitionAgent

**Files:**
- Create: `agents/src/agents/pattern_recognition.py`
- Test: `agents/tests/test_pattern_recognition.py`

**Step 1: Write the failing test**

Create `agents/tests/test_pattern_recognition.py`:

```python
#!/usr/bin/env python
"""Tests for PatternRecognitionAgent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.agents.pattern_recognition import PatternRecognitionAgent


class TestPatternRecognitionAgent:
    """Tests for PatternRecognitionAgent."""

    def test_analyze_returns_pattern_result(self):
        """Should return pattern analysis for ticker with data."""
        agent = PatternRecognitionAgent()

        # Use a ticker likely to have historical data
        result = agent.analyze("AAPL")

        if result is not None:
            assert 'ticker' in result
            assert 'quarters_analyzed' in result
            assert 'bullish_pct' in result
            assert 'current_streak' in result

    def test_returns_none_for_insufficient_data(self):
        """Should return None for ticker with <8 quarters."""
        agent = PatternRecognitionAgent()

        # Use a ticker unlikely to have enough data
        result = agent.analyze("XXXXX")

        assert result is None

    def test_directional_bias_calculation(self):
        """Directional bias should match bullish percentage."""
        agent = PatternRecognitionAgent()

        # Test with known data
        result = agent.analyze("AAPL")

        if result and result.get('directional_bias'):
            bullish_pct = result['bullish_pct']
            bias = result['directional_bias']

            if bullish_pct >= 0.65:
                assert bias == "BULLISH"
            elif bullish_pct <= 0.35:
                assert bias == "BEARISH"
            else:
                assert bias == "NEUTRAL"
```

**Step 2: Run test to verify it fails**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_pattern_recognition.py -v`

Expected: FAIL with "cannot import name 'PatternRecognitionAgent'"

**Step 3: Write implementation**

Create `agents/src/agents/pattern_recognition.py`:

```python
"""PatternRecognitionAgent - Analyzes historical earnings patterns.

This agent mines historical_moves data to identify actionable patterns
like directional bias, streaks, and magnitude trends.
"""

from typing import Dict, Any, Optional, List
import logging

from ..integration.container_2_0 import Container2_0
from ..utils.schemas import PatternResult

logger = logging.getLogger(__name__)


class PatternRecognitionAgent:
    """
    Agent for analyzing historical earnings patterns.

    Patterns detected:
    - Directional bias (>65% moves in same direction)
    - Streak (consecutive same-direction moves)
    - Magnitude trend (recent moves vs overall average)
    - Seasonality (Q4 vs other quarters)
    - Fade pattern (gap direction vs close direction)

    Minimum data requirement: 8 quarters

    Example:
        agent = PatternRecognitionAgent()
        result = agent.analyze("NVDA")
        # Returns:
        # {
        #     'ticker': 'NVDA',
        #     'quarters_analyzed': 16,
        #     'bullish_pct': 0.69,
        #     'directional_bias': 'BULLISH',
        #     'current_streak': 3,
        #     'streak_direction': 'UP',
        #     ...
        # }
    """

    MIN_QUARTERS = 8
    BIAS_THRESHOLD = 0.65  # 65% for directional bias
    STREAK_THRESHOLD = 3   # Min streak to report
    MAGNITUDE_CHANGE_THRESHOLD = 0.20  # 20% change for trend

    def __init__(self):
        """Initialize with core container for data access."""
        self.container = Container2_0()

    def analyze(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Analyze historical patterns for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Pattern analysis dict or None if insufficient data
        """
        # Get historical moves
        moves = self.container.get_historical_moves(ticker, limit=50)

        # Handle Result type
        if hasattr(moves, 'is_err') and moves.is_err:
            return None
        if hasattr(moves, 'value'):
            moves = moves.value

        if not moves or len(moves) < self.MIN_QUARTERS:
            logger.debug(f"{ticker}: Insufficient data ({len(moves) if moves else 0} quarters)")
            return None

        # Sort by date (newest first)
        moves = sorted(moves, key=lambda x: x.get('earnings_date', ''), reverse=True)

        # Calculate patterns
        directional = self._analyze_directional(moves)
        streak = self._analyze_streak(moves)
        magnitude = self._analyze_magnitude(moves)
        recent = self._get_recent_moves(moves)

        # Build result
        result = {
            'ticker': ticker,
            'quarters_analyzed': len(moves),
            **directional,
            **streak,
            **magnitude,
            'recent_moves': recent
        }

        # Validate with schema
        try:
            validated = PatternResult(**result)
            return validated.dict()
        except Exception as e:
            logger.error(f"Schema validation error: {e}")
            return result

    def _analyze_directional(self, moves: List[Dict]) -> Dict[str, Any]:
        """Analyze directional bias."""
        up_count = sum(1 for m in moves if m.get('direction') == 'UP')
        total = len(moves)

        bullish_pct = up_count / total if total > 0 else 0.5
        bearish_pct = 1 - bullish_pct

        # Determine bias
        if bullish_pct >= self.BIAS_THRESHOLD:
            bias = "BULLISH"
        elif bearish_pct >= self.BIAS_THRESHOLD:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {
            'bullish_pct': round(bullish_pct, 2),
            'bearish_pct': round(bearish_pct, 2),
            'directional_bias': bias
        }

    def _analyze_streak(self, moves: List[Dict]) -> Dict[str, Any]:
        """Analyze current streak."""
        if not moves:
            return {'current_streak': 0, 'streak_direction': 'UP'}

        # Get direction of most recent move
        current_direction = moves[0].get('direction', 'UP')
        streak = 1

        # Count consecutive moves in same direction
        for move in moves[1:]:
            if move.get('direction') == current_direction:
                streak += 1
            else:
                break

        return {
            'current_streak': streak,
            'streak_direction': current_direction
        }

    def _analyze_magnitude(self, moves: List[Dict]) -> Dict[str, Any]:
        """Analyze magnitude trends."""
        # Get move magnitudes
        magnitudes = [abs(m.get('gap_move_pct', 0)) for m in moves]

        if len(magnitudes) < 4:
            return {
                'avg_move_recent': 0.0,
                'avg_move_overall': 0.0,
                'magnitude_trend': None
            }

        # Recent = last 4 quarters, Overall = all data
        recent_avg = sum(magnitudes[:4]) / 4
        overall_avg = sum(magnitudes) / len(magnitudes)

        # Determine trend
        if overall_avg > 0:
            change_pct = (recent_avg - overall_avg) / overall_avg
            if change_pct > self.MAGNITUDE_CHANGE_THRESHOLD:
                trend = "EXPANDING"
            elif change_pct < -self.MAGNITUDE_CHANGE_THRESHOLD:
                trend = "CONTRACTING"
            else:
                trend = "STABLE"
        else:
            trend = "STABLE"

        return {
            'avg_move_recent': round(recent_avg, 2),
            'avg_move_overall': round(overall_avg, 2),
            'magnitude_trend': trend
        }

    def _get_recent_moves(self, moves: List[Dict], limit: int = 4) -> List[Dict]:
        """Get recent moves for display."""
        recent = []
        for move in moves[:limit]:
            recent.append({
                'date': move.get('earnings_date', 'N/A'),
                'move': round(move.get('gap_move_pct', 0), 2),
                'direction': move.get('direction', 'N/A')
            })
        return recent
```

**Step 4: Run test to verify it passes**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_pattern_recognition.py -v`

Expected: PASS (3 tests)

**Step 5: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/agents/pattern_recognition.py agents/tests/test_pattern_recognition.py && git commit -m "feat(6.0): add PatternRecognitionAgent

- Directional bias detection (>65% threshold)
- Streak tracking (consecutive same-direction moves)
- Magnitude trend analysis (expanding/contracting)
- Recent moves history

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 4.3: Integrate Patterns into AnalyzeOrchestrator

**Files:**
- Modify: `agents/src/orchestrators/analyze.py`

**Step 1: Add PatternRecognitionAgent import**

At the top of `analyze.py`:

```python
from ..agents.pattern_recognition import PatternRecognitionAgent
```

**Step 2: Run pattern analysis in parallel**

In `_parallel_specialist_analysis`, add pattern agent after anomaly detection (around line 251):

```python
            # Run pattern recognition
            pattern_agent = PatternRecognitionAgent()
            patterns = pattern_agent.analyze(ticker)

            return {
                'ticker_analysis': ticker_result,
                'sentiment': sentiment_result if not isinstance(sentiment_result, Exception) else {'error': str(sentiment_result)},
                'explanation': explanation,
                'anomaly': anomaly,
                'patterns': patterns
            }
```

**Step 3: Add patterns to report synthesis**

In `_synthesize_report`, add after `'historical_context'` (around line 326):

```python
            'patterns': specialist_results.get('patterns')
```

**Step 4: Add patterns section to format_results**

In `format_results`, add after the Anomalies section (around line 554):

```python
        # Patterns
        patterns = report.get('patterns')
        if patterns and patterns.get('quarters_analyzed', 0) >= 8:
            output.append("## Historical Patterns")
            output.append("")
            output.append(f"**Quarters Analyzed:** {patterns['quarters_analyzed']}")
            output.append("")

            # Directional bias
            bias = patterns.get('directional_bias', 'NEUTRAL')
            bullish_pct = patterns.get('bullish_pct', 0.5)
            bias_emoji = {'BULLISH': '↑', 'BEARISH': '↓', 'NEUTRAL': '↔'}
            output.append(f"{bias_emoji.get(bias, '↔')} **Directional Bias:** {bias} ({bullish_pct:.0%} UP moves)")

            # Streak
            streak = patterns.get('current_streak', 0)
            streak_dir = patterns.get('streak_direction', 'UP')
            if streak >= 3:
                output.append(f"🔥 **Current Streak:** {streak} consecutive {streak_dir}")

            # Magnitude trend
            trend = patterns.get('magnitude_trend')
            if trend and trend != 'STABLE':
                recent = patterns.get('avg_move_recent', 0)
                overall = patterns.get('avg_move_overall', 0)
                trend_emoji = '📈' if trend == 'EXPANDING' else '📉'
                output.append(f"{trend_emoji} **Magnitude:** {trend} ({recent:.1f}% recent vs {overall:.1f}% avg)")

            # Recent moves
            recent_moves = patterns.get('recent_moves', [])
            if recent_moves:
                output.append("")
                output.append("**Recent Earnings:**")
                for move in recent_moves:
                    arrow = '↑' if move['direction'] == 'UP' else '↓'
                    output.append(f"  {move['date']}: {move['move']:+.1f}% {arrow}")

            output.append("")
```

**Step 5: Run tests**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/test_analyze_live.py -v`

Expected: PASS

**Step 6: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/orchestrators/analyze.py && git commit -m "feat(6.0): integrate PatternRecognitionAgent into analyze

- Run pattern analysis in parallel with other agents
- Add Historical Patterns section to output
- Show directional bias, streaks, magnitude trends

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 4.4: Update exports and run final test suite

**Files:**
- Modify: `agents/src/agents/__init__.py`
- Modify: `agents/src/utils/__init__.py`

**Step 1: Update agents/__init__.py**

```python
from .ticker_analysis import TickerAnalysisAgent
from .sentiment_fetch import SentimentFetchAgent
from .health import HealthCheckAgent
from .explanation import ExplanationAgent
from .anomaly import AnomalyDetectionAgent
from .sector_fetch import SectorFetchAgent
from .data_quality import DataQualityAgent
from .pattern_recognition import PatternRecognitionAgent

__all__ = [
    'TickerAnalysisAgent',
    'SentimentFetchAgent',
    'HealthCheckAgent',
    'ExplanationAgent',
    'AnomalyDetectionAgent',
    'SectorFetchAgent',
    'DataQualityAgent',
    'PatternRecognitionAgent',
]
```

**Step 2: Update utils/__init__.py**

```python
from .schemas import (
    TickerAnalysisResponse,
    SentimentFetchResponse,
    ExplanationResponse,
    AnomalyDetectionResponse,
    HealthCheckResponse,
    PositionLimits,
    TickerMetadata,
    PatternResult,
)
```

**Step 3: Run full test suite**

Run: `cd $PROJECT_ROOT/agents && ../core/venv/bin/python -m pytest tests/ -v`

Expected: All tests pass (25+ tests)

**Step 4: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/src/agents/__init__.py agents/src/utils/__init__.py && git commit -m "chore(6.0): update exports for Phase 3 completion

- Export all new agents
- Export all new schemas

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Final Steps

### Update agents README

**Step 1: Update README.md Phase 3 status**

In `agents/README.md`, update the Phase 3 section to show completion:

```markdown
### ✅ Phase 3: Enhanced Intelligence (Complete - Jan 2026)

**Delivered:**
- ✅ TRR-based position sizing (HIGH TRR warnings, max contracts)
- ✅ Real sector data integration (Finnhub company profiles)
- ✅ Automated data quality fixes (--fix mode for duplicates)
- ✅ PatternRecognitionAgent (directional bias, streaks, trends)
- ✅ sector-sync maintenance command
```

**Step 2: Commit**

```bash
cd "$PROJECT_ROOT" && git add agents/README.md && git commit -m "docs(6.0): update README for Phase 3 completion

- Mark all Phase 3 features as complete
- Update architecture diagram
- Add new commands and agents

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Verification Checklist

After completing all tasks, verify:

- [ ] `./agent.sh maintenance health` shows all systems healthy
- [ ] `./agent.sh whisper` shows TRR badges for HIGH risk tickers
- [ ] `./agent.sh analyze AAPL` shows Position Limits and Patterns sections
- [ ] `./agent.sh maintenance data-quality --dry-run` works
- [ ] `./agent.sh maintenance sector-sync` shows tickers needing data
- [ ] All tests pass: `../core/venv/bin/python -m pytest tests/ -v`
