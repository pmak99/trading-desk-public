"""Ticker analyzer service for IV Crush analysis."""

import logging
from datetime import date, datetime
from typing import Optional

from src.domain.types import (
    TickerAnalysis, ImpliedMove, VRPResult, PositionLimitsSnapshot,
    SizingContext, snapshot_is_stale,
)
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.enums import DirectionalBias, EarningsTiming, Recommendation
from src.application.metrics.market_conditions import MarketConditions
from src.application.metrics.vrp import compute_close_baseline_vrp
from src.application.metrics.skew_enhanced import SkewAnalysis, fuse_skew_bias

import sys as _sys
from pathlib import Path as _Path
_root = str(_Path(__file__).resolve().parent.parent.parent.parent.parent)
if _root not in _sys.path:
    _sys.path.insert(0, _root)
from common.constants import ORATS_ENABLED, ORATS_SNAPSHOT_MAX_AGE_DAYS  # noqa: E402

logger = logging.getLogger(__name__)


class TickerAnalyzer:
    """Service for analyzing ticker IV Crush opportunities.

    Orchestrates the full analysis flow:
    1. Calculate implied move from options chain
    2. Fetch historical moves from database
    3. Calculate VRP ratio and recommendation

    Args:
        container: Dependency injection container
    """

    def __init__(self, container):
        self.container = container

    def analyze(
        self,
        ticker: str,
        earnings_date: date,
        expiration: date,
        generate_strategies: bool = False
    ) -> Result[TickerAnalysis, AppError]:
        """Analyze a ticker for IV Crush opportunity.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Date of earnings announcement
            expiration: Option expiration date (will be adjusted to nearest available)
            generate_strategies: If True, generate trade strategies (bull put, bear call, iron condor)

        Returns:
            Result containing TickerAnalysis or error
        """
        sizing_ctx = None
        try:
            # Step 0: Find nearest available expiration if exact date not available
            nearest_exp_result = self.container.tradier.find_nearest_expiration(
                ticker, expiration
            )
            if nearest_exp_result.is_err:
                return Err(nearest_exp_result.error)

            actual_expiration = nearest_exp_result.value
            if actual_expiration != expiration:
                logger.info(
                    f"{ticker}: Using adjusted expiration {actual_expiration} "
                    f"(requested {expiration})"
                )

            # Step 1: Calculate implied move
            implied_result = self.container.implied_move_calculator.calculate(
                ticker, actual_expiration
            )

            if implied_result.is_err:
                return Err(implied_result.error)

            implied_move = implied_result.value

            # Step 2: Get historical moves
            hist_result = self.container.prices_repository.get_historical_moves(
                ticker, limit=12
            )

            if hist_result.is_err:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No historical data for {ticker}",
                    )
                )

            historical_moves = hist_result.value

            min_quarters = self.container.config.thresholds.min_historical_quarters
            if len(historical_moves) < min_quarters:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"Insufficient historical data for {ticker} "
                        f"(need {min_quarters}+, got {len(historical_moves)})",
                    )
                )

            # Step 3: Calculate VRP
            vrp_result = self.container.vrp_calculator.calculate(
                ticker=ticker,
                expiration=actual_expiration,
                implied_move=implied_move,
                historical_moves=historical_moves,
            )

            if vrp_result.is_err:
                return Err(vrp_result.error)

            vrp = vrp_result.value

            # Step 3.5: Apply adaptive thresholds based on VIX regime
            vrp, market_conditions = self._apply_adaptive_thresholds(ticker, vrp)

            # Step 4: Optionally calculate enhanced skew (Phase 4)
            skew = None
            if self.container.skew_analyzer:
                skew_result = self.container.skew_analyzer.analyze_skew_curve(
                    ticker, actual_expiration
                )
                if skew_result.is_ok:
                    skew = skew_result.value
                    logger.debug(f"{ticker}: Skew analysis complete")
                else:
                    logger.warning(f"{ticker}: Skew analysis failed: {skew_result.error}")

            # Step 5: Optionally calculate enhanced consistency (Phase 4)
            consistency = None
            if self.container.consistency_analyzer and len(historical_moves) >= 4:
                consistency_result = self.container.consistency_analyzer.analyze_consistency(
                    ticker, historical_moves
                )
                if consistency_result.is_ok:
                    consistency = consistency_result.value
                    logger.debug(f"{ticker}: Consistency analysis complete")
                else:
                    logger.warning(f"{ticker}: Consistency analysis failed: {consistency_result.error}")

            # Step 5.5: Load ORATS snapshot + tail risk for tradeable tickers
            tail_risk_level = None
            snapshot = PositionLimitsSnapshot()
            if vrp.is_tradeable:
                tail_risk_level = self._compute_tail_risk_level(historical_moves)
                if ORATS_ENABLED:
                    snapshot = self._load_position_limits(ticker)

            # Step 5.6: Fuse skew with ORATS rSlp30 when available; when r_slp_30
            # is None (ORATS retired Jun 2026, or ticker missing from snapshot),
            # _fuse_skew_signals substitutes the Tradier slope_atm proxy at
            # reduced confidence — the post-ORATS continuity path.
            if skew is not None and (
                snapshot.r_slp_30 is not None or skew.slope_atm is not None
            ):
                fused_bias = self._fuse_skew_signals(skew, snapshot.r_slp_30)
                slope_desc = (
                    f"rSlp30={snapshot.r_slp_30:.3f}"
                    if snapshot.r_slp_30 is not None
                    else f"proxy(slope_atm={skew.slope_atm:.1f})"
                )
                logger.debug(
                    f"{ticker}: Skew fused: Tradier={skew.directional_bias.value} "
                    f"(R²={skew.bias_confidence:.2f}) + "
                    f"{slope_desc} → {fused_bias.value}"
                )
                skew = SkewAnalysis(
                    ticker=skew.ticker,
                    expiration=skew.expiration,
                    stock_price=skew.stock_price,
                    skew_atm=skew.skew_atm,
                    curvature=skew.curvature,
                    strength=skew.strength,
                    directional_bias=fused_bias,
                    confidence=skew.confidence,
                    num_points=skew.num_points,
                    slope_atm=skew.slope_atm,
                    bias_confidence=skew.bias_confidence,
                )

            # Step 5.7: Build sizing context AFTER fusion so the compound-risk
            # cap (>=2 of [TRR HIGH, sizing alarm, bearish fused skew] -> 25
            # contracts) sees the fused bias, not the raw Tradier-only bias.
            if vrp.is_tradeable:
                sizing_ctx = self._build_sizing_context(
                    snapshot,
                    tail_risk_level,
                    fused_bias=skew.directional_bias if skew is not None else None,
                    vrp_marginal=(vrp.recommendation == Recommendation.MARGINAL),
                )

            # Step 6: Optionally generate trade strategies
            # market_conditions is None only when VIX fetch failed — don't generate
            # strategies without regime confirmation even if raw VRP looks tradeable.
            strategies = None
            option_chain = None
            if generate_strategies and vrp.is_tradeable and market_conditions is not None:
                try:
                    chain_result = self.container.cached_options_provider.get_option_chain(
                        ticker, actual_expiration
                    )
                    if chain_result.is_ok:
                        option_chain = chain_result.value
                        strategies = self.container.strategy_generator.generate_strategies(
                            ticker=ticker,
                            option_chain=option_chain,
                            vrp=vrp,
                            skew=skew,
                            tail_risk_level=tail_risk_level,
                            sizing_context=sizing_ctx,
                        )
                        logger.info(f"{ticker}: Generated {len(strategies.strategies)} strategies")
                    else:
                        logger.warning(f"{ticker}: Could not fetch option chain for strategies: {chain_result.error}")
                except Exception as e:
                    logger.warning(f"{ticker}: Strategy generation failed: {e}")

            # Step 6.5: IV term structure + calendar spread pilot (Jun 2026).
            # Both are best-effort — failures degrade to None, never block analysis.
            term_structure = None
            calendar_candidate = None
            if vrp.is_tradeable:
                term_structure, calendar_candidate = self._analyze_term_structure(
                    ticker, actual_expiration, option_chain, skew, sizing_ctx
                )

            # Compute gap-inclusive close-to-close VRP before building TickerAnalysis
            # so the value is carried on the object (not only logged to the DB).
            close_mean, vrp_close = compute_close_baseline_vrp(
                float(implied_move.implied_move_pct.value), historical_moves
            )

            # Step 6.6: "One position per ticker per earnings event" (CLAUDE.md).
            # Best-effort check against journaled history — see field docstring
            # on TickerAnalysis.existing_position_warning for the real limit
            # (no live open-position tracker exists for this system).
            existing_position_warning = None
            if generate_strategies and strategies is not None:
                existing_position_warning = self._check_existing_position(ticker, earnings_date)

            # Build complete analysis
            analysis = TickerAnalysis(
                ticker=ticker,
                earnings_date=earnings_date,
                earnings_timing=EarningsTiming.AMC,  # Default to After Market Close
                entry_time=datetime.now(),
                expiration=actual_expiration,  # Use adjusted expiration
                implied_move=implied_move,
                vrp=vrp,
                recommendation=vrp.recommendation,
                consistency=consistency,  # Phase 4 enhanced
                skew=skew,  # Phase 4 enhanced (fused if ORATS available)
                term_structure=term_structure,
                sizing_context=sizing_ctx,
                strategies=strategies,  # Strategy recommendations
                calendar_candidate=calendar_candidate,
                vrp_close_ratio=vrp_close,
                historical_close_mean_pct=close_mean,
                existing_position_warning=existing_position_warning,
            )

            # Recent-move streak (observational only, no gate — Sep 2026 OOS
            # backtest found no predictive power). historical_moves is DESC
            # by earnings_date, so [:4] is the last up-to-4 prior quarters.
            recent_window = historical_moves[:4]
            recent_move_qtrs = len(recent_window)
            recent_move_up_count = sum(
                1 for m in recent_window if m.close_move_pct.value > 0
            )

            # Persist to analysis_log — fire-and-forget (failures caught inside repo)
            self.container.analysis_repository.log_analysis(
                analysis,
                market_conditions,
                historical_close_mean_pct=analysis.historical_close_mean_pct,
                vrp_close_ratio=analysis.vrp_close_ratio,
                term_slope_ratio=(
                    term_structure.slope_ratio if term_structure else None
                ),
                recent_move_up_count=recent_move_up_count,
                recent_move_qtrs=recent_move_qtrs,
            )

            return Ok(analysis)

        except Exception as e:
            logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
            return Err(
                AppError(ErrorCode.CALCULATION, f"Analysis failed: {str(e)}")
            )

    def _analyze_term_structure(
        self,
        ticker: str,
        front_expiration,
        front_chain,
        skew,
        sizing_ctx,
    ):
        """
        Compute IV term structure (front vs ~30d-out ATM IV) and, when
        elevated tail risk coincides with backwardation, build the calendar
        spread pilot candidate as the defined-risk alternative to skipping.

        Returns (TermStructureResult | None, Strategy | None). Best-effort:
        any failure returns (None, None) or (ts, None) — never raises.
        """
        from src.application.metrics.term_structure import (
            select_back_expiration,
            analyze_term_structure,
            classify_slope_ratio,
            RATIO_MODERATE,
        )
        from src.application.services.calendar_spread import build_calendar_spread
        from src.domain.enums import DirectionalBias

        provider = self.container.cached_options_provider
        try:
            if front_chain is None:
                chain_result = provider.get_option_chain(ticker, front_expiration)
                if chain_result.is_err:
                    return None, None
                front_chain = chain_result.value

            exp_result = provider.get_expirations(ticker)
            if exp_result.is_err:
                logger.debug(f"{ticker}: expirations unavailable for term structure")
                return None, None

            back_expiration = select_back_expiration(exp_result.value, front_expiration)
            if back_expiration is None:
                return None, None

            back_result = provider.get_option_chain(ticker, back_expiration)
            if back_result.is_err:
                return None, None

            ts_result = analyze_term_structure(front_chain, back_result.value)
            if ts_result.is_err:
                logger.debug(f"{ticker}: term structure failed: {ts_result.error}")
                return None, None
            ts = ts_result.value
            logger.info(
                f"{ticker}: Term structure {classify_slope_ratio(ts.slope_ratio)} — "
                f"front {float(ts.ivs[0].value):.1f} ({ts.expirations[0]}) vs "
                f"back {float(ts.ivs[1].value):.1f} ({ts.expirations[1]}), "
                f"ratio {ts.slope_ratio:.2f}x"
            )

            # Calendar pilot gate: elevated tail risk + real event premium in
            # the front expiry (otherwise the short leg has nothing to crush).
            calendar = None
            risk_elevated = sizing_ctx is not None and (
                sizing_ctx.trr_level == 'HIGH' or sizing_ctx.iv_effect_reduction
            )
            if risk_elevated and ts.slope_ratio is not None and ts.slope_ratio >= RATIO_MODERATE:
                bias = skew.directional_bias if skew is not None else DirectionalBias.NEUTRAL
                cal_result = build_calendar_spread(
                    front_chain, back_result.value, bias, sizing_ctx
                )
                if cal_result.is_ok:
                    calendar = cal_result.value
                else:
                    logger.debug(f"{ticker}: calendar pilot not constructible: {cal_result.error}")

            return ts, calendar
        except Exception as e:
            logger.warning(f"{ticker}: term structure analysis failed: {e}")
            return None, None

    def _compute_tail_risk_level(self, historical_moves) -> Optional[str]:
        gap_moves = [
            abs(m.gap_move_pct.value) for m in historical_moves
            if m.gap_move_pct is not None
        ]
        if len(gap_moves) < 2:
            return None
        avg_gap = sum(gap_moves) / len(gap_moves)
        max_gap = max(gap_moves)
        trr = max_gap / avg_gap if avg_gap > 0 else 0
        if trr > 2.5:
            return 'HIGH'
        if trr >= 1.5:
            return 'NORMAL'
        return 'LOW'

    def _check_existing_position(self, ticker: str, earnings_date: date) -> Optional[str]:
        """
        CLAUDE.md: "One position per ticker per earnings event. No second
        bet, no repair attempt." — a real repair campaign that turned a
        large first loss catastrophic is the exact failure mode this guards
        against.

        This is a best-effort check against the journaled `strategies`
        table (populated by /journal after fills are imported from Fidelity
        exports), NOT a live open-position tracker — no such tracker exists
        for the main earnings system (unlike TACO's taco_positions, which
        is updated in real time). It will catch a repeat attempt on a
        ticker+earnings_date that was already journaled; it cannot catch a
        same-day repeat entered before /journal has been run for the first
        leg. Best-effort, not a guarantee — surfaced as a warning, not a
        hard block, since a human may have deliberately closed and
        re-entered, or the journal may simply be behind.
        """
        try:
            with self.container.db_pool.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT strategy_type, quantity, acquired_date, sale_date, "
                    "gain_loss, campaign_id FROM strategies "
                    "WHERE symbol = ? AND earnings_date = ? "
                    "ORDER BY acquired_date DESC LIMIT 5",
                    (ticker, earnings_date.isoformat()),
                )
                rows = cursor.fetchall()
        except Exception as e:
            logger.debug(f"{ticker}: Existing-position check failed (non-fatal): {e}")
            return None

        if not rows:
            return None

        n = len(rows)
        latest = rows[0]
        detail = (
            f"{n} prior journaled position(s) already exist for {ticker} on "
            f"{earnings_date.isoformat()} — most recent: {latest['strategy_type']} "
            f"x{latest['quantity']}, acquired {latest['acquired_date']}"
        )
        if latest['campaign_id']:
            detail += f", campaign={latest['campaign_id']}"
        logger.warning(f"{ticker}: {detail} — CLAUDE.md: no second bet, no repair attempt.")
        return detail

    def _load_position_limits(self, ticker: str) -> PositionLimitsSnapshot:
        with self.container.db_pool.get_connection() as conn:
            cursor = conn.execute(
                "SELECT fcst_ern_iv_effect, iee_earn_effect, r_slp_30, last_updated "
                "FROM position_limits WHERE ticker = ?",
                (ticker,)
            )
            row = cursor.fetchone()
        if not row:
            return PositionLimitsSnapshot()
        if snapshot_is_stale(row['last_updated']):
            logger.warning(
                f"{ticker}: position_limits snapshot is stale "
                f"(last_updated={row['last_updated']}, max age "
                f"{ORATS_SNAPSHOT_MAX_AGE_DAYS}d) — treating as absent. "
                f"ORATS-era snapshot (subscription ended Jun 2026): "
                f"historical context only, not refreshable."
            )
            return PositionLimitsSnapshot()
        return PositionLimitsSnapshot(
            fcst_ern_iv_effect=row['fcst_ern_iv_effect'],
            iee_earn_effect=row['iee_earn_effect'],
            r_slp_30=row['r_slp_30'],
        )

    def _build_sizing_context(
        self,
        snapshot: PositionLimitsSnapshot,
        trr_level: Optional[str],
        fused_bias: Optional['DirectionalBias'] = None,
        vrp_marginal: bool = False,
    ) -> SizingContext:
        return SizingContext(
            trr_level=trr_level,
            fcst_ern_iv_effect=snapshot.fcst_ern_iv_effect,
            iee_earn_effect=snapshot.iee_earn_effect,
            fused_bias=fused_bias,
            vrp_marginal=vrp_marginal,
        )

    def _fuse_skew_signals(
        self,
        tradier_skew: SkewAnalysis,
        r_slp_30: Optional[float],
    ) -> DirectionalBias:
        # Delegates to the shared fusion formula (skew_enhanced.fuse_skew_bias)
        # so live display and persisted bias_predictions can never drift.
        fused = fuse_skew_bias(
            tradier_bias=tradier_skew.directional_bias,
            tradier_conf=tradier_skew.bias_confidence,
            slope_atm=tradier_skew.slope_atm,
            r_slp_30=r_slp_30,
        )
        if fused is None:
            # No slope signal at all — callers guard against this, but fall
            # back to the raw Tradier bias rather than raising.
            return tradier_skew.directional_bias
        return fused

    def _apply_adaptive_thresholds(
        self, ticker: str, vrp: VRPResult
    ) -> tuple[VRPResult, Optional[MarketConditions]]:
        """Apply adaptive thresholds based on current VIX regime.

        In elevated volatility environments, we require higher VRP ratios
        to maintain edge. This may downgrade recommendations from the base
        VRP calculation.

        Args:
            ticker: Stock ticker (for logging)
            vrp: Original VRP result with base recommendation

        Returns:
            Tuple of (adjusted VRPResult, MarketConditions or None on failure)
        """
        try:
            # Get current market conditions (cached for 15 min — one fetch per scan session)
            market_conditions_result = self.container.market_conditions_analyzer.get_current_conditions()

            if market_conditions_result.is_err:
                logger.warning(
                    f"{ticker}: VIX unavailable — adaptive thresholds NOT applied. "
                    f"Base recommendation ({vrp.recommendation.value}) may overstate edge."
                )
                return vrp, None

            market_conditions = market_conditions_result.value

            # Get adaptive thresholds based on VIX regime
            adapted = self.container.adaptive_threshold_calculator.calculate(market_conditions)

            # Check if trading is not recommended in current regime
            if not adapted.trade_recommended:
                logger.warning(
                    f"{ticker}: Trading not recommended in {adapted.regime} regime "
                    f"(VIX={adapted.vix_level:.1f}). Overriding to SKIP."
                )
                return VRPResult(
                    ticker=vrp.ticker,
                    expiration=vrp.expiration,
                    implied_move_pct=vrp.implied_move_pct,
                    historical_mean_move_pct=vrp.historical_mean_move_pct,
                    vrp_ratio=vrp.vrp_ratio,
                    edge_score=vrp.edge_score,
                    recommendation=Recommendation.SKIP,
                ), market_conditions

            # Re-evaluate recommendation using adapted thresholds
            if vrp.vrp_ratio >= adapted.vrp_excellent:
                new_recommendation = Recommendation.EXCELLENT
            elif vrp.vrp_ratio >= adapted.vrp_good:
                new_recommendation = Recommendation.GOOD
            elif vrp.vrp_ratio >= adapted.vrp_marginal:
                new_recommendation = Recommendation.MARGINAL
            else:
                new_recommendation = Recommendation.SKIP

            # Log if recommendation changed
            if new_recommendation != vrp.recommendation:
                logger.info(
                    f"{ticker}: Adaptive thresholds adjusted recommendation from "
                    f"{vrp.recommendation.value} → {new_recommendation.value} "
                    f"(VIX regime: {adapted.regime}, factor: {adapted.adjustment_factor:.1f}x)"
                )
                return VRPResult(
                    ticker=vrp.ticker,
                    expiration=vrp.expiration,
                    implied_move_pct=vrp.implied_move_pct,
                    historical_mean_move_pct=vrp.historical_mean_move_pct,
                    vrp_ratio=vrp.vrp_ratio,
                    edge_score=vrp.edge_score,
                    recommendation=new_recommendation,
                ), market_conditions

            # Log that adaptive thresholds were applied but no change needed
            if adapted.is_adjusted:
                logger.debug(
                    f"{ticker}: Adaptive thresholds applied (factor: {adapted.adjustment_factor:.1f}x) "
                    f"but recommendation unchanged: {vrp.recommendation.value}"
                )

            return vrp, market_conditions

        except Exception as e:
            logger.warning(
                f"{ticker}: Error applying adaptive thresholds: {e}. "
                f"Using base recommendation: {vrp.recommendation.value}"
            )
            return vrp, None
