"""
Job handlers for scheduled tasks.

Each job is an async function that performs a specific operation.

Implied Move Calculation:
    All job handlers use real options data from Tradier to calculate implied moves.
    The fetch_real_implied_move() helper fetches ATM straddle prices for accurate
    VRP calculation. Only if Tradier data is unavailable (no price, no expiration,
    empty chain), we fall back to historical_avg * 1.5 as a conservative estimate.

    See src/domain/implied_move.py for the shared helper functions.
"""

import asyncio
import sqlite3
from typing import Dict, Any, List
from datetime import timedelta

from src.core.config import settings, now_et, today_et, MARKET_TZ
from src.core.logging import log
from src.core.budget import BudgetTracker, BudgetExhausted
from src.core.database import DatabaseSync
from src.core import metrics
from src.integrations import (
    AlphaVantageClient,
    TradierClient,
    PerplexityClient,
    TelegramSender,
    YahooFinanceClient,
    TwelveDataClient,
)
from src.domain import (
    calculate_vrp,
    classify_liquidity_tier,
    calculate_score,
    apply_sentiment_modifier,
    HistoricalMovesRepository,
    SentimentCacheRepository,
    generate_strategies,
)
from src.domain.implied_move import (
    fetch_real_implied_move,
    get_implied_move_with_fallback,
    IMPLIED_MOVE_FALLBACK_MULTIPLIER,
)
from src.formatters.telegram import format_digest


# Configuration constants for job limits
# These can be overridden via environment variables if needed
MAX_PRE_MARKET_TICKERS = 30  # Max tickers to evaluate in pre-market prep
MAX_PRIME_CANDIDATES = 40  # Max candidates to consider for priming
MAX_PRIME_CALLS = 15  # Max Perplexity API calls during prime
MAX_DIGEST_CANDIDATES = 40  # Max candidates to consider for digest
MAX_BACKFILL_TICKERS = 60  # Max tickers to backfill in weekly job
MAX_OUTCOME_TICKERS = 30  # Max tickers to record outcomes for same-day earnings
RATE_LIMIT_DELAY = 0.5  # Seconds between API calls
RATE_LIMIT_BATCH_SIZE = 5  # API calls before adding delay


def filter_to_tracked_tickers(
    earnings: List[Dict[str, Any]],
    tracked_tickers: set
) -> List[Dict[str, Any]]:
    """
    Filter earnings list to only include tickers with historical moves data.

    This is the most reliable way to filter out OTC/foreign stocks because:
    1. If a ticker is in historical_moves, we have VRP data for it
    2. These are tickers we've explicitly tracked and can analyze
    3. No false positives (legitimate tickers won't be filtered)
    4. No false negatives (untraceable tickers won't slip through)

    Args:
        earnings: List of earnings dicts from Alpha Vantage (with 'symbol' key)
        tracked_tickers: Set of ticker symbols from historical_moves table

    Returns:
        Filtered list containing only tracked tickers
    """
    return [e for e in earnings if e["symbol"] in tracked_tickers]

# Alert thresholds
PRE_MARKET_ALERT_THRESHOLD = 0.5  # Alert if pre-market move > 50% of historical avg
AFTER_HOURS_ALERT_THRESHOLD = 1.0  # Only track after-hours moves > 1%

# API calls per ticker when fetching real implied move (quote + expirations + chain)
TRADIER_CALLS_PER_TICKER = 3


def _parse_price_history(closes: dict) -> List[tuple]:
    """
    Parse timestamp->price dict into sorted (date_str, price) list.

    Handles various timestamp formats from Yahoo Finance API:
    - datetime objects with strftime
    - string timestamps
    - other types (converted via str)

    Args:
        closes: Dict mapping timestamps to prices

    Returns:
        List of (date_str, price) tuples sorted by date ascending
    """
    price_data = []
    for timestamp, price in closes.items():
        if price is None:
            continue
        try:
            if hasattr(timestamp, 'strftime'):
                date_str = timestamp.strftime("%Y-%m-%d")
            elif isinstance(timestamp, str):
                date_str = timestamp[:10]
            else:
                date_str = str(timestamp)[:10]
            price_data.append((date_str, float(price)))
        except (ValueError, TypeError):
            continue
    price_data.sort(key=lambda x: x[0])
    return price_data


