"""Earnings moves from Twelve Data daily bars -- the one shared core.

Used by two callers with different contracts:

- `backfill_historical.py` -- the interactive/new-ticker backfill. Lenient: a
  failed fetch logs and returns [] (`get_twelve_data_prices`), and it keeps its
  own writer, which upserts the calendar and the prices.
- `jobs/record_moves.py` -- the scheduled job that appends a newly reported
  quarter. Strict: it needs to tell a rate limit (stop the run) from a bad
  symbol (a leftover) from a network blip (try tomorrow), so it calls
  `fetch_twelve_data_prices`, which raises; and it writes moves only through
  `insert_move_if_absent`, which adds a missing row and does nothing else. (Its
  one calendar write, a timing retag Yahoo and Finnhub agree on, is its own.)

Moved here from backfill_historical.py on 2026-09-13 so the job would not carry
a second copy of the fetch and the BMO/AMC arithmetic. Behaviour of the moved
functions is unchanged, except that since 2026-09-14 the calculator measures no
reaction whose session has not closed (it used to compare the date to today).
"""

import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Dict, List, Optional

import requests

from common.timezone import now_et
from src.config.config import APIConfig
from src.domain.reaction_session import is_reaction_complete

# The request, its rate limit and its retries live in the Twelve Data client.
# The error classes are imported into this namespace because record_moves and
# the tests catch them from here.
from src.infrastructure.api.twelve_data import (  # noqa: F401
    TwelveDataError,
    TwelveDataRateLimited,
    time_series,
)

logger = logging.getLogger(__name__)

TWELVE_DATA_TIMEOUT_SEC = 30


class EarningsTiming(Enum):
    BMO = "bmo"  # Before Market Open
    AMC = "amc"  # After Market Close
    UNKNOWN = ""


@dataclass
class EarningsEvent:
    """Earnings event with timing information."""
    ticker: str
    date: date
    timing: EarningsTiming
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None


@dataclass
class DailyPrice:
    """Daily OHLCV price data."""
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class EarningsMove:
    """Calculated earnings move."""
    ticker: str
    earnings_date: date
    timing: EarningsTiming
    prev_close: float
    reaction_open: float
    reaction_high: float
    reaction_low: float
    reaction_close: float
    gap_move_pct: float
    intraday_move_pct: float
    close_move_pct: float
    volume_before: int
    volume_reaction: int


def fetch_twelve_data_prices(
    ticker: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    *,
    api_key: Optional[str] = None,
) -> List[DailyPrice]:
    """Daily bars for `ticker`, newest first. Raises instead of hiding failures.

    The request waits on the process's Twelve Data limit, and a minute limit or
    a 5xx is retried before anything is raised (src/infrastructure/api/twelve_data.py).

    Raises:
        TwelveDataRateLimited: a rate limit the retries did not clear, the day's
            credits, or no call slot in time.
        TwelveDataError: any other error body, or no key.
        requests.RequestException: network failure or another HTTP error status.

    An `ok` response with no values returns [] -- that is data, not a failure.
    """
    key = api_key if api_key is not None else APIConfig.from_env().twelve_data_key
    if not key:
        raise TwelveDataError("TWELVE_DATA_KEY not set")

    params = {
        "symbol": ticker,
        "interval": "1day",
        "outputsize": 5000,  # Max available
    }
    if start_date:
        params["start_date"] = start_date.isoformat()
    if end_date:
        params["end_date"] = end_date.isoformat()

    data = time_series(params, key, timeout=TWELVE_DATA_TIMEOUT_SEC)

    prices = []
    for v in data.get("values", []) or []:
        try:
            prices.append(DailyPrice(
                date=date.fromisoformat(v["datetime"]),
                open=float(v["open"]),
                high=float(v["high"]),
                low=float(v["low"]),
                close=float(v["close"]),
                volume=int(v["volume"]),
            ))
        except (KeyError, ValueError) as ex:
            logger.debug(f"Skipping malformed price: {ex}")
            continue

    prices.sort(key=lambda x: x.date, reverse=True)
    return prices


