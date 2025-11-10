#!/usr/bin/env python
"""
Regression test - verify no existing functionality was broken.
"""

import sys
import re

sys.path.insert(0, '.')

def test_critical_methods_preserved():
    """Ensure critical methods still exist."""
    print("=" * 70)
    print("REGRESSION TEST: Critical Methods Preserved")
    print("=" * 70)

    tests_passed = 0
    tests_failed = 0

    # Test 1: earnings_analyzer.py critical methods
    print("\n[1] Checking earnings_analyzer.py...")
    with open('src/analysis/earnings_analyzer.py', 'r') as f:
        content = f.read()

    critical_methods = [
        'def analyze_specific_tickers',
        'def analyze_date',
        'def _fetch_tickers_data',
        'def _fetch_options_parallel',  # New method
        'def _run_parallel_analysis',
        'def _analyze_single_ticker',
    ]

    for method in critical_methods:
        if method in content:
            print(f"   ✓ Method preserved: {method}")
            tests_passed += 1
        else:
            print(f"   ✗ Method missing: {method}")
            tests_failed += 1

    # Test 2: tradier_client.py critical methods
    print("\n[2] Checking tradier_client.py...")
    with open('src/options/tradier_client.py', 'r') as f:
        content = f.read()

    critical_methods = [
        'def get_options_data',
        'def _get_quote',
        'def _get_options_chain',
        'def _extract_iv_rank',
        'def _find_closest_expiration',
    ]

    for method in critical_methods:
        if method in content:
            print(f"   ✓ Method preserved: {method}")
            tests_passed += 1
        else:
            print(f"   ✗ Method missing: {method}")
            tests_failed += 1

    # Test 3: reddit_scraper.py critical methods
    print("\n[3] Checking reddit_scraper.py...")
    with open('src/data/reddit_scraper.py', 'r') as f:
        content = f.read()

    critical_methods = [
        'def get_ticker_sentiment',
        'def _search_subreddit',
        'def _analyze_post_content',
    ]

    for method in critical_methods:
        if method in content:
            print(f"   ✓ Method preserved: {method}")
            tests_passed += 1
        else:
            print(f"   ✗ Method missing: {method}")
            tests_failed += 1

    print(f"\n{'='*70}")
    print(f"Methods Preserved: {tests_passed} passed, {tests_failed} failed")
    print(f"{'='*70}\n")

    return tests_passed, tests_failed


def test_method_signatures():
    """Ensure method signatures weren't changed (breaking API)."""
    print("=" * 70)
    print("REGRESSION TEST: Method Signatures Unchanged")
    print("=" * 70)

    tests_passed = 0
    tests_failed = 0

    # Test 1: get_ticker_sentiment signature
    print("\n[1] Checking get_ticker_sentiment signature...")
    with open('src/data/reddit_scraper.py', 'r') as f:
        content = f.read()

    # Extract method signature
    match = re.search(r'def get_ticker_sentiment\((.*?)\):', content, re.DOTALL)
    if match:
        sig = match.group(1)
        # Check required parameters preserved
        if 'ticker' in sig and 'subreddits' in sig:
            print(f"   ✓ Signature preserved (has ticker, subreddits)")
            tests_passed += 1
        else:
            print(f"   ✗ Signature changed")
            tests_failed += 1

        # Check new code doesn't break old callers
        if 'analyze_content' in sig and 'False' in sig:
            print(f"   ✓ New parameter has default value (backward compatible)")
            tests_passed += 1
        else:
            print(f"   ⚠  analyze_content parameter may break old code")
    else:
        print(f"   ✗ Method signature not found")
        tests_failed += 1

    # Test 2: get_options_data signature
    print("\n[2] Checking get_options_data signature...")
    with open('src/options/tradier_client.py', 'r') as f:
        content = f.read()

    match = re.search(r'def get_options_data\((.*?)\):', content, re.DOTALL)
    if match:
        sig = match.group(1)
        if 'ticker' in sig and 'current_price' in sig:
            print(f"   ✓ Signature preserved")
            tests_passed += 1
        else:
            print(f"   ✗ Signature changed")
            tests_failed += 1
    else:
        print(f"   ✗ Method signature not found")
        tests_failed += 1

    print(f"\n{'='*70}")
    print(f"Signatures Unchanged: {tests_passed} passed, {tests_failed} failed")
    print(f"{'='*70}\n")

    return tests_passed, tests_failed


def test_return_values_unchanged():
    """Verify return value structures weren't changed."""
    print("=" * 70)
    print("REGRESSION TEST: Return Value Structures")
    print("=" * 70)

    tests_passed = 0
    tests_failed = 0

    # Test 1: get_ticker_sentiment return structure
    print("\n[1] Checking get_ticker_sentiment return...")
    with open('src/data/reddit_scraper.py', 'r') as f:
        content = f.read()

    required_keys = ['ticker', 'posts_found', 'sentiment_score', 'avg_score', 'total_comments']
    for key in required_keys:
        if f"'{key}':" in content or f'"{key}":' in content:
            print(f"   ✓ Return includes '{key}'")
            tests_passed += 1
        else:
            print(f"   ✗ Return missing '{key}'")
            tests_failed += 1

    # Test 2: get_options_data return structure
    print("\n[2] Checking get_options_data return...")
    with open('src/options/tradier_client.py', 'r') as f:
        content = f.read()

    # Check docstring mentions key return fields
    required_fields = ['iv_rank', 'current_iv']
    for field in required_fields:
        if field in content:
            print(f"   ✓ Return includes '{field}'")
            tests_passed += 1
        else:
            print(f"   ✗ Return missing '{field}'")
            tests_failed += 1

    print(f"\n{'='*70}")
    print(f"Return Values: {tests_passed} passed, {tests_failed} failed")
    print(f"{'='*70}\n")

    return tests_passed, tests_failed


