#!/usr/bin/env python3
"""
Make trade_journal and strategies agree with Fidelity's Realized Gain/Loss exports.

The exports are the broker's own record of every closed lot. On 2026-09-13 the
journal disagreed with them on 55 contracts: trades imported twice (once from
the CSV and once from the PDF statement parser, with settlement dates and
proceeds = |gain|), fills dropped by the dedup key, calls imported as puts, a
spread's other side never imported, lots imported twice by overlapping exports,
and every wash-sale adjustment discarded. This script rebuilds exactly the
contracts that disagree and leaves everything else alone.

How it decides
--------------
* A contract is (account, symbol, option type, strike, expiration); a stock is
  (account, symbol). Legs and lots are compared only inside each account's
  scope: the exports' years, up to the last row they contain.
* A contract whose legs already equal its lots is untouched. Otherwise legs
  that equal a lot are kept (ids, earnings data and all), the rest are deleted,
  and lots without a leg are inserted.
* A lot goes to the strategy that already holds its contract -- preferring one
  whose dates contain the lot, which is how a copy with shifted dates loses.
  A contract nobody holds (a missing spread side, a call stored as a put) goes
  to the strategy in the same account, symbol and expiration whose rebuilt
  dates contain it; if there is none it is inserted unlinked and reported.
* A strategy left with no legs is deleted. Every strategy touched is recomputed
  from its legs (P&L, winner, quantity, dates, and type when its set of legs
  changed), and every strategy's P&L and quantity are checked against its legs.

Lot P&L is cash: the CSV reader folds each "Wash Sale" row into its lot.

Report-only unless --execute. Take a backup first:
    pg_dump -Fc -t strategies -t trade_journal -f pre-reconcile.dump

Usage:
    2.0/venv/bin/python scripts/reconcile_journal.py \\
        --export ~/Desktop/taxable_2025.csv:TAXABLE:2025 \\
        --export ~/Desktop/taxable_2026.csv:TAXABLE:2026 \\
        --export ~/Desktop/ira_2026.csv:IRA:2026            # report
    ... --execute                                           # write
"""

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db import connect, target  # noqa: E402
from scripts.strategy_grouper import quantity_from_legs  # noqa: E402

# A lot that closed this close to the start of an export's year can sit in
# either year's export (Fidelity files a 12-31 close under the next tax year).
YEAR_BOUNDARY_DAYS = 7


@dataclass(frozen=True)
class Lot:
    account: str
    symbol: str
    option_type: Optional[str]
    strike: Optional[float]
    expiration: Optional[date]
    acquired: date
    sale: date
    quantity: float
    cost: float
    proceeds: float
    gain_loss: float
    wash: float
    term: Optional[str]


@dataclass(frozen=True)
class Leg:
    id: int
    strategy_id: Optional[int]
    account: str
    symbol: str
    option_type: Optional[str]
    strike: Optional[float]
    expiration: Optional[date]
    acquired: date
    sale: date
    quantity: float
    cost: float
    proceeds: float
    gain_loss: float
    wash: float
    earnings_date: Optional[date]
    actual_move: Optional[float]


@dataclass(frozen=True)
class Strategy:
    id: int
    account: str
    symbol: str
    strategy_type: str
    acquired: date
    sale: date
    quantity: Optional[float]
    gain_loss: float
    parent_strategy_id: Optional[int]


@dataclass
class StrategyUpdate:
    strategy_type: str
    acquired: date
    sale: date
    quantity: float
    gain_loss: float
    is_winner: bool


@dataclass
class Plan:
    delete_leg_ids: List[int] = field(default_factory=list)
    insert: List[Tuple[Lot, Optional[int]]] = field(default_factory=list)
    delete_strategy_ids: List[int] = field(default_factory=list)
    reparent: Dict[int, Optional[int]] = field(default_factory=dict)
    strategy_updates: Dict[int, StrategyUpdate] = field(default_factory=dict)
    earnings: Dict[int, Tuple[Optional[date], Optional[float]]] = field(default_factory=dict)
    contracts: List[dict] = field(default_factory=list)
    unlinked: List[Lot] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.delete_leg_ids or self.insert or self.delete_strategy_ids
                    or self.reparent or self.strategy_updates)


