"""
Live harvest Phase 1 — post-ORATS replacement for the frozen snapshot ranking.

The old `get_harvest_candidates` filtered and ranked entirely on the
position_limits ORATS snapshot (frozen 2026-06-24). This module computes the
same inputs live:

  - IV30:  Tradier ATM chain nearest 30 DTE (same method as track_iv_weekly)
  - HV20:  realized vol from live daily closes (yfinance)
  - IVR:   index tickers get a real 52-week percentile from their vol index
           (SPY->^VIX, QQQ->^VXN, IWM->^RVX). Single names have no IVR source
           until iv_history matures (~Jun 2027) — they carry ivr_source
           'unavailable' and must be IVR-verified at the broker before entry.
  - TRR:   from historical_moves gap moves (live table, not the snapshot)
  - Earnings exclusion: earnings_calendar (live table)

Skew (r_slp_30) has no Phase-1 live source and is reported as None; the
scorer's HARVEST_SKEW_NULL_SCORE midpoint applies.
"""

import logging
import math
import sqlite3
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .constants import (
    HARVEST_EARNINGS_EXCLUSION_DAYS,
    HARVEST_IV_RANK_MIN,
    HARVEST_UNIVERSE_INDEX,
    HARVEST_UNIVERSE_STOCK,
    HARVEST_VOL_INDEX_FOR,
)

logger = logging.getLogger(__name__)

MIN_IV_HV_RATIO = 1.2       # same gate as the old snapshot SQL
HV20_WINDOW = 20            # trading days of log returns
MIN_IVR_HISTORY_POINTS = 10  # below this a percentile is meaningless
TRR_HIGH = 2.5
TRR_NORMAL = 1.5


def compute_hv20(closes: List[float]) -> Optional[float]:
    """
    Annualized 20-day realized volatility (%) from daily closes.

    Uses the most recent HV20_WINDOW log returns; needs >= 21 closes.
    """
    if closes is None or len(closes) < HV20_WINDOW + 1:
        return None
    window = closes[-(HV20_WINDOW + 1):]
    rets = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0


def compute_ivr(current: float, history: List[float]) -> Optional[float]:
    """
    Percentile-range IV rank: 100 * (current - min) / (max - min).

    Returns None when history is too short or the range is degenerate.
    """
    if current is None or history is None or len(history) < MIN_IVR_HISTORY_POINTS:
        return None
    lo, hi = min(history), max(history)
    if hi <= lo:
        return None
    return max(0.0, min(100.0, 100.0 * (current - lo) / (hi - lo)))


def classify_trr(gap_moves: List[float]) -> Optional[str]:
    """
    TRR level from historical gap moves (same thresholds as
    analyzer._compute_tail_risk_level): max|move| / avg|move|.
    """
    moves = [abs(m) for m in gap_moves if m is not None]
    if len(moves) < 2:
        return None
    avg = sum(moves) / len(moves)
    if avg <= 0:
        return None
    trr = max(moves) / avg
    if trr > TRR_HIGH:
        return "HIGH"
    if trr >= TRR_NORMAL:
        return "NORMAL"
    return "LOW"


def build_live_candidates(
    raw: List[Dict],
    iv_rank_min: float = HARVEST_IV_RANK_MIN,
    earnings_exclusion_days: int = HARVEST_EARNINGS_EXCLUSION_DAYS,
    today: Optional[date] = None,
) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """
    Apply harvest gates to prefetched live inputs.

    Each raw entry: {ticker, is_index, iv30d, hv20d, ivr, gap_moves,
    next_earnings (ISO str or None), max_contracts}.

    Gates (in order): IV/HV >= 1.2; index IVR >= iv_rank_min (stocks have no
    IVR gate — flagged 'unavailable'); TRR != HIGH; no earnings within
    earnings_exclusion_days.

    Returns (candidates, skipped) where candidates are dicts shaped for
    calculate_harvest_score and the harvest_mode display, and skipped is
    [(ticker, reason)].
    """
    today = today or date.today()
    candidates: List[Dict] = []
    skipped: List[Tuple[str, str]] = []

    for r in raw:
        ticker = r["ticker"]

        iv30d, hv20d = r.get("iv30d"), r.get("hv20d")
        if not iv30d or not hv20d:
            skipped.append((ticker, "missing live IV or HV"))
            continue

        iv_hv = iv30d / hv20d
        if iv_hv < MIN_IV_HV_RATIO:
            skipped.append((ticker, f"IV/HV {iv_hv:.2f} < {MIN_IV_HV_RATIO}"))
            continue

        ivr = r.get("ivr")
        if r.get("is_index") and ivr is not None:
            if ivr < iv_rank_min:
                skipped.append((ticker, f"IVR {ivr:.0f} < {iv_rank_min:.0f}"))
                continue
            ivr_source = "vol-index"
        else:
            # Stocks have no live IVR source until iv_history matures; an
            # index whose vol index is unavailable (^RVX discontinued)
            # degrades to the same ungated, verify-at-broker path.
            ivr = None
            ivr_source = "unavailable"

        trr_level = classify_trr(r.get("gap_moves") or [])
        if trr_level == "HIGH":
            skipped.append((ticker, "TRR HIGH"))
            continue

        next_earnings = r.get("next_earnings")
        if next_earnings:
            days_out = (date.fromisoformat(next_earnings) - today).days
            if 0 <= days_out <= earnings_exclusion_days:
                skipped.append(
                    (ticker, f"earnings {next_earnings} within {earnings_exclusion_days}d"))
                continue

        candidates.append({
            "ticker": ticker,
            "iv_rank_1y": round(ivr, 1) if ivr is not None else None,
            "ivr_source": ivr_source,
            "iv_hv_ratio": round(iv_hv, 3),
            "iv30d": round(iv30d, 1),
            "hv20d": round(hv20d, 1),
            "r_slp_30": None,
            "tail_risk_level": trr_level,
            "max_contracts": r.get("max_contracts"),
            "next_earnings": next_earnings,
        })

    return candidates, skipped


