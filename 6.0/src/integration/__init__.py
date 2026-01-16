"""6.0 Integration layer.

Wrappers for external systems:
- Container2_0: Access to 2.0's core math engine
- Cache4_0: Access to 4.0's sentiment caching
- Perplexity5_0: Access to 5.0's Perplexity API client
- MCPTaskClient: MCP protocol for agent spawning (Phase 2)
"""

from .container_2_0 import Container2_0
from .cache_4_0 import Cache4_0
from .perplexity_5_0 import Perplexity5_0
from .mcp_client import MCPTaskClient

__all__ = [
    'Container2_0',
    'Cache4_0',
    'Perplexity5_0',
    'MCPTaskClient',
]
