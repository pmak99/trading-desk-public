"""record_moves -- append each newly reported quarter to historical_moves.

Why it exists (root-caused 2026-09-13): nothing scheduled recorded a new quarter
for a ticker that already had history. 5.0's weekly_backfill was dropped at the
Postgres migration on the belief that backfill_historical.py replaced it, but
nothing ran that script on a schedule. Coverage since June was 100% for tickers
someone happened to /analyze and ~0% otherwise (Jul/Aug 2026: 22% of past
calendar events had a move), so VRP and TRR baselines went a quarter stale.

Why it is not backfill_historical.py on a timer: that script, run unattended,
(1) runs Yahoo discovery and inserts calendar rows for a ticker with no move
row (until 2026-09-14, whenever its range held <4 events), (2) infers UNKNOWN
timing from the gaps and writes the guess to the calendar as confirmed, and
(3) given a bare ticker, re-derives every calendar
row -- turning a calendar cluster into a move cluster. This job shares its fetch
and arithmetic (scripts/earnings_moves.py) and none of that: it inserts a missing
move row with ON CONFLICT DO NOTHING, never rewrites a move, never calls
discovery, and its one calendar write is a timing tag two sources agree on.

Why it fixes timing itself and stays quiet (2026-09-13, after the first --execute
run): that run recorded 4 moves and Telegrammed 50 "needs review" events -- 45
UNKNOWN timing, 4 stored AMC that Yahoo reports pre-market (DKS 08-25, TIGR 08-26,
RY 08-27, CGNT 09-09), OCC dated 09-09 where Yahoo says 12-17, FCEL/GME on Labor
Day -- and Kuma stayed red because any unrecorded event counted. Nobody would read
that list, and an always-red monitor alarms on nothing. So: what Yahoo and Finnhub
agree on is fixed without a human; what is left goes to the log, and to Telegram
only for tickers that have been traded; Kuma answers only "did the job run".

What it does:

  Candidates  earnings_calendar rows of tickers that already have move rows,
              dated in the last 21 days (not today), with no move row.
  Gates       in order; a failure is LEFT OVER (a leftover) with its reason, or
              PENDING when the next run will simply retry it:
    a  the event date is a trading session, and the reaction session (BMO: the
       event date; anything else: the next session) is complete -- strictly
       before today in ET. Not complete is PENDING: not due yet.
    c  no other calendar row or move row for the ticker within 15 days
       (EARNINGS_SAME_EVENT_DAYS).
    y  Yahoo's reported earnings timestamp (YahooFinanceEarnings.
       get_earnings_date_near) is the stored date. Same BMO/AMC as stored ->
       corroborated. Yahoo unreachable -> PENDING, never recorded uncorroborated.
    r  retag: the stored tag is UNKNOWN/DMH, or Yahoo reads the other BMO/AMC.
       Finnhub's symbol calendar (event -3 .. +3 days) must list exactly the
       stored date with the same BMO/AMC as Yahoo. Then, with --execute,
       UPDATE earnings_calendar SET timing, confirmed=true, updated_at,
       last_validated_at for that one row (guarded on the stored timing) and the
       event goes on through gate a again (the reaction session for the NEW
       timing), then d -- recorded in the same run. Never a date change, an
       insert or a delete. Finnhub blank hour, NODATA, error, any disagreement
       or another date -> leftover. Finnhub paced >= 1.1 s and asked only for
       events that need it; a rate limit or auth refusal stops retagging for the
       rest of the run (those events are leftovers for today, retried tomorrow).
       --skip-yahoo turns y and r off: stored BMO/AMC is trusted (a timing a
       human confirmed), UNKNOWN/DMH is a leftover. The unit never passes it.
    d  Twelve Data's window (event -10 .. +10 days) holds the exact reference and
       reaction bars with volume > 0, and the calculator measured from those two
       bars and no others.
  Budget      one Twelve Data call per ticker, oldest events first, after y and
              r so a refused event spends no credit. The call waits on the
              process's Twelve Data limit (8 in any 60 s, the free tier's minute)
              and a minute limit or 5xx is retried inside it
              (src/infrastructure/api/twelve_data.py); this loop adds no spacing
              of its own. Stops at --budget calls (default 700 of the free tier's
              800/day), at --max-minutes (default 23, so a last fetch's 4 minutes
              of waits and timeouts still ends inside the unit's 30-minute start
              timeout), or at a rate limit that outlasts those retries or
              the day's credits running out -- Twelve Data's, or the host's
              TWELVE_DATA_DAILY_CEILING (780 calls in the UTC day across every
              process, counted by the gate). What is left is PENDING and the
              next run picks it up.
  Output      the full summary on stdout (the job log): every recorded, retagged,
              left-over and pending event with its full reason. With --execute:
              Telegram ONLY when a leftover is on a traded ticker (strategies or
              trade_journal) -- one counts line and one line per traded
              leftover; an Uptime Kuma push to RECORD_MOVES_PUSH_URL, status=up
              with the counts line after a run that finished. A failure is
              reported once, by run_job's Telegram, and pushes nothing; Kuma's
              4-day interval catches a job that stops running.

Report-only unless --execute, like every writer here. A report-only run makes the
Yahoo, Finnhub and Twelve Data calls (to show what would be retagged and recorded)
but does not write, claim the day, send Telegram or push Kuma, and its connection
is read-only. It does not migrate the database either; --execute does, first
(apply_migrations, inside the claimed run).

Run (from 2.0/):
    python -m scripts.jobs.record_moves                         # report only
    python -m scripts.jobs.record_moves --tickers RH CPRT --limit 10
    python -m scripts.jobs.record_moves --execute [--force]     # the scheduled run
"""

