"""Market data + frozen entry-reference computation for the TACO engine.

All reference levels are computed once at entry and FROZEN — the exit engine
never recomputes them (a moving panic_low chases the market down and the stop
can never fire; audit finding 1, spec 2026-07-14).
"""

import logging
from dataclasses import dataclass
from datetime import date
from statistics import mean, pstdev
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

Series = List[Tuple[date, float]]  # ascending by date

SPX_SYMBOL = "^GSPC"
VIX_SYMBOL = "^VIX"
VIX3M_SYMBOL = "^VIX3M"

LOOKBACK_SESSIONS = 20
VIX_BASELINE_SESSIONS = 10


def fetch_series(symbol: str, period: str = "1y") -> Series:
    """Daily closes via yfinance; [] on any failure (same pattern as harvest_live)."""
    try:
        import yfinance as yf  # noqa: PLC0415
        hist = yf.Ticker(symbol).history(period=period, interval="1d",
                                         auto_adjust=False)
        return [(d.date(), float(c))
                for d, c in zip(hist.index, hist["Close"], strict=False)
                if c and c > 0]
    except Exception as e:
        logger.warning(f"{symbol}: yfinance history failed — {e}")
        return []


def with_live_override(series: Series, as_of: date,
                       live_value: Optional[float]) -> Series:
    """Replace (or append) the as_of entry in a daily-close series with a
    live intraday value. No-op if live_value is None (fetch failed or
    market closed) — callers always get a valid series back either way."""
    if live_value is None:
        return series
    if series and series[-1][0] == as_of:
        return series[:-1] + [(as_of, live_value)]
    return series + [(as_of, live_value)]


def fetch_intraday_quote(symbol: str) -> Optional[float]:
    """Live last price via yfinance fast_info. None on any failure — callers
    must fall back to the daily-close series, never block on this.
    fast_info.last_price is attribute access, not a dict key — FastInfo's
    .get() only sees its raw camelCase keys (e.g. 'lastPrice') and silently
    returns None for the snake_case name."""
    try:
        import yfinance as yf  # noqa: PLC0415
        price = yf.Ticker(symbol).fast_info.last_price
        return float(price) if price else None
    except Exception as e:
        logger.warning(f"{symbol}: yfinance fast_info failed — {e}")
        return None


def up_to(series: Series, as_of: date) -> Series:
    return [p for p in series if p[0] <= as_of]


@dataclass(frozen=True)
class EntryRefs:
    """Frozen at entry. Calls use panic_low; puts use euphoria_high+rip_base."""
    direction: str                   # 'CALL' | 'PUT'
    event_date: date
    pre_event_high: float            # 20-session closing high as of event_date
    panic_low: Optional[float]
    euphoria_high: Optional[float]
    rip_base: Optional[float]        # 20-session mean close as of event_date
    pre_event_vix: float             # median VIX close, 10 sessions pre-event


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def default_event_date(spx: Series, as_of: date) -> date:
    """Date of the 20-session closing high ending at as_of (latest date at the
    max — the last session at the top before the break)."""
    window = up_to(spx, as_of)[-LOOKBACK_SESSIONS:]
    if not window:
        raise ValueError("no SPX data at or before as_of")
    hi = max(c for _, c in window)
    return [d for d, c in window if c == hi][-1]


def compute_refs(direction: str, spx: Series, vix: Series,
                 event_date: date, entry_date: date) -> EntryRefs:
    """Compute the frozen reference set at entry. Raises ValueError on
    insufficient history — refs must never be silently wrong."""
    pre = up_to(spx, event_date)
    if len(pre) < LOOKBACK_SESSIONS:
        raise ValueError(
            f"need >= {LOOKBACK_SESSIONS} SPX sessions before event_date")
    pre_window = [c for _, c in pre[-LOOKBACK_SESSIONS:]]
    pre_event_high = max(pre_window)

    between = [c for d, c in spx if event_date <= d <= entry_date]
    if not between:
        raise ValueError("no SPX closes between event_date and entry_date")

    vix_pre = [c for d, c in vix if d < event_date][-VIX_BASELINE_SESSIONS:]
    if len(vix_pre) < VIX_BASELINE_SESSIONS:
        raise ValueError(
            f"need >= {VIX_BASELINE_SESSIONS} VIX sessions before event_date")
    pre_event_vix = _median(vix_pre)

    if direction == "CALL":
        return EntryRefs("CALL", event_date, pre_event_high,
                         min(between), None, None, pre_event_vix)
    if direction == "PUT":
        return EntryRefs("PUT", event_date, pre_event_high,
                         None, max(between), mean(pre_window), pre_event_vix)
    raise ValueError(f"direction must be CALL or PUT, got {direction!r}")