# ── IO orchestration ─────────────────────────────────────────────────────────

def _fetch_closes_yf(symbol: str, period: str) -> List[float]:
    """Daily closes via yfinance; [] on any failure."""
    try:
        import yfinance as yf  # noqa: PLC0415
        hist = yf.Ticker(symbol).history(period=period, interval="1d",
                                         auto_adjust=False)
        return [float(c) for c in hist["Close"].tolist() if c and c > 0]
    except Exception as e:
        logger.warning(f"{symbol}: yfinance history failed — {e}")
        return []


def _fetch_live_iv30(api, ticker: str, today: date) -> Optional[float]:
    """ATM IV nearest 30 DTE from Tradier — same method as track_iv_weekly."""
    from scripts.track_iv_weekly import _atm_iv, _pick_expiration  # noqa: PLC0415

    exp_result = api.get_expirations(ticker)
    if exp_result.is_err:
        logger.warning(f"{ticker}: expirations failed — {exp_result.error}")
        return None
    expiration = _pick_expiration(exp_result.value, today)
    if expiration is None:
        return None
    chain_result = api.get_option_chain(ticker, expiration)
    if chain_result.is_err:
        logger.warning(f"{ticker}: chain failed — {chain_result.error}")
        return None
    chain = chain_result.value
    iv, _ = _atm_iv(chain, float(chain.stock_price.amount))
    return iv


def get_live_harvest_candidates(
    container,
    db_path: str,
    iv_rank_min: float = HARVEST_IV_RANK_MIN,
    earnings_exclusion_days: int = HARVEST_EARNINGS_EXCLUSION_DAYS,
) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """
    Live Phase-1 candidate fetch for the fixed harvest universe.

    All market inputs are fetched at call time (Tradier ATM IV, yfinance
    HV20 and vol-index IVR); only earnings dates, gap moves, and the
    max_contracts policy value come from the DB.
    """
    today = date.today()
    universe = list(HARVEST_UNIVERSE_INDEX) + list(HARVEST_UNIVERSE_STOCK)

    conn = sqlite3.connect(db_path)
    try:
        raw: List[Dict] = []
        for ticker in universe:
            is_index = ticker in HARVEST_UNIVERSE_INDEX

            iv30d = _fetch_live_iv30(container.tradier, ticker, today)
            closes = _fetch_closes_yf(ticker, period="3mo")
            hv20d = compute_hv20(closes)

            ivr = None
            vol_sym = HARVEST_VOL_INDEX_FOR.get(ticker) if is_index else None
            if vol_sym:
                vol_hist = _fetch_closes_yf(vol_sym, period="1y")
                if vol_hist:
                    ivr = compute_ivr(vol_hist[-1], vol_hist)

            gap_moves = [row[0] for row in conn.execute(
                "SELECT gap_move_pct FROM historical_moves "
                "WHERE ticker = ? AND gap_move_pct IS NOT NULL "
                "ORDER BY earnings_date DESC LIMIT 12", (ticker,))]

            ern_row = conn.execute(
                "SELECT MIN(earnings_date) FROM earnings_calendar "
                "WHERE ticker = ? AND earnings_date >= date('now')",
                (ticker,)).fetchone()
            next_earnings = ern_row[0] if ern_row else None

            mc_row = conn.execute(
                "SELECT max_contracts FROM position_limits WHERE ticker = ?",
                (ticker,)).fetchone()
            max_contracts = mc_row[0] if mc_row else None

            raw.append({
                "ticker": ticker,
                "is_index": is_index,
                "iv30d": iv30d,
                "hv20d": hv20d,
                "ivr": ivr,
                "gap_moves": gap_moves,
                "next_earnings": next_earnings,
                "max_contracts": max_contracts,
            })
    finally:
        conn.close()

    return build_live_candidates(
        raw, iv_rank_min=iv_rank_min,
        earnings_exclusion_days=earnings_exclusion_days, today=today)
