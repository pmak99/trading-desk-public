"""
Unit tests for VRP (Volatility Risk Premium) Calculator.
"""

import pytest
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.analysis.vrp import (
    VRPCalculator,
    VRPResult,
    HistoricalMove,
    Recommendation,
)


@pytest.fixture
def temp_db():
    """Create a temporary database with test data."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create historical_moves table
    cursor.execute("""
        CREATE TABLE historical_moves (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            earnings_date TEXT NOT NULL,
            prev_close REAL,
            earnings_close REAL,
            close_move_pct REAL,
            gap_move_pct REAL,
            intraday_move_pct REAL
        )
    """)

    # Insert test data for AAPL - consistent 4% mover
    test_data = [
        ('AAPL', '2024-10-31', 170.0, 177.0, 4.1, 3.5, 2.1),
        ('AAPL', '2024-07-25', 180.0, 172.8, -4.0, -3.8, 1.5),
        ('AAPL', '2024-05-02', 169.0, 175.8, 4.0, 3.2, 1.8),
        ('AAPL', '2024-02-01', 185.0, 178.0, -3.8, -3.5, 1.2),
        ('AAPL', '2023-11-02', 173.0, 179.8, 3.9, 3.0, 2.0),
        ('AAPL', '2023-08-03', 195.0, 187.2, -4.0, -3.6, 1.4),
    ]

    for row in test_data:
        cursor.execute("""
            INSERT INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_close,
             close_move_pct, gap_move_pct, intraday_move_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, row)

    # Insert data for NVDA - volatile mover (high variance: 6%, 14%, 8%, 12%)
    nvda_data = [
        ('NVDA', '2024-11-20', 140.0, 148.4, 6.0, 5.0, 3.0),
        ('NVDA', '2024-08-28', 120.0, 103.2, -14.0, -12.0, 5.0),
        ('NVDA', '2024-05-22', 90.0, 97.2, 8.0, 7.0, 4.0),
        ('NVDA', '2024-02-21', 70.0, 78.4, 12.0, 10.0, 5.0),
    ]

    for row in nvda_data:
        cursor.execute("""
            INSERT INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_close,
             close_move_pct, gap_move_pct, intraday_move_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, row)

    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    db_path.unlink(missing_ok=True)


@pytest.fixture
def vrp_calculator(temp_db):
    """Create VRP calculator with test database."""
    return VRPCalculator(db_path=temp_db, min_quarters=4)


class TestVRPCalculator:
    """Tests for VRPCalculator class."""

    def test_init_default_thresholds(self, temp_db):
        """Test default threshold values."""
        calc = VRPCalculator(db_path=temp_db)
        assert calc.thresholds['excellent'] == 7.0
        assert calc.thresholds['good'] == 4.0
        assert calc.thresholds['marginal'] == 1.5

    def test_init_custom_thresholds(self, temp_db):
        """Test custom threshold configuration."""
        calc = VRPCalculator(
            db_path=temp_db,
            threshold_excellent=10.0,
            threshold_good=5.0,
            threshold_marginal=2.0,
        )
        assert calc.thresholds['excellent'] == 10.0
        assert calc.thresholds['good'] == 5.0
        assert calc.thresholds['marginal'] == 2.0

    def test_get_historical_moves(self, vrp_calculator):
        """Test fetching historical moves from database."""
        moves = vrp_calculator.get_historical_moves('AAPL', limit=12)

        assert len(moves) == 6
        assert all(isinstance(m, HistoricalMove) for m in moves)
        assert moves[0].earnings_date == date(2024, 10, 31)
        assert abs(moves[0].close_move_pct - 4.1) < 0.01

    def test_get_historical_moves_empty(self, vrp_calculator):
        """Test fetching moves for unknown ticker."""
        moves = vrp_calculator.get_historical_moves('UNKNOWN')
        assert moves == []

    def test_calculate_excellent_vrp(self, vrp_calculator):
        """Test VRP calculation with excellent rating."""
        # AAPL average move is ~4%, implied of 28% = 7x VRP
        result = vrp_calculator.calculate(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            implied_move_pct=28.0,
        )

        assert result is not None
        assert result.recommendation == Recommendation.EXCELLENT
        assert result.vrp_ratio >= 7.0
        assert result.quarters_of_data == 6

    def test_calculate_good_vrp(self, vrp_calculator):
        """Test VRP calculation with good rating."""
        # AAPL average move is ~4%, implied of 16% = 4x VRP
        result = vrp_calculator.calculate(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            implied_move_pct=16.0,
        )

        assert result is not None
        assert result.recommendation == Recommendation.GOOD
        assert 4.0 <= result.vrp_ratio < 7.0

    def test_calculate_marginal_vrp(self, vrp_calculator):
        """Test VRP calculation with marginal rating."""
        # AAPL average move is ~4%, implied of 6% = 1.5x VRP
        result = vrp_calculator.calculate(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            implied_move_pct=6.0,
        )

        assert result is not None
        assert result.recommendation == Recommendation.MARGINAL
        assert 1.5 <= result.vrp_ratio < 4.0

    def test_calculate_skip_vrp(self, vrp_calculator):
        """Test VRP calculation with skip rating."""
        # AAPL average move is ~4%, implied of 4% = 1x VRP
        result = vrp_calculator.calculate(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            implied_move_pct=4.0,
        )

        assert result is not None
        assert result.recommendation == Recommendation.SKIP
        assert result.vrp_ratio < 1.5

    def test_calculate_insufficient_data(self, vrp_calculator):
        """Test VRP calculation with insufficient historical data."""
        # Only 4 quarters for NVDA, min_quarters=4 should work
        result = vrp_calculator.calculate(
            ticker='NVDA',
            expiration=date(2024, 12, 20),
            implied_move_pct=20.0,
        )
        assert result is not None
        assert result.quarters_of_data == 4

        # Set min_quarters higher
        vrp_calculator.min_quarters = 5
        result = vrp_calculator.calculate(
            ticker='NVDA',
            expiration=date(2024, 12, 20),
            implied_move_pct=20.0,
        )
        assert result is None

    def test_calculate_unknown_ticker(self, vrp_calculator):
        """Test VRP calculation for unknown ticker."""
        result = vrp_calculator.calculate(
            ticker='UNKNOWN',
            expiration=date(2024, 12, 20),
            implied_move_pct=10.0,
        )
        assert result is None

    def test_edge_score_calculation(self, vrp_calculator):
        """Test edge score penalizes high variance."""
        # AAPL has consistent 4% moves (std ~0.09) -> higher edge score relative to VRP
        aapl_result = vrp_calculator.calculate(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            implied_move_pct=16.0,  # ~4x VRP
        )

        # NVDA has high variance moves (6%, 14%, 8%, 12% - std ~3.16)
        # Mean = 10%, set implied to get ~4x VRP
        nvda_result = vrp_calculator.calculate(
            ticker='NVDA',
            expiration=date(2024, 12, 20),
            implied_move_pct=40.0,  # 4x VRP (10% avg * 4)
        )

        assert aapl_result is not None
        assert nvda_result is not None
        # Both should have similar VRP ratios
        assert abs(aapl_result.vrp_ratio - nvda_result.vrp_ratio) < 0.5
        # NVDA has higher variance, so lower edge score
        # Edge score = VRP / (1 + MAD/median)
        # AAPL: low MAD -> edge close to VRP
        # NVDA: high MAD -> edge significantly lower than VRP
        assert nvda_result.historical_std_pct > aapl_result.historical_std_pct
        # AAPL's edge score should be higher due to consistency
        assert aapl_result.edge_score > nvda_result.edge_score

    def test_move_metric_close(self, vrp_calculator):
        """Test using close move metric."""
        vrp_calculator.move_metric = "close"
        result = vrp_calculator.calculate(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            implied_move_pct=16.0,
        )
        assert result is not None
        # Close moves average around 4%
        assert 3.5 < result.historical_mean_pct < 4.5

    def test_move_metric_gap(self, vrp_calculator):
        """Test using gap move metric."""
        vrp_calculator.move_metric = "gap"
        result = vrp_calculator.calculate(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            implied_move_pct=16.0,
        )
        assert result is not None
        # Gap moves are smaller than close moves
        assert result.historical_mean_pct < 4.0

    def test_vrp_result_fields(self, vrp_calculator):
        """Test all fields in VRPResult are populated."""
        result = vrp_calculator.calculate(
            ticker='AAPL',
            expiration=date(2024, 12, 20),
            implied_move_pct=16.0,
        )

        assert result.ticker == 'AAPL'
        assert result.expiration == date(2024, 12, 20)
        assert result.implied_move_pct == 16.0
        assert result.historical_mean_pct > 0
        assert result.historical_median_pct > 0
        assert result.historical_std_pct >= 0
        assert result.vrp_ratio > 0
        assert result.edge_score > 0
        assert isinstance(result.recommendation, Recommendation)
        assert result.quarters_of_data > 0


class TestHistoricalMove:
    """Tests for HistoricalMove dataclass."""

    def test_historical_move_creation(self):
        """Test creating HistoricalMove instance."""
        move = HistoricalMove(
            earnings_date=date(2024, 10, 31),
            prev_close=170.0,
            earnings_close=177.0,
            close_move_pct=4.1,
            gap_move_pct=3.5,
            intraday_move_pct=2.1,
        )

        assert move.earnings_date == date(2024, 10, 31)
        assert move.prev_close == 170.0
        assert move.earnings_close == 177.0
        assert move.close_move_pct == 4.1


class TestRecommendation:
    """Tests for Recommendation enum."""

    def test_recommendation_values(self):
        """Test recommendation enum values."""
        assert Recommendation.EXCELLENT.value == "excellent"
        assert Recommendation.GOOD.value == "good"
        assert Recommendation.MARGINAL.value == "marginal"
        assert Recommendation.SKIP.value == "skip"
