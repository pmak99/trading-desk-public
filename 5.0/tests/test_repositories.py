# 5.0/tests/test_repositories.py
"""Tests for domain repositories."""

import pytest
import tempfile
import os
import sqlite3
from datetime import date, timedelta
from src.domain.repositories import HistoricalMovesRepository, SentimentCacheRepository, VRPCacheRepository


@pytest.fixture
def db_path():
    """Create temp database with schema matching production."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    # Create historical_moves table (matching production schema)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE historical_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            earnings_date DATE NOT NULL,
            prev_close REAL,
            earnings_open REAL,
            earnings_high REAL,
            earnings_low REAL,
            earnings_close REAL,
            intraday_move_pct REAL,
            gap_move_pct REAL,
            close_move_pct REAL,
            UNIQUE(ticker, earnings_date)
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

    # Save multiple moves with intraday_move_pct (default metric)
    for i, pct in enumerate([5.0, -3.0, 8.0, -2.0]):
        repo.save_move({
            "ticker": "AAPL",
            "earnings_date": f"2024-{i+1:02d}-15",
            "gap_move_pct": pct,
            "intraday_move_pct": pct,  # Default metric uses intraday
        })

    avg = repo.get_average_move("AAPL")
    # (5 + 3 + 8 + 2) / 4 = 4.5
    assert avg == 4.5

    # Also test gap metric explicitly
    avg_gap = repo.get_average_move("AAPL", metric="gap")
    assert avg_gap == 4.5


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


def test_get_tracked_tickers_empty(db_path):
    """get_tracked_tickers returns empty set when no historical moves exist."""
    repo = HistoricalMovesRepository(db_path=db_path)
    tracked = repo.get_tracked_tickers()
    assert tracked == set()


def test_get_tracked_tickers_returns_distinct(db_path):
    """get_tracked_tickers returns distinct tickers from historical_moves."""
    repo = HistoricalMovesRepository(db_path=db_path)

    # Save multiple moves for same ticker (should only appear once)
    repo.save_move({"ticker": "AAPL", "earnings_date": "2025-01-15", "gap_move_pct": 5.0})
    repo.save_move({"ticker": "AAPL", "earnings_date": "2025-04-15", "gap_move_pct": -3.0})
    repo.save_move({"ticker": "NVDA", "earnings_date": "2025-01-15", "gap_move_pct": 8.0})
    repo.save_move({"ticker": "MSFT", "earnings_date": "2025-01-15", "gap_move_pct": 2.0})

    tracked = repo.get_tracked_tickers()

    assert tracked == {"AAPL", "NVDA", "MSFT"}
    assert len(tracked) == 3  # AAPL only counted once


def test_get_tracked_tickers_used_for_filtering(db_path):
    """get_tracked_tickers integrates with filter_to_tracked_tickers helper."""
    from src.jobs.handlers import filter_to_tracked_tickers

    repo = HistoricalMovesRepository(db_path=db_path)

    # Add some tracked tickers
    repo.save_move({"ticker": "AAPL", "earnings_date": "2025-01-15", "gap_move_pct": 5.0})
    repo.save_move({"ticker": "NVDA", "earnings_date": "2025-01-15", "gap_move_pct": 8.0})

    tracked = repo.get_tracked_tickers()

    # Simulated Alpha Vantage earnings (includes OTC tickers)
    earnings = [
        {"symbol": "AAPL", "report_date": "2025-01-20"},
        {"symbol": "NVDA", "report_date": "2025-01-20"},
        {"symbol": "CUIRF", "report_date": "2025-01-20"},  # OTC - not tracked
        {"symbol": "UNKNOWN", "report_date": "2025-01-20"},  # Not tracked
    ]

    filtered = filter_to_tracked_tickers(earnings, tracked)

    assert len(filtered) == 2
    symbols = [e["symbol"] for e in filtered]
    assert "AAPL" in symbols
    assert "NVDA" in symbols
    assert "CUIRF" not in symbols
    assert "UNKNOWN" not in symbols


# Sentiment Cache Tests
#
# Schema fixed 2026-07-27: SentimentCacheRepository previously expected a
# schema (id, earnings_date, direction, score, tailwinds, headwinds,
# raw_response, created_at, expires_at) that was never actually applied to
# the shared ivcrush.db -- the real table (populated and actively used by
# 2.0) is (ticker, date, source, sentiment [JSON blob], cached_at),
# PRIMARY KEY (ticker, date). Every test below now inserts/asserts against
# that real schema.

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
    assert cached["headwinds"] == "China exposure"
    assert cached["source"] == "perplexity"  # default


def test_sentiment_cache_save_records_source(db_path):
    """save_sentiment's source param is stored and round-trips."""
    repo = SentimentCacheRepository(db_path=db_path)
    repo.save_sentiment("AAPL", "2025-01-15", {"direction": "neutral", "score": 0.1}, source="council")
    cached = repo.get_sentiment("AAPL", "2025-01-15")
    assert cached["source"] == "council"


