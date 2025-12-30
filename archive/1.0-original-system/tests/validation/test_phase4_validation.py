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
    # Test chunked generator
    items = list(range(100))
    chunks = list(chunked(items, 25))
    assert len(chunks) == 4, f"Expected 4 chunks, got {len(chunks)}"
    assert all(len(chunk) == 25 for chunk in chunks), "All chunks should be size 25"

    # Test filtered generator
    numbers = list(range(20))
    evens = list(filtered_generator(numbers, lambda x: x % 2 == 0))
    assert len(evens) == 10, f"Expected 10 even numbers, got {len(evens)}"
    assert all(n % 2 == 0 for n in evens), "All numbers should be even"

    # Test lazy_map
    squares = list(lazy_map(lambda x: x * x, [1, 2, 3, 4, 5]))
    assert squares == [1, 4, 9, 16, 25], f"Expected [1,4,9,16,25], got {squares}"

    # Test sliding window
    prices = [10, 12, 11, 13, 15, 14, 16]
    windows = list(sliding_window(prices, 3))
    assert len(windows) == 5, f"Expected 5 windows, got {len(windows)}"
    assert windows[0] == [10, 12, 11], f"First window should be [10,12,11]"


def test_batch_processing():
    """Test batch processing generators."""
    items = list(range(100))
    results = []

    def processor(x):
        return x * 2

    # Process in batches
    for result in batch_process_generator(items, processor, chunk_size=25, log_progress=False):
        results.append(result)

    assert len(results) == 100, f"Expected 100 results, got {len(results)}"
    assert results[0] == 0, f"First result should be 0, got {results[0]}"
    assert results[99] == 198, f"Last result should be 198, got {results[99]}"


def test_ticker_stream():
    """Test ticker stream generator."""
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
    assert len(results) == 2, f"Expected 2 results (MSFT, TSLA), got {len(results)}"


def test_config_file():
    """Test performance.yaml configuration file."""
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
    assert not missing, f"Config missing sections: {missing}"

    # Verify some key values
    assert config['rate_limiting']['tradier']['rate'] == 120, "Tradier rate should be 120"
    assert config['batch_processing']['yfinance_chunk_size'] == 50, "yfinance chunk size should be 50"
    assert config['circuit_breaker']['failure_threshold'] == 5, "Circuit breaker threshold should be 5"


def test_error_messages():
    """Test enhanced error messages."""
    # Test API rate limit error
    error = api_rate_limit_error(
        api_name="Tradier",
        current_usage=120,
        limit=120,
        reset_time="60 seconds"
    )

    formatted = error.format()
    assert "Tradier" in formatted, "Error should contain 'Tradier'"
    assert "120/120" in formatted, "Error should contain '120/120'"
    assert "Suggestion" in formatted, "Error should contain 'Suggestion'"

    # Test ticker not found error
    error2 = ticker_not_found_error(
        ticker="INVALID",
        searched_sources=["yfinance", "tradier"]
    )

    formatted2 = error2.format()
    assert "INVALID" in formatted2, "Error should contain 'INVALID'"
    assert "yfinance" in formatted2, "Error should contain 'yfinance'"

    # Test insufficient data error
    error3 = insufficient_data_error(
        ticker="AAPL",
        missing_fields=["iv_rank", "options_volume"],
        data_source="yfinance"
    )

    formatted3 = error3.format()
    assert "AAPL" in formatted3, "Error should contain 'AAPL'"
    assert "iv_rank" in formatted3, "Error should contain 'iv_rank'"

    # Test validation error
    error4 = validation_error(
        field_name="price",
        expected_type="float",
        actual_value="not_a_number",
        ticker="AAPL"
    )

    assert "price" in error4.format(), "Error should contain 'price'"
    assert "float" in error4.format(), "Error should contain 'float'"

    # Test generic error formatting
    try:
        raise ValueError("Test error")
    except ValueError as e:
        formatted_exc = format_error(
            e,
            context={"ticker": "AAPL", "operation": "fetch_data"},
            suggestion="Check API credentials"
        )
        assert "ValueError" in formatted_exc, "Error should contain 'ValueError'"
        assert "AAPL" in formatted_exc, "Error should contain 'AAPL'"


def test_command_pattern():
    """Test command pattern with undo/redo."""
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
    assert result == "executed", f"Expected 'executed', got {result}"
    assert execute_count == 1, f"Execute count should be 1, got {execute_count}"

    # Undo command
    success = history.undo()
    assert success, "Undo should succeed"
    assert undo_count == 1, f"Undo count should be 1, got {undo_count}"

    # Redo command
    success = history.redo()
    assert success, "Redo should succeed"
    assert execute_count == 2, f"Execute count should be 2 after redo, got {execute_count}"

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
    assert obj.value == 20, f"Object value should be 20, got {obj.value}"

    # Undo modification
    history2.undo()
    assert obj.value == 10, f"Object value should be 10 after undo, got {obj.value}"

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
    assert len(history_list) == 3, f"History should have 3 items, got {len(history_list)}"


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
