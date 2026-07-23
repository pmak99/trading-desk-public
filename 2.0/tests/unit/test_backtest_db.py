"""Tests for backtest.db SQLite query functions."""
import sqlite3
import pytest
from datetime import date
from pathlib import Path

from src.application.services.backtest.db import (
    get_historical_moves,
    get_all_earnings_in_period,
)


@pytest.fixture
def db_path(tmp_path) -> Path:
    """In-memory SQLite DB with historical_moves table seeded."""
    p = tmp_path / "test.db"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE historical_moves "
        "(ticker TEXT, earnings_date TEXT, close_move_pct REAL)"
    )
    conn.executemany("INSERT INTO historical_moves VALUES (?,?,?)", [
        ("AAPL", "2025-01-15", 5.2),
        ("AAPL", "2024-10-15", -3.1),
        ("AAPL", "2024-07-15", 8.4),
        ("AAPL", "2024-04-15", 4.0),
        ("GOOGL", "2025-01-20", 4.8),
        ("GOOGL", "2024-10-20", -2.5),
    ])
    conn.commit()
    conn.close()
    return p


class TestGetHistoricalMoves:
    def test_returns_moves_before_date(self, db_path):
        moves = get_historical_moves(db_path, "AAPL", date(2025, 6, 1))
        assert len(moves) == 4
        assert all(d < date(2025, 6, 1) for d, _ in moves)

    def test_returns_date_float_tuples(self, db_path):
        moves = get_historical_moves(db_path, "AAPL", date(2025, 6, 1))
        for d, v in moves:
            assert isinstance(d, date)
            assert isinstance(v, float)

    def test_respects_num_quarters_limit(self, db_path):
        moves = get_historical_moves(db_path, "AAPL", date(2025, 6, 1), num_quarters=2)
        assert len(moves) <= 2

    def test_excludes_moves_on_or_after_date(self, db_path):
        # 2025-01-15 is NOT before 2025-01-15 (strict less-than)
        moves = get_historical_moves(db_path, "AAPL", date(2025, 1, 15))
        assert all(d < date(2025, 1, 15) for d, _ in moves)

    def test_unknown_ticker_returns_empty(self, db_path):
        moves = get_historical_moves(db_path, "UNKNOWN", date(2025, 6, 1))
        assert moves == []

    def test_ordered_most_recent_first(self, db_path):
        moves = get_historical_moves(db_path, "AAPL", date(2025, 6, 1))
        dates = [d for d, _ in moves]
        assert dates == sorted(dates, reverse=True)


class TestGetAllEarningsInPeriod:
    def test_returns_events_in_range(self, db_path):
        events = get_all_earnings_in_period(db_path, date(2025, 1, 1), date(2025, 12, 31))
        assert len(events) == 2  # AAPL 2025-01-15 + GOOGL 2025-01-20

    def test_returns_ticker_date_float_tuples(self, db_path):
        events = get_all_earnings_in_period(db_path, date(2025, 1, 1), date(2025, 12, 31))
        for ticker, d, move in events:
            assert isinstance(ticker, str)
            assert isinstance(d, date)
            assert isinstance(move, float)

    def test_inclusive_boundaries(self, db_path):
        events = get_all_earnings_in_period(db_path, date(2025, 1, 15), date(2025, 1, 15))
        tickers = [e[0] for e in events]
        assert "AAPL" in tickers

    def test_empty_range_returns_empty(self, db_path):
        events = get_all_earnings_in_period(db_path, date(2023, 1, 1), date(2023, 12, 31))
        assert events == []

    def test_ordered_by_date_then_ticker(self, db_path):
        events = get_all_earnings_in_period(db_path, date(2024, 1, 1), date(2025, 12, 31))
        dates = [d for _, d, _ in events]
        assert dates == sorted(dates)