def test_sentiment_cache_reads_2point0_catalysts_risks_convention(db_path):
    """
    A row written by 2.0 (catalysts/risks keys, no tailwinds/headwinds)
    must still be readable by 5.0's callers via the tailwinds/headwinds
    keys they expect -- this is the whole point of sharing one table.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sentiment_cache (
            ticker TEXT NOT NULL, date TEXT NOT NULL, source TEXT NOT NULL,
            sentiment TEXT NOT NULL, cached_at TEXT NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)
    import json as _json
    conn.execute(
        "INSERT INTO sentiment_cache (ticker, date, source, sentiment, cached_at) "
        "VALUES ('PYPL', '2025-01-15', 'perplexity', ?, datetime('now'))",
        (_json.dumps({
            "direction": "bearish", "score": 0.3,
            "catalysts": "Regulatory tailwind", "risks": "Margin compression",
        }),),
    )
    conn.commit()
    conn.close()

    repo = SentimentCacheRepository(db_path=db_path)
    cached = repo.get_sentiment("PYPL", "2025-01-15")
    assert cached is not None
    assert cached["tailwinds"] == "Regulatory tailwind"
    assert cached["headwinds"] == "Margin compression"


def test_sentiment_cache_writes_catalysts_risks_for_2point0_interop(db_path):
    """save_sentiment stores under catalysts/risks so 2.0 skill queries (which
    expect that convention) can read entries 5.0 writes."""
    repo = SentimentCacheRepository(db_path=db_path)
    repo.save_sentiment("MSFT", "2025-01-15", {
        "direction": "bullish", "score": 0.6,
        "tailwinds": "Cloud growth", "headwinds": "FX headwind",
    })

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT sentiment FROM sentiment_cache WHERE ticker='MSFT' AND date='2025-01-15'"
    ).fetchone()
    conn.close()

    import json as _json
    payload = _json.loads(row[0])
    assert payload["catalysts"] == "Cloud growth"
    assert payload["risks"] == "FX headwind"


def test_sentiment_cache_expires_via_read_time_ttl(db_path):
    """
    TTL is enforced at READ time against cached_at, not stored per-row
    (the shared table has no expires_at column). ttl_hours=0 on the read
    means "must have been cached in the last 0 hours" -- anything just
    written is already outside that window.
    """
    repo = SentimentCacheRepository(db_path=db_path)
    repo.save_sentiment("TEST", "2025-01-15", {"direction": "bullish", "score": 0.5})

    cached = repo.get_sentiment("TEST", "2025-01-15", ttl_hours=0)
    assert cached is None

    # Same row, wide TTL window -- still there.
    cached = repo.get_sentiment("TEST", "2025-01-15", ttl_hours=24)
    assert cached is not None


def test_sentiment_cache_clear_expired(db_path):
    """clear_expired removes rows older than the cleanup threshold (default 24h)."""
    repo = SentimentCacheRepository(db_path=db_path)

    # Directly insert a stale row using the real schema.
    conn = sqlite3.connect(db_path)
    import json as _json
    conn.execute(
        "INSERT INTO sentiment_cache (ticker, date, source, sentiment, cached_at) "
        "VALUES ('OLD', '2025-01-01', 'perplexity', ?, datetime('now', '-2 days'))",
        (_json.dumps({"direction": "neutral", "score": 0}),),
    )
    conn.commit()
    conn.close()

    # Save fresh entry
    repo.save_sentiment("NEW", "2025-01-15", {"direction": "bullish"})

    cleared = repo.clear_expired()
    assert cleared >= 1

    # NEW should remain
    assert repo.get_sentiment("NEW", "2025-01-15") is not None


def test_sentiment_cache_clear_all(db_path):
    """clear_all removes all entries."""
    repo = SentimentCacheRepository(db_path=db_path)

    repo.save_sentiment("A", "2025-01-15", {"direction": "bullish"})
    repo.save_sentiment("B", "2025-01-15", {"direction": "bearish"})

    cleared = repo.clear_all()
    assert cleared == 2

    assert repo.get_sentiment("A", "2025-01-15") is None
    assert repo.get_sentiment("B", "2025-01-15") is None


# Position Limits Tests (TRR Feature)

@pytest.fixture
def db_with_position_limits():
    """Create temp database with position_limits table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = sqlite3.connect(path)
    # Create required tables
    conn.execute("""
        CREATE TABLE historical_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            earnings_date DATE NOT NULL,
            prev_close REAL,
            earnings_open REAL,
            earnings_high REAL,
            earnings_low REAL,
            earnings_close REAL,
            intraday_move_pct REAL,
            gap_move_pct REAL,
            close_move_pct REAL,
            UNIQUE(ticker, earnings_date)
        )
    """)
    conn.execute("""
        CREATE TABLE position_limits (
            ticker TEXT PRIMARY KEY,
            tail_risk_ratio REAL,
            tail_risk_level TEXT,
            max_contracts INTEGER,
            max_notional INTEGER,
            avg_move REAL,
            max_move REAL,
            num_quarters INTEGER
        )
    """)
    # Insert test data - MU as HIGH tail risk
    conn.execute("""
        INSERT INTO position_limits VALUES
        ('MU', 3.05, 'HIGH', 50, 25000, 3.68, 11.21, 12),
        ('AAPL', 1.45, 'LOW', 100, 50000, 4.5, 6.5, 12),
        ('NVDA', 2.1, 'NORMAL', 100, 50000, 5.0, 10.5, 12)
    """)
    conn.commit()
    conn.close()

    yield path
    os.unlink(path)


def test_get_position_limits_found(db_with_position_limits):
    """get_position_limits returns data for known ticker."""
    repo = HistoricalMovesRepository(db_path=db_with_position_limits)

    limits = repo.get_position_limits("MU")

    assert limits is not None
    assert limits["ticker"] == "MU"
    assert limits["tail_risk_ratio"] == 3.05
    assert limits["tail_risk_level"] == "HIGH"
    assert limits["max_contracts"] == 50
    assert limits["max_notional"] == 25000


