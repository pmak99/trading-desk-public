"""Render 2.0 scan result dicts as Telegram HTML.

Consumes the dict shape produced by scan.workflows.ticker_analysis
.analyze_ticker() — see ScanRun.results for the full key list. It must only ever
DISPLAY those values, never recompute them: 5.0's digest calculated its own
simplified VRP and composite score, which is why the message on the phone could
rank tickers differently than `/whisper`, and why CLAUDE.md had to declare 2.0
authoritative for trade decisions. That divergence is the thing being retired.

Ranking is by '_quality_score' — the risk-adjusted composite from
scan/quality_scorer.py — not by raw VRP.
"""

import html
from typing import Any, Dict, List

# Emoji by VRP recommendation tier, matching the CLI's tiering.
TIER_EMOJI = {
    "EXCELLENT": "\U0001f7e2",   # green
    "GOOD": "\U0001f7e2",
    "MARGINAL": "\U0001f7e1",    # yellow — tradeable at 50% size
    "SKIP": "⚪",
}

LIQUIDITY_EMOJI = {
    "EXCELLENT": "✅",
    "GOOD": "✅",
    "WARNING": "⚠️",
    "REJECT": "❌",
}


def _esc(value: Any) -> str:
    """Escape for HTML parse mode. Every interpolated value goes through this —
    a stray '<' makes Telegram reject the whole message."""
    return html.escape(str(value))


def _score(row: Dict[str, Any]) -> float:
    """Quality score, tolerating both a missing key and an explicit null.

    `.get(key, 0.0)` would NOT fire on an explicit None — a recurring trap in
    this codebase — and '_quality_score' is only added to tradeable rows, and
    only after _precalculate_quality_scores() has run.
    """
    return float(row.get("_quality_score") or 0.0)


def format_ticker_line(row: Dict[str, Any], rank: int) -> str:
    """One ranked line of the digest, plus the one-position-per-event warning
    beneath it when the ticker already carries a journaled position."""
    tier = str(row.get("recommendation", "")).upper()
    liq = str(row.get("liquidity_tier", "")).upper()
    # Quarters behind the tier: 4 and 12 read the same without it.
    quarters = row.get("historical_quarters")
    graded_on = f" ({_esc(quarters)}Q)" if quarters is not None else ""

    line = (
        f"{rank}. {TIER_EMOJI.get(tier, '⚪')} <b>{_esc(row.get('ticker'))}</b>"
        f"  VRP {float(row.get('vrp_ratio') or 0):.2f}x{graded_on}"
        # implied_move_pct / historical_mean_pct are str(Percentage), which
        # ALREADY carries a trailing "%" — appending one renders "7.48%%".
        f"  · IM {_esc(row.get('implied_move_pct'))}"
        f" vs {_esc(row.get('historical_mean_pct'))}"
        f"  · {LIQUIDITY_EMOJI.get(liq.rstrip('*'), '')} {_esc(liq.title())}"
        f"  · {_esc(row.get('earnings_date'))}"
    )
    warning = row.get("existing_position_warning")
    if warning:
        line += f"\n   \U0001f6a8 {_esc(warning)}"
    return line


def format_digest(
    results: List[Dict[str, Any]],
    title: str,
    max_rows: int = 10,
) -> str:
    """Render tradeable candidates, best quality score first.

    Non-tradeable rows are dropped rather than shown greyed out: MARGINAL VRP
    counts as tradeable (at half size), so `is_tradeable` already encodes the
    right cut and must not be re-derived here from the recommendation string.
    """
    header = f"<b>{_esc(title)}</b>"

    tradeable = [r for r in results if r.get("is_tradeable")]
    if not tradeable:
        analyzed = f" ({len(results)} analyzed)" if results else ""
        return f"{header}\n\nNo tradeable candidates{analyzed}."

    ranked = sorted(tradeable, key=_score, reverse=True)[:max_rows]
    lines = [format_ticker_line(r, i) for i, r in enumerate(ranked, start=1)]

    footer = (
        f"\n<i>{len(tradeable)} tradeable of {len(results)} analyzed "
        f"· 2.0 engine</i>"
    )
    return "\n".join([header, ""] + lines) + footer