# --------------------------------------------------------------------------- helpers

def _close(d1: Optional[date], d2: Optional[date]) -> date:
    return max(d for d in (d1, d2) if d)


def _key(x) -> tuple:
    if x.option_type is None:
        return (x.account, x.symbol, None, None, None)
    return (x.account, x.symbol, x.option_type, round(float(x.strike), 3), x.expiration)


def _money(v: float) -> float:
    return round(float(v), 2)


def _fingerprint(x) -> tuple:
    return (x.acquired, x.sale, round(float(x.quantity), 6), _money(x.cost), _money(x.proceeds),
            _money(x.gain_loss), _money(x.wash))


def _dedup_key(x) -> tuple:
    """trade_journal_dedup's columns (plus expiration, which it should have had)."""
    return (x.account, x.symbol, x.option_type, x.strike, x.expiration, x.acquired, x.sale, _money(x.cost))


def _combine(a, b) -> Lot:
    return Lot(account=a.account, symbol=a.symbol, option_type=a.option_type, strike=a.strike,
               expiration=a.expiration, acquired=a.acquired, sale=a.sale,
               quantity=a.quantity + b.quantity, cost=_money(a.cost + b.cost),
               proceeds=_money(a.proceeds + b.proceeds), gain_loss=_money(a.gain_loss + b.gain_loss),
               wash=_money(a.wash + b.wash), term=getattr(a, "term", None) or getattr(b, "term", None))


def _merge_collisions(lots: Iterable[Lot]) -> List[Lot]:
    """Lots that would collide on trade_journal_dedup become one leg, as the importer does.

    Repeated until stable: a merge changes the cost, and the new cost can equal
    another lot's (FIG 55C: two 1-lots at $17.02 make $34.04, a real 2-lot's cost).
    """
    items = sorted(lots, key=lambda x: (str(_dedup_key(x)), x.quantity, x.proceeds))
    while True:
        out: Dict[tuple, Lot] = {}
        merged = False
        for lt in items:
            k = _dedup_key(lt)
            if k in out:
                out[k] = _combine(out[k], lt)
                merged = True
            else:
                out[k] = lt
        items = list(out.values())
        if not merged:
            return items


def _in_scope(close: date, account: str, scope, with_lots: bool) -> bool:
    if account not in scope:
        return False
    start, end = scope[account]
    lo = start - timedelta(days=YEAR_BOUNDARY_DAYS) if with_lots else start
    return lo <= close <= end


def _contains(window: Tuple[date, date], x) -> bool:
    lo, hi = min(x.acquired, x.sale), max(x.acquired, x.sale)
    return window[0] <= lo and hi <= window[1]


def _window(items) -> Tuple[date, date]:
    dates = [d for x in items for d in (x.acquired, x.sale) if d]
    return (min(dates), max(dates))


def _classify(legs) -> Optional[str]:
    if all(x.option_type is None for x in legs):
        return "STOCK"
    distinct = {(x.strike, x.option_type) for x in legs if x.option_type is not None}
    return {1: "SINGLE", 4: "IRON_CONDOR"}.get(len(distinct), "SPREAD")


# --------------------------------------------------------------------------- planning

