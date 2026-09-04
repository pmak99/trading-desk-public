"""Single-ticker earnings analysis — analyze_ticker and analyze_ticker_concurrent."""
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from src.container import Container
from src.domain.enums import EarningsTiming
from src.domain.types import TickerAnalysis
from src.application.metrics.vrp import compute_close_baseline_vrp
from src.application.filters.weekly_options import has_weekly_options

from ..constants import (
    BACKFILL_TIMEOUT_SECONDS,
    BACKFILL_YEARS,
)
from ..date_utils import (
    calculate_implied_move_expiration,
    validate_expiration_date,
)
from ..market_data import (
    get_ticker_name,
    check_liquidity_hybrid,
)

logger = logging.getLogger(__name__)

def _compute_term_slope_ratio(container, ticker, front_expiration):
    """Front/back ATM IV ratio for analysis_log, or None.

    The scan/whisper path logs analysis_log separately from
    TickerAnalyzer.analyze(), and used to omit term_slope_ratio entirely — so
    every batch-written row had it NULL while single-ticker /analyze runs
    populated it. That silently biased the term-structure A/B: the Sep 1 2026
    evaluation found 39 events with outcomes and ZERO below slope 1.10, because
    the only rows carrying the column came from /analyze runs the user tends to
    fire at names that already look like good candidates. No FLAT observations
    meant the hypothesis under test could not be evaluated at all.

    Uses the same shared metrics as the analyzer so the two paths cannot
    diverge. Best-effort: any failure returns None rather than disturbing the
    scan. Chains and expirations come from cached_options_provider (60s / 1h
    TTL), so a back-chain fetch is one token per ticker, not per call site.
    """
    try:
        from src.application.metrics.term_structure import (
            select_back_expiration,
            analyze_term_structure,
        )

        provider = container.cached_options_provider

        exp_result = provider.get_expirations(ticker)
        if exp_result.is_err:
            return None
        back_expiration = select_back_expiration(exp_result.value, front_expiration)
        if back_expiration is None:
            return None

        front_result = provider.get_option_chain(ticker, front_expiration)
        if front_result.is_err:
            return None
        back_result = provider.get_option_chain(ticker, back_expiration)
        if back_result.is_err:
            return None

        ts_result = analyze_term_structure(front_result.value, back_result.value)
        if ts_result.is_err:
            return None
        return ts_result.value.slope_ratio
    except Exception:
        return None