import argparse
import html
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

# 2.0/ for the `scripts.` namespace when `--help` runs the file directly; `src`
# and `common` are installed packages.
_ENGINE = str(Path(__file__).resolve().parents[2])
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

from common.constants import EARNINGS_SAME_EVENT_DAYS  # noqa: E402
from common.db import connect  # noqa: E402
from scripts.earnings_moves import (  # noqa: E402
    DailyPrice,
    EarningsEvent,
    EarningsMove,
    EarningsTiming,
    TwelveDataError,
    TwelveDataRateLimited,
    calculate_earnings_move,
    fetch_twelve_data_prices,
    insert_move_if_absent,
)
from scripts.jobs.runner import HOMELAB_CA, run_job, tls_verify  # noqa: E402
from src.config.config import APIConfig, Config  # noqa: E402
from src.domain.market_calendar import (  # noqa: E402
    is_trading_day,
    next_trading_day,
    previous_trading_day,
)
from src.domain.reaction_session import reaction_session  # noqa: E402
from src.infrastructure.database.migrations import apply_migrations  # noqa: E402
from src.infrastructure.notifications import TelegramSender  # noqa: E402

logger = logging.getLogger(__name__)

JOB_NAME = "record-moves"
from common.timezone import today_et  # noqa: E402

LOOKBACK_DAYS = 21            # candidates: calendar events this recent
CLUSTER_DAYS = EARNINGS_SAME_EVENT_DAYS
PRICE_WINDOW_DAYS = 10        # Twelve Data window: event -10 .. +10 days
FINNHUB_WINDOW_DAYS = 3       # Finnhub symbol calendar: event -3 .. +3 days
FINNHUB_SPACING_SEC = 1.1     # Finnhub free tier: 60 calls a minute
DEFAULT_BUDGET = 700          # of the free tier's 800 a day
DEFAULT_MAX_MINUTES = 23      # TimeoutStartSec=1800 less DNS wait, a last fetch, reporting
BRIEF_CHARS = 60              # a leftover's reason on its Telegram line
KUMA_MSG_CHARS = 200

PUSH_URL_ENV = "RECORD_MOVES_PUSH_URL"
PUSH_TIMEOUT_SEC = 20

RECORDED = "RECORDED"
WOULD_RECORD = "WOULD RECORD"
ALREADY_PRESENT = "ALREADY PRESENT"
PENDING = "PENDING"
LEFT = "LEFT OVER"

SESSION_TIMINGS = ("BMO", "AMC")

CANDIDATES_SQL = """
    SELECT e.ticker, e.earnings_date, e.timing,
           ARRAY(SELECT o.earnings_date FROM earnings_calendar o
                  WHERE o.ticker = e.ticker AND o.earnings_date <> e.earnings_date
                    AND abs(o.earnings_date - e.earnings_date) <= %(cluster_days)s
                  ORDER BY 1) AS near_calendar,
           ARRAY(SELECT h.earnings_date FROM historical_moves h
                  WHERE h.ticker = e.ticker AND h.earnings_date <> e.earnings_date
                    AND abs(h.earnings_date - e.earnings_date) <= %(cluster_days)s
                  ORDER BY 1) AS near_moves
    FROM earnings_calendar e
    WHERE e.earnings_date BETWEEN %(today)s::date - %(lookback)s AND %(today)s::date - 1
      AND EXISTS (SELECT 1 FROM historical_moves k WHERE k.ticker = e.ticker)
      AND NOT EXISTS (SELECT 1 FROM historical_moves h
                      WHERE h.ticker = e.ticker AND h.earnings_date = e.earnings_date)
      AND (%(tickers)s::text[] IS NULL OR e.ticker = ANY(%(tickers)s::text[]))
    ORDER BY e.earnings_date, e.ticker
"""

# The one calendar write. Keyed on the primary key, and guarded on the timing
# this run read: a human who fixed the row mid-run keeps their fix.
RETAG_SQL = """
    UPDATE earnings_calendar
       SET timing = %(timing)s, confirmed = true, updated_at = now(), last_validated_at = now()
     WHERE ticker = %(ticker)s AND earnings_date = %(day)s AND timing = %(was)s
"""

# Who hears about a leftover: tickers that have been traded.
TRADED_SQL = """
    SELECT symbol FROM strategies WHERE symbol = ANY(%(tickers)s)
    UNION
    SELECT symbol FROM trade_journal WHERE symbol = ANY(%(tickers)s)
"""


@dataclass(frozen=True)
class Candidate:
    ticker: str
    earnings_date: date
    timing: str                              # as stored (or as retagged): BMO / AMC / DMH / UNKNOWN
    near_calendar: Tuple[date, ...] = ()     # other calendar dates within CLUSTER_DAYS
    near_moves: Tuple[date, ...] = ()        # move dates within CLUSTER_DAYS


