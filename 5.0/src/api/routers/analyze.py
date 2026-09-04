"""Analyze endpoint — deep single-ticker VRP analysis."""

import asyncio
import re
import time
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException

from src.core.config import today_et, settings
from src.core.logging import log
from src.core import metrics
from src.domain import (
    calculate_vrp,
    classify_liquidity_tier,
    calculate_score,
    apply_sentiment_modifier,
    generate_strategies,
    calculate_position_size,
    normalize_ticker,
    InvalidTickerError,
    has_weekly_options,
)
from src.domain.implied_move import (
    calculate_implied_move_from_chain,
    fetch_real_implied_move,
    get_implied_move_with_fallback,
)
from src.domain.skew import analyze_skew
from src.domain.direction import get_direction
from src.domain.position_sizing import (
    apply_compound_risk_cap, apply_vrp_liquidity_reduction, is_tradeable_tier,
)
from src.formatters.cli import format_analyze_cli
from src.api.state import _mask_sensitive
from src.api.dependencies import (
    verify_api_key,
    get_tradier,
    get_perplexity,
    get_twelvedata,
    get_historical_repo,
    get_sentiment_cache,
    get_finnhub,
)

router = APIRouter(prefix="/api", tags=["analysis"])

@router.get("/analyze")
async def analyze(ticker: str, date: str = None, format: str = "json", fresh: bool = False, _: bool = Depends(verify_api_key)):
    """
    Deep analysis of single ticker.

    Returns VRP, liquidity, sentiment, and strategy recommendations.

    Args:
        fresh: If True, skip sentiment cache and fetch fresh data
    """
    # Validate date parameter if provided
    if date and not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        raise HTTPException(400, "Invalid date format (expected YYYY-MM-DD)")

    # Validate and normalize ticker using centralized validation
    try:
        ticker = normalize_ticker(ticker)
    except InvalidTickerError as e:
        raise HTTPException(400, str(e))

    log("info", "Analyze request", ticker=ticker, date=date)
    start_time = time.time()

    try:
        # Get historical data
        repo = get_historical_repo()
        moves = repo.get_moves(ticker)
        historical_count = len(moves)

        # Get position limits (TRR data) from precomputed table
        position_limits = repo.get_position_limits(ticker)

        if historical_count < 4:
            return {
                "ticker": ticker,
                "status": "insufficient_data",
                "message": f"Need at least 4 historical moves, found {historical_count or 0}. Use stock ticker symbol (e.g., NKE not NIKE).",
            }

        # Determine earnings date - use provided date or look up from calendar
        target_date = date
        earnings_timing = ""
        if not target_date:
            earnings_info = repo.get_next_earnings(ticker)
            if earnings_info:
                target_date = earnings_info["earnings_date"]
                earnings_timing = earnings_info.get("timing", "")
                log("info", "Found earnings date from calendar", ticker=ticker, date=target_date)

                # Freshness validation: if earnings within 7 days, validate against Alpha Vantage
                try:
                    from datetime import datetime
                    db_date = datetime.strptime(target_date, "%Y-%m-%d").date()
                    today = datetime.strptime(today_et(), "%Y-%m-%d").date()
                    days_until = (db_date - today).days

                    if 0 <= days_until <= 7:
                        finnhub_client = get_finnhub()
                        if not finnhub_client:
                            raise ValueError("Finnhub not configured")
                        av_earnings = await finnhub_client.get_earnings_calendar(symbol=ticker)
                        if av_earnings:
                            av_date = av_earnings[0].get("report_date")
                            if av_date and av_date != target_date:
                                # Check if API is returning next quarter
                                av_date_parsed = datetime.strptime(av_date, "%Y-%m-%d").date()
                                date_diff_days = (av_date_parsed - db_date).days

                                # If API returns 45+ days different (earlier OR later), it's a different quarter
                                # Later: API shows next quarter (earnings already reported or DB date was wrong)
                                # Earlier: DB has next quarter date but API shows current quarter (rare edge case)
                                # Either way, don't blindly accept - skip this ticker
                                # Note: 5.0 is stateless per-request, doesn't update DB (sync handled separately)
                                NEXT_QUARTER_THRESHOLD_DAYS = 45
                                if abs(date_diff_days) >= NEXT_QUARTER_THRESHOLD_DAYS:
                                    direction = "later" if date_diff_days > 0 else "earlier"
                                    log("warn", f"API shows different quarter ({direction}), DB date likely stale",
                                        ticker=ticker, db_date=target_date,
                                        api_date=av_date, diff_days=abs(date_diff_days))
                                    return {
                                        "ticker": ticker,
                                        "status": "stale_or_reported",
                                        "message": f"DB date {target_date} stale or mismatched. API shows: {av_date} ({abs(date_diff_days)}d {direction})",
                                    }

                                log("warn", "Earnings date changed", ticker=ticker, db_date=target_date, api_date=av_date)
                                target_date = av_date
                except Exception as e:
                    log("debug", "Earnings validation failed, using cached date", ticker=ticker, error=str(e))
            else:
                # No earnings in calendar - query Finnhub directly
                log("info", "No earnings in calendar, querying Finnhub", ticker=ticker)
                try:
                    finnhub_client = get_finnhub()
                    if not finnhub_client:
                        return {
                            "ticker": ticker,
                            "status": "no_earnings",
                            "message": f"Finnhub not configured — cannot look up earnings for {ticker}",
                        }
                    av_earnings = await finnhub_client.get_earnings_calendar(symbol=ticker)
                    if av_earnings:
                        av_date = av_earnings[0].get("report_date")
                        if av_date:
                            target_date = av_date
                            log("info", "Found earnings from Alpha Vantage", ticker=ticker, date=av_date)
                            # Store in calendar for future use
                            repo.upsert_earnings_calendar(av_earnings)
                        else:
                            return {
                                "ticker": ticker,
                                "status": "no_earnings",
                                "message": f"No upcoming earnings found for {ticker}",
                            }
                    else:
                        return {
                            "ticker": ticker,
                            "status": "no_earnings",
                            "message": f"No upcoming earnings found for {ticker}",
                        }
                except Exception as e:
                    log("error", "Failed to fetch earnings from Finnhub", ticker=ticker, error=str(e))
                    return {
                        "ticker": ticker,
                        "status": "no_earnings",
                        "message": f"Could not determine earnings date for {ticker}",
                    }

        # Get current price from Tradier (more reliable than Yahoo in cloud)
        tradier = get_tradier()
        quote = await tradier.get_quote(ticker)
        price = quote.get("last") or quote.get("close") or quote.get("prevclose")
        if not price:
            # Fallback to Twelve Data if Tradier fails (more reliable than Yahoo)
            twelvedata = get_twelvedata()
            price = await twelvedata.get_current_price(ticker)
        if not price:
            return {
                "ticker": ticker,
                "status": "error",
                "message": "Could not get current price",
            }

        # Get options chain for implied move
        expirations = await tradier.get_expirations(ticker)

        # Check for weekly options availability
        has_weeklies, weekly_reason = has_weekly_options(expirations, target_date)
        weekly_warning = None
        if settings.require_weekly_options and not has_weeklies:
            weekly_warning = f"No weekly options: {weekly_reason}"

        nearest_exp = None
        for exp in expirations:
            if exp >= target_date:
                nearest_exp = exp
                break

        implied_move_data = None
        liquidity_tier = "REJECT"
        skew_analysis = None
        if nearest_exp:
            chain = await tradier.get_options_chain(ticker, nearest_exp)
            if chain:
                implied_move_data = calculate_implied_move_from_chain(chain, price)

                # Calculate liquidity from chain
                total_oi = sum(opt.get("open_interest") or 0 for opt in chain)
                avg_spread = 0
                spread_count = 0
                for opt in chain:
                    bid = opt.get("bid") or 0
                    ask = opt.get("ask") or 0
                    if bid > 0 and ask > 0:
                        spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
                        avg_spread += spread_pct
                        spread_count += 1

                if spread_count > 0:
                    avg_spread /= spread_count

                liquidity_tier = classify_liquidity_tier(
                    oi=total_oi,
                    spread_pct=avg_spread,
                    position_size=settings.DEFAULT_POSITION_SIZE,
                )

                # Analyze skew for directional bias
                skew_analysis = analyze_skew(ticker, price, chain)

        # Calculate VRP - extract historical move percentages (use intraday, matches 2.0)
        historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
        historical_avg = sum(historical_pcts) / len(historical_pcts) if historical_pcts else 5.0
        implied_move_pct = implied_move_data["implied_move_pct"] if implied_move_data else historical_avg * 1.5
        vrp_data = calculate_vrp(
            implied_move_pct=implied_move_pct,
            historical_moves=historical_pcts,
        )

        # Calculate tail risk from historical data (fallback if not in position_limits table)
        if historical_pcts:
            max_move = max(historical_pcts)
            tail_risk_ratio = max_move / historical_avg if historical_avg > 0 else 0
            if tail_risk_ratio > 2.5:
                tail_risk_level = "HIGH"
            elif tail_risk_ratio >= 1.5:
                tail_risk_level = "NORMAL"
            else:
                tail_risk_level = "LOW"
        else:
            max_move = 0
            tail_risk_ratio = 0
            tail_risk_level = "UNKNOWN"

        # Use precomputed position_limits if available, otherwise calculate on the fly
        if not position_limits and historical_pcts:
            position_limits = {
                "ticker": ticker,
                "tail_risk_ratio": round(tail_risk_ratio, 2),
                "tail_risk_level": tail_risk_level,
                "avg_move": round(historical_avg, 2),
                "max_move": round(max_move, 2),
                "num_quarters": len(historical_pcts),
                # Default limits for HIGH tail risk
                "max_contracts": 50 if tail_risk_level == "HIGH" else 100,
                "max_notional": 25000 if tail_risk_level == "HIGH" else 50000,
            }

        # Compound tail risk cap (parity with 2.0 SizingContext, Jul 2026):
        # live TRR HIGH + bearish skew -> max 25 contracts. Applied to both
        # DB-precomputed and fallback limits; uses live tail_risk_level, not
        # the frozen position_limits snapshot.
        position_limits = apply_compound_risk_cap(
            position_limits,
            tail_risk_level,
            skew_analysis.directional_bias.value if skew_analysis else None,
        )

        # CLAUDE.md sizing Rule 4 (parity with 2.0 SizingContext, added
        # 2026-07-27): MARGINAL VRP or WARNING liquidity -> 50% reduction,
        # single (non-stacking), skipped if compound risk already capped
        # tighter. Must run after apply_compound_risk_cap so it can see
        # compound_risk_active.
        position_limits = apply_vrp_liquidity_reduction(
            position_limits, vrp_data["tier"], liquidity_tier,
        )

        # Calculate score
        score_data = calculate_score(
            vrp_ratio=vrp_data["vrp_ratio"],
            vrp_tier=vrp_data["tier"],
            implied_move_pct=implied_move_pct,
            liquidity_tier=liquidity_tier,
        )

        # Get sentiment
        sentiment_data = None
        cache = get_sentiment_cache()

        # Check cache first (unless fresh=True)
        if not fresh:
            cached = cache.get_sentiment(ticker, target_date)
            if cached:
                sentiment_data = cached

        if not sentiment_data:
            perplexity = get_perplexity()
            sentiment_data = await perplexity.get_sentiment(ticker, target_date)
            # Save to cache if successful
            if sentiment_data and not sentiment_data.get("error"):
                cache.save_sentiment(ticker, target_date, sentiment_data)

        # Apply sentiment modifier and determine direction
        # Uses 3-rule system: skew + sentiment -> adjusted direction
        skew_bias = skew_analysis.directional_bias.value if skew_analysis else None
        sentiment_score = sentiment_data.get("score", 0) if sentiment_data else None
        sentiment_direction = sentiment_data.get("direction") if sentiment_data else None

        direction = get_direction(
            skew_bias=skew_bias,
            sentiment_score=sentiment_score,
            sentiment_direction=sentiment_direction,
        )

        final_score = score_data["total_score"]
        if sentiment_data and sentiment_score is not None:
            final_score = apply_sentiment_modifier(score_data["total_score"], sentiment_score)

        # Tradeable gate (parity with 2.0 VRPResult.is_tradeable, added
        # 2026-07-27): SKIP tier (<1.2x VRP) means don't trade -- previously
        # this endpoint generated and returned a strategy recommendation
        # regardless of tier, including SKIP. MARGINAL remains tradeable
        # (at the 50% reduced size applied above) per CLAUDE.md.
        tradeable = is_tradeable_tier(vrp_data["tier"])

        strategies = []
        account_size = settings.account_size
        position_size = 0
        if tradeable:
            strategies = generate_strategies(
                ticker=ticker,
                price=price,
                implied_move_pct=implied_move_pct,
                direction=direction,
                liquidity_tier=liquidity_tier,
                expiration=nearest_exp or "",
            )

            if strategies:  # REJECT liquidity allowed but penalized in scoring (Feb 2026)
                top_strategy = strategies[0]
                position_size = calculate_position_size(
                    account_value=account_size,
                    max_risk_per_contract=top_strategy.max_risk,
                    win_rate=0.574,  # Historical win rate
                    risk_reward=top_strategy.risk_reward,
                    max_contracts_cap=(
                        position_limits.get("max_contracts") if position_limits else None
                    ),
                )

        result = {
            "ticker": ticker,
            "status": "success",
            "tradeable": tradeable,
            "price": price,
            "earnings_date": target_date,
            "timing": earnings_timing,
            "expiration": nearest_exp,
            "vrp": {
                "ratio": vrp_data["vrp_ratio"],
                "tier": vrp_data["tier"],
                "implied_move_pct": implied_move_pct,
                "historical_mean": historical_avg,
                "historical_count": historical_count,
            },
            "liquidity_tier": liquidity_tier,
            "score": {
                "base": score_data["total_score"],
                "final": final_score,
                "components": score_data["components"],
            },
            "sentiment": sentiment_data,
            "skew": {
                "bias": skew_analysis.directional_bias.value if skew_analysis else None,
                "slope": round(skew_analysis.slope, 2) if skew_analysis else None,
                "confidence": round(skew_analysis.confidence, 3) if skew_analysis else None,
                "points": skew_analysis.num_points if skew_analysis else 0,
            } if skew_analysis else None,
            "direction": direction,
            "strategies": [
                {
                    "name": s.name,
                    "description": s.description,
                    "max_profit": s.max_profit,
                    "max_risk": s.max_risk,
                    "pop": s.pop,
                    "breakeven": s.breakeven,
                }
                for s in strategies
            ],
            "position_size": position_size,
            "position_limits": position_limits,
            "tail_risk": {
                "ratio": round(tail_risk_ratio, 2),
                "level": tail_risk_level,
                "max_move": round(max_move, 2),
            },
            "has_weekly_options": has_weeklies,
            "weekly_warning": weekly_warning,
        }

        # Record metrics
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_success("analyze", duration_ms)
        metrics.vrp_analyzed(ticker, vrp_data["vrp_ratio"], vrp_data["tier"])
        metrics.liquidity_checked(liquidity_tier)
        if sentiment_data and sentiment_data.get("score"):
            metrics.sentiment_fetched(ticker, sentiment_data["score"])

        # Format for CLI if requested
        if format == "cli":
            return {"output": format_analyze_cli(result)}

        return result

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("analyze", duration_ms)
        log("error", "Analyze failed", ticker=ticker, error=type(e).__name__, details=_mask_sensitive(str(e)))
        raise HTTPException(500, "Analysis failed")

