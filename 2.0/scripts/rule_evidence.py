#!/usr/bin/env python3
"""The trade evidence CLAUDE.md quotes, computed from the `positions` view and the journal.

Until 2026-09-13 these figures came out of chat sessions. The audit that day
found the DTE, sizing and TRR evidence did not reproduce from the table under
any of four definitions, and the journal beneath them was double-counting
wash-sale losses. This script is now their only source: the headline table,
§3 TRR, §5 DTE, §6 sizing and return on max loss, §8 exits and the banned
tables print here, and `--verify-only` fails when a figure in CLAUDE.md or
README.md stops matching the book closed through SNAPSHOT_THROUGH.

Definitions, settled 2026-09-13:
  position        a row of the `positions` view (migration 5): one `strategies`
                  row, except a trade placed in both accounts -- rows in IRA and
                  TAXABLE with the same ticker, entry and exit dates, expiration,
                  strategy type, legs (option type and strike, from trade_journal),
                  earnings date and parent -- is one position, P&L and contracts
                  summed (since 2026-09-15; PLTR, DDOG, SPXW and seven more had
                  counted twice). The rule lives in the view only; pairs it leaves
                  unmerged are integrity_report.py's to flag. TRR, DTE and holding
                  follow from the matched fields
  win             the position's net P&L > 0 (the view's `is_winner`)
  index option    an option position on SPY, SPX or SPXW
  earnings trade  a non-index SPREAD or SINGLE with an earnings_date. The journal
                  dates a position opened 0-7 days before an event in
                  historical_moves and still open at it, so one on an event that
                  table lacks falls outside; the report lists every undated
                  position earnings_calendar shows open across an event.
  DTE             expiration minus entry date, in calendar days
  TRR tier        recomputed from the 12 gap moves before the entry date, with
                  the engine's own function; an event on the entry day is excluded,
                  since an AMC entry is made before that report. `strategies.trr_at_entry` is not
                  used: it mixes the frozen ORATS snapshot, a backfill computed
                  with later quarters, and live values.
  held            trading days from entry to exit
  max loss        (since 2026-09-15) what a spread's journal lots can lose held to
                  expiry: short lots open on their proceeds (Fidelity dates them
                  sale-before-acquired), long lots on their cost basis, wash-sale
                  rows included, against the worst payoff at any price. A trade in
                  both accounts risks the two accounts' max losses summed.
                  `strategies` has no max loss; the 2026-09-13 audit (4.1) rebuilt
                  it this way in a script since deleted
  return on max loss
                  the position's P&L / its max loss. Mean and log-optimal f*
                  (the fraction of bankroll committed as max loss that maximises
                  mean log growth) carry percentile 95% CIs from BOOTSTRAP_RESAMPLES
                  resamples of positions, and of ISO weeks of entry

To refresh CLAUDE.md: move SNAPSHOT_THROUGH, run with `--through` that date,
paste the tables, then `--verify-only`.

Usage (from 2.0/):
    ./venv/bin/python scripts/rule_evidence.py                     # whole book
    ./venv/bin/python scripts/rule_evidence.py --through 2026-09-11
    ./venv/bin/python scripts/rule_evidence.py --verify-only
"""

from __future__ import annotations

import argparse
import random
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

# Run by path: 2.0/ for the `scripts.` namespace; `src` and `common` are installed.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db import as_date, connect, target  # noqa: E402
from src.domain.market_calendar import is_trading_day  # noqa: E402
from src.domain.tail_risk import trr_as_of, trr_level  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# The last close in the book when CLAUDE.md's figures were derived: the day the
# journal was reconciled against Fidelity's exports.
SNAPSHOT_THROUGH = date(2026, 9, 11)

INDEX_SYMBOLS = frozenset({"SPY", "SPX", "SPXW"})
EARNINGS_TYPES = frozenset({"SPREAD", "SINGLE"})

DTE_BANDS = ("0–2", "3–5", "6+")
SIZE_BANDS = ("1–50", "51–100", "101–200", "200+")
TRR_TIERS = ("LOW", "NORMAL", "HIGH", "UNKNOWN")
HELD_BANDS = ("0–1", "2+")

# §6 return on max loss. Draws come from random.Random.random(), the one stream
# Python promises not to change between versions, so an upgrade cannot move a CI
# the gate checks.
LARGE_RISK = 100_000.0
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 7

History = Dict[str, List[Tuple[date, float]]]


