"""Unit tests for circuit breaker."""

import pytest
import time
from unittest.mock import Mock
from datetime import datetime, timedelta

from src.utils.circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerOpenError


class TestCircuitBreaker:
    """Tests for CircuitBreaker class."""

    def test_initial_state_is_closed(self):
        """Test that circuit starts in CLOSED state."""
        breaker = CircuitBreaker("test", failure_threshold=3)

        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_successful_call_in_closed_state(self):
        """Test that successful calls work in CLOSED state."""
        breaker = CircuitBreaker("test", failure_threshold=3)
        mock_func = Mock(return_value="success")

        result = breaker.call(mock_func, "arg1", kwarg="value")

        assert result == "success"
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0
        mock_func.assert_called_once_with("arg1", kwarg="value")

    def test_failure_increments_count(self):
        """Test that failures increment failure count."""
        breaker = CircuitBreaker("test", failure_threshold=3)
        mock_func = Mock(side_effect=ValueError("error"))

        with pytest.raises(ValueError):
            breaker.call(mock_func)

        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 1

    def test_circuit_opens_after_threshold(self):
        """Test that circuit opens after reaching failure threshold."""
        breaker = CircuitBreaker("test", failure_threshold=3)
        mock_func = Mock(side_effect=ValueError("error"))

        # First 2 failures - should stay closed
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(mock_func)
            assert breaker.state == CircuitState.CLOSED

        # Third failure - should open
        with pytest.raises(ValueError):
            breaker.call(mock_func)

        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3

    def test_open_circuit_blocks_calls(self):
        """Test that OPEN circuit blocks all calls."""
        breaker = CircuitBreaker("test", failure_threshold=2)
        mock_func = Mock(side_effect=ValueError("error"))

        # Trigger circuit to open
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(mock_func)

        # Now circuit is open - should block without calling function
        mock_func.reset_mock()

        with pytest.raises(CircuitBreakerOpenError, match="Circuit test is OPEN"):
            breaker.call(mock_func)

        assert mock_func.call_count == 0

    def test_half_open_state_after_timeout(self):
        """Test that circuit enters HALF_OPEN after recovery timeout."""
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        mock_func = Mock(side_effect=ValueError("error"))

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(mock_func)

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.15)

        # Next call should transition to HALF_OPEN
        success_func = Mock(return_value="success")
        result = breaker.call(success_func)

        assert result == "success"
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_half_open_success_closes_circuit(self):
        """Test that success in HALF_OPEN closes the circuit."""
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        failing_func = Mock(side_effect=ValueError("error"))

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(failing_func)

        # Wait and try with successful function
        time.sleep(0.15)
        success_func = Mock(return_value="recovered")

        result = breaker.call(success_func)

        assert result == "recovered"
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_half_open_failure_reopens_circuit(self):
        """Test that failure in HALF_OPEN reopens the circuit."""
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        failing_func = Mock(side_effect=ValueError("error"))

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN
        initial_failure_time = breaker.last_failure_time

        # Wait and try again - should fail and reopen
        time.sleep(0.15)

        with pytest.raises(ValueError):
            breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3
        assert breaker.last_failure_time > initial_failure_time

    def test_success_resets_failure_count(self):
        """Test that success resets failure count."""
        breaker = CircuitBreaker("test", failure_threshold=3)
        mock_func = Mock()

        # Have some failures
        mock_func.side_effect = ValueError("error")
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(mock_func)

        assert breaker.failure_count == 2

        # Then have success
        mock_func.side_effect = None
        mock_func.return_value = "success"

        result = breaker.call(mock_func)

        assert result == "success"
        assert breaker.failure_count == 0
        assert breaker.state == CircuitState.CLOSED

    def test_circuit_breaker_with_different_exceptions(self):
        """Test that circuit breaker counts all exception types."""
        breaker = CircuitBreaker("test", failure_threshold=3)

        mock_func = Mock()
        exceptions = [ValueError("error1"), TypeError("error2"), RuntimeError("error3")]

        for exc in exceptions:
            mock_func.side_effect = exc
            with pytest.raises(type(exc)):
                breaker.call(mock_func)

        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3

    def test_multiple_circuits_independent(self):
        """Test that multiple circuit breakers are independent."""
        breaker_a = CircuitBreaker("service_a", failure_threshold=2)
        breaker_b = CircuitBreaker("service_b", failure_threshold=2)

        failing_func = Mock(side_effect=ValueError("error"))

        # Open circuit A
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker_a.call(failing_func)

        assert breaker_a.state == CircuitState.OPEN
        assert breaker_b.state == CircuitState.CLOSED

        # Circuit B should still work
        success_func = Mock(return_value="success")
        result = breaker_b.call(success_func)

        assert result == "success"
        assert breaker_b.state == CircuitState.CLOSED
