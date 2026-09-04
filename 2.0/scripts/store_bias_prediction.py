#!/usr/bin/env python3
"""
Store directional bias predictions for upcoming earnings.

This script analyzes skew for upcoming earnings and stores predictions
in the database for later validation against actual price moves.

Usage:
    # Store predictions for specific tickers
    python scripts/store_bias_prediction.py AAPL TSLA CRM

    # Store predictions for all upcoming earnings (next 14 days)
    python scripts/store_bias_prediction.py --all

    # Store for specific date range
    python scripts/store_bias_prediction.py --start 2025-12-01 --end 2025-12-07
"""

import sys
import argparse
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.config import Config
from src.container import Container
from src.domain.enums import DirectionalBias, EarningsTiming
from scripts.scan.date_utils import calculate_expiration_date

logger = logging.getLogger(__name__)


def resolve_compound_risk_context(
    db_path: str,
    ticker: str,
    skew_analysis,
    orats_enabled: Optional[bool] = None,
) -> tuple:
    """
    Resolve (r_slp_30, fused_bias_value, sizing_alarm) for a prediction.

    When ORATS is enabled, reads the position_limits snapshot: sizing_alarm
    from fcst/iee (Rules A/B) and fused bias from r_slp_30 at ORATS
    confidence. When disabled (post-Jun-2026 sunset), the frozen snapshot is
    NOT read — sizing_alarm stays False, r_slp_30 stays None, and the fused
    bias comes from the Tradier slope_atm proxy at reduced confidence,
    matching analyzer._fuse_skew_signals so stored predictions agree with
    /analyze output.
    """
    from src.application.metrics.skew_enhanced import fuse_skew_bias
    import sys as _sys
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parent.parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from common.constants import ORATS_ENABLED  # noqa: E402

    if orats_enabled is None:
        orats_enabled = ORATS_ENABLED

    r_slp_30 = None
    sizing_alarm = False

    if orats_enabled:
        from src.domain.types import snapshot_is_stale
        try:
            conn_pl = sqlite3.connect(db_path)
            row = conn_pl.execute(
                "SELECT r_slp_30, fcst_ern_iv_effect, iee_earn_effect, last_updated "
                "FROM position_limits WHERE ticker=?",
                (ticker,)
            ).fetchone()
            conn_pl.close()
            if row and snapshot_is_stale(row[3]):
                logger.warning(
                    f"{ticker}: position_limits snapshot stale "
                    f"(last_updated={row[3]}) — ignoring; run refresh_orats_snapshots.py"
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


def store_bias_prediction(
    db_path: str,
    ticker: str,
    earnings_date: date,
    expiration: date,
    stock_price: float,
    skew_analysis,
    vrp_result=None,
    r_slp_30: Optional[float] = None,
    fused_bias_value: Optional[str] = None,
    trr_level: Optional[str] = None,
    sizing_alarm: bool = False,
):
    """
    Store a directional bias prediction in the database.

    Args:
        db_path: Path to database
        ticker: Stock symbol
        earnings_date: Earnings announcement date
        expiration: Option expiration date
        stock_price: Current stock price
        skew_analysis: SkewAnalysis object from skew_enhanced.py
        vrp_result: Optional VRPResult object
        r_slp_30: ORATS 30-day put/call skew slope at prediction time
        fused_bias_value: Fused directional bias string (e.g. "strong_bearish")
        trr_level: Tail risk level at prediction time ("LOW"|"NORMAL"|"HIGH")
        sizing_alarm: True if fcst_ern_iv_effect>=2.0 or iee/fcst>=1.5
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Extract data from skew analysis
        bias = skew_analysis.directional_bias

        # Compound risk: ≥2 of [TRR HIGH, sizing alarm (fcst≥2.0 or iee/fcst≥1.5), BEARISH/STRONG_BEARISH fused skew]
        compound_signals = sum([
            trr_level == 'HIGH',
            sizing_alarm,
            fused_bias_value in ('bearish', 'strong_bearish'),
        ])
        compound_risk_active = 1 if compound_signals >= 2 else 0

        cursor.execute("""
            INSERT OR REPLACE INTO bias_predictions (
                ticker, earnings_date, expiration,
                stock_price, predicted_at,
                skew_atm, skew_curvature, skew_strength, slope_atm,
                directional_bias, bias_strength, bias_confidence,
                r_squared, num_points,
                vrp_ratio, implied_move_pct, historical_mean_pct,
                compound_risk_active, r_slp_30, fused_bias, trr_level, sizing_alarm
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker,
            earnings_date,
            expiration,
            stock_price,
            datetime.now(),
            skew_analysis.skew_atm.value,
            skew_analysis.curvature,
            skew_analysis.strength,
            skew_analysis.slope_atm,
            bias.value,  # Store enum value (e.g., "strong_bullish")
            bias.strength(),
            skew_analysis.bias_confidence,
            skew_analysis.confidence,
            skew_analysis.num_points,
            vrp_result.vrp_ratio if vrp_result else None,
            vrp_result.implied_move.value if vrp_result else None,
            vrp_result.historical_mean.value if vrp_result else None,
            compound_risk_active,
            r_slp_30,
            fused_bias_value,
            trr_level,
            1 if sizing_alarm else 0,
        ))

        conn.commit()
        logger.info(
            f"Stored bias prediction: {ticker} - {bias.value} "
            f"(strength={bias.strength()}, confidence={skew_analysis.bias_confidence:.2f})"
        )

    except Exception as e:
        logger.error(f"Failed to store bias prediction for {ticker}: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def analyze_and_store(
    container: Container,
    ticker: str,
    earnings_date: date,
    db_path: str
) -> bool:
    """
    Analyze skew and store bias prediction for a ticker.

    Returns:
        True if successful, False otherwise
    """
    try:
        # Expiration: the TRADING expiration (min-3-DTE weekly Friday),
        # snapped to a listed expiration via Tradier — must match what
        # trade.sh's calculate_expiration() computes and passes to
        # analyze.py, since a stored bias prediction is only meaningful if
        # it's fit against the same chain the trade is actually taken on.
        # calculate_implied_move_expiration (the near-dated earnings+1
        # expiration used for VRP/implied-move math) was used here instead
        # until Aug 27 2026 — for a Thursday reporter that's a 1-DTE chain
        # vs the 8-DTE chain /analyze actually shows, enough to flip both
        # the sign and shape of the skew fit (see git history / CLAUDE.md
        # Incident History for the MRVL/S divergence that surfaced this).
        desired_expiration = calculate_expiration_date(earnings_date, EarningsTiming.UNKNOWN)
        exp_result = container.tradier.find_nearest_expiration(ticker, desired_expiration)
        expiration = exp_result.value if exp_result.is_ok else desired_expiration

        # Analyze skew
        skew_analyzer = container.skew_analyzer
        skew_result = skew_analyzer.analyze_skew_curve(ticker, expiration)

        if skew_result.is_err:
            logger.warning(f"{ticker}: Skew analysis failed - {skew_result.error}")
            return False

        skew_analysis = skew_result.value
        stock_price = float(skew_analysis.stock_price.amount)

        # Optionally get VRP for context
        vrp_result = None
        try:
            vrp_calculator = container.vrp_calculator
            vrp_res = vrp_calculator.calculate_vrp(ticker, earnings_date)
            if vrp_res.is_ok:
                vrp_result = vrp_res.value
        except Exception as e:
            logger.debug(f"{ticker}: VRP calculation failed (non-critical): {e}")

        # Resolve compound risk context (ORATS-gated — frozen snapshot is
        # never read when ORATS_ENABLED is false; fused bias falls back to
        # the Tradier slope_atm proxy, matching /analyze output)
        trr_level = None
        try:
            r_slp_30, fused_bias_value, sizing_alarm = resolve_compound_risk_context(
                db_path, ticker, skew_analysis
            )
        except Exception as e:
            logger.debug(f"{ticker}: Compound risk context fetch failed (non-critical): {e}")
            r_slp_30, fused_bias_value, sizing_alarm = None, None, False

        # Compute TRR level from VRP historical context if available
        if vrp_result is not None:
            try:
                hist_result = container.prices_repository.get_historical_moves(ticker, limit=12)
                if hist_result.is_ok:
                    gap_moves = [abs(m.gap_move_pct.value) for m in hist_result.value if m.gap_move_pct]
                    if len(gap_moves) >= 2:
                        avg_g = sum(gap_moves) / len(gap_moves)
                        max_g = max(gap_moves)
                        trr = max_g / avg_g if avg_g > 0 else 0
                        trr_level = 'HIGH' if trr > 2.5 else ('NORMAL' if trr >= 1.5 else 'LOW')
            except Exception as e:
                logger.debug(f"{ticker}: TRR computation failed (non-critical): {e}")

        # Store prediction
        store_bias_prediction(
            db_path,
            ticker,
            earnings_date,
            expiration,
            stock_price,
            skew_analysis,
            vrp_result,
            r_slp_30=r_slp_30,
            fused_bias_value=fused_bias_value,
            trr_level=trr_level,
            sizing_alarm=sizing_alarm,
        )

        logger.info(
            f"{ticker}: ✓ Stored prediction - {skew_analysis.directional_bias.value} "
            f"(confidence={skew_analysis.bias_confidence:.2f})"
        )
        return True

    except Exception as e:
        logger.error(f"{ticker}: Failed to analyze and store - {e}")
        return False


