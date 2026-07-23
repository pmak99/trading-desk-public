"""Cross-asset confirmation for TACO entry checks (spec 2026-07-22).

Verifies a VIX/SPX panic is macro-wide by checking flight-to-safety moves
in TLT (rates), UUP (dollar), USO (oil) since event onset. Count of
confirming assets (0-3) feeds the fifth entry-score component; on the put
side the mirrored read is display/log-only. Inspired by Signum Global
Advisors' cross-asset "TACO index" (MarketWatch 2026-07-22), generalized
to market-observable assets only.
"""

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import requests

from .constants import CROSS_ASSET_SYMBOLS, CROSS_ASSET_THRESHOLDS, EventType
from .market_data import Series, fetch_series, up_to

logger = logging.getLogger(__name__)

# CALL side: oil UP confirms supply-shock events, DOWN confirms
# demand-destruction events, either direction for UNKNOWN.
_OIL_UP_EVENTS = {EventType.GEOPOLITICAL}


@dataclass(frozen=True)
class AssetRead:
    symbol: str
    move_pct: float          # % move from event-onset baseline to last close
    threshold: float         # noise threshold (%, always positive)
    confirmed: bool
    source: str              # 'yfinance' | 'twelvedata'


@dataclass(frozen=True)
class CrossAssetRead:
    direction: str           # 'CALL' | 'PUT'
    event_type: EventType
    count: int
    available: int
    reads: List[AssetRead]
    unavailable: List[str]


def _move_pct(series: Series, event_date: date, as_of: date) -> Optional[float]:
    """% move from the last close <= event_date to the last close <= as_of.
    None when either endpoint is missing (asset treated as unavailable)."""
    base = up_to(series, event_date)
    cur = up_to(series, as_of)
    if not base or not cur:
        return None
    return (cur[-1][1] / base[-1][1] - 1.0) * 100.0


def _confirms(symbol: str, move: float, direction: str,
              event_type: EventType) -> bool:
    thr = CROSS_ASSET_THRESHOLDS[symbol]
    eps = 1e-9  # Tolerance for floating-point comparison
    if symbol in ("TLT", "UUP"):
        # flight-to-safety direction; mirrored for puts (euphoria = exits
        # from safety). Threshold hits are inclusive (spec 2026-07-22).
        return (move + eps) >= thr if direction == "CALL" else (move - eps) <= -thr
    # USO. Put side is direction-blind: euphoria events don't map onto the
    # panic EventType taxonomy, so the oil rule is deliberately not mirrored.
    if direction == "PUT" or event_type == EventType.UNKNOWN:
        return abs(move) + eps >= thr
    if event_type in _OIL_UP_EVENTS:
        return move + eps >= thr
    return move - eps <= -thr


def evaluate_cross_asset(direction: str, event_type: EventType,
                         assets: Dict[str, Tuple[Series, str]],
                         event_date: date, as_of: date) -> CrossAssetRead:
    reads: List[AssetRead] = []
    unavailable: List[str] = []
    for symbol in CROSS_ASSET_SYMBOLS:
        series, source = assets.get(symbol, ([], ""))
        move = _move_pct(series, event_date, as_of)
        if move is None:
            unavailable.append(symbol)
            continue
        reads.append(AssetRead(
            symbol=symbol, move_pct=move,
            threshold=CROSS_ASSET_THRESHOLDS[symbol],
            confirmed=_confirms(symbol, move, direction, event_type),
            source=source))
    return CrossAssetRead(
        direction=direction, event_type=event_type,
        count=sum(r.confirmed for r in reads), available=len(reads),
        reads=reads, unavailable=unavailable)


TWELVEDATA_URL = "https://api.twelvedata.com/time_series"


def fetch_twelvedata(symbol: str, api_key: str,
                     outputsize: int = 400) -> Series:
    """Daily closes from Twelve Data, ascending; [] on any failure.
    Free tier serves US-listed ETFs (TLT/UUP/USO verified 2026-07-22).
    Closes are unadjusted — acceptable because both baseline and last
    close come from the same source (same-source-per-symbol invariant)."""
    try:
        resp = requests.get(TWELVEDATA_URL, params={
            "symbol": symbol, "interval": "1day",
            "outputsize": outputsize, "apikey": api_key}, timeout=15)
        data = resp.json()
        if data.get("status") != "ok":
            logger.warning(f"{symbol}: twelvedata error — "
                           f"{data.get('message', 'unknown')}")
            return []
        return sorted(
            (datetime.strptime(v["datetime"], "%Y-%m-%d").date(),
             float(v["close"]))
            for v in data["values"])
    except Exception as e:
        logger.warning(f"{symbol}: twelvedata fetch failed — {e}")
        return []


def fetch_asset_series(symbol: str) -> Tuple[Series, str]:
    """yfinance primary, Twelve Data fallback. NEVER mixes sources for one
    symbol — baseline and last close must share adjustment conventions."""
    series = fetch_series(symbol, period="1y")
    if series:
        return series, "yfinance"
    key = os.getenv("TWELVE_DATA_KEY", "")
    if key:
        series = fetch_twelvedata(symbol, key)
        if series:
            return series, "twelvedata"
    return [], ""
