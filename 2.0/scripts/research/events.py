"""Shared event loading and outcome measurement for IV-crush research.

One implementation of "load events -> derive implied move -> measure outcome".
Two harnesses previously reimplemented this independently and diverged: the
services/backtest/ package hardcoded implied = avg_move * 1.4 (making VRP a
constant, unable to discriminate) while the scoring-factor A/B used real
straddle prices but modelled no costs. That package is retired; this module is
the single source.

Outcomes are measured on a SHORT STRADDLE HELD TO EXPIRY, net of costs -- not
the defined-risk next-day-exit spread actually traded, because no historical
option chains are stored. Label results a straddle proxy wherever cited.

Stdlib only, matching ab_evaluate_live.py.
"""

from datetime import date, timedelta

from common.db import connect  # noqa: E402

DEFAULT_LOOKBACK_QUARTERS = 12   # matches analyzer.py:88
MIN_PRIOR_QUARTERS = 4           # matches config.py min_historical_quarters
MIN_PRIOR_FOR_CRUSH_RATE = 3     # below this a trailing rate is noise, so None

# The two VRP DENOMINATOR conventions, both kept reproducible on purpose.
# `close` is what the LIVE system grades on since the 2026-09 migration.
# `intraday` (live before) stays so intraday-era studies reproduce.
BASELINE_COLUMNS = {"close": "close_move_pct", "intraday": "intraday_move_pct"}

# The OUTCOME is deliberately NOT configurable. It is always the gap-inclusive
# close move -- what a short premium position actually experiences overnight.
# An A/B that varied the outcome alongside the denominator would not be
# comparing denominators at all; holding it fixed is what makes the comparison
# mean anything. This mirrors the original scoring-factor A/B design
# (research/scoring_factors.py).
OUTCOME_COLUMN = "close_move_pct"


def load_events(
    start: date | None = None,
    end: date | None = None,
    lookback: int = DEFAULT_LOOKBACK_QUARTERS,
    min_prior: int = MIN_PRIOR_QUARTERS,
    baseline: str = "close",
) -> list[dict]:
    """Load earnings events with point-in-time prior history and outcomes.

    An event is usable only if it has a real pre_earnings_straddle_pct, a
    realised gap-inclusive close move, and at least `min_prior`
    strictly-earlier moves in the chosen baseline column. Events without a
    straddle are SKIPPED, never defaulted.

    `baseline` selects the VRP DENOMINATOR only (see BASELINE_COLUMNS); it
    defaults to `close` to match the live system. The outcome
    (`actual`/`crushed`/`edge`) is always the gap-inclusive close move
    regardless -- see OUTCOME_COLUMN for why that must not vary.
    """
    if baseline not in BASELINE_COLUMNS:
        raise ValueError(
            f"baseline must be one of {sorted(BASELINE_COLUMNS)}, got {baseline!r}"
        )
    col = BASELINE_COLUMNS[baseline]

    conn = connect()
    # `col` comes only from BASELINE_COLUMNS, never from caller input, so this
    # interpolation cannot carry injection.
    rows = conn.execute(
        f"""SELECT ticker, earnings_date, {OUTCOME_COLUMN} AS outcome_pct,
                   {col} AS baseline_pct, pre_earnings_straddle_pct,
                   ern_iv_effect
            FROM historical_moves
            ORDER BY ticker, earnings_date"""
    ).fetchall()
    rows = [r._asdict() for r in rows]
    conn.close()

    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    events: list[dict] = []
    for ticker, series in by_ticker.items():
        for i, r in enumerate(series):
            straddle = r["pre_earnings_straddle_pct"]
            actual = r["outcome_pct"]
            if straddle is None or actual is None:
                continue

            ed = r["earnings_date"]
            if (start and ed < start) or (end and ed > end):
                continue

            # Point-in-time: strictly earlier rows only, most recent `lookback`.
            prior = [
                abs(p["baseline_pct"])
                for p in series[:i]
                if p["baseline_pct"] is not None
            ][-lookback:]
            if len(prior) < min_prior:
                continue

            mean_prior = sum(prior) / len(prior)
            if mean_prior <= 0:
                continue

            # Trailing crush frequency, point-in-time like everything else:
            # only prior events that carried both a straddle and a move.
            prior_crush = [
                (p["pre_earnings_straddle_pct"], abs(p["baseline_pct"]))
                for p in series[:i]
                if p["pre_earnings_straddle_pct"] and p["baseline_pct"] is not None
            ][-lookback:]
            crush_rate = (
                sum(1 for s, a in prior_crush if s > a) / len(prior_crush)
                if len(prior_crush) >= MIN_PRIOR_FOR_CRUSH_RATE
                else None
            )

            actual_abs = abs(actual)
            events.append(
                {
                    "ticker": ticker,
                    "earnings_date": ed,
                    "straddle": straddle,
                    "actual": actual_abs,
                    "prior_moves": prior,
                    "mean_prior": mean_prior,
                    "vrp": straddle / mean_prior,
                    "prior_crush_rate": crush_rate,
                    # The event's OWN ern_iv_effect -- trailing, known only
                    # post-quarter, so any use of it is post-hoc by
                    # construction. Frozen at the 2026-05-21 ORATS sunset.
                    "iv_effect": r["ern_iv_effect"],
                    "crushed": 1 if straddle > actual_abs else 0,
                    "edge": straddle - actual_abs,
                }
            )
    return events


def net_edge(
    edge: float,
    straddle: float,
    bid_ask_spread_pct: float = 0.10,
    commission_per_contract: float = 0.65,
    stock_price: float = 100.0,
) -> float:
    """Apply round-trip execution costs to a gross edge, in percentage points.

    Salvaged from the retired services/backtest/math.py. Entry sells the
    straddle at bid and exit buys it back at ask, so each side gives up half
    the spread. Commission covers 4 contracts (call + put, entry + exit).
    """
    entry_slippage = (straddle / 2) * (bid_ask_spread_pct / 2)
    residual = straddle * 0.10  # residual extrinsic value after the event
    exit_slippage = residual * (bid_ask_spread_pct / 2)
    commission_pct = ((4 * commission_per_contract) / 100) / stock_price * 100
    return edge - entry_slippage - exit_slippage - commission_pct


def walk_forward_windows(
    start: date,
    end: date,
    train_days: int = 180,
    test_days: int = 90,
    step_days: int = 90,
) -> list[tuple[date, date, date, date]]:
    """Rolling (train_start, train_end, test_start, test_end) windows.

    Salvaged from the retired services/backtest/runner.py. Windows whose test
    period would run past `end` are dropped rather than truncated.
    """
    windows = []
    train_start = start
    while True:
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        windows.append((train_start, train_end, test_start, test_end))
        train_start += timedelta(days=step_days)
    return windows
