#!/usr/bin/env python3
"""
Store directional bias predictions for upcoming earnings.

This script analyzes skew for upcoming earnings and stores predictions
in the database for later validation against actual price moves, and for the
compound-risk recalibration (scripts/research/compound_risk_recalibration.py).

`analyze_and_store` is the one writer. Every analysis path reaches it: scan,
whisper, ticker mode, the Telegram bot and the morning digest through
scan/workflows/ticker_analysis.analyze_ticker, and `./trade.sh TICKER` through
this CLI. It keeps one row per event -- the first, taken before the reaction
session opens -- and writes only events the recalibration can grade.

Usage:
    # Store predictions for tickers reporting on one date (what trade.sh TICKER DATE runs)
    python scripts/store_bias_prediction.py AAPL TSLA --earnings-date 2025-12-04

    # Store predictions for all upcoming earnings (next 14 days)
    python scripts/store_bias_prediction.py --all
"""

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional, Tuple

# Run by path (trade.sh): 2.0/ for the `scripts.*` namespace.
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.constants import FCST_ERN_IV_THRESHOLD
from common.db import connect
from common.timezone import now_et
from src.config.config import Config
from src.container import Container
from src.domain.enums import DirectionalBias, EarningsTiming
from src.domain.reaction_session import reaction_open_cutoff
from src.domain.tail_risk import TRR_LOOKBACK_QUARTERS, tail_risk_level
from src.domain.trading_expiration import calculate_expiration_date
from src.domain.types import SizingContext
from src.infrastructure.database.migrations import apply_migrations
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)

# analyze_and_store's outcomes. SKIPPED is not an error: the event already has
# its prediction, is past its reaction open, or is not one the recalibration grades.
STORED, SKIPPED, FAILED = "stored", "skipped", "failed"


def resolve_compound_risk_context(
    ticker: str,
    skew_analysis,
    orats_enabled: bool,
) -> tuple:
    """
    Resolve (r_slp_30, fused_bias_value, sizing_alarm) for a prediction.

    `orats_enabled` is the caller's `config.api.orats_enabled`.
    When ORATS is enabled, reads the position_limits snapshot: sizing_alarm
    from fcst/iee (Rules A/B) and fused bias from r_slp_30 at ORATS
    confidence. When disabled (post-Jun-2026 sunset), the frozen snapshot is
    NOT read — sizing_alarm stays False, r_slp_30 stays None, and the fused
    bias is the Tradier bias unchanged (no second source exists; see
    skew_enhanced.fuse_skew_bias), matching analyzer._fuse_skew_signals so
    stored predictions agree with /analyze output.
    """
    from src.application.metrics.skew_enhanced import fuse_skew_bias

    r_slp_30 = None
    sizing_alarm = False

    if orats_enabled:
        from src.domain.types import snapshot_is_stale
        try:
            conn_pl = connect()
            row = conn_pl.execute(
                "SELECT r_slp_30, fcst_ern_iv_effect, iee_earn_effect, last_updated "
                "FROM position_limits WHERE ticker=%s",
                (ticker,)
            ).fetchone()
            conn_pl.close()
            if row and snapshot_is_stale(row[3]):
                logger.warning(
                    f"{ticker}: position_limits snapshot stale "
                    f"(last_updated={row[3]}) — ignoring. ORATS-era data, "
                    f"frozen 2026-06-24 and not refreshable."
                )
                row = None
            if row:
                r_slp_30, fcst_ern_iv_effect, iee_earn_effect = row[0], row[1], row[2]
                # Sizing alarm: fcst >= 2.0 (Rule A) OR iee/fcst >= 1.5 (Rule B)
                if fcst_ern_iv_effect is not None and fcst_ern_iv_effect >= 2.0:
                    sizing_alarm = True
                if (fcst_ern_iv_effect is not None and fcst_ern_iv_effect > 0
                        and iee_earn_effect is not None
                        and iee_earn_effect / fcst_ern_iv_effect >= 1.5):
                    sizing_alarm = True
        except Exception as e:
            logger.debug(f"{ticker}: position_limits fetch failed (non-critical): {e}")

    fused = fuse_skew_bias(
        tradier_bias=skew_analysis.directional_bias,
        tradier_conf=skew_analysis.bias_confidence,
        slope_atm=skew_analysis.slope_atm,
        r_slp_30=r_slp_30,
    )
    return r_slp_30, fused.value if fused is not None else None, sizing_alarm


