"""Bootstrap CI on Sharpe + Monte Carlo drawdown/ruin for the trade book.

Puts the netting/bucketing/monthly-aggregation methodology in code, so that
every figure downstream (bootstrap Sharpe CI, Monte Carlo drawdown) comes from
one reviewed implementation rather than an ad hoc query. The netting rule is
what makes the difference: a stock campaign and the options overlapping it are
one unit, and a trade placed in two accounts is one unit, not two.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from common.db import connect  # noqa: E402
from common.db import target as db_target
from common.timezone import today_et  # noqa: E402

# Under scripts/, not data/ -- 2.0/data/ is gitignored wholesale (it holds the
# live DB and test fixtures) and *.csv is ignored repo-wide, so a committed
# CSV under data/ would silently never make it into git. JSON avoids both.
VOO_DATA_PATH = Path(__file__).resolve().parent / "data" / "voo_monthly_2024-01_2026-08.json"

BOOTSTRAP_N = 20000
SEED = 7

# §5/§9's Sharpe comparisons all use the same 24 *active* trading months
# (2024-06..2026-08 in the doc's snapshot) -- not a flat calendar range: 3
# calendar months in that span had zero closed trades and are naturally
# absent from monthly_series() already, and one earlier isolated legacy
# close (2024-01, a single stock sale) is dropped explicitly by this floor.
# Confirmed by reproducing the doc's exact 24-month VOO stats (mean 1.60%,
# sd 3.63%, Sharpe 1.53, cum +44.4%, 16/24 positive, worst -5.6%) to the
# last digit using this window.
SHARPE_WINDOW_START = "2024-06"

# §5's 6 scenario series: (label, exclude buckets, include-only buckets).
SCENARIOS = [
    ("everything as traded", None, None),
    ("minus condors/strangles", {"condor_strangle"}, None),
    ("minus concentrated equity", {"equity_campaign"}, None),
    ("minus both", {"condor_strangle", "equity_campaign"}, None),
    ("earnings options only", None, {"earnings_options"}),
    ("index options only", None, {"index_options"}),
]

# §2: "index_equity" / "index_options" bucket tickers. Netting (below) is
# scoped to single-name STOCK only -- an index STOCK row is never netted
# with options on the same ticker, per §2's wording.
INDEX_TICKERS = {"SPY", "SPX", "SPXW", "VOO", "VIX", "FXAIX", "VBR", "VEA", "VOE", "VTV", "VWO"}

CONDOR_STRANGLE_TYPES = {"IRON_CONDOR", "STRANGLE"}


@dataclass
class NettedUnit:
    symbol: str
    account_type: str
    sale_date: str
    net_pnl: float
    strategy_types: frozenset
    is_equity_campaign: bool


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def _accounts(account_type: str) -> set[str]:
    """A position's accounts: 'IRA', 'TAXABLE', or both for 'IRA+TAXABLE'."""
    return set(account_type.split("+"))


def net_campaigns(rows: list[dict]) -> list[NettedUnit]:
    """Apply §2's netting rule.

    Rows are positions (load_positions): a trade placed in both the IRA and
    TAXABLE is one row, account 'IRA+TAXABLE'. Each single-name STOCK row is
    grouped with every non-STOCK row on the same symbol that shares an account
    with it and whose [acquired_date, sale_date] window overlaps the stock's.
    Each such row is claimed by at most one campaign (earlier-acquired campaigns
    claim first). Index-ticker STOCK rows are never netted -- they, and options
    on the same index ticker, always stand alone. A stock held in one account
    never nets with an option held only in the other.

    Until 2026-09-15 the rows were `strategies`, a row per account: shares
    assigned in both accounts counted as two campaigns where the book made one
    trade, and every trade placed in both accounts was two half-size Monte
    Carlo draws.
    """
    stock_rows = [
        r for r in rows
        if r["strategy_type"] == "STOCK" and r["symbol"] not in INDEX_TICKERS
    ]
    claimed_ids: set = set()
    units: list[NettedUnit] = []

    for stock in sorted(stock_rows, key=lambda r: r["acquired_date"]):
        s_start, s_end = _parse_date(stock["acquired_date"]), _parse_date(stock["sale_date"])
        members = [stock]
        for r in rows:
            if r is stock or r["id"] in claimed_ids or r["strategy_type"] == "STOCK":
                continue
            if r["symbol"] != stock["symbol"]:
                continue
            if not _accounts(r["account_type"]) & _accounts(stock["account_type"]):
                continue
            r_start, r_end = _parse_date(r["acquired_date"]), _parse_date(r["sale_date"])
            if _overlaps(s_start, s_end, r_start, r_end):
                members.append(r)

        for m in members:
            claimed_ids.add(m["id"])

        units.append(NettedUnit(
            symbol=stock["symbol"],
            account_type=stock["account_type"],
            sale_date=max(m["sale_date"] for m in members),
            net_pnl=sum(m["gain_loss"] for m in members),
            strategy_types=frozenset(m["strategy_type"] for m in members),
            is_equity_campaign=True,
        ))

    for r in rows:
        if r["id"] in claimed_ids:
            continue
        units.append(NettedUnit(
            symbol=r["symbol"],
            account_type=r["account_type"],
            sale_date=r["sale_date"],
            net_pnl=r["gain_loss"],
            strategy_types=frozenset([r["strategy_type"]]),
            is_equity_campaign=False,
        ))

    return units


