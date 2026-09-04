"""
Earnings source aggregation - Finnhub, Yahoo Finance, and DB lookups.

Provides earnings calendar fetching, validation, and database sync.
"""

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from tqdm import tqdm

from src.container import Container
from src.domain.enums import EarningsTiming
from src.application.services.earnings_date_validator import EarningsDateValidator
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings

logger = logging.getLogger(__name__)

# Module-level singleton so LRU cache persists across all calls in one whisper run
_yf_earnings: Optional["YahooFinanceEarnings"] = None


def _get_yf_earnings() -> "YahooFinanceEarnings":
    global _yf_earnings
    if _yf_earnings is None:
        _yf_earnings = YahooFinanceEarnings()
    return _yf_earnings


def fetch_earnings_for_date(
    container: Container,
    scan_date: date
) -> List[Tuple[str, date, EarningsTiming]]:
    """
    Fetch earnings calendar and filter for specific date.

    Returns:
        List of (ticker, earnings_date, timing) tuples
    """
    logger.info(f"Fetching earnings calendar for {scan_date}...")

    finnhub = container.finnhub
    result = finnhub.get_earnings_calendar(horizon="3month")

    if result.is_err:
        logger.error(f"Failed to fetch earnings calendar: {result.error}")
        return []

    all_earnings = result.value
    logger.info(f"Fetched {len(all_earnings)} total earnings events")

    # Filter for specific date
    filtered = [
        (ticker, earn_date, timing)
        for ticker, earn_date, timing in all_earnings
        if earn_date == scan_date
    ]

    logger.info(f"Found {len(filtered)} earnings on {scan_date}")
    return filtered