def test_get_position_limits_not_found(db_with_position_limits):
    """get_position_limits returns None for unknown ticker."""
    repo = HistoricalMovesRepository(db_path=db_with_position_limits)

    limits = repo.get_position_limits("UNKN")

    assert limits is None


def test_get_position_limits_missing_table(db_path):
    """get_position_limits gracefully handles missing table."""
    # db_path fixture doesn't have position_limits table
    repo = HistoricalMovesRepository(db_path=db_path)

    # Should return None, not raise exception
    limits = repo.get_position_limits("MU")

    assert limits is None


def test_get_position_limits_validates_ticker(db_with_position_limits):
    """get_position_limits validates ticker format."""
    repo = HistoricalMovesRepository(db_path=db_with_position_limits)

    # Invalid ticker should raise ValueError
    with pytest.raises(ValueError):
        repo.get_position_limits("invalid_ticker!")

    with pytest.raises(ValueError):
        repo.get_position_limits("TOOLONG")


# Batch Moves Tests (Performance Optimization)

def test_get_moves_batch_empty(db_path):
    """get_moves_batch returns empty dict for empty ticker list."""
    repo = HistoricalMovesRepository(db_path=db_path)
    result = repo.get_moves_batch([])
    assert result == {}


def test_get_moves_batch_single(db_path):
    """get_moves_batch works with single ticker."""
    repo = HistoricalMovesRepository(db_path=db_path)

    repo.save_move({
        "ticker": "NVDA",
        "earnings_date": "2025-02-15",
        "gap_move_pct": 8.5,
        "intraday_move_pct": 10.2,
    })

    result = repo.get_moves_batch(["NVDA"])

    assert "NVDA" in result
    assert len(result["NVDA"]) == 1
    assert result["NVDA"][0]["gap_move_pct"] == 8.5


def test_get_moves_batch_multiple(db_path):
    """get_moves_batch fetches multiple tickers in one query."""
    repo = HistoricalMovesRepository(db_path=db_path)

    # Save moves for 3 tickers
    for ticker, move_pct in [("AAPL", 5.0), ("NVDA", 8.5), ("MSFT", 3.0)]:
        repo.save_move({
            "ticker": ticker,
            "earnings_date": "2025-02-15",
            "gap_move_pct": move_pct,
            "intraday_move_pct": move_pct * 1.2,
        })

    result = repo.get_moves_batch(["AAPL", "NVDA", "MSFT"])

    assert len(result) == 3
    assert result["AAPL"][0]["gap_move_pct"] == 5.0
    assert result["NVDA"][0]["gap_move_pct"] == 8.5
    assert result["MSFT"][0]["gap_move_pct"] == 3.0


def test_get_moves_batch_unknown_ticker(db_path):
    """get_moves_batch returns empty list for unknown tickers."""
    repo = HistoricalMovesRepository(db_path=db_path)

    repo.save_move({
        "ticker": "NVDA",
        "earnings_date": "2025-02-15",
        "gap_move_pct": 8.5,
        "intraday_move_pct": 10.2,
    })

    result = repo.get_moves_batch(["NVDA", "UNKN"])

    assert len(result["NVDA"]) == 1
    assert len(result["UNKN"]) == 0


def test_get_moves_batch_respects_limit(db_path):
    """get_moves_batch respects per-ticker limit."""
    repo = HistoricalMovesRepository(db_path=db_path)

    # Save 5 moves for same ticker
    for i in range(5):
        repo.save_move({
            "ticker": "AAPL",
            "earnings_date": f"2025-0{i+1}-15",
            "gap_move_pct": i * 1.5,
            "intraday_move_pct": i * 2.0,
        })

    # Request limit of 3
    result = repo.get_moves_batch(["AAPL"], limit=3)

    assert len(result["AAPL"]) == 3


# VRP Cache Tests (Performance Optimization)

def test_vrp_cache_empty(db_path):
    """get_vrp returns None for uncached ticker."""
    repo = VRPCacheRepository(db_path=db_path)
    result = repo.get_vrp("NVDA", "2025-01-15")
    assert result is None


def test_vrp_cache_save_and_get(db_path):
    """Can save and retrieve VRP data."""
    repo = VRPCacheRepository(db_path=db_path)

    vrp_data = {
        "implied_move_pct": 8.5,
        "vrp_ratio": 2.1,
        "vrp_tier": "EXCELLENT",
        "historical_mean": 4.05,
        "price": 500.0,
        "expiration": "2025-01-17",
        "used_real_data": True,
    }

    result = repo.save_vrp("NVDA", "2025-01-15", vrp_data)
    assert result is True

    cached = repo.get_vrp("NVDA", "2025-01-15")
    assert cached is not None
    assert cached["vrp_ratio"] == 2.1
    assert cached["vrp_tier"] == "EXCELLENT"
    assert cached["implied_move_pct"] == 8.5
    assert cached["used_real_data"] is True
    assert cached["from_cache"] is True  # Indicates this came from cache


