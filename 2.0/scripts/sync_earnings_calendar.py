#!/usr/bin/env python3
"""
Automated earnings calendar synchronization and validation.

This script proactively discovers and validates upcoming earnings dates by:
1. Fetching full earnings calendar from Finnhub (3-month horizon)
2. Cross-validating new/changed dates with Yahoo Finance
3. Detecting conflicts and using consensus (Yahoo Finance priority)
4. Updating database with validated dates
5. Deduplicating stale entries (same ticker, same quarter)

Designed to run as a daily cron job (recommended: 8 PM ET after market close)

Report-only unless --execute is passed (--check-staleness never writes).

Usage:
    # Show what a sync would change
    python scripts/sync_earnings_calendar.py

    # Sync
    python scripts/sync_earnings_calendar.py --execute

    # Custom horizon
    python scripts/sync_earnings_calendar.py --horizon 6month --execute

    # Check staleness only
    python scripts/sync_earnings_calendar.py --check-staleness --threshold 14

    # Cleanup duplicate entries only (no sync)
    python scripts/sync_earnings_calendar.py --cleanup-dupes            # report
    python scripts/sync_earnings_calendar.py --cleanup-dupes --execute  # delete

    # A backlog older than the routine 14-day scan needs a tighter quarter window
    python scripts/sync_earnings_calendar.py --cleanup-dupes --window-past-days 400 \\
        --quarter-window-days 45
"""

import argparse
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Set, Tuple

from tqdm import tqdm

from common.db import connect  # noqa: E402
from common.timezone import now_et, today_et  # noqa: E402
from src.application.services.earnings_date_validator import EarningsDateValidator
from src.config.config import Config
from src.domain.types import EarningsTiming
from src.infrastructure.api.finnhub import FinnhubAPI
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings
from src.infrastructure.database.repositories.earnings_repository import (
    EarningsRepository,
)
from src.utils.logging import setup_logging
from src.utils.rate_limiter import create_finnhub_limiter

logger = logging.getLogger(__name__)

# Skip conflict validation for tickers validated within this threshold
VALIDATION_SKIP_HOURS = 48

# Data is considered stale if not updated within this threshold
STALENESS_THRESHOLD_DAYS = 14

# Max days apart for two entries to be considered the same earnings event
DEDUP_QUARTER_WINDOW_DAYS = 90

# ⚠️ Rules 1 and 3 delete entries up to the quarter window apart, and real
# consecutive quarters are ~90 days apart. Inside the routine 14-day scan that
# only ever meets a stale echo; scan back further and it meets real quarters: a
# 1000-day dry run proposed deleting 2,093 rows against 507 genuine clusters.
# So a scan wider than WIDE_SCAN_PAST_DAYS must tighten the quarter window to at
# most WIDE_SCAN_MAX_QUARTER_WINDOW_DAYS -- halfway to the next quarter, the
# same threshold scan/earnings_fetcher.py uses to call a date a different quarter.
WIDE_SCAN_PAST_DAYS = 180
WIDE_SCAN_MAX_QUARTER_WINDOW_DAYS = 45


def check_scan_windows(window_past_days: int, quarter_window_days: int) -> None:
    """Refuse a wide dedup scan that keeps the full quarter window."""
    if (window_past_days > WIDE_SCAN_PAST_DAYS
            and quarter_window_days > WIDE_SCAN_MAX_QUARTER_WINDOW_DAYS):
        raise ValueError(
            f"a {window_past_days}-day scan with a {quarter_window_days}-day quarter window "
            f"would treat real consecutive quarters as duplicates; above "
            f"{WIDE_SCAN_PAST_DAYS} days pass --quarter-window-days "
            f"{WIDE_SCAN_MAX_QUARTER_WINDOW_DAYS} or less"
        )


def should_skip_validation(last_validated_at: datetime | None) -> bool:
    """
    Check if conflict validation can be skipped based on last validation time.

    Args:
        last_validated_at: Timestamp of last validation, or None if never validated

    Returns:
        True if validation was performed within VALIDATION_SKIP_HOURS, False otherwise
    """
    if last_validated_at is None:
        return False
    hours_since_validation = (datetime.now(timezone.utc) - last_validated_at).total_seconds() / 3600
    return hours_since_validation < VALIDATION_SKIP_HOURS


