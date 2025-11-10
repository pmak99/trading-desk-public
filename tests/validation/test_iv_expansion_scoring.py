"""
Test suite for IV expansion scoring changes.

Tests:
1. Weekly IV change calculation (IVHistoryTracker.get_weekly_iv_change)
2. IVExpansionScorer scoring logic
3. Updated CompositeScorer with new weights
4. Integration test with real database
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.options.iv_history_tracker import IVHistoryTracker
from src.analysis.scorers import IVExpansionScorer, IVScorer, CompositeScorer


def test_weekly_iv_change_calculation():
    """Test weekly IV % change calculation."""
    print("\n=== Test 1: Weekly IV Change Calculation ===")

    # Create temp database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        tracker = IVHistoryTracker(db_path=db_path)

        # Record historical IV data
        today = datetime.now()

        # Scenario 1: Strong expansion (40% → 72% = +80% change)
        tracker.record_iv("NVDA", 40.0, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
        tracker.record_iv("NVDA", 45.0, (today - timedelta(days=6)).strftime('%Y-%m-%d'))
        tracker.record_iv("NVDA", 55.0, (today - timedelta(days=3)).strftime('%Y-%m-%d'))
        tracker.record_iv("NVDA", 72.0, today.strftime('%Y-%m-%d'))

        weekly_change = tracker.get_weekly_iv_change("NVDA", 72.0)
        expected_change = ((72.0 - 40.0) / 40.0) * 100  # +80%

        assert weekly_change is not None, "Weekly change should not be None"
        assert abs(weekly_change - expected_change) < 1.0, f"Expected ~{expected_change:.1f}%, got {weekly_change:.1f}%"
        print(f"✅ NVDA: Strong expansion detected: {weekly_change:+.1f}% (40% → 72%)")

        # Scenario 2: Premium leaking (85% → 70% = -17.6% change)
        tracker.record_iv("META", 85.0, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
        tracker.record_iv("META", 78.0, (today - timedelta(days=3)).strftime('%Y-%m-%d'))
        tracker.record_iv("META", 70.0, today.strftime('%Y-%m-%d'))

        weekly_change_leak = tracker.get_weekly_iv_change("META", 70.0)
        expected_leak = ((70.0 - 85.0) / 85.0) * 100  # -17.6%

        assert weekly_change_leak is not None, "Weekly change should not be None"
        assert abs(weekly_change_leak - expected_leak) < 1.0, f"Expected ~{expected_leak:.1f}%, got {weekly_change_leak:.1f}%"
        print(f"✅ META: Premium leaking detected: {weekly_change_leak:+.1f}% (85% → 70%)")

        # Scenario 3: No history (new ticker)
        no_history = tracker.get_weekly_iv_change("AAPL", 65.0)
        assert no_history is None, "Should return None for ticker without 7-day history"
        print(f"✅ AAPL: No history - correctly returned None")

        tracker.close()
        print("✅ All weekly IV change calculations passed")
        return True

    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.remove(db_path)


def test_iv_expansion_scorer():
    """Test IVExpansionScorer scoring logic."""
    print("\n=== Test 2: IVExpansionScorer Scoring ===")

    # Create temp database with test data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        tracker = IVHistoryTracker(db_path=db_path)
        today = datetime.now()

        # Setup test scenarios
        scenarios = [
            ("EXCELLENT", 40.0, 72.0, 100.0),  # +80% change → score 100
            ("GOOD", 50.0, 70.0, 80.0),        # +40% change → score 80
            ("MODERATE", 60.0, 72.0, 60.0),    # +20% change → score 60
            ("WEAK", 70.0, 75.0, 40.0),        # +7% change → score 40
            ("LEAKING", 80.0, 70.0, 20.0),     # -12.5% change → score 20
        ]

        scorer = IVExpansionScorer()

        for ticker, old_iv, current_iv, expected_score in scenarios:
            # Record historical data
            tracker.record_iv(ticker, old_iv, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
            tracker.record_iv(ticker, current_iv, today.strftime('%Y-%m-%d'))

            # Create ticker data
            ticker_data = {
                'ticker': ticker,
                'options_data': {'current_iv': current_iv}
            }

            # Calculate score
            score = scorer.score(ticker_data)

            pct_change = ((current_iv - old_iv) / old_iv) * 100

            assert abs(score - expected_score) < 5.0, \
                f"{ticker}: Expected score ~{expected_score}, got {score} (IV change: {pct_change:+.1f}%)"

            print(f"✅ {ticker}: {old_iv}% → {current_iv}% ({pct_change:+.1f}%) = score {score:.0f}")

        tracker.close()
        print("✅ All IVExpansionScorer tests passed")
        return True

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_updated_composite_scorer():
    """Test CompositeScorer with new weights."""
    print("\n=== Test 3: Updated CompositeScorer ===")

    # Create temp database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        tracker = IVHistoryTracker(db_path=db_path)
        today = datetime.now()

        # Scenario: Strong candidate (high expansion, high IV, good liquidity)
        ticker_data = {
            'ticker': 'STRONG',
            'price': 150.0,
            'market_cap': 100e9,  # $100B
            'options_data': {
                'current_iv': 85.0,
                'options_volume': 15000,
                'open_interest': 75000,
                'bid_ask_spread_pct': 0.03,
                'iv_crush_ratio': 1.25
            }
        }

        # Record strong IV expansion
        tracker.record_iv("STRONG", 45.0, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
        tracker.record_iv("STRONG", 85.0, today.strftime('%Y-%m-%d'))

        # Calculate composite score
        scorer = CompositeScorer()
        score = scorer.calculate_score(ticker_data)

        print(f"Strong candidate score: {score:.1f}/100")
        print(f"  - IV expansion: 45% → 85% (+88.9%)")
        print(f"  - Current IV: 85%")
        print(f"  - Options volume: 15,000")
        print(f"  - IV crush ratio: 1.25")

        # Should score well (>70)
        assert score > 70, f"Strong candidate should score >70, got {score:.1f}"
        print(f"✅ Strong candidate scored {score:.1f} (>70 threshold)")

        # Scenario: Weak candidate (negative expansion, mediocre IV)
        weak_data = {
            'ticker': 'WEAK',
            'price': 50.0,
            'market_cap': 15e9,  # $15B
            'options_data': {
                'current_iv': 65.0,
                'options_volume': 2000,
                'open_interest': 8000,
                'bid_ask_spread_pct': 0.08,
                'iv_crush_ratio': 1.05
            }
        }

        # Record weak/negative expansion
        tracker.record_iv("WEAK", 75.0, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
        tracker.record_iv("WEAK", 65.0, today.strftime('%Y-%m-%d'))

        weak_score = scorer.calculate_score(weak_data)

        print(f"\nWeak candidate score: {weak_score:.1f}/100")
        print(f"  - IV expansion: 75% → 65% (-13.3%)")
        print(f"  - Current IV: 65%")
        print(f"  - Options volume: 2,000")
        print(f"  - IV crush ratio: 1.05")

        # Should score lower than strong candidate
        assert weak_score < score, f"Weak candidate should score lower than strong"
        print(f"✅ Weak candidate scored {weak_score:.1f} (lower than strong)")

        tracker.close()
        print("✅ CompositeScorer tests passed")
        return True

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_iv_scorer_simplified():
    """Test simplified IVScorer (removed IV Rank dependency)."""
    print("\n=== Test 4: Simplified IVScorer (No IV Rank) ===")

    scorer = IVScorer()

    test_cases = [
        (85.0, 100.0, "Extreme IV (85%) → high score"),
        (75.0, 75.0, "Good IV (75%) → medium-high score"),
        (62.0, 62.0, "Minimum IV (62%) → passing score"),
        (55.0, 0.0, "Low IV (55%) → filtered out"),
    ]

    for current_iv, expected_min_score, description in test_cases:
        ticker_data = {
            'ticker': 'TEST',
            'options_data': {'current_iv': current_iv}
        }

        score = scorer.score(ticker_data)

        if expected_min_score == 0:
            assert score == 0, f"{description}: Expected 0, got {score}"
            print(f"✅ {description}: score = {score}")
        else:
            assert score >= expected_min_score * 0.9, \
                f"{description}: Expected >={expected_min_score}, got {score}"
            print(f"✅ {description}: score = {score:.1f}")

    print("✅ Simplified IVScorer tests passed")
    return True


def test_integration_with_real_database():
    """Integration test using actual IV history database."""
    print("\n=== Test 5: Integration with Real Database ===")

    db_path = "data/iv_history.db"

    if not os.path.exists(db_path):
        print("⚠️  Skipping - no real database found at data/iv_history.db")
        return True

    try:
        tracker = IVHistoryTracker(db_path=db_path)

        # Get some tickers from database
        conn = tracker._get_connection()
        cursor = conn.execute(
            """SELECT DISTINCT ticker FROM iv_history
               WHERE date >= date('now', '-10 days')
               LIMIT 3"""
        )
        tickers = [row['ticker'] for row in cursor]

        if not tickers:
            print("⚠️  No recent data in database")
            tracker.close()
            return True

        print(f"Testing with tickers: {', '.join(tickers)}")

        for ticker in tickers:
            # Get latest IV
            cursor = conn.execute(
                """SELECT iv_value, date FROM iv_history
                   WHERE ticker = ?
                   ORDER BY date DESC LIMIT 1""",
                (ticker,)
            )
            row = cursor.fetchone()

            if row:
                current_iv = row['iv_value']
                latest_date = row['date']

                # Calculate weekly change
                weekly_change = tracker.get_weekly_iv_change(ticker, current_iv)

                if weekly_change is not None:
                    print(f"✅ {ticker}: IV = {current_iv:.1f}%, Weekly change = {weekly_change:+.1f}%")
                else:
                    print(f"⚠️  {ticker}: Insufficient history for weekly change")

        tracker.close()
        print("✅ Integration test passed")
        return True

    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        return False


def run_all_tests():
    """Run all tests and report results."""
    print("=" * 70)
    print("IV EXPANSION SCORING TEST SUITE")
    print("=" * 70)

    tests = [
        ("Weekly IV Change Calculation", test_weekly_iv_change_calculation),
        ("IVExpansionScorer", test_iv_expansion_scorer),
        ("Updated CompositeScorer", test_updated_composite_scorer),
        ("Simplified IVScorer", test_iv_scorer_simplified),
        ("Integration Test", test_integration_with_real_database),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"\n❌ {test_name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, success in results if success)
    total = len(results)

    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status}: {test_name}")

    print("=" * 70)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 70)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