def test_vrp_cache_expires(db_path):
    """Expired VRP not returned."""
    repo = VRPCacheRepository(db_path=db_path)

    # Manually insert an expired entry
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO vrp_cache
        (ticker, earnings_date, implied_move_pct, vrp_ratio, vrp_tier,
         created_at, expires_at)
        VALUES ('TEST', '2025-01-15', 8.5, 2.1, 'EXCELLENT',
                datetime('now', '-1 day'), datetime('now', '-1 hour'))
    """)
    conn.commit()
    conn.close()

    # Should be expired
    cached = repo.get_vrp("TEST", "2025-01-15")
    assert cached is None


def test_vrp_cache_clear_expired(db_path):
    """clear_expired removes old entries."""
    repo = VRPCacheRepository(db_path=db_path)

    # Insert expired entry directly
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO vrp_cache
        (ticker, earnings_date, implied_move_pct, vrp_ratio, vrp_tier,
         created_at, expires_at)
        VALUES ('OLD', '2025-01-01', 5.0, 1.5, 'GOOD',
                datetime('now', '-1 day'), datetime('now', '-1 hour'))
    """)
    conn.commit()
    conn.close()

    # Save fresh entry
    repo.save_vrp("NEW", "2025-01-15", {
        "implied_move_pct": 8.5,
        "vrp_ratio": 2.1,
        "vrp_tier": "EXCELLENT"
    })

    cleared = repo.clear_expired()
    assert cleared >= 1

    # NEW should remain
    assert repo.get_vrp("NEW", "2025-01-15") is not None


def test_vrp_cache_clear_all(db_path):
    """clear_all removes all entries."""
    repo = VRPCacheRepository(db_path=db_path)

    repo.save_vrp("A", "2025-01-15", {"implied_move_pct": 5.0, "vrp_ratio": 1.5, "vrp_tier": "GOOD"})
    repo.save_vrp("B", "2025-01-15", {"implied_move_pct": 8.0, "vrp_ratio": 2.0, "vrp_tier": "EXCELLENT"})

    cleared = repo.clear_all()
    assert cleared == 2

    assert repo.get_vrp("A", "2025-01-15") is None
    assert repo.get_vrp("B", "2025-01-15") is None


def test_vrp_cache_stats(db_path):
    """get_cache_stats returns correct statistics."""
    repo = VRPCacheRepository(db_path=db_path)

    # Save some entries
    repo.save_vrp("A", "2025-01-15", {"implied_move_pct": 5.0, "vrp_ratio": 1.5, "vrp_tier": "GOOD"})
    repo.save_vrp("B", "2025-01-15", {"implied_move_pct": 8.0, "vrp_ratio": 2.0, "vrp_tier": "EXCELLENT"})

    stats = repo.get_cache_stats()

    assert stats["total_entries"] == 2
    assert stats["valid_entries"] == 2
    assert stats["expired_entries"] == 0


def test_vrp_cache_ttl_near_earnings(db_path, monkeypatch):
    """TTL should be 1 hour when earnings <= 3 days away."""
    repo = VRPCacheRepository(db_path=db_path)

    # Mock today_et in the config module (where it's imported from)
    monkeypatch.setattr("src.core.config.today_et", lambda: "2025-01-15")

    # Earnings tomorrow (1 day away) - should use NEAR TTL
    ttl = repo._calculate_ttl_hours("2025-01-16")
    assert ttl == VRPCacheRepository.TTL_HOURS_NEAR  # 1 hour

    # Earnings in 3 days - should still use NEAR TTL (boundary)
    ttl = repo._calculate_ttl_hours("2025-01-18")
    assert ttl == VRPCacheRepository.TTL_HOURS_NEAR  # 1 hour

    # Earnings today (0 days away) - should use NEAR TTL
    ttl = repo._calculate_ttl_hours("2025-01-15")
    assert ttl == VRPCacheRepository.TTL_HOURS_NEAR  # 1 hour


def test_vrp_cache_ttl_far_earnings(db_path, monkeypatch):
    """TTL should be 6 hours when earnings > 3 days away."""
    repo = VRPCacheRepository(db_path=db_path)

    # Mock today_et in the config module (where it's imported from)
    monkeypatch.setattr("src.core.config.today_et", lambda: "2025-01-15")

    # Earnings in 4 days - should use FAR TTL
    ttl = repo._calculate_ttl_hours("2025-01-19")
    assert ttl == VRPCacheRepository.TTL_HOURS_FAR  # 6 hours

    # Earnings in 7 days - should use FAR TTL
    ttl = repo._calculate_ttl_hours("2025-01-22")
    assert ttl == VRPCacheRepository.TTL_HOURS_FAR  # 6 hours


def test_vrp_cache_ttl_invalid_date(db_path):
    """TTL should default to NEAR (1 hour) for invalid dates."""
    repo = VRPCacheRepository(db_path=db_path)

    # Invalid date format - should default to NEAR TTL
    ttl = repo._calculate_ttl_hours("invalid-date")
    assert ttl == VRPCacheRepository.TTL_HOURS_NEAR

    # Empty string - should default to NEAR TTL
    ttl = repo._calculate_ttl_hours("")
    assert ttl == VRPCacheRepository.TTL_HOURS_NEAR


def test_vrp_cache_overwrite_existing(db_path):
    """Saving VRP data for existing ticker/date overwrites the entry."""
    repo = VRPCacheRepository(db_path=db_path)

    # Save initial data
    vrp_data = {
        "implied_move_pct": 8.5,
        "vrp_ratio": 2.1,
        "vrp_tier": "EXCELLENT",
        "historical_mean": 4.05,
        "price": 500.0,
        "expiration": "2025-01-17",
        "used_real_data": True,
    }
    repo.save_vrp("AAPL", "2025-01-15", vrp_data)

    # Overwrite with new data
    vrp_data_updated = {
        "implied_move_pct": 10.0,
        "vrp_ratio": 2.5,
        "vrp_tier": "EXCELLENT",
        "historical_mean": 4.0,
        "price": 510.0,
        "expiration": "2025-01-17",
        "used_real_data": True,
    }
    repo.save_vrp("AAPL", "2025-01-15", vrp_data_updated)

    # Should get updated values
    cached = repo.get_vrp("AAPL", "2025-01-15")
    assert cached["vrp_ratio"] == 2.5
    assert cached["implied_move_pct"] == 10.0
    assert cached["price"] == 510.0


