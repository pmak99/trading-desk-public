"""
Council: 6-source AI sentiment consensus for pre-earnings analysis.

Aggregates signals from Finnhub analysts, Finnhub news, Perplexity (quick + deep),
options skew, and historical patterns into a weighted consensus.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from src.core.logging import log
from src.core import metrics


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


@dataclass
class CouncilMember:
    """Single council member result."""
    name: str
    weight: float
    score: float = 0.0       # -1.0 to +1.0
    direction: str = "neutral"
    status: str = ""          # "fresh", "cached", "33 analysts", etc.
    failed: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CouncilResult:
    """Full council consensus result."""
    ticker: str
    earnings_date: str
    timing: str
    price: float
    members: List[CouncilMember]
    consensus_score: float
    consensus_direction: str
    agreement: str            # HIGH/MEDIUM/LOW
    agreement_count: int
    active_count: int
    modifier: float
    base_score: float         # 2.0 score
    final_score: float        # 4.0 score
    direction: str            # final from 3-rule system
    skew_bias: str
    rule_applied: str
    tail_risk: Dict[str, Any]
    risk_flags: List[str]
    status: str               # "success" or "insufficient_data"


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


async def run_council(
    ticker: str,
    finnhub,  # Optional[FinnhubClient]
    perplexity,  # PerplexityClient
    tradier,  # TradierClient
    repo,  # HistoricalMovesRepository
    cache,  # SentimentCacheRepository
) -> CouncilResult:
    """
    Run 6-source council consensus for a ticker.

    Sources (renormalized from 85% to 100%):
    1. Perplexity Research (29.4%) — deep prompt
    2. Finnhub Analysts (23.5%) — recommendation trends
    3. Perplexity Quick (11.8%) — cached sentiment
    4. Finnhub News (11.8%) — keyword sentiment
    5. Options Skew (11.8%) — live chain analysis
    6. Historical Pattern (11.8%) — past earnings moves
    """
    from src.domain.skew import analyze_skew
    from src.domain.direction import get_direction
    from src.domain.scoring import apply_sentiment_modifier, calculate_score
    from src.core.config import today_et

    members: List[CouncilMember] = []

    # 1. Validate ticker and look up earnings
    earnings_info = repo.get_next_earnings(ticker)
    if not earnings_info:
        return CouncilResult(
            ticker=ticker, earnings_date="", timing="", price=0,
            members=[], consensus_score=0, consensus_direction="neutral",
            agreement="LOW", agreement_count=0, active_count=0,
            modifier=0, base_score=0, final_score=0,
            direction="NEUTRAL", skew_bias="", rule_applied="",
            tail_risk={}, risk_flags=[], status="no_earnings",
        )

    earnings_date = earnings_info["earnings_date"]
    timing = earnings_info.get("timing", "")

    # 2. Get price
    quote = await tradier.get_quote(ticker)
    price = quote.get("last") or quote.get("close") or quote.get("prevclose") or 0

    # Get position limits for TRR
    position_limits = repo.get_position_limits(ticker)

    # Get historical moves
    moves = repo.get_moves(ticker)
    historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]

    # Calculate tail risk
    tail_risk = {}
    risk_flags = []
    if historical_pcts:
        avg_move = sum(historical_pcts) / len(historical_pcts)
        max_move = max(historical_pcts)
        trr = max_move / avg_move if avg_move > 0 else 0
        if position_limits:
            trr = position_limits.get("tail_risk_ratio", trr)
            tail_risk_level = position_limits.get("tail_risk_level", "UNKNOWN")
        else:
            tail_risk_level = "HIGH" if trr > 2.5 else "NORMAL" if trr >= 1.5 else "LOW"
        tail_risk = {"ratio": round(trr, 2), "level": tail_risk_level, "max_move": round(max_move, 2)}
        if tail_risk_level == "HIGH":
            risk_flags.append(f"TRR {trr:.2f}x HIGH — max 50 contracts")

    # 3. Phase 1: Parallel free/cheap sources
    async def _fetch_finnhub_analysts():
        if not finnhub:
            return CouncilMember(name="Finnhub Analysts", weight=WEIGHTS["finnhub_analysts"], failed=True, status="no API key")
        rec = await finnhub.get_recommendations(ticker)
        if rec.get("error"):
            return CouncilMember(name="Finnhub Analysts", weight=WEIGHTS["finnhub_analysts"], failed=True, status=rec["error"])
        score = normalize_analyst_score(rec)
        total = sum(rec.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
        return CouncilMember(
            name="Finnhub Analysts", weight=WEIGHTS["finnhub_analysts"],
            score=score, direction=score_to_direction(score),
            status=f"{total} analysts", details=rec,
        )

    async def _fetch_finnhub_news():
        if not finnhub:
            return CouncilMember(name="Finnhub News", weight=WEIGHTS["finnhub_news"], failed=True, status="no API key")
        today = today_et()
        articles = await finnhub.get_company_news(ticker, from_date=today, to_date=today, limit=10)
        if not articles:
            # Try wider date range
            from datetime import datetime, timedelta
            end = datetime.strptime(today, "%Y-%m-%d")
            start = (end - timedelta(days=7)).strftime("%Y-%m-%d")
            articles = await finnhub.get_company_news(ticker, from_date=start, to_date=today, limit=10)
        if not articles:
            return CouncilMember(name="Finnhub News", weight=WEIGHTS["finnhub_news"], failed=True, status="no articles")
        score = calculate_news_score(articles)
        return CouncilMember(
            name="Finnhub News", weight=WEIGHTS["finnhub_news"],
            score=score, direction=score_to_direction(score),
            status=f"{len(articles)} articles",
            details={"article_count": len(articles)},
        )

    async def _fetch_skew():
        try:
            expirations = await tradier.get_expirations(ticker)
            nearest_exp = None
            for exp in expirations:
                if exp >= earnings_date:
                    nearest_exp = exp
                    break
            if not nearest_exp:
                return CouncilMember(name="Options Skew", weight=WEIGHTS["options_skew"], failed=True, status="no expiration")
            chain = await tradier.get_options_chain(ticker, nearest_exp)
            if not chain:
                return CouncilMember(name="Options Skew", weight=WEIGHTS["options_skew"], failed=True, status="no chain")
            analysis = analyze_skew(ticker, price, chain)
            if not analysis:
                return CouncilMember(name="Options Skew", weight=WEIGHTS["options_skew"], failed=True, status="insufficient data")
            bias_value = analysis.directional_bias.value.upper()
            score = calculate_skew_score(bias_value)
            return CouncilMember(
                name="Options Skew", weight=WEIGHTS["options_skew"],
                score=score, direction=score_to_direction(score),
                status=bias_value,
                details={"bias": bias_value, "slope": round(analysis.slope, 2), "confidence": round(analysis.confidence, 3)},
            )
        except Exception as e:
            return CouncilMember(name="Options Skew", weight=WEIGHTS["options_skew"], failed=True, status=str(e)[:50])

    async def _fetch_historical():
        if not moves:
            return CouncilMember(name="Historical Pattern", weight=WEIGHTS["historical_pattern"], failed=True, status="no data")
        score = calculate_historical_score(moves)
        return CouncilMember(
            name="Historical Pattern", weight=WEIGHTS["historical_pattern"],
            score=score, direction=score_to_direction(score),
            status=f"{len(moves)}Q",
            details={"quarters": len(moves)},
        )

    async def _fetch_perplexity_quick():
        # Check cache first
        cached = cache.get_sentiment(ticker, earnings_date)
        if cached:
            score = cached.get("score", 0)
            if isinstance(score, (int, float)):
                return CouncilMember(
                    name="Perplexity Quick", weight=WEIGHTS["perplexity_quick"],
                    score=score, direction=cached.get("direction", "neutral"),
                    status="cached",
                )
        # Fetch fresh
        try:
            sentiment = await perplexity.get_sentiment(ticker, earnings_date)
            if sentiment and not sentiment.get("error"):
                cache.save_sentiment(ticker, earnings_date, sentiment)
                score = sentiment.get("score", 0)
                return CouncilMember(
                    name="Perplexity Quick", weight=WEIGHTS["perplexity_quick"],
                    score=score, direction=sentiment.get("direction", "neutral"),
                    status="fresh",
                )
        except Exception as e:
            log("warn", "Perplexity Quick failed", ticker=ticker, error=type(e).__name__)
        return CouncilMember(name="Perplexity Quick", weight=WEIGHTS["perplexity_quick"], failed=True, status="failed")

    # Run Phase 1 in parallel
    phase1_results = await asyncio.gather(
        _fetch_finnhub_analysts(),
        _fetch_finnhub_news(),
        _fetch_skew(),
        _fetch_historical(),
        _fetch_perplexity_quick(),
        return_exceptions=True,
    )

    for result in phase1_results:
        if isinstance(result, Exception):
            log("warn", "Council Phase 1 member failed", error=str(result))
            members.append(CouncilMember(name="Unknown", weight=0, failed=True, status=str(result)[:50]))
        else:
            members.append(result)

    # 4. Phase 2: Deep research
    research_member = CouncilMember(
        name="Perplexity Research", weight=WEIGHTS["perplexity_research"],
        failed=True, status="skipped",
    )

    try:
        prompt = (
            f"For {ticker} earnings on {earnings_date}, analyze:\n"
            f"1. Analyst consensus and recent rating changes\n"
            f"2. EPS/revenue estimates vs whisper numbers\n"
            f"3. Key business metric to watch\n"
            f"4. Bull case and bear case (2 bullets each)\n"
            f"5. Key risk\n\n"
            f"Respond ONLY in this format:\n"
            f"Direction: [bullish/bearish/neutral]\n"
            f"Score: [number -1.0 to +1.0]\n"
            f"Bull Case: [2 bullets, max 15 words each]\n"
            f"Bear Case: [2 bullets, max 15 words each]\n"
            f"Key Risk: [1 bullet, max 20 words]\n"
            f"Analyst Trend: [upgrading/stable/downgrading]"
        )
        response = await perplexity.query([
            {"role": "system", "content": "You are a financial analyst providing pre-earnings sentiment analysis."},
            {"role": "user", "content": prompt},
        ])
        if not response.get("error"):
            text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            if text:
                parsed = parse_research_response(text)
                research_member = CouncilMember(
                    name="Perplexity Research", weight=WEIGHTS["perplexity_research"],
                    score=parsed["score"], direction=parsed["direction"],
                    status="fresh", details=parsed,
                )
    except Exception as e:
        log("warn", "Perplexity Research failed", ticker=ticker, error=type(e).__name__)
        research_member.status = f"error: {type(e).__name__}"

    members.insert(0, research_member)  # Research is first member

    # 5. Calculate weighted consensus (exclude failed, renormalize)
    active = [m for m in members if not m.failed]
    active_count = len(active)

    if active_count < 3:
        return CouncilResult(
            ticker=ticker, earnings_date=earnings_date, timing=timing, price=price,
            members=members, consensus_score=0, consensus_direction="neutral",
            agreement="LOW", agreement_count=0, active_count=active_count,
            modifier=0, base_score=0, final_score=0,
            direction="NEUTRAL", skew_bias="", rule_applied="",
            tail_risk=tail_risk, risk_flags=risk_flags,
            status="insufficient_data",
        )

    total_weight = sum(m.weight for m in active)
    if total_weight > 0:
        consensus_score = sum(m.score * m.weight for m in active) / total_weight
    else:
        consensus_score = 0.0

    consensus_direction = score_to_direction(consensus_score)

    # 6. Agreement metrics (pass only active members)
    agreement, agreement_count, agreement_total = calculate_agreement(active)

    # 7. Apply 3-rule direction system
    skew_member = next((m for m in members if m.name == "Options Skew" and not m.failed), None)
    skew_bias = skew_member.details.get("bias") if skew_member else None

    direction = get_direction(
        skew_bias=skew_bias,
        sentiment_score=consensus_score,
        sentiment_direction=consensus_direction,
    )

    # Determine which rule was applied
    if skew_bias and skew_bias != "NEUTRAL" and consensus_direction != "neutral":
        skew_dir = "bullish" if "BULLISH" in skew_bias else "bearish" if "BEARISH" in skew_bias else "neutral"
        if skew_dir != consensus_direction:
            rule_applied = "Rule 2: Conflict → NEUTRAL"
        else:
            rule_applied = "Rule 3: Skew confirms sentiment"
    elif (not skew_bias or skew_bias == "NEUTRAL") and consensus_direction != "neutral":
        rule_applied = "Rule 1: Sentiment breaks tie"
    else:
        rule_applied = "Rule 3: Default"

    # 8. Calculate base 2.0 score and apply 4.0 modifier
    base_score = 0.0
    if historical_pcts and price:
        from src.domain import calculate_vrp
        avg_move = sum(historical_pcts) / len(historical_pcts)
        vrp_data = calculate_vrp(implied_move_pct=avg_move * 1.5, historical_moves=historical_pcts)
        if not vrp_data.get("error"):
            score_data = calculate_score(
                vrp_ratio=vrp_data["vrp_ratio"],
                vrp_tier=vrp_data["tier"],
                implied_move_pct=avg_move * 1.5,
                liquidity_tier="GOOD",
            )
            base_score = score_data["total_score"]

    final_score = apply_sentiment_modifier(base_score, consensus_score) if base_score > 0 else 0
    # Derive actual modifier from the stepped calculation (matches apply_sentiment_modifier)
    actual_modifier = (final_score - base_score) / base_score if base_score > 0 else 0.0

    # 9. Cache result
    try:
        council_data = {
            "direction": consensus_direction,
            "score": consensus_score,
            "tailwinds": "",
            "headwinds": "",
            "raw": json.dumps({
                "council": True,
                "members": [{"name": m.name, "score": m.score, "direction": m.direction, "failed": m.failed} for m in members],
                "consensus": consensus_score,
                "agreement": agreement,
            }),
        }
        cache.save_sentiment(ticker, earnings_date, council_data)
    except Exception as e:
        log("warn", "Council cache save failed", ticker=ticker, error=type(e).__name__)

    return CouncilResult(
        ticker=ticker,
        earnings_date=earnings_date,
        timing=timing,
        price=price,
        members=members,
        consensus_score=round(consensus_score, 3),
        consensus_direction=consensus_direction,
        agreement=agreement,
        agreement_count=agreement_count,
        active_count=active_count,
        modifier=round(actual_modifier, 3),
        base_score=round(base_score, 1),
        final_score=round(final_score, 1),
        direction=direction,
        skew_bias=skew_bias or "",
        rule_applied=rule_applied,
        tail_risk=tail_risk,
        risk_flags=risk_flags,
        status="success",
    )