def build_plan(lots: List[Lot], legs: List[Leg], strategies: List[Strategy],
               scope: Dict[str, Tuple[date, date]]) -> Plan:
    plan = Plan()
    by_id = {s.id: s for s in strategies}
    lots = _merge_collisions(lots)

    lots_by_key: Dict[tuple, List[Lot]] = defaultdict(list)
    for lt in lots:
        lots_by_key[_key(lt)].append(lt)

    legs_by_key: Dict[tuple, List[Leg]] = defaultdict(list)
    for lg in legs:
        k = _key(lg)
        if _in_scope(_close(lg.acquired, lg.sale), lg.account, scope, with_lots=k in lots_by_key):
            legs_by_key[k].append(lg)

    final: Dict[int, List] = defaultdict(list)          # strategy -> legs/lots it ends with
    for lg in legs:
        if lg.strategy_id is not None:
            final[lg.strategy_id].append(lg)
    original_final = {sid: list(v) for sid, v in final.items()}

    deleted: set = set()
    pending_new: List[Tuple[tuple, Lot]] = []

    def drop(lg: Leg):
        deleted.add(lg.id)
        if lg.strategy_id is not None:
            final[lg.strategy_id] = [x for x in final[lg.strategy_id] if not (isinstance(x, Leg) and x.id == lg.id)]

    for k in sorted(set(lots_by_key) | set(legs_by_key), key=lambda k: tuple(str(p) for p in k)):
        klots, klegs = lots_by_key.get(k, []), legs_by_key.get(k, [])
        if sorted(map(_fingerprint, klots)) == sorted(map(_fingerprint, klegs)):
            continue
        owners = sorted({lg.strategy_id for lg in klegs if lg.strategy_id is not None})
        change = {"key": k, "legs_before": len(klegs), "lots": len(klots),
                  "pnl_before": _money(sum(x.gain_loss for x in klegs)),
                  "pnl_after": _money(sum(x.gain_loss for x in klots)),
                  "qty_before": sum(x.quantity for x in klegs), "qty_after": sum(x.quantity for x in klots),
                  "strategies_before": owners}

        prints = {_fingerprint(x) for x in klots}

        def score(sid, klegs=klegs, prints=prints):
            return sum(1 for lg in klegs if lg.strategy_id == sid and _fingerprint(lg) in prints)

        unmatched = list(klegs)
        pending = []
        contract_inserts: List[Tuple[Lot, int]] = []
        for lt in klots:                                   # exact twins first, in any strategy
            twin = next((lg for lg in unmatched if _fingerprint(lg) == _fingerprint(lt)), None)
            if twin is not None:
                unmatched.remove(twin)
            else:
                pending.append(lt)
        for lt in pending:
            # The lot's old leg -- same dates, near-identical money and size --
            # decides where it goes, so a corrected lot never wanders into a
            # neighbouring position whose window happens to contain it.
            old = next((lg for lg in unmatched if lg.strategy_id is not None
                        and (lg.acquired, lg.sale) == (lt.acquired, lt.sale)
                        and abs(lg.proceeds - lt.proceeds) <= max(1.0, 0.01 * abs(lt.proceeds))
                        and abs(lg.quantity - lt.quantity) <= 1.0), None)
            sid = old.strategy_id if old is not None else None
            if sid is None and owners:
                containing = [s for s in owners if _contains((by_id[s].acquired, by_id[s].sale), lt)]
                pool = containing or owners
                sid = sorted(pool, key=lambda s: (-score(s), s))[0]
            if sid is None:
                pending_new.append((k, lt))
                continue
            contract_inserts.append((lt, sid))
        for lg in unmatched:
            drop(lg)

        # An insert must not share a dedup key with a leg being kept: merge them
        # (the kept leg is replaced), and repeat, since merging changes the cost.
        kept = [lg for lg in klegs if lg.id not in deleted]
        while True:
            by_dkey: Dict[tuple, list] = defaultdict(list)
            for lg in kept:
                by_dkey[_dedup_key(lg)].append(("leg", lg))
            for i, (lt, _sid) in enumerate(contract_inserts):
                by_dkey[_dedup_key(lt)].append(("lot", i))
            clash = next((v for v in by_dkey.values() if len(v) > 1), None)
            if clash is None:
                break
            legs_in = [x for kind, x in clash if kind == "leg"]
            lots_in = [contract_inserts[i] for kind, i in clash if kind == "lot"]
            sid = legs_in[0].strategy_id if legs_in else lots_in[0][1]
            # A clash always includes a lot: the constraint already keeps two legs apart.
            parts = list(legs_in) + [lt for lt, _ in lots_in]
            combined = parts[0]
            for p in parts[1:]:
                combined = _combine(combined, p)
            for lg in legs_in:
                drop(lg)
                kept.remove(lg)
            drop_idx = {i for kind, i in clash if kind == "lot"}
            contract_inserts = [x for i, x in enumerate(contract_inserts) if i not in drop_idx]
            contract_inserts.append((combined, sid))

        for lt, sid in contract_inserts:
            plan.insert.append((lt, sid))
            final[sid].append(lt)
        plan.contracts.append(change)

    # Contracts nobody held: join the position with the same account, symbol and
    # expiration whose rebuilt dates contain them.
    for _key_unused, lt in pending_new:
        candidates = []
        for sid, s in by_id.items():
            if (s.account, s.symbol) != (lt.account, lt.symbol):
                continue
            held = original_final.get(sid, [])
            if lt.option_type is not None and not any(x.expiration == lt.expiration for x in held):
                continue
            if lt.option_type is None and s.strategy_type != "STOCK":
                continue
            items = final.get(sid) or held
            if items and _contains(_window(items), lt):
                candidates.append(sid)
        if len(candidates) == 1:
            plan.insert.append((lt, candidates[0]))
            final[candidates[0]].append(lt)
        else:
            plan.insert.append((lt, None))
            plan.unlinked.append(lt)

    plan.delete_leg_ids = sorted(deleted)
    touched = {sid for _, sid in plan.insert if sid is not None} | {
        lg.strategy_id for lg in legs if lg.id in deleted and lg.strategy_id is not None}

    receivers: Dict[int, Optional[int]] = {}
    for sid in sorted(touched):
        if not final.get(sid):
            plan.delete_strategy_ids.append(sid)
            gained = [s for lt, s in plan.insert if s is not None and s != sid
                      and any(_key(lt) == _key(lg) for lg in original_final.get(sid, []))]
            receivers[sid] = gained[0] if gained else None
    for s in strategies:
        if s.parent_strategy_id in receivers and s.id not in plan.delete_strategy_ids:
            new_parent = receivers[s.parent_strategy_id]
            plan.reparent[s.id] = new_parent if new_parent != s.id else None

    for s in strategies:
        if s.id in plan.delete_strategy_ids or not final.get(s.id):
            continue
        items = final[s.id]
        pnl = _money(sum(x.gain_loss for x in items))
        qty = quantity_from_legs([(x.strike, x.quantity) for x in items])
        if s.id in touched:
            before = {(x.strike, x.option_type) for x in original_final.get(s.id, [])}
            after = {(x.strike, x.option_type) for x in items}
            stype = s.strategy_type if before == after else (_classify(items) or s.strategy_type)
            acquired, sale = _window(items)
        else:
            stype, acquired, sale = s.strategy_type, s.acquired, s.sale
        if _classify(items) == "STOCK":
            stype = "STOCK"                                # share lots are never a spread
        upd = StrategyUpdate(stype, acquired, sale, qty, pnl, pnl > 0)
        if (stype, acquired, sale) != (s.strategy_type, s.acquired, s.sale) \
                or abs(pnl - float(s.gain_loss)) > 0.005 \
                or s.quantity is None or abs(float(qty) - float(s.quantity)) > 1e-6:
            plan.strategy_updates[s.id] = upd

    for sid in {s for _, s in plan.insert if s is not None}:
        known = [lg for lg in original_final.get(sid, []) if lg.earnings_date is not None]
        plan.earnings[sid] = (known[0].earnings_date, known[0].actual_move) if known else (None, None)
    return plan