def test_vrp_cache_missing_optional_fields(db_path):
    """save_vrp handles missing optional fields with defaults."""
    repo = VRPCacheRepository(db_path=db_path)

    # Minimal required fields only
    vrp_data = {
        "implied_move_pct": 8.5,
        "vrp_ratio": 2.1,
        "vrp_tier": "GOOD",
    }
    result = repo.save_vrp("META", "2025-01-20", vrp_data)
    assert result is True

    cached = repo.get_vrp("META", "2025-01-20")
    assert cached is not None
    assert cached["vrp_ratio"] == 2.1


def test_vrp_cache_case_insensitive_ticker(db_path):
    """Ticker lookup is case-insensitive (normalized to uppercase)."""
    repo = VRPCacheRepository(db_path=db_path)

    vrp_data = {
        "implied_move_pct": 5.0,
        "vrp_ratio": 1.8,
        "vrp_tier": "EXCELLENT",
        "historical_mean": 2.8,
        "price": 150.0,
        "expiration": "2025-01-17",
        "used_real_data": True,
    }

    # Save with lowercase
    repo.save_vrp("msft", "2025-01-15", vrp_data)

    # Retrieve with uppercase
    cached = repo.get_vrp("MSFT", "2025-01-15")
    assert cached is not None
    assert cached["ticker"] == "MSFT"  # Should be normalized


def test_vrp_cache_different_earnings_dates(db_path):
    """Same ticker can have different cached VRP for different earnings dates."""
    repo = VRPCacheRepository(db_path=db_path)

    vrp_data_jan = {
        "implied_move_pct": 8.0,
        "vrp_ratio": 2.0,
        "vrp_tier": "EXCELLENT",
        "historical_mean": 4.0,
        "price": 500.0,
        "expiration": "2025-01-17",
        "used_real_data": True,
    }
    vrp_data_apr = {
        "implied_move_pct": 6.0,
        "vrp_ratio": 1.5,
        "vrp_tier": "GOOD",
        "historical_mean": 4.0,
        "price": 520.0,
        "expiration": "2025-04-18",
        "used_real_data": True,
    }

    repo.save_vrp("NVDA", "2025-01-15", vrp_data_jan)
    repo.save_vrp("NVDA", "2025-04-15", vrp_data_apr)

    cached_jan = repo.get_vrp("NVDA", "2025-01-15")
    cached_apr = repo.get_vrp("NVDA", "2025-04-15")

    assert cached_jan["vrp_ratio"] == 2.0
    assert cached_apr["vrp_ratio"] == 1.5


# Input Validation Tests

from src.domain.repositories import is_valid_ticker, validate_days


class TestIsValidTicker:
    """Tests for is_valid_ticker boolean validation function."""

    def test_valid_standard_tickers(self):
        """Standard tickers should be valid."""
        assert is_valid_ticker("AAPL") is True
        assert is_valid_ticker("NVDA") is True
        assert is_valid_ticker("A") is True  # Single letter
        assert is_valid_ticker("META") is True

    def test_valid_dotted_tickers(self):
        """Dotted tickers like BRK.B should be valid."""
        assert is_valid_ticker("BRK.B") is True
        assert is_valid_ticker("BF.A") is True
        assert is_valid_ticker("BRK.A") is True

    def test_invalid_preferred_stocks(self):
        """Preferred stocks with dash should be invalid."""
        assert is_valid_ticker("COF-PI") is False
        assert is_valid_ticker("BAC-PB") is False
        assert is_valid_ticker("WFC-PL") is False

    def test_invalid_warrants(self):
        """Warrants with + should be invalid."""
        assert is_valid_ticker("ACHR+") is False
        assert is_valid_ticker("SPAC+") is False

    def test_dotted_suffixes_valid(self):
        """Dotted suffixes (.U, .WS) match the pattern for BRK.B style tickers."""
        # These match the regex pattern - if filtering needed, do it elsewhere
        assert is_valid_ticker("SPAC.U") is True  # Unit shares
        assert is_valid_ticker("TEST.W") is True  # Some warrants

    def test_invalid_too_long(self):
        """Tickers longer than 5 chars should be invalid."""
        assert is_valid_ticker("TOOLONG") is False
        assert is_valid_ticker("ABCDEF") is False

    def test_invalid_empty_or_none(self):
        """Empty strings should be invalid."""
        assert is_valid_ticker("") is False
        assert is_valid_ticker("   ") is False

    def test_case_insensitive(self):
        """Should normalize to uppercase."""
        assert is_valid_ticker("aapl") is True
        assert is_valid_ticker("Nvda") is True

    def test_whitespace_handling(self):
        """Should strip whitespace."""
        assert is_valid_ticker(" AAPL ") is True
        assert is_valid_ticker("  NVDA") is True


class TestValidateDays:
    """Tests for validate_days bounds checking function."""

    def test_valid_days(self):
        """Days within bounds should pass."""
        assert validate_days(1) == 1
        assert validate_days(5) == 5
        assert validate_days(30) == 30
        assert validate_days(365) == 365

    def test_invalid_zero(self):
        """Zero days should raise ValueError."""
        with pytest.raises(ValueError) as exc:
            validate_days(0)
        assert "must be 1-365" in str(exc.value)

    def test_invalid_negative(self):
        """Negative days should raise ValueError."""
        with pytest.raises(ValueError) as exc:
            validate_days(-1)
        assert "must be 1-365" in str(exc.value)

    def test_invalid_too_large(self):
        """Days > 365 should raise ValueError."""
        with pytest.raises(ValueError) as exc:
            validate_days(366)
        assert "must be 1-365" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            validate_days(1000)
        assert "must be 1-365" in str(exc.value)