@dataclass(frozen=True)
class Trade:
    id: int
    symbol: str
    strategy_type: str
    account_type: str
    acquired_date: date
    sale_date: date
    expiration: Optional[date]
    quantity: float
    gain_loss: float
    earnings_date: Optional[date]
    parent_strategy_id: Optional[int]
    # Every `strategies` row in the position, id first: two for a trade placed in both accounts.
    strategy_ids: Tuple[int, ...]

    @property
    def won(self) -> bool:
        return self.gain_loss > 0


@dataclass(frozen=True)
class Tally:
    n: int
    wins: int
    pnl: float
    avg_loss: Optional[float]
    median_quantity: Optional[float]

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.n if self.n else None


def tally(trades: Iterable[Trade]) -> Tally:
    trades = list(trades)
    losses = [t.gain_loss for t in trades if not t.won]
    return Tally(
        n=len(trades),
        wins=sum(t.won for t in trades),
        pnl=sum(t.gain_loss for t in trades),
        avg_loss=sum(losses) / len(losses) if losses else None,
        median_quantity=statistics.median(t.quantity for t in trades) if trades else None,
    )


def is_index_option(t: Trade) -> bool:
    return t.symbol in INDEX_SYMBOLS and t.strategy_type != "STOCK"


def is_earnings_trade(t: Trade) -> bool:
    return (t.symbol not in INDEX_SYMBOLS and t.strategy_type in EARNINGS_TYPES
            and t.earnings_date is not None)


def dte_band(t: Trade) -> str:
    dte = (t.expiration - t.acquired_date).days
    return "0–2" if dte <= 2 else "3–5" if dte <= 5 else "6+"


def size_band(quantity: float) -> str:
    if quantity <= 50:
        return "1–50"
    if quantity <= 100:
        return "51–100"
    return "101–200" if quantity <= 200 else "200+"


def trading_days_held(entry: date, exit_: date) -> int:
    """Trading days after entry up to and including exit: next-day exit is 1."""
    days, d = 0, entry
    while d < exit_:
        d += timedelta(days=1)
        if is_trading_day(d):
            days += 1
    return days


def held_band(t: Trade) -> str:
    return "0–1" if trading_days_held(t.acquired_date, t.sale_date) <= 1 else "2+"


def trr_tier_at_entry(t: Trade, history: History) -> str:
    return trr_level(trr_as_of(history.get(t.symbol, []), t.acquired_date)) or "UNKNOWN"


class Unbuildable(ValueError):
    """Lots that give no max loss to divide by; the report names the position and why."""


@dataclass(frozen=True)
class Leg:
    """One trade_journal lot."""
    strategy_id: int
    option_type: Optional[str]          # CALL or PUT; None for a stock lot
    strike: Optional[float]
    expiration: Optional[date]
    quantity: float
    cost_basis: float
    proceeds: float
    wash_sale: float
    # Fidelity dates a lot sold to open sale-before-acquired. None when its dates
    # cannot tell: a lot opened and closed the same day.
    opened_short: Optional[bool]


def held_contracts(legs: Iterable[Leg]) -> Dict[Tuple[str, float], float]:
    """Contracts held to expiry by (option type, strike): long positive, short negative."""
    held: Dict[Tuple[str, float], float] = defaultdict(float)
    for leg in legs:
        held[(leg.option_type, leg.strike)] += -leg.quantity if leg.opened_short else leg.quantity
    return {key: q for key, q in held.items() if q}


def max_loss(legs: Sequence[Leg]) -> float:
    """The most these lots can lose held to expiry.

    The opening cash is the short lots' proceeds less the long lots' cost basis. A
    long lot's basis carries losses disallowed on earlier sales and its wash-sale
    rows carry them back out, so a leg's rows sum to the cash paid (CLAUDE.md, data
    caveats). The payoff is piecewise linear in the underlying, so its minimum is at
    zero or at a strike, unless more calls are short than long.
    """
    if not legs:
        raise Unbuildable("no journal legs")
    if any(leg.option_type is None or leg.strike is None for leg in legs):
        raise Unbuildable("a stock lot, or one with no strike, has no expiry payoff")
    if len({leg.expiration for leg in legs}) > 1:
        raise Unbuildable("lots expire on different dates")
    if any(leg.opened_short is None for leg in legs):
        raise Unbuildable("a lot opened and closed the same day, so its side cannot be read")
    held = held_contracts(legs)
    if sum(q for (kind, _), q in held.items() if kind == "CALL") < 0:
        raise Unbuildable("more calls short than long: the loss is unbounded")
    credit = sum(leg.proceeds if leg.opened_short else -leg.cost_basis for leg in legs)

    def payoff(price: float) -> float:
        return credit + 100 * sum(
            q * (max(strike - price, 0.0) if kind == "PUT" else max(price - strike, 0.0))
            for (kind, strike), q in held.items())

    loss = -min(payoff(price) for price in (0.0, *(strike for _, strike in held)))
    if loss <= 0:
        raise Unbuildable("the lots cannot lose at expiry, so there is no max loss")
    return loss


