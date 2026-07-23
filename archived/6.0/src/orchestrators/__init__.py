"""6.0 Orchestrator implementations.

Orchestrators coordinate multiple agents to accomplish complex workflows
like /whisper, /analyze, /prime, and /maintenance.
"""

from .base import BaseOrchestrator
from .whisper import WhisperOrchestrator
from .analyze import AnalyzeOrchestrator
from .prime import PrimeOrchestrator

__all__ = [
    'BaseOrchestrator',
    'WhisperOrchestrator',
    'AnalyzeOrchestrator',
    'PrimeOrchestrator',
]
