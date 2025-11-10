"""
JSON parsing utilities for AI responses.

Provides common functionality for parsing AI-generated JSON responses
that may be wrapped in markdown code blocks or have other formatting issues.
"""

# Standard library imports
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def extract_json_from_markdown(text: str) -> str:
    """
    Extract JSON content from markdown code blocks.

    Handles cases where AI responses wrap JSON in ```json or ``` code blocks.

    Args:
        text: Raw text that may contain markdown-wrapped JSON

    Returns:
        Cleaned JSON string

    Example:
        >>> text = '```json\\n{"key": "value"}\\n```'
        >>> extract_json_from_markdown(text)
        '{"key": "value"}'
    """
    if not text.startswith('```'):
        return text

    lines = text.split('\n')
    json_lines = []
    in_json = False

    for line in lines:
        if line.startswith('```'):
            in_json = not in_json
            continue
        if in_json:
            json_lines.append(line)

    return '\n'.join(json_lines)


def parse_json_safely(text: str, context: str = "") -> Dict[str, Any]:
    """
    Parse JSON with automatic markdown extraction.

    Args:
        text: Raw text containing JSON (may be markdown-wrapped)
        context: Context string for error messages (e.g., "sentiment analysis")

    Returns:
        Parsed JSON as dict

    Raises:
        json.JSONDecodeError: If JSON parsing fails after cleanup

    Example:
        >>> text = '```json\\n{"sentiment": "bullish"}\\n```'
        >>> parse_json_safely(text, "sentiment")
        {'sentiment': 'bullish'}
    """
    # Clean up markdown formatting
    cleaned_text = extract_json_from_markdown(text.strip())

    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        error_msg = f"JSON parsing failed"
        if context:
            error_msg += f" for {context}"
        error_msg += f": {e}"
        logger.error(error_msg)
        raise


def safe_json_loads(text: str, default: Any = None) -> Any:
    """
    Parse JSON with fallback to default value.

    Args:
        text: Raw text containing JSON
        default: Default value if parsing fails (default: None)

    Returns:
        Parsed JSON or default value

    Example:
        >>> safe_json_loads('{"key": "value"}')
        {'key': 'value'}
        >>> safe_json_loads('invalid json', default={})
        {}
    """
    try:
        cleaned_text = extract_json_from_markdown(text.strip())
        return json.loads(cleaned_text)
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        logger.debug(f"JSON parsing failed, returning default: {e}")
        return default