class SyncStats:
    """Track synchronization statistics."""

    def __init__(self):
        self.new_dates = 0
        self.updated_dates = 0
        self.unchanged_dates = 0
        self.conflicts_detected = 0
        self.uncorroborated = 0  # only one source had a date
        self.validation_skipped = 0  # Tickers skipped due to recent validation
        self.dupes_removed = 0
        self.errors = 0
        self.tickers_processed: Set[str] = set()
        self.changes: List[Dict] = []
        self.dedup_details: List[Dict] = []
        # Every date the sync did not write, listed at the END of the summary. On
        # 2026-09-15 the unattended /maintenance run saw a truncated tool output that
        # ended before the summary; it reported one conflict (FDX) of four and read
        # exit 1 as harmless. The per-ticker log lines are thousands of characters
        # upstream -- the list has to live where the summary is.
        self.needs_review: List[Dict] = []
        # Tickers whose date was cross-validated (Yahoo+Finnhub, no conflict) earlier
        # in this same run. Handed to cleanup_duplicate_earnings() so the post-sync
        # dedup pass can reuse that consensus instead of re-deriving it from scratch.
        self.resolved_this_run: Dict[str, Tuple[date, EarningsTiming]] = {}

    def add_review(self, ticker: str, stored: date | None, sources: str, conflict: bool) -> None:
        """Record a date the sync refused to write: sources disagree, or only one has it."""
        if conflict:
            self.conflicts_detected += 1
            reason = "sources disagree"
        else:
            self.uncorroborated += 1
            reason = "only one source has a date"
        self.errors += 1
        self.needs_review.append(
            {"ticker": ticker, "stored": stored, "sources": sources, "reason": reason}
        )
        logger.warning(
            f"  ⏭️  NEEDS REVIEW — {ticker}: {reason} ({sources}); stored "
            f"{stored or 'nothing'}. Not written, will retry next sync."
        )

    def review(self, ticker: str, stored: date | None, validation) -> None:
        """add_review() for a validator result that is not corroborated."""
        sources = " | ".join(
            f"{s.source.value} {s.earnings_date} ({s.timing.value})" for s in validation.sources
        )
        self.add_review(ticker, stored, sources, conflict=validation.has_conflict)

    def log_summary(self):
        """Log summary statistics."""
        failed = self.errors - len(self.needs_review)
        logger.info("\n" + "=" * 80)
        logger.info("SYNC SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Tickers processed: {len(self.tickers_processed)}")
        logger.info(f"  ✓ New earnings dates: {self.new_dates}")
        logger.info(f"  ↻ Updated dates: {self.updated_dates}")
        logger.info(f"  = Unchanged: {self.unchanged_dates}")
        logger.info(f"  ⏭️  Validation skipped (recent): {self.validation_skipped}")
        logger.info(f"  ⚠️  Conflicts detected: {self.conflicts_detected}")
        logger.info(f"  ⚠️  Single-source dates not written: {self.uncorroborated}")
        logger.info(f"  🗑️  Duplicates removed: {self.dupes_removed}")
        logger.info(
            f"  ✗ Errors: {self.errors} ({len(self.needs_review)} need review, {failed} failed)"
        )

        if self.changes:
            logger.info("\nCHANGES DETECTED:")
            for change in self.changes:
                logger.info(
                    f"  {change['ticker']}: {change['old_date']} → {change['new_date']} "
                    f"({change['timing'].value}) {change['reason']}"
                )

        if self.dedup_details:
            logger.info("\nDUPLICATES REMOVED:")
            for d in self.dedup_details:
                logger.info(
                    f"  {d['ticker']}: removed {d['removed_date']} "
                    f"({d['removed_timing']}, confirmed={d['removed_confirmed']}) "
                    f"— kept {d['kept_date']} (confirmed={d['kept_confirmed']}, "
                    f"{d['days_apart']}d apart)"
                )

        if self.needs_review:
            logger.info(f"\nNEEDS REVIEW ({len(self.needs_review)}) — not written:")
            for r in self.needs_review:
                logger.info(
                    f"  {r['ticker']}: stored {r['stored'] or 'nothing'} — {r['reason']}: {r['sources']}"
                )


def get_focused_tickers() -> Set[str]:
    """
    Return tickers we've actually traded (from strategies + trade_journal).
    Used as the default sync target to avoid crawling thousands of irrelevant tickers.
    """
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT symbol FROM strategies "
        "UNION SELECT DISTINCT symbol FROM trade_journal"
    )
    tickers = {row[0] for row in cursor.fetchall()}
    conn.close()
    return tickers


def get_database_dates(
    horizon_days: int = 90,
    ticker_filter: Set[str] | None = None,
) -> Dict[str, Tuple[date, EarningsTiming, datetime, datetime | None]]:
    """
    Get current earnings dates from database.

    Args:
        ticker_filter: If provided, restrict to this set of tickers.

    Returns:
        Dict[ticker] = (earnings_date, timing, updated_at, last_validated_at)
    """
    conn = connect()
    cursor = conn.cursor()

    cutoff_date = (today_et() - timedelta(days=7)).isoformat()
    future_date = (today_et() + timedelta(days=horizon_days)).isoformat()

    if ticker_filter:
        placeholders = ", ".join(["%s"] * len(ticker_filter))
        cursor.execute(
            f"""
            SELECT ticker, earnings_date, timing, updated_at, last_validated_at
            FROM earnings_calendar
            WHERE earnings_date >= %s AND earnings_date <= %s
            AND ticker IN ({placeholders})
            ORDER BY earnings_date
            """,
            (cutoff_date, future_date, *ticker_filter),
        )
    else:
        cursor.execute(
            """
            SELECT ticker, earnings_date, timing, updated_at, last_validated_at
            FROM earnings_calendar
            WHERE earnings_date >= %s AND earnings_date <= %s
            ORDER BY earnings_date
            """,
            (cutoff_date, future_date),
        )

    result = {}
    for row in cursor.fetchall():
        # date / timestamptz columns arrive already typed. The old "handle
        # both ISO and simple format" dance existed because SQLite stored
        # two text conventions; Postgres stores one instant.
        ticker, earnings_date, timing_str, updated_at, last_validated_at = row
        timing = EarningsTiming(timing_str)
        result[ticker] = (earnings_date, timing, updated_at, last_validated_at)

    conn.close()
    return result


def check_staleness(threshold_days: int = STALENESS_THRESHOLD_DAYS) -> List[Dict]:
    """
    Check for stale earnings data.

    Returns:
        List of dicts with stale ticker info
    """
    conn = connect()
    cursor = conn.cursor()

    future_date = (today_et() + timedelta(days=90)).isoformat()

    cursor.execute(
        """
        SELECT
            ticker,
            earnings_date,
            timing,
            updated_at,
            EXTRACT(EPOCH FROM (now() - updated_at))/86400 as days_stale
        FROM earnings_calendar
        WHERE earnings_date >= CURRENT_DATE
          AND earnings_date <= %s
          AND EXTRACT(EPOCH FROM (now() - updated_at))/86400 > %s
        ORDER BY days_stale DESC
        """,
        (future_date, threshold_days),
    )

    stale = []
    for row in cursor.fetchall():
        ticker, earnings_date, timing, updated_at, days_stale = row
        stale.append(
            {
                "ticker": ticker,
                "earnings_date": earnings_date,
                "timing": timing,
                "updated_at": updated_at,
                "days_stale": days_stale,
            }
        )

    conn.close()
    return stale


