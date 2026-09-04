"""
Unit tests for TickerAnalyzer._check_existing_position() -- the "one
position per ticker per earnings event" guard (CLAUDE.md: "No second bet,
no repair attempt", added after a real repair campaign turned a large
first loss catastrophic).

Uses a real sqlite ConnectionPool against a tmp DB (not mocked row objects)
so the actual SQL runs and any query-syntax error would be caught, matching
the pattern used for the other data-integrity fixes this session.
"""

import sys
import sqlite3
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.application.services.analyzer import TickerAnalyzer
from src.infrastructure.database.connection_pool import ConnectionPool


def _create_db(path: Path) -> str:
    db_path = str(path / "test_ivcrush.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            acquired_date DATE NOT NULL,
            sale_date DATE,
            earnings_date DATE,
            quantity INTEGER,
            gain_loss REAL NOT NULL,
            is_winner BOOLEAN NOT NULL,
            campaign_id TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert(db_path, symbol, earnings_date, strategy_type="SPREAD",
            acquired_date="2025-12-21", sale_date="2025-12-22",
            quantity=128, gain_loss=-39811.3, campaign_id=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO strategies
           (symbol, strategy_type, acquired_date, sale_date, earnings_date,
            quantity, gain_loss, is_winner, campaign_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (symbol, strategy_type, acquired_date, sale_date, earnings_date,
         quantity, gain_loss, gain_loss > 0, campaign_id),
    )
    conn.commit()
    conn.close()


def _make_analyzer(db_path) -> TickerAnalyzer:
    container = MagicMock()
    container.db_pool = ConnectionPool(db_path=Path(db_path), pool_size=1, max_overflow=1)
    return TickerAnalyzer(container)


class TestExistingPositionCheck:

    def test_no_prior_position_returns_none(self, tmp_path):
        db = _create_db(tmp_path)
        analyzer = _make_analyzer(db)

        result = analyzer._check_existing_position("MU", date(2025, 12, 19))

        assert result is None

    def test_prior_position_same_ticker_and_event_flagged(self, tmp_path):
        # Reproduces the exact Dec 2025 MU shape: first leg already journaled
        # for this specific earnings event.
        db = _create_db(tmp_path)
        _insert(db, "MU", "2025-12-19", quantity=128, campaign_id="MU-2025-12")
        analyzer = _make_analyzer(db)

        result = analyzer._check_existing_position("MU", date(2025, 12, 19))

        assert result is not None
        assert "MU" in result
        assert "128" in result
        assert "MU-2025-12" in result

    def test_same_ticker_different_earnings_event_not_flagged(self, tmp_path):
        # A prior position on MU from a DIFFERENT quarter's earnings must
        # not block a new, unrelated event.
        db = _create_db(tmp_path)
        _insert(db, "MU", "2025-09-23", quantity=800, campaign_id=None)
        analyzer = _make_analyzer(db)

        result = analyzer._check_existing_position("MU", date(2025, 12, 19))

        assert result is None

    def test_different_ticker_same_date_not_flagged(self, tmp_path):
        db = _create_db(tmp_path)
        _insert(db, "AAPL", "2025-12-19", quantity=50)
        analyzer = _make_analyzer(db)

        result = analyzer._check_existing_position("MU", date(2025, 12, 19))

        assert result is None

    def test_multiple_prior_positions_counted_and_most_recent_shown(self, tmp_path):
        db = _create_db(tmp_path)
        _insert(db, "MU", "2025-12-19", acquired_date="2025-12-21",
                quantity=128, campaign_id="MU-2025-12")
        _insert(db, "MU", "2025-12-19", acquired_date="2025-12-28",
                quantity=200, campaign_id="MU-2025-12")
        analyzer = _make_analyzer(db)

        result = analyzer._check_existing_position("MU", date(2025, 12, 19))

        assert result is not None
        assert "2 prior" in result
        # Most recent by acquired_date (12-28) should be the one described.
        assert "200" in result

    def test_db_error_is_non_fatal(self, tmp_path):
        # Point at a path with no strategies table -- must degrade to None,
        # never raise and block analysis.
        db_path = str(tmp_path / "empty.db")
        sqlite3.connect(db_path).close()
        analyzer = _make_analyzer(db_path)

        result = analyzer._check_existing_position("MU", date(2025, 12, 19))

        assert result is None