# Earnings Calendar Tests

@pytest.fixture
def db_with_earnings_calendar():
    """Create temp database with earnings_calendar table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = sqlite3.connect(path)
    # Create required tables
    conn.execute("""
        CREATE TABLE historical_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            earnings_date DATE NOT NULL,
            prev_close REAL,
            earnings_open REAL,
            earnings_high REAL,
            earnings_low REAL,
            earnings_close REAL,
            intraday_move_pct REAL,
            gap_move_pct REAL,
            close_move_pct REAL,
            UNIQUE(ticker, earnings_date)
        )
    """)
    conn.execute("""
        CREATE TABLE earnings_calendar (
            ticker TEXT NOT NULL,
            earnings_date DATE NOT NULL,
            timing TEXT,
            confirmed INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, earnings_date)
        )
    """)
    conn.commit()
    conn.close()

    yield path
    os.unlink(path)


class TestUpsertEarningsCalendar:
    """Tests for upsert_earnings_calendar method."""

    def test_upsert_empty_list(self, db_with_earnings_calendar):
        """Empty list should return 0."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)
        count = repo.upsert_earnings_calendar([])
        assert count == 0

    def test_upsert_valid_records(self, db_with_earnings_calendar):
        """Valid records should be inserted."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"symbol": "NVDA", "report_date": "2025-01-21", "timing": "BMO"},
            {"symbol": "MSFT", "report_date": "2025-01-22", "timing": "UNKNOWN"},
        ]

        count = repo.upsert_earnings_calendar(earnings)
        assert count == 3

    def test_upsert_filters_invalid_tickers(self, db_with_earnings_calendar):
        """Invalid tickers should be skipped."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"symbol": "COF-PI", "report_date": "2025-01-21", "timing": "BMO"},  # Preferred - skip
            {"symbol": "ACHR+", "report_date": "2025-01-22", "timing": "BMO"},   # Warrant - skip
            {"symbol": "NVDA", "report_date": "2025-01-23", "timing": "AMC"},
        ]

        count = repo.upsert_earnings_calendar(earnings)
        assert count == 2  # Only AAPL and NVDA

    def test_upsert_filters_invalid_dates(self, db_with_earnings_calendar):
        """Invalid dates should be skipped."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"symbol": "NVDA", "report_date": "invalid-date", "timing": "BMO"},  # Invalid - skip
            {"symbol": "MSFT", "report_date": "", "timing": "BMO"},              # Empty - skip
        ]

        count = repo.upsert_earnings_calendar(earnings)
        assert count == 1  # Only AAPL

    def test_upsert_handles_missing_fields(self, db_with_earnings_calendar):
        """Records with missing fields should be skipped."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"report_date": "2025-01-21", "timing": "BMO"},           # Missing symbol
            {"symbol": "MSFT", "timing": "BMO"},                       # Missing date
        ]

        count = repo.upsert_earnings_calendar(earnings)
        assert count == 1  # Only AAPL

    def test_upsert_replaces_existing(self, db_with_earnings_calendar):
        """Existing records should be updated (INSERT OR REPLACE)."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        # Insert first
        earnings1 = [{"symbol": "AAPL", "report_date": "2025-01-20", "timing": "BMO"}]
        count1 = repo.upsert_earnings_calendar(earnings1)
        assert count1 == 1

        # Update with new timing
        earnings2 = [{"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"}]
        count2 = repo.upsert_earnings_calendar(earnings2)
        assert count2 == 1

        # Verify only one record exists with updated timing
        result = repo.get_earnings_by_date("2025-01-20")
        assert len(result) == 1
        assert result[0]["timing"] == "AMC"

    def test_upsert_handles_none_timing(self, db_with_earnings_calendar):
        """None timing should default to UNKNOWN."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [{"symbol": "AAPL", "report_date": "2025-01-20", "timing": None}]
        count = repo.upsert_earnings_calendar(earnings)
        assert count == 1

        result = repo.get_earnings_by_date("2025-01-20")
        assert result[0]["timing"] == "UNKNOWN"


