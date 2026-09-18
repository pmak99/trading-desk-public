"""Morning digest (07:40 ET) — top VRP candidates for the week, via Telegram.

Replaces 5.0's morning_digest handler. That handler called 5.0's own
calculate_vrp / calculate_score / generate_strategies, i.e. the simplified
reimplementation CLAUDE.md declares non-authoritative — so the ranked list
arriving on the phone each morning was produced by the engine the docs say not
to trust for trade decisions. This job runs the same code path `/whisper` does
and renders its output verbatim.

It differs from `/whisper` in ONE respect, deliberately: which week it covers.
See _weeks_to_try — /whisper always looks a week ahead, which is right for a
planning command and wrong for a daily alert.

Run:
    PYTHONPATH=<2.0> python -m scripts.jobs.morning_digest [--dry-run] [--force]
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

# Import everything under the `scripts.` prefix. 2.0/scripts/scan is reachable
# as BOTH `scan.*` (the CLI entry point puts 2.0/scripts on sys.path) and
# `scripts.scan.*`, and Python treats those as two distinct module objects —
# verified: `scan.quality_scorer is scripts.scan.quality_scorer` is False.
# Mixing prefixes makes unittest.mock.patch silently patch the wrong copy.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.timezone import now_et  # noqa: E402
from scripts.jobs.runner import run_job  # noqa: E402
from scripts.scan.earnings_fetcher import ensure_tickers_in_db  # noqa: E402
from scripts.scan.market_data import get_shared_cache  # noqa: E402
from scripts.scan.quality_scorer import _precalculate_quality_scores  # noqa: E402
from scripts.scan.workflows.whisper_mode import collect_whisper_results  # noqa: E402
from src.application.formatters.telegram import format_digest  # noqa: E402
from src.config.config import Config  # noqa: E402
from src.container import Container  # noqa: E402
from src.infrastructure.data_sources.earnings_whisper_scraper import (  # noqa: E402
    EarningsWhisperScraper,
    get_week_monday,
)
from src.infrastructure.database.migrations import apply_migrations  # noqa: E402
from src.infrastructure.notifications import TelegramSender  # noqa: E402

logger = logging.getLogger(__name__)

JOB_NAME = "morning-digest"
MAX_DIGEST_ROWS = 10

# datetime.weekday(): Mon=0 .. Sun=6. From Friday on, the current week's
# earnings have already reported, so the digest looks ahead to next week.
FIRST_DAY_LOOKING_AHEAD = 4  # Friday

def _build_container() -> Container:
    """The job's startup: migrate (once per run, before any database work), then
    build the engine. Inside run_job for a real run, so a failed migration or an
    edited migration file is a job error that Telegrams."""
    apply_migrations()
    return Container(Config.from_env())


def _weeks_to_try(now: Optional[datetime] = None) -> List[datetime]:
    """Weeks to request, most-wanted first.

    Mon-Thu  -> [this week, next week]
    Fri-Sun  -> [next week, this week]     (this week's earnings are done)

    This DIVERGES from /whisper, deliberately. The scraper's own auto-detect is
    unconditional next-week-first on every day of the week
    (`weeks_to_try = [next_monday, current_monday]` in
    earnings_whisper_scraper.py), which suits a planning command you invoke on
    purpose but makes a daily 07:40 push useless: on Wed 2026-09-09 the digest
    showed one REJECT-liquidity name for the week of Sep 14 while ADBE at 4.9x
    VRP reported the following morning and never appeared at all.

    The second entry is a fallback, not a preference — a genuinely quiet week
    should roll forward rather than produce an error.
    """
    now = now or now_et().replace(tzinfo=None)  # the ET weekday, naive as before
    this_monday = get_week_monday(now)
    next_monday = this_monday + timedelta(days=7)

    if now.weekday() >= FIRST_DAY_LOOKING_AHEAD:
        return [next_monday, this_monday]
    return [this_monday, next_monday]


def _week_tickers(container: Container) -> Tuple[List[str], date]:
    """Anticipated tickers plus the week-Monday they belong to.

    Requests an EXPLICIT week rather than passing week_monday=None: None hands
    the choice to the scraper, which always prefers next week (see
    _weeks_to_try). The shared 6-day cache keeps repeat runs off
    earningswhispers.com's undocumented internal endpoint, which soft-rate-limits
    — and it is keyed per week, so trying two weeks costs at most two lookups.
    """
    scraper = EarningsWhisperScraper(cache=get_shared_cache(container))

    tried: List[str] = []
    for candidate in _weeks_to_try():
        week_str = candidate.strftime("%Y-%m-%d")
        tried.append(week_str)
        result = scraper.get_most_anticipated_earnings(week_monday=week_str)

        # is_err is a property, not a method: `result.is_err()` would be a bound
        # method and therefore always truthy, inverting this check.
        if result.is_err:
            raise RuntimeError(f"EarningsWhispers fetch failed: {result.error}")

        tickers, monday = result.value
        if tickers:
            ensure_tickers_in_db(tickers, container)
            return tickers, monday

    # Every candidate week came back empty. whisper_mode guards the single-week
    # case with `if not tickers: return 1`; the same reasoning applies here, but
    # only once BOTH weeks are exhausted. The scraper can return Ok([]) from its
    # DB fallback or from a cached empty list (the cache check is
    # `if cached_tickers is not None`, which accepts []), so treating one empty
    # week as fatal would fail on a legitimately quiet week — while treating two
    # as normal would let a silent outage render as "No tradeable candidates"
    # and record success, every morning.
    raise RuntimeError(
        f"EarningsWhispers returned no tickers for any of {', '.join(tried)} "
        "— treating as a failure, not a quiet fortnight"
    )


def build_digest_text(container: Container, tickers: List[str],
                      monday: date) -> str:
    """Run the 2.0 scan for the week and render it for Telegram."""
    run = collect_whisper_results(container, tickers, monday,
                                  monday + timedelta(days=6))

    # Ranking is by _quality_score; without this every row scores 0.0 and the
    # digest is silently mis-ordered while still looking plausible.
    tradeable = [r for r in run.results if r.get("is_tradeable")]
    if tradeable:
        _precalculate_quality_scores(tradeable)

    # The scraper returns a datetime, not a date, so a bare isoformat()
    # renders "2026-09-14T00:00:00" in the title.
    week_of = monday.date() if hasattr(monday, "date") else monday

    return format_digest(
        run.results,
        title=f"Morning Digest — week of {week_of.isoformat()}",
        max_rows=MAX_DIGEST_ROWS,
    )


def _render() -> str:
    container = _build_container()
    tickers, monday = _week_tickers(container)
    return build_digest_text(container, tickers, monday)


def _work_once(sender: Optional[TelegramSender]) -> str:
    text = _render()

    if sender is None:
        raise RuntimeError("Telegram not configured; cannot send digest")

    # A False return means Telegram rejected the message. Raise so the runner
    # records an error — a silently-undelivered digest would look like a
    # successful run every morning.
    if not sender.send(text):
        raise RuntimeError("Telegram send returned False")

    return f"sent {len(text)} chars"


def main(argv: Optional[List[str]] = None,
         sender: Optional[TelegramSender] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the digest instead of sending it")
    parser.add_argument("--force", action="store_true",
                        help="clear today's job claim and re-run")
    args = parser.parse_args(argv)

    config = Config.from_env()
    if sender is None and config.telegram.is_configured:
        sender = TelegramSender(config.telegram.bot_token,
                                config.telegram.chat_id)

    # A preview must not touch job state. Routing --dry-run through run_job
    # claimed the day and marked it 'success', so the real 07:30 trigger got
    # LOCK_HELD and no digest was ever sent — while job_status read 'success',
    # which hid it completely.
    if args.dry_run:
        print(_render())
        return 0

    return run_job(
        JOB_NAME,
        lambda: _work_once(sender),
        sender=sender,
        force=args.force,
        push_url=os.getenv("MORNING_DIGEST_PUSH_URL") or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
