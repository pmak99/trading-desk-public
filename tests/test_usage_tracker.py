"""Tests for usage tracker with updated limits and error messages."""

import pytest
from unittest.mock import Mock, patch, mock_open
import json
import tempfile
import os
from src.core.usage_tracker import UsageTracker, BudgetExceededError


@pytest.fixture
def temp_tracker():
    """Create a tracker with temporary database for isolated testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    tracker = UsageTracker(db_path=db_path)

    yield tracker

    # Cleanup
    try:
        os.unlink(db_path)
    except:
        pass


class TestDailyLimits:
    """Test updated daily limits."""

    def test_daily_limit_is_40_calls(self):
        """Test that daily limit is set to 40 calls."""
        # Load actual config file
        tracker = UsageTracker()

        # Check config has correct limit
        assert tracker.config['daily_limits']['max_api_calls'] == 40

    def test_daily_limit_error_message(self, temp_tracker):
        """Test that daily limit error message is clear."""
        # Log 40 calls to hit daily limit
        for _ in range(40):
            temp_tracker.log_api_call(model='sonar-pro', tokens_used=100, cost=0.0005)

        can_call, reason = temp_tracker.can_make_call('sonar-pro')

        assert not can_call
        assert 'DAILY_LIMIT' in reason
        assert 'Resets at midnight ET' in reason


class TestPerModelLimits:
    """Test per-model daily limits."""

    def test_gpt4o_mini_limit_is_40(self):
        """Test that gpt-4o-mini has 40 calls/day limit."""
        tracker = UsageTracker()

        assert tracker.config['daily_limits']['gpt-4o-mini_calls'] == 40

    def test_sonar_pro_limit_is_40(self):
        """Test that sonar-pro has 40 calls/day limit."""
        tracker = UsageTracker()

        assert tracker.config['daily_limits']['sonar-pro_calls'] == 40


class TestPerplexityHardLimit:
    """Test Perplexity $4.98 hard limit."""

    def test_perplexity_hard_limit_enforced(self, temp_tracker):
        """Test that Perplexity stops at $4.98."""
        # Log calls to reach Perplexity limit ($4.98)
        # sonar-pro costs $0.005 per 1k tokens
        temp_tracker.log_api_call(model='sonar-pro', tokens_used=996_000, cost=4.98)

        can_call, reason = temp_tracker.can_make_call('sonar-pro')

        assert not can_call
        assert 'PERPLEXITY_LIMIT_EXCEEDED' in reason


class TestModelFallback:
    """Test model fallback when limits reached."""

    def test_fallback_to_gemini_on_perplexity_limit(self, temp_tracker):
        """Test that system falls back to Gemini when Perplexity limit reached."""
        # Log calls to reach Perplexity limit ($4.98)
        temp_tracker.log_api_call(model='sonar-pro', tokens_used=996_000, cost=4.98)

        # Should return gemini model
        model, provider = temp_tracker.get_available_model('sonar-pro', 'sentiment')

        assert provider == 'google'
        assert model == 'gemini-2.0-flash'


class TestBudgetConfiguration:
    """Test budget configuration values."""

    def test_config_has_correct_model_costs(self):
        """Test that model costs are configured correctly."""
        tracker = UsageTracker()

        # Verify sonar-pro cost
        assert tracker.config['models']['sonar-pro']['cost_per_1k_tokens'] == 0.005

        # Verify gpt-4o-mini cost (60% cheaper)
        assert tracker.config['models']['gpt-4o-mini']['cost_per_1k_tokens'] == 0.0002

        # Verify gemini is free
        assert tracker.config['models']['gemini-2.0-flash']['cost_per_1k_tokens'] == 0.0

    def test_default_models_are_configured(self):
        """Test that default models are set correctly."""
        tracker = UsageTracker()

        assert tracker.config['defaults']['sentiment_model'] == 'sonar-pro'
        assert tracker.config['defaults']['strategy_model'] == 'sonar-pro'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
