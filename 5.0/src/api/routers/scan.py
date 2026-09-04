"""Scan endpoint — all earnings on a specific date."""

import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from src.core.config import settings
from src.core.logging import log
from src.core import metrics
from src.domain import (
    calculate_vrp,
    classify_liquidity_tier,
    calculate_score,
)
from src.domain.implied_move import (
    fetch_real_implied_move,
    get_implied_move_with_fallback,
)
from src.api.state import _mask_sensitive
from src.api.dependencies import verify_api_key, get_tradier, get_historical_repo

router = APIRouter(prefix="/api", tags=["analysis"])

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

