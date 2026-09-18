"""VIX context and term structure helpers shared across all scanning modes."""
import logging

from src.container import Container
from src.infrastructure.api.yahoo import yahoo_call

logger = logging.getLogger(__name__)


def _log_vix_context(container: Container) -> None:
    """Fetch VIX once and log the regime, with a warning if elevated.

    Shared by scan and /analyze. A failed fetch is a WARNING with its
    traceback: it was `except Exception: pass`, which silently dropped the
    elevated-VIX warning when it was needed.
    """
    try:
        result = container.market_conditions_analyzer.get_current_conditions()
        if result.is_ok:
            mc = result.value
            vix_val = mc.vix_level.value
            logger.info(f"VIX: {vix_val:.1f} ({mc.regime})")
            if mc.regime in ('elevated_high', 'high', 'extreme'):
                logger.warning(
                    f"⚠  VIX {vix_val:.1f} ({mc.regime}) — VRP tiers and sizing do not "
                    f"adjust for VIX; the size call is yours"
                )
        else:
            logger.warning(f"VIX regime unavailable: {result.error}")
    except Exception:
        logger.warning("VIX regime check failed — no regime warning this run", exc_info=True)


def _check_vix_term_structure() -> tuple:
    """
    Fetch VIX (30-day) and VIX3M (90-day) to assess vol term structure.
    Returns (vix, vix3m, regime_label) where regime_label is one of:
      'CONTANGO'      — VIX3M/VIX >= 1.05  (normal, healthy for short-vol)
      'FLAT'          — VIX3M/VIX 0.95-1.05
      'BACKWARDATION' — VIX3M/VIX < 0.95   (stress, reduce exposure)
      'STRESS'        — VIX3M/VIX < 0.85   (high stress, consider pausing)
    On any fetch error, returns (None, None, 'UNKNOWN').
    """
    try:
        import math

        import yfinance as yf
        # Under the Yahoo limit. download() swallows a symbol's rate limit into
        # a NaN leg (handled below), so this call is limited but never retried.
        data = yahoo_call(lambda: yf.download(["^VIX", "^VIX3M"], period="2d",
                                              progress=False, auto_adjust=False))
        closes = data["Close"].iloc[-1]
        vix = float(closes["^VIX"])
        vix3m = float(closes["^VIX3M"])
        # A failed download leg comes back NaN, not an exception — NaN falls
        # through every ratio comparison to STRESS, the most alarming label.
        if math.isnan(vix):
            vix = None
        if math.isnan(vix3m):
            vix3m = None
        if vix is None or vix3m is None or vix <= 0:
            return vix, vix3m, 'UNKNOWN'
        ratio = vix3m / vix
        if ratio >= 1.05:
            label = 'CONTANGO'
        elif ratio >= 0.95:
            label = 'FLAT'
        elif ratio >= 0.85:
            label = 'BACKWARDATION'
        else:
            label = 'STRESS'
        return vix, vix3m, label
    except Exception:
        return None, None, 'UNKNOWN'
