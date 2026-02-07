"""
Analysis endpoints for Trading Desk 5.0.

Provides VRP analysis, earnings scanning, and whisper (most anticipated) functionality.
"""

import asyncio
import re
import time
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException

from src.core.config import now_et, today_et, settings
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
    is_valid_ticker,
    has_weekly_options,
)
from src.domain.implied_move import (
    calculate_implied_move_from_chain,
    fetch_real_implied_move,
    get_implied_move_with_fallback,
)
from src.domain.skew import analyze_skew
from src.domain.direction import get_direction
from src.formatters.cli import format_digest_cli, format_analyze_cli
from src.api.state import _mask_sensitive
from src.api.dependencies import (
    verify_api_key,
    get_tradier,
    get_alphavantage,
    get_perplexity,
    get_twelvedata,
    get_budget_tracker,
    get_historical_repo,
    get_sentiment_cache,
    get_vrp_cache,
)

from datetime import timedelta

router = APIRouter(prefix="/api", tags=["analysis"])

# Scan timeout for whisper endpoint to avoid Cloud Run timeout
MAX_SCAN_TIME_SECONDS = 120

# Concurrency limit for parallel ticker analysis (ported from 6.0)
# Prevents database connection pool exhaustion and API rate limiting
MAX_CONCURRENT_ANALYSIS = 5