@dataclass(frozen=True)
class Sessions:
    reference: date                          # the close the move is measured from
    reaction: date                           # the session that reacted


@dataclass
class Outcome:
    candidate: Candidate
    status: str
    reason: str = ""
    kind: str = ""                           # groups reasons: session, cluster, yahoo, retag, bars, ...
    move: Optional[EarningsMove] = None
    retagged_from: Optional[str] = None      # the stored timing, when this run (would have) retagged it
    short: str = ""                          # the reason in a Telegram line's width

    @property
    def brief(self) -> str:
        text = self.short or self.reason
        return text if len(text) <= BRIEF_CHARS else text[:BRIEF_CHARS - 1] + "…"


@dataclass
class Report:
    today: date
    execute: bool
    outcomes: List[Outcome]
    calls: int = 0
    budget: int = DEFAULT_BUDGET
    stopped: Optional[str] = None            # why Twelve Data fetching stopped early
    yahoo: bool = True                       # was the Yahoo corroboration on?
    retag_stopped: Optional[str] = None      # why retagging stopped early (Finnhub)
    traded: FrozenSet[str] = field(default_factory=frozenset)   # leftover tickers ever traded

    def with_status(self, *statuses: str) -> List[Outcome]:
        return [o for o in self.outcomes if o.status in statuses]

    def leftovers(self) -> List[Outcome]:
        return self.with_status(LEFT)

    def traded_leftovers(self) -> List[Outcome]:
        return [o for o in self.leftovers() if o.candidate.ticker in self.traded]

    def retagged(self) -> List[Outcome]:
        return [o for o in self.outcomes if o.retagged_from is not None]


def _left(c: Candidate, kind: str, reason: str, short: str, **kw) -> Outcome:
    return Outcome(c, LEFT, reason, kind, short=short, **kw)


# --- gates a and c: no network -------------------------------------------------

def screen(c: Candidate, today: date) -> Optional[Outcome]:
    """Gates a and c in order. None when both pass, else the first failure."""
    d = c.earnings_date

    # (a) The event date must itself be a session: a BMO reacts on it and an AMC
    # measures from its close. The shared calculator would otherwise move a
    # closed-day date to the nearest bar and measure the wrong reaction.
    if not is_trading_day(d):
        return _left(c, "session", f"event date {d} ({d:%a}) is not a trading session "
                                   f"-- the calendar date is suspect",
                     "not a trading session; calendar date suspect")
    # BMO reacts on the event date. Anything else reacts no later than the next
    # session, so UNKNOWN waits until even its latest reaction is done. A day, not
    # the session close the calculator waits for: the 06:15 run sees the same.
    reaction = reaction_session(d, c.timing)
    if reaction >= today:
        return Outcome(c, PENDING, f"reaction session {reaction} not complete before {today} (ET)",
                       "not_due")

    # (c) A neighbour within 15 days means one event stored twice or a wrong date.
    if c.near_calendar or c.near_moves:
        parts = []
        if c.near_calendar:
            parts.append("calendar " + ", ".join(map(str, c.near_calendar)))
        if c.near_moves:
            parts.append("moves " + ", ".join(map(str, c.near_moves)))
        near = sorted(c.near_calendar + c.near_moves)
        more = f" +{len(near) - 1}" if len(near) > 1 else ""
        return _left(c, "cluster", f"another row within {CLUSTER_DAYS} days ({'; '.join(parts)}) "
                                   f"-- one event stored twice or a wrong date",
                     f"another row within {CLUSTER_DAYS} days: {near[0]}{more}")
    return None


def sessions_for(c: Candidate) -> Sessions:
    """Reference and reaction sessions. Only a BMO or AMC tag has them."""
    d = c.earnings_date
    if c.timing == "BMO":
        return Sessions(previous_trading_day(d), d)
    if c.timing == "AMC":
        return Sessions(d, next_trading_day(d))
    raise ValueError(f"{c.ticker} {d}: no sessions for timing {c.timing}")


# --- gate d: the bars ----------------------------------------------------------

def check_bars(c: Candidate, sessions: Sessions,
               prices: Sequence[DailyPrice]) -> Union[EarningsMove, Outcome]:
    bars: Dict[date, DailyPrice] = {p.date: p for p in prices}
    for label, day in (("reference", sessions.reference), ("reaction", sessions.reaction)):
        bar = bars.get(day)
        if bar is None:
            return _left(c, "bars", f"no {label} bar for {day} in the Twelve Data window",
                         f"no {label} bar for {day}")
        if not bar.volume or bar.volume <= 0:
            return _left(c, "bars", f"{label} bar {day} has volume {bar.volume}",
                         f"{label} bar {day} has volume {bar.volume}")

    event = EarningsEvent(c.ticker, c.earnings_date,
                          EarningsTiming.BMO if c.timing == "BMO" else EarningsTiming.AMC)
    move = calculate_earnings_move(event, list(prices))
    ref, reaction = bars[sessions.reference], bars[sessions.reaction]
    if move is None or (move.prev_close, move.reaction_open, move.reaction_close) != (
            ref.close, reaction.open, reaction.close):
        between = sorted(b for b in bars if sessions.reference < b < sessions.reaction)
        detail = (f"bars on closed days {', '.join(map(str, between))}" if between
                  else "the calculator measured from other bars")
        return _left(c, "bars", f"price window does not match sessions {sessions.reference} -> "
                                f"{sessions.reaction}: {detail}",
                     f"bars do not match {sessions.reference} -> {sessions.reaction}")
    return move


