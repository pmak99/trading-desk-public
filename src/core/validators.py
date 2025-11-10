"""
Runtime validators for type safety.

Provides validation functions to ensure data structures conform to TypedDict
definitions at runtime. Useful for validating data coming from external APIs
or user input.
"""

import logging
from typing import Any, Dict, List, Optional

from src.core.types import (
    OptionsData,
    OptionContract,
    TickerData,
    AnalysisResult,
    SentimentData,
    StrategyData
)

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when data validation fails."""
    pass


def validate_options_data(data: Dict[str, Any], strict: bool = False) -> bool:
    """
    Validate that a dictionary conforms to OptionsData structure.

    Args:
        data: Dictionary to validate
        strict: If True, require all required fields. If False, only warn.

    Returns:
        True if valid, False otherwise

    Raises:
        ValidationError: If strict=True and validation fails
    """
    required_fields = ['data_source']  # Minimal required field
    optional_fields = [
        'current_iv', 'iv_rank', 'iv_percentile', 'expected_move_pct',
        'avg_actual_move_pct', 'options_volume', 'open_interest',
        'bid_ask_spread_pct', 'iv_crush_ratio', 'expiration', 'last_updated'
    ]

    # Check for required fields
    for field in required_fields:
        if field not in data:
            msg = f"Missing required field '{field}' in OptionsData"
            if strict:
                raise ValidationError(msg)
            logger.warning(msg)
            return False

    # Validate types of present fields
    type_checks = {
        'current_iv': (int, float),
        'iv_rank': (int, float),
        'iv_percentile': (int, float),
        'expected_move_pct': (int, float),
        'avg_actual_move_pct': (int, float),
        'options_volume': int,
        'open_interest': int,
        'bid_ask_spread_pct': (int, float),
        'iv_crush_ratio': (int, float),
        'data_source': str,
        'expiration': str,
        'last_updated': str
    }

    for field, expected_type in type_checks.items():
        if field in data:
            value = data[field]
            # Skip None values for optional fields
            if value is None:
                continue
            if not isinstance(value, expected_type):
                msg = f"Field '{field}' has wrong type: expected {expected_type}, got {type(value)}"
                if strict:
                    raise ValidationError(msg)
                logger.warning(msg)
                return False

    return True


def validate_ticker_data(data: Dict[str, Any], strict: bool = False) -> bool:
    """
    Validate that a dictionary conforms to TickerData structure.

    Args:
        data: Dictionary to validate
        strict: If True, require all required fields. If False, only warn.

    Returns:
        True if valid, False otherwise

    Raises:
        ValidationError: If strict=True and validation fails
    """
    required_fields = ['ticker', 'price']
    optional_fields = [
        'market_cap', 'volume', 'options_data', 'iv', 'score',
        'sector', 'industry', 'earnings_date', 'atm_call', 'atm_put'
    ]

    # Check for required fields
    for field in required_fields:
        if field not in data:
            msg = f"Missing required field '{field}' in TickerData"
            if strict:
                raise ValidationError(msg)
            logger.warning(msg)
            return False

    # Validate types of present fields
    type_checks = {
        'ticker': str,
        'price': (int, float),
        'market_cap': (int, float),
        'volume': int,
        'iv': (int, float),
        'score': (int, float),
        'sector': str,
        'industry': str,
        'earnings_date': str
    }

    for field, expected_type in type_checks.items():
        if field in data:
            value = data[field]
            # Skip None values for optional fields
            if value is None:
                continue
            if not isinstance(value, expected_type):
                msg = f"Field '{field}' has wrong type: expected {expected_type}, got {type(value)}"
                if strict:
                    raise ValidationError(msg)
                logger.warning(msg)
                return False

    # Validate nested OptionsData if present
    if 'options_data' in data and data['options_data']:
        if not validate_options_data(data['options_data'], strict=False):
            if strict:
                raise ValidationError("Invalid OptionsData in TickerData")
            return False

    return True


def validate_analysis_result(data: Dict[str, Any], strict: bool = False) -> bool:
    """
    Validate that a dictionary conforms to AnalysisResult structure.

    Args:
        data: Dictionary to validate
        strict: If True, require all required fields. If False, only warn.

    Returns:
        True if valid, False otherwise

    Raises:
        ValidationError: If strict=True and validation fails
    """
    required_fields = ['ticker', 'earnings_date', 'price', 'score']

    # Check for required fields
    for field in required_fields:
        if field not in data:
            msg = f"Missing required field '{field}' in AnalysisResult"
            if strict:
                raise ValidationError(msg)
            logger.warning(msg)
            return False

    # Validate types of present fields
    type_checks = {
        'ticker': str,
        'earnings_date': str,
        'price': (int, float),
        'score': (int, float),
        'analyzed_at': str,
        'analysis_version': str
    }

    for field, expected_type in type_checks.items():
        if field in data:
            value = data[field]
            # Skip None values for optional fields
            if value is None:
                continue
            if not isinstance(value, expected_type):
                msg = f"Field '{field}' has wrong type: expected {expected_type}, got {type(value)}"
                if strict:
                    raise ValidationError(msg)
                logger.warning(msg)
                return False

    # Validate nested structures
    if 'options_data' in data and data['options_data']:
        if not validate_options_data(data['options_data'], strict=False):
            if strict:
                raise ValidationError("Invalid OptionsData in AnalysisResult")
            return False

    if 'sentiment' in data and data['sentiment']:
        if not isinstance(data['sentiment'], dict):
            msg = "Field 'sentiment' must be a dict"
            if strict:
                raise ValidationError(msg)
            logger.warning(msg)
            return False

    if 'strategies' in data and data['strategies']:
        if not isinstance(data['strategies'], list):
            msg = "Field 'strategies' must be a list"
            if strict:
                raise ValidationError(msg)
            logger.warning(msg)
            return False

    return True


def sanitize_options_data(data: Dict[str, Any]) -> OptionsData:
    """
    Sanitize and normalize options data dictionary.

    Ensures numeric fields are properly typed and adds default values
    for missing optional fields.

    Args:
        data: Raw options data dictionary

    Returns:
        Sanitized OptionsData dictionary
    """
    sanitized: OptionsData = {
        'data_source': str(data.get('data_source', 'unknown'))
    }

    # Numeric fields with defaults
    numeric_fields = {
        'current_iv': 0.0,
        'iv_rank': 0.0,
        'iv_percentile': 0.0,
        'expected_move_pct': 0.0,
        'options_volume': 0,
        'open_interest': 0
    }

    for field, default in numeric_fields.items():
        value = data.get(field)
        if value is not None:
            try:
                if field in ['options_volume', 'open_interest']:
                    sanitized[field] = int(value)  # type: ignore
                else:
                    sanitized[field] = float(value)  # type: ignore
            except (ValueError, TypeError):
                logger.warning(f"Could not convert {field}={value} to number, using default {default}")
                sanitized[field] = default  # type: ignore

    # Optional numeric fields
    optional_numeric = ['avg_actual_move_pct', 'bid_ask_spread_pct', 'iv_crush_ratio']
    for field in optional_numeric:
        value = data.get(field)
        if value is not None:
            try:
                sanitized[field] = float(value)  # type: ignore
            except (ValueError, TypeError):
                logger.warning(f"Could not convert {field}={value} to float")

    # String fields
    string_fields = ['expiration', 'last_updated']
    for field in string_fields:
        value = data.get(field)
        if value is not None:
            sanitized[field] = str(value)  # type: ignore

    return sanitized


def sanitize_ticker_data(data: Dict[str, Any]) -> TickerData:
    """
    Sanitize and normalize ticker data dictionary.

    Ensures numeric fields are properly typed and adds default values
    for missing optional fields.

    Args:
        data: Raw ticker data dictionary

    Returns:
        Sanitized TickerData dictionary
    """
    sanitized: TickerData = {
        'ticker': str(data.get('ticker', '')),
        'price': float(data.get('price', 0.0))
    }

    # Numeric fields with defaults
    if 'market_cap' in data:
        try:
            sanitized['market_cap'] = float(data['market_cap'])
        except (ValueError, TypeError):
            sanitized['market_cap'] = 0.0

    if 'volume' in data:
        try:
            sanitized['volume'] = int(data['volume'])
        except (ValueError, TypeError):
            sanitized['volume'] = 0

    if 'score' in data:
        try:
            sanitized['score'] = float(data['score'])
        except (ValueError, TypeError):
            sanitized['score'] = 0.0

    # Optional fields
    if 'iv' in data and data['iv'] is not None:
        try:
            sanitized['iv'] = float(data['iv'])
        except (ValueError, TypeError):
            pass

    # String fields
    for field in ['sector', 'industry', 'earnings_date']:
        if field in data and data[field] is not None:
            sanitized[field] = str(data[field])  # type: ignore

    # Nested options data
    if 'options_data' in data and data['options_data']:
        try:
            sanitized['options_data'] = sanitize_options_data(data['options_data'])
        except Exception as e:
            logger.warning(f"Could not sanitize options_data: {e}")

    return sanitized
