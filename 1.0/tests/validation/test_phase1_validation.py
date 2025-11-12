#!/usr/bin/env python3
"""Comprehensive Phase 1 validation tests."""

import sys
from typing import Dict, Any

# Test imports
try:
    from src.core.types import (
        TickerData, OptionsData, AnalysisResult,
        OptionContract, SentimentData, StrategyData
    )
    from src.core.validators import (
        validate_options_data,
        validate_ticker_data,
        validate_analysis_result,
        ValidationError
    )
    print("✅ Phase 1 imports successful")
except Exception as e:
    print(f"❌ Phase 1 import failed: {e}")
    # sys.exit(1)


def test_types():
    """Test TypedDict definitions."""
    print("\n=== Testing TypedDict Definitions ===")

    # Test OptionsData
    options: OptionsData = {
        'current_iv': 75.5,
        'iv_rank': 85.0,
        'expected_move_pct': 8.5,
        'options_volume': 15000,
        'open_interest': 50000,
        'iv_crush_ratio': 1.25,
        'data_source': 'tradier'
    }
    print(f"✅ OptionsData created: IV={options['current_iv']}%, IV Rank={options['iv_rank']}%")

    # Test TickerData
    ticker: TickerData = {
        'ticker': 'AAPL',
        'price': 175.50,
        'market_cap': 2_800_000_000_000,
        'options_data': options,
        'score': 87.5
    }
    print(f"✅ TickerData created: {ticker['ticker']} @ ${ticker['price']}, Score={ticker['score']}")

    # Test AnalysisResult
    result: AnalysisResult = {
        'ticker': 'AAPL',
        'earnings_date': '2024-01-25',
        'price': 175.50,
        'score': 87.5,
        'options_data': options
    }
    print(f"✅ AnalysisResult created: {result['ticker']} earnings on {result['earnings_date']}")

    return True


def test_validators():
    """Test validation functions."""
    print("\n=== Testing Validators ===")

    # Test valid OptionsData
    valid_options: Dict[str, Any] = {
        'current_iv': 75.5,
        'iv_rank': 85.0,
        'expected_move_pct': 8.5,
        'options_volume': 15000,
        'open_interest': 50000,
        'data_source': 'tradier'
    }

    if validate_options_data(valid_options):
        print("✅ Valid OptionsData passed validation")
    else:
        print("❌ Valid OptionsData failed validation")
        return False

    # Test invalid OptionsData (wrong type)
    invalid_options: Dict[str, Any] = {
        'current_iv': 'not a number',  # Should be float
        'iv_rank': 85.0
    }

    if not validate_options_data(invalid_options):
        print("✅ Invalid OptionsData correctly rejected")
    else:
        print("❌ Invalid OptionsData incorrectly accepted")
        return False

    # Test valid TickerData
    valid_ticker: Dict[str, Any] = {
        'ticker': 'AAPL',
        'price': 175.50,
        'market_cap': 2_800_000_000_000,
        'score': 87.5,
        'options_data': valid_options
    }

    if validate_ticker_data(valid_ticker):
        print("✅ Valid TickerData passed validation")
    else:
        print("❌ Valid TickerData failed validation")
        return False

    # Test None handling (optional fields)
    ticker_with_none: Dict[str, Any] = {
        'ticker': 'AAPL',
        'price': 175.50,
        'market_cap': None,  # Optional field can be None
        'score': 87.5
    }

    if validate_ticker_data(ticker_with_none):
        print("✅ TickerData with None values passed validation")
    else:
        print("❌ TickerData with None values failed validation")
        return False

    # Test strict mode
    try:
        validate_options_data(invalid_options, strict=True)
        print("❌ Strict mode should have raised ValidationError")
        return False
    except ValidationError as e:
        print(f"✅ Strict mode correctly raised ValidationError: {e}")

    return True


def test_type_coverage():
    """Verify type hints are comprehensive."""
    print("\n=== Testing Type Coverage ===")

    import inspect
    from src.core import validators

    # Check that all validation functions have proper type hints
    for name, obj in inspect.getmembers(validators):
        if inspect.isfunction(obj) and name.startswith('validate_'):
            sig = inspect.signature(obj)
            has_return_type = sig.return_annotation != inspect.Signature.empty

            if has_return_type:
                print(f"✅ {name} has return type annotation")
            else:
                print(f"❌ {name} missing return type annotation")
                return False

    return True


if __name__ == '__main__':
    print("=" * 70)
    print("PHASE 1 VALIDATION TEST SUITE")
    print("Testing: Types, Validators, Type Coverage")
    print("=" * 70)

    all_passed = True

    try:
        if not test_types():
            all_passed = False
            print("\n❌ TypedDict tests failed")

        if not test_validators():
            all_passed = False
            print("\n❌ Validator tests failed")

        if not test_type_coverage():
            all_passed = False
            print("\n❌ Type coverage tests failed")

        print("\n" + "=" * 70)
        if all_passed:
            print("✅ ALL PHASE 1 TESTS PASSED!")
        else:
            print("❌ SOME PHASE 1 TESTS FAILED")
            sys.exit(1)
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ Unexpected error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
