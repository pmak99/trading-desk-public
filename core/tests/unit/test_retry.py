"""Unit tests for retry decorator."""

import asyncio
import pytest
import time
from unittest.mock import Mock

from src.utils.retry import async_retry, sync_retry

# Mark all async tests with pytest-asyncio
pytestmark = pytest.mark.asyncio


class TestSyncRetry:
    """Tests for sync_retry decorator."""

    def test_success_on_first_attempt(self):
        """Test that function succeeds on first attempt."""
        mock_func = Mock(return_value="success")
        decorated = sync_retry(max_attempts=3)(mock_func)

        result = decorated()

        assert result == "success"
        assert mock_func.call_count == 1

    def test_success_after_retries(self):
        """Test that function succeeds after failures."""
        mock_func = Mock(side_effect=[ValueError("fail"), ValueError("fail"), "success"])
        mock_func.__name__ = "test_func"
        decorated = sync_retry(max_attempts=3, backoff_base=0.01)(mock_func)

        result = decorated()

        assert result == "success"
        assert mock_func.call_count == 3

    def test_failure_after_max_attempts(self):
        """Test that function raises after max attempts."""
        mock_func = Mock(side_effect=ValueError("always fails"))
        mock_func.__name__ = "test_func"
        decorated = sync_retry(max_attempts=3, backoff_base=0.01)(mock_func)

        with pytest.raises(ValueError, match="always fails"):
            decorated()

        assert mock_func.call_count == 3

    def test_exponential_backoff(self):
        """Test that backoff time increases exponentially."""
        call_times = []

        def failing_func():
            call_times.append(time.time())
            raise ValueError("fail")

        # Use backoff_base=2.0 so delays increase (2^0=1, 2^1=2, 2^2=4)
        decorated = sync_retry(max_attempts=3, backoff_base=2.0, jitter=False)(failing_func)

        with pytest.raises(ValueError):
            decorated()

        # Check that delays increase (with some tolerance for timing)
        assert len(call_times) == 3
        delay1 = call_times[1] - call_times[0]
        delay2 = call_times[2] - call_times[1]

        # With backoff_base=2.0: delay1~1s, delay2~2s
        assert delay1 >= 0.9
        assert delay2 >= 1.8
        assert delay2 > delay1 * 1.5

    def test_specific_exception_type(self):
        """Test that only specified exceptions are retried."""
        mock_func = Mock(side_effect=TypeError("wrong type"))
        decorated = sync_retry(max_attempts=3, exceptions=(ValueError,))(mock_func)

        # TypeError should not be retried
        with pytest.raises(TypeError):
            decorated()

        assert mock_func.call_count == 1

    def test_max_backoff_limit(self):
        """Test that backoff doesn't exceed max_backoff."""
        call_times = []

        def failing_func():
            call_times.append(time.time())
            raise ValueError("fail")

        # With high backoff_base but low max_backoff
        decorated = sync_retry(
            max_attempts=4, backoff_base=10.0, max_backoff=0.1, jitter=False
        )(failing_func)

        with pytest.raises(ValueError):
            decorated()

        # All delays should be <= max_backoff
        for i in range(1, len(call_times)):
            delay = call_times[i] - call_times[i - 1]
            assert delay <= 0.2  # Some tolerance for timing


class TestAsyncRetry:
    """Tests for async_retry decorator."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        """Test that async function succeeds on first attempt."""
        mock_func = Mock(return_value="success")

        @async_retry(max_attempts=3)
        async def test_func():
            return mock_func()

        result = await test_func()

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retries(self):
        """Test that async function succeeds after failures."""
        mock_func = Mock(side_effect=[ValueError("fail"), ValueError("fail"), "success"])

        @async_retry(max_attempts=3, backoff_base=0.01)
        async def test_func():
            return mock_func()

        result = await test_func()

        assert result == "success"
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_failure_after_max_attempts(self):
        """Test that async function raises after max attempts."""
        mock_func = Mock(side_effect=ValueError("always fails"))

        @async_retry(max_attempts=3, backoff_base=0.01)
        async def test_func():
            return mock_func()

        with pytest.raises(ValueError, match="always fails"):
            await test_func()

        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_exponential_backoff_async(self):
        """Test that async backoff time increases exponentially."""
        call_times = []

        @async_retry(max_attempts=3, backoff_base=2.0, jitter=False)
        async def failing_func():
            call_times.append(time.time())
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await failing_func()

        # Check that delays increase
        assert len(call_times) == 3
        delay1 = call_times[1] - call_times[0]
        delay2 = call_times[2] - call_times[1]

        # With backoff_base=2.0: delay1~1s, delay2~2s
        assert delay1 >= 0.9
        assert delay2 >= 1.8
        assert delay2 > delay1 * 1.5

    @pytest.mark.asyncio
    async def test_concurrent_retries(self):
        """Test that multiple async retries can run concurrently."""
        call_count = {"a": 0, "b": 0}

        @async_retry(max_attempts=2, backoff_base=0.01)
        async def task_a():
            call_count["a"] += 1
            if call_count["a"] == 1:
                raise ValueError("fail once")
            return "a_success"

        @async_retry(max_attempts=2, backoff_base=0.01)
        async def task_b():
            call_count["b"] += 1
            if call_count["b"] == 1:
                raise ValueError("fail once")
            return "b_success"

        results = await asyncio.gather(task_a(), task_b())

        assert results == ["a_success", "b_success"]
        assert call_count["a"] == 2
        assert call_count["b"] == 2
