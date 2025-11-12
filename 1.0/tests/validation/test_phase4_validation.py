#!/usr/bin/env python3
"""Comprehensive Phase 4 validation tests."""

import sys
import yaml
from typing import List

# Test imports
try:
    from src.core.generators import (
        chunked,
        filtered_generator,
        batch_process_generator,
        ticker_stream_generator,
        lazy_map,
        sliding_window
    )
    from src.core.error_messages import (
        ErrorMessage,
        api_rate_limit_error,
        ticker_not_found_error,
        insufficient_data_error,
        validation_error,
        format_error
    )
    from src.core.command_pattern import (
        Command,
        CommandHistory,
        FunctionCommand,
        DataModificationCommand
    )
    print("✅ Phase 4 imports successful")
except Exception as e:
    print(f"❌ Phase 4 import failed: {e}")
    import traceback
    traceback.print_exc()
    # sys.exit(1)


def test_generators():
    """Test generator functions for memory efficiency."""
    print("\n=== Testing Generators ===")

    # Test chunked generator
    items = list(range(100))
    chunks = list(chunked(items, 25))

    if len(chunks) == 4 and all(len(chunk) == 25 for chunk in chunks):
        print(f"✅ Chunked generator: 100 items → {len(chunks)} chunks of 25")
    else:
        print(f"❌ Chunked generator failed: got {len(chunks)} chunks")
        return False

    # Test filtered generator
    numbers = list(range(20))
    evens = list(filtered_generator(numbers, lambda x: x % 2 == 0))

    if len(evens) == 10 and all(n % 2 == 0 for n in evens):
        print(f"✅ Filtered generator: {len(evens)}/20 items passed filter")
    else:
        print("❌ Filtered generator failed")
        return False

    # Test lazy_map
    squares = list(lazy_map(lambda x: x * x, [1, 2, 3, 4, 5]))

    if squares == [1, 4, 9, 16, 25]:
        print(f"✅ Lazy map: transformed {len(squares)} items")
    else:
        print(f"❌ Lazy map failed: {squares}")
        return False

    # Test sliding window
    prices = [10, 12, 11, 13, 15, 14, 16]
    windows = list(sliding_window(prices, 3))

    if len(windows) == 5 and windows[0] == [10, 12, 11]:
        print(f"✅ Sliding window: created {len(windows)} windows of size 3")
    else:
        print(f"❌ Sliding window failed")
        return False

    return True


def test_batch_processing():
    """Test batch processing generators."""
    print("\n=== Testing Batch Processing ===")

    items = list(range(100))
    results = []

    def processor(x):
        return x * 2

    # Process in batches
    for result in batch_process_generator(items, processor, chunk_size=25, log_progress=False):
        results.append(result)

    if len(results) == 100 and results[0] == 0 and results[99] == 198:
        print(f"✅ Batch processing: processed {len(results)} items in chunks")
    else:
        print(f"❌ Batch processing failed: {len(results)} results")
        return False

    return True


def test_ticker_stream():
    """Test ticker stream generator."""
    print("\n=== Testing Ticker Stream Generator ===")

    tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']

    def mock_fetcher(ticker):
        return {'ticker': ticker, 'score': ord(ticker[0])}  # Simple score based on first letter

    def filter_func(data):
        return data['score'] > 75  # Filter for tickers starting with M or later

    results = list(ticker_stream_generator(
        tickers=tickers,
        fetcher=mock_fetcher,
        filter_func=filter_func,
        chunk_size=2
    ))

    # M=77, T=84 pass filter (>75), A=65, G=71 don't
    if len(results) == 2:  # MSFT, TSLA
        print(f"✅ Ticker stream: {len(results)}/5 tickers passed filter")
    else:
        print(f"❌ Ticker stream failed: {len(results)} results (expected 2)")
        return False

    return True


def test_config_file():
    """Test performance.yaml configuration file."""
    print("\n=== Testing Configuration File ===")

    try:
        with open('config/performance.yaml', 'r') as f:
            config = yaml.safe_load(f)

        # Verify key sections exist
        required_sections = [
            'rate_limiting',
            'batch_processing',
            'caching',
            'circuit_breaker',
            'memory',
            'timeouts',
            'filtering',
            'logging'
        ]

        missing = [s for s in required_sections if s not in config]
        if missing:
            print(f"❌ Config missing sections: {missing}")
            return False

        print(f"✅ Config file has all {len(required_sections)} required sections")

        # Verify some key values
        if config['rate_limiting']['tradier']['rate'] == 120:
            print("✅ Config values loaded correctly (tradier rate: 120)")
        else:
            print("❌ Config values incorrect")
            return False

        if config['batch_processing']['yfinance_chunk_size'] == 50:
            print("✅ Config batch size: 50")
        else:
            print("❌ Config batch size incorrect")
            return False

        if config['circuit_breaker']['failure_threshold'] == 5:
            print("✅ Config circuit breaker threshold: 5")
        else:
            print("❌ Config circuit breaker threshold incorrect")
            return False

    except FileNotFoundError:
        print("❌ config/performance.yaml not found")
        return False
    except yaml.YAMLError as e:
        print(f"❌ YAML parsing error: {e}")
        return False

    return True


