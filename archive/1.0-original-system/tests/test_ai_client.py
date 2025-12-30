"""Tests for AI client with retry logic and model selection."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests
from src.ai.client import AIClient
from src.core.usage_tracker import UsageTracker, BudgetExceededError


class TestAIClientRetryLogic:
    """Test retry logic with exponential backoff."""

    @patch('src.ai.client.requests.post')
    @patch('src.ai.client.time.sleep')
    def test_retry_on_timeout(self, mock_sleep, mock_post):
        """Test that client retries on timeout errors."""
        # Setup: first 2 calls timeout, 3rd succeeds
        mock_post.side_effect = [
            requests.exceptions.Timeout("Timeout 1"),
            requests.exceptions.Timeout("Timeout 2"),
            Mock(status_code=200, json=lambda: {
                'choices': [{'message': {'content': 'Success'}}],
                'usage': {'total_tokens': 100}
            })
        ]

        client = AIClient()
        # Mock API keys to prevent environment check
        client.perplexity_key = 'fake_key'

        # Mock usage tracker to allow call
        with patch.object(client.usage_tracker, 'get_available_model', return_value=('sonar-pro', 'perplexity')):
            with patch.object(client.usage_tracker, 'log_api_call'):
                result = client.chat_completion("test prompt", max_retries=3)

        # Verify retries happened
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2  # 2 retries
        assert result['content'] == 'Success'

    @patch('src.ai.client.requests.post')
    @patch('src.ai.client.time.sleep')
    def test_exponential_backoff(self, mock_sleep, mock_post):
        """Test exponential backoff timing."""
        # Setup: timeout on all calls
        mock_post.side_effect = requests.exceptions.Timeout("Timeout")

        client = AIClient()
        # Mock API keys to prevent environment check
        client.perplexity_key = 'fake_key'

        with patch.object(client.usage_tracker, 'get_available_model', return_value=('sonar-pro', 'perplexity')):
            with pytest.raises(requests.exceptions.Timeout):
                client.chat_completion("test prompt", max_retries=3)

        # Verify exponential backoff: 1s, 2s
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert sleep_calls == [1, 2]  # 2^0, 2^1

    @patch('src.ai.client.requests.post')
    def test_no_retry_on_budget_error(self, mock_post):
        """Test that budget errors are not retried."""
        client = AIClient()

        with patch.object(client.usage_tracker, 'get_available_model', side_effect=BudgetExceededError("Budget exceeded")):
            with pytest.raises(BudgetExceededError):
                client.chat_completion("test prompt")

        # Should not retry budget errors
        assert mock_post.call_count == 0


class TestAIClientModelSelection:
    """Test model selection from config."""

    def test_sentiment_uses_sonar_pro(self):
        """Test that sentiment analysis uses sonar-pro by default."""
        client = AIClient()

        with patch.object(client.usage_tracker, 'get_available_model') as mock_get_model:
            mock_get_model.return_value = ('sonar-pro', 'perplexity')

            with patch.object(client, '_call_perplexity', return_value={'content': 'test', 'model': 'sonar-pro'}):
                client.chat_completion("test", preferred_model="sonar-pro", use_case="sentiment")

            mock_get_model.assert_called_with("sonar-pro", "sentiment", False)

    def test_strategy_uses_gpt4o_mini(self):
        """Test that strategy generation can use gpt-4o-mini."""
        client = AIClient()

        with patch.object(client.usage_tracker, 'get_available_model') as mock_get_model:
            mock_get_model.return_value = ('gpt-4o-mini', 'perplexity')

            with patch.object(client, '_call_perplexity', return_value={'content': 'test', 'model': 'gpt-4o-mini'}):
                client.chat_completion("test", preferred_model="gpt-4o-mini", use_case="strategy")

            mock_get_model.assert_called_with("gpt-4o-mini", "strategy", False)


class TestAIClientFallback:
    """Test automatic fallback to Gemini."""

    @patch('src.ai.client.requests.post')
    def test_fallback_to_gemini_on_budget_limit(self, mock_post):
        """Test fallback to Gemini when Perplexity limit reached."""
        client = AIClient()
        # Mock API keys to prevent environment check
        client.google_key = 'fake_key'

        # Setup mock to trigger fallback
        with patch.object(client.usage_tracker, 'get_available_model') as mock_get_model:
            mock_get_model.return_value = ('gemini-2.0-flash', 'google')

            mock_post.return_value = Mock(status_code=200, json=lambda: {
                'candidates': [{'content': {'parts': [{'text': 'Gemini response'}]}}]
            })

            with patch.object(client.usage_tracker, 'log_api_call'):
                result = client.chat_completion("test prompt", preferred_model="sonar-pro")

            assert result['provider'] == 'google'
            assert result['model'] == 'gemini-2.0-flash'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
