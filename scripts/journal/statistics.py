"""Group parsed trades into strategies and compute summary statistics."""

from collections import defaultdict
from typing import Dict, List

from journal.models import Strategy, Trade


def group_trades_into_strategies(trades: List[Trade]) -> List[Strategy]:
    """
    Group individual trade legs into strategies by (symbol, expiration, option_type).

    One distinct strike → SINGLE. Two → paired SPREADs. Three or more → single
    SPREAD grouping all legs (avoids silently dropping middle-strike legs).

    Note: iron condors cannot be detected here — grouping by option_type means
    PUT and CALL legs always land in separate groups. They appear as two SPREADs.
    For in-memory reporting only; do not persist Strategy objects to the DB.
    """
    options = [t for t in trades if t.is_option]
    stocks = [t for t in trades if not t.is_option]

    groups: Dict[tuple, List[Trade]] = defaultdict(list)
    for t in options:
        key = (t.symbol, t.expiration or '', t.option_type or '')
        groups[key].append(t)

    strategies: List[Strategy] = []

    for (symbol, expiration, option_type), legs in groups.items():
        strikes = sorted({leg.strike for leg in legs if leg.strike})
        n_strikes = len(strikes)

        if n_strikes <= 1:
            # Multiple fills of the same single — one strategy per strike group
            by_strike: Dict = defaultdict(list)
            for leg in legs:
                by_strike[leg.strike].append(leg)
            for strike_legs in by_strike.values():
                pnl = sum(leg.gain_loss for leg in strike_legs)
                strategies.append(Strategy(
                    symbol=symbol, strategy_type='SINGLE',
                    option_type=option_type or None,
                    expiration=expiration or None, gain_loss=pnl,
                    legs=strike_legs,
                    earnings_date=strike_legs[0].earnings_date,
                    actual_move=strike_legs[0].actual_move,
                ))
        elif n_strikes == 2:
            # SPREAD: all fills at both strikes consolidated into one strategy.
            # Fidelity often splits a spread into N lot-pairs; consolidating keeps
            # one strategy per position regardless of fill count.
            pnl = sum(leg.gain_loss for leg in legs)
            strategies.append(Strategy(
                symbol=symbol, strategy_type='SPREAD',
                option_type=option_type or None,
                expiration=expiration or None, gain_loss=pnl,
                legs=legs,
                earnings_date=legs[0].earnings_date,
                actual_move=legs[0].actual_move,
            ))
        else:
            # 3+ distinct strikes (partial condor, broken spread, etc.) —
            # group all legs together to avoid silently dropping middle strikes
            pnl = sum(leg.gain_loss for leg in legs)
            strategies.append(Strategy(
                symbol=symbol, strategy_type='SPREAD',
                option_type=option_type or None,
                expiration=expiration or None, gain_loss=pnl,
                legs=legs,
                earnings_date=legs[0].earnings_date,
                actual_move=legs[0].actual_move,
            ))

    for t in stocks:
        strategies.append(Strategy(
            symbol=t.symbol, strategy_type='STOCK',
            option_type=None, expiration=None,
            gain_loss=t.gain_loss, legs=[t],
            earnings_date=t.earnings_date, actual_move=t.actual_move,
        ))

    return strategies


def calculate_strategy_statistics(strategies: List[Strategy]) -> Dict:
    """Calculate statistics at the strategy level (spreads counted as one trade).

    A ratio with nothing to divide by is None, printed "n/a": win rate with no
    trades, average win with no winner, average loss with no loser, and profit
    factor when nothing lost money — all winners, breakevens or no trades —
    because winners over a $0 loss has no value to print.
    """
    total = len(strategies)
    winners = [s for s in strategies if s.is_winner]
    losers = [s for s in strategies if not s.is_winner]
    options = [s for s in strategies if s.strategy_type != 'STOCK']
    stocks = [s for s in strategies if s.strategy_type == 'STOCK']

    total_pnl = sum(s.gain_loss for s in strategies)
    winner_pnl = sum(s.gain_loss for s in winners)
    loser_pnl = sum(s.gain_loss for s in losers)

    by_ticker: Dict = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'wins': 0})
    for s in strategies:
        by_ticker[s.symbol]['count'] += 1
        by_ticker[s.symbol]['pnl'] += s.gain_loss
        if s.is_winner:
            by_ticker[s.symbol]['wins'] += 1

    by_month: Dict = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'wins': 0})
    for s in strategies:
        cd = s.close_date or ''
        month = cd[:7] if len(cd) >= 7 else 'UNKNOWN'
        by_month[month]['count'] += 1
        by_month[month]['pnl'] += s.gain_loss
        if s.is_winner:
            by_month[month]['wins'] += 1

    by_option_type: Dict = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'wins': 0})
    for s in options:
        otype = s.option_type or 'UNKNOWN'
        by_option_type[otype]['count'] += 1
        by_option_type[otype]['pnl'] += s.gain_loss
        if s.is_winner:
            by_option_type[otype]['wins'] += 1

    by_strategy_type: Dict = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'wins': 0})
    for s in strategies:
        by_strategy_type[s.strategy_type]['count'] += 1
        by_strategy_type[s.strategy_type]['pnl'] += s.gain_loss
        if s.is_winner:
            by_strategy_type[s.strategy_type]['wins'] += 1

    earnings_strategies = [s for s in strategies if s.earnings_date]
    # Per lot, as Fidelity lists them: a strategy's wash sales are its legs'.
    legs = [leg for s in strategies for leg in s.legs]

    return {
        'total_trades': total,
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(100 * len(winners) / total, 1) if total else None,
        'total_pnl': round(total_pnl, 2),
        'winner_pnl': round(winner_pnl, 2),
        'loser_pnl': round(loser_pnl, 2),
        'avg_win': round(winner_pnl / len(winners), 2) if winners else None,
        'avg_loss': round(loser_pnl / len(losers), 2) if losers else None,
        'profit_factor': round(abs(winner_pnl / loser_pnl), 2) if round(loser_pnl, 2) else None,
        'options_count': len(options),
        'stocks_count': len(stocks),
        'by_ticker': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0,
        } for k, v in sorted(by_ticker.items(), key=lambda x: x[1]['pnl'], reverse=True)},
        'by_month': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0,
        } for k, v in sorted(by_month.items())},
        'by_option_type': {k: dict(v) for k, v in by_option_type.items()},
        'by_strategy_type': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0,
        } for k, v in sorted(by_strategy_type.items())},
        'earnings_correlated': len(earnings_strategies),
        'wash_sales': {
            'count': sum(1 for leg in legs if leg.wash_sale_amount),
            'total': round(sum(leg.wash_sale_amount for leg in legs), 2),
        },
    }