# --------------------------------------------------------------------------- I/O

def load_journal(conn) -> Tuple[List[Leg], List[Strategy]]:
    legs = [Leg(id=r.id, strategy_id=r.strategy_id, account=r.account_type, symbol=r.symbol,
                option_type=r.option_type, strike=float(r.strike) if r.strike is not None else None,
                expiration=r.expiration, acquired=r.acquired_date or r.sale_date, sale=r.sale_date,
                quantity=float(r.quantity or 0), cost=float(r.cost_basis), proceeds=float(r.proceeds),
                gain_loss=float(r.gain_loss), wash=float(r.wash_sale_amount or 0),
                earnings_date=r.earnings_date, actual_move=r.actual_move)
            for r in conn.execute(
                "SELECT id, strategy_id, account_type, symbol, option_type, strike, expiration,"
                " acquired_date, sale_date, quantity, cost_basis, proceeds, gain_loss,"
                " wash_sale_amount, earnings_date, actual_move FROM trade_journal ORDER BY id")]
    strategies = [Strategy(id=r.id, account=r.account_type, symbol=r.symbol, strategy_type=r.strategy_type,
                           acquired=r.acquired_date, sale=r.sale_date,
                           quantity=float(r.quantity) if r.quantity is not None else None,
                           gain_loss=float(r.gain_loss), parent_strategy_id=r.parent_strategy_id)
                  for r in conn.execute(
                      "SELECT id, account_type, symbol, strategy_type, acquired_date, sale_date,"
                      " quantity, gain_loss, parent_strategy_id FROM strategies ORDER BY id")]
    return legs, strategies


