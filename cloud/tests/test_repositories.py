# 5.0/tests/test_repositories.py
"""Tests for domain repositories."""

import pytest
import tempfile
import os
import sqlite3
from src.domain.repositories import HistoricalMovesRepository, SentimentCacheRepository


@pytest.fixture
def db_path():
    """Create temp database with schema."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    # Create historical_moves table
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE historical_moves (
            ticker TEXT,
            earnings_date TEXT,
            gap_move_pct REAL,
            intraday_move_pct REAL,
            close_before REAL,
            close_after REAL,
            direction TEXT,
            PRIMARY KEY (ticker, earnings_date)
        )
    """)
    conn.commit()
    conn.close()

    yield path
    os.unlink(path)


# Historical Moves Tests

def test_historical_moves_empty(db_path):
    """get_moves returns empty list for unknown ticker."""
    repo = HistoricalMovesRepository(db_path=db_path)
    moves = repo.get_moves("UNKN")  # Valid 4-letter ticker
    assert moves == []


def test_historical_moves_save_and_get(db_path):
    """Can save and retrieve historical moves."""
    repo = HistoricalMovesRepository(db_path=db_path)

    move = {
        "ticker": "NVDA",
        "earnings_date": "2025-02-15",
        "gap_move_pct": 8.5,
        "intraday_move_pct": 10.2,
        "close_before": 500.0,
        "close_after": 543.0,
        "direction": "UP",
    }

    result = repo.save_move(move)
    assert result is True

    moves = repo.get_moves("NVDA")
    assert len(moves) == 1
    assert moves[0]["gap_move_pct"] == 8.5
    assert moves[0]["direction"] == "UP"


def test_historical_moves_average(db_path):
    """get_average_move calculates average absolute move."""
    repo = HistoricalMovesRepository(db_path=db_path)

    # Save multiple moves
    for i, pct in enumerate([5.0, -3.0, 8.0, -2.0]):
        repo.save_move({
            "ticker": "AAPL",
            "earnings_date": f"2024-{i+1:02d}-15",
            "gap_move_pct": pct,
        })

    avg = repo.get_average_move("AAPL")
    # (5 + 3 + 8 + 2) / 4 = 4.5
    assert avg == 4.5


def test_historical_moves_count(db_path):
    """count_moves returns correct count."""
    repo = HistoricalMovesRepository(db_path=db_path)

    for i in range(5):
        repo.save_move({
            "ticker": "TSLA",
            "earnings_date": f"2024-{i+1:02d}-15",
            "gap_move_pct": i * 1.5,
        })

    assert repo.count_moves("TSLA") == 5
    assert repo.count_moves("OTHER") == 0


# Sentiment Cache Tests

def test_sentiment_cache_empty(db_path):
    """get_sentiment returns None for uncached ticker."""
    repo = SentimentCacheRepository(db_path=db_path)
    result = repo.get_sentiment("UNKN", "2025-01-15")  # Valid 4-letter ticker
    assert result is None


def test_sentiment_cache_save_and_get(db_path):
    """Can save and retrieve sentiment."""
    repo = SentimentCacheRepository(db_path=db_path)

    sentiment = {
        "direction": "bullish",
        "score": 0.7,
        "tailwinds": "Strong AI demand",
        "headwinds": "China exposure",
        "raw": "Full response text",
    }

    result = repo.save_sentiment("NVDA", "2025-01-15", sentiment, ttl_hours=8)
    assert result is True

    cached = repo.get_sentiment("NVDA", "2025-01-15")
    assert cached is not None
    assert cached["direction"] == "bullish"
    assert cached["score"] == 0.7
    assert cached["tailwinds"] == "Strong AI demand"


def test_sentiment_cache_expires(db_path):
    """Expired sentiment not returned."""
    repo = SentimentCacheRepository(db_path=db_path)

    sentiment = {"direction": "bullish", "score": 0.5}

    # Save with 0 hours TTL (expires immediately)
    repo.save_sentiment("TEST", "2025-01-15", sentiment, ttl_hours=0)

    # Should be expired
    cached = repo.get_sentiment("TEST", "2025-01-15")
    assert cached is None


def test_sentiment_cache_clear_expired(db_path):
    """clear_expired removes old entries."""
    repo = SentimentCacheRepository(db_path=db_path)

    # Directly insert an expired entry (can't use save_sentiment with negative TTL)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO sentiment_cache
        (ticker, earnings_date, direction, score, created_at, expires_at)
        VALUES ('OLD', '2025-01-01', 'neutral', 0, datetime('now', '-1 day'), datetime('now', '-1 hour'))
    """)
    conn.commit()
    conn.close()

    # Save fresh entry
    repo.save_sentiment("NEW", "2025-01-15", {"direction": "bullish"}, ttl_hours=24)

    cleared = repo.clear_expired()
    assert cleared >= 1

    # NEW should remain
    assert repo.get_sentiment("NEW", "2025-01-15") is not None


def test_sentiment_cache_clear_all(db_path):
    """clear_all removes all entries."""
    repo = SentimentCacheRepository(db_path=db_path)

    repo.save_sentiment("A", "2025-01-15", {"direction": "bullish"}, ttl_hours=24)
    repo.save_sentiment("B", "2025-01-15", {"direction": "bearish"}, ttl_hours=24)

    cleared = repo.clear_all()
    assert cleared == 2

    assert repo.get_sentiment("A", "2025-01-15") is None
    assert repo.get_sentiment("B", "2025-01-15") is None
