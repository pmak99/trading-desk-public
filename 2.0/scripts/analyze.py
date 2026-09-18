#!/usr/bin/env python3
"""
Analyze a single ticker for IV Crush opportunity.

Usage:
    python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01
"""

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# Run by path (trade.sh): 2.0/ for the `scripts.*` namespace.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.scan.workflows.vix import _log_vix_context
from src.application.services.strategy.sizing import max_loss_warning
from src.config.config import Config
from src.container import Container
from src.domain.enums import EarningsTiming
from src.domain.types import Money
from src.domain.vrp_tier import vrp_tier
from src.infrastructure.database.migrations import apply_migrations
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


def _vrp_ratio_line(label: str, vrp) -> str:
    """The VRP ratio and its tier, with the quarters behind it when known."""
    quarters = f" · {vrp.historical_quarters} quarters" if vrp.historical_quarters is not None else ""
    return f"VRP Ratio {label}: {vrp.vrp_ratio:.2f}x  [{vrp_tier(vrp.vrp_ratio).name}{quarters}]"


def _log_vrp_intraday_reference(logger_fn, intraday_ratio: float, intraday_mean_pct: float) -> None:
    """The intraday VRP as a reference with no tier: tiers are close-to-close since the
    2026-09 migration, and an intraday ratio graded on them compares units
    (scripts/ab_evaluate_live.py)."""
    logger_fn(f"  VRP Ratio (intraday, reference only): {intraday_ratio:.2f}x")
    logger_fn(f"  Historical Mean (intraday): {intraday_mean_pct:.1f}%")


LIQUIDITY_ICON = {"EXCELLENT": "✅", "WARNING": "⚠️ ", "REJECT": "❌"}

# CLAUDE.md §2, printed where a REJECT pick would have printed entry steps.
REJECT_NEXT_STEPS = (
    "   1. Check the ATM chain at your broker",
    "   2. ATM single bid ≥ $1.50 and OI ≥ 200 → trade it as a single at half size",
    "   3. Otherwise skip this event",
)


def _signed_credit(amount: Money) -> str:
    """A credit as $x.xx, a debit (negative credit) as 'debit $x.xx'."""
    if amount.amount < 0:
        return f"debit ${-amount.amount:.2f}"
    return str(amount)


def _credit_line(strategy) -> str:
    """The mid credit, with the natural one (sold at bid, bought at ask) when quoted."""
    line = f"{_signed_credit(strategy.net_credit)} mid"
    if strategy.natural_credit is not None:
        line += f" · {_signed_credit(strategy.natural_credit)} natural (sell at bid, buy at ask)"
    return line


def _max_loss_line(max_loss: Money, natural_max_loss) -> str:
    """Max loss at mid, with the natural price's beside it when quoted."""
    if natural_max_loss is None:
        return str(max_loss)
    return f"{max_loss} · {natural_max_loss} at natural"


def _log_calendar_pilot(logger_fn, cal, max_loss_warn_threshold: float) -> None:
    """The calendar pilot block: structure, risk, and both legs' liquidity."""
    logger_fn("\n" + "=" * 80)
    logger_fn("🗓️  CALENDAR SPREAD PILOT (tail-risk alternative)")
    logger_fn("=" * 80)
    logger_fn(f"   Structure: {cal.strike_description}")
    natural = (
        f" (${abs(float(cal.natural_credit.amount)):.2f} natural)"
        if cal.natural_credit is not None else ""
    )
    logger_fn(
        f"   Net Debit: ${abs(float(cal.net_credit.amount)):.2f}/spread{natural} "
        f"× {cal.contracts} contracts"
    )
    logger_fn(f"   Max Loss (defined): {_max_loss_line(cal.max_loss, cal.natural_max_loss)}")
    cal_warn = max_loss_warning(
        float(cal.worst_case_max_loss.amount), max_loss_warn_threshold, cal.contracts
    )
    if cal_warn:
        logger_fn(f"   \u26a0\ufe0f  {cal_warn}")
    # The tier is the worse leg's; WARNING is why a count reads 5, not 10.
    logger_fn(
        f"   Liquidity: {cal.liquidity_tier} · min OI {cal.min_open_interest} · "
        f"widest spread {cal.max_spread_pct:.1f}% (both legs)"
    )
    logger_fn(f"   Est. Profit (50% managed exit): {cal.max_profit}")
    logger_fn(f"   {cal.rationale}")


