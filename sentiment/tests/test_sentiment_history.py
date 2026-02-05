"""
Unit tests for 4.0 sentiment_history module.

Tests the permanent sentiment storage system for backtesting.
"""

import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "cache"))

from cache.sentiment_history import (
    SentimentDirection,
    SentimentRecord,
    SentimentHistory,
    record_sentiment,
    record_outcome,
    get_pending_outcomes,
    get_sentiment_stats,
)


class TestSentimentDirection:
    """Tests for SentimentDirection enum."""

    def test_direction_values(self):
        """SentimentDirection should have correct values."""
        assert SentimentDirection.BULLISH.value == "bullish"
        assert SentimentDirection.BEARISH.value == "bearish"
        assert SentimentDirection.NEUTRAL.value == "neutral"
        assert SentimentDirection.UNKNOWN.value == "unknown"

    def test_all_directions_present(self):
        """Should have exactly 4 directions."""
        assert len(SentimentDirection) == 4


class TestSentimentRecord:
    """Tests for SentimentRecord dataclass."""

    def test_has_outcome_false_initially(self):
        """has_outcome should be False when actual_move_pct is None."""
        record = SentimentRecord(
            ticker="NVDA",
            earnings_date="2025-12-09",
            collected_at=datetime.now(timezone.utc),
            source="perplexity",
            sentiment_text="Bullish outlook",
            sentiment_score=0.7,
            sentiment_direction=SentimentDirection.BULLISH,
            vrp_ratio=8.2,
            implied_move_pct=12.5
        )
        assert record.has_outcome is False

    def test_has_outcome_true_when_filled(self):
        """has_outcome should be True when actual_move_pct is set."""
        record = SentimentRecord(
            ticker="NVDA",
            earnings_date="2025-12-09",
            collected_at=datetime.now(timezone.utc),
            source="perplexity",
            sentiment_text="Bullish outlook",
            sentiment_score=0.7,
            sentiment_direction=SentimentDirection.BULLISH,
            vrp_ratio=8.2,
            implied_move_pct=12.5,
            actual_move_pct=5.2,
            actual_direction="UP"
        )
        assert record.has_outcome is True

    def test_default_values(self):
        """Post-earnings fields should default to None."""
        record = SentimentRecord(
            ticker="NVDA",
            earnings_date="2025-12-09",
            collected_at=datetime.now(timezone.utc),
            source="perplexity",
            sentiment_text="Bullish outlook",
            sentiment_score=0.7,
            sentiment_direction=SentimentDirection.BULLISH,
            vrp_ratio=8.2,
            implied_move_pct=12.5
        )
        assert record.actual_move_pct is None
        assert record.actual_direction is None
        assert record.prediction_correct is None
        assert record.trade_outcome is None


class TestSentimentHistory:
    """Tests for SentimentHistory class."""

    @pytest.fixture
    def temp_history(self, tmp_path):
        """Create a temporary history for testing."""
        db_path = tmp_path / "test_history.db"
        return SentimentHistory(db_path=db_path)

    def test_init_creates_database(self, tmp_path):
        """History initialization should create database file."""
        db_path = tmp_path / "new_history.db"
        history = SentimentHistory(db_path=db_path)
        assert db_path.exists()

    def test_init_creates_table(self, temp_history):
        """History should create sentiment_history table."""
        with sqlite3.connect(temp_history.db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='sentiment_history'
            """)
            assert cursor.fetchone() is not None

    def test_init_creates_indexes(self, temp_history):
        """History should create appropriate indexes."""
        with sqlite3.connect(temp_history.db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='index' AND name LIKE 'idx_history%'
            """)
            indexes = [row[0] for row in cursor.fetchall()]
            assert 'idx_history_date' in indexes
            assert 'idx_history_outcome' in indexes

    def test_valid_sources_constant(self, temp_history):
        """VALID_SOURCES should contain expected sources."""
        assert "perplexity" in temp_history.VALID_SOURCES
        assert "websearch" in temp_history.VALID_SOURCES
        assert "finnhub" in temp_history.VALID_SOURCES
        assert "manual" in temp_history.VALID_SOURCES
        assert len(temp_history.VALID_SOURCES) == 4