async def _analyze_single_ticker(
    ticker: str,
    earnings_date: str,
    name: str,
    repo,
    tradier,
    sentiment_cache,
    vrp_cache,
    semaphore: asyncio.Semaphore,
    prefetched_moves: Optional[List[Dict[str, Any]]] = None,
    filter_mode: str = "filter",
    fresh: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Analyze a single ticker for VRP opportunity.

    Uses semaphore for controlled concurrency across parallel calls.
    Uses VRP cache to reduce Tradier API calls (smart TTL based on earnings proximity).
    Accepts pre-fetched moves to reduce N+1 database queries.
    Returns result dict if qualified, None otherwise.

    Args:
        filter_mode: "filter" to return None for non-weekly tickers (default),
                     "warn" to include ticker with warning
        fresh: If True, bypass VRP and sentiment caches (fetch fresh data)
    """
    async with semaphore:
        try:
            # Get historical data (use pre-fetched if available)
            moves = prefetched_moves if prefetched_moves is not None else repo.get_moves(ticker)
            historical_count = len(moves)

            if historical_count < 4:
                return None

            # Extract historical move percentages (use intraday, matches 2.0)
            historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
            if not historical_pcts:
                return None

            historical_avg = sum(historical_pcts) / len(historical_pcts)

            # Check VRP cache first (reduces Tradier API calls by ~89%)
            # Skip cache if fresh=True (Telegram requests real-time data)
            cached_vrp = None if fresh else vrp_cache.get_vrp(ticker, earnings_date)
            has_weekly = True  # Default: permissive on error
            weekly_reason = ""
            if cached_vrp:
                # Use cached VRP data
                implied_move_pct = cached_vrp["implied_move_pct"]
                vrp_ratio = cached_vrp["vrp_ratio"]
                vrp_tier = cached_vrp["vrp_tier"]
                price = cached_vrp.get("price")
                expiration = cached_vrp.get("expiration", "")
                used_real_data = cached_vrp.get("used_real_data", False)
                has_weekly = cached_vrp.get("has_weekly_options", True)
                weekly_reason = cached_vrp.get("weekly_reason", "")
                log("debug", "VRP cache hit", ticker=ticker, vrp_ratio=vrp_ratio)
                metrics.count("ivcrush.vrp_cache.hit", {"ticker": ticker})
            else:
                # Cache miss - fetch fresh data from Tradier
                metrics.count("ivcrush.vrp_cache.miss", {"ticker": ticker})

                # Fetch real implied move from Tradier options chain
                im_result = await fetch_real_implied_move(
                    tradier, ticker, earnings_date
                )

                # Skip if we couldn't get a price
                if im_result.get("error") == "No price available":
                    return None

                implied_move_pct, used_real_data = get_implied_move_with_fallback(
                    im_result, historical_avg
                )
                price = im_result.get("price")
                expiration = im_result.get("expiration", "")
                has_weekly = im_result.get("has_weekly_options", True)
                weekly_reason = im_result.get("weekly_reason", "")

                # Calculate VRP
                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                # Skip if VRP calculation failed
                if vrp_data.get("error"):
                    return None

                vrp_ratio = vrp_data["vrp_ratio"]
                vrp_tier = vrp_data["tier"]

                # Cache the VRP data for future requests (includes weekly options status)
                vrp_cache.save_vrp(ticker, earnings_date, {
                    "implied_move_pct": implied_move_pct,
                    "vrp_ratio": vrp_ratio,
                    "vrp_tier": vrp_tier,
                    "historical_mean": historical_avg,
                    "price": price,
                    "expiration": expiration,
                    "used_real_data": used_real_data,
                    "has_weekly_options": has_weekly,
                    "weekly_reason": weekly_reason,
                })
                log("debug", "VRP cached", ticker=ticker, vrp_ratio=vrp_ratio)

            # Check weekly options filter (opt-in via REQUIRE_WEEKLY_OPTIONS env var)
            weekly_warning = None
            if settings.require_weekly_options and not has_weekly:
                if filter_mode == "filter":
                    log("debug", "Filtered out non-weekly ticker", ticker=ticker, reason=weekly_reason)
                    return None
                else:
                    # filter_mode == "warn": include ticker but with warning
                    weekly_warning = f"No weekly options: {weekly_reason}"
                    log("debug", "Weekly options warning", ticker=ticker, reason=weekly_reason)

            # Skip if below discovery threshold
            if vrp_ratio < settings.VRP_DISCOVERY:
                return None

            # Calculate score (assume GOOD liquidity for screening)
            score_data = calculate_score(
                vrp_ratio=vrp_ratio,
                vrp_tier=vrp_tier,
                implied_move_pct=implied_move_pct,
                liquidity_tier="GOOD",
            )

            # Get cached sentiment if available and use get_direction for consistency
            # Note: skew analysis not available in whisper (would require extra API calls)
            # so we pass skew_bias=None to let sentiment drive direction
            # Skip cache if fresh=True (Telegram requests real-time data)
            sentiment = None if fresh else sentiment_cache.get_sentiment(ticker, earnings_date)
            sentiment_score = sentiment.get("score") if sentiment else None
            sentiment_direction = sentiment.get("direction") if sentiment else None
            direction = get_direction(
                skew_bias=None,  # No skew analysis in whisper endpoint
                sentiment_score=sentiment_score,
                sentiment_direction=sentiment_direction,
            )

            # Generate trading strategies
            strategy_name = f"VRP {vrp_tier}"  # Fallback
            credit = 0

            if price and implied_move_pct > 0:
                strategies = generate_strategies(
                    ticker=ticker,
                    price=price,
                    implied_move_pct=implied_move_pct,
                    direction=direction,
                    liquidity_tier="GOOD",  # Assumed for screening
                    expiration=expiration,
                )
                if strategies:
                    top_strategy = strategies[0]
                    strategy_name = top_strategy.description
                    credit = top_strategy.max_profit / 100  # Convert to per-contract

            return {
                "ticker": ticker,
                "name": name,
                "earnings_date": earnings_date,
                "price": price,
                "vrp_ratio": vrp_ratio,
                "vrp_tier": vrp_tier,
                "implied_move_pct": round(implied_move_pct, 1),
                "historical_mean": round(historical_avg, 1),
                "score": score_data["total_score"],
                "real_data": used_real_data,
                "direction": direction,
                "strategy": strategy_name,
                "credit": credit,
                "has_weekly_options": has_weekly,
                "weekly_warning": weekly_warning,
            }

        except Exception as ex:
            log("debug", "Skipping ticker", ticker=ticker, error=str(ex))
            return None


async def _scan_tickers_for_whisper(
    upcoming: List[Dict],
    repo,
    tradier,
    fresh: bool = False,
    partial_results: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Scan tickers for VRP opportunities using parallel execution.

    Uses REAL implied move from Tradier options chains (ATM straddle pricing)
    to calculate accurate VRP ratios. Falls back to estimate only if options
    data unavailable.

    Optimizations:
    - Parallelization (from 6.0) - semaphore-controlled concurrency
    - VRP caching - smart TTL reduces Tradier API calls by ~89%
    - Batch DB queries - single query for all historical moves (30 queries -> 1)

    Target: 60s -> 15s for 30 tickers.

    Extracted for asyncio.wait_for timeout support.
    Results are accumulated into partial_results list as tasks complete,
    so on timeout the caller can still access completed results.

    Args:
        fresh: If True, bypass VRP and sentiment caches (for Telegram real-time requests)
        partial_results: Shared list that accumulates results as tasks complete.
                        On timeout, this list contains all results completed before timeout.
    """
    sentiment_cache = get_sentiment_cache()
    vrp_cache = get_vrp_cache()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ANALYSIS)

    # Use provided list or create new one
    if partial_results is None:
        partial_results = []

    # Filter out invalid tickers (e.g., COF-PI preferred stocks, warrants)
    # These don't have options and can't be analyzed for IV crush
    valid_upcoming = [e for e in upcoming if is_valid_ticker(e["symbol"])]
    invalid_count = len(upcoming) - len(valid_upcoming)
    if invalid_count > 0:
        invalid_tickers = [e["symbol"] for e in upcoming if not is_valid_ticker(e["symbol"])]
        log("debug", "Filtered invalid tickers", count=invalid_count, tickers=invalid_tickers[:5])

    # Limit to 100 tickers (increased from 30 to capture more opportunities)
    tickers_to_scan = valid_upcoming[:100]

    # Batch fetch all historical moves in ONE query (30 queries -> 1)
    all_tickers = [e["symbol"] for e in tickers_to_scan]
    batch_moves = repo.get_moves_batch(all_tickers, limit=12)
    log("debug", "Batch fetched historical moves", ticker_count=len(all_tickers))

    # Create parallel tasks for all tickers
    tasks = []
    for e in tickers_to_scan:
        ticker = e["symbol"]
        earnings_date = e["report_date"]
        name = e.get("name", "")

        # Get pre-fetched moves for this ticker
        prefetched_moves = batch_moves.get(ticker, [])

        task = asyncio.create_task(
            _analyze_single_ticker(
                ticker=ticker,
                earnings_date=earnings_date,
                name=name,
                repo=repo,
                tradier=tradier,
                sentiment_cache=sentiment_cache,
                vrp_cache=vrp_cache,
                semaphore=semaphore,
                prefetched_moves=prefetched_moves,
                fresh=fresh
            )
        )
        tasks.append(task)

    # Execute all tasks in parallel with exception handling
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    # Detect high error rates (possible API outage)
    error_count = sum(1 for r in results_raw if isinstance(r, Exception))
    total_tasks = len(results_raw)
    if total_tasks > 0 and error_count > total_tasks * 0.5:
        error_types = {}
        for r in results_raw:
            if isinstance(r, Exception):
                err_type = type(r).__name__
                error_types[err_type] = error_types.get(err_type, 0) + 1
        log("error", "High error rate in whisper scan - possible API outage",
            total=total_tasks, errors=error_count,
            error_rate_pct=round(error_count / total_tasks * 100, 1),
            error_types=error_types)
        metrics.count("ivcrush.whisper.high_error_rate", {
            "errors": str(error_count), "total": str(total_tasks)
        })

    # Filter out None results and exceptions, accumulate into shared list
    for result in results_raw:
        if isinstance(result, Exception):
            log("warning", "Task failed with exception", error=str(result))
            continue
        if result is not None:
            partial_results.append(result)

    return partial_results, error_count