def drawdown_pct(spx: Series, as_of: date) -> float:
    """% below the 20-session closing high (>= 0)."""
    window = up_to(spx, as_of)[-LOOKBACK_SESSIONS:]
    if not window:
        raise ValueError("no SPX data at or before as_of")
    hi = max(c for _, c in window)
    return max(0.0, (hi - window[-1][1]) / hi * 100.0)


def drawdown_from_event(spx: Series, event_date: date, as_of: date) -> float:
    """% below the 20-session closing high AS OF event_date (>= 0). Unlike
    drawdown_pct, the pre-crash high cannot roll out of the window during a
    slide longer than 20 sessions."""
    pre = up_to(spx, event_date)
    if not pre:
        raise ValueError("no SPX data at or before event_date")
    hi = max(c for _, c in pre[-LOOKBACK_SESSIONS:])
    window = up_to(spx, as_of)
    if not window:
        raise ValueError("no SPX data at or before as_of")
    return max(0.0, (hi - window[-1][1]) / hi * 100.0)


def drawdown_from_level(spx: Series, high_as_of: date,
                        current_level: float) -> float:
    """% below the 20-session closing high as of `high_as_of`, using an
    explicit current level (e.g. a live intraday quote) in place of a
    series lookup for 'now'. Pass high_as_of=today for the no-event-date
    path, or high_as_of=event_date for the anchored path — mirrors the
    drawdown_pct / drawdown_from_event split above."""
    window = up_to(spx, high_as_of)[-LOOKBACK_SESSIONS:]
    if not window:
        raise ValueError("no SPX data at or before high_as_of")
    hi = max(c for _, c in window)
    return max(0.0, (hi - current_level) / hi * 100.0)


def runup_zscore(spx: Series, as_of: date) -> float:
    """Z-score of the trailing 20-session return vs its own distribution
    over the supplied series (~1y). Put-side dip-depth mirror — NOT
    'above 20-day mean', which is almost always positive (audit finding 4)."""
    closes = [c for _, c in up_to(spx, as_of)]
    if len(closes) < 3 * LOOKBACK_SESSIONS:
        raise ValueError("need >= 60 sessions for run-up z-score")
    rets = [closes[i] / closes[i - LOOKBACK_SESSIONS] - 1
            for i in range(LOOKBACK_SESSIONS, len(closes))]
    sd = pstdev(rets)
    return 0.0 if sd == 0 else (rets[-1] - mean(rets)) / sd


def runup_zscore_from_level(spx: Series, as_of: date,
                            current_level: float) -> float:
    """Like runup_zscore, but 'now' is an explicit level (e.g. a live
    intraday quote) rather than the series' last close."""
    closes = [c for _, c in up_to(spx, as_of)]
    if len(closes) < 3 * LOOKBACK_SESSIONS:
        raise ValueError("need >= 60 sessions for run-up z-score")
    rets = [closes[i] / closes[i - LOOKBACK_SESSIONS] - 1
            for i in range(LOOKBACK_SESSIONS, len(closes))]
    sd = pstdev(rets)
    current_ret = current_level / closes[-(LOOKBACK_SESSIONS + 1)] - 1
    return 0.0 if sd == 0 else (current_ret - mean(rets)) / sd


def vix_spike(vix: Series, as_of: date) -> float:
    """Last VIX close / mean of the prior 10 closes."""
    window = up_to(vix, as_of)
    if len(window) < VIX_BASELINE_SESSIONS + 1:
        raise ValueError("need >= 11 VIX sessions")
    prior = [c for _, c in window[-(VIX_BASELINE_SESSIONS + 1):-1]]
    return window[-1][1] / mean(prior)


def vix_spike_from_level(vix: Series, as_of: date, current_level: float) -> float:
    """Like vix_spike, but 'now' is an explicit level (e.g. a live intraday
    quote) rather than the series' last close. Baseline is always the 10
    sessions strictly BEFORE as_of, so it doesn't matter whether today's
    partial daily bar is already present in the series."""
    prior = [c for d, c in vix if d < as_of][-VIX_BASELINE_SESSIONS:]
    if len(prior) < VIX_BASELINE_SESSIONS:
        raise ValueError(f"need >= {VIX_BASELINE_SESSIONS} VIX sessions before as_of")
    return current_level / mean(prior)