class JobRunner:
    """Runs scheduled jobs with proper error handling."""

    def __init__(self):
        self._tradier = None
        self._alphavantage = None
        self._perplexity = None
        self._telegram = None
        self._yahoo = None
        self._twelvedata = None

    @property
    def tradier(self) -> TradierClient:
        if self._tradier is None:
            self._tradier = TradierClient(settings.tradier_api_key)  # Use property accessor
        return self._tradier

    @property
    def alphavantage(self) -> AlphaVantageClient:
        if self._alphavantage is None:
            self._alphavantage = AlphaVantageClient(settings.alpha_vantage_key)  # Use property accessor
        return self._alphavantage

    @property
    def perplexity(self) -> PerplexityClient:
        if self._perplexity is None:
            self._perplexity = PerplexityClient(
                api_key=settings.perplexity_api_key,  # Use property accessor
                db_path=settings.DB_PATH,
            )
        return self._perplexity

    @property
    def telegram(self) -> TelegramSender:
        if self._telegram is None:
            self._telegram = TelegramSender(
                bot_token=settings.telegram_bot_token,  # Use property accessor
                chat_id=settings.telegram_chat_id,  # Use property accessor
            )
        return self._telegram

    @property
    def yahoo(self) -> YahooFinanceClient:
        if self._yahoo is None:
            self._yahoo = YahooFinanceClient()
        return self._yahoo

    @property
    def twelvedata(self) -> TwelveDataClient:
        if self._twelvedata is None:
            self._twelvedata = TwelveDataClient(settings.twelve_data_key)
        return self._twelvedata

    async def run(self, job_name: str) -> Dict[str, Any]:
        """
        Run a job by name.

        Args:
            job_name: Name of the job to run

        Returns:
            Result dict with status and details
        """
        handler_map = {
            "pre-market-prep": self._pre_market_prep,
            "sentiment-scan": self._sentiment_scan,
            "morning-digest": self._morning_digest,
            "market-open-refresh": self._market_open_refresh,
            "pre-trade-refresh": self._pre_trade_refresh,
            "after-hours-check": self._after_hours_check,
            "outcome-recorder": self._outcome_recorder,
            "evening-summary": self._evening_summary,
            "weekly-backfill": self._weekly_backfill,
            "weekly-backup": self._weekly_backup,
            "weekly-cleanup": self._weekly_cleanup,
            "calendar-sync": self._calendar_sync,
        }

        handler = handler_map.get(job_name)
        if not handler:
            log("error", "Unknown job", job=job_name)
            return {"status": "error", "error": f"Unknown job: {job_name}"}

        try:
            log("info", "Starting job", job=job_name)
            result = await handler()
            log("info", "Job completed", job=job_name, result=result)
            return result
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log("error", "Job failed", job=job_name, error=str(e), traceback=tb)
            return {"status": "error", "error": str(e)}

    async def _pre_market_prep(self) -> Dict[str, Any]:
        """
        Pre-market prep (05:30 ET).
        Fetch today's earnings and calculate VRP for each.
        """
        start_time = asyncio.get_event_loop().time()
        today = today_et()

        # Get earnings for today and next few days
        earnings = await self.alphavantage.get_earnings_calendar()

        # Validate API response
        if not earnings:
            log("warn", "Empty earnings calendar from Alpha Vantage")
            metrics.count("ivcrush.job.api_empty", {"job": "pre_market_prep", "api": "alphavantage"})
            return {"status": "warning", "tickers_found": 0, "earnings_dates": [], "note": "Empty calendar from API"}

        # Filter to upcoming earnings
        target_dates = [today]
        for i in range(1, 4):
            future = (now_et() + timedelta(days=i)).strftime("%Y-%m-%d")
            target_dates.append(future)

        upcoming = [e for e in earnings if e["report_date"] in target_dates]

        # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
        repo = HistoricalMovesRepository(settings.DB_PATH)
        tracked_tickers = repo.get_tracked_tickers()
        upcoming = filter_to_tracked_tickers(upcoming, tracked_tickers)

        # Log truncation if limit exceeded
        if len(upcoming) > MAX_PRE_MARKET_TICKERS:
            log("info", "Truncating pre-market candidates",
                total=len(upcoming), processing=MAX_PRE_MARKET_TICKERS)

        # Calculate VRP for each
        results = []
        failed_tickers = []
        api_calls = 0

        for e in upcoming[:MAX_PRE_MARKET_TICKERS]:
            ticker = e["symbol"]
            try:
                # Get historical moves
                historical = repo.get_average_move(ticker)
                if historical is None:
                    continue

                # Rate limiting for Tradier API calls
                api_calls += 1
                if api_calls % RATE_LIMIT_BATCH_SIZE == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Get current price from Tradier (more reliable than Yahoo after hours)
                quote = await self.tradier.get_quote(ticker)
                price = quote.get("last") or quote.get("close") or quote.get("prevclose") if quote else None
                if not price:
                    log("debug", "No price data for ticker", ticker=ticker)
                    continue

                results.append({
                    "ticker": ticker,
                    "earnings_date": e["report_date"],
                    "historical_avg": historical,
                    "price": price,
                })
            except Exception as ex:
                failed_tickers.append(ticker)
                log("warn", "Failed to process ticker in pre-market prep",
                    ticker=ticker, error=str(ex), job="pre_market_prep")

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "pre_market_prep"})
        metrics.gauge("ivcrush.job.tickers_processed", len(results), {"job": "pre_market_prep"})

        return {
            "status": "success",
            "tickers_found": len(results),
            "earnings_dates": target_dates,
            "failed_tickers": failed_tickers if failed_tickers else None,
        }

    async def _sentiment_scan(self) -> Dict[str, Any]:
        """
        Sentiment scan / Prime (06:30 ET).
        Pre-cache AI sentiment for high-VRP tickers.

        Uses REAL implied move from Tradier options chains (ATM straddle pricing)
        to calculate accurate VRP ratios for selecting which tickers to prime.
        Falls back to estimate only if options data unavailable.
        """
        start_time = asyncio.get_event_loop().time()
        today = today_et()

        # Get earnings for today and next few days
        earnings = await self.alphavantage.get_earnings_calendar()

        # Validate API response
        if not earnings:
            log("warn", "Empty earnings calendar from Alpha Vantage", job="sentiment_scan")
            metrics.count("ivcrush.job.api_empty", {"job": "sentiment_scan", "api": "alphavantage"})
            return {"status": "warning", "candidates": 0, "primed": 0, "note": "Empty calendar from API"}

        target_dates = [today]
        for i in range(1, 4):
            future = (now_et() + timedelta(days=i)).strftime("%Y-%m-%d")
            target_dates.append(future)

        upcoming = [e for e in earnings if e["report_date"] in target_dates]

        # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
        repo = HistoricalMovesRepository(settings.DB_PATH)
        tracked_tickers = repo.get_tracked_tickers()
        upcoming = filter_to_tracked_tickers(upcoming, tracked_tickers)

        # Log truncation if limit exceeded
        if len(upcoming) > MAX_PRIME_CANDIDATES:
            log("info", "Truncating prime candidates",
                total=len(upcoming), processing=MAX_PRIME_CANDIDATES)

        # Calculate VRP and filter to candidates worth priming
        cache = SentimentCacheRepository(settings.DB_PATH)
        budget = BudgetTracker(db_path=settings.DB_PATH)

        candidates = []
        failed_tickers = []
        api_calls = 0
        real_implied_count = 0

        for e in upcoming[:MAX_PRIME_CANDIDATES]:
            ticker = e["symbol"]
            earnings_date = e["report_date"]

            try:
                # Skip if already cached
                if cache.get_sentiment(ticker, earnings_date):
                    continue

                # Check VRP threshold
                moves = repo.get_moves(ticker)
                if len(moves) < 4:
                    continue

                historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                if not historical_pcts:
                    continue

                historical_avg = sum(historical_pcts) / len(historical_pcts)

                # Rate limiting for Tradier API calls (3 calls per ticker)
                api_calls += TRADIER_CALLS_PER_TICKER
                if api_calls % (RATE_LIMIT_BATCH_SIZE * TRADIER_CALLS_PER_TICKER) == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Fetch real implied move from Tradier options chain
                im_result = await fetch_real_implied_move(
                    self.tradier, ticker, earnings_date
                )
                implied_move_pct, used_real = get_implied_move_with_fallback(
                    im_result, historical_avg
                )
                if used_real:
                    real_implied_count += 1

                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                # Only prime tickers with VRP >= discovery threshold
                if vrp_data.get("vrp_ratio", 0) >= settings.VRP_DISCOVERY:
                    candidates.append({
                        "ticker": ticker,
                        "earnings_date": earnings_date,
                        "vrp_ratio": vrp_data["vrp_ratio"],
                    })
            except Exception as ex:
                failed_tickers.append(ticker)
                log("warn", "Failed to evaluate ticker for prime",
                    ticker=ticker, error=str(ex), job="sentiment_scan")

        log("info", "Sentiment scan VRP analysis complete",
            real_implied_count=real_implied_count, total_evaluated=api_calls)

        # Sort by VRP and prime top candidates
        candidates.sort(key=lambda x: x["vrp_ratio"], reverse=True)

        primed = 0
        prime_failed = []
        budget_skipped = []  # Tickers skipped due to budget exhaustion

        for i, c in enumerate(candidates[:MAX_PRIME_CALLS]):
            if not budget.can_call("perplexity"):
                # Track remaining tickers that won't be primed due to budget
                budget_skipped = [x["ticker"] for x in candidates[i:MAX_PRIME_CALLS]]
                log("warn", "Perplexity budget exhausted during prime",
                    primed_so_far=primed, skipped_tickers=budget_skipped)
                break

            try:
                # Rate limiting between Perplexity API calls
                if i > 0 and i % RATE_LIMIT_BATCH_SIZE == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                sentiment = await self.perplexity.get_sentiment(c["ticker"], c["earnings_date"])
                if sentiment and not sentiment.get("error"):
                    cache.save_sentiment(c["ticker"], c["earnings_date"], sentiment, ttl_hours=12)
                    primed += 1
                    log("info", "Primed sentiment", ticker=c["ticker"], vrp=c["vrp_ratio"])
                else:
                    log("debug", "Empty sentiment response", ticker=c["ticker"])
            except BudgetExhausted as be:
                # Budget exhausted mid-loop - track remaining tickers
                budget_skipped = [x["ticker"] for x in candidates[i:MAX_PRIME_CALLS]]
                log("warn", "Budget exhausted mid-prime",
                    ticker=c["ticker"], skipped_tickers=budget_skipped, reason=str(be))
                metrics.count("ivcrush.budget.exhausted", {"job": "sentiment_scan"})
                break
            except Exception as ex:
                prime_failed.append(c["ticker"])
                log("warn", "Failed to prime ticker",
                    ticker=c["ticker"], error=str(ex), job="sentiment_scan")

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "sentiment_scan"})
        metrics.gauge("ivcrush.job.candidates", len(candidates), {"job": "sentiment_scan"})
        metrics.gauge("ivcrush.job.primed", primed, {"job": "sentiment_scan"})

        result = {
            "status": "success",
            "candidates": len(candidates),
            "primed": primed,
        }
        if failed_tickers or prime_failed:
            result["failed_tickers"] = failed_tickers + prime_failed
        if budget_skipped:
            result["budget_skipped"] = budget_skipped
            metrics.gauge("ivcrush.job.budget_skipped", len(budget_skipped), {"job": "sentiment_scan"})

        return result

    async def _morning_digest(self) -> Dict[str, Any]:
        """
        Morning digest / Whisper (07:30 ET).
        Send summary of top VRP opportunities via Telegram.

        Uses REAL implied move from Tradier options chains (ATM straddle pricing)
        to calculate accurate VRP ratios. Falls back to estimate only if options
        data unavailable.

        Note: GOOD liquidity is assumed during screening. Actual liquidity
        must be verified via Tradier before placing any trades.
        """
        start_time = asyncio.get_event_loop().time()
        today = today_et()

        # Get earnings for today and next few days
        earnings = await self.alphavantage.get_earnings_calendar()

        # Validate API response
        if not earnings:
            log("warn", "Empty earnings calendar from Alpha Vantage", job="morning_digest")
            metrics.count("ivcrush.job.api_empty", {"job": "morning_digest", "api": "alphavantage"})
            # Still send a notification about the empty calendar
            try:
                await self.telegram.send_message(
                    f"📋 <b>Trading Desk Digest: {today}</b>\n\n⚠️ Earnings calendar unavailable."
                )
            except Exception as tg_err:
                log("error", "Failed to send Telegram notification", error=str(tg_err))
            return {"status": "warning", "opportunities": 0, "sent": False, "note": "Empty calendar from API"}

        target_dates = [today]
        for i in range(1, 4):
            future = (now_et() + timedelta(days=i)).strftime("%Y-%m-%d")
            target_dates.append(future)

        upcoming = [e for e in earnings if e["report_date"] in target_dates]

        # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
        repo = HistoricalMovesRepository(settings.DB_PATH)
        tracked_tickers = repo.get_tracked_tickers()
        upcoming = filter_to_tracked_tickers(upcoming, tracked_tickers)

        # Log truncation if limit exceeded
        if len(upcoming) > MAX_DIGEST_CANDIDATES:
            log("info", "Truncating digest candidates",
                total=len(upcoming), processing=MAX_DIGEST_CANDIDATES)

        # Build opportunities list with VRP and sentiment
        cache = SentimentCacheRepository(settings.DB_PATH)
        budget = BudgetTracker(db_path=settings.DB_PATH)

        opportunities: List[Dict[str, Any]] = []
        failed_tickers = []
        api_calls = 0  # Track API calls for rate limiting
        real_implied_count = 0  # Track how many tickers got real options data

        for e in upcoming[:MAX_DIGEST_CANDIDATES]:
            ticker = e["symbol"]
            earnings_date = e["report_date"]

            try:
                # Get historical data for VRP
                moves = repo.get_moves(ticker)
                if len(moves) < 4:
                    continue

                historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                if not historical_pcts:
                    continue

                historical_avg = sum(historical_pcts) / len(historical_pcts)

                # Rate limiting for Tradier API calls (3 calls per ticker)
                api_calls += TRADIER_CALLS_PER_TICKER
                if api_calls % (RATE_LIMIT_BATCH_SIZE * TRADIER_CALLS_PER_TICKER) == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Fetch real implied move from Tradier options chain
                im_result = await fetch_real_implied_move(
                    self.tradier, ticker, earnings_date
                )
                implied_move_pct, used_real = get_implied_move_with_fallback(
                    im_result, historical_avg
                )
                if used_real:
                    real_implied_count += 1

                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                # Apply VRP discovery threshold
                if vrp_data.get("vrp_ratio", 0) < settings.VRP_DISCOVERY:
                    continue

                # Calculate score (assume GOOD liquidity for screening - see docstring)
                score_data = calculate_score(
                    vrp_ratio=vrp_data["vrp_ratio"],
                    vrp_tier=vrp_data["tier"],
                    implied_move_pct=implied_move_pct,
                    liquidity_tier="GOOD",
                )

                # Get cached sentiment if available
                sentiment = cache.get_sentiment(ticker, earnings_date)
                direction = "NEUTRAL"
                tailwinds = ""
                headwinds = ""
                final_score = score_data["total_score"]

                if sentiment:
                    direction = sentiment.get("direction", "neutral").upper()
                    tailwinds = sentiment.get("tailwinds", "")
                    headwinds = sentiment.get("headwinds", "")
                    sentiment_score = sentiment.get("score", 0)
                    final_score = apply_sentiment_modifier(score_data["total_score"], sentiment_score)

                # Generate actual trading strategies
                price = im_result.get("price")
                expiration = im_result.get("expiration", "")
                strategy_name = f"VRP {vrp_data['tier']}"  # Fallback
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

                opportunities.append({
                    "ticker": ticker,
                    "vrp_ratio": vrp_data["vrp_ratio"],
                    "score": final_score,
                    "direction": direction,
                    "tailwinds": tailwinds,
                    "headwinds": headwinds,
                    "strategy": strategy_name,
                    "credit": credit,
                    "real_data": used_real,  # Track if we used real options data
                })

            except Exception as ex:
                failed_tickers.append(ticker)
                log("warn", "Failed to evaluate ticker for digest",
                    ticker=ticker, error=str(ex), job="morning_digest")

        log("info", "Digest analysis complete",
            real_implied_count=real_implied_count, total_candidates=len(upcoming[:MAX_DIGEST_CANDIDATES]))

        # Sort by score descending
        opportunities.sort(key=lambda x: x["score"], reverse=True)

        # Get budget info
        budget_summary = budget.get_summary("perplexity")

        # Format and send digest (only if there are opportunities)
        log("info", "Sending digest", opportunities=len(opportunities))

        sent = False
        telegram_error = None
        try:
            if opportunities:
                digest_msg = format_digest(
                    target_dates[0],
                    opportunities[:10],  # Top 10
                    budget_summary["today_calls"],
                    budget_summary["budget_remaining"],
                )
                sent = await self.telegram.send_message(digest_msg)
            else:
                # Skip sending when no opportunities - don't spam with empty alerts
                log("info", "Skipping digest - no opportunities", job="morning_digest")
        except Exception as tg_err:
            telegram_error = str(tg_err)
            log("error", "Failed to send Telegram digest",
                error=telegram_error, opportunities=len(opportunities), job="morning_digest")

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "morning_digest"})
        metrics.tickers_qualified(len(opportunities))

        result = {
            "status": "success",
            "opportunities": len(opportunities),
            "sent": sent,
        }
        if telegram_error:
            result["telegram_error"] = telegram_error
        if failed_tickers:
            result["failed_tickers"] = failed_tickers
            metrics.gauge("ivcrush.job.tickers_failed", len(failed_tickers), {"job": "morning_digest"})

        return result

    async def _market_open_refresh(self) -> Dict[str, Any]:
        """
        Market open refresh (10:00 ET).
        Refresh prices for today's earnings tickers after market opens.
        Sends alert if any high-VRP ticker has significant pre-market movement.
        """
        start_time = asyncio.get_event_loop().time()
        today = today_et()

        # Get earnings for today
        earnings = await self.alphavantage.get_earnings_calendar()

        if not earnings:
            log("warn", "Empty earnings calendar", job="market_open_refresh")
            metrics.count("ivcrush.job.api_empty", {"job": "market_open_refresh", "api": "alphavantage"})
            return {"status": "warning", "refreshed": 0, "note": "Empty calendar from API"}

        # Filter to today's earnings only
        todays_earnings = [e for e in earnings if e["report_date"] == today]

        # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
        repo = HistoricalMovesRepository(settings.DB_PATH)
        tracked_tickers = repo.get_tracked_tickers()
        todays_earnings = filter_to_tracked_tickers(todays_earnings, tracked_tickers)

        if not todays_earnings:
            log("info", "No earnings today", job="market_open_refresh")
            return {"status": "success", "refreshed": 0, "note": "No earnings today"}

        # Refresh prices and check for significant moves
        refreshed = 0
        significant_moves = []
        failed_tickers = []
        api_calls = 0

        for e in todays_earnings[:MAX_PRE_MARKET_TICKERS]:
            ticker = e["symbol"]
            try:
                # Rate limiting
                api_calls += 1
                if api_calls % RATE_LIMIT_BATCH_SIZE == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Get current price from Tradier (more reliable than Yahoo)
                quote = await self.tradier.get_quote(ticker)
                price = quote.get("last") or quote.get("close") or quote.get("prevclose") if quote else None
                if not price:
                    log("debug", "No current price for market refresh", ticker=ticker)
                    continue

                refreshed += 1

                # Check historical average to detect significant pre-market moves
                moves = repo.get_moves(ticker)
                if len(moves) >= 4:
                    historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                    if historical_pcts:
                        historical_avg = sum(historical_pcts) / len(historical_pcts)
                        # If we have a previous close, check pre-market move
                        history = await self.twelvedata.get_stock_history(ticker, period="5d", interval="1d")
                        if history and "Close" in history:
                            closes = list(history["Close"].values())
                            if len(closes) >= 2 and closes[-2]:
                                prev_close = closes[-2]
                                pre_market_move = abs((price - prev_close) / prev_close * 100)
                                # Alert if pre-market move exceeds threshold of historical avg
                                if pre_market_move > historical_avg * PRE_MARKET_ALERT_THRESHOLD:
                                    significant_moves.append({
                                        "ticker": ticker,
                                        "pre_market_move": round(pre_market_move, 2),
                                        "historical_avg": round(historical_avg, 2),
                                        "current_price": round(price, 2),
                                    })

            except Exception as ex:
                failed_tickers.append(ticker)
                log("warn", "Failed to refresh ticker",
                    ticker=ticker, error=str(ex), job="market_open_refresh")

        # Send alert if significant pre-market moves detected
        telegram_error = None
        if significant_moves:
            try:
                msg_lines = [f"⚡ <b>Pre-Market Alert ({today})</b>\n"]
                for move in significant_moves[:5]:
                    msg_lines.append(
                        f"• <b>{move['ticker']}</b>: {move['pre_market_move']}% move "
                        f"(avg: {move['historical_avg']}%)"
                    )
                await self.telegram.send_message("\n".join(msg_lines))
            except Exception as tg_err:
                telegram_error = str(tg_err)
                log("error", "Failed to send pre-market alert", error=telegram_error)

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "market_open_refresh"})
        metrics.gauge("ivcrush.job.tickers_refreshed", refreshed, {"job": "market_open_refresh"})

        result = {
            "status": "success",
            "refreshed": refreshed,
            "significant_moves": len(significant_moves),
        }
        if telegram_error:
            result["telegram_error"] = telegram_error
        if failed_tickers:
            result["failed_tickers"] = failed_tickers
            metrics.gauge("ivcrush.job.tickers_failed", len(failed_tickers), {"job": "market_open_refresh"})
        return result

    async def _pre_trade_refresh(self) -> Dict[str, Any]:
        """
        Pre-trade refresh (14:30 ET).
        Final refresh before typical 2:30-3:30 PM trade window.
        Re-validates VRP with current IV and sends actionable alert.
        """
        start_time = asyncio.get_event_loop().time()
        today = today_et()

        # Get earnings for today (AMC - after market close)
        earnings = await self.alphavantage.get_earnings_calendar()

        if not earnings:
            log("warn", "Empty earnings calendar", job="pre_trade_refresh")
            metrics.count("ivcrush.job.api_empty", {"job": "pre_trade_refresh", "api": "alphavantage"})
            return {"status": "warning", "candidates": 0, "note": "Empty calendar from API"}

        # Filter to today's AMC earnings (tradeable now)
        todays_earnings = [e for e in earnings if e["report_date"] == today]

        # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
        repo = HistoricalMovesRepository(settings.DB_PATH)
        tracked_tickers = repo.get_tracked_tickers()
        todays_earnings = filter_to_tracked_tickers(todays_earnings, tracked_tickers)

        if not todays_earnings:
            log("info", "No earnings today", job="pre_trade_refresh")
            return {"status": "success", "candidates": 0, "note": "No earnings today"}

        # Re-evaluate VRP for today's tickers with current prices
        cache = SentimentCacheRepository(settings.DB_PATH)
        candidates = []
        failed_tickers = []
        api_calls = 0

        for e in todays_earnings[:MAX_PRE_MARKET_TICKERS]:
            ticker = e["symbol"]
            try:
                # Rate limiting
                api_calls += 1
                if api_calls % RATE_LIMIT_BATCH_SIZE == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Get historical moves for VRP calc
                moves = repo.get_moves(ticker)
                if len(moves) < 4:
                    continue

                historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                if not historical_pcts:
                    continue

                historical_avg = sum(historical_pcts) / len(historical_pcts)

                # Fetch real implied move from Tradier options chain
                im_result = await fetch_real_implied_move(
                    self.tradier, ticker, today
                )
                implied_move_pct, _ = get_implied_move_with_fallback(
                    im_result, historical_avg
                )

                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                if vrp_data.get("vrp_ratio", 0) < settings.VRP_DISCOVERY:
                    continue

                # Get current price for context (use price from implied move result)
                price = im_result.get("price")

                # Get cached sentiment
                sentiment = cache.get_sentiment(ticker, today)
                direction = sentiment.get("direction", "neutral").upper() if sentiment else "NEUTRAL"

                candidates.append({
                    "ticker": ticker,
                    "vrp_ratio": round(vrp_data["vrp_ratio"], 2),
                    "tier": vrp_data["tier"],
                    "direction": direction,
                    "price": round(price, 2) if price else None,
                    "implied_move": round(implied_move_pct, 2),
                })

            except Exception as ex:
                failed_tickers.append(ticker)
                log("warn", "Failed to evaluate ticker",
                    ticker=ticker, error=str(ex), job="pre_trade_refresh")

        # Sort by VRP ratio
        candidates.sort(key=lambda x: x["vrp_ratio"], reverse=True)

        # Send actionable alert
        telegram_error = None
        if candidates:
            try:
                msg_lines = [f"🎯 <b>Pre-Trade Alert ({today} 2:30 PM)</b>\n"]
                msg_lines.append("Top opportunities for AMC earnings:\n")
                for c in candidates[:5]:
                    emoji = "🟢" if c["direction"] == "BULLISH" else "🔴" if c["direction"] == "BEARISH" else "⚪"
                    msg_lines.append(
                        f"{emoji} <b>{c['ticker']}</b>: VRP {c['vrp_ratio']}x ({c['tier']}) "
                        f"| ±{c['implied_move']}% | ${c['price'] or 'N/A'}"
                    )
                msg_lines.append("\n⚠️ Verify liquidity before trading")
                await self.telegram.send_message("\n".join(msg_lines))
            except Exception as tg_err:
                telegram_error = str(tg_err)
                log("error", "Failed to send pre-trade alert", error=telegram_error)

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "pre_trade_refresh"})
        metrics.gauge("ivcrush.job.candidates", len(candidates), {"job": "pre_trade_refresh"})

        result = {
            "status": "success",
            "candidates": len(candidates),
            "top_tickers": [c["ticker"] for c in candidates[:5]],
        }
        if telegram_error:
            result["telegram_error"] = telegram_error
        if failed_tickers:
            result["failed_tickers"] = failed_tickers
            metrics.gauge("ivcrush.job.tickers_failed", len(failed_tickers), {"job": "pre_trade_refresh"})
        return result

    async def _after_hours_check(self) -> Dict[str, Any]:
        """
        After hours check (16:30 ET).
        Check for after-market-close earnings announcements.
        Alerts about earnings that just reported and their initial moves.
        """
        start_time = asyncio.get_event_loop().time()
        today = today_et()

        # Get earnings for today
        earnings = await self.alphavantage.get_earnings_calendar()

        if not earnings:
            log("warn", "Empty earnings calendar", job="after_hours_check")
            metrics.count("ivcrush.job.api_empty", {"job": "after_hours_check", "api": "alphavantage"})
            return {"status": "warning", "checked": 0, "note": "Empty calendar from API"}

        # Filter to today's earnings
        todays_earnings = [e for e in earnings if e["report_date"] == today]

        # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
        repo = HistoricalMovesRepository(settings.DB_PATH)
        tracked_tickers = repo.get_tracked_tickers()
        todays_earnings = filter_to_tracked_tickers(todays_earnings, tracked_tickers)

        if not todays_earnings:
            log("info", "No earnings today", job="after_hours_check")
            return {"status": "success", "checked": 0, "note": "No earnings today"}

        # Check after-hours prices for earnings that just reported
        checked = 0
        reported = []
        failed_tickers = []
        api_calls = 0

        for e in todays_earnings[:MAX_PRE_MARKET_TICKERS]:
            ticker = e["symbol"]
            try:
                # Rate limiting
                api_calls += 1
                if api_calls % RATE_LIMIT_BATCH_SIZE == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Get current after-hours quote from Tradier (more reliable than Yahoo)
                quote = await self.tradier.get_quote(ticker)
                price = quote.get("last") or quote.get("close") or quote.get("prevclose") if quote else None
                if not price:
                    log("debug", "No after-hours price available", ticker=ticker)
                    continue

                checked += 1

                # Get today's close for comparison
                history = await self.twelvedata.get_stock_history(ticker, period="5d", interval="1d")
                if not history or "Close" not in history:
                    log("debug", "No history data for after-hours check", ticker=ticker)
                    continue

                # Parse price history with date verification
                price_data = _parse_price_history(history["Close"])
                if not price_data:
                    log("debug", "No valid price data", ticker=ticker)
                    continue

                # Find today's regular session close explicitly
                regular_close = None
                for date_str, close_price in price_data:
                    if date_str == today:
                        regular_close = close_price
                        break

                # Fallback to last available if today not found (with warning)
                if regular_close is None:
                    if price_data:
                        log("warn", "Today's close not found, using last available",
                            ticker=ticker, last_date=price_data[-1][0], today=today)
                        regular_close = price_data[-1][1]
                    else:
                        continue

                if not regular_close:
                    continue

                # Calculate after-hours move
                ah_move_pct = ((price - regular_close) / regular_close) * 100

                # Get historical avg for context
                moves = repo.get_moves(ticker)
                historical_avg = None
                if len(moves) >= 4:
                    historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                    if historical_pcts:
                        historical_avg = sum(historical_pcts) / len(historical_pcts)

                # Only track if move exceeds threshold
                if abs(ah_move_pct) > AFTER_HOURS_ALERT_THRESHOLD:
                    reported.append({
                        "ticker": ticker,
                        "ah_move": round(ah_move_pct, 2),
                        "regular_close": round(regular_close, 2),
                        "ah_price": round(price, 2),
                        "historical_avg": round(historical_avg, 2) if historical_avg else None,
                        "beat_expected": abs(ah_move_pct) < historical_avg if historical_avg else None,
                    })

            except Exception as ex:
                failed_tickers.append(ticker)
                log("warn", "Failed to check ticker",
                    ticker=ticker, error=str(ex), job="after_hours_check")

        # Sort by absolute move size
        reported.sort(key=lambda x: abs(x["ah_move"]), reverse=True)

        # Send after-hours alert
        telegram_error = None
        if reported:
            try:
                msg_lines = [f"📊 <b>After-Hours Earnings ({today})</b>\n"]
                for r in reported[:10]:
                    direction = "📈" if r["ah_move"] > 0 else "📉"
                    move_str = f"+{r['ah_move']}%" if r["ah_move"] > 0 else f"{r['ah_move']}%"
                    context = ""
                    if r["historical_avg"]:
                        if r["beat_expected"]:
                            context = f" (within ±{r['historical_avg']}% avg)"
                        else:
                            context = f" (exceeded ±{r['historical_avg']}% avg)"
                    msg_lines.append(f"{direction} <b>{r['ticker']}</b>: {move_str}{context}")
                await self.telegram.send_message("\n".join(msg_lines))
            except Exception as tg_err:
                telegram_error = str(tg_err)
                log("error", "Failed to send after-hours alert", error=telegram_error)

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "after_hours_check"})
        metrics.gauge("ivcrush.job.tickers_checked", checked, {"job": "after_hours_check"})
        metrics.gauge("ivcrush.job.earnings_reported", len(reported), {"job": "after_hours_check"})

        result = {
            "status": "success",
            "checked": checked,
            "reported": len(reported),
            "moves": [{k: v for k, v in r.items() if k in ["ticker", "ah_move"]} for r in reported[:5]],
        }
        if telegram_error:
            result["telegram_error"] = telegram_error
        if failed_tickers:
            result["failed_tickers"] = failed_tickers
            metrics.gauge("ivcrush.job.tickers_failed", len(failed_tickers), {"job": "after_hours_check"})
        return result

    async def _outcome_recorder(self) -> Dict[str, Any]:
        """
        Outcome recorder (19:00 ET).
        Record post-earnings moves for today's completed earnings.
        Updates historical_moves table for same-day tracking.
        """
        from datetime import datetime

        start_time = asyncio.get_event_loop().time()
        today = today_et()
        repo = HistoricalMovesRepository(settings.DB_PATH)

        # Get earnings for today
        earnings = await self.alphavantage.get_earnings_calendar()

        if not earnings:
            log("warn", "Empty earnings calendar", job="outcome_recorder")
            metrics.count("ivcrush.job.api_empty", {"job": "outcome_recorder", "api": "alphavantage"})
            return {"status": "warning", "recorded": 0, "note": "Empty calendar from API"}

        # Filter to today's earnings
        todays_earnings = [e for e in earnings if e["report_date"] == today]

        # NOTE: Unlike alert jobs, outcome-recorder does NOT filter to whitelist.
        # This allows new tickers to build history and eventually be analyzed.
        # Only filter out obviously invalid tickers (preferred stocks, warrants, etc.)
        from src.domain.repositories import is_valid_ticker
        todays_earnings = [e for e in todays_earnings if is_valid_ticker(e["symbol"])]

        if not todays_earnings:
            log("info", "No earnings today to record", job="outcome_recorder")
            return {"status": "success", "recorded": 0, "note": "No earnings today"}

        recorded = 0
        skipped_duplicate = 0
        failed_tickers = []
        api_calls = 0

        for e in todays_earnings[:MAX_OUTCOME_TICKERS]:
            ticker = e["symbol"]
            try:
                # Check if we already have this record
                existing = repo.get_moves(ticker)
                if any(m.get("earnings_date") == today for m in existing):
                    skipped_duplicate += 1
                    continue

                # Rate limiting
                api_calls += 1
                if api_calls % RATE_LIMIT_BATCH_SIZE == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Get historical prices
                history = await self.twelvedata.get_stock_history(ticker, period="5d", interval="1d")

                if not history or "Close" not in history:
                    log("debug", "No history data for outcome recording", ticker=ticker)
                    continue

                # Parse price history using helper function
                price_data = _parse_price_history(history.get("Close", {}))
                if len(price_data) < 2:
                    log("debug", "Insufficient price data", ticker=ticker, data_points=len(price_data))
                    continue

                # Find today's close and previous close
                today_close = None
                prev_close = None

                for i, (date_str, price) in enumerate(price_data):
                    if date_str == today:
                        today_close = price
                        if i > 0:
                            prev_close = price_data[i - 1][1]
                        break

                # If today not found, use last two prices (with warning)
                if today_close is None and len(price_data) >= 2:
                    log("warn", "Today's close not found for outcome, using last available",
                        ticker=ticker, last_date=price_data[-1][0], today=today)
                    today_close = price_data[-1][1]
                    prev_close = price_data[-2][1]

                if prev_close and today_close and prev_close > 0:
                    move_pct = ((today_close - prev_close) / prev_close) * 100

                    move_record = {
                        "ticker": ticker,
                        "earnings_date": today,
                        "gap_move_pct": round(move_pct, 4),
                        "intraday_move_pct": round(move_pct, 4),
                        "prev_close": round(prev_close, 2),
                        "earnings_close": round(today_close, 2),
                    }

                    repo.save_move(move_record)
                    recorded += 1
                    log("debug", "Recorded outcome", ticker=ticker, date=today,
                        move=round(move_pct, 2))

            except Exception as ex:
                failed_tickers.append(ticker)
                log("warn", "Failed to record outcome",
                    ticker=ticker, error=str(ex), job="outcome_recorder")

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "outcome_recorder"})
        metrics.gauge("ivcrush.job.outcomes_recorded", recorded, {"job": "outcome_recorder"})

        log("info", "Outcome recording complete",
            recorded=recorded, skipped=skipped_duplicate, errors=len(failed_tickers))

        result = {
            "status": "success",
            "recorded": recorded,
            "skipped_duplicate": skipped_duplicate,
            "errors": len(failed_tickers),
        }
        if failed_tickers:
            result["failed_tickers"] = failed_tickers
            metrics.gauge("ivcrush.job.tickers_failed", len(failed_tickers), {"job": "outcome_recorder"})
        return result

    async def _evening_summary(self) -> Dict[str, Any]:
        """
        Evening summary (20:00 ET).
        Send end-of-day summary only if there were earnings today.
        """
        start_time = asyncio.get_event_loop().time()
        today = today_et()

        # Check if there were any earnings today worth summarizing
        earnings = await self.alphavantage.get_earnings_calendar()
        todays_earnings = [e for e in (earnings or []) if e["report_date"] == today]

        # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
        repo = HistoricalMovesRepository(settings.DB_PATH)
        tracked_tickers = repo.get_tracked_tickers()
        todays_earnings = filter_to_tracked_tickers(todays_earnings, tracked_tickers)

        sent = False
        telegram_error = None

        if not todays_earnings:
            # No earnings today - skip sending empty summary
            log("info", "Skipping evening summary - no earnings today", job="evening_summary")
        else:
            try:
                # Get outcome stats from today's recordings
                recorded_moves = []
                for e in todays_earnings[:10]:
                    moves = repo.get_moves(e["symbol"])
                    today_move = next((m for m in moves if m.get("earnings_date") == today), None)
                    if today_move:
                        recorded_moves.append({
                            "ticker": e["symbol"],
                            "move": today_move.get("intraday_move_pct", 0),
                        })

                if recorded_moves:
                    msg_lines = [f"📊 <b>Trading Desk Summary: {today}</b>\n"]
                    msg_lines.append(f"Tracked {len(todays_earnings)} earnings today:\n")
                    for m in sorted(recorded_moves, key=lambda x: abs(x["move"]), reverse=True)[:5]:
                        direction = "📈" if m["move"] > 0 else "📉"
                        move_str = f"+{m['move']:.1f}%" if m["move"] > 0 else f"{m['move']:.1f}%"
                        msg_lines.append(f"{direction} <b>{m['ticker']}</b>: {move_str}")
                    sent = await self.telegram.send_message("\n".join(msg_lines))
                else:
                    # Had earnings but no recorded outcomes yet - skip
                    log("info", "Skipping evening summary - no outcomes recorded yet", job="evening_summary")
            except Exception as tg_err:
                telegram_error = str(tg_err)
                log("error", "Failed to send evening summary",
                    error=telegram_error, job="evening_summary")

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "evening_summary"})

        result = {"status": "success", "sent": sent, "earnings_today": len(todays_earnings)}
        if telegram_error:
            result["telegram_error"] = telegram_error
        return result

    async def _weekly_backfill(self) -> Dict[str, Any]:
        """
        Weekly backfill (Saturday 04:00 ET).
        Record actual moves for recent completed earnings.
        """
        from datetime import datetime

        start_time = asyncio.get_event_loop().time()
        repo = HistoricalMovesRepository(settings.DB_PATH)
        backfilled = 0
        skipped_duplicate = 0
        failed_tickers = []

        # Get earnings from past 7 days
        earnings = await self.alphavantage.get_earnings_calendar()

        # Validate API response
        if not earnings:
            log("warn", "Empty earnings calendar from Alpha Vantage", job="weekly_backfill")
            metrics.count("ivcrush.job.api_empty", {"job": "weekly_backfill", "api": "alphavantage"})
            return {"status": "warning", "backfilled": 0, "note": "Empty calendar from API"}

        today = now_et()

        # Filter to valid tickers (excludes preferred stocks, warrants, etc.)
        # NOTE: Like outcome-recorder, backfill does NOT use whitelist to allow new tickers to build history
        from src.domain.repositories import is_valid_ticker

        past_earnings = []
        for e in earnings:
            try:
                if not is_valid_ticker(e["symbol"]):
                    continue
                earnings_date_str = e["report_date"]
                # Parse earnings date and make it timezone-aware for proper comparison
                earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d")
                earnings_date = MARKET_TZ.localize(earnings_date)
                days_ago = (today - earnings_date).days
                if 1 <= days_ago <= 7:
                    past_earnings.append(e)
            except Exception:
                continue

        # Log truncation if limit exceeded
        if len(past_earnings) > MAX_BACKFILL_TICKERS:
            log("info", "Truncating backfill candidates",
                total=len(past_earnings), processing=MAX_BACKFILL_TICKERS)

        log("info", "Found past earnings to backfill", count=len(past_earnings))

        api_calls = 0
        for e in past_earnings[:MAX_BACKFILL_TICKERS]:
            ticker = e["symbol"]
            earnings_date = e["report_date"]

            try:
                # FIXED: Check ALL existing moves for this ticker, not just the most recent
                existing = repo.get_moves(ticker)
                if any(m.get("earnings_date") == earnings_date for m in existing):
                    skipped_duplicate += 1
                    continue

                # Rate limiting for Yahoo API calls
                api_calls += 1
                if api_calls % RATE_LIMIT_BATCH_SIZE == 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Get historical prices around earnings
                history = await self.twelvedata.get_stock_history(ticker, period="1mo", interval="1d")

                # Validate API response
                if not history:
                    log("debug", "Empty history response for backfill", ticker=ticker)
                    continue
                if "Close" not in history:
                    log("debug", "No Close data in history for backfill", ticker=ticker)
                    continue

                # Parse price history using helper function
                price_data = _parse_price_history(history.get("Close", {}))
                if not price_data:
                    log("debug", "No valid price data after parsing", ticker=ticker)
                    continue

                # Find price before and after earnings with more robust matching
                prev_close = None
                earnings_close = None
                earnings_idx = None

                # First pass: look for exact earnings date match
                for i, (date_str, price) in enumerate(price_data):
                    if date_str == earnings_date:
                        earnings_idx = i
                        earnings_close = price
                        break

                # Second pass: if no exact match, find first trading day after earnings
                if earnings_idx is None:
                    for i, (date_str, price) in enumerate(price_data):
                        if date_str > earnings_date:
                            earnings_idx = i
                            earnings_close = price
                            break

                # Get previous close
                if earnings_idx is not None and earnings_idx > 0:
                    prev_close = price_data[earnings_idx - 1][1]

                if prev_close and earnings_close and prev_close > 0:
                    gap_move_pct = ((earnings_close - prev_close) / prev_close) * 100
                    # For simplicity, use gap move as intraday move (could enhance with intraday data)
                    intraday_move_pct = gap_move_pct

                    move_record = {
                        "ticker": ticker,
                        "earnings_date": earnings_date,
                        "gap_move_pct": round(gap_move_pct, 4),
                        "intraday_move_pct": round(intraday_move_pct, 4),
                        "prev_close": round(prev_close, 2),
                        "earnings_close": round(earnings_close, 2),
                    }

                    repo.save_move(move_record)
                    backfilled += 1
                    log("debug", "Backfilled move", ticker=ticker, date=earnings_date,
                        move=round(gap_move_pct, 2))
                else:
                    log("debug", "Insufficient price data for backfill",
                        ticker=ticker, prev_close=prev_close, earnings_close=earnings_close)

            except Exception as ex:
                failed_tickers.append(ticker)
                log("warn", "Failed to backfill ticker",
                    ticker=ticker, earnings_date=earnings_date, error=str(ex), job="weekly_backfill")

        # Record metrics
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        metrics.record("ivcrush.job.duration", duration_ms, {"job": "weekly_backfill"})
        metrics.gauge("ivcrush.job.backfilled", backfilled, {"job": "weekly_backfill"})
        metrics.gauge("ivcrush.job.errors", len(failed_tickers), {"job": "weekly_backfill"})

        log("info", "Weekly backfill complete",
            backfilled=backfilled, skipped_duplicate=skipped_duplicate, errors=len(failed_tickers))

        result = {
            "status": "success",
            "backfilled": backfilled,
            "skipped_duplicate": skipped_duplicate,
            "errors": len(failed_tickers),
        }
        if failed_tickers:
            result["failed_tickers"] = failed_tickers
            metrics.gauge("ivcrush.job.tickers_failed", len(failed_tickers), {"job": "weekly_backfill"})
        return result

    async def _weekly_backup(self) -> Dict[str, Any]:
        """
        Weekly backup (Sunday 03:00 ET).
        Backup database to GCS after integrity check.
        """
        start_time = asyncio.get_event_loop().time()

        try:
            import shutil
            from pathlib import Path

            db_path = Path(settings.DB_PATH)
            if not db_path.exists():
                log("warn", "No database file to backup", path=str(db_path))
                metrics.count("ivcrush.job.backup_skipped", {"reason": "no_file"})
                return {"status": "success", "backed_up": False, "reason": "No database file"}

            # Run database integrity check before backup
            integrity_ok = False
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.execute("PRAGMA integrity_check")
                result = cursor.fetchone()
                conn.close()

                integrity_ok = result and result[0] == "ok"
                if not integrity_ok:
                    log("error", "Database integrity check failed",
                        result=result[0] if result else "no result", job="weekly_backup")
                    metrics.count("ivcrush.job.integrity_failed", {"job": "weekly_backup"})
                    return {
                        "status": "error",
                        "error": f"Database integrity check failed: {result[0] if result else 'no result'}",
                    }
            except sqlite3.Error as db_err:
                log("error", "Failed to run integrity check",
                    error=str(db_err), job="weekly_backup")
                return {"status": "error", "error": f"Integrity check error: {str(db_err)}"}

            # Create timestamped backup filename
            timestamp = now_et().strftime("%Y%m%d_%H%M%S")
            backup_blob_name = f"backups/ivcrush_{timestamp}.db"

            # Use DatabaseSync to upload
            sync = DatabaseSync(
                bucket_name=settings.gcs_bucket,
                blob_name=backup_blob_name,
            )

            # Copy current database to sync location
            shutil.copy(str(db_path), str(sync.local_path))

            # Upload to GCS
            success = sync.upload()

            # Record metrics
            duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
            metrics.record("ivcrush.job.duration", duration_ms, {"job": "weekly_backup"})

            if success:
                log("info", "Weekly backup complete", blob=backup_blob_name)
                metrics.count("ivcrush.job.backup_success")
                return {"status": "success", "backed_up": True, "blob": backup_blob_name}
            else:
                log("error", "Weekly backup failed - upload conflict", job="weekly_backup")
                metrics.count("ivcrush.job.backup_failed", {"reason": "upload_conflict"})
                return {"status": "error", "error": "Upload conflict"}

        except Exception as e:
            log("error", "Weekly backup failed", error=str(e), job="weekly_backup")
            metrics.count("ivcrush.job.backup_failed", {"reason": "exception"})
            return {"status": "error", "error": str(e)}

    async def _weekly_cleanup(self) -> Dict[str, Any]:
        """
        Weekly cleanup (Sunday 03:30 ET).
        Clean expired cache entries.
        """
        start_time = asyncio.get_event_loop().time()

        try:
            cache = SentimentCacheRepository(settings.DB_PATH)
            cleared = cache.clear_expired()

            # Record metrics
            duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
            metrics.record("ivcrush.job.duration", duration_ms, {"job": "weekly_cleanup"})
            metrics.gauge("ivcrush.job.cache_cleared", cleared, {"job": "weekly_cleanup"})

            log("info", "Weekly cleanup complete", cleared=cleared)
            return {"status": "success", "cleared": cleared}
        except Exception as e:
            log("error", "Weekly cleanup failed", error=str(e), job="weekly_cleanup")
            return {"status": "error", "error": str(e)}

    async def _calendar_sync(self) -> Dict[str, Any]:
        """
        Calendar sync (Sunday 04:00 ET).
        Sync earnings calendar from Alpha Vantage.
        """
        start_time = asyncio.get_event_loop().time()

        try:
            earnings = await self.alphavantage.get_earnings_calendar(horizon="3month")

            # Validate API response
            if not earnings:
                log("warn", "Empty earnings calendar from Alpha Vantage", job="calendar_sync")
                metrics.count("ivcrush.job.api_empty", {"job": "calendar_sync", "api": "alphavantage"})
                return {"status": "warning", "synced": 0, "note": "Empty calendar from API"}

            # Actually store the earnings to the database
            repo = HistoricalMovesRepository(settings.DB_PATH)
            upserted = repo.upsert_earnings_calendar(earnings)

            # Record metrics
            duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
            metrics.record("ivcrush.job.duration", duration_ms, {"job": "calendar_sync"})
            metrics.gauge("ivcrush.job.earnings_synced", upserted, {"job": "calendar_sync"})

            log("info", "Calendar sync complete", fetched=len(earnings), upserted=upserted)
            return {"status": "success", "fetched": len(earnings), "synced": upserted}
        except Exception as e:
            log("error", "Calendar sync failed", error=str(e), job="calendar_sync")
            return {"status": "error", "error": str(e)}