class TestUpsertEarningsCalendarConfirmedProtection:
    """Fixed 2026-07-27: this weekly job used to unconditionally
    INSERT OR REPLACE from Finnhub alone -- no cross-check of any kind,
    could silently overwrite an already-confirmed (cross-validated
    elsewhere) date. Reproduces the PYPL incident shape: a confirmed row
    exists at the correct date; a single-source Finnhub re-fetch proposes
    a different, wrong date for the same event.
    """

    def _seed_confirmed(self, db_path, ticker, earnings_date, timing="BMO"):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed) "
            "VALUES (?, ?, ?, 1)",
            (ticker, earnings_date, timing),
        )
        conn.commit()
        conn.close()

    def test_confirmed_row_at_same_date_different_timing_is_skipped(self, db_with_earnings_calendar):
        # Same date, only timing differs -- still a change to a confirmed
        # fact from one uncorroborated source, must be skipped.
        self._seed_confirmed(db_with_earnings_calendar, "PYPL", "2026-07-28", timing="BMO")
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        count = repo.upsert_earnings_calendar(
            [{"symbol": "PYPL", "report_date": "2026-07-28", "timing": "AMC"}]
        )

        assert count == 0
        result = repo.get_earnings_by_date("2026-07-28")
        assert result[0]["timing"] == "BMO"  # unchanged

    def test_confirmed_row_different_date_new_finnhub_date_skipped(self, db_with_earnings_calendar):
        # The actual PYPL shape: confirmed date, single-source Finnhub bulk
        # fetch proposes a date 1 day earlier for the same ticker -- must not
        # create or apply the wrong date. Dates must stay relative to today
        # (not the real PYPL incident's literal 2026-07-28/07-27) because the
        # guard being tested only protects UPCOMING confirmed rows
        # (earnings_date >= date('now')) -- a hardcoded past-tense pair here
        # silently stops exercising that branch once real time passes it by.
        confirmed_date = (date.today() + timedelta(days=5)).isoformat()
        wrong_date = (date.today() + timedelta(days=4)).isoformat()
        self._seed_confirmed(db_with_earnings_calendar, "PYPL", confirmed_date, timing="BMO")
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        count = repo.upsert_earnings_calendar(
            [{"symbol": "PYPL", "report_date": wrong_date, "timing": "BMO"}]
        )

        assert count == 0
        conn = sqlite3.connect(db_with_earnings_calendar)
        rows = conn.execute(
            "SELECT earnings_date, confirmed FROM earnings_calendar WHERE ticker = 'PYPL'"
        ).fetchall()
        conn.close()
        assert rows == [(confirmed_date, 1)]  # original confirmed row untouched, no new row added

    def test_confirmed_row_matching_finnhub_date_still_updates(self, db_with_earnings_calendar):
        # Sources agree -- should still write (refreshes updated_at/timing
        # normally), not be blocked just because a confirmed row exists.
        self._seed_confirmed(db_with_earnings_calendar, "AAPL", "2025-01-20", timing="BMO")
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        count = repo.upsert_earnings_calendar(
            [{"symbol": "AAPL", "report_date": "2025-01-20", "timing": "BMO"}]
        )

        assert count == 1

    def test_unconfirmed_existing_row_still_overwritten(self, db_with_earnings_calendar):
        # Pre-fix behavior preserved for the common case: no confirmed
        # data to protect, single-source Finnhub writes go through as before.
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)
        repo.upsert_earnings_calendar(
            [{"symbol": "NVDA", "report_date": "2025-02-10", "timing": "AMC"}]
        )

        count = repo.upsert_earnings_calendar(
            [{"symbol": "NVDA", "report_date": "2025-02-11", "timing": "AMC"}]
        )

        assert count == 1
        result = repo.get_earnings_by_date("2025-02-11")
        assert len(result) == 1

    def test_no_prior_row_at_all_still_inserts(self, db_with_earnings_calendar):
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)
        count = repo.upsert_earnings_calendar(
            [{"symbol": "TSLA", "report_date": "2025-03-01", "timing": "AMC"}]
        )
        assert count == 1

    def test_confirmed_row_for_different_future_event_does_not_block_new_event(self, db_with_earnings_calendar):
        # A confirmed row exists for a NEAR-TERM earnings event; Finnhub's
        # bulk fetch also reports a genuinely different, much-LATER quarter
        # for the same ticker (>45d out -- outside CONFIRMED_EVENT_WINDOW_DAYS).
        # This must NOT be blocked -- it's a different event, not a
        # conflicting overwrite of the confirmed one.
        near_date = (date.today() + timedelta(days=5)).isoformat()
        far_date = (date.today() + timedelta(days=95)).isoformat()
        self._seed_confirmed(db_with_earnings_calendar, "MU", near_date, timing="AMC")
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        count = repo.upsert_earnings_calendar(
            [{"symbol": "MU", "report_date": far_date, "timing": "AMC"}]
        )

        assert count == 1
        conn = sqlite3.connect(db_with_earnings_calendar)
        rows = conn.execute(
            "SELECT earnings_date, confirmed FROM earnings_calendar WHERE ticker = 'MU' "
            "ORDER BY earnings_date"
        ).fetchall()
        conn.close()
        assert rows == [(near_date, 1), (far_date, 0)]

    def test_confirmed_row_for_nearby_future_event_blocks_near_duplicate(self, db_with_earnings_calendar):
        # The actual dangerous case: a confirmed row exists a few days out;
        # Finnhub's bulk fetch proposes a DIFFERENT date only 1-2 days away
        # for the same ticker (within CONFIRMED_EVENT_WINDOW_DAYS) -- this
        # looks like the same event with a wrong date and must be blocked,
        # exactly like the real PYPL 07-27-vs-07-28 shape.
        confirmed_date = (date.today() + timedelta(days=5)).isoformat()
        wrong_date = (date.today() + timedelta(days=6)).isoformat()
        self._seed_confirmed(db_with_earnings_calendar, "ZZZZ", confirmed_date, timing="AMC")
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        count = repo.upsert_earnings_calendar(
            [{"symbol": "ZZZZ", "report_date": wrong_date, "timing": "AMC"}]
        )

        assert count == 0
        conn = sqlite3.connect(db_with_earnings_calendar)
        rows = conn.execute(
            "SELECT earnings_date, confirmed FROM earnings_calendar WHERE ticker = 'ZZZZ'"
        ).fetchall()
        conn.close()
        assert rows == [(confirmed_date, 1)]  # no new row added