def analyze_ticker(
    container: Container,
    ticker: str,
    earnings_date: date,
    expiration_date: date,
    auto_backfill: bool = False,
    skip_weekly_filter: bool = False,
    earnings_timing: EarningsTiming = EarningsTiming.UNKNOWN,
) -> Optional[dict]:
    """
    Analyze a single ticker for IV Crush opportunity.

    Args:
        container: Dependency injection container
        ticker: Stock ticker symbol
        earnings_date: Date of earnings announcement
        expiration_date: Options expiration date
        auto_backfill: If True, automatically backfill missing historical data
        skip_weekly_filter: If True, skip weekly options filter (override REQUIRE_WEEKLY_OPTIONS)

    Returns dict with analysis results or None if analysis failed.
    """
    try:
        logger.info(f"\n{'=' * 80}")
        logger.info(f"Analyzing {ticker}")
        logger.info(f"{'=' * 80}")
        logger.info(f"Earnings Date: {earnings_date}")
        logger.info(f"Expiration: {expiration_date}")

        # Fetch company name early (for result dictionaries)
        company_name = get_ticker_name(ticker)

        # Check for weekly options (opt-in filter via REQUIRE_WEEKLY_OPTIONS)
        has_weeklies = True  # Default: permissive
        weekly_reason = ""
        if container.config.thresholds.require_weekly_options and not skip_weekly_filter:
            # Fetch expirations to check for weekly options (cached — listings change rarely)
            expirations_result = container.cached_options_provider.get_expirations(ticker)
            if expirations_result.is_ok:
                expirations_list = [exp.isoformat() for exp in expirations_result.value]
                has_weeklies, weekly_reason = has_weekly_options(
                    expirations_list,
                    earnings_date.isoformat()
                )
                if not has_weeklies:
                    logger.info(f"\u2717 {ticker}: No weekly options - {weekly_reason}")
                    return None
            else:
                # On API error, be permissive - don't block trading opportunities
                logger.debug(f"{ticker}: Could not check weekly options, skipping filter")

        # Validate expiration date
        validation_error = validate_expiration_date(expiration_date, earnings_date, ticker)
        if validation_error:
            logger.error(f"\u2717 Invalid expiration date: {validation_error}")
            return None

        # Calculate the implied move expiration (first post-earnings day)
        # This is different from trading expiration to capture pure IV crush
        implied_move_exp = calculate_implied_move_expiration(earnings_date)

        # Find nearest available expiration for implied move calculation (cached)
        nearest_im_exp_result = container.cached_options_provider.find_nearest_expiration(ticker, implied_move_exp)
        if nearest_im_exp_result.is_err:
            logger.warning(f"\u2717 Failed to find implied move expiration for {ticker}: {nearest_im_exp_result.error}")
            return None

        actual_im_expiration = nearest_im_exp_result.value
        if actual_im_expiration != implied_move_exp:
            logger.info(f"  Implied move expiration: {implied_move_exp} \u2192 {actual_im_expiration}")

        # Find nearest available expiration for trading/liquidity (cached)
        nearest_exp_result = container.cached_options_provider.find_nearest_expiration(ticker, expiration_date)
        if nearest_exp_result.is_err:
            logger.warning(f"\u2717 Failed to find trading expiration for {ticker}: {nearest_exp_result.error}")
            return None

        actual_expiration = nearest_exp_result.value
        if actual_expiration != expiration_date:
            logger.info(f"  Trading expiration: {expiration_date} \u2192 {actual_expiration}")
            expiration_date = actual_expiration

        # Get calculators
        implied_move_calc = container.implied_move_calculator
        vrp_calc = container.vrp_calculator
        prices_repo = container.prices_repository

        # Step 1: Calculate implied move using first post-earnings expiration
        logger.info("\n\U0001f4ca Calculating Implied Move...")
        implied_result = implied_move_calc.calculate(ticker, actual_im_expiration)

        if implied_result.is_err:
            logger.warning(f"\u2717 Failed to calculate implied move: {implied_result.error}")
            return None

        implied_move = implied_result.value
        logger.info(f"\u2713 Implied Move: {implied_move.implied_move_pct}")
        logger.info(f"  Stock Price: {implied_move.stock_price}")
        logger.info(f"  ATM Strike: {implied_move.atm_strike}")
        logger.info(f"  Straddle Cost: {implied_move.straddle_cost}")

        # Step 2: Get historical moves
        logger.info("\n\U0001f4ca Fetching Historical Moves...")
        hist_result = prices_repo.get_historical_moves(ticker, limit=12)

        if hist_result.is_err:
            logger.warning(f"\u2717 No historical data: {hist_result.error}")

            # Auto-backfill if enabled (for ticker mode/list mode)
            if auto_backfill:
                logger.info(f"\U0001f4ca Auto-backfilling historical earnings data for {ticker}...")

                # Calculate start date (3 years ago)
                start_date = (date.today() - timedelta(days=BACKFILL_YEARS*365)).isoformat()
                end_date = (date.today() - timedelta(days=1)).isoformat()

                try:
                    # Call backfill script
                    result = subprocess.run(
                        [
                            sys.executable,
                            "scripts/backfill_historical.py",
                            ticker,
                            "--start-date", start_date,
                            "--end-date", end_date
                        ],
                        cwd=Path(__file__).parent.parent.parent.parent,
                        capture_output=True,
                        text=True,
                        timeout=BACKFILL_TIMEOUT_SECONDS
                    )

                    if result.returncode == 0:
                        logger.info(f"\u2713 Backfill complete for {ticker}")

                        # Retry fetching historical moves
                        logger.info("\U0001f4ca Retrying historical data fetch...")
                        hist_result = prices_repo.get_historical_moves(ticker, limit=12)

                        if hist_result.is_err:
                            logger.warning(f"\u2717 Still no historical data after backfill: {hist_result.error}")
                            return {
                                'ticker': ticker,
                                'ticker_name': company_name,
                                'earnings_date': str(earnings_date),
                                'expiration_date': str(expiration_date),
                                'implied_move_pct': str(implied_move.implied_move_pct),
                                'stock_price': float(implied_move.stock_price.amount),
                                'status': 'NO_HISTORICAL_DATA',
                                'tradeable': False
                            }
                    else:
                        logger.warning(f"\u2717 Backfill failed for {ticker}: {result.stderr}")
                        return {
                            'ticker': ticker,
                            'ticker_name': company_name,
                            'earnings_date': str(earnings_date),
                            'expiration_date': str(expiration_date),
                            'implied_move_pct': str(implied_move.implied_move_pct),
                            'stock_price': float(implied_move.stock_price.amount),
                            'status': 'BACKFILL_FAILED',
                            'tradeable': False
                        }

                except subprocess.TimeoutExpired:
                    logger.warning(f"\u2717 Backfill timeout for {ticker}")
                    return {
                        'ticker': ticker,
                        'ticker_name': company_name,
                        'earnings_date': str(earnings_date),
                        'expiration_date': str(expiration_date),
                        'implied_move_pct': str(implied_move.implied_move_pct),
                        'stock_price': float(implied_move.stock_price.amount),
                        'status': 'BACKFILL_TIMEOUT',
                        'tradeable': False
                    }
                except Exception as e:
                    logger.warning(f"\u2717 Backfill error for {ticker}: {e}")
                    return {
                        'ticker': ticker,
                        'ticker_name': company_name,
                        'earnings_date': str(earnings_date),
                        'expiration_date': str(expiration_date),
                        'implied_move_pct': str(implied_move.implied_move_pct),
                        'stock_price': float(implied_move.stock_price.amount),
                        'status': 'BACKFILL_ERROR',
                        'tradeable': False
                    }
            else:
                # No auto-backfill - suggest manual backfill
                logger.info("   Run: python scripts/backfill_historical.py " + ticker)
                return {
                    'ticker': ticker,
                    'ticker_name': company_name,
                    'earnings_date': str(earnings_date),
                    'expiration_date': str(expiration_date),
                    'implied_move_pct': str(implied_move.implied_move_pct),
                    'stock_price': float(implied_move.stock_price.amount),
                    'status': 'NO_HISTORICAL_DATA',
                    'tradeable': False
                }

        historical_moves = hist_result.value
        logger.info(f"\u2713 Found {len(historical_moves)} historical moves")

        # Step 3: Calculate VRP
        logger.info("\n\U0001f4ca Calculating VRP...")
        vrp_result = vrp_calc.calculate(
            ticker=ticker,
            expiration=expiration_date,
            implied_move=implied_move,
            historical_moves=historical_moves,
        )

        if vrp_result.is_err:
            logger.warning(f"\u2717 Failed to calculate VRP: {vrp_result.error}")
            return None

        vrp = vrp_result.value

        logger.info(f"\u2713 VRP Ratio: {vrp.vrp_ratio:.2f}x")
        logger.info(f"  Implied Move: {vrp.implied_move_pct}")
        logger.info(f"  Historical Mean: {vrp.historical_mean_move_pct}")
        logger.info(f"  Edge Score: {vrp.edge_score:.2f}")
        logger.info(f"  Recommendation: {vrp.recommendation.value.upper()}")

        # CRITICAL: Check liquidity tier using HYBRID approach (C-then-B with dynamic thresholds)
        implied_move_pct = float(str(implied_move.implied_move_pct).rstrip('%'))
        has_liquidity, liquidity_tier, hybrid_details = check_liquidity_hybrid(
            ticker=ticker,
            expiration=expiration_date,
            implied_move_pct=implied_move_pct,
            container=container,
            max_loss_budget=20000.0,
            use_dynamic_thresholds=True,
        )

        # Log hybrid liquidity details
        if hybrid_details and hybrid_details.get('method') not in ('NO_CHAIN', 'ERROR', 'FAILED'):
            thresholds = hybrid_details.get('thresholds', {})
            oi_ratio = hybrid_details.get('oi_ratio')
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            price_tier = thresholds.get('price_tier', 'N/A')
            spread_width = thresholds.get('spread_width', 'N/A')
            contracts = thresholds.get('contracts', 'N/A')
            max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
            logger.info(f"  Liquidity Tier: {liquidity_tier} (Hybrid {hybrid_details['method']})")
            logger.info(f"    Call ${hybrid_details['call_strike']:.0f} OI={hybrid_details['call_oi']:,}, "
                       f"Put ${hybrid_details['put_strike']:.0f} OI={hybrid_details['put_oi']:,}")
            logger.info(f"    Position: {contracts} contracts \u00d7 ${spread_width} spread ({price_tier} tier)")
            # Show tier breakdown
            oi_icon = {'EXCELLENT': '\u2713', 'GOOD': '\u2713', 'WARNING': '\u26a0\ufe0f', 'REJECT': '\u274c'}.get(oi_tier, '?')
            spread_icon = {'EXCELLENT': '\u2713', 'GOOD': '\u2713', 'WARNING': '\u26a0\ufe0f', 'REJECT': '\u274c'}.get(spread_tier, '?')
            logger.info(f"    OI: {oi_ratio:.1f}x \u2192 {oi_tier} {oi_icon} | Spread: {max_spread:.0f}% \u2192 {spread_tier} {spread_icon}")
        else:
            logger.info(f"  Liquidity Tier: {liquidity_tier}")

        # 4-Tier Warning Messages
        tier_clean = liquidity_tier.replace('*', '')
        if tier_clean == "GOOD":
            logger.info(f"\n\u2713 GOOD liquidity for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "GOOD":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.info(f"   OI/Position ratio {oi_ratio:.1f}x (2-5x) - adequate for full size")
            if spread_tier == "GOOD":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.info(f"   Bid/ask spread {max_spread:.0f}% (8-12%) - acceptable slippage")
        elif tier_clean == "WARNING":
            logger.warning(f"\n\u26a0\ufe0f  WARNING: Low liquidity detected for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "WARNING":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.warning(f"   OI/Position ratio {oi_ratio:.1f}x (1-2x) - consider reducing size")
            if spread_tier == "WARNING":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.warning(f"   Bid/ask spread {max_spread:.0f}% (>12%) - expect slippage")
        elif tier_clean == "REJECT":
            logger.warning(f"\n\u274c CRITICAL: Very low liquidity for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "REJECT":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.warning(f"   OI/Position ratio {oi_ratio:.1f}x (<1x) - DO NOT TRADE at full size")
            if spread_tier == "REJECT":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.warning(f"   Bid/ask spread {max_spread:.0f}% (>15%) - DO NOT TRADE")

        if vrp.is_tradeable:
            logger.info("\n\u2705 TRADEABLE OPPORTUNITY")
        else:
            logger.info("\n\u23ed\ufe0f  SKIP - Insufficient edge")

        # Get directional bias from skew analysis
        directional_bias = "NEUTRAL"  # Default if skew analysis unavailable
        skew_analyzer = container.skew_analyzer
        if skew_analyzer:
            skew_result = skew_analyzer.analyze_skew_curve(ticker, expiration_date)
            if skew_result.is_ok:
                # Format: "STRONG BEARISH" instead of "strong_bearish"
                directional_bias = skew_result.value.directional_bias.value.replace('_', ' ').upper()
                logger.info(f"  Directional Bias: {directional_bias}")

        # Build hybrid liquidity info for result
        oi_ratio = None
        if hybrid_details and hybrid_details.get('oi_ratio'):
            oi_ratio = hybrid_details['oi_ratio']

        # Persist to analysis_log — fire-and-forget (vix_level NULL for this path)
        try:
            _log_entry = TickerAnalysis(
                ticker=ticker,
                earnings_date=earnings_date,
                earnings_timing=earnings_timing,
                entry_time=datetime.now(),
                expiration=expiration_date,
                implied_move=implied_move,
                vrp=vrp,
                recommendation=vrp.recommendation,
            )
            _close_mean, _vrp_close = compute_close_baseline_vrp(
                float(implied_move.implied_move_pct.value), historical_moves
            )
            # Gated on is_tradeable to match TickerAnalyzer.analyze()'s own
            # guard, which also bounds the extra chain fetches to the handful
            # of tickers a scan actually surfaces rather than the full universe.
            _term_slope = (
                _compute_term_slope_ratio(container, ticker, expiration_date)
                if vrp.is_tradeable else None
            )
            container.analysis_repository.log_analysis(
                _log_entry,
                historical_close_mean_pct=_close_mean,
                vrp_close_ratio=_vrp_close,
                term_slope_ratio=_term_slope,
            )
        except Exception:
            pass

        return {
            'ticker': ticker,
            'ticker_name': company_name,
            'earnings_date': str(earnings_date),
            'expiration_date': str(expiration_date),
            'stock_price': float(implied_move.stock_price.amount),
            'implied_move_pct': str(vrp.implied_move_pct),
            'historical_mean_pct': str(vrp.historical_mean_move_pct),
            'vrp_ratio': float(vrp.vrp_ratio),
            'edge_score': float(vrp.edge_score),
            'recommendation': vrp.recommendation.value,
            'is_tradeable': vrp.is_tradeable,
            'liquidity_tier': liquidity_tier,  # CRITICAL ADDITION
            'liquidity_oi_ratio': oi_ratio,  # NEW: OI/Position ratio from hybrid check
            'directional_bias': directional_bias,  # NEW: Directional bias from skew
            'status': 'SUCCESS'
        }

    except Exception as e:
        logger.error(f"\u2717 Error analyzing {ticker}: {e}", exc_info=True)
        return None


def analyze_ticker_concurrent(
    container: Container,
    ticker: str,
    earnings_date: date,
    expiration_date: date,
    skip_weekly_filter: bool = False,
    earnings_timing: EarningsTiming = EarningsTiming.UNKNOWN,
) -> Optional[dict]:
    """
    Wrapper for analyze_ticker() compatible with ConcurrentScanner.

    Used by ConcurrentScanner.scan_ticker() as the analyze_func parameter.
    Disables auto-backfill for concurrent mode to avoid blocking.

    Args:
        container: DI container
        ticker: Stock ticker symbol
        earnings_date: Earnings announcement date
        expiration_date: Options expiration date
        skip_weekly_filter: If True, skip weekly options filter
        earnings_timing: BMO/AMC timing forwarded from ConcurrentScanner

    Returns:
        Analysis result dict or None
    """
    return analyze_ticker(
        container=container,
        ticker=ticker,
        earnings_date=earnings_date,
        expiration_date=expiration_date,
        auto_backfill=False,  # Disable backfill in concurrent mode
        skip_weekly_filter=skip_weekly_filter,
        earnings_timing=earnings_timing,
    )


