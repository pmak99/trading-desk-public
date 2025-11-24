"""
Secure JSON serialization for domain objects.

Replaces pickle to avoid arbitrary code execution vulnerabilities.
Supports all domain types: Money, Percentage, Strike, OptionChain, etc.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.domain.types import (
    Money, Percentage, Strike, OptionQuote, OptionChain,
    ImpliedMove, HistoricalMove, VRPResult, ConsistencyResult,
    SkewResult, TermStructureResult, TickerAnalysis,
    StrategyLeg, Strategy, StrategyRecommendation
)
from src.domain.enums import (
    EarningsTiming, OptionType, Recommendation, StrategyType,
    DirectionalBias
)


class DomainJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles domain value objects."""

    def default(self, obj):
        """Convert domain objects to JSON-serializable dicts."""

        # Handle datetime/date
        if isinstance(obj, datetime):
            return {'__type__': 'datetime', 'value': obj.isoformat()}
        if isinstance(obj, date):
            return {'__type__': 'date', 'value': obj.isoformat()}

        # Handle Decimal
        if isinstance(obj, Decimal):
            return {'__type__': 'Decimal', 'value': str(obj)}

        # Handle value objects
        if isinstance(obj, Money):
            return {'__type__': 'Money', 'amount': str(obj.amount)}
        if isinstance(obj, Percentage):
            return {'__type__': 'Percentage', 'value': obj.value}
        if isinstance(obj, Strike):
            return {'__type__': 'Strike', 'price': str(obj.price)}

        # Handle OptionQuote
        if isinstance(obj, OptionQuote):
            return {
                '__type__': 'OptionQuote',
                'bid': obj.bid,
                'ask': obj.ask,
                'implied_volatility': obj.implied_volatility,
                'open_interest': obj.open_interest,
                'volume': obj.volume,
                'delta': obj.delta,
                'gamma': obj.gamma,
                'theta': obj.theta,
                'vega': obj.vega,
            }

        # Handle OptionChain
        if isinstance(obj, OptionChain):
            # Convert strike keys to strings for JSON
            calls_dict = {str(k.price): v for k, v in obj.calls.items()}
            puts_dict = {str(k.price): v for k, v in obj.puts.items()}
            return {
                '__type__': 'OptionChain',
                'ticker': obj.ticker,
                'expiration': obj.expiration,
                'stock_price': obj.stock_price,
                'calls': calls_dict,
                'puts': puts_dict,
            }

        # Handle analysis results
        if isinstance(obj, ImpliedMove):
            return {
                '__type__': 'ImpliedMove',
                'ticker': obj.ticker,
                'expiration': obj.expiration,
                'stock_price': obj.stock_price,
                'atm_strike': obj.atm_strike,
                'straddle_cost': obj.straddle_cost,
                'implied_move_pct': obj.implied_move_pct,
                'upper_bound': obj.upper_bound,
                'lower_bound': obj.lower_bound,
                'call_iv': obj.call_iv,
                'put_iv': obj.put_iv,
                'avg_iv': obj.avg_iv,
            }

        if isinstance(obj, VRPResult):
            return {
                '__type__': 'VRPResult',
                'ticker': obj.ticker,
                'expiration': obj.expiration,
                'implied_move_pct': obj.implied_move_pct,
                'historical_mean_move_pct': obj.historical_mean_move_pct,
                'vrp_ratio': obj.vrp_ratio,
                'edge_score': obj.edge_score,
                'recommendation': obj.recommendation.value,
            }

        # Handle enums
        if isinstance(obj, (EarningsTiming, OptionType, Recommendation,
                          StrategyType, DirectionalBias)):
            return {'__type__': obj.__class__.__name__, 'value': obj.value}

        # Handle lists/dicts
        if isinstance(obj, dict):
            return {k: self.default(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.default(item) for item in obj]

        return super().default(obj)


def domain_object_hook(dct: Dict[str, Any]) -> Any:
    """Decode JSON dicts back to domain objects."""

    if '__type__' not in dct:
        return dct

    obj_type = dct['__type__']

    # Handle datetime/date
    if obj_type == 'datetime':
        return datetime.fromisoformat(dct['value'])
    if obj_type == 'date':
        return date.fromisoformat(dct['value'])

    # Handle Decimal
    if obj_type == 'Decimal':
        return Decimal(dct['value'])

    # Handle value objects
    if obj_type == 'Money':
        return Money(dct['amount'])
    if obj_type == 'Percentage':
        return Percentage(dct['value'])
    if obj_type == 'Strike':
        return Strike(dct['price'])

    # Handle OptionQuote
    if obj_type == 'OptionQuote':
        return OptionQuote(
            bid=dct['bid'],
            ask=dct['ask'],
            implied_volatility=dct['implied_volatility'],
            open_interest=dct['open_interest'],
            volume=dct['volume'],
            delta=dct['delta'],
            gamma=dct['gamma'],
            theta=dct['theta'],
            vega=dct['vega'],
        )

    # Handle OptionChain
    if obj_type == 'OptionChain':
        # Convert string keys back to Strike objects
        calls = {Strike(k): v for k, v in dct['calls'].items()}
        puts = {Strike(k): v for k, v in dct['puts'].items()}
        return OptionChain(
            ticker=dct['ticker'],
            expiration=dct['expiration'],
            stock_price=dct['stock_price'],
            calls=calls,
            puts=puts,
        )

    # Handle ImpliedMove
    if obj_type == 'ImpliedMove':
        return ImpliedMove(
            ticker=dct['ticker'],
            expiration=dct['expiration'],
            stock_price=dct['stock_price'],
            atm_strike=dct['atm_strike'],
            straddle_cost=dct['straddle_cost'],
            implied_move_pct=dct['implied_move_pct'],
            upper_bound=dct['upper_bound'],
            lower_bound=dct['lower_bound'],
            call_iv=dct.get('call_iv'),
            put_iv=dct.get('put_iv'),
            avg_iv=dct.get('avg_iv'),
        )

    # Handle VRPResult
    if obj_type == 'VRPResult':
        return VRPResult(
            ticker=dct['ticker'],
            expiration=dct['expiration'],
            implied_move_pct=dct['implied_move_pct'],
            historical_mean_move_pct=dct['historical_mean_move_pct'],
            vrp_ratio=dct['vrp_ratio'],
            edge_score=dct['edge_score'],
            recommendation=Recommendation(dct['recommendation']),
        )

    # Handle enums
    if obj_type == 'EarningsTiming':
        return EarningsTiming(dct['value'])
    if obj_type == 'OptionType':
        return OptionType(dct['value'])
    if obj_type == 'Recommendation':
        return Recommendation(dct['value'])
    if obj_type == 'StrategyType':
        return StrategyType(dct['value'])
    if obj_type == 'DirectionalBias':
        return DirectionalBias(dct['value'])

    return dct


def serialize(obj: Any) -> str:
    """Serialize domain object to JSON string."""
    return json.dumps(obj, cls=DomainJSONEncoder, indent=None, separators=(',', ':'))


def deserialize(json_str: str) -> Any:
    """Deserialize JSON string to domain object."""
    return json.loads(json_str, object_hook=domain_object_hook)