def compound_risk_active(
    trr_level: Optional[str],
    sizing_alarm: bool,
    fused_bias_value: Optional[str],
) -> bool:
    """The engine's verdict on the stored inputs: SizingContext.compound_risk_active.

    Not re-derived here, so the flag a prediction stores and the cap /analyze
    applies cannot drift. The stored alarm is a flag, not the ORATS readings
    behind it, so it reaches SizingContext as an fcst reading at the Rule A
    threshold -- which fires iv_effect_reduction exactly as the readings did.
    """
    ctx = SizingContext(
        trr_level=trr_level,
        fcst_ern_iv_effect=FCST_ERN_IV_THRESHOLD if sizing_alarm else None,
        fused_bias=DirectionalBias(fused_bias_value) if fused_bias_value else None,
    )
    return ctx.compound_risk_active


def store_bias_prediction(
    conn,
    ticker: str,
    earnings_date: date,
    expiration: date,
    stock_price: float,
    skew_analysis,
    predicted_at: datetime,
    r_slp_30: Optional[float] = None,
    fused_bias_value: Optional[str] = None,
    trr_level: Optional[str] = None,
    sizing_alarm: bool = False,
) -> bool:
    """
    Insert a directional bias prediction; True if this call wrote the row.

    The event's first prediction is kept: a row already stored for (ticker,
    earnings_date) wins, and this call writes nothing. The caller owns the
    transaction (`with pool.connection() as conn:` commits on exit).

    Args:
        conn: Open psycopg connection
        ticker: Stock symbol
        earnings_date: Earnings announcement date
        expiration: Option expiration date
        stock_price: Current stock price
        skew_analysis: SkewAnalysis object from skew_enhanced.py
        predicted_at: When the prediction was taken, tz-aware
        r_slp_30: ORATS 30-day put/call skew slope at prediction time
        fused_bias_value: Fused directional bias string (e.g. "strong_bearish")
        trr_level: Tail risk level at prediction time ("LOW"|"NORMAL"|"HIGH")
        sizing_alarm: True if fcst_ern_iv_effect>=2.0 or iee/fcst>=1.5
    """
    bias = skew_analysis.directional_bias
    cursor = conn.execute("""
        INSERT INTO bias_predictions (
            ticker, earnings_date, expiration,
            stock_price, predicted_at,
            skew_atm, skew_curvature, skew_strength, slope_atm,
            directional_bias, bias_strength, bias_confidence,
            r_squared, num_points,
            compound_risk_active, r_slp_30, fused_bias, trr_level, sizing_alarm
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        -- DO NOTHING, not DO UPDATE (until 2026-09-14): a re-run replaced the
        -- forecast with a later read, after the reaction open when run late, and
        -- the recalibration could only grade the latest one. The first
        -- prediction is the one no later look chose -- the same reason
        -- ab_evaluate_live grades the first pre-open analysis. Outcome columns
        -- (actual_*, prediction_correct, validated_at) are validation's and
        -- are never touched here.
        ON CONFLICT (ticker, earnings_date) DO NOTHING
    """, (
        ticker,
        earnings_date,
        expiration,
        stock_price,
        # Tz-aware (analyze_and_store passes now_et()), never datetime.now():
        # this column is timestamptz, and a NAIVE datetime is parsed by Postgres
        # in the SESSION timezone (UTC in the container), so an ET wall clock
        # lands four hours early. Same defect class as analysis_log.timestamp
        # and sync_earnings_calendar's staleness maths.
        predicted_at,
        skew_analysis.skew_atm.value,
        skew_analysis.curvature,
        skew_analysis.strength,
        skew_analysis.slope_atm,
        bias.value,  # Store enum value (e.g., "strong_bullish")
        bias.strength(),
        skew_analysis.bias_confidence,
        skew_analysis.confidence,
        skew_analysis.num_points,
        compound_risk_active(trr_level, sizing_alarm, fused_bias_value),
        r_slp_30,
        fused_bias_value,
        trr_level,
        bool(sizing_alarm),
    ))
    return cursor.rowcount == 1


class ScannedSkew(NamedTuple):
    """A skew reading the caller already took, for analyze_and_store to reuse."""
    requested: date   # the trading expiration the caller computed, before snapping
    expiration: date  # the listed expiration its chain was read on
    result: Any       # analyze_skew_curve's Result for that chain


# One round trip: the stored timing tag (the one the recalibration grades the
# reaction with) and whether the event already has its prediction.
_WINDOW_QUERY = """
SELECT ec.timing,
       EXISTS (SELECT 1 FROM bias_predictions bp
               WHERE bp.ticker = ec.ticker AND bp.earnings_date = ec.earnings_date) AS predicted
FROM earnings_calendar ec
WHERE ec.ticker = %s AND ec.earnings_date = %s
"""