def bucket_unit(unit: NettedUnit) -> str:
    """§2's 5-way bucket assignment for a netted unit."""
    if unit.symbol in INDEX_TICKERS:
        return "index_equity" if "STOCK" in unit.strategy_types else "index_options"
    if unit.is_equity_campaign:
        return "equity_campaign"
    if unit.strategy_types & CONDOR_STRANGLE_TYPES:
        return "condor_strangle"
    return "earnings_options"


def bucket_totals(units: list[NettedUnit]) -> dict[str, tuple[int, int]]:
    """(count, total P&L rounded to the dollar) per bucket, §4's table shape."""
    n: dict[str, int] = defaultdict(int)
    total: dict[str, float] = defaultdict(float)
    for u in units:
        b = bucket_unit(u)
        n[b] += 1
        total[b] += u.net_pnl
    return {b: (n[b], round(total[b])) for b in n}


def monthly_series(units: list[NettedUnit]) -> dict[str, float]:
    """Sum net_pnl by calendar month of sale_date ("YYYY-MM" -> total), in
    calendar order: the stationary bootstrap resamples runs of adjacent months,
    so iteration order is part of the contract, not a display detail."""
    out: dict[str, float] = defaultdict(float)
    for u in units:
        out[u.sale_date[:7]] += u.net_pnl
    return {m: out[m] for m in sorted(out)}


def sharpe_ratio(monthly_values: list[float]) -> float | None:
    """Annualized Sharpe (rf=0) of a monthly P&L series: mean/sd * sqrt(12).

    None if there are fewer than 2 observations or the series has zero
    variance (undefined Sharpe) rather than raising or returning inf.
    """
    if len(monthly_values) < 2:
        return None
    sd = st.stdev(monthly_values)
    if sd == 0:
        return None
    return st.mean(monthly_values) / sd * math.sqrt(12)


def sortino_ratio(monthly_values: list[float], mar: float = 0.0) -> float | None:
    """Annualized Sortino of a monthly P&L series: (mean - mar) /
    downside_deviation * sqrt(12). Downside deviation is the semi-deviation
    below `mar` (target/minimum-acceptable-return, default 0):
    sqrt(mean(min(0, v - mar)^2)) over ALL observations (the standard N
    divisor for downside deviation -- not N-1; this measures spread around
    a fixed external target, not the sample's own mean, so Sharpe's N-1
    sample-variance convention doesn't apply here).

    None if there are fewer than 2 observations, or if no observation
    falls below `mar` (downside deviation is 0 -- nothing to divide by,
    and treating an all-upside series as "infinite Sortino" would be
    misleading rather than informative).
    """
    if len(monthly_values) < 2:
        return None
    downside_sq = [min(0.0, v - mar) ** 2 for v in monthly_values]
    downside_dev = math.sqrt(sum(downside_sq) / len(monthly_values))
    if downside_dev == 0:
        return None
    return (st.mean(monthly_values) - mar) / downside_dev * math.sqrt(12)


def trailing_window_series(
    monthly: dict[str, float], months: int, as_of: date | None = None
) -> list[float]:
    """Values from `monthly` ("YYYY-MM" -> P&L) whose month falls within the
    trailing `months` calendar months ending at `as_of` (default: today).
    months=1 keeps only as_of's own month. A calendar month with no trades
    has no key in `monthly` and is simply absent from the result -- never
    fabricated as a 0.0 data point.
    """
    if as_of is None:
        as_of = today_et()
    y, m = as_of.year, as_of.month
    keys = []
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return [monthly[k] for k in keys if k in monthly]


