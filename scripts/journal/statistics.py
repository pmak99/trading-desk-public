"""Group parsed trades into strategies and compute summary statistics."""

from collections import defaultdict
from typing import Dict, List

from journal.models import Trade, Strategy


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
        strikes = sorted(set(l.strike for l in legs if l.strike))
        n_strikes = len(strikes)

        if n_strikes <= 1:
            # Multiple fills of the same single — one strategy per strike group
            by_strike: Dict = defaultdict(list)
            for l in legs:
                by_strike[l.strike].append(l)
            for strike_legs in by_strike.values():
                pnl = sum(l.gain_loss for l in strike_legs)
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
            pnl = sum(l.gain_loss for l in legs)
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
            pnl = sum(l.gain_loss for l in legs)
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
    """Calculate statistics at the strategy level (spreads counted as one trade)."""
    total = len(strategies)
    if total == 0:
        return {}

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

    return {
        'total_trades': total,
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(100 * len(winners) / total, 1) if total > 0 else 0,
        'total_pnl': round(total_pnl, 2),
        'winner_pnl': round(winner_pnl, 2),
        'loser_pnl': round(loser_pnl, 2),
        'avg_win': round(winner_pnl / len(winners), 2) if winners else 0,
        'avg_loss': round(loser_pnl / len(losers), 2) if losers else 0,
        'profit_factor': round(abs(winner_pnl / loser_pnl), 2) if loser_pnl else 0,
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
        'wash_sales': {'count': 0, 'total': 0.0},
    }


def calculate_statistics(trades: List[Trade]) -> Dict:
    """Calculate comprehensive trading statistics"""

    total = len(trades)
    if total == 0:
        return {}

    winners = [t for t in trades if t.is_winner]
    losers = [t for t in trades if not t.is_winner]
    options = [t for t in trades if t.is_option]
    stocks = [t for t in trades if not t.is_option]

    total_pnl = sum(t.gain_loss for t in trades)
    winner_pnl = sum(t.gain_loss for t in winners)
    loser_pnl = sum(t.gain_loss for t in losers)

    # By ticker
    by_ticker = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        by_ticker[t.symbol]['count'] += 1
        by_ticker[t.symbol]['pnl'] += t.gain_loss
        if t.is_winner:
            by_ticker[t.symbol]['wins'] += 1

    # By month — use the CLOSE date for bucketing.
    # For credit trades (sell-to-open), Fidelity inverts the dates:
    # "Date Acquired" = when the short was covered (close), "Date Sold" = when opened.
    # So for credit trades where acquired_date > sale_date, use acquired_date.
    by_month = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        close_date = t.sale_date
        if t.acquired_date and t.sale_date and t.acquired_date > t.sale_date:
            close_date = t.acquired_date  # credit trade: close = acquired_date
        if close_date and len(close_date) >= 7:
            month = close_date[:7]
        else:
            month = 'UNKNOWN'
        by_month[month]['count'] += 1
        by_month[month]['pnl'] += t.gain_loss
        if t.is_winner:
            by_month[month]['wins'] += 1

    # By option type
    by_option_type = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in options:
        otype = t.option_type or 'UNKNOWN'
        by_option_type[otype]['count'] += 1
        by_option_type[otype]['pnl'] += t.gain_loss
        if t.is_winner:
            by_option_type[otype]['wins'] += 1

    # Earnings correlation stats
    earnings_trades = [t for t in trades if t.earnings_date]

    return {
        'total_trades': total,
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(100 * len(winners) / total, 1) if total > 0 else 0,
        'total_pnl': round(total_pnl, 2),
        'winner_pnl': round(winner_pnl, 2),
        'loser_pnl': round(loser_pnl, 2),
        'avg_win': round(winner_pnl / len(winners), 2) if winners else 0,
        'avg_loss': round(loser_pnl / len(losers), 2) if losers else 0,
        'profit_factor': round(abs(winner_pnl / loser_pnl), 2) if loser_pnl else 0,
        'options_count': len(options),
        'stocks_count': len(stocks),
        'by_ticker': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0
        } for k, v in sorted(by_ticker.items(), key=lambda x: x[1]['pnl'], reverse=True)},
        'by_month': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0
        } for k, v in sorted(by_month.items())},
        'by_option_type': dict(by_option_type),
        'earnings_correlated': len(earnings_trades),
        'wash_sales': {
            'count': len([t for t in trades if t.wash_sale_amount != 0]),
            'total': round(sum(t.wash_sale_amount for t in trades), 2),
        }
    }
