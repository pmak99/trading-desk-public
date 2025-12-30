"""
Shared configuration loader for trading criteria.

Provides centralized, cached access to configuration files to avoid
duplicate loading and parsing.
"""

import os
import yaml
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ConfigLoader:
    """
    Singleton configuration loader with caching.

    Usage:
        criteria = ConfigLoader.load_trading_criteria()
        budget = ConfigLoader.load_budget_config()
    """

    _cache: Dict[str, Dict] = {}

    @classmethod
    def load_trading_criteria(cls) -> Optional[Dict]:
        """
        Load trading criteria configuration.

        Returns:
            Dict with trading criteria or None if file not found
        """
        if 'trading_criteria' in cls._cache:
            return cls._cache['trading_criteria']

        config_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            '..',
            'config',
            'trading_criteria.yaml'
        )

        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                cls._cache['trading_criteria'] = config
                logger.debug(f"Loaded trading criteria from {config_path}")
                return config
        except FileNotFoundError:
            logger.warning(f"Trading criteria config not found at {config_path}")
            return None
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse trading criteria YAML: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error loading trading criteria: {e}")
            return None

    @classmethod
    def load_budget_config(cls) -> Optional[Dict]:
        """
        Load budget configuration.

        Returns:
            Dict with budget config or None if file not found
        """
        if 'budget' in cls._cache:
            return cls._cache['budget']

        config_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            '..',
            'config',
            'budget.yaml'
        )

        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                cls._cache['budget'] = config
                logger.debug(f"Loaded budget config from {config_path}")
                return config
        except FileNotFoundError:
            logger.warning(f"Budget config not found at {config_path}")
            return None
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse budget YAML: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error loading budget config: {e}")
            return None

    @classmethod
    def clear_cache(cls):
        """Clear all cached configurations (useful for testing)."""
        cls._cache.clear()
        logger.debug("Cleared configuration cache")

    @classmethod
    def reload_config(cls, config_name: str) -> Optional[Dict]:
        """
        Force reload a specific configuration.

        Args:
            config_name: Name of config ('trading_criteria' or 'budget')

        Returns:
            Reloaded config dict or None
        """
        if config_name in cls._cache:
            del cls._cache[config_name]

        if config_name == 'trading_criteria':
            return cls.load_trading_criteria()
        elif config_name == 'budget':
            return cls.load_budget_config()
        else:
            logger.warning(f"Unknown config name: {config_name}")
            return None