def prediction_cutoff(pool, ticker: str, earnings_date: date,
                      now: datetime) -> Tuple[Optional[datetime], Optional[str]]:
    """(reaction open, None) while this event can take its prediction, else (None, why not).

    Each rule is a row the compound-risk recalibration could not grade:
      - not in earnings_calendar: record_moves records a reaction only for a
        stored event, and the cutoff needs the stored timing tag;
      - already predicted: the first pre-open prediction is kept;
      - at or after the reaction session's open, by the stored tag and the
        grader's own rule (src/domain/reaction_session.py) -- a post-open read
        measures the collapsed chain, not the forecast.
    Checked before any chain is read, so a repeat analysis costs one query.
    """
    with pool.connection() as conn:
        row = conn.execute(_WINDOW_QUERY, (ticker, earnings_date)).fetchone()
    if row is None:
        return None, "not in earnings_calendar, so no reaction will be graded"
    if row.predicted:
        return None, "already predicted (the first pre-open prediction is kept)"
    cutoff = reaction_open_cutoff(earnings_date, row.timing)
    if now >= cutoff:
        return None, f"the reaction session opened {cutoff:%Y-%m-%d %H:%M} ET ({row.timing})"
    return cutoff, None


def analyze_and_store(
    container: Container,
    ticker: str,
    earnings_date: date,
    scanned: Optional[ScannedSkew] = None,
) -> str:
    """
    Analyze skew and store the bias prediction for one earnings event.

    Args:
        scanned: the caller's own skew reading. Reused when the caller asked for
            the same trading expiration, so a scan pays no second chain read;
            otherwise (a custom --expiration-offset) this reads its own chain.

    Returns:
        STORED, SKIPPED (see prediction_cutoff) or FAILED. Never raises: a
        failure is a WARNING with its traceback.
    """
    try:
        cutoff, why_not = prediction_cutoff(container.db_pool, ticker, earnings_date, now_et())
        if why_not:
            logger.info(f"{ticker}: no bias prediction for {earnings_date} — {why_not}")
            return SKIPPED

        # Expiration: the TRADING expiration (weekly Friday at least MIN_DTE out),
        # snapped to a listed expiration via Tradier — must match what
        # trade.sh's calculate_expiration() computes and passes to
        # analyze.py, since a stored bias prediction is only meaningful if
        # it's fit against the same chain the trade is actually taken on.
        # The near-dated implied-move expiration (first one containing the
        # reaction, used for VRP/implied-move math) was used here instead
        # until Aug 27 2026 — for a Thursday reporter that's a 1-DTE chain
        # vs the 8-DTE chain /analyze actually shows, enough to flip both
        # the sign and shape of the skew fit (see git history / CLAUDE.md
        # Incident History for the MRVL/S divergence that surfaced this).
        desired_expiration = calculate_expiration_date(
            earnings_date, EarningsTiming.UNKNOWN, min_dte=container.config.thresholds.min_dte
        )
        if scanned is not None and scanned.requested == desired_expiration:
            # The scan asked for the same Friday and snapped it against the same
            # listing: its chain is this prediction's chain.
            expiration, skew_result = scanned.expiration, scanned.result
        else:
            if container.skew_analyzer is None:
                logger.warning(f"{ticker}: no bias prediction for {earnings_date} — "
                               f"skew analysis is disabled (USE_ENHANCED_SKEW=false)")
                return SKIPPED
            exp_result = container.cached_options_provider.find_nearest_expiration(ticker, desired_expiration)
            expiration = exp_result.value if exp_result.is_ok else desired_expiration
            skew_result = container.skew_analyzer.analyze_skew_curve(ticker, expiration)

        if skew_result.is_err:
            # A chain too thin for a 25Δ fit: a data condition, not a fault (the
            # scan shows NEUTRAL for it). The recalibration cannot group the event.
            logger.info(f"{ticker}: no bias prediction for {earnings_date} — "
                        f"no skew reading: {skew_result.error}")
            return SKIPPED

        skew_analysis = skew_result.value
        stock_price = float(skew_analysis.stock_price.amount)

        # Resolve compound risk context (ORATS-gated — frozen snapshot is
        # never read when ORATS_ENABLED is false; fused bias is then the
        # Tradier bias unchanged, matching /analyze output)
        try:
            r_slp_30, fused_bias_value, sizing_alarm = resolve_compound_risk_context(
                ticker, skew_analysis, orats_enabled=container.config.api.orats_enabled
            )
        except Exception:
            # Stored anyway, with no fused bias: compound_risk_active reads
            # false. At DEBUG that row looked like a clean NEITHER.
            logger.warning(f"{ticker}: compound-risk context failed — prediction stored "
                           f"without a fused bias", exc_info=True)
            r_slp_30, fused_bias_value, sizing_alarm = None, None, False

        # TRR from the stored gap moves, as the analyzer computes it. Until
        # 2026-09-14 this ran only after a VRP lookup that always failed
        # (VRPCalculator has no calculate_vrp), so trr_level was NULL on every
        # row from June 2026 and compound_risk_active could not be true.
        trr_level = None
        try:
            hist_result = container.prices_repository.get_historical_moves(
                ticker, limit=TRR_LOOKBACK_QUARTERS)
            if hist_result.is_ok:
                trr_level = tail_risk_level(
                    m.gap_move_pct.value for m in hist_result.value if m.gap_move_pct is not None)
        except Exception:
            logger.warning(f"{ticker}: TRR failed — prediction stored with trr_level NULL",
                           exc_info=True)

        # The chain read can take a while under the rate limiter: a prediction
        # taken at or after the open is not one.
        predicted_at = now_et()
        if predicted_at >= cutoff:
            logger.info(f"{ticker}: no bias prediction for {earnings_date} — "
                        f"the reaction session opened while the chain was read")
            return SKIPPED

        with container.db_pool.connection() as conn:
            stored = store_bias_prediction(
                conn,
                ticker,
                earnings_date,
                expiration,
                stock_price,
                skew_analysis,
                predicted_at,
                r_slp_30=r_slp_30,
                fused_bias_value=fused_bias_value,
                trr_level=trr_level,
                sizing_alarm=sizing_alarm,
            )
        if not stored:
            logger.info(f"{ticker}: no bias prediction for {earnings_date} — "
                        f"a concurrent run stored the first one")
            return SKIPPED

        logger.info(
            f"{ticker}: ✓ Stored prediction - {skew_analysis.directional_bias.value} "
            f"(confidence={skew_analysis.bias_confidence:.2f})"
        )
        return STORED

    except Exception:
        logger.warning(
            f"{ticker}: bias prediction failed — {earnings_date} is missing from the "
            f"compound-risk recalibration",
            exc_info=True,
        )
        return FAILED


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {value}") from None


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Store directional bias predictions for upcoming earnings"
    )

    parser.add_argument(
        'tickers',
        nargs='*',
        help='Ticker symbols to analyze, all for --earnings-date'
    )
    # Required with tickers. Until 2026-09-14 the CLI took each ticker's next
    # calendar date on or after today, and trade.sh passed no date, so
    # `./trade.sh TICKER DATE` predicted whichever event came next, not DATE.
    parser.add_argument(
        '--earnings-date',
        type=_iso_date,
        help='The earnings date the tickers report on (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Analyze all upcoming earnings in the next --days-ahead days'
    )
    parser.add_argument(
        '--days-ahead',
        type=int,
        default=14,
        help='Days ahead to look for earnings (default: 14)'
    )

    args = parser.parse_args()
    if args.all and (args.tickers or args.earnings_date):
        parser.error("--all takes every calendared event; give it no tickers or --earnings-date")
    if not args.all:
        if not args.tickers:
            parser.error("give tickers with --earnings-date, or --all")
        if args.earnings_date is None:
            parser.error("--earnings-date is required with tickers")

    # Setup logging
    setup_logging()

    # Initialize container; migrate once, before this writes bias_predictions
    config = Config.from_env()
    apply_migrations()
    container = Container(config)
    # `db_path` was removed at the Postgres port: it was threaded through
    # these signatures but never used to connect -- every access goes
    # through common.db.connect(), which reads PG* from the environment.
    # Left in place it pointed at 2.0/data/ivcrush.db, a file that no
    # longer exists.

    print("=" * 70)
    print("DIRECTIONAL BIAS PREDICTION STORAGE")
    print("=" * 70)

    # Determine which events to analyze. An event missing from earnings_calendar
    # is SKIPPED by analyze_and_store (prediction_cutoff), not guessed at.
    if args.all:
        print(f"\nFetching upcoming earnings (next {args.days_ahead} days)...")
        upcoming = container.earnings_repository.get_upcoming_earnings(args.days_ahead)
        if upcoming.is_err:
            print(f"\n❌ Upcoming earnings unavailable: {upcoming.error}")
            return 1
        earnings_to_analyze = upcoming.value
        print(f"Found {len(earnings_to_analyze)} upcoming earnings events")
    else:
        earnings_to_analyze = [(ticker.upper(), args.earnings_date) for ticker in args.tickers]

    if not earnings_to_analyze:
        print("\n❌ No earnings events to analyze")
        return 1

    # Analyze and store predictions
    print(f"\nAnalyzing {len(earnings_to_analyze)} earnings events...")
    print("-" * 70)

    outcomes = {STORED: 0, SKIPPED: 0, FAILED: 0}

    for ticker, earnings_date in earnings_to_analyze:
        print(f"\n{ticker} - {earnings_date}:")
        outcomes[analyze_and_store(container, ticker, earnings_date)] += 1

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total: {len(earnings_to_analyze)}")
    print(f"  ✓ Stored: {outcomes[STORED]}")
    print(f"  – Skipped: {outcomes[SKIPPED]} (already predicted, past the reaction open, "
          f"no skew reading, or not calendared)")
    print(f"  ✗ Failed: {outcomes[FAILED]}")
    print("=" * 70)

    return 0 if outcomes[FAILED] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
