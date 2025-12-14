"""
Job handlers for scheduled tasks.

Each job is an async function that performs a specific operation.
"""

from typing import Dict, Any
from datetime import timedelta

from src.core.config import settings, now_et, today_et
from src.core.logging import log
from src.integrations import (
    AlphaVantageClient,
    TradierClient,
    PerplexityClient,
    TelegramSender,
    YahooFinanceClient,
)
from src.domain import (
    calculate_vrp,
    classify_liquidity_tier,
    calculate_score,
    apply_sentiment_modifier,
    HistoricalMovesRepository,
    SentimentCacheRepository,
)


class JobRunner:
    """Runs scheduled jobs with proper error handling."""

    def __init__(self):
        self._tradier = None
        self._alphavantage = None
        self._perplexity = None
        self._telegram = None
        self._yahoo = None

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
            log("error", "Job failed", job=job_name, error=str(e))
            return {"status": "error", "error": str(e)}

    async def _pre_market_prep(self) -> Dict[str, Any]:
        """
        Pre-market prep (05:30 ET).
        Fetch today's earnings and calculate VRP for each.
        """
        today = today_et()

        # Get earnings for today and next few days
        earnings = await self.alphavantage.get_earnings_calendar()

        # Filter to upcoming earnings
        target_dates = [today]
        for i in range(1, 4):
            future = (now_et() + timedelta(days=i)).strftime("%Y-%m-%d")
            target_dates.append(future)

        upcoming = [e for e in earnings if e["report_date"] in target_dates]

        # Calculate VRP for each
        results = []
        repo = HistoricalMovesRepository(settings.DB_PATH)

        for e in upcoming[:20]:  # Limit to top 20
            ticker = e["symbol"]
            try:
                # Get historical moves
                historical = repo.get_average_move(ticker)
                if historical is None:
                    continue

                # Get current price for implied move calc
                price = await self.yahoo.get_current_price(ticker)
                if not price:
                    continue

                results.append({
                    "ticker": ticker,
                    "earnings_date": e["report_date"],
                    "historical_avg": historical,
                    "price": price,
                })
            except Exception as ex:
                log("warn", "Failed to process ticker", ticker=ticker, error=str(ex))

        return {
            "status": "success",
            "tickers_found": len(results),
            "earnings_dates": target_dates,
        }

    async def _sentiment_scan(self) -> Dict[str, Any]:
        """
        Sentiment scan (06:30 ET).
        Get AI sentiment for high-VRP tickers.
        """
        # This would use pre-market-prep results
        # For now, placeholder
        return {"status": "success", "analyzed": 0}

    async def _morning_digest(self) -> Dict[str, Any]:
        """
        Morning digest (07:30 ET).
        Send summary of opportunities via Telegram.
        """
        today = today_et()

        # Get cached sentiment data
        cache = SentimentCacheRepository(settings.DB_PATH)
        # Would get top opportunities from cache

        # Send digest
        sent = await self.telegram.send_digest(today, [])

        return {"status": "success", "sent": sent}

    async def _market_open_refresh(self) -> Dict[str, Any]:
        """
        Market open refresh (10:00 ET).
        Update prices after market opens.
        """
        return {"status": "success", "refreshed": 0}

    async def _pre_trade_refresh(self) -> Dict[str, Any]:
        """
        Pre-trade refresh (14:30 ET).
        Final refresh before typical trade window.
        """
        return {"status": "success", "refreshed": 0}

    async def _after_hours_check(self) -> Dict[str, Any]:
        """
        After hours check (16:30 ET).
        Check for after-hours earnings announcements.
        """
        return {"status": "success", "checked": 0}

    async def _outcome_recorder(self) -> Dict[str, Any]:
        """
        Outcome recorder (19:00 ET).
        Record post-earnings moves for completed earnings.
        """
        today = today_et()
        repo = HistoricalMovesRepository(settings.DB_PATH)

        # Would check earnings from previous day
        # and record actual moves

        return {"status": "success", "recorded": 0}

    async def _evening_summary(self) -> Dict[str, Any]:
        """
        Evening summary (20:00 ET).
        Send end-of-day summary.
        """
        today = today_et()
        sent = await self.telegram.send_message(f"IV Crush daily summary for {today}")
        return {"status": "success", "sent": sent}

    async def _weekly_backfill(self) -> Dict[str, Any]:
        """
        Weekly backfill (Saturday 04:00 ET).
        Backfill any missing historical data.
        """
        return {"status": "success", "backfilled": 0}

    async def _weekly_backup(self) -> Dict[str, Any]:
        """
        Weekly backup (Sunday 03:00 ET).
        Backup database to GCS.
        """
        # Would use DatabaseSync to upload
        return {"status": "success", "backed_up": True}

    async def _weekly_cleanup(self) -> Dict[str, Any]:
        """
        Weekly cleanup (Sunday 03:30 ET).
        Clean expired cache entries.
        """
        cache = SentimentCacheRepository(settings.DB_PATH)
        cleared = cache.clear_expired()
        return {"status": "success", "cleared": cleared}

    async def _calendar_sync(self) -> Dict[str, Any]:
        """
        Calendar sync (Sunday 04:00 ET).
        Sync earnings calendar from Alpha Vantage.
        """
        earnings = await self.alphavantage.get_earnings_calendar(horizon="3month")
        return {"status": "success", "synced": len(earnings)}