def get_twelve_data_prices(
    ticker: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    api_key: Optional[str] = None,
) -> List[DailyPrice]:
    """
    Get historical daily prices from Twelve Data.

    backfill_historical's contract: every failure is logged and returns [].

    Args:
        ticker: Stock symbol
        start_date: Start date for data
        end_date: End date for data
        api_key: defaults to TWELVE_DATA_KEY, read at call time (APIConfig.from_env)

    Returns:
        List of DailyPrice sorted by date descending
    """
    try:
        prices = fetch_twelve_data_prices(ticker, start_date, end_date, api_key=api_key)
    except TwelveDataError as e:
        logger.error(f"Twelve Data error: {e}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Twelve Data API error: {e}")
        return []

    if not prices:
        logger.warning(f"No price data for {ticker}")
    return prices


def calculate_earnings_move(
    event: EarningsEvent,
    prices: List[DailyPrice],
) -> Optional[EarningsMove]:
    """
    Calculate earnings move with correct BMO/AMC handling.

    BMO (Before Market Open):
        - Earnings announced before market opens
        - Stock reacts on earnings day
        - Move = prev_day close -> earnings_day open

    AMC (After Market Close):
        - Earnings announced after market closes
        - Stock reacts NEXT day
        - Move = earnings_day close -> next_day open

    UNKNOWN:
        - Infer from price action by comparing gap magnitudes
        - If next-day gap > same-day gap, likely AMC

    ⚠️ Two liberties here that a scheduled caller must not rely on: a date with
    no bar is moved to the nearest bar within 3 days, and UNKNOWN timing is
    inferred from the gaps. jobs/record_moves.py checks the exact reference and
    reaction sessions itself and never passes UNKNOWN.
    """
    # Build price lookup by date
    price_map: Dict[date, DailyPrice] = {p.date: p for p in prices}

    earnings_date = event.date

    # A reaction session still trading has a partial daily bar: no move until it
    # closes (16:00 ET, 13:00 on an early close). UNKNOWN waits for the next
    # session, the later of the two it could react on (src/domain/reaction_session.py).
    if not is_reaction_complete(now_et(), earnings_date, event.timing.name):
        logger.warning(f"  ⏳ Skipping {earnings_date} ({event.timing.name}) - "
                       "reaction session has not closed")
        return None

    # Find trading days around earnings
    sorted_dates = sorted(price_map.keys())

    # Find earnings day index
    if earnings_date not in price_map:
        # Find closest trading day
        closest = min(sorted_dates, key=lambda d: abs((d - earnings_date).days))
        if abs((closest - earnings_date).days) > 3:
            logger.warning(f"  No price data near {earnings_date}")
            return None
        earnings_date = closest
        logger.debug(f"  Adjusted earnings date to {earnings_date}")

    earnings_idx = sorted_dates.index(earnings_date)

    # Get prev day (for BMO) or earnings day close (for AMC)
    if earnings_idx == 0:
        logger.warning(f"  No previous day data for {earnings_date}")
        return None

    prev_date = sorted_dates[earnings_idx - 1]
    prev_price = price_map[prev_date]
    earnings_price = price_map[earnings_date]

    # Determine reaction day based on timing
    effective_timing = event.timing

    # For UNKNOWN timing, infer from price action
    if event.timing == EarningsTiming.UNKNOWN and earnings_idx < len(sorted_dates) - 1:
        next_date = sorted_dates[earnings_idx + 1]
        next_price = price_map[next_date]

        # Calculate gap magnitude for both scenarios
        # BMO scenario: prev_close -> earnings_open
        bmo_gap = abs((earnings_price.open - prev_price.close) / prev_price.close * 100) if prev_price.close else 0
        # AMC scenario: earnings_close -> next_open
        amc_gap = abs((next_price.open - earnings_price.close) / earnings_price.close * 100) if earnings_price.close else 0

        # If next-day gap is significantly larger (>2x), likely AMC
        # Also if next-day gap > 3% and same-day gap < 1%, likely AMC
        if amc_gap > bmo_gap * 2 or (amc_gap > 3.0 and bmo_gap < 1.0):
            effective_timing = EarningsTiming.AMC
            logger.info(f"  📊 Inferred AMC timing: same-day gap {bmo_gap:.1f}% vs next-day gap {amc_gap:.1f}%")
        else:
            effective_timing = EarningsTiming.BMO
            logger.debug(f"  📊 Inferred BMO timing: same-day gap {bmo_gap:.1f}% vs next-day gap {amc_gap:.1f}%")

    if effective_timing == EarningsTiming.AMC:
        # AMC: reaction is next trading day
        if earnings_idx >= len(sorted_dates) - 1:
            logger.warning(f"  No next day data for AMC earnings {earnings_date}")
            return None

        next_date = sorted_dates[earnings_idx + 1]
        reaction_price = price_map[next_date]

        # For AMC: prev_close is earnings_day close, reaction is next_day
        reference_close = earnings_price.close
        volume_before = earnings_price.volume

        logger.debug(f"  AMC: {earnings_date} close ${reference_close:.2f} -> {next_date} open ${reaction_price.open:.2f}")

    else:
        # BMO: reaction is earnings day
        reaction_price = earnings_price

        # For BMO: prev_close is prev_day close
        reference_close = prev_price.close
        volume_before = prev_price.volume

        logger.debug(f"  BMO: {prev_date} close ${reference_close:.2f} -> {earnings_date} open ${reaction_price.open:.2f}")

    # Calculate moves
    if reference_close == 0:
        logger.warning("  Reference close is zero")
        return None

    # Gap move: reference close -> reaction open (preserves sign)
    gap_move_pct = (reaction_price.open - reference_close) / reference_close * 100

    # Intraday move: reaction-day open -> close, signed (the single convention
    # since the June 2026 re-backfill; consumers take abs() for magnitude)
    if reaction_price.open == 0:
        logger.warning("  Reaction open is zero")
        return None
    intraday_move_pct = (reaction_price.close - reaction_price.open) / reaction_price.open * 100

    # Close move: reference close -> reaction close (preserves sign)
    close_move_pct = (reaction_price.close - reference_close) / reference_close * 100

    return EarningsMove(
        ticker=event.ticker,
        earnings_date=event.date,  # Original earnings date
        timing=effective_timing,  # Use inferred timing if UNKNOWN was resolved
        prev_close=reference_close,
        reaction_open=reaction_price.open,
        reaction_high=reaction_price.high,
        reaction_low=reaction_price.low,
        reaction_close=reaction_price.close,
        gap_move_pct=gap_move_pct,
        intraday_move_pct=intraday_move_pct,
        close_move_pct=close_move_pct,
        volume_before=volume_before,
        volume_reaction=reaction_price.volume,
    )


# intraday/gap/close_move_pct are GENERATED from these prices (migration 2) and
# cannot be written. pre_earnings_straddle_pct and ern_iv_effect are frozen ORATS
# data and are not named. ON CONFLICT DO NOTHING: an existing row -- however it
# got there -- is never rewritten, and earnings_calendar is never touched.
INSERT_MOVE_IF_ABSENT_SQL = """
    INSERT INTO historical_moves
        (ticker, earnings_date, prev_close, earnings_open, earnings_high,
         earnings_low, earnings_close, volume_before, volume_earnings)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, earnings_date) DO NOTHING
"""


def insert_move_if_absent(conn, move: EarningsMove) -> bool:
    """Add `move` if (ticker, earnings_date) has no row. True if it was added.

    Does not commit: the caller owns the transaction.
    """
    cur = conn.execute(INSERT_MOVE_IF_ABSENT_SQL, (
        move.ticker,
        move.earnings_date,
        move.prev_close,
        move.reaction_open,
        move.reaction_high,
        move.reaction_low,
        move.reaction_close,
        move.volume_before,
        move.volume_reaction,
    ))
    return cur.rowcount == 1