def load_exports(exports: List[Tuple[str, str, int]]) -> Tuple[List[Lot], Dict[str, Tuple[date, date]]]:
    from journal.csv_reader import parse_fidelity_csv

    lots: List[Lot] = []
    years: Dict[str, set] = defaultdict(set)
    last_close: Dict[str, date] = {}
    for path, account, year in exports:
        trades, _, unable = parse_fidelity_csv(path)
        if unable:
            raise SystemExit(f"{path}: Fidelity could not provide gain/loss for {len(unable)} position(s); "
                             "re-export before reconciling")
        years[account].add(year)
        for t in trades:
            sale = date.fromisoformat(t.sale_date)
            acquired = date.fromisoformat(t.acquired_date) if t.acquired_date else sale
            lots.append(Lot(account=account, symbol=t.symbol, option_type=t.option_type,
                            strike=float(t.strike) if t.strike is not None else None,
                            expiration=date.fromisoformat(t.expiration) if t.expiration else None,
                            acquired=acquired, sale=sale, quantity=float(t.quantity),
                            cost=_money(t.cost_basis), proceeds=_money(t.proceeds),
                            gain_loss=_money(t.gain_loss), wash=_money(t.wash_sale_amount), term=t.term))
            close = _close(acquired, sale)
            last_close[account] = max(last_close.get(account, close), close)
    scope = {}
    for account, ys in years.items():
        start = date(min(ys), 1, 1)
        end = min(date(max(ys), 12, 31), last_close.get(account, date(max(ys), 12, 31)))
        scope[account] = (start, end)
    return lots, scope


