"""Shared helpers and constants for analysis route modules."""

import asyncio
import time
from typing import Optional, List, Dict, Any, Tuple

from src.core.config import settings
from src.core.logging import log
from src.core import metrics
from src.domain import (
    calculate_vrp,
    calculate_score,
    generate_strategies,
    is_valid_ticker,
)
from src.domain.implied_move import (
    fetch_real_implied_move,
    get_implied_move_with_fallback,
)
from src.domain.direction import get_direction
from src.api.dependencies import get_sentiment_cache, get_vrp_cache

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
    fresh: bool = False,
    timing: str = "",
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

            # Check TRR for high-risk flag
            trr_high = False
            if moves:
                pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                if pcts:
                    avg = sum(pcts) / len(pcts)
                    if avg > 0 and max(pcts) / avg > 2.5:
                        trr_high = True

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
                "timing": timing,
                "trr_high": trr_high,
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
) -> Tuple[List[Dict[str, Any]], int]:
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
                fresh=fresh,
                timing=e.get("timing", ""),
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