def is_vertical(legs: Sequence[Leg]) -> bool:
    """One option type, one strike short and one long, the same size."""
    held = held_contracts(legs)
    sizes = sorted(held.values())
    return (len(held) == 2 and len({kind for kind, _ in held}) == 1
            and sizes[0] < 0 < sizes[1] and sizes[0] == -sizes[1])


def describe_lots(legs: Sequence[Leg]) -> str:
    return " · ".join(f"{'short' if q < 0 else 'long'} {abs(q):g} {kind} {strike:g}"
                      for (kind, strike), q in sorted(held_contracts(legs).items(),
                                                     key=lambda item: (item[0][0], -item[0][1])))


@dataclass(frozen=True)
class RiskedPosition:
    trade: Trade
    max_loss: float                   # each account's max loss, summed
    union_max_loss: Optional[float]   # every account's lots rebuilt at once; None if that fails
    clean: bool                       # each account's lots a vertical
    wash_sale: bool
    legs: Tuple[Leg, ...]

    @property
    def r(self) -> float:
        return self.trade.gain_loss / self.max_loss


def rebuild(t: Trade, legs: Mapping[int, Sequence[Leg]]) -> RiskedPosition:
    accounts = [legs.get(sid, ()) for sid in t.strategy_ids]
    total = sum(max_loss(lots) for lots in accounts)
    every = tuple(leg for lots in accounts for leg in lots)
    try:
        union: Optional[float] = max_loss(every)
    except Unbuildable:
        union = None
    return RiskedPosition(t, total, union, all(is_vertical(lots) for lots in accounts),
                          any(leg.wash_sale for leg in every), every)


def resample_counts(groups: Sequence[Sequence[int]], n: int, resamples: int, seed: int) -> np.ndarray:
    """How often each of n positions is drawn in each resample, len(groups) groups
    drawn with replacement. Groups of one are the ordinary bootstrap; a week of
    entries keeps the positions opened in it together."""
    rng = random.Random(seed)
    k = len(groups)
    counts = np.zeros((resamples, n))
    for row in range(resamples):
        drawn = [0] * n
        for _ in range(k):
            for i in groups[int(rng.random() * k)]:
                drawn[i] += 1
        counts[row] = drawn
    return counts


