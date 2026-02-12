"""6.0 Integration layer.

Wrappers for external systems:
- Perplexity5_0: Access to 5.0's Perplexity API client
- MCPTaskClient: MCP protocol for agent spawning
"""

from .perplexity_5_0 import Perplexity5_0
from .mcp_client import MCPTaskClient

__all__ = [
    'Perplexity5_0',
    'MCPTaskClient',
]
