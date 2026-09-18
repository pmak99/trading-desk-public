"""Ticker analyzer service for IV Crush analysis."""

import logging
from datetime import date
from typing import Optional

from common.constants import ORATS_SNAPSHOT_MAX_AGE_DAYS  # noqa: E402
from common.timezone import now_et
from src.application.metrics.implied_move_expiration import implied_move_expiration
from src.application.metrics.skew_enhanced import SkewAnalysis, fuse_skew_bias
from src.application.metrics.vrp import logged_vrp_columns
from src.application.services.existing_position import existing_position_warning
from src.application.services.stale_history import stale_history_warning
from src.domain.enums import DirectionalBias, EarningsTiming, Recommendation
from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.market_conditions import MarketConditions
from src.domain.tail_risk import tail_risk_level
from src.domain.types import (
    PositionLimitsSnapshot,
    SizingContext,
    TickerAnalysis,
    snapshot_is_stale,
)

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
        earnings_timing: EarningsTiming,
        generate_strategies: bool = False
    ) -> Result[TickerAnalysis, AppError]:
        """Analyze a ticker for IV Crush opportunity.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Date of earnings announcement
            expiration: TRADING expiration (calculate_expiration_date), adjusted to
                the nearest listed one. Strategies, skew and term structure use it.
            earnings_timing: BMO/AMC/DMH/UNKNOWN. Picks the implied-move
                expiration, the same one scan/whisper/bot price at.
            generate_strategies: If True, generate trade strategies (bull put, bear call)

        Returns:
            Result containing TickerAnalysis or error
        """
        sizing_ctx = None
        provider = self.container.cached_options_provider
        try:
            # Step 0: Find nearest available trading expiration
            nearest_exp_result = provider.find_nearest_expiration(ticker, expiration)
            if nearest_exp_result.is_err:
                return Err(nearest_exp_result.error)

            actual_expiration = nearest_exp_result.value
            if actual_expiration != expiration:
                logger.info(
                    f"{ticker}: Using adjusted expiration {actual_expiration} "
                    f"(requested {expiration})"
                )

            # Step 1: Implied move at the first expiration containing the
            # reaction, not the trading expiration: the later expiry's extra
            # diffusion inflated VRP on /analyze only (audit 3.1).
            im_exp_result = implied_move_expiration(
                provider, ticker, earnings_date, earnings_timing
            )
            if im_exp_result.is_err:
                return Err(im_exp_result.error)
            implied_move_exp = im_exp_result.value
            implied_result = self.container.implied_move_calculator.calculate(
                ticker, implied_move_exp
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

            # A gap here means at least one quarter is missing from
            # historical_moves entirely -- VRP/TRR below window off whatever is
            # stored, stale or not. Checked before the min_quarters gate: a
            # ticker can clear that count and still be missing its two most
            # recent reactions (LEN: 12 quarters on file, but the newest two
            # were six months stale).
            stale_warning = stale_history_warning(ticker, historical_moves, earnings_date)

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

            # Step 3.6: both conventions' VRP for analysis_log -- close-to-close
            # graded above, intraday recomputed as a reference.
            vrp_columns = logged_vrp_columns(vrp, historical_moves)

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
                if self.container.config.api.orats_enabled:
                    snapshot = self._load_position_limits(ticker)

            # Step 5.6: Fuse skew with ORATS rSlp30 when available. When r_slp_30
            # is None (ORATS retired Jun 2026, or ticker missing from snapshot)
            # there is no second source and the Tradier bias passes through
            # unchanged (skew_enhanced.fuse_skew_bias).
            if skew is not None and (
                snapshot.r_slp_30 is not None or skew.slope_atm is not None
            ):
                fused_bias = self._fuse_skew_signals(skew, snapshot.r_slp_30)
                slope_desc = (
                    f"rSlp30={snapshot.r_slp_30:.3f}"
                    if snapshot.r_slp_30 is not None
                    else "no second source (Tradier only)"
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
            strategies = None
            option_chain = None
            if generate_strategies and vrp.is_tradeable:
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
            #
            # Term structure is computed for EVERY ticker, tradeable or not.
            # It is the logged A/B variable, and gating it on is_tradeable
            # biased the sample toward names that already look like good
            # candidates: the Sep 4 2026 evaluation found the flat/mild
            # (<1.10) comparison bucket held 0 of 37 qualifying events, so the
            # hypothesis under test could not be evaluated at all. The calendar
            # pilot stays gated on is_tradeable — that one is a trade
            # recommendation, not an observation, and must never be built for a
            # ticker the VRP gate rejected.
            term_structure, calendar_candidate = self._analyze_term_structure(
                ticker, actual_expiration, option_chain, skew, sizing_ctx,
                allow_calendar=vrp.is_tradeable,
            )

            # Step 6.6: "One position per ticker per earnings event" (CLAUDE.md).
            # The same check scan, whisper and the bot run, on every analysis:
            # it was gated on generate_strategies until 2026-09-14. It sees
            # closed, journaled trades only (services/existing_position.py).
            existing_position = existing_position_warning(
                self.container.db_pool, ticker, earnings_date
            )

            # Build complete analysis
            analysis = TickerAnalysis(
                ticker=ticker,
                earnings_date=earnings_date,
                earnings_timing=earnings_timing,
                entry_time=now_et(),
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
                **vrp_columns,
                existing_position_warning=existing_position,
                stale_history_warning=stale_warning,
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
                self._market_conditions_for_log(ticker),
                term_slope_ratio=(
                    term_structure.slope_ratio if term_structure else None
                ),
                recent_move_up_count=recent_move_up_count,
                recent_move_qtrs=recent_move_qtrs,
                source="analyze",
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
        allow_calendar: bool = True,
    ):
        """
        Compute IV term structure (front vs ~30d-out ATM IV) and, when
        elevated tail risk coincides with backwardation, build the calendar
        spread pilot candidate as the defined-risk alternative to skipping.

        allow_calendar=False computes and returns the term structure but never
        builds a calendar candidate. Callers pass False for tickers that failed
        the VRP gate: the term structure is still logged (it is the A/B
        variable), but a non-tradeable ticker must not produce a trade.

        Returns (TermStructureResult | None, Strategy | None). Best-effort:
        any failure returns (None, None) or (ts, None) — never raises.
        """
        from src.application.metrics.term_structure import (
            RATIO_MODERATE,
            analyze_term_structure,
            classify_slope_ratio,
            select_back_expiration,
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
            if (allow_calendar and risk_elevated and ts.slope_ratio is not None
                    and ts.slope_ratio >= RATIO_MODERATE):
                bias = skew.directional_bias if skew is not None else DirectionalBias.NEUTRAL
                cal_result = build_calendar_spread(
                    front_chain, back_result.value, self.container.liquidity_scorer,
                    bias, sizing_ctx,
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
        return tail_risk_level(
            m.gap_move_pct.value for m in historical_moves if m.gap_move_pct is not None
        )

    def _load_position_limits(self, ticker: str) -> PositionLimitsSnapshot:
        with self.container.db_pool.connection() as conn:
            cursor = conn.execute(
                "SELECT fcst_ern_iv_effect, iee_earn_effect, r_slp_30, last_updated "
                "FROM position_limits WHERE ticker = %s",
                (ticker,)
            )
            row = cursor.fetchone()
        if not row:
            return PositionLimitsSnapshot()
        if snapshot_is_stale(row.last_updated):
            logger.warning(
                f"{ticker}: position_limits snapshot is stale "
                f"(last_updated={row.last_updated}, max age "
                f"{ORATS_SNAPSHOT_MAX_AGE_DAYS}d) — treating as absent. "
                f"ORATS-era snapshot (subscription ended Jun 2026): "
                f"historical context only, not refreshable."
            )
            return PositionLimitsSnapshot()
        return PositionLimitsSnapshot(
            fcst_ern_iv_effect=row.fcst_ern_iv_effect,
            iee_earn_effect=row.iee_earn_effect,
            r_slp_30=row.r_slp_30,
        )

    def _build_sizing_context(
        self,
        snapshot: PositionLimitsSnapshot,
        trr_level: Optional[str],
        fused_bias: Optional['DirectionalBias'] = None,
        vrp_marginal: bool = False,
    ) -> SizingContext:
        # Tiers come from config, not the SizingContext defaults — otherwise
        # SIZING_* env vars are unreachable on the live path (the same defect
        # that made MAX_CONTRACTS a no-op until Sep 2026).
        return SizingContext.from_strategy_config(
            self.container.config.strategy,
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

    def _market_conditions_for_log(self, ticker: str) -> Optional[MarketConditions]:
        """VIX regime for the analysis_log row, or None. Display and logging only.

        Until 2026-09-14 this regime also raised the VRP thresholds 1.1-1.5x and
        refused every trade at VIX 40+, on /analyze alone. A pre-registered test
        (scripts/research/vix_adaptive_thresholds.py) found 29 demoted events on
        17 dates in three years, too few to support it, and those crushed more
        often than the events kept, so every path now uses the CLAUDE.md tiers.
        """
        try:
            result = self.container.market_conditions_analyzer.get_current_conditions()
        except Exception:
            logger.warning(f"{ticker}: VIX regime fetch failed — analysis_log vix_level NULL",
                           exc_info=True)
            return None
        if result.is_err:
            logger.debug(f"{ticker}: VIX regime unavailable: {result.error}")
            return None
        return result.value
