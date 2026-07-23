"""TACO CLI — argparse front end over the tested engine modules.

Invoked via: ./trade.sh taco <subcommand>  (see .claude/commands/taco.md).
DB path resolves like the rest of 2.0: DB_PATH env var, default data/ivcrush.db.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime

from .constants import (
    CROSS_ASSET_SYMBOLS,
    MIN_DTE,
    STRIKE_OTM_MAX,
    STRIKE_OTM_MIN,
    TARGET_DTE_MAX,
    TARGET_DTE_MIN,
    EventType,
    Tier,
)
from .cross_asset import evaluate_cross_asset, fetch_asset_series
from .exits import evaluate_exit
from .guard import WinddownPhase, entries_allowed, winddown_phase
from .market_data import (
    SPX_SYMBOL,
    VIX3M_SYMBOL,
    VIX_SYMBOL,
    EntryRefs,
    compute_refs,
    default_event_date,
    drawdown_from_level,
    fetch_intraday_quote,
    fetch_series,
    runup_zscore_from_level,
    up_to,
    vix_spike_from_level,
    with_live_override,
)
from .scoring import score_entry
from .store import close_position, log_run, open_positions, record_position
from src.utils.market_hours import get_market_status


def _db_path() -> str:
    return os.getenv("DB_PATH", "data/ivcrush.db")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _live_quotes():
    """Live SPX/VIX quotes during market hours; None/None otherwise (or on
    fetch failure) — callers fall back to the daily-close series. Returns
    (live_spx, live_vix, data_source_label) for consistent [data: ...]
    tagging across every TACO command that reads current market levels."""
    market_open, market_reason = get_market_status()
    live_spx = fetch_intraday_quote(SPX_SYMBOL) if market_open else None
    live_vix = fetch_intraday_quote(VIX_SYMBOL) if market_open else None
    if market_open and live_spx is not None and live_vix is not None:
        data_source = "intraday"
    elif market_open:
        data_source = "prior close (intraday fetch failed, market open)"
    else:
        data_source = f"prior close ({market_reason})"
    return live_spx, live_vix, data_source


def cmd_check(args) -> int:
    today = date.today()
    ok, guard_msg = entries_allowed(today)
    if guard_msg:
        print(f"⚠️  {guard_msg}")
    if not ok:
        return 1

    spx = fetch_series(SPX_SYMBOL, "1y")
    vix = fetch_series(VIX_SYMBOL, "6mo")
    vix3m = fetch_series(VIX3M_SYMBOL, "6mo")
    if not spx or not vix:
        print("ERROR: SPX/VIX download failed — cannot score")
        return 1

    direction = args.direction.upper()
    event_date = (_parse_date(args.event_date) if args.event_date
                  else default_event_date(spx, today))

    live_spot, live_vix, data_source = _live_quotes()
    spot = live_spot if live_spot is not None else spx[-1][1]
    vix_level = live_vix if live_vix is not None else vix[-1][1]

    spike = vix_spike_from_level(vix, today, vix_level)
    # Align VIX3M to the VIX as-of date — the two feeds can lag each other
    # by a session, which would silently mix dates in the term ratio.
    term = None
    term_note = ""
    if vix3m:
        v3_aligned = up_to(vix3m, vix[-1][0])
        if v3_aligned:
            term = vix_level / v3_aligned[-1][1]
            if v3_aligned[-1][0] != vix[-1][0]:
                term_note = f" (VIX3M as of {v3_aligned[-1][0]})"
    event_type = EventType(args.event_type)

    assets = {sym: fetch_asset_series(sym) for sym in CROSS_ASSET_SYMBOLS}
    xa = evaluate_cross_asset(direction, event_type, assets,
                              event_date, today)

    dd = zu = None
    if direction == "CALL":
        # With an explicit event date, measure depth from the pre-event high
        # (a slide longer than 20 sessions rolls the high out of the plain
        # window and understates depth).
        dd = drawdown_from_level(spx, event_date if args.event_date else today,
                                 spot)
        sig = score_entry("CALL", drawdown=dd, vix_level=vix_level,
                          vix_spike_ratio=spike, vix3m_ratio=term,
                          event_type=event_type,
                          cross_asset_count=xa.count,
                          cross_asset_available=xa.available)
    else:
        zu = runup_zscore_from_level(spx, today, spot)
        sig = score_entry("PUT", runup_z=zu, vix_level=vix_level,
                          vix_spike_ratio=spike, vix3m_ratio=term,
                          event_type=event_type,
                          cross_asset_count=xa.count,
                          cross_asset_available=xa.available)

    lo, hi = spot * (1 + STRIKE_OTM_MIN), spot * (1 + STRIKE_OTM_MAX)
    if direction == "PUT":
        lo, hi = spot * (1 - STRIKE_OTM_MAX), spot * (1 - STRIKE_OTM_MIN)
    rec = (f"{sig.tier.value}"
           + (f" — ~${sig.sizing_usd:,.0f} premium, SPX {direction} "
              f"strike {lo:,.0f}-{hi:,.0f}, expiry {TARGET_DTE_MIN}-"
              f"{TARGET_DTE_MAX} DTE (hard min {MIN_DTE})"
              if sig.tier != Tier.SKIP else " — no entry"))

    print(f"\nTACO ENTRY CHECK — {direction} @ SPX {spot:,.0f}  "
          f"[data: {data_source}]")
    print(f"  event: {event_type.value} ({args.rationale}) "
          f"anchored {event_date}")
    depth_label = (f"drawdown {dd:.1f}%" if dd is not None
                   else f"run-up z {zu:.2f}")
    print(f"  {depth_label} | VIX {vix_level:.1f} (spike {spike:.2f}x) | "
          f"term {f'{term:.3f}' if term else 'n/a'}{term_note}")
    if "cross_asset" not in sig.components:
        print(f"  components: {sig.components} (score renormalized ×100/85)")
    else:
        print(f"  components: {sig.components}")
    xa_label = ("display-only (unscored — no put history)"
                if direction == "PUT" else "scored component")
    print(f"  cross-asset confirmation {xa.count}/{xa.available or 0} "
          f"— {xa_label}")
    for r in xa.reads:
        mark = "✓" if r.confirmed else "✗"
        print(f"    {mark} {r.symbol:<4} {r.move_pct:+.1f}% since event "
              f"(threshold {r.threshold:.1f}%, {r.source})")
    for sym in xa.unavailable:
        print(f"    ? {sym:<4} unavailable")
    if xa.available == 0:
        print("  ⚠️  CROSS_ASSET_UNAVAILABLE — scored on 4 components "
              "renormalized")
    print(f"  SCORE {sig.score:.0f} → {rec}")
    for n in sig.notes:
        print(f"  note: {n}")

    log_run(_db_path(), mode="check", direction=direction, spot=spot,
            drawdown_pct=dd, runup_z=zu, vix=vix_level, vix_spike=spike,
            vix_term_ratio=term, event_date=event_date.isoformat(),
            event_type=event_type.value, event_rationale=args.rationale,
            score=sig.score, tier=sig.tier.value, recommendation=rec,
            cross_asset_count=(xa.count if xa.available else None),
            cross_asset_available=xa.available,
            cross_asset_detail=json.dumps({
                "reads": [{"symbol": r.symbol,
                           "move_pct": round(r.move_pct, 2),
                           "threshold": r.threshold,
                           "confirmed": r.confirmed,
                           "source": r.source} for r in xa.reads],
                "unavailable": xa.unavailable}))
    return 0


def cmd_positions(args) -> int:
    today = date.today()
    phase = winddown_phase(today)
    if phase not in (WinddownPhase.CLEAR, WinddownPhase.RELEASED):
        print(f"⚠️  IPO wind-down phase: {phase.value}")

    positions = open_positions(_db_path())
    if not positions:
        print("No open TACO positions.")
        return 0

    spx = fetch_series(SPX_SYMBOL, "1y")
    vix = fetch_series(VIX_SYMBOL, "6mo")
    if not spx or not vix:
        print("ERROR: SPX/VIX download failed — cannot evaluate exits")
        return 1

    # Splice in a live quote for 'today' when the market's open — exits
    # (STOPPED/TAKE_PROFIT) then evaluate against the intraday level instead
    # of waiting for the close, same as entry checks.
    live_spot, live_vix, data_source = _live_quotes()
    spx = with_live_override(spx, today, live_spot)
    vix = with_live_override(vix, today, live_vix)
    print(f"[data: {data_source}]")

    for p in positions:
        refs = EntryRefs(p["direction"], _parse_date(p["event_date"]),
                         p["pre_event_high"], p["panic_low"],
                         p["euphoria_high"], p["rip_base"],
                         p["pre_event_vix"])
        entry = _parse_date(p["entry_date"])
        spx_since = [x for x in spx if x[0] >= entry]
        vix_since = [x for x in vix if x[0] >= entry]
        ev = evaluate_exit(refs, spx_since, vix_since)
        compliant = "" if p["rule_compliant"] else " [rule_compliant=0]"
        liq = (" ⚠️ LIQUIDATE BEFORE IPO FUNDING"
               if phase == WinddownPhase.LIQUIDATE else "")
        print(f"[{p['id']}] {p['direction']} {p['symbol']} "
              f"{p['contracts']:g}x {p['strike']:g} exp {p['expiration']} "
              f"(${p['premium_paid']:,.0f}){compliant}{liq}")
        print(f"     {ev.status.value}: {ev.reason}")
        # Exit-signal forward validation: without this, there is no record of
        # what the engine said and when (audit finding 3).
        log_run(_db_path(), mode="positions", direction=p["direction"],
                spot=spx[-1][1], event_date=p["event_date"],
                recommendation=(f"pos {p['id']} {p['symbol']} "
                                f"{ev.status.value}: {ev.reason}"))
    return 0


def cmd_record(args) -> int:
    entry = _parse_date(args.entry_date)
    event = _parse_date(args.event_date)
    exp = _parse_date(args.expiration)
    dte = (exp - entry).days
    if dte < MIN_DTE:
        print(f"REFUSED: DTE {dte} < hard minimum {MIN_DTE} — "
              f"short-dated index trades are a different, worse strategy")
        return 1
    spx = fetch_series(SPX_SYMBOL, "1y")
    vix = fetch_series(VIX_SYMBOL, "6mo")
    if not spx or not vix:
        print("ERROR: SPX/VIX download failed — cannot compute frozen refs")
        return 1
    refs = compute_refs(args.direction.upper(), spx, vix, event, entry)
    pid = record_position(
        _db_path(), direction=args.direction.upper(), symbol=args.symbol,
        contracts=args.contracts, strike=args.strike,
        expiration=args.expiration, premium_paid=args.premium,
        entry_date=args.entry_date, event_date=args.event_date, refs=refs,
        rule_compliant=0 if args.not_compliant else 1)
    anchor = (f"panic_low {refs.panic_low:,.0f}" if refs.panic_low is not None
              else f"euphoria_high {refs.euphoria_high:,.0f}")
    print(f"Recorded position {pid}: refs frozen — pre_event_high "
          f"{refs.pre_event_high:,.0f}, {anchor}, "
          f"pre_event_vix {refs.pre_event_vix:.1f}")
    return 0


def cmd_close(args) -> int:
    exit_date = args.exit_date or date.today().isoformat()
    pnl = close_position(_db_path(), args.id, exit_date, args.proceeds)
    print(f"Closed position {args.id}: P&L ${pnl:,.0f}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="taco",
                                description="TACO index macro-event engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="score a potential entry now")
    c.add_argument("--direction", required=True, choices=["call", "put"])
    c.add_argument("--event-type", required=True,
                   choices=[e.value for e in EventType])
    c.add_argument("--rationale", required=True,
                   help="one-line why (logged for forward validation)")
    c.add_argument("--event-date",
                   help="YYYY-MM-DD; default = 20d-high date")
    c.set_defaults(fn=cmd_check)

    sub.add_parser("positions",
                   help="evaluate open positions vs exit targets"
                   ).set_defaults(fn=cmd_positions)

    r = sub.add_parser("record", help="record an executed fill")
    r.add_argument("--direction", required=True, choices=["call", "put"])
    r.add_argument("--symbol", required=True, choices=["SPX", "SPY"])
    r.add_argument("--contracts", required=True, type=float)
    r.add_argument("--strike", required=True, type=float)
    r.add_argument("--expiration", required=True, help="YYYY-MM-DD")
    r.add_argument("--premium", required=True, type=float,
                   help="total dollars paid")
    r.add_argument("--entry-date", required=True, help="YYYY-MM-DD")
    r.add_argument("--event-date", required=True, help="YYYY-MM-DD")
    r.add_argument("--not-compliant", action="store_true",
                   help="mark rule_compliant=0 (excluded from validation)")
    r.set_defaults(fn=cmd_record)

    x = sub.add_parser("close", help="close a recorded position")
    x.add_argument("--id", required=True, type=int)
    x.add_argument("--proceeds", required=True, type=float,
                   help="total dollars received")
    x.add_argument("--exit-date", help="YYYY-MM-DD; default today")
    x.set_defaults(fn=cmd_close)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