def stationary_bootstrap_resample(series: list[float], rng: random.Random, mean_block_len: float) -> list[float]:
    """Politis-Romano stationary bootstrap: circular blocks with geometric
    lengths (mean `mean_block_len`), so short-lag serial correlation in the
    original series is preserved in the resample rather than destroyed by
    i.i.d. resampling.
    """
    n = len(series)
    p = 1.0 / mean_block_len
    out: list[float] = []
    idx = rng.randrange(n)
    while len(out) < n:
        out.append(series[idx])
        idx = rng.randrange(n) if rng.random() < p else (idx + 1) % n
    return out


def bootstrap_sharpe_ci(
    monthly_values: list[float],
    n_boot: int = BOOTSTRAP_N,
    seed: int = SEED,
    mean_block_len: float | None = None,
) -> tuple[float | None, float | None, float | None]:
    """(point estimate, 2.5th pct, 97.5th pct) Sharpe via stationary bootstrap.

    Default block length follows the n^(1/3) rule of thumb for the mean
    geometric block length in a stationary bootstrap.
    """
    if mean_block_len is None:
        mean_block_len = max(1.0, len(monthly_values) ** (1 / 3))
    point = sharpe_ratio(monthly_values)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = stationary_bootstrap_resample(monthly_values, rng, mean_block_len)
        s = sharpe_ratio(sample)
        if s is not None:
            boots.append(s)
    if not boots:
        return point, None, None
    boots.sort()
    lo = boots[int(0.025 * len(boots))]
    hi = boots[int(0.975 * len(boots))]
    return point, lo, hi


def monte_carlo_drawdown(
    unit_pnls: list[float],
    n_sims: int = BOOTSTRAP_N,
    seed: int = SEED,
    rolling_window: int | None = None,
    drawdown_thresholds: tuple[float, ...] = (150_000.0, 250_000.0),
) -> dict:
    """Path-dependent risk from resampling netted trade-level P&Ls.

    Resamples `unit_pnls` with replacement into `n_sims` equity-curve
    orderings (each length len(unit_pnls)) and summarizes: the distribution
    of max drawdown, and how often a `rolling_window`-trade run nets
    negative. `rolling_window` defaults to half the trade count -- the real
    sample spans ~2 years, so half the trades approximates "a year" of
    trades; this is a trade-count proxy, not a real calendar window, since
    resampling reorders trades and cannot preserve actual dates.
    `drawdown_thresholds` default to $150k/$250k, bracketing observed
    pain in the book.
    """
    n = len(unit_pnls)
    if rolling_window is None:
        rolling_window = max(1, round(n / 2))
    rng = random.Random(seed)
    max_drawdowns: list[float] = []
    rolling_negative_count = 0

    for _ in range(n_sims):
        path = [unit_pnls[rng.randrange(n)] for _ in range(n)]

        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in path:
            cum += pnl
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        max_drawdowns.append(max_dd)

        window_sum = sum(path[:rolling_window])
        went_negative = window_sum < 0
        if not went_negative:
            running = window_sum
            for i in range(rolling_window, n):
                running += path[i] - path[i - rolling_window]
                if running < 0:
                    went_negative = True
                    break
        if went_negative:
            rolling_negative_count += 1

    max_drawdowns.sort()

    def pct(p: float) -> float:
        return max_drawdowns[min(len(max_drawdowns) - 1, int(p * len(max_drawdowns)))]

    return {
        "median_drawdown": pct(0.50),
        "p90_drawdown": pct(0.90),
        "p95_drawdown": pct(0.95),
        "p_drawdown_gt": {
            t: sum(1 for d in max_drawdowns if d > t) / n_sims for t in drawdown_thresholds
        },
        "p_rolling_window_negative": rolling_negative_count / n_sims,
        "rolling_window_trades": rolling_window,
    }


