"""
Secrets Manager - Secure credential storage using OS keychain.

Uses macOS Keychain (via python-keyring) for secure secret storage,
with automatic fallback to .env file for backward compatibility.

Security Benefits:
- Secrets stored in encrypted OS keychain (not plaintext)
- Automatic credential rotation support
- Better audit trail (OS manages access)
- Works with macOS Keychain Access app

Usage:
    from src.core.secrets_manager import get_secret

    api_key = get_secret('TRADIER_ACCESS_TOKEN')
"""

# Standard library imports
import logging
import os
from typing import Optional

# Third-party imports
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    keyring = None

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Service name for keyring (appears in Keychain Access)
SERVICE_NAME = "trading-desk"

# Load .env as fallback
load_dotenv()


class SecretsManager:
    """
    Manages API keys and secrets using OS keychain with .env fallback.

    Priority:
    1. OS Keychain (via python-keyring)
    2. Environment variables (from .env file)
    3. None (with warning)

    Example:
        manager = SecretsManager()
        api_key = manager.get('TRADIER_ACCESS_TOKEN')
    """

    def __init__(self):
        """Initialize secrets manager."""
        self.using_keyring = KEYRING_AVAILABLE

        if not KEYRING_AVAILABLE:
            logger.debug("python-keyring not available, using .env fallback")

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get secret value with keyring → env → default fallback.

        Args:
            key: Secret key name (e.g., 'TRADIER_ACCESS_TOKEN')
            default: Default value if not found

        Returns:
            Secret value or default

        Example:
            >>> manager = SecretsManager()
            >>> token = manager.get('TRADIER_ACCESS_TOKEN')
        """
        # Try keyring first (most secure)
        if self.using_keyring:
            try:
                value = keyring.get_password(SERVICE_NAME, key)
                if value:
                    logger.debug(f"Retrieved '{key}' from OS keychain")
                    return value
            except Exception as e:
                logger.debug(f"Keyring error for '{key}': {e}")

        # Fallback to environment variable (from .env)
        value = os.getenv(key)
        if value:
            logger.debug(f"Retrieved '{key}' from environment")
            return value

        # Return default or None
        if default is not None:
            logger.debug(f"Using default value for '{key}'")
            return default

        logger.warning(f"Secret '{key}' not found in keychain or environment")
        return None

    def set(self, key: str, value: str) -> bool:
        """
        Store secret in OS keychain.

        Args:
            key: Secret key name
            value: Secret value

        Returns:
            True if successful, False otherwise

        Example:
            >>> manager = SecretsManager()
            >>> manager.set('TRADIER_ACCESS_TOKEN', 'abc123')
        """
        if not self.using_keyring:
            logger.error("Cannot set secret: keyring not available")
            logger.info("Install keyring: pip install keyring")
            return False

        try:
            keyring.set_password(SERVICE_NAME, key, value)
            logger.info(f"Stored '{key}' in OS keychain")
            return True
        except Exception as e:
            logger.error(f"Failed to store '{key}' in keychain: {e}")
            return False

    def delete(self, key: str) -> bool:
        """
        Delete secret from OS keychain.

        Args:
            key: Secret key name

        Returns:
            True if successful, False otherwise
        """
        if not self.using_keyring:
            logger.error("Cannot delete secret: keyring not available")
            return False

        try:
            keyring.delete_password(SERVICE_NAME, key)
            logger.info(f"Deleted '{key}' from OS keychain")
            return True
        except keyring.errors.PasswordDeleteError:
            logger.warning(f"Secret '{key}' not found in keychain")
            return False
        except Exception as e:
            logger.error(f"Failed to delete '{key}' from keychain: {e}")
            return False

    def list_keys(self) -> list[str]:
        """
        List all known secret keys (from environment and expected keys).

        Note: Keyring doesn't support listing all keys, so we return
        known keys that might be configured.

        Returns:
            List of secret key names
        """
        known_keys = [
            'PERPLEXITY_API_KEY',
            'GOOGLE_API_KEY',
            'ALPHA_VANTAGE_API_KEY',
            'TRADIER_ACCESS_TOKEN',
            'TRADIER_ENDPOINT',
            'REDDIT_CLIENT_ID',
            'REDDIT_CLIENT_SECRET',
        ]

        # Check which keys are actually set
        available_keys = []
        for key in known_keys:
            if self.get(key) is not None:
                available_keys.append(key)

        return available_keys


# Singleton instance for convenience
_manager = None


def get_manager() -> SecretsManager:
    """Get singleton SecretsManager instance."""
    global _manager
    if _manager is None:
        _manager = SecretsManager()
    return _manager


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Convenience function to get secret.

    Args:
        key: Secret key name
        default: Default value if not found

    Returns:
        Secret value or default

    Example:
        >>> from src.core.secrets_manager import get_secret
        >>> api_key = get_secret('TRADIER_ACCESS_TOKEN')
    """
    return get_manager().get(key, default)


def set_secret(key: str, value: str) -> bool:
    """
    Convenience function to set secret in keychain.

    Args:
        key: Secret key name
        value: Secret value

    Returns:
        True if successful
    """
    return get_manager().set(key, value)
