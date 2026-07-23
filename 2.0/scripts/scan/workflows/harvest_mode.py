"""harvest_mode — non-earnings 45 DTE premium harvest candidates."""
import logging

from src.container import Container

from ..constants import (
    HARVEST_EARNINGS_EXCLUSION_DAYS,
    HARVEST_TOP_N,
    MEGACAP_CLUSTER,
)
from ..market_data import validate_harvest_chain, _harvest_target_expiry
from ..harvest_live import get_live_harvest_candidates
from ..quality_scorer import calculate_harvest_score, _rslp30_to_skew_label

from .vix import _check_vix_term_structure

logger = logging.getLogger(__name__)


def harvest_mode(
    container,
    db_path: str,
    top_n: int = HARVEST_TOP_N,
    iv_rank_min: float = 60.0,
    earnings_exclusion_days: int = HARVEST_EARNINGS_EXCLUSION_DAYS,
) -> int:
    """
    Surface non-earnings premium harvest candidates (~45 DTE entry, close at 21 DTE).

    Phase 1: SQL filter from position_limits (IV rank, TRR, skew, earnings exclusion).
    Phase 2: Parallel Tradier chain validation — real bid, OI, $5-wide spread credit.

    Returns 0 on success, 1 on DB error or no data.
    """
    import datetime as _dt
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # ── VIX term structure check ──────────────────────────────────────────────
    vix_val, vix3m_val, vts_label = _check_vix_term_structure()
    logger.info("\nVIX TERM STRUCTURE")
    if vts_label == 'UNKNOWN':
        logger.info("  VIX Term Structure: unavailable (skipping check)")
    else:
        ratio_str = f"{vix3m_val / vix_val:.2f}x" if vix_val and vix_val > 0 else "N/A"
        if vts_label == 'CONTANGO':
            logger.info(
                f"  VIX: {vix_val:.1f}  |  VIX3M: {vix3m_val:.1f}  |  "
                f"Ratio: {ratio_str}  |  CONTANGO ✓ (normal for short-vol)"
            )
        elif vts_label == 'FLAT':
            logger.info(
                f"  VIX: {vix_val:.1f}  |  VIX3M: {vix3m_val:.1f}  |  "
                f"Ratio: {ratio_str}  |  FLAT (neutral)"
            )
        elif vts_label == 'BACKWARDATION':
            logger.info(
                f"  VIX: {vix_val:.1f}  |  VIX3M: {vix3m_val:.1f}  |  "
                f"Ratio: {ratio_str}  |  BACKWARDATION ⚠"
            )
            logger.info("  → Market stress signal. Reduce 45 DTE position sizes by 50%.")
        else:  # STRESS
            logger.info(
                f"  VIX: {vix_val:.1f}  |  VIX3M: {vix3m_val:.1f}  |  "
                f"Ratio: {ratio_str}  |  STRESS ⛔"
            )
            logger.info("  → High-stress regime. Consider pausing 45 DTE sleeve entirely.")

    # ── Phase 1: live candidate fetch (Tradier IV + yfinance HV/vol-index) ──
    logger.info("\nFetching live IV/HV for the harvest universe...")
    candidates, phase1_skipped = get_live_harvest_candidates(
        container,
        db_path=db_path,
        iv_rank_min=iv_rank_min,
        earnings_exclusion_days=earnings_exclusion_days,
    )

    if phase1_skipped:
        logger.info("Phase 1 exclusions:")
        for ticker, reason in phase1_skipped:
            logger.info(f"  {ticker}: {reason}")

    if not candidates:
        logger.info("\n" + "=" * 70)
        logger.info("PREMIUM HARVEST — No candidates found")
        logger.info("=" * 70)
        logger.info(
            f"\nNo tickers passed live filters: index IVR >= {iv_rank_min:.0f}%, "
            f"IV/HV >= 1.2, TRR != HIGH, "
            f"no earnings in next {earnings_exclusion_days}d."
        )
        return 1

    # Score and sort before chain validation (so we prioritise API calls)
    for c in candidates:
        c['_harvest_score'] = calculate_harvest_score(c)
    candidates.sort(key=lambda x: x['_harvest_score'], reverse=True)

    # ── Phase 2: parallel chain validation ───────────────────────────────────
    target_expiry = _harvest_target_expiry(_dt.date.today())
    dte = (target_expiry - _dt.date.today()).days
    logger.info(f"\nValidating {len(candidates)} candidates vs live chain at {target_expiry} ({dte} DTE)...")

    chain_data: dict[str, dict] = {}

    def _validate(ticker: str) -> tuple[str, dict]:
        return ticker, validate_harvest_chain(ticker, target_expiry, container)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_validate, c['ticker']): c['ticker'] for c in candidates}
        done = 0
        for fut in as_completed(futures):
            ticker, data = fut.result()
            chain_data[ticker] = data
            done += 1
            if done % 10 == 0:
                logger.info(f"  ... {done}/{len(candidates)} chains fetched")

    # Merge chain data into candidates; filter SKIP+THIN below minimum credit
    MIN_CREDIT = 0.40  # minimum spread credit (absolute, not width-relative)
    validated = []
    skipped = []
    for c in candidates:
        cd = chain_data.get(c['ticker'], {})
        c.update({
            '_chain': cd,
            '_credit':     cd.get('best_credit', 0.0),
            '_side':       cd.get('best_side', 'SKIP'),
            '_liq':        cd.get('liq_tier', 'THIN'),
            '_short_strike': cd.get('short_strike'),
            '_stock_price':  cd.get('stock_price', 0.0),
        })
        if c['_credit'] >= MIN_CREDIT and c['_side'] != 'SKIP':
            validated.append(c)
        else:
            skipped.append(c['ticker'])

    if not validated:
        logger.info(
            f"\nNo candidates passed chain validation "
            f"(all below ${MIN_CREDIT:.2f} credit or illiquid)."
        )
        logger.info(f"Skipped: {', '.join(skipped[:10])}")
        return 1

    # Re-rank: 70% original score + 30% credit score (credit up to $2.00 = full 30pts)
    for c in validated:
        credit_score = min(c['_credit'] / 2.00, 1.0) * 30.0
        c['_final_score'] = round(c['_harvest_score'] * 0.70 + credit_score, 1)
    validated.sort(key=lambda x: x['_final_score'], reverse=True)
    display = validated[:top_n]

    # Megacap cluster warning
    megacap_hits = [c['ticker'] for c in display if c['ticker'] in MEGACAP_CLUSTER]
    if len(megacap_hits) >= 3:
        _megacap_warn = (
            f"\n⚠ MEGACAP CLUSTER ({len(megacap_hits)} names): {', '.join(megacap_hits)}\n"
            f"  3+ highly correlated positions. Skip the lowest-scoring one or reduce all to half-size.\n"
            f"  These names move together in stress scenarios — diversification benefit is low."
        )
    elif len(megacap_hits) == 2:
        _megacap_warn = (
            f"\n⚠ MEGACAP CLUSTER (2 names): {', '.join(megacap_hits)}\n"
            f"  Correlated pair. Reduce each to half-size vs a single uncorrelated name."
        )
    else:
        _megacap_warn = None

    # ── Output ───────────────────────────────────────────────────────────────
    today_str = _dt.date.today().strftime("%b %d, %Y")
    logger.info("\n" + "=" * 90)
    logger.info(f"PREMIUM HARVEST — {today_str}  |  Expiry: {target_expiry} ({dte} DTE)")
    logger.info("~45 DTE entry | Close at 21 DTE | Exit: 50% profit OR 21 DTE")
    logger.info("=" * 90)

    header = (
        f"  {'#':>3}  {'Ticker':<7}  {'Score':>5}  {'IV Rank':>7}  {'IV/HV':>6}  "
        f"{'Skew':<13}  {'Credit':>6}  {'Liq':<5}  {'Side':<5}  {'Strike':>7}  Next Earnings"
    )
    logger.info(f"\n{header}")
    logger.info("  " + "-" * 88)

    warn_tickers = []

    for i, c in enumerate(display, 1):
        skew_label = _rslp30_to_skew_label(c.get('r_slp_30'))
        skew_short = {
            'STRONG_BULLISH': 'Str Bull',
            'BULLISH':        'Bull    ',
            'WEAK_BULLISH':   'Wk Bull ',
            'NEUTRAL':        'Neutral ',
            'WEAK_BEARISH':   'Wk Bear ',
            'BEARISH':        'Bear    ',
            'STRONG_BEARISH': 'Str Bear',
        }.get(skew_label, 'N/A     ')

        iv_rank   = c.get('iv_rank_1y')
        iv_rank_str = f"{iv_rank:>6.1f}%" if iv_rank is not None else "   n/a*"
        iv_hv     = c.get('iv_hv_ratio') or 1.0
        credit    = c['_credit']
        liq       = c['_liq']
        side      = c['_side']
        strike    = c['_short_strike']
        final_sc  = c['_final_score']
        next_ern  = c.get('next_earnings')

        strike_str = f"${strike:.0f}" if strike else 'N/A'

        # Liq badge
        liq_badge = {'GOOD': '✓', 'WARN': '⚠', 'THIN': '✗'}.get(liq, '?')

        if next_ern:
            try:
                ern_date = _dt.datetime.strptime(next_ern, '%Y-%m-%d').date()
                days_to_ern = (ern_date - _dt.date.today()).days
                ern_display = ern_date.strftime('%b %d')
                if days_to_ern <= 45:
                    warn_tickers.append((c['ticker'], ern_display, days_to_ern))
            except ValueError:
                ern_display = next_ern
        else:
            ern_display = 'N/A'

        logger.info(
            f"  {i:>3}  {c['ticker']:<7}  {final_sc:>5.1f}  {iv_rank_str}  {iv_hv:>5.2f}x  "
            f"{skew_short}  ${credit:>5.2f}  {liq_badge}{liq:<4}  {side:<5}  {strike_str:>7}  {ern_display}"
        )

    # Summary
    logger.info(
        f"\n  Screened: {len(candidates)} | Validated: {len(validated)} "
        f"| Displayed: {len(display)}"
        + (f" | Skipped (thin/skip): {len(skipped)}" if skipped else "")
    )

    if _megacap_warn:
        logger.info(_megacap_warn)

    if any(c.get('iv_rank_1y') is None for c in display):
        logger.info(
            "\n  * n/a: single-name IVR has no live source until iv_history "
            "matures (~Jun 2027).\n"
            "    IVR > 25 is a HARD sleeve gate — verify IVR at the broker "
            "before entering any n/a name."
        )
    if any(c.get('ivr_source') == 'vol-index' for c in display):
        logger.info(
            "  Index IVR = 52-week percentile of the CBOE vol index "
            "(SPY:VIX, QQQ:VXN), computed live. RVX is discontinued — "
            "IWM shows n/a."
        )

    logger.info("\nTRADE PARAMETERS")
    logger.info(f"  Expiry    : {target_expiry} ({dte} DTE)")
    logger.info("  Structure : Defined-risk spreads — $5 wide max")
    logger.info("              Index options (SPX/SPXW/NDX): $10-25 wide, 60/40 tax treatment")
    logger.info("  Entry     : ~45 DTE (nearest standard monthly expiry >= 30 DTE)")
    logger.info("  Exit      : 50% profit OR 21 DTE remaining — whichever first")
    logger.info("  Credit    : Estimated $5-wide spread credit at 30-delta short leg")
    logger.info("  Size      : max_contracts per TRR level; no single name > 25% of harvest portfolio")
    logger.info("  Liq       : ✓GOOD = bid≥$1.00 + OI≥200 | ⚠WARN = bid≥$0.75 + OI≥100 | ✗THIN = below")

    if warn_tickers:
        logger.info("\nEARNINGS APPROACHING (within 45d — plan exit before earnings):")
        for ticker, ern_display, days_to_ern in warn_tickers:
            close_by = (_dt.date.today() + _dt.timedelta(days=days_to_ern - 21)).strftime('%b %d')
            logger.info(f"  {ticker}: {ern_display} ({days_to_ern}d away) → close by {close_by}")

    logger.info(f"\n\U0001f4a1 Run './trade.sh TICKER YYYY-MM-DD' for full options chain and strategy details")
    return 0