def load_positions(conn, *, sale_date_through: str | None = None) -> list[dict]:
    """Every row of the `positions` view, oldest-acquired first, with the columns
    net_campaigns() reads. A position is a trade: one placed in both accounts is
    one row (migration 5), so it is netted and drawn once, at its full size.

    `sale_date_through` restricts to trades closed on or before that date --
    used to pin a run to a fixed snapshot, independent of trades added since.
    Live runs (the actual bootstrap/Monte Carlo output) omit it and see the
    full current book.
    """
    query = ("SELECT id, symbol, strategy_type, account_type, acquired_date,"
             " sale_date, gain_loss FROM positions")
    params: tuple = ()
    if sale_date_through is not None:
        query += " WHERE sale_date <= %s"
        params = (sale_date_through,)
    # id (the lowest strategies id of the position) breaks same-day ties: physical
    # order changes on UPDATE, and the Monte Carlo maps each seeded draw to an
    # index in this list.
    query += " ORDER BY acquired_date, id"
    return [_isoify(r._asdict()) for r in conn.execute(query, params).fetchall()]


def _isoify(row: dict) -> dict:
    """Render date columns as ISO strings.

    This module treats dates as strings throughout -- NettedUnit.sale_date is
    typed `str`, monthly_series slices `sale_date[:7]`, and campaign overlap
    goes through _parse_date. Postgres hands back datetime.date objects, so
    normalise once here rather than making every consumer type-aware.
    """
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()}


def filter_units(
    units: list[NettedUnit],
    exclude: set[str] | None = None,
    include_only: set[str] | None = None,
) -> list[NettedUnit]:
    """Select units by §2 bucket, matching §5's scenario definitions.

    `include_only` takes precedence over `exclude` if both are given.
    Neither given returns every unit unfiltered.
    """
    if include_only is not None:
        return [u for u in units if bucket_unit(u) in include_only]
    if exclude:
        return [u for u in units if bucket_unit(u) not in exclude]
    return list(units)


def load_voo_monthly(path: Path = VOO_DATA_PATH) -> dict[str, float] | None:
    """VOO's cached monthly total-return series ("YYYY-MM" -> fractional
    return, e.g. 0.016 for +1.6%), or None if the cache file isn't present.
    Stored as {"YYYY-MM": monthly_return_pct} (percent, not fraction).
    """
    if not path.exists():
        return None
    with open(path) as f:
        raw = json.load(f)
    return {m: v / 100.0 for m, v in raw.items()}


def filter_monthly_series(series: dict[str, float], start: str | None = None) -> dict[str, float]:
    """Drop months before `start` ("YYYY-MM"). None returns every month."""
    if start is None:
        return dict(series)
    return {m: v for m, v in series.items() if m >= start}


def calendar_months(start: str, end: str) -> list[str]:
    """Every "YYYY-MM" from start to end inclusive."""
    year, month = int(start[:4]), int(start[5:7])
    out = []
    while f"{year}-{month:02d}" <= end:
        out.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def zero_filled(series: dict[str, float], months: list[str]) -> list[float]:
    """The series over exactly `months`, a month with no closed trade counting as 0.

    The active-month series (monthly_series) drops such months. That is a fair
    pairing for the whole book, which trades nearly every month, but it flatters
    a sparse sub-book against VOO, which is invested every month: on 2026-09-13
    earnings options alone read Sharpe 1.61 over 20 active months and 1.35 over
    the 27 calendar months, against VOO's 1.53 and 1.56.
    """
    return [series.get(m, 0.0) for m in months]


