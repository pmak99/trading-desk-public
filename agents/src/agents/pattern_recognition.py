"""PatternRecognitionAgent - Analyzes historical earnings patterns.

This agent mines historical_moves data to identify actionable patterns
like directional bias, streaks, and magnitude trends.
"""

from typing import Dict, Any, Optional, List
import logging

from ..integration.container_2_0 import Container2_0
from ..utils.schemas import PatternResult

logger = logging.getLogger(__name__)


class PatternRecognitionAgent:
    """
    Agent for analyzing historical earnings patterns.

    Patterns detected:
    - Directional bias (>65% moves in same direction)
    - Streak (consecutive same-direction moves)
    - Magnitude trend (recent moves vs overall average)
    - Seasonality (Q4 vs other quarters)
    - Fade pattern (gap direction vs close direction)

    Minimum data requirement: 8 quarters

    Example:
        agent = PatternRecognitionAgent()
        result = agent.analyze("NVDA")
        # Returns:
        # {
        #     'ticker': 'NVDA',
        #     'quarters_analyzed': 16,
        #     'bullish_pct': 0.69,
        #     'directional_bias': 'BULLISH',
        #     'current_streak': 3,
        #     'streak_direction': 'UP',
        #     ...
        # }
    """

    MIN_QUARTERS = 8
    BIAS_THRESHOLD = 0.65  # 65% for directional bias
    STREAK_THRESHOLD = 3   # Min streak to report
    MAGNITUDE_CHANGE_THRESHOLD = 0.20  # 20% change for trend

    def __init__(self):
        """Initialize with core container for data access."""
        self.container = Container2_0()

    def _extract_value(self, val) -> float:
        """Extract float value from various types (Percentage, Money, dict, float).

        Args:
            val: Value to extract from (could be Percentage, Money, dict, or float)

        Returns:
            Float value extracted from the input
        """
        if val is None:
            return 0.0
        # Handle dataclass types with .value attribute (Percentage)
        if hasattr(val, 'value') and not isinstance(val, dict):
            return float(val.value)
        # Handle dataclass types with .amount attribute (Money)
        if hasattr(val, 'amount') and not isinstance(val, dict):
            return float(val.amount)
        # Handle dict from asdict() conversion
        if isinstance(val, dict):
            if 'value' in val:
                return float(val['value'])
            if 'amount' in val:
                return float(val['amount'])
            return 0.0
        # Handle numeric types
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    def _convert_move_to_dict(self, move) -> Dict[str, Any]:
        """Convert a HistoricalMove object or dict to a standardized dict.

        Args:
            move: Either a HistoricalMove dataclass or a dict

        Returns:
            Dict with standardized keys including computed 'direction'
        """
        # If already a dict, use it directly
        if isinstance(move, dict):
            result = move.copy()
        else:
            # It's a dataclass - access attributes directly to avoid asdict issues
            # with nested dataclasses (Money, Percentage)
            result = {
                'ticker': getattr(move, 'ticker', ''),
                'earnings_date': getattr(move, 'earnings_date', ''),
                'gap_move_pct': self._extract_value(getattr(move, 'gap_move_pct', 0)),
                'intraday_move_pct': self._extract_value(getattr(move, 'intraday_move_pct', 0)),
                'close_move_pct': self._extract_value(getattr(move, 'close_move_pct', 0)),
            }

        # Convert date to string if needed
        if 'earnings_date' in result and hasattr(result['earnings_date'], 'isoformat'):
            result['earnings_date'] = result['earnings_date'].isoformat()

        # Convert any remaining nested types to float
        for key in ['gap_move_pct', 'intraday_move_pct', 'close_move_pct']:
            if key in result:
                result[key] = self._extract_value(result[key])

        # Compute direction from gap_move_pct if not present
        if 'direction' not in result:
            gap_move = result.get('gap_move_pct', 0)
            result['direction'] = 'UP' if gap_move >= 0 else 'DOWN'

        return result

    def analyze(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Analyze historical patterns for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Pattern analysis dict or None if insufficient data
        """
        # Get historical moves
        moves_result = self.container.get_historical_moves(ticker, limit=50)

        # Handle Result type
        if hasattr(moves_result, 'is_err') and moves_result.is_err:
            return None
        if hasattr(moves_result, 'value'):
            moves_raw = moves_result.value
        else:
            moves_raw = moves_result

        if not moves_raw or len(moves_raw) < self.MIN_QUARTERS:
            logger.debug(f"{ticker}: Insufficient data ({len(moves_raw) if moves_raw else 0} quarters)")
            return None

        # Convert to standardized dicts
        moves = [self._convert_move_to_dict(m) for m in moves_raw]

        # Sort by date (newest first)
        moves = sorted(moves, key=lambda x: x.get('earnings_date', ''), reverse=True)

        # Calculate patterns
        directional = self._analyze_directional(moves)
        streak = self._analyze_streak(moves)
        magnitude = self._analyze_magnitude(moves)
        recent = self._get_recent_moves(moves)

        # Build result
        result = {
            'ticker': ticker,
            'quarters_analyzed': len(moves),
            **directional,
            **streak,
            **magnitude,
            'recent_moves': recent
        }

        # Validate with schema
        try:
            validated = PatternResult(**result)
            return validated.model_dump()
        except Exception as e:
            logger.error(f"Schema validation error: {e}")
            return result

    def _analyze_directional(self, moves: List[Dict]) -> Dict[str, Any]:
        """Analyze directional bias."""
        up_count = sum(1 for m in moves if m.get('direction') == 'UP')
        total = len(moves)

        bullish_pct = up_count / total if total > 0 else 0.5
        bearish_pct = 1 - bullish_pct

        # Determine bias
        if bullish_pct >= self.BIAS_THRESHOLD:
            bias = "BULLISH"
        elif bearish_pct >= self.BIAS_THRESHOLD:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {
            'bullish_pct': round(bullish_pct, 2),
            'bearish_pct': round(bearish_pct, 2),
            'directional_bias': bias
        }

    def _analyze_streak(self, moves: List[Dict]) -> Dict[str, Any]:
        """Analyze current streak."""
        if not moves:
            return {'current_streak': 0, 'streak_direction': 'UP'}

        # Get direction of most recent move
        current_direction = moves[0].get('direction', 'UP')
        streak = 1

        # Count consecutive moves in same direction
        for move in moves[1:]:
            if move.get('direction') == current_direction:
                streak += 1
            else:
                break

        return {
            'current_streak': streak,
            'streak_direction': current_direction
        }

    def _analyze_magnitude(self, moves: List[Dict]) -> Dict[str, Any]:
        """Analyze magnitude trends."""
        # Get move magnitudes
        magnitudes = []
        for m in moves:
            gap = m.get('gap_move_pct', 0)
            if hasattr(gap, 'amount'):
                gap = float(gap.amount)
            elif hasattr(gap, 'value'):
                gap = float(gap.value)
            else:
                gap = float(gap) if gap else 0
            magnitudes.append(abs(gap))

        if len(magnitudes) < 4:
            return {
                'avg_move_recent': 0.0,
                'avg_move_overall': 0.0,
                'magnitude_trend': None
            }

        # Recent = last 4 quarters, Overall = all data
        recent_avg = sum(magnitudes[:4]) / 4
        overall_avg = sum(magnitudes) / len(magnitudes)

        # Determine trend
        if overall_avg > 0:
            change_pct = (recent_avg - overall_avg) / overall_avg
            if change_pct > self.MAGNITUDE_CHANGE_THRESHOLD:
                trend = "EXPANDING"
            elif change_pct < -self.MAGNITUDE_CHANGE_THRESHOLD:
                trend = "CONTRACTING"
            else:
                trend = "STABLE"
        else:
            trend = "STABLE"

        return {
            'avg_move_recent': round(recent_avg, 2),
            'avg_move_overall': round(overall_avg, 2),
            'magnitude_trend': trend
        }

    def _get_recent_moves(self, moves: List[Dict], limit: int = 4) -> List[Dict]:
        """Get recent moves for display."""
        recent = []
        for move in moves[:limit]:
            gap = move.get('gap_move_pct', 0)
            if hasattr(gap, 'amount'):
                gap = float(gap.amount)
            elif hasattr(gap, 'value'):
                gap = float(gap.value)
            else:
                gap = float(gap) if gap else 0

            recent.append({
                'date': move.get('earnings_date', 'N/A'),
                'move': round(gap, 2),
                'direction': move.get('direction', 'N/A')
            })
        return recent