def parse_date(date_str: str) -> date:
    """Parse date string in ISO format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date format: {date_str}. Use YYYY-MM-DD") from None


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze IV Crush opportunity for a ticker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze AAPL for upcoming earnings
    python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01

    # Use next Friday expiration
    python scripts/analyze.py TSLA --earnings-date 2025-02-15 --expiration 2025-02-21

Notes:
    - Requires TRADIER_API_KEY in .env
    - Historical data must be backfilled first for VRP calculation
        """,
    )

    parser.add_argument("ticker", type=str, help="Stock ticker symbol")
    parser.add_argument(
        "--earnings-date",
        type=parse_date,
        required=True,
        help="Earnings date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--expiration",
        type=parse_date,
        required=True,
        help="Option expiration date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--strategies",
        action="store_true",
        help="Generate trade strategies (bull put and bear call spreads, both built and scored)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    logger.info("=" * 80)
    logger.info("IV Crush 2.0 - Ticker Analysis")
    logger.info("=" * 80)
    logger.info(f"Ticker: {args.ticker}")
    logger.info(f"Earnings Date: {args.earnings_date}")
    logger.info(f"Expiration: {args.expiration}")
    logger.info("=" * 80)

    try:
        # Load configuration; migrate once, before any database work
        config = Config.from_env()
        apply_migrations()

        # Create container
        container = Container(config)

        # Use the analyzer service
        analyzer = container.analyzer

        # The implied move is priced at the first expiration containing the
        # reaction, which depends on BMO/AMC. The CLI carries no timing, so read
        # the stored tag; absent is UNKNOWN (priced at the next day's expiration).
        timing_result = container.earnings_repository.get_timing(
            args.ticker, args.earnings_date
        )
        earnings_timing = (
            timing_result.value if timing_result.is_ok else EarningsTiming.UNKNOWN
        )
        if timing_result.is_err:
            logger.info(f"Earnings timing unknown ({timing_result.error})")
        logger.info(f"Earnings Timing: {earnings_timing.value}")

        # Run complete analysis
        logger.info("\n📊 Running Complete Analysis...")
        result = analyzer.analyze(
            ticker=args.ticker,
            earnings_date=args.earnings_date,
            expiration=args.expiration,
            earnings_timing=earnings_timing,
            generate_strategies=args.strategies,
        )

        if result.is_err:
            logger.error(f"Analysis failed: {result.error}")
            if "No historical data" in str(result.error):
                logger.info("\nℹ️  VRP calculation requires historical data")
                logger.info("   Run: python scripts/backfill_historical.py " + args.ticker + " --execute")
            return 1

        analysis = result.value

        # Display results
        logger.info("\n" + "=" * 80)
        logger.info("ANALYSIS RESULTS")
        logger.info("=" * 80)

        # VIX regime context (cached from the analysis_log fetch). The shared
        # helper, not a copy: this block duplicated it with `except: pass`.
        logger.info("")
        _log_vix_context(container)

        # Implied Move
        logger.info("\n📊 Implied Move:")
        logger.info(f"  Stock Price: {analysis.implied_move.stock_price}")
        logger.info(f"  Implied Move: {analysis.implied_move.implied_move_pct}  "
                    f"(expiration {analysis.implied_move.expiration}; trades {analysis.expiration})")
        logger.info(f"  Upper Bound: {analysis.implied_move.upper_bound}")
        logger.info(f"  Lower Bound: {analysis.implied_move.lower_bound}")
        if analysis.implied_move.avg_iv:
            logger.info(f"  Average IV: {analysis.implied_move.avg_iv}")

        # VRP Analysis
        logger.info("\n📊 VRP Analysis:")
        logger.info(f"  {_vrp_ratio_line('(close-to-close)', analysis.vrp)}")
        logger.info(f"  Historical Mean (close-to-close): {analysis.vrp.historical_mean_move_pct}")
        if analysis.vrp_intraday_ratio is not None and analysis.historical_intraday_mean_pct is not None:
            _log_vrp_intraday_reference(
                logger.info, analysis.vrp_intraday_ratio, analysis.historical_intraday_mean_pct
            )
        logger.info(f"  Edge Score: {analysis.vrp.edge_score:.2f}")
        logger.info(f"  Recommendation: {analysis.vrp.recommendation.value.upper()}")

        # A quarter missing from historical_moves entirely -- the VRP/TRR above
        # are computed on a stale window (CLAUDE.md quarter-gap data caveat).
        if analysis.stale_history_warning:
            logger.info(f"\n⚠️  STALE HISTORY: {analysis.stale_history_warning}")

        # Strategy Recommendations
        if args.strategies and analysis.strategies:
            logger.info("\n" + "=" * 80)
            logger.info("STRATEGY RECOMMENDATIONS")
            logger.info("=" * 80)

            strats = analysis.strategies
            logger.info(f"\nDirectional Bias: {strats.directional_bias.value.replace('_', ' ').upper()}")
            logger.info(f"Generated {len(strats.strategies)} strategies")

            for i, strategy in enumerate(strats.strategies, 1):
                is_recommended = (i - 1 == strats.recommended_index)

                # Add visual separator between strategies
                logger.info("\n" + "─" * 80)

                # Strategy header with prominent number
                if is_recommended:
                    logger.info(f"★ STRATEGY #{i} (RECOMMENDED): {strategy.strategy_type.value.replace('_', ' ').upper()}")
                else:
                    logger.info(f"  STRATEGY #{i}: {strategy.strategy_type.value.replace('_', ' ').upper()}")

                logger.info("─" * 80)

                # Trade Details section
                logger.info("\n  📋 TRADE DETAILS:")
                logger.info(f"     Strikes: {strategy.strike_description}")
                logger.info(f"     Net Credit: {_credit_line(strategy)}")
                if strategy.liquidity_rejected:
                    # CLAUDE.md §2: not traded as a spread, so no size and no
                    # position totals -- one spread's figures, for reference.
                    logger.info("     Contracts: not sized — liquidity REJECT, not tradeable "
                                "as a spread (CLAUDE.md §2)")
                    per = strategy.contracts
                    logger.info("\n  💰 PROFIT/LOSS (one spread):")
                    logger.info(f"     Max Profit: {strategy.max_profit / per}")
                    logger.info("     Max Loss: " + _max_loss_line(
                        strategy.max_loss / per,
                        strategy.natural_max_loss / per if strategy.natural_max_loss is not None else None,
                    ))
                else:
                    logger.info(f"     Contracts: {strategy.contracts}")

                    # Profit/Loss section
                    logger.info("\n  💰 PROFIT/LOSS:")
                    logger.info(f"     Max Profit: {strategy.max_profit}")
                    logger.info(f"     Max Loss: {_max_loss_line(strategy.max_loss, strategy.natural_max_loss)}")
                    _risk_warn = max_loss_warning(
                        float(strategy.worst_case_max_loss.amount),
                        config.strategy.max_loss_warn_threshold,
                        strategy.contracts,
                    )
                    if _risk_warn:
                        logger.info(f"     \u26a0\ufe0f  {_risk_warn}")
                logger.info(f"     Reward/Risk: {strategy.reward_risk_ratio:.2f}")
                logger.info(f"     Breakeven: {', '.join(str(be) for be in strategy.breakeven)}")

                # Probability & Scoring section
                logger.info("\n  📊 PROBABILITY & SCORE:")
                logger.info(f"     Win Probability: {strategy.probability_of_profit:.1%}")
                logger.info(f"     Overall Score: {strategy.overall_score:.1f}/100")

                # Display liquidity tier if available
                if strategy.liquidity_tier:
                    tier_icon = LIQUIDITY_ICON.get(strategy.liquidity_tier, "ℹ️ ")
                    logger.info(f"     Liquidity: {tier_icon} {strategy.liquidity_tier}")

                # Display Greeks if available
                if strategy.position_delta is not None:
                    logger.info("\n  📈 GREEKS:")
                    logger.info(f"     Delta: {strategy.position_delta:+.2f} (directional exposure)")
                    if strategy.position_gamma is not None:
                        logger.info(f"     Gamma: {strategy.position_gamma:+.4f} (delta sensitivity)")
                    if strategy.position_theta is not None:
                        logger.info(f"     Theta: {strategy.position_theta:+.2f} (daily time decay)")
                    if strategy.position_vega is not None:
                        logger.info(f"     Vega: {strategy.position_vega:+.2f} (IV sensitivity)")

                logger.info(f"\n  💡 Rationale: {strategy.rationale}")

            # Final recommendation summary
            logger.info("\n" + "=" * 80)
            logger.info(f"💡 RECOMMENDATION: {strats.recommendation_rationale}")

        elif args.strategies and not analysis.strategies:
            logger.info("\n⚠️  Strategy generation was requested but not available")
            logger.info("   (VRP may not meet minimum threshold)")

        # Final Summary
        logger.info("\n" + "=" * 80)
        logger.info("SINGLE TICKER ANALYSIS - SUMMARY")
        logger.info("=" * 80)
        logger.info("\n📊 Analysis Results:")
        logger.info(f"   Ticker: {args.ticker}")
        logger.info(f"   Earnings: {args.earnings_date}")
        # Show actual expiration used (may differ from requested if adjusted)
        if analysis.expiration != args.expiration:
            logger.info(f"   Expiration: {analysis.expiration} (adjusted from {args.expiration})")
        else:
            logger.info(f"   Expiration: {args.expiration}")
        logger.info(f"   Stock Price: {analysis.implied_move.stock_price}")
        logger.info("\n📈 VRP Metrics:")
        logger.info(f"   Implied Move: {analysis.vrp.implied_move_pct}")
        logger.info(f"   Historical Avg (close-to-close): {analysis.vrp.historical_mean_move_pct}")
        logger.info(f"   {_vrp_ratio_line('(close-to-close)', analysis.vrp)}")
        if analysis.vrp_intraday_ratio is not None and analysis.historical_intraday_mean_pct is not None:
            _log_vrp_intraday_reference(
                lambda msg: logger.info(f"   {msg.lstrip()}"),
                analysis.vrp_intraday_ratio, analysis.historical_intraday_mean_pct,
            )
        logger.info(f"   Edge Score: {analysis.vrp.edge_score:.2f}")
        logger.info(f"   Recommendation: {analysis.vrp.recommendation.value.upper()}")
        # Display directional bias from skew analysis if available
        if analysis.skew:
            # Format: "STRONG BEARISH" instead of "strong_bearish"
            bias_formatted = analysis.skew.directional_bias.value.replace('_', ' ').upper()
            logger.info(f"   Directional Bias: {bias_formatted}")
        elif analysis.strategies:
            bias_formatted = analysis.strategies.directional_bias.value.replace('_', ' ').upper()
            logger.info(f"   Directional Bias: {bias_formatted}")

        # IV term structure (Jun 2026): how much of the front IV is event-specific
        if analysis.term_structure and analysis.term_structure.slope_ratio is not None:
            from src.application.metrics.term_structure import classify_slope_ratio
            ts = analysis.term_structure
            label = classify_slope_ratio(ts.slope_ratio).replace('_', ' ')
            icon = "✅" if ts.slope_ratio >= 1.10 else "⚠️ "
            logger.info(f"\n📐 Term Structure: {icon} {label}")
            logger.info(
                f"   Front ATM IV {float(ts.ivs[0].value):.1f} ({ts.expirations[0]}) vs "
                f"back {float(ts.ivs[1].value):.1f} ({ts.expirations[1]}) — "
                f"event-vol multiple {ts.slope_ratio:.2f}x"
            )
            if ts.slope_ratio < 1.10:
                logger.info(
                    "   ⚠️  Front IV is NOT event-concentrated — a high VRP here is "
                    "persistent risk the post-earnings crush will not remove."
                )

        # Calendar spread pilot (Jun 2026): defined-risk alternative when
        # compound tail risk would otherwise force a skip
        if analysis.calendar_candidate:
            _log_calendar_pilot(
                logger.info, analysis.calendar_candidate,
                config.strategy.max_loss_warn_threshold,
            )

        # "One position per ticker per earnings event" (CLAUDE.md) — a prior
        # journaled position on this exact event already exists. Loud and
        # ahead of the recommendation, matching a repair campaign on
        # record: the repair attempt (a second position), not the first
        # loss, made it catastrophic. The warning text carries the rule and the
        # check's limit (closed, journaled trades only): the same line scan,
        # whisper and the bot show.
        if analysis.existing_position_warning:
            logger.info("\n" + "=" * 80)
            logger.info("🚨 EXISTING POSITION ALREADY ON THIS EARNINGS EVENT")
            logger.info("=" * 80)
            logger.info(f"   {analysis.existing_position_warning}")

        if analysis.vrp.is_tradeable:
            logger.info("\n" + "=" * 80)
            logger.info("✅ RESULT: TRADEABLE OPPORTUNITY")
            logger.info("=" * 80)

            if args.strategies and analysis.strategies and (
                analysis.strategies.recommended_strategy.liquidity_rejected
            ):
                # The generator ranks REJECT last, so a REJECT pick means every
                # offered spread is REJECT: no spread to enter (CLAUDE.md §2).
                rec = analysis.strategies.recommended_strategy
                logger.info(
                    f"\n💡 Best-scored spread: {rec.strategy_type.value.replace('_', ' ').title()} "
                    f"— ❌ liquidity REJECT, not tradeable as a spread"
                )
                logger.info(f"   Strikes: {rec.strike_description}")
                logger.info(f"   Net Credit: {_credit_line(rec)}")
                logger.info("\n📝 Next Steps (CLAUDE.md §2 — every offered spread is liquidity REJECT):")
                for step in REJECT_NEXT_STEPS:
                    logger.info(step)
            elif args.strategies and analysis.strategies:
                rec = analysis.strategies.recommended_strategy
                logger.info(f"\n💡 Recommended Strategy: {rec.strategy_type.value.replace('_', ' ').title()}")
                logger.info(f"   Strikes: {rec.strike_description}")
                logger.info(f"   Net Credit: {_credit_line(rec)}")
                logger.info(f"   Max Profit: {rec.max_profit} ({rec.contracts} contracts)")
                logger.info(f"   Max Loss: {_max_loss_line(rec.max_loss, rec.natural_max_loss)}")
                _rec_warn = max_loss_warning(
                    float(rec.worst_case_max_loss.amount),
                    config.strategy.max_loss_warn_threshold,
                    rec.contracts,
                )
                if _rec_warn:
                    logger.info(f"   \u26a0\ufe0f  {_rec_warn}")
                    logger.info("      Contract caps do not bound dollar risk; "
                                "size on max loss, not contract count.")
                logger.info(f"   Reward/Risk: {rec.reward_risk_ratio:.2f}")
                logger.info(f"   Win Probability: {rec.probability_of_profit:.1%}")
                if rec.liquidity_tier:
                    tier_icon = LIQUIDITY_ICON.get(rec.liquidity_tier, "ℹ️ ")
                    logger.info(f"   Liquidity: {tier_icon} {rec.liquidity_tier}")
                logger.info("\n📝 Next Steps:")
                logger.info("   1. Review strategy details above")
                logger.info("   2. Check your broker for exact pricing")
                logger.info(f"   3. Enter position before earnings ({args.earnings_date})")
                logger.info("   4. Close position at market open after earnings")
            else:
                logger.info("\n📝 Next Steps:")
                logger.info("   1. No specific trade strategies could be generated")
                logger.info("   2. Consider manual strategy setup based on VRP edge detected")
        else:
            logger.info("\n" + "=" * 80)
            logger.info("⏭️  RESULT: SKIP - INSUFFICIENT EDGE")
            logger.info("=" * 80)
            logger.info("\n❌ Not Tradeable:")
            logger.info(f"   VRP ratio {analysis.vrp.vrp_ratio:.2f}x does not meet minimum threshold")
            logger.info(f"   Edge score {analysis.vrp.edge_score:.2f} is too low")
            logger.info("\n📝 Recommendation:")
            logger.info("   Skip this earnings - insufficient statistical edge")

        return 0

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