def voo_series_for_months(voo: dict[str, float], months: set[str]) -> list[float]:
    """VOO's returns for exactly `months`, sorted -- aligns VOO to the book's
    own active-month set (see SHARPE_WINDOW_START) rather than every
    calendar month VOO has a return for, matching §5's paired-month
    methodology. Months in `months` that VOO has no return for are skipped.
    """
    return [voo[m] for m in sorted(months) if m in voo]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bootstrap Sharpe CI + Monte Carlo drawdown/ruin for the trade book "
                     "(extends docs/SHARPE_ANALYSIS_2026-09.md)"
    )
    ap.add_argument("--n-boot", type=int, default=BOOTSTRAP_N)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--voo-data", type=Path, default=VOO_DATA_PATH)
    ap.add_argument("--through", metavar="YYYY-MM-DD",
                    help="only trades closed on or before this date; the Sep 2026 docs "
                         "quote --through 2026-08-18")
    args = ap.parse_args()

    conn = connect()
    try:
        rows = load_positions(conn, sale_date_through=args.through)
    finally:
        conn.close()

    units = net_campaigns(rows)
    voo = load_voo_monthly(args.voo_data)

    # The "everything as traded" active months anchor both the window floor
    # display and VOO's alignment -- see SHARPE_WINDOW_START.
    book_months = set(filter_monthly_series(monthly_series(units), start=SHARPE_WINDOW_START))

    print("=" * 70)
    print("BOOTSTRAP SHARPE CI + MONTE CARLO DRAWDOWN")
    print("=" * 70)
    print(f"db: {db_target()}")
    print(f"netted units: {len(units)}   total P&L: {sum(u.net_pnl for u in units):,.0f}")
    print(f"sharpe window: sale_date >= {SHARPE_WINDOW_START} ({len(book_months)} active months)")
    print(f"n_boot={args.n_boot}  seed={args.seed}")
    print()

    print(f"{'scenario':28s} {'Sharpe':>8s} {'stationary-boot 95% CI':>24s} {'n_mo':>5s}")
    print("-" * 70)
    for label, exclude, include_only in SCENARIOS:
        scenario_units = filter_units(units, exclude=exclude, include_only=include_only)
        series = list(filter_monthly_series(monthly_series(scenario_units), start=SHARPE_WINDOW_START).values())
        point, lo, hi = bootstrap_sharpe_ci(series, n_boot=args.n_boot, seed=args.seed)
        p_str = f"{point:.2f}" if point is not None else "n/a"
        ci_str = f"[{lo:.2f}, {hi:.2f}]" if lo is not None else "n/a"
        print(f"{label:28s} {p_str:>8s} {ci_str:>24s} {len(series):>5d}")

    if voo is not None:
        voo_series = voo_series_for_months(voo, book_months)
        point, lo, hi = bootstrap_sharpe_ci(voo_series, n_boot=args.n_boot, seed=args.seed)
        p_str = f"{point:.2f}" if point is not None else "n/a"
        ci_str = f"[{lo:.2f}, {hi:.2f}]" if lo is not None else "n/a"
        print(f"{'VOO':28s} {p_str:>8s} {ci_str:>24s} {len(voo_series):>5d}")
    else:
        print(f"{'VOO':28s} {'--- ' + str(args.voo_data.name) + ' not found ---':>38s}")
    print()

    # The same comparison over calendar months, a month with no close at 0 --
    # see zero_filled() for why the active-month table alone misleads.
    months = calendar_months(SHARPE_WINDOW_START, max(book_months))
    print(f"calendar months {months[0]}..{months[-1]} ({len(months)}), empty months count as 0")
    print(f"{'scenario':28s} {'Sharpe':>8s} {'stationary-boot 95% CI':>24s} {'n_mo':>5s}")
    print("-" * 70)
    for label, exclude, include_only in SCENARIOS:
        scenario_units = filter_units(units, exclude=exclude, include_only=include_only)
        series = zero_filled(monthly_series(scenario_units), months)
        point, lo, hi = bootstrap_sharpe_ci(series, n_boot=args.n_boot, seed=args.seed)
        p_str = f"{point:.2f}" if point is not None else "n/a"
        ci_str = f"[{lo:.2f}, {hi:.2f}]" if lo is not None else "n/a"
        print(f"{label:28s} {p_str:>8s} {ci_str:>24s} {len(series):>5d}")
    if voo is not None:
        voo_series = [voo[m] for m in months if m in voo]
        point, lo, hi = bootstrap_sharpe_ci(voo_series, n_boot=args.n_boot, seed=args.seed)
        p_str = f"{point:.2f}" if point is not None else "n/a"
        ci_str = f"[{lo:.2f}, {hi:.2f}]" if lo is not None else "n/a"
        print(f"{'VOO':28s} {p_str:>8s} {ci_str:>24s} {len(voo_series):>5d}")
    print()

    print(f"{'scenario':28s} {'median dd':>10s} {'p95 dd':>10s} "
          f"{'P(dd>150k)':>11s} {'P(dd>250k)':>11s} {'P(bad yr)':>10s}")
    print("-" * 70)
    for label, exclude, include_only in SCENARIOS:
        scenario_units = filter_units(units, exclude=exclude, include_only=include_only)
        pnls = [u.net_pnl for u in scenario_units]
        if len(pnls) < 2:
            print(f"{label:28s} {'n/a':>10s}")
            continue
        mc = monte_carlo_drawdown(pnls, n_sims=args.n_boot, seed=args.seed)
        pgt = mc["p_drawdown_gt"]
        print(f"{label:28s} {mc['median_drawdown']:10,.0f} {mc['p95_drawdown']:10,.0f} "
              f"{pgt[150_000.0]:11.1%} {pgt[250_000.0]:11.1%} "
              f"{mc['p_rolling_window_negative']:10.1%}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
