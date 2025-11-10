"""
Tests for SecretsManager.

Tests secure credential storage using OS keychain with .env fallback.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.core.secrets_manager import SecretsManager, get_secret, set_secret


class TestSecretsManager:
    """Tests for SecretsManager class."""

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', False)
    def test_fallback_to_env(self, monkeypatch):
        """Test fallback to environment variables when keyring unavailable."""
        monkeypatch.setenv('TEST_SECRET', 'from_env')

        manager = SecretsManager()
        value = manager.get('TEST_SECRET')

        assert value == 'from_env'
        assert not manager.using_keyring

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', False)
    def test_default_value_when_not_found(self):
        """Test default value returned when secret not found."""
        manager = SecretsManager()
        value = manager.get('NONEXISTENT_SECRET', default='default_value')

        assert value == 'default_value'

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', False)
    def test_none_when_not_found_no_default(self):
        """Test None returned when secret not found and no default."""
        manager = SecretsManager()
        value = manager.get('NONEXISTENT_SECRET')

        assert value is None

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', True)
    @patch('src.core.secrets_manager.keyring')
    def test_keyring_get_success(self, mock_keyring):
        """Test successful retrieval from keyring."""
        mock_keyring.get_password.return_value = 'from_keychain'

        manager = SecretsManager()
        value = manager.get('TEST_SECRET')

        assert value == 'from_keychain'
        mock_keyring.get_password.assert_called_once_with('trading-desk', 'TEST_SECRET')

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', True)
    @patch('src.core.secrets_manager.keyring')
    def test_keyring_set_success(self, mock_keyring):
        """Test successful storage in keyring."""
        manager = SecretsManager()
        result = manager.set('TEST_SECRET', 'new_value')

        assert result is True
        mock_keyring.set_password.assert_called_once_with(
            'trading-desk', 'TEST_SECRET', 'new_value'
        )

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', False)
    def test_set_fails_without_keyring(self):
        """Test set() fails gracefully when keyring unavailable."""
        manager = SecretsManager()
        result = manager.set('TEST_SECRET', 'value')

        assert result is False

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', True)
    @patch('src.core.secrets_manager.keyring')
    def test_keyring_delete_success(self, mock_keyring):
        """Test successful deletion from keyring."""
        manager = SecretsManager()
        result = manager.delete('TEST_SECRET')

        assert result is True
        mock_keyring.delete_password.assert_called_once_with(
            'trading-desk', 'TEST_SECRET'
        )

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', False)
    def test_delete_fails_without_keyring(self):
        """Test delete() fails gracefully when keyring unavailable."""
        manager = SecretsManager()
        result = manager.delete('TEST_SECRET')

        assert result is False

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', True)
    @patch('src.core.secrets_manager.keyring')
    def test_keyring_fallback_to_env_on_error(self, mock_keyring, monkeypatch):
        """Test fallback to env when keyring raises exception."""
        mock_keyring.get_password.side_effect = Exception("Keyring error")
        monkeypatch.setenv('TEST_SECRET', 'from_env')

        manager = SecretsManager()
        value = manager.get('TEST_SECRET')

        assert value == 'from_env'

    def test_list_keys_returns_configured_keys(self, monkeypatch):
        """Test list_keys() returns only configured keys."""
        # Set up some test secrets in environment
        monkeypatch.setenv('TRADIER_ACCESS_TOKEN', 'token123')
        monkeypatch.setenv('GOOGLE_API_KEY', 'key456')

        manager = SecretsManager()
        keys = manager.list_keys()

        assert 'TRADIER_ACCESS_TOKEN' in keys
        assert 'GOOGLE_API_KEY' in keys
        # Keys not set should not be in list
        assert all(manager.get(key) is not None for key in keys)


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', False)
    def test_get_secret_convenience(self, monkeypatch):
        """Test get_secret() convenience function."""
        monkeypatch.setenv('TEST_SECRET', 'value')

        value = get_secret('TEST_SECRET')
        assert value == 'value'

    @patch('src.core.secrets_manager._manager', None)  # Reset singleton
    @patch('src.core.secrets_manager.KEYRING_AVAILABLE', True)
    @patch('src.core.secrets_manager.keyring')
    def test_set_secret_convenience(self, mock_keyring):
        """Test set_secret() convenience function."""
        result = set_secret('TEST_SECRET', 'value')

        assert result is True
        mock_keyring.set_password.assert_called_once()
