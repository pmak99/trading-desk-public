"""Council endpoint — 6-source AI sentiment consensus."""

import time

from fastapi import APIRouter, Depends, HTTPException

from src.core.logging import log
from src.core import metrics
from src.domain import normalize_ticker, InvalidTickerError
from src.formatters.cli import format_council_cli
from src.api.state import _mask_sensitive
from src.api.dependencies import (
    verify_api_key,
    get_tradier,
    get_perplexity,
    get_historical_repo,
    get_sentiment_cache,
    get_finnhub,
)

router = APIRouter(prefix="/api", tags=["analysis"])

@router.get("/council")
async def council(ticker: str, format: str = "json", fresh: bool = False, _: bool = Depends(verify_api_key)):
    """
    6-source AI sentiment council for pre-earnings consensus.

    Aggregates Finnhub analysts, Finnhub news, Perplexity (quick + deep),
    options skew, and historical patterns into a weighted consensus.
    """
    # Validate and normalize ticker
    try:
        ticker = normalize_ticker(ticker)
    except InvalidTickerError as e:
        raise HTTPException(400, str(e))

    log("info", "Council request", ticker=ticker)
    start_time = time.time()

    try:
        from src.domain.council import run_council
        from dataclasses import asdict

        finnhub = get_finnhub()
        perplexity = get_perplexity()
        tradier = get_tradier()
        repo = get_historical_repo()
        cache = get_sentiment_cache()

        result = await run_council(ticker, finnhub, perplexity, tradier, repo, cache)

        duration_ms = (time.time() - start_time) * 1000
        metrics.request_success("council", duration_ms)

        if format == "cli":
            return {"output": format_council_cli(result)}

        # Convert dataclass to dict for JSON serialization
        result_dict = asdict(result)
        return result_dict

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("council", duration_ms)
        log("error", "Council failed", ticker=ticker, error=type(e).__name__, details=_mask_sensitive(str(e)))
        raise HTTPException(500, "Council failed")