def fetch_earnings_for_ticker(
    container: Container,
    ticker: str,
    av_cache: Optional[dict] = None,
) -> Optional[Tuple[date, EarningsTiming]]:
    """
    Fetch earnings date for a specific ticker.

    Priority:
    1. Database (validated, cross-referenced source of truth)
    2. Alpha Vantage API (fallback for tickers not in DB)

    Args:
        container: DI container
        ticker: Stock ticker symbol

    Returns:
        (earnings_date, timing) tuple or None if not found
    """
    # PRIORITY 1: Check database first (source of truth, validated data)
    import sqlite3
    db_path = container.config.database.path
    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT earnings_date, timing, updated_at, last_validated_at
                FROM earnings_calendar
                WHERE ticker = ? AND earnings_date >= date('now')
                ORDER BY earnings_date ASC
                LIMIT 1
                ''',
                (ticker,)
            )
            row = cursor.fetchone()
            if row:
                from src.domain.enums import EarningsTiming
                from datetime import datetime
                earnings_date = date.fromisoformat(row[0])
                timing = EarningsTiming(row[1])
                updated_at = datetime.fromisoformat(row[2]) if row[2] else None
                last_validated_at = datetime.fromisoformat(row[3]) if row[3] else None

                # Freshness validation: if earnings within 7 days and not recently validated,
                # check Alpha Vantage to catch date changes
                days_until_earnings = (earnings_date - date.today()).days
                # Use last_validated_at if available, otherwise fall back to updated_at
                last_checked = last_validated_at or updated_at
                hours_since_check = (datetime.now() - last_checked).total_seconds() / 3600 if last_checked else 999

                if days_until_earnings <= 7 and hours_since_check > 24:
                    logger.info(f"{ticker}: Validating stale cache ({hours_since_check:.0f}h old, earnings in {days_until_earnings}d)")
                    # Use pre-fetched bulk calendar when available — avoids a rate-limited per-ticker call
                    av_date, av_timing = None, None
                    if av_cache is not None and ticker in av_cache:
                        av_date, av_timing = av_cache[ticker]
                        av_validated = True
                    else:
                        # Yahoo Finance: higher confidence than AV, no hard rate limit
                        yf_result = _get_yf_earnings().get_next_earnings_date(ticker)
                        av_validated = yf_result.is_ok
                        if av_validated:
                            av_date, av_timing = yf_result.value

                    if av_validated:
                        if av_date != earnings_date:
                            date_diff_days = (av_date - earnings_date).days

                            # If API returns a date 45+ days different (earlier OR later), it's a different quarter
                            NEXT_QUARTER_THRESHOLD_DAYS = 45
                            if abs(date_diff_days) >= NEXT_QUARTER_THRESHOLD_DAYS:
                                direction = "later" if date_diff_days > 0 else "earlier"
                                logger.warning(
                                    f"{ticker}: API shows different quarter ({av_date}, {abs(date_diff_days)}d {direction}). "
                                    f"DB date {earnings_date} likely stale or mismatched. Skipping."
                                )
                                # Mark as validated but don't update to next quarter
                                cursor.execute(
                                    '''
                                    UPDATE earnings_calendar
                                    SET last_validated_at = datetime('now')
                                    WHERE ticker = ? AND earnings_date = ?
                                    ''',
                                    (ticker, earnings_date.isoformat())
                                )
                                conn.commit()
                                # Return None to skip this ticker
                                return None

                            logger.warning(f"{ticker}: Date changed? DB={earnings_date} -> API={av_date} — corroborating before writing")
                            # A single source disagreeing with the DB is not enough to
                            # overwrite a previously-validated date — require a second,
                            # independent source to agree first. Fixed 2026-07-27 after
                            # this exact single-source-overwrite pattern (in the sibling
                            # sync_earnings_calendar.py) silently corrupted a real
                            # ticker's earnings date, twice in one day, off a bulk
                            # calendar source that itself carried two conflicting entries.
                            corroborated = False
                            try:
                                fh_result = container.finnhub.get_earnings_calendar(
                                    symbol=ticker, horizon="3month"
                                )
                                if fh_result.is_ok and fh_result.value:
                                    _, fh_date, _ = fh_result.value[0]
                                    corroborated = (fh_date == av_date)
                            except Exception as e:
                                logger.debug(f"{ticker}: Corroboration check failed: {e}")

                            if not corroborated:
                                logger.warning(
                                    f"{ticker}: NEEDS REVIEW — could not corroborate {av_date} "
                                    f"against a second source. Keeping DB date {earnings_date}, will retry next check."
                                )
                                cursor.execute(
                                    '''
                                    UPDATE earnings_calendar
                                    SET last_validated_at = datetime('now')
                                    WHERE ticker = ? AND earnings_date = ?
                                    ''',
                                    (ticker, earnings_date.isoformat())
                                )
                                conn.commit()
                                logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value}) [from DB - unresolved conflict, kept]")
                                return (earnings_date, timing)

                            # Delete old entry and insert new one to avoid PRIMARY KEY violation
                            try:
                                cursor.execute(
                                    'DELETE FROM earnings_calendar WHERE ticker = ? AND earnings_date = ?',
                                    (ticker, earnings_date.isoformat())
                                )
                                cursor.execute(
                                    '''
                                    INSERT OR REPLACE INTO earnings_calendar
                                    (ticker, earnings_date, timing, updated_at, last_validated_at)
                                    VALUES (?, ?, ?, datetime('now'), datetime('now'))
                                    ''',
                                    (ticker, av_date.isoformat(), av_timing.value)
                                )
                                conn.commit()
                            except sqlite3.IntegrityError as e:
                                logger.warning(f"{ticker}: DB update failed ({e}), using API date anyway")
                            logger.info(f"{ticker}: Earnings on {av_date} ({av_timing.value}) [from API - corroborated & corrected]")
                            return (av_date, av_timing)
                        else:
                            # Date confirmed, update last_validated_at
                            cursor.execute(
                                '''
                                UPDATE earnings_calendar
                                SET last_validated_at = datetime('now')
                                WHERE ticker = ? AND earnings_date = ?
                                ''',
                                (ticker, earnings_date.isoformat())
                            )
                            conn.commit()
                            logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value}) [from DB - validated]")
                            return (earnings_date, timing)
                    else:
                        logger.warning(f"{ticker}: API validation failed, using potentially stale cache date {earnings_date}")

                logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value}) [from DB]")
                return (earnings_date, timing)
    except Exception as e:
        logger.debug(f"DB lookup failed for {ticker}: {e}")

    # PRIORITY 2: Fallback to Alpha Vantage API
    finnhub = container.finnhub
    result = finnhub.get_earnings_calendar(symbol=ticker, horizon="3month")

    if result.is_err:
        logger.warning(f"Failed to fetch earnings for {ticker}: {result.error}")
        return None

    earnings = result.value
    if not earnings:
        logger.warning(f"No upcoming earnings found for {ticker}")
        return None

    # Get the nearest earnings date. `earnings[0]` used to be trusted as-is,
    # but Finnhub's response order is unspecified and only dedupes on exact
    # (ticker, date) — near-duplicate entries a day or two apart (seen live
    # for PYPL: both 07-27 and 07-28 returned for the same event) survive
    # and could land in either order. Explicitly sort so "nearest" means
    # nearest, not "whichever the API happened to return first".
    if len(earnings) > 1:
        dates = sorted({e[1] for e in earnings})
        if len(dates) > 1 and (dates[1] - dates[0]).days <= 3:
            logger.debug(
                f"{ticker}: Finnhub returned near-duplicate dates {dates[0]} and "
                f"{dates[1]} for the same event — using the earliest."
            )
    earnings_sorted = sorted(earnings, key=lambda e: e[1])
    ticker_symbol, earnings_date, timing = earnings_sorted[0]
    logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value}) [from API]")
    return (earnings_date, timing)


def validate_tradeable_earnings_dates(tradeable_results: List[dict], container: Container) -> None:
    """
    Validate earnings dates for tradeable opportunities only.

    Cross-references earnings dates from Yahoo Finance and Alpha Vantage
    for tickers that passed all filters and have tradeable strategies.
    This optimizes validation by skipping tickers that won't be displayed.

    Args:
        tradeable_results: List of tradeable result dictionaries
        container: DI container with Alpha Vantage API
    """
    if not tradeable_results:
        return

    # Extract unique tickers from tradeable results
    tickers_to_validate = list({r['ticker'] for r in tradeable_results})

    if not tickers_to_validate:
        return

    # Skip tickers validated in the last 5 min — fetch_earnings_for_ticker already checked them
    import sqlite3
    _cutoff = (datetime.now() - timedelta(minutes=5)).isoformat()
    try:
        with sqlite3.connect(container.config.database.path, timeout=10) as _conn:
            _ph = ','.join('?' * len(tickers_to_validate))
            _rows = _conn.execute(
                f"SELECT DISTINCT ticker FROM earnings_calendar "
                f"WHERE ticker IN ({_ph}) AND earnings_date >= date('now') "
                f"AND last_validated_at > ?",
                tickers_to_validate + [_cutoff]
            ).fetchall()
            _recently_validated = {row[0] for row in _rows}
    except Exception:
        _recently_validated = set()

    if _recently_validated:
        tickers_to_validate = [t for t in tickers_to_validate if t not in _recently_validated]
        logger.info(f"Skipping {len(_recently_validated)} recently validated: {', '.join(sorted(_recently_validated))}")

    if not tickers_to_validate:
        logger.info("All tradeable tickers recently validated — skipping redundant pass")
        return

    logger.info(f"\n\U0001f50d Validating earnings dates for {len(tickers_to_validate)} tradeable tickers...")

    # Initialize validator — Yahoo Finance only (Finnhub bulk already ran; no per-ticker call needed)
    yahoo_finance = YahooFinanceEarnings()
    validator = EarningsDateValidator(
        finnhub=None,
        yahoo_finance=yahoo_finance
    )

    # Validate each ticker with progress bar
    success_count = 0
    conflict_count = 0

    for ticker in tqdm(tickers_to_validate, desc="Validating", unit="ticker"):
        result = validator.validate_earnings_date(ticker)

        if result.is_ok:
            validation = result.value
            success_count += 1

            if validation.has_conflict:
                conflict_count += 1
                logger.debug(f"\u26a0\ufe0f  {ticker}: Conflict detected - {validation.conflict_details}")
        else:
            logger.debug(f"\u2717 {ticker}: Validation failed - {result.error}")

    logger.info(f"\u2713 Validated {success_count}/{len(tickers_to_validate)} tickers" +
                (f" (\u26a0\ufe0f  {conflict_count} conflicts)" if conflict_count > 0 else ""))
    logger.info("")


def ensure_tickers_in_db(tickers: list[str], container: Container) -> None:
    """
    Ensure all tickers are in the database, resolving each missing ticker's
    real next earnings date via Yahoo Finance before writing anything.

    Previously this wrote an unconditional `today + 7 days` placeholder for
    every newly-discovered ticker. That guess always landed inside the scan
    week by construction, so it was silently trusted (no revalidation for
    rows less than 24h old) and fed a bogus VRP/implied-move calculation for
    any ticker whose real earnings were actually months away. The follow-up
    sync/cleanup step that was supposed to correct it only ever ran for
    tickers already present in `strategies`/`trade_journal`, so newly
    discovered untraded tickers kept the fake date permanently. Root cause
    found + fixed 2026-07-29.

    Args:
        tickers: List of ticker symbols to ensure in database
        container: DI container
    """
    import sqlite3

    db_path = container.config.database.path

    # Check which tickers are missing from DB
    missing_tickers = []
    with sqlite3.connect(db_path, timeout=30) as conn:
        cursor = conn.cursor()
        for ticker in tickers:
            cursor.execute(
                "SELECT COUNT(*) FROM earnings_calendar WHERE ticker = ? AND earnings_date >= date('now')",
                (ticker,)
            )
            if cursor.fetchone()[0] == 0:
                missing_tickers.append(ticker)

    if not missing_tickers:
        logger.info(f"\u2713 All {len(tickers)} tickers already in database")
        return

    logger.info(f"\U0001f50d Resolving real earnings dates for {len(missing_tickers)} new tickers...")
    yf_earnings = _get_yf_earnings()

    resolved: list[tuple[str, str, str]] = []  # (ticker, earnings_date, timing)
    unresolved: list[str] = []
    for ticker in missing_tickers:
        result = yf_earnings.get_next_earnings_date(ticker)
        if result.is_ok:
            earnings_date, timing = result.value
            resolved.append((ticker, earnings_date.isoformat(), timing.value))
        else:
            unresolved.append(ticker)

    if resolved:
        with sqlite3.connect(db_path, timeout=30) as conn:
            cursor = conn.cursor()
            for ticker, earnings_date_str, timing_value in resolved:
                cursor.execute(
                    """INSERT OR IGNORE INTO earnings_calendar
                       (ticker, earnings_date, timing, confirmed, last_validated_at)
                       VALUES (?, ?, ?, 1, datetime('now'))""",
                    (ticker, earnings_date_str, timing_value)
                )
            conn.commit()
        logger.info(
            f"\u2713 Added {len(resolved)} tickers with validated earnings dates: "
            f"{', '.join(t for t, _, _ in resolved[:10])}" +
            ("..." if len(resolved) > 10 else "")
        )

    if unresolved:
        logger.info(
            f"\u23ed\ufe0f  Skipping {len(unresolved)} tickers with no resolvable earnings date "
            f"(will retry via API fallback during analysis): {', '.join(unresolved[:10])}" +
            ("..." if len(unresolved) > 10 else "")
        )
