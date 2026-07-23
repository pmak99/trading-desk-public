"""Council orchestrator — runs all sources in parallel and builds CouncilResult."""

import asyncio
import json
from typing import Dict, Any, List, Optional

from src.core.logging import log
from src.core import metrics
from src.domain.council.types import CouncilMember, CouncilResult
from src.domain.council.scoring import (
    WEIGHTS,
    normalize_analyst_score,
    calculate_historical_score,
    calculate_skew_score,
    calculate_news_score,
    score_to_direction,
    calculate_agreement,
    parse_research_response,
)


async def run_council(
    ticker: str,
    finnhub,  # Optional[FinnhubClient]
    perplexity,  # PerplexityClient
    tradier,  # TradierClient
    repo,  # HistoricalMovesRepository
    cache,  # SentimentCacheRepository
) -> CouncilResult:
    """
    Run 6-source weighted consensus + 2 free risk signals for a ticker.

    Weighted council members:
    1. Perplexity Research (29.3%) — deep prompt
    2. Finnhub Analysts (23.5%) — recommendation trends
    3. Perplexity Quick (11.8%) — cached sentiment
    4. Finnhub News (11.8%) — keyword sentiment
    5. Options Skew (11.8%) — live chain analysis
    6. Historical Pattern (11.8%) — past earnings moves

    Risk signals (non-scoring, informational):
    7. SEC EDGAR — NT 10-K/Q late filing check (bearish flag)
    8. FINRA — short volume ratio >= 50% flag
    """
    from src.domain.skew import analyze_skew
    from src.domain.direction import adjust_direction
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

    # 3. Phase 0.5: Risk signal checks (non-scoring, run in parallel with Phase 1)
    async def _check_edgar_flags():
        try:
            from src.integrations.sec_edgar import SecEdgarClient
            edgar = SecEdgarClient()
            filings = await edgar.get_nt_filings(ticker)
            await edgar.close()
            flag = edgar.build_risk_flag(filings)
            if flag:
                risk_flags.append(flag)
        except Exception as e:
            log("debug", "EDGAR check failed", ticker=ticker, error=str(e)[:80])

    async def _check_finra_flags():
        try:
            from src.integrations.finra import FinraClient
            finra = FinraClient()
            ratio = await finra.get_short_ratio(ticker)
            await finra.close()
            flag = finra.build_risk_flag(ratio)
            if flag:
                risk_flags.append(flag)
        except Exception as e:
            log("debug", "FINRA check failed", ticker=ticker, error=str(e)[:80])

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

    # Run Phase 1 + risk checks in parallel
    phase1_results = await asyncio.gather(
        _fetch_finnhub_analysts(),
        _fetch_finnhub_news(),
        _fetch_skew(),
        _fetch_historical(),
        _fetch_perplexity_quick(),
        _check_edgar_flags(),
        _check_finra_flags(),
        return_exceptions=True,
    )

    for result in phase1_results:
        if isinstance(result, Exception):
            log("warn", "Council Phase 1 member failed", error=str(result))
            members.append(CouncilMember(name="Unknown", weight=0, failed=True, status=str(result)[:50]))
        elif isinstance(result, CouncilMember):
            members.append(result)
        # None results are from risk-flag coroutines (EDGAR, FINRA) — they already mutated risk_flags

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

    adj = adjust_direction(
        skew_bias=skew_bias or "NEUTRAL",
        sentiment_score=consensus_score,
        sentiment_direction=consensus_direction,
    )
    direction = adj.adjusted_bias.value.upper()
    rule_applied = adj.rule_applied

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
