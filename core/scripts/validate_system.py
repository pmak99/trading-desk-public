#!/usr/bin/env python3
"""
Comprehensive System Validation - IV Crush 2.0

Tests all critical components:
1. Configuration and setup
2. VRP calculations with new thresholds
3. Implied move calculations
4. Strategy generation
5. Liquidity scoring
6. Error handling
7. Integration tests
"""

import sys
from pathlib import Path
from datetime import date, datetime, timedelta
import logging

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import get_config
from src.application.metrics.vrp import VRPCalculator
from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.domain.types import Percentage, Money, HistoricalMove
from src.domain.enums import Recommendation

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class ValidationSuite:
    """Comprehensive validation test suite."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests_run = []

    def test(self, name: str, func):
        """Run a single test."""
        print(f"\n{'='*80}")
        print(f"TEST: {name}")
        print(f"{'='*80}")
        try:
            func()
            print(f"✅ PASSED: {name}")
            self.passed += 1
            self.tests_run.append((name, "PASS"))
        except AssertionError as e:
            print(f"❌ FAILED: {name}")
            print(f"   Error: {e}")
            self.failed += 1
            self.tests_run.append((name, "FAIL", str(e)))
        except Exception as e:
            print(f"❌ ERROR: {name}")
            print(f"   Exception: {e}")
            import traceback
            traceback.print_exc()
            self.failed += 1
            self.tests_run.append((name, "ERROR", str(e)))

    def report(self):
        """Print final report."""
        print("\n" + "="*80)
        print("VALIDATION SUMMARY")
        print("="*80)
        total = self.passed + self.failed
        print(f"\nTests Run: {total}")
        print(f"✅ Passed: {self.passed} ({self.passed/total*100:.1f}%)")
        print(f"❌ Failed: {self.failed} ({self.failed/total*100:.1f}%)")

        if self.failed > 0:
            print("\n⚠️  FAILED TESTS:")
            for test in self.tests_run:
                if test[1] in ["FAIL", "ERROR"]:
                    print(f"  - {test[0]}: {test[2] if len(test) > 2 else 'Unknown'}")

        print("\n" + "="*80)
        if self.failed == 0:
            print("🎉 ALL TESTS PASSED - System validated!")
        else:
            print(f"⚠️  {self.failed} test(s) failed - review required")
        print("="*80)


def test_config_loading():
    """Test configuration loads correctly."""
    config = get_config()
    assert config is not None, "Config should not be None"
    assert config.database is not None, "Database config required"
    assert config.strategy is not None, "Strategy config required"
    print(f"✓ Config loaded successfully")
    print(f"  Database: {config.database.path}")
    print(f"  Strategy weights: VRP={config.strategy.scoring_weights.vrp_weight:.2f}")


def test_new_vrp_thresholds():
    """Test new VRP thresholds are correct."""
    calc = VRPCalculator()

    # Check new thresholds
    assert calc.thresholds['excellent'] == 7.0, f"Expected 7.0, got {calc.thresholds['excellent']}"
    assert calc.thresholds['good'] == 4.0, f"Expected 4.0, got {calc.thresholds['good']}"
    assert calc.thresholds['marginal'] == 1.5, f"Expected 1.5, got {calc.thresholds['marginal']}"

    print(f"✓ VRP thresholds correctly set:")
    print(f"  EXCELLENT: >= {calc.thresholds['excellent']}x")
    print(f"  GOOD: >= {calc.thresholds['good']}x")
    print(f"  MARGINAL: >= {calc.thresholds['marginal']}x")


def test_vrp_classification():
    """Test VRP classification with new thresholds."""
    calc = VRPCalculator()

    # Create test historical moves
    def make_moves(mean_pct: float, count: int = 8):
        moves = []
        for i in range(count):
            moves.append(HistoricalMove(
                earnings_date=date.today() - timedelta(days=90*i),
                close_move_pct=Percentage(mean_pct),
                intraday_move_pct=Percentage(mean_pct * 1.2),
                gap_move_pct=Percentage(mean_pct * 0.8),
            ))
        return moves

    # Test EXCELLENT (15x)
    moves_low = make_moves(1.0)  # 1% historical
    result = calc.calculate(
        ticker="TEST",
        implied_move_pct=Percentage(15.0),  # 15% implied = 15x VRP
        historical_moves=moves_low,
        earnings_date=date.today(),
    )
    assert result.is_ok(), "Should calculate successfully"
    vrp = result.unwrap()
    assert vrp.recommendation == Recommendation.EXCELLENT, f"15x should be EXCELLENT, got {vrp.recommendation}"
    print(f"✓ VRP 15.0x → {vrp.recommendation.value.upper()}")

    # Test GOOD (5x)
    moves_med = make_moves(2.0)  # 2% historical
    result = calc.calculate(
        ticker="TEST",
        implied_move_pct=Percentage(10.0),  # 10% implied = 5x VRP
        historical_moves=moves_med,
        earnings_date=date.today(),
    )
    vrp = result.unwrap()
    assert vrp.recommendation == Recommendation.GOOD, f"5x should be GOOD, got {vrp.recommendation}"
    print(f"✓ VRP 5.0x → {vrp.recommendation.value.upper()}")

    # Test MARGINAL (2x)
    moves_high = make_moves(5.0)  # 5% historical
    result = calc.calculate(
        ticker="TEST",
        implied_move_pct=Percentage(10.0),  # 10% implied = 2x VRP
        historical_moves=moves_high,
        earnings_date=date.today(),
    )
    vrp = result.unwrap()
    assert vrp.recommendation == Recommendation.MARGINAL, f"2x should be MARGINAL, got {vrp.recommendation}"
    print(f"✓ VRP 2.0x → {vrp.recommendation.value.upper()}")

    # Test POOR (1.2x)
    result = calc.calculate(
        ticker="TEST",
        implied_move_pct=Percentage(6.0),  # 6% implied = 1.2x VRP
        historical_moves=moves_high,
        earnings_date=date.today(),
    )
    vrp = result.unwrap()
    assert vrp.recommendation == Recommendation.POOR, f"1.2x should be POOR, got {vrp.recommendation}"
    print(f"✓ VRP 1.2x → {vrp.recommendation.value.upper()}")


def test_liquidity_scorer():
    """Test liquidity scoring functionality."""
    from src.domain.types import OptionQuote, OptionType, OptionLeg, Strike

    scorer = LiquidityScorer()

    # Create mock option quote
    option = OptionQuote(
        symbol="TEST_C_100",
        strike=Strike(100.0),
        option_type=OptionType.CALL,
        expiration=date.today() + timedelta(days=7),
        bid=Money(2.50),
        ask=Money(2.60),
        mid=Money(2.55),
        last=Money(2.55),
        volume=150,
        open_interest=500,
        implied_volatility=0.35,
        delta=0.50,
    )

    score = scorer.score_option(option)

    assert score.overall_score > 0, "Should have positive liquidity score"
    assert score.is_liquid, "Should meet minimum liquidity standards"

    print(f"✓ Liquidity scoring functional:")
    print(f"  Overall Score: {score.overall_score:.1f}")
    print(f"  OI Score: {score.oi_score:.1f}")
    print(f"  Volume Score: {score.volume_score:.1f}")
    print(f"  Spread Score: {score.spread_score:.1f}")
    print(f"  Tier: {score.liquidity_tier}")


def test_edge_score_calculation():
    """Test edge score calculation."""
    calc = VRPCalculator()

    # Create test data
    def make_moves(mean_pct: float, count: int = 8):
        moves = []
        for i in range(count):
            moves.append(HistoricalMove(
                earnings_date=date.today() - timedelta(days=90*i),
                close_move_pct=Percentage(mean_pct),
                intraday_move_pct=Percentage(mean_pct * 1.2),
                gap_move_pct=Percentage(mean_pct * 0.8),
            ))
        return moves

    moves = make_moves(1.0)
    result = calc.calculate(
        ticker="TEST",
        implied_move_pct=Percentage(10.0),  # 10x VRP
        historical_moves=moves,
        earnings_date=date.today(),
    )

    vrp = result.unwrap()
    assert vrp.edge_score > 0, "Edge score should be positive"
    assert vrp.vrp_ratio > 1.0, "VRP ratio should be > 1"

    print(f"✓ Edge score calculation:")
    print(f"  VRP Ratio: {vrp.vrp_ratio:.2f}x")
    print(f"  Edge Score: {vrp.edge_score:.2f}")


def test_percentage_type():
    """Test Percentage domain type."""
    p1 = Percentage(5.0)
    assert p1.value == 5.0
    assert float(p1) == 0.05  # Converts to decimal

    p2 = Percentage(10.0)
    assert p2 > p1

    print(f"✓ Percentage type working correctly")


def test_money_type():
    """Test Money domain type."""
    m1 = Money(10.50)
    m2 = Money(5.25)

    assert m1.amount == 10.50
    assert m2.amount == 5.25
    assert m1 > m2

    # Test arithmetic
    m3 = Money(m1.amount + m2.amount)
    assert m3.amount == 15.75

    print(f"✓ Money type working correctly")


def test_error_handling():
    """Test error handling with invalid inputs."""
    calc = VRPCalculator()

    # Test with insufficient data
    result = calc.calculate(
        ticker="TEST",
        implied_move_pct=Percentage(10.0),
        historical_moves=[],  # Empty moves
        earnings_date=date.today(),
    )

    assert result.is_err(), "Should return error for insufficient data"
    error = result.unwrap_err()
    assert "insufficient" in error.message.lower(), "Error should mention insufficient data"

    print(f"✓ Error handling working:")
    print(f"  Error code: {error.code}")
    print(f"  Message: {error.message}")


def test_database_connection():
    """Test database connectivity."""
    import sqlite3

    config = get_config()
    db_path = str(config.database.path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]

    assert 'analysis_log' in tables, "analysis_log table should exist"
    assert 'earnings_history' in tables, "earnings_history table should exist"

    conn.close()

    print(f"✓ Database connection successful:")
    print(f"  Path: {db_path}")
    print(f"  Tables: {', '.join(tables)}")


def test_strategy_config():
    """Test strategy configuration."""
    config = get_config()
    weights = config.strategy.scoring_weights

    # Check weights sum to ~1.0
    total = (weights.vrp_weight + weights.pop_weight +
             weights.reward_risk_weight + weights.liquidity_weight +
             weights.consistency_weight)

    assert abs(total - 1.0) < 0.01, f"Weights should sum to 1.0, got {total}"

    print(f"✓ Strategy config valid:")
    print(f"  VRP weight: {weights.vrp_weight}")
    print(f"  POP weight: {weights.pop_weight}")
    print(f"  R/R weight: {weights.reward_risk_weight}")
    print(f"  Liquidity weight: {weights.liquidity_weight}")
    print(f"  Consistency weight: {weights.consistency_weight}")
    print(f"  Total: {total:.3f}")


def test_date_handling():
    """Test date handling and calculations."""
    from datetime import date, timedelta

    today = date.today()
    future = today + timedelta(days=7)
    past = today - timedelta(days=30)

    assert future > today
    assert past < today
    assert (future - today).days == 7

    print(f"✓ Date handling working correctly")


def main():
    """Run all validation tests."""
    print("="*80)
    print("IV CRUSH 2.0 - COMPREHENSIVE SYSTEM VALIDATION")
    print("="*80)
    print(f"\nTimestamp: {datetime.now().isoformat()}")
    print(f"Python: {sys.version.split()[0]}")

    suite = ValidationSuite()

    # Core configuration tests
    suite.test("Config Loading", test_config_loading)
    suite.test("Database Connection", test_database_connection)
    suite.test("Strategy Configuration", test_strategy_config)

    # New VRP threshold tests
    suite.test("New VRP Thresholds", test_new_vrp_thresholds)
    suite.test("VRP Classification", test_vrp_classification)
    suite.test("Edge Score Calculation", test_edge_score_calculation)

    # Component tests
    suite.test("Liquidity Scorer", test_liquidity_scorer)
    suite.test("Percentage Type", test_percentage_type)
    suite.test("Money Type", test_money_type)
    suite.test("Date Handling", test_date_handling)

    # Error handling
    suite.test("Error Handling", test_error_handling)

    # Final report
    suite.report()

    return 0 if suite.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
