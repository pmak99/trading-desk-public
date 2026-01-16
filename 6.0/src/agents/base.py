"""Base agent utilities for prompt building and response parsing.

Provides common functionality for worker agents:
- Prompt template loading and variable injection
- JSON response parsing and validation
- Error handling patterns
"""

import json
import re
from typing import Dict, Any, Optional, Type
from pathlib import Path
from pydantic import BaseModel, ValidationError

from ..utils.schemas import (
    TickerAnalysisResponse,
    ExplanationResponse,
    AnomalyDetectionResponse,
    HealthCheckResponse,
    SentimentFetchResponse
)


class BaseAgent:
    """
    Base class for agent utilities.

    Provides static methods for:
    - Building prompts from templates
    - Parsing and validating JSON responses
    - Error handling

    Example:
        # Build prompt
        prompt = BaseAgent.build_prompt(
            "TickerAnalysisAgent",
            ticker="NVDA",
            earnings_date="2026-02-05"
        )

        # Parse response
        result = BaseAgent.parse_response(
            raw_response,
            TickerAnalysisResponse
        )
    """

    # Map agent types to response schemas
    RESPONSE_SCHEMAS: Dict[str, Type[BaseModel]] = {
        'TickerAnalysisAgent': TickerAnalysisResponse,
        'ExplanationAgent': ExplanationResponse,
        'AnomalyDetectionAgent': AnomalyDetectionResponse,
        'HealthCheckAgent': HealthCheckResponse,
        'SentimentFetchAgent': SentimentFetchResponse
    }

    @staticmethod
    def build_prompt(
        agent_type: str,
        config_path: Optional[Path] = None,
        **kwargs
    ) -> str:
        """
        Build agent prompt from template.

        Args:
            agent_type: Type of agent (e.g., "TickerAnalysisAgent")
            config_path: Path to agents.yaml (default: auto-detect)
            **kwargs: Template variables to inject

        Returns:
            Formatted prompt string

        Example:
            prompt = BaseAgent.build_prompt(
                "TickerAnalysisAgent",
                ticker="NVDA",
                earnings_date="2026-02-05"
            )
        """
        import yaml

        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "agents.yaml"

        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        if agent_type not in config:
            raise ValueError(f"Unknown agent type: {agent_type}")

        template = config[agent_type]['prompt']

        # Replace template variables
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            if placeholder in template:
                template = template.replace(placeholder, str(value))

        return template

    @staticmethod
    def extract_json(response: str) -> str:
        """
        Extract JSON string from response.

        Handles markdown code blocks and raw JSON.

        Args:
            response: Raw response string

        Returns:
            JSON string

        Raises:
            ValueError: If no JSON found
        """
        # Try to extract JSON from markdown code blocks
        json_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        matches = re.findall(json_pattern, response, re.DOTALL)

        if matches:
            return matches[0]

        # Try to find raw JSON
        json_pattern = r'\{.*?\}'
        matches = re.findall(json_pattern, response, re.DOTALL)

        if matches:
            # Return longest match (likely the complete JSON)
            return max(matches, key=len)

        raise ValueError(f"No JSON found in response: {response[:200]}")

    @staticmethod
    def parse_response(
        response: str,
        schema: Optional[Type[BaseModel]] = None,
        agent_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parse and validate agent response.

        Args:
            response: Raw agent response string
            schema: Pydantic schema for validation (optional)
            agent_type: Agent type to auto-detect schema (optional)

        Returns:
            Parsed and validated dict

        Raises:
            ValueError: If parsing or validation fails

        Example:
            result = BaseAgent.parse_response(
                raw_response,
                TickerAnalysisResponse
            )

            # Or with auto-detection
            result = BaseAgent.parse_response(
                raw_response,
                agent_type="TickerAnalysisAgent"
            )
        """
        # Auto-detect schema from agent type
        if schema is None and agent_type is not None:
            schema = BaseAgent.RESPONSE_SCHEMAS.get(agent_type)

        # Extract JSON
        json_str = BaseAgent.extract_json(response)

        # Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

        # Validate with schema if provided
        if schema is not None:
            try:
                validated = schema(**data)
                return validated.dict()
            except ValidationError as e:
                raise ValueError(f"Validation failed: {e}") from e

        return data

    @staticmethod
    def create_error_response(
        agent_type: str,
        error_message: str,
        **extra_fields
    ) -> Dict[str, Any]:
        """
        Create standardized error response.

        Args:
            agent_type: Type of agent that failed
            error_message: Error description
            **extra_fields: Additional fields to include

        Returns:
            Error response dict

        Example:
            error = BaseAgent.create_error_response(
                "TickerAnalysisAgent",
                "API timeout",
                ticker="NVDA"
            )
        """
        response = {
            'error': error_message,
            'success': False,
            'agent_type': agent_type
        }
        response.update(extra_fields)
        return response

    @staticmethod
    def validate_required_fields(
        data: Dict[str, Any],
        required_fields: list
    ) -> None:
        """
        Validate that required fields are present.

        Args:
            data: Data dict to validate
            required_fields: List of required field names

        Raises:
            ValueError: If any required field is missing
        """
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")