def log_optimal_fractions(returns: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Per row of counts, the f in [0, 1] maximising sum(count * log(1 + f * r)).

    f is the fraction of bankroll committed as max loss. No position can lose more
    than its max loss (r >= -1), so every f in [0, 1] is feasible; should one have,
    f stays below 1/|r|. The objective is concave: f* is 0 when its slope at 0 is not
    positive, the bound when its slope at the bound is not negative, else the slope's
    root, found by bisection.
    """
    rows = counts.shape[0]
    bound = 1.0 if returns.min() >= -1 else -1.0 / returns.min()

    def slope(f: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return (counts * (returns / (1.0 + f[:, None] * returns))).sum(axis=1)

    lo, hi = np.zeros(rows), np.full(rows, bound)
    for _ in range(60):
        mid = (lo + hi) / 2
        rising = slope(mid) > 0
        lo, hi = np.where(rising, mid, lo), np.where(rising, hi, mid)
    f = np.where(slope(np.full(rows, bound)) >= 0, bound, (lo + hi) / 2)
    return np.where(slope(np.zeros(rows)) <= 0, 0.0, f)


def percentile_ci(values: np.ndarray) -> Tuple[float, float]:
    """2.5th and 97.5th percentiles, bootstrap_sharpe.py's convention."""
    ordered = np.sort(values)
    return float(ordered[int(0.025 * len(ordered))]), float(ordered[int(0.975 * len(ordered))])


@dataclass(frozen=True)
class ReturnOnMaxLoss:
    positions: List[RiskedPosition]           # rebuilt, in book order
    unbuildable: List[Tuple[Trade, str]]      # left out of every figure, each with its reason
    spread_pnl: float                         # every earnings spread, rebuilt or not
    weeks: int                                # ISO weeks of entry among the rebuilt
    mean_r: Optional[float] = None
    mean_ci: Optional[Tuple[float, float]] = None
    mean_week_ci: Optional[Tuple[float, float]] = None
    f_star: Optional[float] = None
    f_ci: Optional[Tuple[float, float]] = None
    f_week_ci: Optional[Tuple[float, float]] = None

    @property
    def irregular(self) -> List[RiskedPosition]:
        return [p for p in self.positions if not p.clean]

    @property
    def worst(self) -> Optional[RiskedPosition]:
        return min(self.positions, key=lambda p: p.r, default=None)

    @property
    def large(self) -> Tally:
        return tally(p.trade for p in self.positions if p.max_loss > LARGE_RISK)


def return_on_max_loss(spreads: Sequence[Trade], legs: Mapping[int, Sequence[Leg]]) -> ReturnOnMaxLoss:
    positions, unbuildable = [], []
    for t in spreads:
        try:
            positions.append(rebuild(t, legs))
        except Unbuildable as exc:
            unbuildable.append((t, str(exc)))
    weeks: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, p in enumerate(positions):
        iso = p.trade.acquired_date.isocalendar()
        weeks[(iso[0], iso[1])].append(i)
    result = ReturnOnMaxLoss(positions, unbuildable, sum(t.gain_loss for t in spreads), len(weeks))
    if not positions:
        return result

    r = np.array([p.r for p in positions])
    n = len(r)
    by_position = resample_counts([[i] for i in range(n)], n, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED)
    by_week = resample_counts([weeks[w] for w in sorted(weeks)], n, BOOTSTRAP_RESAMPLES,
                              BOOTSTRAP_SEED)

    def means(counts: np.ndarray) -> np.ndarray:
        return (counts @ r) / counts.sum(axis=1)

    return replace(
        result, mean_r=float(r.mean()),
        mean_ci=percentile_ci(means(by_position)), mean_week_ci=percentile_ci(means(by_week)),
        f_star=float(log_optimal_fractions(r, np.ones((1, n)))[0]),
        f_ci=percentile_ci(log_optimal_fractions(r, by_position)),
        f_week_ci=percentile_ci(log_optimal_fractions(r, by_week)))


def _group(trades: Sequence[Trade], key: Callable[[Trade], str],
           order: Sequence[str]) -> Dict[str, Tally]:
    buckets: Dict[str, List[Trade]] = defaultdict(list)
    for t in trades:
        buckets[key(t)].append(t)
    unexpected = set(buckets) - set(order)
    assert not unexpected, f"unbanded trades: {unexpected}"
    return {label: tally(buckets[label]) for label in order}


def compute(trades: Sequence[Trade], history: History, legs: Mapping[int, Sequence[Leg]]) -> dict:
    index = [t for t in trades if is_index_option(t)]
    earnings = [t for t in trades if is_earnings_trade(t)]
    spreads = [t for t in earnings if t.strategy_type == "SPREAD"]
    singles = [t for t in earnings if t.strategy_type == "SINGLE"]
    undated = [t for t in trades if t.symbol not in INDEX_SYMBOLS
               and t.strategy_type in EARNINGS_TYPES and t.earnings_date is None]
    condors = [t for t in trades if t.strategy_type == "IRON_CONDOR"]
    strangles = [t for t in trades if t.strategy_type == "STRANGLE"]
    index_stock = [t for t in trades if t.symbol in INDEX_SYMBOLS and t.strategy_type == "STOCK"]
    stock = [t for t in trades if t.symbol not in INDEX_SYMBOLS and t.strategy_type == "STOCK"]
    adjustments = [t for t in trades if t.parent_strategy_id is not None]

    # An adjusted position: the original plus everything opened against it.
    by_id = {i: t for t in trades for i in t.strategy_ids}
    chains: Dict[int, Tuple[str, float, float]] = {}
    for t in adjustments:
        parent = by_id[t.parent_strategy_id]
        _, first, total = chains.get(parent.id, (parent.symbol, parent.gain_loss, parent.gain_loss))
        chains[parent.id] = (parent.symbol, first, total + t.gain_loss)

    tier = lambda t: trr_tier_at_entry(t, history)  # noqa: E731
    return {
        "headline": {
            "Index options (SPY/SPX/SPXW)": tally(index),
            "Earnings spreads": tally(spreads),
            "Earnings singles": tally(singles),
            "Combined": tally(index + earnings),
            "Spreads and singles with no earnings date": tally(undated),
        },
        "trr": _group(earnings, tier, TRR_TIERS),
        "trr_ex_crwv": _group([t for t in earnings if t.symbol != "CRWV"], tier, TRR_TIERS),
        "dte": _group(earnings, dte_band, DTE_BANDS),
        "size": _group(earnings, lambda t: size_band(t.quantity), SIZE_BANDS),
        "risk": return_on_max_loss(spreads, legs),
        "exits": {
            **{f"SPREAD {k}": v for k, v in _group(spreads, held_band, HELD_BANDS).items()},
            **{f"SINGLE {k}": v for k, v in _group(singles, held_band, HELD_BANDS).items()},
        },
        "banned": {
            "IRON_CONDOR": tally(condors),
            "STRANGLE": tally(strangles),
            **{f"IRON_CONDOR {s}": tally(t for t in condors if t.symbol == s)
               for s in sorted({t.symbol for t in condors})},
            "CRWV stock (IRA)": tally(t for t in stock
                                      if t.symbol == "CRWV" and t.account_type == "IRA"),
            "CRWV options (IRA)": tally(t for t in trades if t.symbol == "CRWV"
                                        and t.account_type == "IRA"
                                        and t.strategy_type == "SINGLE"),
        },
        "adjustments": {
            "All": tally(adjustments),
            **{f"{t.symbol} {t.id}": tally([t]) for t in adjustments},
        },
        "adjustment_chains": chains,
        "book": {
            "Index options": tally(index),
            "Index stock": tally(index_stock),
            "Earnings trades": tally(earnings),
            "Spreads and singles with no earnings date": tally(undated),
            "Iron condors": tally(condors),
            "Strangles": tally(strangles),
            "Stock": tally(stock),
            "Total": tally(trades),
        },
    }


def money(x: float) -> str:
    whole = int(Decimal(str(abs(x))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return f"{'−' if x < 0 and whole else '+'}${whole:,}"


def pct(t: Tally) -> str:
    return f"{100 * t.win_rate:.1f}%" if t.n else "—"


def signed_pct(x: float) -> str:
    text = f"{abs(x) * 100:.1f}"
    return f"{'−' if x < 0 and text != '0.0' else '+'}{text}%"


def ratio(x: float) -> str:
    return f"{'−' if x < 0 else '+'}{abs(x):.3f}"


def fraction(x: float) -> str:
    """f* and its bounds: the ends of [0, 1] print bare, as CLAUDE.md writes them."""
    return "0" if x == 0 else "1" if x == 1 else f"{x:.2f}"


def _risk_lines(risk: ReturnOnMaxLoss) -> List[str]:
    if not risk.positions:
        return [f"across 0 earnings spreads, no max loss rebuilt ({len(risk.unbuildable)} not rebuilt)"]
    assert risk.mean_r is not None and risk.mean_ci and risk.f_star is not None
    assert risk.f_ci and risk.f_week_ci
    large = risk.large
    share = f"{100 * large.pnl / risk.spread_pnl:.0f}%" if risk.spread_pnl else "—"
    return [
        "across {} earnings spreads, mean return on max loss {} [{}, {}] and f* {} [{}, {}]"
        " (week-clustered [{}, {}])".format(
            len(risk.positions), signed_pct(risk.mean_r), *map(signed_pct, risk.mean_ci),
            fraction(risk.f_star), *map(fraction, risk.f_ci), *map(fraction, risk.f_week_ci)),
        "{} of earnings-spread P&L, {}, came from {} positions that each risked over ${:,.0f}k, {}"
        .format(share, money(large.pnl), large.n, LARGE_RISK / 1000,
                "all winners" if large.wins == large.n else f"{large.wins} winners"),
        f"percentile 95% CIs from {BOOTSTRAP_RESAMPLES:,} bootstrap resamples, seed {BOOTSTRAP_SEED}",
    ]


def _row(label: str, t: Tally) -> str:
    loss = money(t.avg_loss) if t.avg_loss is not None else "—"
    return f"| {label} | {t.n} | {pct(t)} | {loss} | {money(t.pnl)} |"


def render_tables(ev: dict) -> Dict[str, List[str]]:
    """CLAUDE.md's evidence tables, row for row."""
    head = ev["headline"]
    headline = ["| Strategy | Trades | Win | P&L |", "|---|:-:|:-:|--:|"]
    for label, t in head.items():
        cells = (label, str(t.n), pct(t), money(t.pnl))
        if label == "Combined":
            cells = tuple(f"**{c}**" for c in cells)
        headline.append("| " + " | ".join(cells) + " |")

    def banded(title: str, table: Dict[str, Tally]) -> List[str]:
        return ([f"| {title} | Trades | Win | Avg loss | P&L |", "|:-:|:-:|:-:|--:|--:|"]
                + [_row(label, t) for label, t in table.items()])

    ex = ev["trr_ex_crwv"]
    changed = [f"{tier} {t.n} trade{'s' if t.n != 1 else ''}, {pct(t)}, {money(t.pnl)}"
               for tier, t in ex.items() if t != ev["trr"][tier]]
    crwv_line = ("Without CRWV: " + "; ".join(changed) + "; the other tiers hold no CRWV trade."
                 if changed else "Without CRWV: no tier changes.")

    exits = ev["exits"]
    banned = ev["banned"]
    condor_parts = [f"{label.split()[1]} {money(t.pnl)}" for label, t in banned.items()
                    if label.startswith("IRON_CONDOR ")]
    adj = ev["adjustments"]
    adj_parts = [f"{label.split()[0]} {money(t.pnl)}" for label, t in adj.items() if label != "All"]
    return {
        "headline": headline,
        "trr": banded("TRR at entry", ev["trr"]) + ["", crwv_line],
        "dte": banded("DTE at entry", ev["dte"]),
        "size": banded("Contracts", ev["size"]),
        "risk": _risk_lines(ev["risk"]),
        "exits": [
            "held 2+ trading days: {} win, {} (n={}); out next day: {} win, {} (n={})".format(
                pct(exits["SPREAD 2+"]), money(exits["SPREAD 2+"].pnl), exits["SPREAD 2+"].n,
                pct(exits["SPREAD 0–1"]), money(exits["SPREAD 0–1"].pnl), exits["SPREAD 0–1"].n),
            "held 2+ trading days: {} win, {} (n={})".format(
                pct(exits["SINGLE 2+"]), money(exits["SINGLE 2+"].pnl), exits["SINGLE 2+"].n),
        ],
        "banned": [
            "| Iron condor / butterfly | **{}** |".format(money(banned["IRON_CONDOR"].pnl)),
            "{} trades, {} win: {}".format(banned["IRON_CONDOR"].n, pct(banned["IRON_CONDOR"]),
                                           ", ".join(condor_parts)),
            "| Strangle | **{}** |".format(money(banned["STRANGLE"].pnl)),
            "{} trades, {} win".format(banned["STRANGLE"].n, pct(banned["STRANGLE"])),
            "CRWV: stock {}, calls {}".format(money(banned["CRWV stock (IRA)"].pnl),
                                             money(banned["CRWV options (IRA)"].pnl)),
            "{} for {}, {} ({})".format(adj["All"].wins, adj["All"].n, money(adj["All"].pnl),
                                        ", ".join(adj_parts)),
            *(f"{sym} turned {money(first)} into {money(total)}"
              for sym, first, total in ev["adjustment_chains"].values()),
        ],
        "readme": [
            "{} trades · {} win · **{}**".format(head["Combined"].n, pct(head["Combined"]),
                                               money(head["Combined"].pnl)),
        ],
    }


def render_fragments(ev: dict) -> List[str]:
    """Every rendered line that carries a figure; each must appear in the docs."""
    return [line for lines in render_tables(ev).values() for line in lines
            if re.search(r"\d", line) and not line.startswith("|:") and not line.startswith("|---")]


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def missing_fragments(fragments: Iterable[str], text: str) -> List[str]:
    haystack = _squash(text)
    return [f for f in fragments if _squash(f) not in haystack]


def load_book(conn, through: Optional[date] = None) -> List[Trade]:
    """The book's positions (the `positions` view), closed on or before `through`."""
    sql = """SELECT id, strategy_ids, symbol, strategy_type, account_type, acquired_date,
                    sale_date, expiration, quantity, gain_loss, earnings_date, parent_strategy_id
             FROM positions"""
    params: tuple = ()
    if through is not None:
        sql += " WHERE sale_date <= %s"
        params = (through,)
    return [
        Trade(id=r.id, symbol=r.symbol, strategy_type=r.strategy_type,
              account_type=r.account_type, acquired_date=as_date(r.acquired_date),
              sale_date=as_date(r.sale_date), expiration=as_date(r.expiration),
              quantity=float(r.quantity or 0), gain_loss=float(r.gain_loss),
              earnings_date=as_date(r.earnings_date),
              parent_strategy_id=r.parent_strategy_id, strategy_ids=tuple(r.strategy_ids))
        for r in conn.execute(sql + " ORDER BY acquired_date, id", params).fetchall()]


def load_legs(conn) -> Dict[int, List[Leg]]:
    """Every journal lot linked to a strategy, by strategy id, in import order."""
    legs: Dict[int, List[Leg]] = defaultdict(list)
    for r in conn.execute(
            """SELECT strategy_id, option_type, strike, expiration, acquired_date, sale_date,
                      quantity, cost_basis, proceeds, wash_sale_amount
               FROM trade_journal WHERE strategy_id IS NOT NULL ORDER BY id"""):
        acquired, sold = as_date(r.acquired_date), as_date(r.sale_date)
        legs[r.strategy_id].append(Leg(
            strategy_id=r.strategy_id, option_type=r.option_type,
            strike=None if r.strike is None else float(r.strike),
            expiration=as_date(r.expiration), quantity=float(r.quantity or 0),
            cost_basis=float(r.cost_basis), proceeds=float(r.proceeds),
            wash_sale=float(r.wash_sale_amount or 0),
            opened_short=None if acquired is None or acquired == sold else acquired > sold))
    return dict(legs)


def load_gap_moves(conn) -> History:
    history: History = defaultdict(list)
    for r in conn.execute("SELECT ticker, earnings_date, gap_move_pct FROM historical_moves"):
        history[r.ticker].append((as_date(r.earnings_date), r.gap_move_pct))
    return dict(history)


def undated_earnings_candidates(conn, trades: Sequence[Trade]) -> List[Tuple[Trade, date]]:
    """Undated spreads/singles held across a calendar event: likely earnings
    trades the journal's 7-day matcher missed, or events historical_moves lacks."""
    calendar: Dict[str, List[date]] = defaultdict(list)
    for r in conn.execute("SELECT ticker, earnings_date FROM earnings_calendar"):
        calendar[r.ticker].append(as_date(r.earnings_date))
    out = []
    for t in trades:
        if (t.symbol in INDEX_SYMBOLS or t.strategy_type not in EARNINGS_TYPES
                or t.earnings_date is not None):
            continue
        spanned = [d for d in calendar.get(t.symbol, []) if t.acquired_date <= d <= t.sale_date]
        if spanned:
            out.append((t, min(spanned)))
    return out


def _print_report(ev: dict, trades: Sequence[Trade], conn, through: Optional[date]) -> None:
    mirrored = [t for t in trades if len(t.strategy_ids) > 1]
    rows = sum(len(t.strategy_ids) for t in trades)
    print(f"db: {target()}   closed through: {through or 'all'}   positions: {len(trades)}"
          f" ({rows} strategies)")
    print(f"\n## one trade in both accounts, counted once: {len(mirrored)}\n")
    for t in mirrored:
        print(f"  {'/'.join(map(str, t.strategy_ids)):9} {t.symbol:6} {t.strategy_type:6}"
              f" {t.acquired_date} → {t.sale_date}  {t.account_type}  size {t.quantity:g}"
              f"  {money(t.gain_loss)}")
    tables = render_tables(ev)
    for name in ("headline", "trr", "dte", "size"):
        print(f"\n## {name}\n")
        print("\n".join(tables[name]))
    print("\n## median contracts by TRR tier\n")
    for tier, t in ev["trr"].items():
        print(f"  {tier:8} {t.median_quantity if t.n else '—'}")
    _print_risk(ev["risk"], tables["risk"])
    for name in ("exits", "banned", "readme"):
        print(f"\n## {name}\n")
        print("\n".join(f"  {line}" for line in tables[name]))

    print("\n## the whole book, partitioned\n")
    for label, t in ev["book"].items():
        print(f"  {label:44} {t.n:4}  {money(t.pnl):>12}")
    parts = [t for label, t in ev["book"].items() if label != "Total"]
    total = ev["book"]["Total"]
    assert sum(t.n for t in parts) == total.n
    assert abs(sum(t.pnl for t in parts) - total.pnl) < 0.01

    candidates = undated_earnings_candidates(conn, trades)
    print(f"\n## undated spreads/singles held across a calendar earnings date: {len(candidates)}"
          f", {money(sum(t.gain_loss for t, _ in candidates))}\n")
    for t, d in candidates:
        print(f"  {t.id:4} {t.symbol:6} {t.strategy_type:6} {t.acquired_date} → {t.sale_date}"
              f"  event {d}  {money(t.gain_loss)}")


def _print_risk(risk: ReturnOnMaxLoss, lines: List[str]) -> None:
    def row(p: RiskedPosition) -> str:
        return (f"    {'/'.join(map(str, p.trade.strategy_ids)):9} {p.trade.symbol:6}"
                f" {p.trade.acquired_date}  size {p.trade.quantity:g}  max loss ${p.max_loss:,.0f}"
                f"  {money(p.trade.gain_loss)}  r {ratio(p.r)}")

    print("\n## return on max loss: earnings spreads, per position\n")
    print("\n".join(f"  {line}" for line in lines))
    if risk.positions:
        assert risk.mean_week_ci is not None
        worst = risk.worst
        assert worst is not None
        print(f"  week-clustered: {risk.weeks} ISO weeks of entry resampled; mean return"
              f" [{', '.join(map(signed_pct, risk.mean_week_ci))}]")
        print(f"  median return on max loss {signed_pct(statistics.median(p.r for p in risk.positions))};"
              f" worst r {ratio(worst.r)} ({worst.trade.symbol} {worst.trade.acquired_date},"
              f" {money(worst.trade.gain_loss)} on a ${worst.max_loss:,.0f} max loss)")
        print(f"  lost more than the rebuilt max loss: {sum(p.r < -1 for p in risk.positions)}")
        wash = [p for p in risk.positions if p.wash_sale]
        print(f"  with wash-sale rows (long lots' basis includes them): {len(wash)} positions,"
              f" {sum(p.trade.won for p in wash)} won")
        merged = [p for p in risk.positions if len(p.trade.strategy_ids) > 1]
        differ = [p for p in merged if p.union_max_loss is None
                  or abs(p.union_max_loss - p.max_loss) > 0.005]
        print(f"  in both accounts: {len(merged)}; accounts' max losses summed differ from one"
              f" rebuild of all their lots: {len(differ)}")
        for p in differ:
            print(row(p) + f"  (all lots at once: {p.union_max_loss})")
    print(f"\n  not a clean vertical, max loss from the expiry payoff of the lots held:"
          f" {len(risk.irregular)}")
    for p in risk.irregular:
        print(row(p) + f"\n      {describe_lots(p.legs)}")
    print(f"\n  not rebuilt, left out of every figure above: {len(risk.unbuildable)}")
    for t, reason in risk.unbuildable:
        print(f"    {'/'.join(map(str, t.strategy_ids)):9} {t.symbol:6} {t.acquired_date}"
              f"  {money(t.gain_loss)}  {reason}")
    large = [p for p in risk.positions if p.max_loss > LARGE_RISK]
    print(f"\n  max loss over ${LARGE_RISK:,.0f}: {len(large)}")
    for p in large:
        print(row(p))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--through", type=date.fromisoformat,
                    help="only positions closed on or before this date")
    ap.add_argument("--verify-only", action="store_true",
                    help=f"check CLAUDE.md and README.md against the book through "
                         f"{SNAPSHOT_THROUGH} and exit non-zero on any mismatch")
    args = ap.parse_args()

    through = SNAPSHOT_THROUGH if args.verify_only else args.through
    with connect() as conn:
        trades = load_book(conn, through)
        ev = compute(trades, load_gap_moves(conn), load_legs(conn))
        if args.verify_only:
            # Only the docs this checkout actually carries: the operator's own
            # CLAUDE.md is not part of the public repository.
            docs = "\n".join((REPO / name).read_text()
                             for name in ("CLAUDE.md", "README.md")
                             if (REPO / name).exists())
            missing = missing_fragments(render_fragments(ev), docs)
            print("EVIDENCE GATE:", "FAIL" if missing else "PASS",
                  f"({len(render_fragments(ev))} figures, book through {through})")
            for line in missing:
                print(f"  missing: {line}")
            return 1 if missing else 0
        _print_report(ev, trades, conn, through)
    return 0


if __name__ == "__main__":
    sys.exit(main())