def cleanup_duplicate_earnings(
    dry_run: bool = False,
    finnhub=None,
    yahoo_finance=None,
    resolved_this_run: Dict[str, Tuple[date, EarningsTiming]] | None = None,
    window_past_days: int = 14,
    window_future_days: int = 120,
    quarter_window_days: int = DEDUP_QUARTER_WINDOW_DAYS,
) -> List[Dict]:
    """
    Remove duplicate earnings entries for the same ticker within the same quarter.

    Companies report once per quarter (~90 days). When multiple entries exist
    for the same ticker, this removes stale duplicates using three rules:

    1. Confirmed beats unconfirmed: if a confirmed entry exists, remove any
       unconfirmed entry within 90 days of it (stale echo of the same event).
    2. Confirmed-vs-confirmed tiebreak: if two confirmed entries are within
       30 days (same event, source disagreement), this used to keep whichever
       had the most-recently-touched `updated_at` — but that field gets bumped
       by routine revalidation unrelated to correctness (any confirm-unchanged
       pass touches it), so "most recent" is not evidence of "most correct".
       Fixed 2026-07-27 after this exact defect flipped PYPL's earnings date:
       Finnhub carried two internally-inconsistent confirmed entries, and an
       unrelated revalidation touch on the wrong row won the old tiebreak.
       Now requires either (a) `resolved_this_run` to already carry a
       no-conflict Yahoo+Finnhub consensus for this ticker from earlier in the
       same sync — the main loop's `validate_earnings_date()` result is reused
       as-is rather than re-derived, or (b) if not, a FRESH two-source check
       (Finnhub + Yahoo Finance, refetched live, not cached) to agree with each
       other before resolving the tie. If a candidate matches the resolved/
       corroborated date, that one wins and the other(s) are removed. If
       neither is available, or the fresh check disagrees, NEITHER candidate
       is removed — the conflict is logged for review instead of guessed at.
       (Added 2026-08-06: relying solely on (b) meant a ticker changed earlier
       in the same run — already cross-validated once — could still get stuck
       "needs review" if Finnhub's rate limit was exhausted by the time dedup
       ran, or if the ticker's event had already passed and Yahoo's "next
       earnings date" lookup had rolled forward past it. Reusing (a) avoids
       both failure modes for same-run changes.)
    3. Unconfirmed-vs-unconfirmed: if no confirmed entry exists, keep the
       most recently updated one.

    Two confirmed entries >30 days apart are treated as different quarters
    and both are kept.

    Args:
        dry_run: If True, report duplicates without deleting
        finnhub: FinnhubAPI client, used only to corroborate confirmed-vs-confirmed
            ties (rule 2) when resolved_this_run has no answer. If None, such
            ties fall through to being left unresolved and flagged.
        yahoo_finance: YahooFinanceEarnings client, same purpose as finnhub above.
        resolved_this_run: Map of ticker -> (date, timing) already cross-validated
            with no conflict earlier in the current sync run (see SyncStats.
            resolved_this_run). Checked before falling back to a fresh corroboration
            call for rule 2. None (the default, e.g. the standalone --cleanup-dupes
            CLI path which has no "main loop" this run) skips straight to (b).
        window_past_days: How many days before today to scan (default 14, matching
            the routine daily-sync window). A pair entirely older than this is
            invisible to the default call and ages out permanently — a wider
            one-off pass (e.g. window_past_days=35) is how a stale-duplicate
            backlog older than the routine window gets reached (Aug 30 2026 audit
            found 39 such pairs, 18-27 days old, unreachable by the default).
        window_future_days: How many days ahead of today to scan (default 120).
        quarter_window_days: Max days apart for rules 1 and 3 to treat two entries
            as one event (default 90). Must be at most 45 when window_past_days
            exceeds 180 -- see check_scan_windows.

    Returns:
        List of dicts describing each removed entry

    Raises:
        ValueError: a wide scan with the quarter window left at full width.
    """
    check_scan_windows(window_past_days, quarter_window_days)
    resolved_this_run = resolved_this_run or {}
    conn = connect()
    try:
        cursor = conn.cursor()

        # Past window catches recently-reported earnings that still have stale future dupes.
        cutoff_past = (today_et() - timedelta(days=window_past_days)).isoformat()
        cutoff_future = (today_et() + timedelta(days=window_future_days)).isoformat()

        # Find tickers with multiple entries in the window
        cursor.execute(
            """
            SELECT ticker, COUNT(*) as cnt
            FROM earnings_calendar
            WHERE earnings_date >= %s AND earnings_date <= %s
            GROUP BY ticker
            -- Repeat the aggregate: SQLite allows a SELECT alias in HAVING,
            -- Postgres does not (column "cnt" does not exist).
            HAVING COUNT(*) > 1
            """,
            (cutoff_past, cutoff_future),
        )
        dupes = cursor.fetchall()

        if not dupes:
            logger.info("No duplicate earnings entries found.")
            return []

        logger.info(f"Found {len(dupes)} tickers with multiple entries in window")

        removed = []
        needs_review = []

        # Tighter window for confirmed-vs-confirmed (same event, source disagreement)
        CONFIRMED_DEDUP_DAYS = 30

        def _parse_date(d):
            return datetime.strptime(d, "%Y-%m-%d").date() if isinstance(d, str) else d

        def _log_removal(ticker, entry, keeper, days_apart, is_dry_run, reason="confirmed beats unconfirmed"):
            prefix = "[DRY RUN] " if is_dry_run else ""
            verb = "would remove" if is_dry_run else "removed"
            logger.info(
                f"  {prefix}{ticker}: {verb} {entry['date']} "
                f"({entry['timing']}, confirmed={entry['confirmed']}) "
                f"— keeping {keeper['date']} (confirmed={keeper['confirmed']}, "
                f"{days_apart}d apart, resolved via {reason})"
            )

        def _log_needs_review(ticker, candidates, reason):
            dates = ", ".join(f"{c['date']} ({c['timing']})" for c in candidates)
            logger.warning(
                f"  ⏭️  {ticker}: NEEDS REVIEW — confirmed entries conflict "
                f"[{dates}] — {reason}. Leaving both, will retry next sync."
            )

        def _corroborate(ticker, candidate_dates):
            """
            Fresh, uncached two-source check to break a confirmed-vs-confirmed tie.

            Returns the corroborated date (as a date object) if Finnhub and Yahoo
            Finance, refetched right now, independently agree with each other —
            regardless of whether that date matches either existing candidate.
            Returns None if either source fails or they disagree (unresolved).

            Both lookups search near candidate_dates rather than asking either
            source "what's next" (get_next_earnings_date / horizon="3month" from
            today). That forward-looking question can never confirm a candidate
            that's already at or before today: once the event has passed, "next"
            has already rolled forward to the FOLLOWING quarter and will never
            match, no matter how many times the sync retries (found 2026-08-06:
            AXON/SKYT/ABTC clusters where both candidates were already ≤ today —
            every retry hit the same "unavailable or still disagrees" dead end).
            """
            if finnhub is None or yahoo_finance is None:
                return None

            yf_result = yahoo_finance.get_earnings_date_near(ticker, candidate_dates)
            if yf_result.is_err:
                return None
            yf_date, _ = yf_result.value

            window_start = min(candidate_dates) - timedelta(days=3)
            window_end = max(candidate_dates) + timedelta(days=3)
            fh_result = finnhub.get_earnings_calendar(
                symbol=ticker, from_date=window_start, to_date=window_end
            )
            if fh_result.is_err or not fh_result.value:
                return None
            _, fh_date, _ = min(
                fh_result.value,
                key=lambda entry: min(abs((entry[1] - ref).days) for ref in candidate_dates),
            )

            if yf_date == fh_date:
                return yf_date
            return None

        for ticker, _count in dupes:
            cursor.execute(
                """
                SELECT ticker, earnings_date, timing, confirmed, updated_at
                FROM earnings_calendar
                WHERE ticker = %s AND earnings_date >= %s AND earnings_date <= %s
                ORDER BY earnings_date
                """,
                (ticker, cutoff_past, cutoff_future),
            )
            rows = cursor.fetchall()

            entries = []
            for row in rows:
                # earnings_date is a real date column now. `date` stays an ISO
                # STRING deliberately: it is the reporting contract -- printed
                # by /maintenance and JSON-serialised -- and a datetime.date is
                # not JSON-serialisable. `date_obj` is the typed one for the
                # arithmetic below.
                entries.append({
                    "date": row[1].isoformat(),
                    "date_obj": row[1],
                    "timing": row[2],
                    "confirmed": row[3],
                    "updated_at": row[4],
                })

            confirmed = [e for e in entries if e["confirmed"]]
            unconfirmed = [e for e in entries if not e["confirmed"]]
            to_remove = set()  # indices into entries list
            # Deferred Rule 1 details — populated after Rule 2 determines which
            # confirmed entries actually survive, so the logged "keeper" is accurate.
            rule1_pending: List[Tuple[int, int]] = []  # (unconfirmed_idx, confirmed_idx)

            # Rule 1: Mark unconfirmed entries near a confirmed entry (stale echoes)
            for i, uc in enumerate(entries):
                if uc["confirmed"]:
                    continue
                for j, c in enumerate(entries):
                    if not c["confirmed"]:
                        continue
                    days_apart = abs((uc["date_obj"] - c["date_obj"]).days)
                    if days_apart <= quarter_window_days:
                        to_remove.add(i)
                        rule1_pending.append((i, j))
                        break

            # Rule 2: Dedup confirmed entries only if within 30 days (same event).
            # Cluster mutually-close confirmed entries, then require fresh
            # two-source corroboration to resolve each genuine conflict —
            # never recency, never timing-completeness alone (see docstring).
            kept_confirmed = []
            if len(confirmed) > 1:
                remaining = list(confirmed)
                while remaining:
                    anchor = remaining.pop(0)
                    cluster = [anchor]
                    i = 0
                    while i < len(remaining):
                        if abs((remaining[i]["date_obj"] - anchor["date_obj"]).days) <= CONFIRMED_DEDUP_DAYS:
                            cluster.append(remaining.pop(i))
                        else:
                            i += 1

                    if len(cluster) == 1:
                        kept_confirmed.append(cluster[0])
                        continue

                    winner = None
                    reason_label = "fresh corroboration"
                    corroborated_date = None

                    run_resolution = resolved_this_run.get(ticker)
                    if run_resolution is not None:
                        resolved_date, _resolved_timing = run_resolution
                        for c in cluster:
                            if c["date_obj"] == resolved_date:
                                winner = c
                                reason_label = "this run's cross-validated consensus"
                                break

                    if winner is None:
                        corroborated_date = _corroborate(ticker, [c["date_obj"] for c in cluster])
                        if corroborated_date is not None:
                            for c in cluster:
                                if c["date_obj"] == corroborated_date:
                                    winner = c
                                    break

                    if winner is None:
                        reason = (
                            "corroborated date matches neither existing entry"
                            if corroborated_date is not None
                            else "fresh Finnhub/Yahoo check unavailable or still disagrees"
                        )
                        _log_needs_review(ticker, cluster, reason)
                        needs_review.append({
                            "ticker": ticker,
                            "candidates": [
                                {"date": c["date"], "timing": c["timing"]} for c in cluster
                            ],
                            "reason": reason,
                        })
                        kept_confirmed.extend(cluster)
                        continue

                    kept_confirmed.append(winner)
                    for c in cluster:
                        if c is winner:
                            continue
                        c_idx = entries.index(c)
                        to_remove.add(c_idx)
                        days_apart = abs((c["date_obj"] - winner["date_obj"]).days)
                        detail = {
                            "ticker": ticker,
                            "removed_date": c["date"],
                            "removed_timing": c["timing"],
                            "removed_confirmed": c["confirmed"],
                            "kept_date": winner["date"],
                            "kept_timing": winner["timing"],
                            "kept_confirmed": winner["confirmed"],
                            "days_apart": days_apart,
                        }
                        removed.append(detail)
                        _log_removal(ticker, c, winner, days_apart, dry_run, reason=reason_label)
            else:
                kept_confirmed = list(confirmed)

            # Now resolve Rule 1 details: find the actual surviving confirmed keeper
            # for each removed unconfirmed entry, so the audit trail is accurate.
            for uc_idx, original_c_idx in rule1_pending:
                uc = entries[uc_idx]
                original_c = entries[original_c_idx]

                # If the original confirmed match survived Rule 2, use it
                if original_c_idx not in to_remove:
                    keeper = original_c
                else:
                    # Original was deduped by Rule 2 — find the nearest surviving confirmed
                    keeper = None
                    best_gap = None
                    for kc in kept_confirmed:
                        gap = abs((uc["date_obj"] - kc["date_obj"]).days)
                        if best_gap is None or gap < best_gap:
                            best_gap = gap
                            keeper = kc
                    if keeper is None:
                        keeper = original_c  # fallback (shouldn't happen)

                days_apart = abs((uc["date_obj"] - keeper["date_obj"]).days)
                detail = {
                    "ticker": ticker,
                    "removed_date": uc["date"],
                    "removed_timing": uc["timing"],
                    "removed_confirmed": uc["confirmed"],
                    "kept_date": keeper["date"],
                    "kept_timing": keeper["timing"],
                    "kept_confirmed": keeper["confirmed"],
                    "days_apart": days_apart,
                }
                removed.append(detail)
                _log_removal(ticker, uc, keeper, days_apart, dry_run, reason="confirmed beats unconfirmed")

            # Rule 3: Multiple unconfirmed with no confirmed sibling — keep newest
            remaining_unconfirmed = [
                e for e in unconfirmed if entries.index(e) not in to_remove
            ]
            if len(remaining_unconfirmed) > 1:
                # Sort by updated_at descending, keep first
                sorted_uc = sorted(
                    remaining_unconfirmed,
                    key=lambda e: e["updated_at"] or "",
                    reverse=True,
                )
                keeper = sorted_uc[0]
                for uc in sorted_uc[1:]:
                    uc_idx = entries.index(uc)
                    days_apart = abs((uc["date_obj"] - keeper["date_obj"]).days)
                    if days_apart <= quarter_window_days:
                        to_remove.add(uc_idx)
                        detail = {
                            "ticker": ticker,
                            "removed_date": uc["date"],
                            "removed_timing": uc["timing"],
                            "removed_confirmed": uc["confirmed"],
                            "kept_date": keeper["date"],
                            "kept_timing": keeper["timing"],
                            "kept_confirmed": keeper["confirmed"],
                            "days_apart": days_apart,
                        }
                        removed.append(detail)
                        _log_removal(ticker, uc, keeper, days_apart, dry_run, reason="recency (both unconfirmed)")

            # Execute deletions
            if not dry_run:
                for i in to_remove:
                    entry = entries[i]
                    cursor.execute(
                        "DELETE FROM earnings_calendar WHERE ticker = %s AND earnings_date = %s",
                        (ticker, entry["date"]),
                    )

        if not dry_run and removed:
            conn.commit()

        if needs_review:
            logger.warning(
                f"\n⏭️  {len(needs_review)} ticker(s) have unresolved confirmed-vs-confirmed "
                f"conflicts — left untouched, will retry next sync: "
                f"{', '.join(r['ticker'] for r in needs_review)}"
            )

        return removed
    finally:
        conn.close()