def get_upcoming_earnings(
    container: Container,
    days_ahead: int = 14
) -> List[tuple]:
    """
    Get upcoming earnings events.

    Returns:
        List of (ticker, earnings_date) tuples
    """
    earnings_repo = container.earnings_repository
    today = datetime.now().date()
    end_date = today + timedelta(days=days_ahead)

    # Query database directly for upcoming earnings
    conn = sqlite3.connect(container.config.database.path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ticker, earnings_date
        FROM earnings_calendar
        WHERE earnings_date >= ? AND earnings_date <= ?
        ORDER BY earnings_date
    """, (today, end_date))

    results = cursor.fetchall()
    conn.close()

    return [(row[0], datetime.strptime(row[1], '%Y-%m-%d').date()) for row in results]


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Store directional bias predictions for upcoming earnings"
    )

    parser.add_argument(
        'tickers',
        nargs='*',
        help='Ticker symbols to analyze'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Analyze all upcoming earnings in next 14 days'
    )
    parser.add_argument(
        '--days-ahead',
        type=int,
        default=14,
        help='Days ahead to look for earnings (default: 14)'
    )
    parser.add_argument(
        '--start',
        type=str,
        help='Start date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end',
        type=str,
        help='End date (YYYY-MM-DD)'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    # Initialize container
    config = Config.from_env()
    container = Container(config)
    db_path = str(config.database.path)

    print("=" * 70)
    print("DIRECTIONAL BIAS PREDICTION STORAGE")
    print("=" * 70)

    # Determine which tickers to analyze
    earnings_to_analyze = []

    if args.all:
        print(f"\nFetching upcoming earnings (next {args.days_ahead} days)...")
        earnings_to_analyze = get_upcoming_earnings(container, args.days_ahead)
        print(f"Found {len(earnings_to_analyze)} upcoming earnings events")

    elif args.tickers:
        # Get earnings dates for specified tickers
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for ticker in args.tickers:
            cursor.execute("""
                SELECT earnings_date
                FROM earnings_calendar
                WHERE ticker = ?
                AND earnings_date >= date('now')
                ORDER BY earnings_date
                LIMIT 1
            """, (ticker,))

            row = cursor.fetchone()
            if row:
                earnings_date = datetime.strptime(row[0], '%Y-%m-%d').date()
                earnings_to_analyze.append((ticker, earnings_date))
            else:
                logger.warning(f"{ticker}: No upcoming earnings found")

        conn.close()
    else:
        parser.print_help()
        return 1

    if not earnings_to_analyze:
        print("\n❌ No earnings events to analyze")
        return 1

    # Analyze and store predictions
    print(f"\nAnalyzing {len(earnings_to_analyze)} earnings events...")
    print("-" * 70)

    success_count = 0
    fail_count = 0

    for ticker, earnings_date in earnings_to_analyze:
        print(f"\n{ticker} - {earnings_date}:")

        if analyze_and_store(container, ticker, earnings_date, db_path):
            success_count += 1
        else:
            fail_count += 1

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total: {len(earnings_to_analyze)}")
    print(f"  ✓ Stored: {success_count}")
    print(f"  ✗ Failed: {fail_count}")
    print("=" * 70)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
