"""Pure scoring functions and constants for the council consensus system."""

import re
from typing import Dict, Any, List

from src.domain.council.types import CouncilMember


# Council member weights (renormalized from 85% to 100% — WebSearch dropped)
WEIGHTS = {
    "perplexity_research": 0.293,
    "finnhub_analysts": 0.235,
    "perplexity_quick": 0.118,
    "finnhub_news": 0.118,
    "options_skew": 0.118,
    "historical_pattern": 0.118,
}

# Skew bias to score mapping
SKEW_SCORE_MAP = {
    "STRONG_BULLISH": 0.7,
    "BULLISH": 0.5,
    "WEAK_BULLISH": 0.3,
    "NEUTRAL": 0.0,
    "WEAK_BEARISH": -0.3,
    "BEARISH": -0.5,
    "STRONG_BEARISH": -0.7,
}

# News sentiment keywords
BULLISH_KEYWORDS = {"upgrade", "beat", "strong", "growth", "record", "raise", "positive", "surpass", "exceed"}
BEARISH_KEYWORDS = {"downgrade", "miss", "weak", "decline", "cut", "lower", "negative", "warning", "disappoint"}


def normalize_analyst_score(rec: Dict[str, Any]) -> float:
    """Convert analyst recommendation counts to -1..+1 score."""
    sb = rec.get("strongBuy", 0)
    b = rec.get("buy", 0)
    h = rec.get("hold", 0)
    s = rec.get("sell", 0)
    ss = rec.get("strongSell", 0)
    total = sb + b + h + s + ss
    if total == 0:
        return 0.0
    raw = (sb * 2 + b - s - ss * 2) / total
    return max(-1.0, min(1.0, raw / 2.0))


def calculate_historical_score(moves: List[Dict[str, Any]]) -> float:
    """Score based on historical up/down pattern."""
    if not moves:
        return 0.0

    up_count = sum(1 for m in moves if m.get("intraday_move_pct", 0) > 0)
    total = len(moves)
    if total == 0:
        return 0.0

    up_ratio = up_count / total
    overall = (up_ratio - 0.5) * 2.0

    # Recent 4 quarters weighted more heavily
    recent = moves[:4]
    recent_up = sum(1 for m in recent if m.get("intraday_move_pct", 0) > 0)
    recent_total = len(recent)
    recent_score = (recent_up / recent_total - 0.5) * 2.0 if recent_total > 0 else 0.0

    return max(-1.0, min(1.0, 0.6 * overall + 0.4 * recent_score))


def calculate_skew_score(bias_value: str) -> float:
    """Convert DirectionalBias value to -1..+1 score."""
    return SKEW_SCORE_MAP.get(bias_value, 0.0)


def calculate_news_score(articles: List[Dict[str, Any]]) -> float:
    """Keyword sentiment scoring on news headlines."""
    if not articles:
        return 0.0

    bull = 0
    bear = 0
    for article in articles:
        headline = article.get("headline", "").lower()
        summary = article.get("summary", "").lower()
        text = headline + " " + summary
        # Classify each article as bull, bear, or neutral (not both)
        is_bull = any(kw in text for kw in BULLISH_KEYWORDS)
        is_bear = any(kw in text for kw in BEARISH_KEYWORDS)
        if is_bull and not is_bear:
            bull += 1
        elif is_bear and not is_bull:
            bear += 1
        # Mixed signals (both bull and bear keywords) → neutral, skip

    total = bull + bear
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (bull - bear) / total * 0.7))


def score_to_direction(score: float) -> str:
    """Map numeric score to direction string."""
    if score >= 0.3:
        return "bullish"
    elif score <= -0.3:
        return "bearish"
    return "neutral"


def calculate_agreement(members: List[CouncilMember]) -> tuple:
    """Calculate agreement level among active members."""
    active = [m for m in members if not m.failed]
    if not active:
        return "LOW", 0, 0

    # Determine majority direction
    directions = [m.direction for m in active]
    majority = max(set(directions), key=directions.count)
    agreeing = directions.count(majority)
    total = len(active)
    ratio = agreeing / total

    if ratio >= 0.71:
        level = "HIGH"
    elif ratio >= 0.57:
        level = "MEDIUM"
    else:
        level = "LOW"

    return level, agreeing, total


def parse_research_response(text: str) -> Dict[str, Any]:
    """Parse deep Perplexity research response."""
    result = {
        "direction": "neutral",
        "score": 0.0,
        "bull_case": "",
        "bear_case": "",
        "key_risk": "",
        "analyst_trend": "",
        "raw": text,
    }

    # Direction
    dir_match = re.search(r'Direction:\s*(bullish|bearish|neutral)', text, re.I)
    if dir_match:
        result["direction"] = dir_match.group(1).lower()

    # Score
    score_match = re.search(r'Score:\s*([+-]?\d*\.?\d+)', text)
    if score_match:
        result["score"] = max(-1.0, min(1.0, float(score_match.group(1))))

    # Bull Case
    bull_match = re.search(r'Bull Case:\s*(.+?)(?=Bear Case:|Key Risk:|$)', text, re.I | re.S)
    if bull_match:
        result["bull_case"] = bull_match.group(1).strip()

    # Bear Case
    bear_match = re.search(r'Bear Case:\s*(.+?)(?=Key Risk:|Analyst Trend:|$)', text, re.I | re.S)
    if bear_match:
        result["bear_case"] = bear_match.group(1).strip()

    # Key Risk
    risk_match = re.search(r'Key Risk:\s*(.+?)(?=Analyst Trend:|$)', text, re.I | re.S)
    if risk_match:
        result["key_risk"] = risk_match.group(1).strip()

    # Analyst Trend
    trend_match = re.search(r'Analyst Trend:\s*(.+?)(?=\n|$)', text, re.I)
    if trend_match:
        result["analyst_trend"] = trend_match.group(1).strip()

    return result
