"""Tests for usage tracker with updated limits and error messages."""

import pytest
from unittest.mock import Mock, patch, mock_open
import json
from src.core.usage_tracker import UsageTracker, BudgetExceededError


class TestDailyLimits:
    """Test updated daily limits."""

    def test_daily_limit_is_40_calls(self):
        """Test that daily limit is set to 40 calls."""
        # Load actual config file
        tracker = UsageTracker()

        # Check config has correct limit
        assert tracker.config['daily_limits']['max_api_calls'] == 40

    def test_daily_limit_error_message(self):
        """Test that daily limit error message is clear."""
        tracker = UsageTracker()

        # Set usage to limit
        tracker.usage_data['daily_usage']['2025-10-29'] = {
            'calls': 40,
            'cost': 0.0,
            'model_calls': {}
        }

        can_call, reason = tracker.can_make_call('sonar-pro')

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

    def test_perplexity_hard_limit_enforced(self):
        """Test that Perplexity stops at $4.98."""
        tracker = UsageTracker()

        # Set Perplexity usage to limit
        tracker.usage_data['perplexity_cost'] = 4.98

        can_call, reason = tracker.can_make_call('sonar-pro')

        assert not can_call
        assert 'PERPLEXITY_LIMIT_EXCEEDED' in reason


class TestModelFallback:
    """Test model fallback when limits reached."""

    def test_fallback_to_gemini_on_perplexity_limit(self):
        """Test that system falls back to Gemini when Perplexity limit reached."""
        tracker = UsageTracker()

        # Set Perplexity usage to limit
        tracker.usage_data['perplexity_cost'] = 4.98

        # Should return gemini model
        model, provider = tracker.get_available_model('sonar-pro', 'sentiment')

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
        assert tracker.config['defaults']['strategy_model'] == 'gpt-4o-mini'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