def _sync_via_yahoo_fallback(
    stats: "SyncStats",
    ticker_filter: Set[str] | None = None,
) -> "SyncStats":
    """
    Yahoo Finance per-ticker check used when the Finnhub bulk fetch fails.

    Iterates DB tickers with upcoming earnings (next 90 days) and asks Yahoo
    for each. Respects ticker_filter (focused mode) to avoid crawling thousands
    of tickers.

    ⚠️ REPORT ONLY -- it never writes. With Finnhub down there is one source,
    and a date change needs two to agree. Until 2026-09-13 this inserted
    Yahoo's date whenever it differed, keyed on (ticker, earnings_date), so a
    different date became a second row: a single-source duplicate quarter, the
    mechanism backfill_historical's 15-day guard was added to stop. A
    disagreement is now NEEDS REVIEW, and the next sync with Finnhub up settles
    it through the corroborated path.
    """
    yahoo = YahooFinanceEarnings()
    db_dates = get_database_dates(ticker_filter=ticker_filter)

    today = today_et()
    cutoff = today + timedelta(days=90)
    stale_tickers = [
        ticker for ticker, dates in db_dates.items()
        if today <= dates[0] <= cutoff
    ]

    logger.info(f"Yahoo Finance fallback: checking {len(stale_tickers)} tickers with upcoming earnings")

    for ticker in stale_tickers:
        try:
            yf_result = yahoo.get_next_earnings_date(ticker)
            if yf_result.is_err:
                logger.debug(f"{ticker}: Yahoo Finance also failed: {yf_result.error}")
                continue

            yf_date, yf_timing = yf_result.value
            existing_date = db_dates.get(ticker, (None,))[0]

            if yf_date != existing_date:
                stats.add_review(
                    ticker, existing_date,
                    f"Yahoo Finance {yf_date} ({yf_timing.value}), Finnhub unavailable",
                    conflict=True,
                )
            else:
                stats.unchanged_dates += 1
        except Exception as e:
            logger.debug(f"{ticker}: Yahoo Finance fallback error: {e}")

    logger.info(
        f"Yahoo Finance fallback complete: {stats.unchanged_dates} agree, "
        f"{stats.conflicts_detected} need review (nothing written)"
    )
    return stats