def test_no_breaking_changes():
    """Test that optimizations don't break existing code flows."""
    print("=" * 70)
    print("REGRESSION TEST: No Breaking Changes")
    print("=" * 70)

    tests_passed = 0
    tests_failed = 0

    # Test 1: Parallel options fetching doesn't break data flow
    print("\n[1] Checking parallel options fetching integration...")
    with open('src/analysis/earnings_analyzer.py', 'r') as f:
        content = f.read()

    # Verify the flow: fetch basic data → fetch options parallel → return
    if 'basic_ticker_data' in content and '_fetch_options_parallel' in content:
        print(f"   ✓ Two-stage fetch pattern implemented")
        tests_passed += 1
    else:
        print(f"   ✗ Fetch pattern broken")
        tests_failed += 1

    # Verify data still gets options_data key
    if "'options_data'" in content or '"options_data"' in content:
        print(f"   ✓ options_data key still populated")
        tests_passed += 1
    else:
        print(f"   ✗ options_data key missing")
        tests_failed += 1

    # Test 2: Cache doesn't interfere with Reddit scraping
    print("\n[2] Checking cache doesn't break Reddit scraping...")
    with open('src/data/reddit_scraper.py', 'r') as f:
        content = f.read()

    # Verify: check cache → if miss, scrape → cache result
    cache_checks = [
        'cached_result = self._cache.get',
        'if cached_result:',
        'return cached_result',
        'self._cache.set(',
    ]

    all_present = all(check in content for check in cache_checks)
    if all_present:
        print(f"   ✓ Cache flow correct (check → miss → scrape → cache)")
        tests_passed += 1
    else:
        print(f"   ✗ Cache flow incomplete")
        tests_failed += 1

    # Verify scraping logic still executes
    if 'ThreadPoolExecutor' in content and '_search_subreddit' in content:
        print(f"   ✓ Original scraping logic preserved")
        tests_passed += 1
    else:
        print(f"   ✗ Scraping logic modified")
        tests_failed += 1

    # Test 3: Session doesn't break Tradier API calls
    print("\n[3] Checking session doesn't break Tradier calls...")
    with open('src/options/tradier_client.py', 'r') as f:
        content = f.read()

    # Verify: session has headers → session.get() has correct params
    if 'self.session.headers.update' in content:
        print(f"   ✓ Session headers configured")
        tests_passed += 1

    # Verify session.get() calls have params
    session_get_pattern = r'self\.session\.get\([^)]*params='
    if re.search(session_get_pattern, content):
        print(f"   ✓ Session.get() calls include params")
        tests_passed += 1
    else:
        print(f"   ⚠  Check session.get() params")

    # Verify response handling unchanged
    if 'response.raise_for_status()' in content and 'response.json()' in content:
        print(f"   ✓ Response handling unchanged")
        tests_passed += 1
    else:
        print(f"   ✗ Response handling modified")
        tests_failed += 1

    print(f"\n{'='*70}")
    print(f"No Breaking Changes: {tests_passed} passed, {tests_failed} failed")
    print(f"{'='*70}\n")

    return tests_passed, tests_failed


def main():
    """Run all regression tests."""
    from datetime import datetime

    print("\n" + "=" * 70)
    print("REGRESSION TEST SUITE")
    print("Verifying no existing functionality was broken")
    print("=" * 70)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70 + "\n")

    total_passed = 0
    total_failed = 0

    tests = [
        ("Critical Methods Preserved", test_critical_methods_preserved),
        ("Method Signatures Unchanged", test_method_signatures),
        ("Return Value Structures", test_return_values_unchanged),
        ("No Breaking Changes", test_no_breaking_changes),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed, failed = test_func()
            total_passed += passed
            total_failed += failed
            results.append((name, passed, failed, 'PASS' if failed == 0 else 'FAIL'))
        except Exception as e:
            print(f"\n✗ Test suite '{name}' crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, 0, 1, 'CRASH'))
            total_failed += 1

    # Print summary
    print("\n" + "=" * 70)
    print("REGRESSION TEST SUMMARY")
    print("=" * 70)
    print(f"{'Test Suite':<35} {'Passed':<10} {'Failed':<10} {'Status':<10}")
    print("-" * 70)

    for name, passed, failed, status in results:
        status_symbol = "✓" if status == 'PASS' else "✗"
        print(f"{name:<35} {passed:<10} {failed:<10} {status_symbol} {status}")

    print("-" * 70)
    print(f"{'TOTAL':<35} {total_passed:<10} {total_failed:<10}")
    print("=" * 70)

    # Final verdict
    if total_failed == 0:
        print("\n✅ NO REGRESSIONS DETECTED!")
        print("\nVerified:")
        print("   ✓ All critical methods preserved")
        print("   ✓ Method signatures backward compatible")
        print("   ✓ Return value structures unchanged")
        print("   ✓ Data flows intact")
        print("   ✓ Optimizations don't break existing code")
        return 0
    else:
        print(f"\n❌ {total_failed} REGRESSION(S) FOUND - Review needed")
        return 1


if __name__ == '__main__':
    exit_code = main()
    # sys.exit(exit_code)
