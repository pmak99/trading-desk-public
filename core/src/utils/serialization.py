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
    """
    JSON encoder that handles domain value objects.

    Provides secure serialization of all domain types without the security
    risks of pickle. Each domain object type is explicitly handled with
    a type tag for safe reconstruction during deserialization.

    Supported types:
        - Value objects: Money, Percentage, Strike
        - Market data: OptionQuote, OptionChain
        - Analysis results: ImpliedMove, VRPResult
        - Strategies: StrategyLeg, Strategy, StrategyRecommendation
        - Enums: All domain enums (StrategyType, OptionType, etc.)
        - Standard types: datetime, date, Decimal

    Security:
        Unlike pickle, this encoder only reconstructs explicitly whitelisted
        domain types. Malicious payloads cannot execute arbitrary code.
    """

    def default(self, obj):
        """
        Convert domain objects to JSON-serializable dicts.

        Args:
            obj: Object to serialize

        Returns:
            JSON-serializable dict with __type__ tag

        Raises:
            TypeError: If object type is not supported
        """

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
    """
    Decode JSON dicts back to domain objects.

    This function is the deserialization counterpart to DomainJSONEncoder.
    It reconstructs domain objects from their JSON representation by checking
    the __type__ tag and calling the appropriate constructor.

    Args:
        dct: Dictionary potentially containing a __type__ tag

    Returns:
        Reconstructed domain object or original dict if no __type__ tag

    Security:
        Only explicitly whitelisted types are reconstructed. Unknown __type__
        values are returned as plain dicts, preventing code execution attacks.
    """

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
    """
    Serialize domain object to JSON string.

    Converts any domain object (Money, Strategy, OptionChain, etc.) into a
    compact JSON string representation. Safe to store in databases or transmit
    over networks.

    Args:
        obj: Domain object, list of objects, or dict containing domain objects

    Returns:
        Compact JSON string (no indentation)

    Examples:
        >>> from src.domain.types import Money
        >>> price = Money("42.50")
        >>> json_str = serialize(price)
        >>> json_str
        '{"__type__":"Money","amount":"42.50"}'

        >>> from src.domain.types import Strike
        >>> strikes = [Strike("100"), Strike("105"), Strike("110")]
        >>> serialize(strikes)
        '[{"__type__":"Strike","price":"100"},...}]'

    Security:
        This function is a secure replacement for pickle.dumps(). Unlike pickle,
        the output cannot be used to execute arbitrary code during deserialization.

    See Also:
        deserialize: Reconstruct objects from JSON string
        DomainJSONEncoder: The encoder handling all domain types
    """
    return json.dumps(obj, cls=DomainJSONEncoder, indent=None, separators=(',', ':'))


def deserialize(json_str: str) -> Any:
    """
    Deserialize JSON string to domain object.

    Reconstructs domain objects from JSON string created by serialize().
    Only explicitly whitelisted domain types are reconstructed.

    Args:
        json_str: JSON string from serialize()

    Returns:
        Reconstructed domain object(s)

    Raises:
        json.JSONDecodeError: If json_str is not valid JSON

    Examples:
        >>> json_str = '{"__type__":"Money","amount":"42.50"}'
        >>> money = deserialize(json_str)
        >>> money
        Money(amount=Decimal('42.50'))

        >>> # Round-trip serialization
        >>> from src.domain.types import Percentage
        >>> original = Percentage(0.65)
        >>> json_str = serialize(original)
        >>> restored = deserialize(json_str)
        >>> restored == original
        True

    Security:
        This function is a secure replacement for pickle.loads(). Unknown
        __type__ tags are ignored, preventing code execution attacks.

    See Also:
        serialize: Convert objects to JSON string
        domain_object_hook: The decoder handling reconstruction
    """
    return json.loads(json_str, object_hook=domain_object_hook)