# --- gate y: Yahoo -------------------------------------------------------------
#
# Why this is on by default rather than optional: the first report-only
# production run (2026-09-13) would have recorded four events, and three of them
# -- DKS 08-25, TIGR 08-26, RY 08-27 -- were stored as AMC with confirmed=true
# while Yahoo's reported timestamps read 07:00, 04:00 and 06:00 ET. Recorded as
# AMC, each measures the session after the reaction: a near-zero move that drags
# the VRP denominator down and inflates VRP -- the dangerous direction. A stored
# BMO/AMC tag is one source; this is the second.

class YahooNoData(Exception):
    """Yahoo returned no earnings history for the ticker."""


class YahooUnavailable(Exception):
    """Yahoo could not be asked (network, rate limit). Retried on the next run."""


_YAHOO = None


def _yahoo_source():
    global _YAHOO
    if _YAHOO is None:
        from src.infrastructure.data_sources.yahoo_finance_earnings import (
            YahooFinanceEarnings,
        )
        _YAHOO = YahooFinanceEarnings()
    return _YAHOO


def yahoo_report_near(ticker: str, day: date) -> Tuple[date, str]:
    """Yahoo's report nearest `day`: (date, 'BMO'|'AMC'|'DMH').

    Through YahooFinanceEarnings.get_earnings_date_near, the corroborator CLAUDE.md
    names for past dates, so the hour classification (before 09:30 BMO, from 16:00
    AMC) and the placeholder-hour correction are the ones the rest of the system uses.
    """
    from src.domain.errors import ErrorCode

    result = _yahoo_source().get_earnings_date_near(ticker, [day])
    if result.is_err:
        if result.error.code == ErrorCode.NODATA:
            raise YahooNoData(result.error.message)
        raise YahooUnavailable(result.error.message)
    found, timing = result.value
    return found, timing.value


Corroborate = Callable[[str, date], Tuple[date, str]]


def yahoo_verdict(c: Candidate, found: date, y_timing: str) -> Optional[Outcome]:
    """A leftover when Yahoo rules the event out whatever Finnhub says: another
    date (never changed here), or an hour that is neither BMO nor AMC. None when
    Yahoo reports the stored date with a BMO or AMC -- the caller then compares it
    with the stored tag."""
    if found != c.earnings_date:
        return _left(c, "yahoo", f"Yahoo dates the nearest report {found} ({y_timing}); "
                                 f"calendar says {c.earnings_date} {c.timing} -- dates are "
                                 f"never changed here", f"Yahoo dates it {found} {y_timing}")
    if y_timing not in SESSION_TIMINGS:
        return _left(c, "retag", f"Yahoo reads {y_timing} for {found}; calendar says {c.timing} "
                                 f"-- no BMO/AMC for two sources to agree on",
                     f"Yahoo reads {y_timing}; stored {c.timing}")
    return None


# --- gate r: Finnhub, the second source for a retag -----------------------------

class FinnhubNoData(Exception):
    """Finnhub lists no report for the ticker in the window."""


class FinnhubUnavailable(Exception):
    """Finnhub answered with an error for this call (timeout, 5xx)."""


class FinnhubStopped(Exception):
    """Finnhub will refuse every later call this run (rate limit, auth, no key)."""


_FINNHUB = None


def _finnhub_source():
    global _FINNHUB
    if _FINNHUB is None:
        from src.infrastructure.api.finnhub import FinnhubAPI
        # No local token bucket: this job paces its own calls FINNHUB_SPACING_SEC apart.
        _FINNHUB = FinnhubAPI(api_key=APIConfig.from_env().finnhub_key)
    return _FINNHUB


_AUTH_REFUSED = re.compile(r"\b(401|403)\b")


def finnhub_report_near(ticker: str, day: date) -> List[Tuple[date, str]]:
    """Finnhub's reports for `ticker` within FINNHUB_WINDOW_DAYS of `day`:
    [(date, 'BMO'|'AMC'|'UNKNOWN')]. A blank or 'dmh' hour reads UNKNOWN."""
    from src.domain.errors import ErrorCode

    if not APIConfig.from_env().finnhub_key:
        raise FinnhubStopped("FINNHUB_API_KEY not set")
    result = _finnhub_source().get_earnings_calendar(
        symbol=ticker, from_date=day - timedelta(days=FINNHUB_WINDOW_DAYS),
        to_date=day + timedelta(days=FINNHUB_WINDOW_DAYS))
    if result.is_err:
        code, message = result.error.code, result.error.message
        if code == ErrorCode.RATELIMIT:
            raise FinnhubStopped("rate limit")
        if code == ErrorCode.NODATA:
            raise FinnhubNoData(message)
        found = _AUTH_REFUSED.search(message)
        if found:
            raise FinnhubStopped(f"auth refused ({found.group(1)})")
        raise FinnhubUnavailable(message)
    return [(d, timing.value) for symbol, d, timing in result.value
            if symbol.upper() == ticker.upper()]