@router.get("/scan")
async def scan(date: str, format: str = "json", _: bool = Depends(verify_api_key)):
    """
    Scan all earnings for a specific date.

    Returns all tickers with earnings on the given date, sorted by VRP score.
    Includes VRP analysis, liquidity tier, and basic metrics.

    Args:
        date: Target date in YYYY-MM-DD format (required)
        format: Output format - "json" or "cli"
    """
    # Validate date format and actual validity
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        raise HTTPException(400, "Invalid date format (expected YYYY-MM-DD)")

    # Validate it's a real date (e.g., not 2026-02-30)
    try:
        from datetime import datetime as dt
        dt.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(400, f"Invalid date: {date}")

    log("info", "Scan request", date=date)
    start_time = time.time()

    try:
        # Get earnings from database (populated by calendar-sync job)
        # This avoids rate-limiting issues with Alpha Vantage API
        repo = get_historical_repo()
        target_earnings = repo.get_earnings_by_date(date)
        log("debug", "Fetched earnings from database", date=date, count=len(target_earnings))

        if not target_earnings:
            return {
                "status": "success",
                "date": date,
                "message": "No earnings found for this date",
                "total_found": 0,
                "qualified": [],
                "filtered": [],
                "errors": [],
            }

        # Get dependencies (repo already created above)
        tradier = get_tradier()

        qualified = []
        filtered = []
        errors = []

        for e in target_earnings[:50]:  # Limit to 50 tickers
            ticker = e["symbol"]
            earnings_date = e["report_date"]

            try:
                # Check historical data requirement
                moves = repo.get_moves(ticker)
                historical_count = len(moves)

                if historical_count < 4:
                    filtered.append({
                        "ticker": ticker,
                        "reason": f"Insufficient history ({historical_count} quarters)",
                    })
                    continue

                # Extract historical move percentages
                historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                if not historical_pcts:
                    filtered.append({
                        "ticker": ticker,
                        "reason": "No valid historical moves",
                    })
                    continue

                historical_avg = sum(historical_pcts) / len(historical_pcts)

                # Fetch real implied move
                im_result = await fetch_real_implied_move(tradier, ticker, earnings_date)

                # Skip if we couldn't get a price
                if im_result.get("error") == "No price available":
                    filtered.append({
                        "ticker": ticker,
                        "reason": "No price available",
                    })
                    continue

                implied_move_pct, used_real_data = get_implied_move_with_fallback(
                    im_result, historical_avg
                )
                price = im_result.get("price")

                # Calculate VRP
                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                if vrp_data.get("error"):
                    filtered.append({
                        "ticker": ticker,
                        "reason": f"VRP calculation failed: {vrp_data.get('error')}",
                    })
                    continue

                vrp_ratio = vrp_data.get("vrp_ratio", 0)
                vrp_tier = vrp_data.get("tier", "SKIP")

                # Get liquidity tier from options chain
                liquidity_tier = "UNKNOWN"
                if im_result.get("chain"):
                    chain = im_result["chain"]
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

                # Calculate score
                score_data = calculate_score(
                    vrp_ratio=vrp_ratio,
                    vrp_tier=vrp_tier,
                    implied_move_pct=implied_move_pct,
                    liquidity_tier=liquidity_tier if liquidity_tier != "UNKNOWN" else "WARNING",
                )

                # Determine if qualified (VRP >= discovery threshold)
                if vrp_ratio >= settings.VRP_DISCOVERY:
                    qualified.append({
                        "ticker": ticker,
                        "name": e.get("name", ""),
                        "price": price,
                        "vrp_ratio": round(vrp_ratio, 2),
                        "vrp_tier": vrp_tier,
                        "implied_move_pct": round(implied_move_pct, 1),
                        "historical_mean": round(historical_avg, 1),
                        "historical_count": historical_count,
                        "liquidity_tier": liquidity_tier,
                        "score": round(score_data["total_score"], 1),
                        "real_data": used_real_data,
                    })
                else:
                    filtered.append({
                        "ticker": ticker,
                        "reason": f"Low VRP ({vrp_ratio:.2f}x < {settings.VRP_DISCOVERY}x)",
                        "vrp_ratio": round(vrp_ratio, 2),
                    })

            except Exception as ex:
                log("debug", "Scan failed for ticker", ticker=ticker, error=str(ex))
                errors.append({
                    "ticker": ticker,
                    "error": str(ex)[:100],
                })
                continue

        # Sort qualified by score descending
        qualified.sort(key=lambda x: x["score"], reverse=True)

        duration_ms = (time.time() - start_time) * 1000
        metrics.request_success("scan", duration_ms)
        metrics.tickers_qualified(len(qualified))

        result = {
            "status": "success",
            "date": date,
            "total_found": len(target_earnings),
            "analyzed": len(qualified) + len(filtered) + len(errors),
            "qualified_count": len(qualified),
            "filtered_count": len(filtered),
            "error_count": len(errors),
            "qualified": qualified,
            "filtered": filtered[:10],  # Limit filtered output
            "errors": errors[:5] if errors else None,
        }

        # Format for CLI if requested
        if format == "cli":
            lines = [f"📅 Scan Results for {date}", "=" * 40]
            lines.append(f"Found: {len(target_earnings)} | Qualified: {len(qualified)} | Filtered: {len(filtered)}")
            lines.append("")
            if qualified:
                lines.append("🎯 QUALIFIED OPPORTUNITIES:")
                for t in qualified[:10]:
                    tier_emoji = "🟢" if t["liquidity_tier"] in ["EXCELLENT", "GOOD"] else "🟡" if t["liquidity_tier"] == "WARNING" else "🔴"
                    lines.append(f"  {tier_emoji} {t['ticker']}: VRP {t['vrp_ratio']}x ({t['vrp_tier']}) | Score {t['score']} | {t['liquidity_tier']}")
            else:
                lines.append("❌ No qualified opportunities found")
            return {"output": "\n".join(lines)}

        return result

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("scan", duration_ms)
        log("error", "Scan failed", date=date, error=type(e).__name__, details=_mask_sensitive(str(e)))
        raise HTTPException(500, "Scan failed")


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
        if not target_date:
            earnings_info = repo.get_next_earnings(ticker)
            if earnings_info:
                target_date = earnings_info["earnings_date"]
                log("info", "Found earnings date from calendar", ticker=ticker, date=target_date)

                # Freshness validation: if earnings within 7 days, validate against Alpha Vantage
                try:
                    from datetime import datetime
                    db_date = datetime.strptime(target_date, "%Y-%m-%d").date()
                    today = datetime.strptime(today_et(), "%Y-%m-%d").date()
                    days_until = (db_date - today).days

                    if 0 <= days_until <= 7:
                        alphavantage = get_alphavantage()
                        av_earnings = await alphavantage.get_earnings_calendar(symbol=ticker)
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
                # No earnings in calendar - query Alpha Vantage directly
                log("info", "No earnings in calendar, querying Alpha Vantage", ticker=ticker)
                try:
                    alphavantage = get_alphavantage()
                    av_earnings = await alphavantage.get_earnings_calendar(symbol=ticker)
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
                    log("error", "Failed to fetch earnings from Alpha Vantage", ticker=ticker, error=str(e))
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

        # Calculate score
        score_data = calculate_score(
            vrp_ratio=vrp_data["vrp_ratio"],
            vrp_tier=vrp_data["tier"],
            implied_move_pct=implied_move_pct,
            liquidity_tier=liquidity_tier,
        )

        # Get sentiment if budget allows
        sentiment_data = None
        budget = get_budget_tracker()
        cache = get_sentiment_cache()

        # Check cache first (unless fresh=True)
        if not fresh:
            cached = cache.get_sentiment(ticker, target_date)
            if cached:
                sentiment_data = cached

        if not sentiment_data and budget.can_call("perplexity"):
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

        # Generate strategies
        strategies = generate_strategies(
            ticker=ticker,
            price=price,
            implied_move_pct=implied_move_pct,
            direction=direction,
            liquidity_tier=liquidity_tier,
            expiration=nearest_exp or "",
        )

        # Calculate position size for top strategy
        # Account size from environment or default (configurable)
        account_size = settings.account_size
        position_size = 0
        if strategies:  # REJECT liquidity allowed but penalized in scoring (Feb 2026)
            top_strategy = strategies[0]
            position_size = calculate_position_size(
                account_value=account_size,
                max_risk_per_contract=top_strategy.max_risk,
                win_rate=0.574,  # Historical win rate
                risk_reward=top_strategy.risk_reward,
            )

        result = {
            "ticker": ticker,
            "status": "success",
            "price": price,
            "earnings_date": target_date,
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


@router.get("/whisper")
async def whisper(date: str = None, format: str = "json", fresh: bool = False, _: bool = Depends(verify_api_key)):
    """
    Most anticipated earnings - find high-VRP opportunities.

    Scans upcoming earnings and returns qualified tickers sorted by score.

    Args:
        fresh: If True, bypass VRP and sentiment caches (for Telegram real-time requests)
    """
    log("info", "Whisper request", date=date, fresh=fresh)
    start_time = time.time()

    try:
        # Get earnings from database (populated by calendar-sync job)
        # This avoids rate-limiting issues with Alpha Vantage API
        repo = get_historical_repo()
        tradier = get_tradier()

        # Build target dates
        today = today_et()
        target_dates = [today]
        for i in range(1, 5):
            future = (now_et() + timedelta(days=i)).strftime("%Y-%m-%d")
            target_dates.append(future)

        if date:
            target_dates = [date]

        # Get upcoming earnings from database (use ET date to avoid UTC mismatch)
        upcoming = repo.get_upcoming_earnings(start_date=today, days=5)
        if date:
            upcoming = [e for e in upcoming if e["report_date"] == date]
        else:
            upcoming = [e for e in upcoming if e["report_date"] in target_dates]

        log("debug", "Fetched upcoming earnings from database", count=len(upcoming), dates=target_dates)

        scan_errors = 0
        # Shared list accumulates results as tasks complete
        # On timeout, this list contains all results completed before timeout
        partial_results = []
        try:
            results, scan_errors = await asyncio.wait_for(
                _scan_tickers_for_whisper(upcoming, repo, tradier, fresh=fresh,
                                         partial_results=partial_results),
                timeout=MAX_SCAN_TIME_SECONDS
            )
        except asyncio.TimeoutError:
            log("warn", "Whisper scan timed out, using partial results",
                timeout_seconds=MAX_SCAN_TIME_SECONDS,
                partial_count=len(partial_results))
            metrics.count("ivcrush.whisper.timeout", {"reason": "scan_timeout"})
            # Use whatever results completed before the timeout
            results = partial_results

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)

        # Get budget info
        budget = get_budget_tracker()

        # If fresh=True, fetch real-time sentiment for top 5 tickers
        # This provides up-to-date direction for Telegram requests
        if fresh and results:
            perplexity = get_perplexity()
            cache = get_sentiment_cache()
            top_n = min(5, len(results))

            for i in range(top_n):
                ticker = results[i]["ticker"]
                earnings_date = results[i]["earnings_date"]

                # Check budget before each call
                if not budget.can_call("perplexity"):
                    log("warn", "Budget exhausted during fresh sentiment fetch",
                        fetched=i, remaining=top_n-i)
                    break

                try:
                    sentiment_data = await perplexity.get_sentiment(ticker, earnings_date)
                    if sentiment_data and not sentiment_data.get("error"):
                        # Cache for future requests
                        cache.save_sentiment(ticker, earnings_date, sentiment_data)

                        # Update direction using fresh sentiment
                        direction = get_direction(
                            skew_bias=None,  # No skew in whisper
                            sentiment_score=sentiment_data.get("score"),
                            sentiment_direction=sentiment_data.get("direction"),
                        )
                        results[i]["direction"] = direction
                        results[i]["sentiment_score"] = sentiment_data.get("score", 0)
                        log("debug", "Fresh sentiment fetched",
                            ticker=ticker, direction=direction,
                            score=sentiment_data.get("score"))
                except Exception as e:
                    log("warn", "Failed to fetch fresh sentiment",
                        ticker=ticker, error=type(e).__name__)

        summary = budget.get_summary("perplexity")

        response = {
            "status": "success",
            "target_dates": target_dates,
            "analyzed": len(upcoming),
            "qualified_count": len(results),
            "error_count": scan_errors,
            "tickers": results[:10],  # Top 10
            "budget": {
                "calls_today": summary["today_calls"],
                "remaining": summary["budget_remaining"],
            },
        }

        # Format for CLI if requested
        if format == "cli":
            ticker_data = [
                {
                    "ticker": t["ticker"],
                    "earnings_date": t.get("earnings_date", ""),
                    "vrp_ratio": t["vrp_ratio"],
                    "score": t["score"],
                    "direction": "NEUTRAL",
                    "tailwinds": "",
                    "headwinds": "",
                    "strategy": f"VRP {t['vrp_tier']}",
                }
                for t in results[:10]
            ]
            cli_output = format_digest_cli(
                target_dates[0],
                ticker_data,
                summary["today_calls"],
                summary["budget_remaining"],
            )
            # Record metrics
            duration_ms = (time.time() - start_time) * 1000
            metrics.request_success("whisper", duration_ms)
            metrics.tickers_qualified(len(results))
            return {"output": cli_output}

        # Record metrics
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_success("whisper", duration_ms)
        metrics.tickers_qualified(len(results))
        metrics.budget_update(
            remaining_calls=40 - summary["today_calls"],
            remaining_dollars=summary["budget_remaining"]
        )

        return response

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("whisper", duration_ms)
        log("error", "Whisper failed", error=type(e).__name__, details=_mask_sensitive(str(e)))
        raise HTTPException(500, "Whisper failed")