def sync_earnings_calendar(
    validator: EarningsDateValidator,
    earnings_repo: EarningsRepository,
    finnhub: FinnhubAPI,
    horizon: str = "3month",
    dry_run: bool = False,
    focused: bool = True,
) -> SyncStats:
    """
    Synchronize earnings calendar with latest data.

    Args:
        validator: Earnings date validator
        earnings_repo: Earnings repository
        finnhub: Finnhub API client
        horizon: Time horizon (3month, 6month, 12month)
        dry_run: If True, don't update database
        focused: If True (default), only sync tickers we've actually traded.
                 Pass False (--all flag) to sync all tickers in the calendar.

    Returns:
        SyncStats with results
    """
    stats = SyncStats()

    # Get current database state
    logger.info("Loading current database state...")
    ticker_filter: Set[str] | None = None
    if focused:
        ticker_filter = get_focused_tickers()
        logger.info(
            f"Focused mode: targeting {len(ticker_filter)} traded tickers "
            f"(use --all to sync full calendar)"
        )
    db_dates = get_database_dates(ticker_filter=ticker_filter)
    logger.info(f"Found {len(db_dates)} existing earnings dates in database")

    # Fetch full calendar from Finnhub
    logger.info(f"Fetching earnings calendar from Finnhub (horizon={horizon})...")
    calendar_result = finnhub.get_earnings_calendar(horizon=horizon)

    if calendar_result.is_err:
        logger.error(f"Failed to fetch calendar: {calendar_result.error}")
        logger.warning("Finnhub unavailable — checking stored dates against Yahoo Finance (report only)")
        return _sync_via_yahoo_fallback(stats=stats, ticker_filter=ticker_filter)

    calendar = calendar_result.value
    logger.info(f"✓ Fetched {len(calendar)} earnings events from Finnhub")

    # Group by ticker (in case multiple entries per ticker)
    ticker_map: Dict[str, List[Tuple[date, EarningsTiming]]] = defaultdict(list)
    for ticker, earnings_date, timing in calendar:
        ticker_map[ticker].append((earnings_date, timing))

    db_tickers = set(db_dates.keys())
    finnhub_tickers = set(ticker_map.keys())
    if focused and ticker_filter is not None:
        # Traded tickers found in the Finnhub calendar — including names whose
        # previous-quarter row already aged out of the DB lookup window. Those
        # hit the "New ticker" branch below and get re-added; intersecting with
        # db_tickers instead made that branch unreachable and silently dropped
        # any traded ticker with no in-window row (NFLX Jul 2026).
        tickers_to_process = ticker_filter & finnhub_tickers
    else:
        # --all mode: only process tickers already in the database to avoid
        # adding thousands of unwanted tickers from the full calendar
        tickers_to_process = db_tickers & finnhub_tickers

    logger.info("\nFiltering tickers:")
    logger.info(f"  • Database: {len(db_tickers)} tickers")
    logger.info(f"  • Finnhub calendar: {len(finnhub_tickers)} tickers")
    logger.info(f"  • Intersection (to process): {len(tickers_to_process)} tickers")

    # Warn about database tickers not in Finnhub
    missing_from_finnhub = db_tickers - finnhub_tickers
    if missing_from_finnhub:
        logger.warning(f"  ⚠️  {len(missing_from_finnhub)} DB tickers not found in Finnhub calendar")
        if len(missing_from_finnhub) <= 10:
            logger.warning(f"     Missing: {', '.join(sorted(missing_from_finnhub))}")

    # Process filtered tickers with progress bar
    for ticker in tqdm(
        sorted(tickers_to_process),
        desc="Syncing tickers",
        unit="ticker",
        disable=False
    ):
        stats.tickers_processed.add(ticker)

        # Get earliest earnings date for this ticker
        dates = sorted(ticker_map[ticker], key=lambda x: x[0])
        finnhub_date, finnhub_timing = dates[0]

        # Check if ticker exists in database
        if ticker in db_dates:
            db_date, db_timing, db_updated_at, last_validated_at = db_dates[ticker]
            days_stale = (datetime.now(timezone.utc) - db_updated_at).days

            # Check if date changed
            if finnhub_date != db_date or finnhub_timing != db_timing:
                logger.info(
                    f"\n{'='*70}\n"
                    f"CHANGE DETECTED: {ticker}\n"
                    f"  Database: {db_date} ({db_timing.value}) [updated {days_stale}d ago]\n"
                    f"  Finnhub: {finnhub_date} ({finnhub_timing.value})\n"
                    f"{'='*70}"
                )

                # NOTE: `should_skip_validation` (recency of the LAST validation) used
                # to gate a fast path here that trusted Finnhub's bulk date directly,
                # with no cross-check, whenever the ticker had been validated recently.
                # That's backwards: a recent validation confirms the OLD date was right;
                # it says nothing about whether Finnhub's NEW, DIFFERENT date is trustworthy.
                # Removed 2026-07-27 after this exact path re-corrupted PYPL's earnings
                # date within the same day it was fixed — Finnhub's bulk calendar itself
                # carried two conflicting entries for PYPL, and this fast path blindly
                # took whichever sorted first, with zero verification. Recency-based
                # skipping remains safe (and still used) for the *unchanged* case below,
                # where skipping just avoids a redundant re-check of a value that isn't
                # changing — it was never safe for a value that IS changing.
                # Cross-validate with Yahoo Finance — always, regardless of how
                # recently this ticker was last validated (see note above).
                # Reuse the bulk Finnhub date already fetched above (finnhub_date)
                # instead of a redundant live per-ticker Finnhub call — making
                # ~1 extra Finnhub call per CHANGE DETECTED ticker was what
                # exhausted the rate limit mid-run (root-caused 2026-08-24).
                result = validator.validate_earnings_date(
                    ticker, known_finnhub_date=(finnhub_date, finnhub_timing)
                )

                if result.is_ok:
                    validation = result.value

                    # Not has_conflict: with Yahoo silent, Finnhub's date alone has
                    # nothing to conflict with, and this branch used to write it.
                    if not validation.corroborated:
                        stats.review(ticker, db_date, validation)
                        continue

                    consensus_date = validation.consensus_date
                    consensus_timing = validation.consensus_timing

                    logger.info(
                        f"  ✓ Consensus: {consensus_date} ({consensus_timing.value})"
                    )
                    stats.resolved_this_run[ticker] = (consensus_date, consensus_timing)

                    if (consensus_date, consensus_timing) == (db_date, db_timing):
                        # Finnhub differed but Yahoo backs the stored row. Counted as an
                        # update until 2026-09-15: 67 "Updated dates", each listed as
                        # "Timing changed", on a run that changed nothing.
                        if not dry_run:
                            save_result = earnings_repo.save_earnings_event(
                                ticker=ticker, earnings_date=db_date, timing=db_timing,
                            )
                            if save_result.is_err:
                                logger.error(f"  ✗ Failed to record validation: {save_result.error}")
                                stats.errors += 1
                                continue
                        logger.info("  = Stored date confirmed")
                        stats.unchanged_dates += 1
                        continue

                    # Update database
                    if not dry_run:
                        save_result = earnings_repo.save_earnings_event(
                            ticker=ticker,
                            earnings_date=consensus_date,
                            timing=consensus_timing,
                        )
                        if save_result.is_ok:
                            stats.updated_dates += 1
                            stats.changes.append(
                                {
                                    "ticker": ticker,
                                    "old_date": db_date,
                                    "new_date": consensus_date,
                                    "timing": consensus_timing,
                                    "reason": "Date changed"
                                    if db_date != consensus_date
                                    else "Timing changed",
                                }
                            )
                            logger.info("  💾 Updated database")
                        else:
                            logger.error(f"  ✗ Failed to update: {save_result.error}")
                            stats.errors += 1
                    else:
                        stats.updated_dates += 1
                        logger.info("  🔍 DRY RUN - Would update database")
                else:
                    logger.error(f"  ✗ Validation failed: {result.error}")
                    stats.errors += 1
            else:
                # Date unchanged, but check if stale
                if days_stale > STALENESS_THRESHOLD_DAYS:
                    # Check if we can skip validation (validated within 48 hours)
                    if should_skip_validation(last_validated_at):
                        hours_ago = (datetime.now(timezone.utc) - last_validated_at).total_seconds() / 3600
                        logger.debug(
                            f"{ticker}: Stale ({days_stale}d) but validated {hours_ago:.1f}h ago - skipping"
                        )
                        stats.validation_skipped += 1
                        stats.unchanged_dates += 1
                    else:
                        logger.debug(
                            f"{ticker}: Unchanged but stale ({days_stale}d) - re-validating..."
                        )

                        # Re-validate to ensure still accurate (reuse bulk Finnhub date)
                        result = validator.validate_earnings_date(
                            ticker, known_finnhub_date=(finnhub_date, finnhub_timing)
                        )
                        if result.is_ok:
                            validation = result.value
                            if not validation.corroborated:
                                stats.review(ticker, db_date, validation)
                            elif validation.consensus_date != db_date:
                                # Date changed according to validation
                                logger.info(
                                    f"\n{'='*70}\n"
                                    f"STALE DATA CORRECTION: {ticker}\n"
                                    f"  Database: {db_date} ({db_timing.value}) [{days_stale}d stale]\n"
                                    f"  Validated: {validation.consensus_date} ({validation.consensus_timing.value})\n"
                                    f"{'='*70}"
                                )
                                stats.resolved_this_run[ticker] = (
                                    validation.consensus_date, validation.consensus_timing
                                )

                                if not dry_run:
                                    save_result = earnings_repo.save_earnings_event(
                                        ticker=ticker,
                                        earnings_date=validation.consensus_date,
                                        timing=validation.consensus_timing,
                                    )
                                    if save_result.is_ok:
                                        stats.updated_dates += 1
                                        stats.changes.append(
                                            {
                                                "ticker": ticker,
                                                "old_date": db_date,
                                                "new_date": validation.consensus_date,
                                                "timing": validation.consensus_timing,
                                                "reason": "Stale data corrected",
                                            }
                                        )
                                    else:
                                        logger.error(f"  ✗ Failed to update: {save_result.error}")
                                        stats.errors += 1
                                else:
                                    stats.updated_dates += 1
                            else:
                                # Date confirmed unchanged, update validation timestamp
                                if not dry_run:
                                    save_result = earnings_repo.save_earnings_event(
                                        ticker=ticker,
                                        earnings_date=db_date,
                                        timing=db_timing,
                                    )
                                    if save_result.is_err:
                                        logger.error(
                                            f"  ✗ Failed to record validation: {save_result.error}"
                                        )
                                        stats.errors += 1
                                stats.unchanged_dates += 1
                        else:
                            logger.error(f"  ✗ Validation failed: {result.error}")
                            stats.errors += 1
                else:
                    stats.unchanged_dates += 1
        else:
            # New ticker
            logger.info(
                f"\n{'='*70}\n"
                f"NEW EARNINGS: {ticker}\n"
                f"  Finnhub: {finnhub_date} ({finnhub_timing.value})\n"
                f"{'='*70}"
            )

            # Validate with Yahoo Finance (reuse bulk Finnhub date)
            result = validator.validate_earnings_date(
                ticker, known_finnhub_date=(finnhub_date, finnhub_timing)
            )

            if result.is_ok:
                validation = result.value

                # A new row needs both sources too: a lone Finnhub date was saved as
                # confirmed, and a traded ticker Yahoo has no date for is usually one
                # that stopped reporting (SKYT, delisted 2026-07-31, stored 2026-11-03).
                if not validation.corroborated:
                    stats.review(ticker, None, validation)
                    continue

                logger.info(
                    f"  ✓ Consensus: {validation.consensus_date} ({validation.consensus_timing.value})"
                )

                # Save to database
                if not dry_run:
                    save_result = earnings_repo.save_earnings_event(
                        ticker=ticker,
                        earnings_date=validation.consensus_date,
                        timing=validation.consensus_timing,
                    )
                    if save_result.is_ok:
                        stats.new_dates += 1
                        logger.info("  💾 Saved to database")
                    else:
                        logger.error(f"  ✗ Failed to save: {save_result.error}")
                        stats.errors += 1
                else:
                    stats.new_dates += 1
                    logger.info("  🔍 DRY RUN - Would save to database")
            else:
                logger.error(f"  ✗ Validation failed: {result.error}")
                stats.errors += 1

    # Dedup after sync — remove stale duplicate entries
    logger.info("\nRunning post-sync deduplication...")
    dedup_results = cleanup_duplicate_earnings(
        dry_run=dry_run,
        finnhub=validator.finnhub, yahoo_finance=validator.yahoo_finance,
        resolved_this_run=stats.resolved_this_run,
    )
    stats.dupes_removed = len(dedup_results)
    stats.dedup_details = dedup_results

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Automated earnings calendar synchronization"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write changes to the database (default: report what would change)",
    )
    parser.add_argument(
        "--horizon",
        default="3month",
        choices=["3month", "6month", "12month"],
        help="Time horizon for calendar fetch (default: 3month)",
    )
    parser.add_argument(
        "--check-staleness",
        "-s",
        action="store_true",
        help="Check for stale data and exit",
    )
    parser.add_argument(
        "--cleanup-dupes",
        action="store_true",
        help="Remove duplicate earnings entries (same ticker, same quarter) and exit",
    )
    parser.add_argument(
        "--window-past-days",
        type=int,
        default=14,
        help=(
            "How far back --cleanup-dupes scans, in days (default: 14, matching the "
            "routine sync window). A duplicate pair whose dates are BOTH older than "
            "this is never examined and stays in the database permanently, so "
            "clearing a historical backlog needs an explicit wider pass "
            "with --quarter-window-days). Above 180 days the quarter window must be "
            "45 or less, or real consecutive quarters (~90 days apart) are deleted "
            "as duplicates."
        ),
    )
    parser.add_argument(
        "--quarter-window-days",
        type=int,
        default=DEDUP_QUARTER_WINDOW_DAYS,
        help=(
            f"Max days apart for --cleanup-dupes to treat two entries as one event "
            f"(default: {DEDUP_QUARTER_WINDOW_DAYS}). Required at "
            f"{WIDE_SCAN_MAX_QUARTER_WINDOW_DAYS} or less when --window-past-days "
            f"exceeds {WIDE_SCAN_PAST_DAYS}."
        ),
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=int,
        default=STALENESS_THRESHOLD_DAYS,
        help=f"Staleness threshold in days (default: {STALENESS_THRESHOLD_DAYS})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sync all tickers in the calendar (default: only traded tickers)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level",
    )

    args = parser.parse_args()
    dry_run = not args.execute
    try:
        check_scan_windows(args.window_past_days, args.quarter_window_days)
    except ValueError as e:
        parser.error(f"{e} (--window-past-days / --quarter-window-days)")

    # Setup logging
    setup_logging(level=args.log_level)

    # `db_path` was removed at the tail of the Postgres migration: every
    # function here already called common.db.connect(), which reads PG*
    # from the environment, so the parameter was threaded through and
    # never used. It defaulted to "data/ivcrush.db" -- a file that no
    # longer exists.

    # Check staleness only
    if args.check_staleness:
        logger.info("Checking for stale earnings data...")
        stale = check_staleness(threshold_days=args.threshold)

        if stale:
            logger.warning(
                f"\n⚠️  WARNING: {len(stale)} tickers with data >{args.threshold} days stale:"
            )
            for item in stale:
                logger.warning(
                    f"  - {item['ticker']}: {item['earnings_date']} "
                    f"({item['timing']}) - {item['days_stale']:.1f} days stale "
                    f"(last updated: {item['updated_at']})"
                )
            sys.exit(1)
        else:
            logger.info(f"✓ All upcoming earnings data is fresh (<{args.threshold} days)")
            sys.exit(0)

    # Cleanup duplicates only
    if args.cleanup_dupes:
        logger.info("=" * 80)
        logger.info("EARNINGS CALENDAR DEDUP")
        logger.info("=" * 80)
        logger.info(f"Mode: {'DRY RUN (pass --execute to delete)' if dry_run else 'LIVE CLEANUP'}")
        logger.info(f"Quarter window: {args.quarter_window_days} days")
        logger.info(f"Scan window: {args.window_past_days}d back .. 120d forward")
        logger.info("=" * 80)

        _finnhub = FinnhubAPI(
            api_key=Config.from_env().api.finnhub_key,
            rate_limiter=create_finnhub_limiter(),
        )
        _yahoo_finance = YahooFinanceEarnings()
        removed = cleanup_duplicate_earnings(
            dry_run=dry_run,
            finnhub=_finnhub, yahoo_finance=_yahoo_finance,
            window_past_days=args.window_past_days,
            quarter_window_days=args.quarter_window_days,
        )

        if removed:
            logger.info(f"\n{'DRY RUN: Would remove' if dry_run else 'Removed'} "
                        f"{len(removed)} duplicate entries")
        else:
            logger.info("\n✓ No duplicates found")

        sys.exit(0)

    # Regular sync
    logger.info("=" * 80)
    logger.info("EARNINGS CALENDAR SYNC")
    logger.info("=" * 80)
    logger.info(f"Mode: {'DRY RUN (pass --execute to write)' if dry_run else 'LIVE SYNC'}")
    logger.info(f"Horizon: {args.horizon}")
    logger.info(f"Time: {now_et().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info("=" * 80)

    # Initialize data sources
    finnhub = FinnhubAPI(
        api_key=Config.from_env().api.finnhub_key,
        rate_limiter=create_finnhub_limiter(),
    )
    yahoo_finance = YahooFinanceEarnings()

    # Initialize validator
    validator = EarningsDateValidator(
        finnhub=finnhub, yahoo_finance=yahoo_finance
    )

    # Initialize repository. Takes no path since the Postgres port — the rest
    # of this script is still SQLite and is ported in Task 4.
    earnings_repo = EarningsRepository()

    # Run sync
    stats = sync_earnings_calendar(
        validator=validator,
        earnings_repo=earnings_repo,
        finnhub=finnhub,
        horizon=args.horizon,
        dry_run=dry_run,
        focused=not args.all,
    )

    # Print summary
    stats.log_summary()

    if dry_run:
        logger.info("\n🔍 DRY RUN - No changes made to database (pass --execute to write)")

    # Exit with error code if there were errors
    sys.exit(1 if stats.errors > 0 else 0)


if __name__ == "__main__":
    main()