class TestRecordSentiment:
    """Tests for record_sentiment method."""

    @pytest.fixture
    def temp_history(self, tmp_path):
        """Create a temporary history for testing."""
        db_path = tmp_path / "test_history.db"
        return SentimentHistory(db_path=db_path)

    def test_record_basic_sentiment(self, temp_history):
        """Should record basic sentiment successfully."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bullish outlook on AI"
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record is not None
        assert record.ticker == "NVDA"
        assert record.sentiment_text == "Bullish outlook on AI"
        assert record.source == "perplexity"

    def test_record_with_all_fields(self, temp_history):
        """Should record sentiment with all optional fields."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bullish outlook",
            sentiment_score=0.7,
            sentiment_direction=SentimentDirection.BULLISH,
            vrp_ratio=8.2,
            implied_move_pct=12.5
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_score == 0.7
        assert record.sentiment_direction == SentimentDirection.BULLISH
        assert record.vrp_ratio == 8.2
        assert record.implied_move_pct == 12.5

    def test_record_invalid_source_raises(self, temp_history):
        """Should raise ValueError for invalid source."""
        with pytest.raises(ValueError) as excinfo:
            temp_history.record_sentiment(
                ticker="NVDA",
                earnings_date="2025-12-09",
                source="invalid_source",
                sentiment_text="Test"
            )
        assert "Invalid source" in str(excinfo.value)

    def test_record_uppercases_ticker(self, temp_history):
        """Should uppercase ticker when recording."""
        temp_history.record_sentiment(
            ticker="nvda",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Test"
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record is not None
        assert record.ticker == "NVDA"

    def test_record_replaces_existing(self, temp_history):
        """Should replace existing entry with same ticker/date."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Old sentiment"
        )

        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="websearch",
            sentiment_text="New sentiment"
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_text == "New sentiment"
        assert record.source == "websearch"

    def test_auto_detect_direction_bullish(self, temp_history):
        """Should auto-detect bullish direction from positive score."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Test",
            sentiment_score=0.5  # > 0.2, so bullish
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_direction == SentimentDirection.BULLISH

    def test_auto_detect_direction_bearish(self, temp_history):
        """Should auto-detect bearish direction from negative score."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Test",
            sentiment_score=-0.5  # < -0.2, so bearish
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_direction == SentimentDirection.BEARISH

    def test_auto_detect_direction_neutral(self, temp_history):
        """Should auto-detect neutral direction from near-zero score."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Test",
            sentiment_score=0.1  # Between -0.2 and 0.2
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_direction == SentimentDirection.NEUTRAL

    def test_explicit_direction_overrides(self, temp_history):
        """Explicit direction should not be overridden by score."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Test",
            sentiment_score=-0.5,  # Would auto-detect as bearish
            sentiment_direction=SentimentDirection.BULLISH  # But explicit bullish
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_direction == SentimentDirection.BULLISH


class TestRecordOutcome:
    """Tests for record_outcome method."""

    @pytest.fixture
    def temp_history(self, tmp_path):
        """Create a temporary history for testing."""
        db_path = tmp_path / "test_history.db"
        return SentimentHistory(db_path=db_path)

    def test_record_outcome_success(self, temp_history):
        """Should successfully record outcome."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bullish",
            sentiment_direction=SentimentDirection.BULLISH
        )

        result = temp_history.record_outcome(
            ticker="NVDA",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="UP",
            trade_outcome="WIN"
        )

        assert result is True
        record = temp_history.get("NVDA", "2025-12-09")
        assert record.actual_move_pct == 5.2
        assert record.actual_direction == "UP"
        assert record.trade_outcome == "WIN"

    def test_record_outcome_no_matching_sentiment(self, temp_history):
        """Should return False when no matching sentiment exists."""
        result = temp_history.record_outcome(
            ticker="MISSING",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="UP"
        )

        assert result is False

    def test_record_outcome_prediction_correct_bullish_up(self, temp_history):
        """Bullish prediction + UP direction = correct."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bullish",
            sentiment_direction=SentimentDirection.BULLISH
        )

        temp_history.record_outcome(
            ticker="NVDA",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="UP"
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.prediction_correct is True

    def test_record_outcome_prediction_correct_bearish_down(self, temp_history):
        """Bearish prediction + DOWN direction = correct."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bearish",
            sentiment_direction=SentimentDirection.BEARISH
        )

        temp_history.record_outcome(
            ticker="NVDA",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="DOWN"
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.prediction_correct is True

    def test_record_outcome_prediction_wrong_bullish_down(self, temp_history):
        """Bullish prediction + DOWN direction = wrong."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bullish",
            sentiment_direction=SentimentDirection.BULLISH
        )

        temp_history.record_outcome(
            ticker="NVDA",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="DOWN"
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.prediction_correct is False

    def test_record_outcome_prediction_none_for_neutral(self, temp_history):
        """Neutral prediction should have None for prediction_correct."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Neutral",
            sentiment_direction=SentimentDirection.NEUTRAL
        )

        temp_history.record_outcome(
            ticker="NVDA",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="UP"
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.prediction_correct is None

    def test_record_outcome_uppercases_direction(self, temp_history):
        """Should uppercase actual_direction."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bullish",
            sentiment_direction=SentimentDirection.BULLISH
        )

        temp_history.record_outcome(
            ticker="NVDA",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="up"  # lowercase
        )

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.actual_direction == "UP"

    def test_record_outcome_invalid_direction_raises(self, temp_history):
        """Should raise ValueError for invalid actual_direction."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bullish",
            sentiment_direction=SentimentDirection.BULLISH
        )

        with pytest.raises(ValueError) as excinfo:
            temp_history.record_outcome(
                ticker="NVDA",
                earnings_date="2025-12-09",
                actual_move_pct=5.2,
                actual_direction="SIDEWAYS"
            )
        assert "Invalid actual_direction" in str(excinfo.value)

    def test_record_outcome_invalid_trade_outcome_raises(self, temp_history):
        """Should raise ValueError for invalid trade_outcome."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Bullish",
            sentiment_direction=SentimentDirection.BULLISH
        )

        with pytest.raises(ValueError) as excinfo:
            temp_history.record_outcome(
                ticker="NVDA",
                earnings_date="2025-12-09",
                actual_move_pct=5.2,
                actual_direction="UP",
                trade_outcome="INVALID"
            )
        assert "Invalid trade_outcome" in str(excinfo.value)


class TestGetMethods:
    """Tests for get methods."""

    @pytest.fixture
    def temp_history(self, tmp_path):
        """Create a temporary history for testing."""
        db_path = tmp_path / "test_history.db"
        return SentimentHistory(db_path=db_path)

    def test_get_returns_none_for_missing(self, temp_history):
        """get should return None for non-existent record."""
        result = temp_history.get("MISSING", "2025-12-09")
        assert result is None

    def test_get_uppercases_ticker(self, temp_history):
        """get should uppercase ticker."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Test"
        )

        record = temp_history.get("nvda", "2025-12-09")
        assert record is not None

    def test_get_pending_outcomes_empty(self, temp_history):
        """get_pending_outcomes should return empty list when no pending."""
        records = temp_history.get_pending_outcomes()
        assert records == []

    def test_get_pending_outcomes_returns_pending(self, temp_history):
        """get_pending_outcomes should return records without outcomes."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Test"
        )

        records = temp_history.get_pending_outcomes()
        assert len(records) == 1
        assert records[0].ticker == "NVDA"

    def test_get_pending_outcomes_excludes_completed(self, temp_history):
        """get_pending_outcomes should exclude records with outcomes."""
        temp_history.record_sentiment(
            ticker="NVDA",
            earnings_date="2025-12-09",
            source="perplexity",
            sentiment_text="Test"
        )
        temp_history.record_outcome(
            ticker="NVDA",
            earnings_date="2025-12-09",
            actual_move_pct=5.2,
            actual_direction="UP"
        )

        records = temp_history.get_pending_outcomes()
        assert len(records) == 0

    def test_get_pending_outcomes_before_date(self, temp_history):
        """get_pending_outcomes should filter by before_date."""
        temp_history.record_sentiment(
            ticker="PAST",
            earnings_date="2025-12-01",
            source="perplexity",
            sentiment_text="Past"
        )
        temp_history.record_sentiment(
            ticker="FUTR",
            earnings_date="2025-12-15",
            source="perplexity",
            sentiment_text="Future"
        )

        records = temp_history.get_pending_outcomes(before_date="2025-12-10")
        assert len(records) == 1
        assert records[0].ticker == "PAST"

    def test_get_by_date_range(self, temp_history):
        """get_by_date_range should return records in range."""
        temp_history.record_sentiment("AA", "2025-12-01", "perplexity", "Test")
        temp_history.record_sentiment("BB", "2025-12-05", "perplexity", "Test")
        temp_history.record_sentiment("CC", "2025-12-10", "perplexity", "Test")
        temp_history.record_sentiment("DD", "2025-12-15", "perplexity", "Test")

        records = temp_history.get_by_date_range("2025-12-03", "2025-12-12")
        tickers = [r.ticker for r in records]
        assert "BB" in tickers
        assert "CC" in tickers
        assert "AA" not in tickers
        assert "DD" not in tickers

    def test_get_by_date_range_with_outcomes_only(self, temp_history):
        """get_by_date_range with with_outcomes_only should filter."""
        temp_history.record_sentiment("HAS", "2025-12-05", "perplexity", "Test")
        temp_history.record_outcome("HAS", "2025-12-05", 5.2, "UP")

        temp_history.record_sentiment("MISS", "2025-12-06", "perplexity", "Test")

        records = temp_history.get_by_date_range(
            "2025-12-01", "2025-12-10",
            with_outcomes_only=True
        )
        assert len(records) == 1
        assert records[0].ticker == "HAS"


class TestAccuracyStats:
    """Tests for get_accuracy_stats method."""

    @pytest.fixture
    def temp_history(self, tmp_path):
        """Create a temporary history for testing."""
        db_path = tmp_path / "test_history.db"
        return SentimentHistory(db_path=db_path)

    def test_accuracy_stats_empty(self, temp_history):
        """get_accuracy_stats should handle empty database."""
        stats = temp_history.get_accuracy_stats()
        assert stats['total_records'] == 0
        assert stats['accuracy'] is None

    def test_accuracy_stats_with_records(self, temp_history):
        """get_accuracy_stats should calculate accuracy correctly."""
        # 2 correct predictions
        temp_history.record_sentiment("COR", "2025-12-01", "perplexity", "Bull",
                                      sentiment_direction=SentimentDirection.BULLISH)
        temp_history.record_outcome("COR", "2025-12-01", 5.0, "UP")

        temp_history.record_sentiment("CORR", "2025-12-02", "perplexity", "Bear",
                                      sentiment_direction=SentimentDirection.BEARISH)
        temp_history.record_outcome("CORR", "2025-12-02", 5.0, "DOWN")

        # 1 wrong prediction
        temp_history.record_sentiment("WRONG", "2025-12-03", "perplexity", "Bull",
                                      sentiment_direction=SentimentDirection.BULLISH)
        temp_history.record_outcome("WRONG", "2025-12-03", 5.0, "DOWN")

        stats = temp_history.get_accuracy_stats()
        assert stats['predictions_made'] == 3
        assert stats['predictions_correct'] == 2
        assert stats['accuracy'] == pytest.approx(2/3, rel=0.01)

    def test_accuracy_stats_by_direction(self, temp_history):
        """get_accuracy_stats should break down by direction."""
        temp_history.record_sentiment("BULL", "2025-12-01", "perplexity", "Bull",
                                      sentiment_direction=SentimentDirection.BULLISH)
        temp_history.record_outcome("BULL", "2025-12-01", 5.0, "UP")

        temp_history.record_sentiment("BEAR", "2025-12-02", "perplexity", "Bear",
                                      sentiment_direction=SentimentDirection.BEARISH)
        temp_history.record_outcome("BEAR", "2025-12-02", 5.0, "DOWN")

        stats = temp_history.get_accuracy_stats()
        assert stats['by_direction']['bullish']['total'] == 1
        assert stats['by_direction']['bearish']['total'] == 1

    def test_accuracy_stats_trade_outcomes(self, temp_history):
        """get_accuracy_stats should count trade outcomes."""
        temp_history.record_sentiment("WIN", "2025-12-01", "perplexity", "Bull",
                                      sentiment_direction=SentimentDirection.BULLISH)
        temp_history.record_outcome("WIN", "2025-12-01", 5.0, "UP", "WIN")

        temp_history.record_sentiment("LOSS", "2025-12-02", "perplexity", "Bull",
                                      sentiment_direction=SentimentDirection.BULLISH)
        temp_history.record_outcome("LOSS", "2025-12-02", 5.0, "DOWN", "LOSS")

        temp_history.record_sentiment("SKIP", "2025-12-03", "perplexity", "Neutral",
                                      sentiment_direction=SentimentDirection.NEUTRAL)
        temp_history.record_outcome("SKIP", "2025-12-03", 5.0, "UP", "SKIP")

        stats = temp_history.get_accuracy_stats()
        assert stats['trade_outcomes']['WIN'] == 1
        assert stats['trade_outcomes']['LOSS'] == 1
        assert stats['trade_outcomes']['SKIP'] == 1


class TestStats:
    """Tests for stats method."""

    @pytest.fixture
    def temp_history(self, tmp_path):
        """Create a temporary history for testing."""
        db_path = tmp_path / "test_history.db"
        return SentimentHistory(db_path=db_path)

    def test_stats_empty(self, temp_history):
        """stats should handle empty database."""
        stats = temp_history.stats()
        assert stats['total_records'] == 0
        assert stats['unique_tickers'] == 0
        assert stats['by_source'] == {}

    def test_stats_with_records(self, temp_history):
        """stats should return correct counts."""
        temp_history.record_sentiment("NVDA", "2025-12-01", "perplexity", "Test")
        temp_history.record_sentiment("AAPL", "2025-12-02", "websearch", "Test")
        temp_history.record_sentiment("MSFT", "2025-12-03", "perplexity", "Test")

        stats = temp_history.stats()
        assert stats['total_records'] == 3
        assert stats['unique_tickers'] == 3
        assert stats['by_source']['perplexity'] == 2
        assert stats['by_source']['websearch'] == 1

    def test_stats_date_range(self, temp_history):
        """stats should show correct date range."""
        temp_history.record_sentiment("AA", "2025-12-01", "perplexity", "Test")
        temp_history.record_sentiment("BB", "2025-12-15", "perplexity", "Test")

        stats = temp_history.stats()
        assert stats['earliest_date'] == "2025-12-01"
        assert stats['latest_date'] == "2025-12-15"

    def test_stats_pending_outcomes(self, temp_history):
        """stats should count pending outcomes."""
        temp_history.record_sentiment("HAS", "2025-12-01", "perplexity", "Test")
        temp_history.record_outcome("HAS", "2025-12-01", 5.0, "UP")

        temp_history.record_sentiment("PEND", "2025-12-02", "perplexity", "Test")

        stats = temp_history.stats()
        assert stats['with_outcomes'] == 1
        assert stats['pending_outcomes'] == 1


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_record_sentiment_function(self):
        """record_sentiment convenience function should call class method."""
        with patch('cache.sentiment_history.SentimentHistory') as MockHistory:
            mock_instance = MockHistory.return_value

            record_sentiment(
                ticker="NVDA",
                earnings_date="2025-12-09",
                source="perplexity",
                sentiment_text="Bullish outlook"
            )

            mock_instance.record_sentiment.assert_called_once()

    def test_record_outcome_function(self):
        """record_outcome convenience function should call class method."""
        with patch('cache.sentiment_history.SentimentHistory') as MockHistory:
            mock_instance = MockHistory.return_value
            mock_instance.record_outcome.return_value = True

            result = record_outcome(
                ticker="NVDA",
                earnings_date="2025-12-09",
                actual_move_pct=5.2,
                actual_direction="UP"
            )

            mock_instance.record_outcome.assert_called_once()
            assert result is True

    def test_get_pending_outcomes_function(self):
        """get_pending_outcomes convenience function should call class method."""
        with patch('cache.sentiment_history.SentimentHistory') as MockHistory:
            mock_instance = MockHistory.return_value
            mock_instance.get_pending_outcomes.return_value = []

            result = get_pending_outcomes()

            mock_instance.get_pending_outcomes.assert_called_once()
            assert result == []

    def test_get_sentiment_stats_format(self):
        """get_sentiment_stats should return formatted string."""
        with patch('cache.sentiment_history.SentimentHistory') as MockHistory:
            mock_instance = MockHistory.return_value
            mock_instance.stats.return_value = {
                'total_records': 10,
                'unique_tickers': 5,
                'earliest_date': '2025-12-01',
                'latest_date': '2025-12-09',
                'with_outcomes': 8,
                'pending_outcomes': 2,
                'by_source': {'perplexity': 7, 'websearch': 3}
            }
            mock_instance.get_accuracy_stats.return_value = {
                'accuracy': 0.75
            }

            result = get_sentiment_stats()

            assert "Sentiment History" in result
            assert "10" in result  # total_records
            assert "75.0%" in result  # accuracy


class TestEdgeCases:
    """Edge case tests."""

    @pytest.fixture
    def temp_history(self, tmp_path):
        """Create a temporary history for testing."""
        db_path = tmp_path / "test_history.db"
        return SentimentHistory(db_path=db_path)

    def test_long_sentiment_text(self, temp_history):
        """Should handle very long sentiment text."""
        long_text = "A" * 10000
        temp_history.record_sentiment("NVDA", "2025-12-09", "perplexity", long_text)
        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_text == long_text

    def test_special_characters_in_sentiment(self, temp_history):
        """Should handle special characters in sentiment."""
        special_text = "Bull's outlook: 'strong' & positive! <tag> \"quoted\""
        temp_history.record_sentiment("NVDA", "2025-12-09", "perplexity", special_text)
        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_text == special_text

    def test_unicode_in_sentiment(self, temp_history):
        """Should handle unicode in sentiment."""
        unicode_text = "Bullish 🚀 outlook for Q4 earnings 📈"
        temp_history.record_sentiment("NVDA", "2025-12-09", "perplexity", unicode_text)
        record = temp_history.get("NVDA", "2025-12-09")
        assert record.sentiment_text == unicode_text

    def test_negative_vrp_ratio(self, temp_history):
        """Should handle negative VRP ratio (edge case)."""
        temp_history.record_sentiment(
            "NVDA", "2025-12-09", "perplexity", "Test",
            vrp_ratio=-1.5
        )
        record = temp_history.get("NVDA", "2025-12-09")
        assert record.vrp_ratio == -1.5

    def test_zero_implied_move(self, temp_history):
        """Should handle zero implied move."""
        temp_history.record_sentiment(
            "NVDA", "2025-12-09", "perplexity", "Test",
            implied_move_pct=0.0
        )
        record = temp_history.get("NVDA", "2025-12-09")
        assert record.implied_move_pct == 0.0

    def test_large_actual_move(self, temp_history):
        """Should handle very large actual moves."""
        temp_history.record_sentiment("NVDA", "2025-12-09", "perplexity", "Test")
        temp_history.record_outcome("NVDA", "2025-12-09", 150.0, "UP")

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.actual_move_pct == 150.0

    def test_creates_parent_directory(self, tmp_path):
        """Should create parent directories if they don't exist."""
        db_path = tmp_path / "nested" / "dir" / "history.db"
        history = SentimentHistory(db_path=db_path)
        assert db_path.exists()

    def test_multiple_sources_same_ticker_date(self, temp_history):
        """Later record should replace earlier one (primary key constraint)."""
        temp_history.record_sentiment("NVDA", "2025-12-09", "perplexity", "First")
        temp_history.record_sentiment("NVDA", "2025-12-09", "websearch", "Second")

        record = temp_history.get("NVDA", "2025-12-09")
        assert record.source == "websearch"
        assert record.sentiment_text == "Second"

    def test_sentiment_score_boundaries(self, temp_history):
        """Should handle boundary sentiment scores.

        Symmetric boundary convention: >= 0.2 is bullish, <= -0.2 is bearish.
        - score >= 0.2 → BULLISH (0.2 is bullish)
        - score <= -0.2 → BEARISH (-0.2 is bearish, symmetric with bullish)
        """
        # Exactly at threshold
        temp_history.record_sentiment("AA", "2025-12-01", "perplexity", "Test",
                                      sentiment_score=0.2)
        temp_history.record_sentiment("BB", "2025-12-02", "perplexity", "Test",
                                      sentiment_score=-0.2)

        r1 = temp_history.get("AA", "2025-12-01")
        r2 = temp_history.get("BB", "2025-12-02")

        # At exactly 0.2, bullish (>= 0.2 is lower bound of bullish)
        assert r1.sentiment_direction == SentimentDirection.BULLISH
        # At exactly -0.2, bearish (<= -0.2, symmetric with bullish threshold)
        assert r2.sentiment_direction == SentimentDirection.BEARISH

    def test_unknown_direction_in_database(self, temp_history):
        """Should handle unknown direction values gracefully."""
        # Insert directly with invalid direction
        with sqlite3.connect(temp_history.db_path) as conn:
            conn.execute("""
                INSERT INTO sentiment_history
                (ticker, earnings_date, collected_at, source, sentiment_text,
                 sentiment_direction, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "TEST", "2025-12-09", datetime.now(timezone.utc).isoformat(),
                "perplexity", "Test", "invalid_direction",
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()

        record = temp_history.get("TEST", "2025-12-09")
        # Should fall back to UNKNOWN
        assert record.sentiment_direction == SentimentDirection.UNKNOWN