FinnhubLookup = Callable[[str, date], List[Tuple[date, str]]]


def finnhub_verdict(c: Candidate, y_timing: str,
                    entries: Sequence[Tuple[date, str]]) -> Optional[Outcome]:
    """None when Finnhub lists exactly one date, the stored one, with Yahoo's BMO/AMC."""
    dates = sorted({d for d, _ in entries})
    if not dates:
        return _left(c, "retag", f"Finnhub lists no report within {FINNHUB_WINDOW_DAYS} days of "
                                 f"{c.earnings_date}; Yahoo reads {y_timing} -- one source is not "
                                 f"enough to retag", f"Finnhub lists no report within {FINNHUB_WINDOW_DAYS} days")
    if dates != [c.earnings_date]:
        shown = ", ".join(map(str, dates))
        return _left(c, "retag", f"Finnhub dates it {shown}; calendar and Yahoo say {c.earnings_date} "
                                 f"-- not retagged", f"Finnhub dates it {shown}")
    f_timing = next(t for d, t in entries if d == c.earnings_date)
    if f_timing not in SESSION_TIMINGS:
        return _left(c, "retag", f"Finnhub hour blank ({f_timing}) for {c.earnings_date}; Yahoo reads "
                                 f"{y_timing} -- no second source for the timing",
                     f"Finnhub hour blank; Yahoo reads {y_timing}")
    if f_timing != y_timing:
        return _left(c, "retag", f"Finnhub reads {f_timing}, Yahoo reads {y_timing} for "
                                 f"{c.earnings_date}; calendar says {c.timing}",
                     f"Finnhub {f_timing} vs Yahoo {y_timing}")
    return None


# --- the database --------------------------------------------------------------

def load_candidates(conn, today: date, tickers: Optional[Sequence[str]] = None) -> List[Candidate]:
    rows = conn.execute(CANDIDATES_SQL, {
        "today": today, "lookback": LOOKBACK_DAYS, "cluster_days": CLUSTER_DAYS,
        "tickers": [t.upper() for t in tickers] if tickers else None,
    }).fetchall()
    return [Candidate(r.ticker, r.earnings_date, r.timing,
                      tuple(r.near_calendar or ()), tuple(r.near_moves or ())) for r in rows]


def traded_tickers(conn, tickers: Sequence[str]) -> FrozenSet[str]:
    if not tickers:
        return frozenset()
    rows = conn.execute(TRADED_SQL, {"tickers": sorted(set(tickers))}).fetchall()
    conn.commit()
    return frozenset(r.symbol for r in rows)


Fetch = Callable[[str, date, date], List[DailyPrice]]
Item = Tuple[Candidate, Optional[str]]          # (candidate, timing it was retagged from)


