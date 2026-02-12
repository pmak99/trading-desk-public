#!/usr/bin/env python3
"""
Analyze a single ticker for IV Crush opportunity.

Usage:
    python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01
"""

import sys
import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.container import Container
from src.config.config import Config

logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> date:
    """Parse date string in ISO format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


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
        help="Generate trade strategies (bull put spread, bear call spread, iron condor)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    logger.info("=" * 80)
    logger.info(f"IV Crush core - Ticker Analysis")
    logger.info("=" * 80)
    logger.info(f"Ticker: {args.ticker}")
    logger.info(f"Earnings Date: {args.earnings_date}")
    logger.info(f"Expiration: {args.expiration}")
    logger.info("=" * 80)

    try:
        # Load configuration
        config = Config.from_env()

        # Create container
        container = Container(config)

        # Use the analyzer service
        analyzer = container.analyzer

        # Run complete analysis
        logger.info("\n📊 Running Complete Analysis...")
        result = analyzer.analyze(
            ticker=args.ticker,
            earnings_date=args.earnings_date,
            expiration=args.expiration,
            generate_strategies=args.strategies,
        )

        if result.is_err:
            logger.error(f"Analysis failed: {result.error}")
            if "No historical data" in str(result.error):
                logger.info("\nℹ️  VRP calculation requires historical data")
                logger.info("   Run: python scripts/backfill.py " + args.ticker)
            return 1

        analysis = result.value

        # Display results
        logger.info("\n" + "=" * 80)
        logger.info("ANALYSIS RESULTS")
        logger.info("=" * 80)

        # Implied Move
        logger.info(f"\n📊 Implied Move:")
        logger.info(f"  Stock Price: {analysis.implied_move.stock_price}")
        logger.info(f"  Implied Move: {analysis.implied_move.implied_move_pct}")
        logger.info(f"  Upper Bound: {analysis.implied_move.upper_bound}")
        logger.info(f"  Lower Bound: {analysis.implied_move.lower_bound}")
        if analysis.implied_move.avg_iv:
            logger.info(f"  Average IV: {analysis.implied_move.avg_iv}")

        # VRP Analysis
        logger.info(f"\n📊 VRP Analysis:")
        logger.info(f"  VRP Ratio: {analysis.vrp.vrp_ratio:.2f}x")
        logger.info(f"  Historical Mean: {analysis.vrp.historical_mean_move_pct}")
        logger.info(f"  Edge Score: {analysis.vrp.edge_score:.2f}")
        logger.info(f"  Recommendation: {analysis.vrp.recommendation.value.upper()}")

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
                logger.info(f"\n  📋 TRADE DETAILS:")
                logger.info(f"     Strikes: {strategy.strike_description}")
                logger.info(f"     Net Credit: {strategy.net_credit}")
                logger.info(f"     Contracts: {strategy.contracts}")

                # Profit/Loss section
                logger.info(f"\n  💰 PROFIT/LOSS:")
                logger.info(f"     Max Profit: {strategy.max_profit}")
                logger.info(f"     Max Loss: {strategy.max_loss}")
                logger.info(f"     Reward/Risk: {strategy.reward_risk_ratio:.2f}")
                logger.info(f"     Breakeven: {', '.join(str(be) for be in strategy.breakeven)}")

                # Probability & Scoring section
                logger.info(f"\n  📊 PROBABILITY & SCORE:")
                logger.info(f"     Win Probability: {strategy.probability_of_profit:.1%}")
                logger.info(f"     Overall Score: {strategy.overall_score:.1f}/100")

                # Display liquidity tier if available
                if strategy.liquidity_tier:
                    tier_icon = {
                        "EXCELLENT": "✅",
                        "WARNING": "⚠️ ",
                        "REJECT": "❌"
                    }.get(strategy.liquidity_tier, "ℹ️ ")
                    logger.info(f"     Liquidity: {tier_icon} {strategy.liquidity_tier}")

                # Display Greeks if available
                if strategy.position_delta is not None:
                    logger.info(f"\n  📈 GREEKS:")
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
        logger.info(f"\n📊 Analysis Results:")
        logger.info(f"   Ticker: {args.ticker}")
        logger.info(f"   Earnings: {args.earnings_date}")
        # Show actual expiration used (may differ from requested if adjusted)
        if analysis.expiration != args.expiration:
            logger.info(f"   Expiration: {analysis.expiration} (adjusted from {args.expiration})")
        else:
            logger.info(f"   Expiration: {args.expiration}")
        logger.info(f"   Stock Price: {analysis.implied_move.stock_price}")
        logger.info(f"\n📈 VRP Metrics:")
        logger.info(f"   Implied Move: {analysis.vrp.implied_move_pct}")
        logger.info(f"   Historical Avg: {analysis.vrp.historical_mean_move_pct}")
        logger.info(f"   VRP Ratio: {analysis.vrp.vrp_ratio:.2f}x")
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

        if analysis.vrp.is_tradeable:
            logger.info("\n" + "=" * 80)
            logger.info("✅ RESULT: TRADEABLE OPPORTUNITY")
            logger.info("=" * 80)

            if args.strategies and analysis.strategies:
                rec = analysis.strategies.recommended_strategy
                logger.info(f"\n💡 Recommended Strategy: {rec.strategy_type.value.replace('_', ' ').title()}")
                logger.info(f"   Strikes: {rec.strike_description}")
                logger.info(f"   Net Credit: {rec.net_credit}")
                logger.info(f"   Max Profit: {rec.max_profit} ({rec.contracts} contracts)")
                logger.info(f"   Max Loss: {rec.max_loss}")
                logger.info(f"   Reward/Risk: {rec.reward_risk_ratio:.2f}")
                logger.info(f"   Win Probability: {rec.probability_of_profit:.1%}")
                if rec.liquidity_tier:
                    tier_icon = {
                        "EXCELLENT": "✅",
                        "WARNING": "⚠️ ",
                        "REJECT": "❌"
                    }.get(rec.liquidity_tier, "ℹ️ ")
                    logger.info(f"   Liquidity: {tier_icon} {rec.liquidity_tier}")
                logger.info(f"\n📝 Next Steps:")
                logger.info(f"   1. Review strategy details above")
                logger.info(f"   2. Check your broker for exact pricing")
                logger.info(f"   3. Enter position before earnings ({args.earnings_date})")
                logger.info(f"   4. Close position at market open after earnings")
            else:
                logger.info(f"\n📝 Next Steps:")
                logger.info(f"   1. No specific trade strategies could be generated")
                logger.info(f"   2. Consider manual strategy setup based on VRP edge detected")
        else:
            logger.info("\n" + "=" * 80)
            logger.info("⏭️  RESULT: SKIP - INSUFFICIENT EDGE")
            logger.info("=" * 80)
            logger.info(f"\n❌ Not Tradeable:")
            logger.info(f"   VRP ratio {analysis.vrp.vrp_ratio:.2f}x does not meet minimum threshold")
            logger.info(f"   Edge score {analysis.vrp.edge_score:.2f} is too low")
            logger.info(f"\n📝 Recommendation:")
            logger.info(f"   Skip this earnings - insufficient statistical edge")

        return 0

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
