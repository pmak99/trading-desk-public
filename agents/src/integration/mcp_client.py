"""MCP Task Client for agent spawning.

Wrapper around Claude's Task tool for spawning worker agents.
Handles prompt building, JSON parsing, and error recovery.
"""

import json
import re
from typing import Dict, Any, Optional
from pathlib import Path


class MCPTaskClient:
    """
    Wrapper around Claude's Task tool for agent spawning.

    Agents are spawned via Claude Desktop's MCP Task tool, which creates
    isolated Claude instances that execute agent prompts and return JSON.

    Example:
        client = MCPTaskClient()
        response = await client.spawn_agent(
            agent_type="TickerAnalysisAgent",
            prompt="Analyze NVDA for earnings on 2026-02-05",
            timeout=30,
            model="haiku"
        )
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize MCP client.

        Args:
            config_path: Path to agents.yaml config (default: auto-detect)
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "agents.yaml"

        self.config_path = config_path
        self._config = None

    @property
    def config(self) -> Dict[str, Any]:
        """Lazy-load agent configuration."""
        if self._config is None:
            import yaml
            with open(self.config_path, 'r') as f:
                self._config = yaml.safe_load(f)
        return self._config

    def build_prompt(
        self,
        agent_type: str,
        **kwargs
    ) -> str:
        """
        Build agent prompt from template.

        Args:
            agent_type: Type of agent (e.g., "TickerAnalysisAgent")
            **kwargs: Template variables to inject

        Returns:
            Formatted prompt string

        Example:
            prompt = client.build_prompt(
                "TickerAnalysisAgent",
                ticker="NVDA",
                earnings_date="2026-02-05"
            )
        """
        if agent_type not in self.config:
            raise ValueError(f"Unknown agent type: {agent_type}")

        template = self.config[agent_type]['prompt']

        # Replace template variables
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            if placeholder in template:
                template = template.replace(placeholder, str(value))

        return template

    def parse_json_response(self, response: str) -> Dict[str, Any]:
        """
        Parse JSON from agent response.

        Handles markdown code blocks and extracts JSON content.

        Args:
            response: Raw agent response string

        Returns:
            Parsed JSON dict

        Raises:
            ValueError: If no valid JSON found
        """
        # Try to extract JSON from markdown code blocks
        json_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        matches = re.findall(json_pattern, response, re.DOTALL)

        if matches:
            # Use first JSON block found
            json_str = matches[0]
        else:
            # Try to find raw JSON
            json_pattern = r'\{.*?\}'
            matches = re.findall(json_pattern, response, re.DOTALL)
            if matches:
                json_str = matches[0]
            else:
                raise ValueError(f"No JSON found in response: {response[:200]}")

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

    async def spawn_agent(
        self,
        agent_type: str,
        prompt: str,
        timeout: int = 30,
        model: str = "haiku"
    ) -> str:
        """
        Spawn agent using Claude's Task tool.

        NOTE: This is a Phase 2 feature. Current orchestrators use direct agent
        instances (e.g., TickerAnalysisAgent().analyze()) instead of this method.

        In Phase 2, this will call Claude Desktop's Task tool via MCP protocol
        to spawn isolated Claude instances for parallel agent execution.

        Args:
            agent_type: Type of agent (e.g., "TickerAnalysisAgent")
            prompt: Full prompt for the agent
            timeout: Max execution time in seconds
            model: Claude model to use ("haiku" for speed, "sonnet" for complexity)

        Returns:
            Raw agent response (JSON string)

        Example:
            response = await client.spawn_agent(
                agent_type="TickerAnalysisAgent",
                prompt=prompt,
                timeout=30,
                model="haiku"
            )
            result = client.parse_json_response(response)
        """
        # Phase 2: MCP Task tool integration
        # Current implementation returns an error response for visibility
        # Orchestrators should use direct agent calls until Phase 2
        error_response = json.dumps({
            "error": f"MCP Task tool not yet implemented (Phase 2). "
                     f"Use direct agent calls instead: {agent_type}().method()",
            "success": False,
            "agent_type": agent_type
        })
        return error_response

    def get_agent_config(self, agent_type: str) -> Dict[str, Any]:
        """
        Get configuration for specific agent type.

        Args:
            agent_type: Type of agent

        Returns:
            Agent config dict with model, timeout, prompt
        """
        if agent_type not in self.config:
            raise ValueError(f"Unknown agent type: {agent_type}")

        return self.config[agent_type]

    def get_timeout(self, agent_type: str) -> int:
        """
        Get default timeout for agent type.

        Args:
            agent_type: Type of agent

        Returns:
            Timeout in seconds
        """
        config = self.get_agent_config(agent_type)
        return config.get('timeout', 30)

    def get_model(self, agent_type: str) -> str:
        """
        Get default model for agent type.

        Args:
            agent_type: Type of agent

        Returns:
            Model name ("haiku" or "sonnet")
        """
        config = self.get_agent_config(agent_type)
        return config.get('model', 'haiku')