def test_error_messages():
    """Test enhanced error messages."""
    print("\n=== Testing Enhanced Error Messages ===")

    # Test API rate limit error
    error = api_rate_limit_error(
        api_name="Tradier",
        current_usage=120,
        limit=120,
        reset_time="60 seconds"
    )

    formatted = error.format()
    if "Tradier" in formatted and "120/120" in formatted and "Suggestion" in formatted:
        print("✅ API rate limit error formatted correctly")
    else:
        print("❌ API rate limit error formatting failed")
        return False

    # Test ticker not found error
    error2 = ticker_not_found_error(
        ticker="INVALID",
        searched_sources=["yfinance", "tradier"]
    )

    formatted2 = error2.format()
    if "INVALID" in formatted2 and "yfinance" in formatted2:
        print("✅ Ticker not found error formatted correctly")
    else:
        print("❌ Ticker not found error formatting failed")
        return False

    # Test insufficient data error
    error3 = insufficient_data_error(
        ticker="AAPL",
        missing_fields=["iv_rank", "options_volume"],
        data_source="yfinance"
    )

    formatted3 = error3.format()
    if "AAPL" in formatted3 and "iv_rank" in formatted3:
        print("✅ Insufficient data error formatted correctly")
    else:
        print("❌ Insufficient data error formatting failed")
        return False

    # Test validation error
    error4 = validation_error(
        field_name="price",
        expected_type="float",
        actual_value="not_a_number",
        ticker="AAPL"
    )

    if "price" in error4.format() and "float" in error4.format():
        print("✅ Validation error formatted correctly")
    else:
        print("❌ Validation error formatting failed")
        return False

    # Test generic error formatting
    try:
        raise ValueError("Test error")
    except ValueError as e:
        formatted_exc = format_error(
            e,
            context={"ticker": "AAPL", "operation": "fetch_data"},
            suggestion="Check API credentials"
        )
        if "ValueError" in formatted_exc and "AAPL" in formatted_exc:
            print("✅ Generic error formatting works")
        else:
            print("❌ Generic error formatting failed")
            return False

    return True


def test_command_pattern():
    """Test command pattern with undo/redo."""
    print("\n=== Testing Command Pattern ===")

    # Test basic command execution
    history = CommandHistory(max_history=10)

    execute_count = 0
    undo_count = 0

    def execute_func():
        nonlocal execute_count
        execute_count += 1
        return "executed"

    def undo_func():
        nonlocal undo_count
        undo_count += 1

    cmd = FunctionCommand(
        execute_func=execute_func,
        undo_func=undo_func,
        description="Test command"
    )

    # Execute command
    result = history.execute(cmd)

    if result == "executed" and execute_count == 1:
        print("✅ Command execution works")
    else:
        print("❌ Command execution failed")
        return False

    # Undo command
    success = history.undo()

    if success and undo_count == 1:
        print("✅ Command undo works")
    else:
        print("❌ Command undo failed")
        return False

    # Redo command
    success = history.redo()

    if success and execute_count == 2:
        print("✅ Command redo works")
    else:
        print("❌ Command redo failed")
        return False

    # Test DataModificationCommand
    class TestObject:
        def __init__(self):
            self.value = 10

    obj = TestObject()
    mod_cmd = DataModificationCommand(
        target=obj,
        attribute='value',
        new_value=20,
        description="Set value to 20"
    )

    history2 = CommandHistory()
    history2.execute(mod_cmd)

    if obj.value == 20:
        print("✅ Data modification command execution works")
    else:
        print("❌ Data modification command failed")
        return False

    # Undo modification
    history2.undo()

    if obj.value == 10:
        print("✅ Data modification command undo works")
    else:
        print("❌ Data modification undo failed")
        return False

    # Test history limits
    history3 = CommandHistory(max_history=3)
    for i in range(5):
        cmd = FunctionCommand(
            execute_func=lambda: i,
            undo_func=lambda: None,
            description=f"Command {i}"
        )
        history3.execute(cmd)

    history_list = history3.get_history()
    if len(history_list) == 3:  # Should only keep last 3
        print(f"✅ Command history respects max_history limit ({len(history_list)}/3)")
    else:
        print(f"❌ Command history limit failed: {len(history_list)}")
        return False

    return True


if __name__ == '__main__':
    print("=" * 70)
    print("PHASE 4 VALIDATION TEST SUITE")
    print("Testing: Generators, Config, Error Messages, Command Pattern")
    print("=" * 70)

    all_passed = True

    try:
        if not test_generators():
            all_passed = False
            print("\n❌ Generator tests failed")

        if not test_batch_processing():
            all_passed = False
            print("\n❌ Batch processing tests failed")

        if not test_ticker_stream():
            all_passed = False
            print("\n❌ Ticker stream tests failed")

        if not test_config_file():
            all_passed = False
            print("\n❌ Config file tests failed")

        if not test_error_messages():
            all_passed = False
            print("\n❌ Error message tests failed")

        if not test_command_pattern():
            all_passed = False
            print("\n❌ Command pattern tests failed")

        print("\n" + "=" * 70)
        if all_passed:
            print("✅ ALL PHASE 4 TESTS PASSED!")
        else:
            print("❌ SOME PHASE 4 TESTS FAILED")
            sys.exit(1)
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ Unexpected error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
