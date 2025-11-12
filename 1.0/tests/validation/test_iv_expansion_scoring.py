"""Test IV expansion scoring changes."""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.options.iv_history_tracker import IVHistoryTracker
from src.analysis.scorers import IVExpansionScorer, IVScorer, CompositeScorer


def test_weekly_iv_change():
    """Test weekly IV % change calculation."""
    print("\n=== Test: Weekly IV Change ===")

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        tracker = IVHistoryTracker(db_path=db_path)
        today = datetime.now()

        # Strong expansion: 40% → 72% = +80%
        tracker.record_iv("NVDA", 40.0, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
        tracker.record_iv("NVDA", 72.0, today.strftime('%Y-%m-%d'))
        weekly_change = tracker.get_weekly_iv_change("NVDA", 72.0)
        assert abs(weekly_change - 80.0) < 1.0
        print(f"✅ NVDA: {weekly_change:+.1f}% (40% → 72%)")

        # Premium leaking: 85% → 70% = -17.6%
        tracker.record_iv("META", 85.0, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
        tracker.record_iv("META", 70.0, today.strftime('%Y-%m-%d'))
        weekly_change_leak = tracker.get_weekly_iv_change("META", 70.0)
        assert abs(weekly_change_leak - (-17.6)) < 1.0
        print(f"✅ META: {weekly_change_leak:+.1f}% (85% → 70%)")

        # No history
        assert tracker.get_weekly_iv_change("AAPL", 65.0) is None
        print("✅ AAPL: No history - correctly returned None")

        tracker.close()
        return True
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_iv_expansion_scorer():
    """Test IVExpansionScorer scoring."""
    print("\n=== Test: IVExpansionScorer ===")

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        tracker = IVHistoryTracker(db_path=db_path)
        today = datetime.now()

        scenarios = [
            ("EXCELLENT", 40.0, 72.0, 100.0),  # +80%
            ("GOOD", 50.0, 70.0, 80.0),        # +40%
            ("MODERATE", 60.0, 72.0, 60.0),    # +20%
            ("WEAK", 70.0, 75.0, 40.0),        # +7%
            ("LEAKING", 80.0, 70.0, 20.0),     # -12.5%
        ]

        scorer = IVExpansionScorer(db_path=db_path)

        for ticker, old_iv, current_iv, expected_score in scenarios:
            tracker.record_iv(ticker, old_iv, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
            tracker.record_iv(ticker, current_iv, today.strftime('%Y-%m-%d'))

            data = {'ticker': ticker, 'options_data': {'current_iv': current_iv}}
            score = scorer.score(data)
            pct_change = ((current_iv - old_iv) / old_iv) * 100

            assert abs(score - expected_score) < 5.0
            print(f"✅ {ticker}: {old_iv}% → {current_iv}% ({pct_change:+.1f}%) = {score:.0f}")

        tracker.close()
        return True
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_composite_scorer():
    """Test CompositeScorer with new weights."""
    print("\n=== Test: CompositeScorer ===")

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        tracker = IVHistoryTracker(db_path=db_path)
        today = datetime.now()

        # Strong candidate
        strong_data = {
            'ticker': 'STRONG',
            'price': 150.0,
            'market_cap': 100e9,
            'options_data': {
                'current_iv': 85.0,
                'options_volume': 15000,
                'open_interest': 75000,
                'bid_ask_spread_pct': 0.03,
                'iv_crush_ratio': 1.25
            }
        }
        tracker.record_iv("STRONG", 45.0, (today - timedelta(days=7)).strftime('%Y-%m-%d'))
        tracker.record_iv("STRONG", 85.0, today.strftime('%Y-%m-%d'))

        scorer = CompositeScorer()
        score = scorer.calculate_score(strong_data)
        print(f"Strong candidate: {score:.1f}/100")
        assert score >= 60

        # Weak candidate
        weak_data = {
            'ticker': 'WEAK',
            'price': 50.0,
            'market_cap': 15e9,
            'options_data': {
                'current_iv': 65.0,
                'options_volume': 2000,
                'open_interest': 8000,
                'bid_ask_spread_pct': 0.08,
                'iv_crush_ratio': 1.05
            }
        }
        weak_score = scorer.calculate_score(weak_data)
        print(f"Weak candidate: {weak_score:.1f}/100")
        assert weak_score < score

        tracker.close()
        return True
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_iv_scorer_simplified():
    """Test simplified IVScorer (no IV Rank)."""
    print("\n=== Test: Simplified IVScorer ===")

    scorer = IVScorer()

    test_cases = [
        (85.0, 100.0, "Extreme IV"),
        (75.0, 75.0, "Good IV"),
        (62.0, 62.0, "Minimum IV"),
        (55.0, 0.0, "Low IV filtered"),
    ]

    for current_iv, expected_min, desc in test_cases:
        data = {'ticker': 'TEST', 'options_data': {'current_iv': current_iv}}
        score = scorer.score(data)

        if expected_min == 0:
            assert score == 0
        else:
            assert score >= expected_min * 0.9

        print(f"✅ {desc}: {score:.1f}")

    return True


def test_integration_with_db():
    """Integration test with real database."""
    print("\n=== Test: Integration ===")

    db_path = "data/iv_history.db"
    if not os.path.exists(db_path):
        print("⚠️  Skipping - no database at data/iv_history.db")
        return True

    try:
        tracker = IVHistoryTracker(db_path=db_path)
        conn = tracker._get_connection()
        cursor = conn.execute(
            """SELECT DISTINCT ticker FROM iv_history
               WHERE date >= date('now', '-10 days') LIMIT 3"""
        )
        tickers = [row['ticker'] for row in cursor]

        if not tickers:
            print("⚠️  No recent data in database")
            tracker.close()
            return True

        print(f"Testing: {', '.join(tickers)}")

        for ticker in tickers:
            cursor = conn.execute(
                """SELECT iv_value FROM iv_history
                   WHERE ticker = ? ORDER BY date DESC LIMIT 1""",
                (ticker,)
            )
            row = cursor.fetchone()

            if row:
                current_iv = row['iv_value']
                weekly_change = tracker.get_weekly_iv_change(ticker, current_iv)

                if weekly_change is not None:
                    print(f"✅ {ticker}: IV={current_iv:.1f}%, Change={weekly_change:+.1f}%")
                else:
                    print(f"⚠️  {ticker}: Insufficient history")

        tracker.close()
        return True
    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        return False


def run_all_tests():
    """Run all tests."""
    print("=" * 70)
    print("IV EXPANSION SCORING TESTS")
    print("=" * 70)

    tests = [
        ("Weekly IV Change", test_weekly_iv_change),
        ("IVExpansionScorer", test_iv_expansion_scorer),
        ("CompositeScorer", test_composite_scorer),
        ("Simplified IVScorer", test_iv_scorer_simplified),
        ("Integration", test_integration_with_db),
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

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, success in results if success)
    for test_name, success in results:
        print(f"{'✅ PASS' if success else '❌ FAIL'}: {test_name}")

    print("=" * 70)
    print(f"Results: {passed}/{len(results)} passed")
    print("=" * 70)

    return passed == len(results)


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