def apply_plan(conn, plan: Plan) -> None:
    """Write the plan in one transaction, then prove each touched strategy equals its legs."""
    with conn.transaction():
        if plan.delete_leg_ids:
            conn.execute("DELETE FROM trade_journal WHERE id = ANY(%s)", (plan.delete_leg_ids,))
        for lt, sid in plan.insert:
            earnings_date, actual_move = plan.earnings.get(sid, (None, None)) if sid is not None else (None, None)
            conn.execute(
                """INSERT INTO trade_journal
                   (symbol, acquired_date, sale_date, days_held, option_type, strike, expiration, quantity,
                    cost_basis, proceeds, gain_loss, is_winner, term, wash_sale_amount, earnings_date,
                    actual_move, strategy_id, account_type)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (lt.symbol, lt.acquired, lt.sale, (lt.sale - lt.acquired).days, lt.option_type, lt.strike,
                 lt.expiration, lt.quantity, lt.cost, lt.proceeds, lt.gain_loss, lt.gain_loss > 0,
                 lt.term, lt.wash, earnings_date, actual_move, sid, lt.account))
        for child, parent in plan.reparent.items():
            conn.execute("UPDATE strategies SET parent_strategy_id = %s WHERE id = %s", (parent, child))
        if plan.delete_strategy_ids:
            conn.execute("DELETE FROM strategies WHERE id = ANY(%s)", (plan.delete_strategy_ids,))
        for sid, u in plan.strategy_updates.items():
            conn.execute(
                """UPDATE strategies SET strategy_type = %s, acquired_date = %s, sale_date = %s,
                          days_held = %s, quantity = %s, gain_loss = %s, is_winner = %s WHERE id = %s""",
                (u.strategy_type, u.acquired, u.sale, (u.sale - u.acquired).days, u.quantity,
                 u.gain_loss, u.is_winner, sid))
        ids = sorted(set(plan.strategy_updates) | {s for _, s in plan.insert if s is not None})
        if ids:
            bad = conn.execute(
                """SELECT s.id FROM strategies s JOIN trade_journal j ON j.strategy_id = s.id
                   WHERE s.id = ANY(%s) GROUP BY s.id, s.gain_loss
                   HAVING abs(s.gain_loss - sum(j.gain_loss)) > 0.005""", (ids,)).fetchall()
            if bad:
                raise RuntimeError(f"strategies no longer equal their legs: {[r.id for r in bad]}")


def print_report(plan: Plan, strategies: List[Strategy], lots: List[Lot], legs: List[Leg], scope) -> None:
    by_id = {s.id: s for s in strategies}
    print(f"Database: {target()}")
    for account, (start, end) in sorted(scope.items()):
        in_scope = [x for x in legs if _in_scope(_close(x.acquired, x.sale), x.account, {account: (start, end)}, True)
                    and x.account == account]
        exp = [x for x in lots if x.account == account]
        print(f"  {account}: {start} .. {end}   export P&L {sum(x.gain_loss for x in exp):,.2f}   "
              f"journal P&L in scope {sum(x.gain_loss for x in in_scope):,.2f}")
    print(f"\nContracts rebuilt: {len(plan.contracts)}   legs deleted: {len(plan.delete_leg_ids)}   "
          f"lots inserted: {len(plan.insert)}   unlinked: {len(plan.unlinked)}")
    for c in plan.contracts:
        acct, sym, typ, strike, exp = c["key"]
        label = f"{acct:7} {sym:5} {typ or 'STOCK':4} {strike if strike is not None else '':>8} {exp or ''}"
        print(f"  {label}  legs {c['legs_before']}->{c['lots']}  qty {c['qty_before']:g}->{c['qty_after']:g}  "
              f"P&L {c['pnl_before']:,.2f}->{c['pnl_after']:,.2f}  strategies {c['strategies_before']}")
    if plan.delete_strategy_ids:
        print(f"\nStrategies deleted (no legs left): {plan.delete_strategy_ids}")
    if plan.reparent:
        print(f"Children re-pointed: {plan.reparent}")
    if plan.strategy_updates:
        print(f"\nStrategies updated: {len(plan.strategy_updates)}")
        for sid, u in sorted(plan.strategy_updates.items()):
            s = by_id[sid]
            print(f"  id={sid:<4} {s.symbol:5} {s.strategy_type}->{u.strategy_type}  "
                  f"{s.acquired}..{s.sale} -> {u.acquired}..{u.sale}  qty {s.quantity}->{u.quantity:g}  "
                  f"P&L {s.gain_loss:,.2f}->{u.gain_loss:,.2f}")
    for lt in plan.unlinked:
        print(f"  UNLINKED lot: {lt}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Reconcile the journal against Fidelity Realized Gain/Loss exports")
    parser.add_argument("--export", action="append", required=True, metavar="PATH:ACCOUNT:YEAR",
                        help="a Realized Gain/Loss CSV, its account (TAXABLE or IRA) and tax year; repeatable")
    parser.add_argument("--execute", action="store_true", help="write the changes (default: report only)")
    args = parser.parse_args()

    exports = []
    for spec in args.export:
        path, account, year = spec.rsplit(":", 2)
        if account not in ("TAXABLE", "IRA"):
            parser.error(f"account must be TAXABLE or IRA: {spec}")
        exports.append((str(Path(path).expanduser()), account, int(year)))

    lots, scope = load_exports(exports)
    with connect() as conn:
        legs, strategies = load_journal(conn)
        plan = build_plan(lots, legs, strategies, scope)
        print_report(plan, strategies, lots, legs, scope)
        if not args.execute:
            print("\nREPORT ONLY -- pass --execute to write (take a pg_dump first)")
            return 0
        if plan.is_empty():
            print("\nNothing to write.")
            return 0
        apply_plan(conn, plan)
        print("\nWritten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
