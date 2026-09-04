"""Council package — backward-compat re-exports."""

from src.domain.council.types import CouncilMember, CouncilResult
from src.domain.council.scoring import (
    WEIGHTS,
    SKEW_SCORE_MAP,
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
    normalize_analyst_score,
    calculate_historical_score,
    calculate_skew_score,
    calculate_news_score,
    score_to_direction,
    calculate_agreement,
    parse_research_response,
)
from src.domain.council.runner import run_council

__all__ = [
    "CouncilMember",
    "CouncilResult",
    "WEIGHTS",
    "SKEW_SCORE_MAP",
    "BULLISH_KEYWORDS",
    "BEARISH_KEYWORDS",
    "normalize_analyst_score",
    "calculate_historical_score",
    "calculate_skew_score",
    "calculate_news_score",
    "score_to_direction",
    "calculate_agreement",
    "parse_research_response",
    "run_council",
]