def record_moves(
    conn,
    today: date,
    *,
    execute: bool,
    fetch: Fetch,
    corroborate: Optional[Corroborate],
    finnhub: Optional[FinnhubLookup],
    budget: int = DEFAULT_BUDGET,
    finnhub_spacing_sec: float = FINNHUB_SPACING_SEC,
    max_runtime_sec: float = DEFAULT_MAX_MINUTES * 60,
    tickers: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Report:
    """Screen every candidate, settle its timing, fetch bars, record or report.

    `corroborate` and `finnhub` have no defaults on purpose: a caller passes the
    Yahoo check and the Finnhub lookup, or says None and owns that choice.

    With execute=False nothing is written; the caller should also hand in a
    read-only connection. Each retag and each insert is committed on its own, so
    a run that is killed keeps what it wrote. `limit` caps Twelve Data calls
    only: every event still gets its Yahoo/Finnhub verdict.
    """
    report = Report(today, execute, [], budget=budget, yahoo=corroborate is not None)
    candidates = load_candidates(conn, today, tickers)       # oldest first
    # End the SELECT's transaction before the paced loop: minutes idle in
    # transaction would pin a snapshot across VACUUM and the nightly dump.
    conn.commit()
    ready: Dict[str, List[Candidate]] = {}
    for c in candidates:
        refused = screen(c, today)
        if refused is None:
            ready.setdefault(c.ticker, []).append(c)
        else:
            report.outcomes.append(refused)

    started = clock()
    # Only Finnhub is paced here. Yahoo and Twelve Data calls wait on the
    # process's own limits for those vendors (src/utils/rate_limiter.py), and
    # spacing on top of them would stack.
    last: Dict[str, Optional[float]] = {"finnhub": None}
    stop: Optional[Tuple[str, str]] = None      # (kind, reason) once fetching has stopped

    def pace(source: str, spacing: float) -> None:
        if last[source] is not None:
            wait = spacing - (clock() - last[source])
            if wait > 0:
                sleep(wait)
        last[source] = clock()

    def carry(items: Sequence[Item], kind: str, why: str) -> None:
        for c, was in items:
            report.outcomes.append(Outcome(c, PENDING, f"{why} -- carried to the next run", kind,
                                           retagged_from=was))

    def deadline() -> Tuple[str, str]:
        return "deadline", f"runtime limit of {max_runtime_sec / 60:g} minutes reached"

    def settle_timing(c: Candidate) -> Union[Item, Outcome]:
        """Gates y and r: a candidate with a corroborated BMO/AMC tag, or why not."""
        if corroborate is None:
            if c.timing in SESSION_TIMINGS:
                return c, None
            return _left(c, "retag", f"timing {c.timing} and Yahoo corroboration is off "
                                     f"(--skip-yahoo) -- never inferred",
                         f"timing {c.timing}; Yahoo off, not retagged")

        try:
            found, y_timing = corroborate(c.ticker, c.earnings_date)
        except YahooNoData as e:
            return _left(c, "yahoo", f"Yahoo has no earnings history ({str(e)[:120]}) -- confirm "
                                     f"the date and timing from the release; then record it with "
                                     f"--execute --force --tickers {c.ticker} --skip-yahoo",
                         "Yahoo has no earnings history")
        except YahooUnavailable as e:
            return Outcome(c, PENDING, f"Yahoo unavailable ({str(e)[:120]}) -- not recorded "
                                       f"uncorroborated; retried next run", "yahoo_unavailable")
        refused = yahoo_verdict(c, found, y_timing)
        if refused is not None:
            return refused
        if y_timing == c.timing:
            return c, None

        # The stored tag is UNKNOWN/DMH, or Yahoo reads the other session:
        # a retag needs Finnhub to say the same.
        if finnhub is None:
            return _left(c, "retag", f"Yahoo reads {y_timing}; calendar says {c.timing}; Finnhub "
                                     f"not consulted -- one source is not enough to retag",
                         f"Yahoo reads {y_timing}; stored {c.timing}; no Finnhub")
        if report.retag_stopped:
            return _left(c, "retag", f"Yahoo reads {y_timing}; calendar says {c.timing}; not "
                                     f"retagged: Finnhub {report.retag_stopped} earlier this run",
                         f"not retagged: Finnhub {report.retag_stopped}")
        pace("finnhub", finnhub_spacing_sec)
        try:
            entries = finnhub(c.ticker, c.earnings_date)
        except FinnhubStopped as e:
            report.retag_stopped = str(e)[:80]
            logger.warning(f"record_moves: Finnhub {e}; retagging stopped for this run")
            return _left(c, "retag", f"Finnhub {e}; retagging stopped for this run",
                         f"not retagged: Finnhub {report.retag_stopped}")
        except FinnhubNoData:
            entries = []
        except FinnhubUnavailable as e:
            return _left(c, "retag", f"Finnhub error ({str(e)[:160]}); Yahoo reads {y_timing} -- "
                                     f"not retagged", f"Finnhub error: {e}")
        refused = finnhub_verdict(c, y_timing, entries)
        if refused is not None:
            return refused

        if execute:
            updated = conn.execute(RETAG_SQL, {"timing": y_timing, "ticker": c.ticker,
                                               "day": c.earnings_date, "was": c.timing}).rowcount
            if updated != 1:
                conn.rollback()
                return _left(c, "retag", f"calendar row changed during the run ({updated} rows "
                                         f"matched {c.timing}) -- not retagged",
                             "calendar row changed during the run")
            conn.commit()
        return replace(c, timing=y_timing), c.timing

    for ticker, cands in ready.items():
        items: List[Item] = [(c, None) for c in cands]
        if stop is None and clock() - started > max_runtime_sec:
            stop = deadline()
        if stop is not None:
            carry(items, *stop)
            continue

        # Gates y and r before Twelve Data: a refused event spends no credit.
        settled: List[Item] = []
        for i, (c, _) in enumerate(items):
            if clock() - started > max_runtime_sec:
                stop = deadline()
                carry(items[i:], *stop)
                break
            verdict = settle_timing(c)
            if isinstance(verdict, Outcome):
                report.outcomes.append(verdict)
                continue
            new, was = verdict
            if was is not None:
                # The new tag has its own reaction session; it may not be done.
                refused = screen(new, today)
                if refused is not None:
                    refused.retagged_from = was
                    report.outcomes.append(refused)
                    continue
            settled.append(verdict)
        if not settled:
            continue
        if stop is None and report.calls >= budget:
            stop = ("budget", f"budget of {budget} Twelve Data calls reached")
        if stop is not None:
            carry(settled, *stop)
            continue
        if limit is not None and report.calls >= limit:
            carry(settled, "limit", f"beyond --limit {limit}")
            continue

        start = min(c.earnings_date for c, _ in settled) - timedelta(days=PRICE_WINDOW_DAYS)
        end = max(c.earnings_date for c, _ in settled) + timedelta(days=PRICE_WINDOW_DAYS)
        report.calls += 1
        try:
            prices = fetch(ticker, start, end)
        except TwelveDataRateLimited as e:
            # Every later call fails the same way: stop, keep the rest for tomorrow.
            stop = ("rate_limit", f"Twelve Data rate limit: {str(e)[:200]}")
            carry(settled, *stop)
            continue
        except TwelveDataError as e:
            for c, was in settled:
                report.outcomes.append(_left(c, "api", f"Twelve Data: {str(e)[:200]}",
                                             f"Twelve Data: {e}", retagged_from=was))
            continue
        except requests.RequestException as e:
            for c, was in settled:
                report.outcomes.append(Outcome(
                    c, PENDING, f"Twelve Data request failed ({type(e).__name__}: {str(e)[:200]}) "
                                f"-- retried next run", "request", retagged_from=was))
            continue

        for c, was in settled:
            verdict = check_bars(c, sessions_for(c), prices)
            if isinstance(verdict, Outcome):
                verdict.retagged_from = was
                report.outcomes.append(verdict)
            elif not execute:
                report.outcomes.append(Outcome(c, WOULD_RECORD, move=verdict, retagged_from=was))
            else:
                added = insert_move_if_absent(conn, verdict)
                conn.commit()
                report.outcomes.append(Outcome(c, RECORDED if added else ALREADY_PRESENT,
                                               "" if added else "a row appeared during the run; left as is",
                                               move=verdict, retagged_from=was))

    report.stopped = stop[1] if stop else None
    report.traded = traded_tickers(conn, [o.candidate.ticker for o in report.leftovers()])
    return report


# --- output --------------------------------------------------------------------

def counts_line(report: Report) -> str:
    """The one line a human sees: Telegram's header, Kuma's message, the job's summary."""
    recorded = len(report.with_status(RECORDED, WOULD_RECORD))
    retagged = len(report.retagged())
    verb, retag = ("recorded", "retagged") if report.execute else ("would record", "would retag")
    line = (f"{verb} {recorded} · {retag} {retagged} · left {len(report.leftovers())}, "
            f"of which {len(report.traded_leftovers())} traded")
    if report.retag_stopped:
        line += f" · retag stopped: Finnhub {report.retag_stopped}"
    return line


def _move_text(o: Outcome) -> str:
    m = o.move
    return (f"{o.candidate.ticker} {o.candidate.earnings_date} {o.candidate.timing} "
            f"gap {m.gap_move_pct:+.2f}% close {m.close_move_pct:+.2f}%")


def format_summary(report: Report) -> str:
    mode = "EXECUTE" if report.execute else "REPORT ONLY (pass --execute to write)"
    lines = [
        f"record_moves {report.today} -- {mode}",
        f"candidates {len(report.outcomes)} (calendar events of known tickers, last "
        f"{LOOKBACK_DAYS} days, no move row) · Twelve Data calls {report.calls}/{report.budget} · "
        + ("Yahoo corroboration on" if report.yahoo else "Yahoo corroboration OFF (--skip-yahoo)"),
        counts_line(report),
        f"pending {len(report.with_status(PENDING))} · "
        f"already present {len(report.with_status(ALREADY_PRESENT))}",
    ]
    if report.stopped:
        lines.append(f"stopped early: {report.stopped}")

    retagged = report.retagged()
    if retagged:
        lines.append("")
        lines.append(f"{'RETAGGED' if report.execute else 'WOULD RETAG'} ({len(retagged)})")
        for o in retagged:
            c = o.candidate
            lines.append(f"  {c.ticker} {c.earnings_date} {o.retagged_from} -> {c.timing} "
                         f"(Yahoo and Finnhub agree) -- {o.status}")

    left = sorted(report.leftovers(), key=lambda o: o.candidate.ticker not in report.traded)
    for title, rows in (("RECORDED" if report.execute else "WOULD RECORD",
                         report.with_status(RECORDED, WOULD_RECORD)),
                        ("ALREADY PRESENT", report.with_status(ALREADY_PRESENT)),
                        ("LEFT OVER", left),
                        ("PENDING", report.with_status(PENDING))):
        if not rows:
            continue
        lines.append("")
        lines.append(f"{title} ({len(rows)})")
        for o in rows:
            if o.move is not None:
                lines.append(f"  {_move_text(o)}" + (f" -- {o.reason}" if o.reason else ""))
            else:
                c = o.candidate
                traded = " [traded]" if o.status == LEFT and c.ticker in report.traded else ""
                lines.append(f"  {c.ticker} {c.earnings_date} {c.timing} [{o.kind}]{traded} {o.reason}")
    return "\n".join(lines)


def _telegram_text(report: Report) -> str:
    esc = html.escape
    lines = [f"<b>record_moves {report.today}</b> {esc(counts_line(report))}"]
    lines.extend(esc(f"{o.candidate.ticker} {o.candidate.earnings_date} {o.candidate.timing} — {o.brief}")
                 for o in report.traded_leftovers())
    return "\n".join(lines)


def notify(report: Report, sender: Optional[TelegramSender]) -> bool:
    """Telegram only when a leftover is on a traded ticker. False if not sent.

    Recorded and retagged events, and leftovers on tickers never traded, are in
    the job log only. A failed send is logged, not raised: the writes are done,
    and the next run lists every leftover still inside the window.
    """
    if not report.traded_leftovers():
        return False
    if sender is None:
        logger.info("Telegram not configured; summary not sent")
        return False
    ok = sender.send(_telegram_text(report))
    if not ok:
        logger.error("record_moves: Telegram send failed")
    return bool(ok)


def _push_url(url: str) -> str:
    """The push URL without status/msg, which this job sets itself -- a URL copied
    from Kuma carries `?status=up&msg=OK&ping=`, and a repeated key is ambiguous."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in ("status", "msg")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def push_kuma(url: str, status: str, msg: str, *, get=requests.get, ca_path: str = HOMELAB_CA) -> bool:
    """Push `status` to Kuma (the job pushes 'up' after a run that finished). Never raises.

    TLS is verified: against the homelab CA when its certificate is present (as
    weekly-maintenance.sh does with curl --cacert), else against system roots.
    Never verify=False -- `runner.tls_verify`, shared with the job runner.
    """
    verify = tls_verify(ca_path)
    try:
        get(_push_url(url), params={"status": status, "msg": msg[:KUMA_MSG_CHARS]},
            timeout=PUSH_TIMEOUT_SEC, verify=verify)
        return True
    except Exception as e:
        logger.warning(f"record_moves: Kuma push failed: {type(e).__name__}")
        return False


# --- entry points --------------------------------------------------------------

def _twelve_data_fetch(ticker: str, start: date, end: date) -> List[DailyPrice]:
    return fetch_twelve_data_prices(ticker, start, end)


def _run_once(*, execute: bool, corroborate: Optional[Corroborate],
              finnhub: Optional[FinnhubLookup], fetch: Optional[Fetch] = None, **options) -> Report:
    if execute:
        # A writer migrates first, once per run, inside run_job: an edited
        # migration file or a failed migration is a job error that Telegrams.
        # Report-only writes nothing, so it does not migrate either.
        apply_migrations()
    with connect() as conn:
        if not execute:
            conn.read_only = True
        return record_moves(conn, today_et(), execute=execute, corroborate=corroborate,
                            finnhub=finnhub, fetch=fetch or _twelve_data_fetch, **options)


def _require_keys() -> Optional[str]:
    api = APIConfig.from_env()
    missing = [name for name, key in (("TWELVE_DATA_KEY", api.twelve_data_key),
                                      ("FINNHUB_API_KEY", api.finnhub_key)) if not key]
    if missing:
        return f"{', '.join(missing)} not set (source 2.0/.env with set -a)"
    return None


def _execute(sender: Optional[TelegramSender], options: dict, fetch: Optional[Fetch]) -> str:
    url = os.getenv(PUSH_URL_ENV)
    if not url:
        logger.warning(f"{PUSH_URL_ENV} not set; Kuma not pushed")
    # A failure raises straight through: run_job records it and Telegrams it, and
    # that one message is the alert. No Kuma down push -- it sent a second message
    # for the same failure. Kuma's own 4-day interval covers a job that never runs.
    missing = _require_keys()
    if missing:
        raise RuntimeError(missing)
    report = _run_once(execute=True, fetch=fetch, **options)
    print(format_summary(report), flush=True)
    notify(report, sender)
    counts = counts_line(report)
    if url:
        push_kuma(url, "up", counts)
    return counts


def main(argv: Optional[List[str]] = None, sender: Optional[TelegramSender] = None,
         fetch: Optional[Fetch] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append newly reported quarters to historical_moves (insert-only), "
                    "retagging a calendar timing only where Yahoo and Finnhub agree. "
                    "Report only unless --execute.")
    parser.add_argument("--execute", action="store_true",
                        help="write retags and missing move rows, claim today's job slot, "
                             "Telegram traded leftovers, push Kuma (default: report only -- "
                             "Yahoo, Finnhub and Twelve Data are still called)")
    parser.add_argument("--force", action="store_true",
                        help="with --execute: release today's completed claim and run again")
    parser.add_argument("--tickers", nargs="+", metavar="TICKER", help="only these tickers")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="fetch bars for at most N tickers; the rest stay pending")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, metavar="CALLS",
                        help=f"stop after this many Twelve Data calls (default {DEFAULT_BUDGET})")
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                        help=f"stop fetching after this long (default {DEFAULT_MAX_MINUTES})")
    parser.add_argument("--skip-yahoo", action="store_true",
                        help="trust a stored BMO/AMC without Yahoo (and never retag) -- only for "
                             "a timing a human has confirmed from the release (use with --tickers)")
    args = parser.parse_args(argv)
    if args.force and not args.execute:
        parser.error("--force only applies with --execute")

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    options = {"budget": args.budget, "max_runtime_sec": args.max_minutes * 60,
               "tickers": args.tickers, "limit": args.limit,
               "corroborate": None if args.skip_yahoo else yahoo_report_near,
               "finnhub": finnhub_report_near}

    if not args.execute:
        missing = _require_keys()
        if missing:
            print(f"record_moves: {missing}", file=sys.stderr)
            return 1
        print(format_summary(_run_once(execute=False, fetch=fetch, **options)))
        return 0

    if sender is None:
        telegram = Config.from_env().telegram
        if telegram.is_configured:
            sender = TelegramSender(telegram.bot_token, telegram.chat_id)

    # No push_url to run_job: its ping is a bare success ping with no message. This job
    # pushes its own, carrying the run's counts (up after every run that finished).
    return run_job(JOB_NAME, lambda: _execute(sender, options, fetch),
                   sender=sender, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