class TestGetEarningsByDate:
    """Tests for get_earnings_by_date method."""

    def test_get_earnings_found(self, db_with_earnings_calendar):
        """Should return earnings for specific date."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"symbol": "NVDA", "report_date": "2025-01-20", "timing": "BMO"},
            {"symbol": "MSFT", "report_date": "2025-01-21", "timing": "AMC"},
        ]
        repo.upsert_earnings_calendar(earnings)

        result = repo.get_earnings_by_date("2025-01-20")

        assert len(result) == 2
        symbols = [r["symbol"] for r in result]
        assert "AAPL" in symbols
        assert "NVDA" in symbols

    def test_get_earnings_not_found(self, db_with_earnings_calendar):
        """Should return empty list for date with no earnings."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        result = repo.get_earnings_by_date("2025-01-20")
        assert result == []

    def test_get_earnings_invalid_date(self, db_with_earnings_calendar):
        """Should raise ValueError for invalid date format."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        with pytest.raises(ValueError) as exc:
            repo.get_earnings_by_date("invalid-date")
        assert "Invalid date format" in str(exc.value)

    def test_get_earnings_returns_correct_format(self, db_with_earnings_calendar):
        """Should return dicts with expected keys."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [{"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"}]
        repo.upsert_earnings_calendar(earnings)

        result = repo.get_earnings_by_date("2025-01-20")

        assert len(result) == 1
        assert "symbol" in result[0]
        assert "report_date" in result[0]
        assert "timing" in result[0]
        assert "name" in result[0]
        assert result[0]["name"] == ""  # Not stored in DB


class TestGetUpcomingEarnings:
    """Tests for get_upcoming_earnings method."""

    def test_get_upcoming_found(self, db_with_earnings_calendar):
        """Should return earnings within date range."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"symbol": "NVDA", "report_date": "2025-01-22", "timing": "BMO"},
            {"symbol": "MSFT", "report_date": "2025-01-25", "timing": "AMC"},
            {"symbol": "GOOGL", "report_date": "2025-01-30", "timing": "AMC"},  # Outside 5 days
        ]
        repo.upsert_earnings_calendar(earnings)

        result = repo.get_upcoming_earnings("2025-01-20", days=5)

        assert len(result) == 3
        symbols = [r["symbol"] for r in result]
        assert "AAPL" in symbols
        assert "NVDA" in symbols
        assert "MSFT" in symbols
        assert "GOOGL" not in symbols

    def test_get_upcoming_empty_range(self, db_with_earnings_calendar):
        """Should return empty list for date range with no earnings."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        result = repo.get_upcoming_earnings("2025-01-20", days=5)
        assert result == []

    def test_get_upcoming_default_days(self, db_with_earnings_calendar):
        """Should default to 5 days."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"symbol": "NVDA", "report_date": "2025-01-25", "timing": "BMO"},  # Exactly 5 days
            {"symbol": "MSFT", "report_date": "2025-01-26", "timing": "AMC"},  # 6 days - excluded
        ]
        repo.upsert_earnings_calendar(earnings)

        result = repo.get_upcoming_earnings("2025-01-20")  # No days specified

        assert len(result) == 2
        symbols = [r["symbol"] for r in result]
        assert "AAPL" in symbols
        assert "NVDA" in symbols
        assert "MSFT" not in symbols

    def test_get_upcoming_custom_days(self, db_with_earnings_calendar):
        """Should respect custom days parameter."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        earnings = [
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"symbol": "NVDA", "report_date": "2025-01-22", "timing": "BMO"},
            {"symbol": "MSFT", "report_date": "2025-01-25", "timing": "AMC"},
        ]
        repo.upsert_earnings_calendar(earnings)

        result = repo.get_upcoming_earnings("2025-01-20", days=2)

        assert len(result) == 2
        symbols = [r["symbol"] for r in result]
        assert "AAPL" in symbols
        assert "NVDA" in symbols
        assert "MSFT" not in symbols

    def test_get_upcoming_invalid_date(self, db_with_earnings_calendar):
        """Should raise ValueError for invalid date format."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        with pytest.raises(ValueError) as exc:
            repo.get_upcoming_earnings("invalid-date")
        assert "Invalid date format" in str(exc.value)

    def test_get_upcoming_invalid_days(self, db_with_earnings_calendar):
        """Should raise ValueError for invalid days parameter."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        with pytest.raises(ValueError) as exc:
            repo.get_upcoming_earnings("2025-01-20", days=0)
        assert "must be 1-365" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            repo.get_upcoming_earnings("2025-01-20", days=-1)
        assert "must be 1-365" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            repo.get_upcoming_earnings("2025-01-20", days=366)
        assert "must be 1-365" in str(exc.value)

    def test_get_upcoming_ordered_by_date_and_ticker(self, db_with_earnings_calendar):
        """Should return results ordered by date, then ticker."""
        repo = HistoricalMovesRepository(db_path=db_with_earnings_calendar)

        # Insert in random order
        earnings = [
            {"symbol": "MSFT", "report_date": "2025-01-21", "timing": "AMC"},
            {"symbol": "AAPL", "report_date": "2025-01-20", "timing": "AMC"},
            {"symbol": "NVDA", "report_date": "2025-01-20", "timing": "BMO"},
        ]
        repo.upsert_earnings_calendar(earnings)

        result = repo.get_upcoming_earnings("2025-01-20", days=5)

        # Should be: AAPL (1/20), NVDA (1/20), MSFT (1/21)
        assert result[0]["symbol"] == "AAPL"
        assert result[1]["symbol"] == "NVDA"
        assert result[2]["symbol"] == "MSFT"
