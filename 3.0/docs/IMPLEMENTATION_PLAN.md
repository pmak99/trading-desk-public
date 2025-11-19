# Trading System 3.0 - Detailed Implementation Plan

## Table of Contents
1. [File Inventory](#file-inventory)
2. [MCP Adapter Implementations](#mcp-adapter-implementations)
3. [News Sentiment & Technical Indicators](#news-sentiment--technical-indicators)
4. [Memory MCP Integration](#memory-mcp-integration)
5. [Custom MCP Servers](#custom-mcp-servers)
6. [Container Modifications](#container-modifications)
7. [Cache Strategy](#cache-strategy)
8. [Configuration](#configuration)
9. [Script Modifications](#script-modifications)
10. [Octagon Trial Maximization](#octagon-trial-maximization)
11. [Testing Strategy](#testing-strategy)
12. [Phased Rollout](#phased-rollout)
13. [Rollback Procedures](#rollback-procedures)

---

## File Inventory

### Files to Create (17 total)

#### MCP Adapters (8 files)
```
3.0/src/infrastructure/api/mcp_adapters/__init__.py
3.0/src/infrastructure/api/mcp_adapters/alpha_vantage_mcp.py
3.0/src/infrastructure/api/mcp_adapters/yahoo_finance_mcp.py
3.0/src/infrastructure/api/mcp_adapters/sequential_thinking_mcp.py
3.0/src/infrastructure/api/mcp_adapters/octagon_mcp.py
3.0/src/infrastructure/api/mcp_adapters/composer_mcp.py
3.0/src/infrastructure/api/mcp_adapters/alpaca_mcp.py
3.0/src/infrastructure/api/mcp_adapters/sentiment_indicators_mcp.py
3.0/src/infrastructure/api/mcp_adapters/memory_mcp.py
```

#### Cache Layer (1 file)
```
3.0/src/infrastructure/cache/unified_cache.py
```

#### New Scripts (3 files)
```
3.0/scripts/backtest_mcp.py
3.0/scripts/octagon_bulk_research.py
3.0/scripts/paper_trade.py
```

#### Custom MCP Servers (8 files)
```
mcp-servers/README.md
mcp-servers/trades-history/server.py
mcp-servers/trades-history/requirements.txt
mcp-servers/trades-history/README.md
mcp-servers/screening-results/server.py
mcp-servers/screening-results/requirements.txt
mcp-servers/screening-results/README.md
```

### Files to Modify (5 total)
```
3.0/src/container.py           # Add MCP providers
3.0/src/config/config.py       # Add MCP configuration
3.0/scripts/scan.py            # Use MCP adapters
3.0/scripts/analyze.py         # Add reasoning/research options
3.0/trade.sh                   # Add backtest/paper commands
```

### Files to Copy from 2.0 (Entire src/ and scripts/)
```bash
# Copy entire 2.0 structure as base
cp -r "2.0/src" "3.0/src"
cp -r "2.0/scripts" "3.0/scripts"
cp -r "2.0/data" "3.0/data"
cp "2.0/trade.sh" "3.0/trade.sh"
cp "2.0/requirements.txt" "3.0/requirements.txt"
```

---

## MCP Adapter Implementations

### Alpha Vantage MCP Adapter

**File:** `3.0/src/infrastructure/api/mcp_adapters/alpha_vantage_mcp.py`

```python
"""
Alpha Vantage MCP Adapter - Drop-in replacement for AlphaVantageAPI
"""
from typing import Optional
from datetime import datetime
from result import Result, Ok, Err
from src.domain.errors import AppError
from src.infrastructure.cache.unified_cache import UnifiedCache
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.rate_limiter import RateLimiter
import logging

logger = logging.getLogger(__name__)

# Cache version for invalidation on schema changes
CACHE_VERSION = "v1"

class AlphaVantageMCPAdapter:
    """
    MCP-backed implementation matching AlphaVantageAPI interface.
    All methods return identical formats to original.
    """

    def __init__(self, cache: UnifiedCache):
        self._cache = cache
        self._cache_ttl = 21600  # 6 hours for 25/day limit
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            reset_timeout=300,
            half_open_max_calls=1
        )
        self._rate_limiter = RateLimiter(
            max_calls=25,
            period=86400  # 24 hours
        )

    def get_earnings_calendar(
        self,
        symbol: Optional[str] = None,
        horizon: str = "3month"
    ) -> Result[list, AppError]:
        """
        Fetch earnings calendar via Alpha Vantage MCP.

        Returns same format as direct API:
        [
            {
                'symbol': 'AAPL',
                'name': 'Apple Inc',
                'reportDate': '2024-01-25',
                'fiscalDateEnding': '2023-12-31',
                'estimate': '2.10',
                'currency': 'USD'
            },
            ...
        ]
        """
        cache_key = f"{CACHE_VERSION}:av_mcp:earnings:{horizon}:{symbol or 'all'}"

        # Check cache first
        cached = self._cache.get(cache_key)
        if cached:
            return Ok(cached)

        # Check circuit breaker state
        if not self._circuit_breaker.can_execute():
            logger.warning("Circuit breaker OPEN for Alpha Vantage")
            return Err(AppError("Alpha Vantage circuit breaker is open"))

        # Check rate limit
        if not self._rate_limiter.acquire():
            logger.warning("Rate limit exceeded for Alpha Vantage (25/day)")
            return Err(AppError("Alpha Vantage rate limit exceeded"))

        try:
            # Call MCP tool
            # mcp__alphavantage__EARNINGS_CALENDAR
            result = self._call_mcp_earnings_calendar(symbol, horizon)

            # Record success with circuit breaker
            self._circuit_breaker.record_success()

            # Cache the result
            self._cache.set(cache_key, result, ttl=self._cache_ttl)

            return Ok(result)
        except Exception as e:
            # Record failure with circuit breaker
            self._circuit_breaker.record_failure()
            logger.error(f"MCP earnings calendar failed: {e}")
            return Err(AppError(f"MCP earnings calendar failed: {e}"))

    def get_daily_prices(
        self,
        symbol: str,
        outputsize: str = "compact"
    ) -> Result[dict, AppError]:
        """
        Fetch daily adjusted prices via Alpha Vantage MCP.

        Returns same format as direct API.
        """
        cache_key = f"av_mcp:daily:{symbol}:{outputsize}"

        cached = self._cache.get(cache_key)
        if cached:
            return Ok(cached)

        try:
            # Call mcp__alphavantage__TIME_SERIES_DAILY_ADJUSTED
            result = self._call_mcp_daily_prices(symbol, outputsize)

            self._cache.set(cache_key, result, ttl=self._cache_ttl)

            return Ok(result)
        except Exception as e:
            return Err(AppError(f"MCP daily prices failed: {e}"))

    def _call_mcp_earnings_calendar(self, symbol, horizon):
        """
        Internal: Call the MCP tool and transform response.

        Note: Actual MCP calls are handled by Claude Code runtime.
        This method structures the request/response transformation.
        """
        # MCP tool: mcp__alphavantage__EARNINGS_CALENDAR
        # Parameters: symbol (optional), horizon (3month, 6month, 12month)
        # Transform CSV response to list of dicts
        pass

    def _call_mcp_daily_prices(self, symbol, outputsize):
        """
        Internal: Call the MCP tool for daily prices.
        """
        # MCP tool: mcp__alphavantage__TIME_SERIES_DAILY_ADJUSTED
        # Parameters: symbol, outputsize
        pass
```

### Yahoo Finance MCP Adapter

**File:** `3.0/src/infrastructure/api/mcp_adapters/yahoo_finance_mcp.py`

```python
"""
Yahoo Finance MCP Adapter - Replace yfinance library calls
"""
from typing import Optional
from src.infrastructure.cache.unified_cache import UnifiedCache

class YahooFinanceMCPAdapter:
    """
    MCP-backed replacement for yfinance library calls.
    Used in scan.py and backfill_yfinance.py.
    """

    def __init__(self, cache: UnifiedCache):
        self._cache = cache
        self._market_cap_ttl = 86400   # 24 hours
        self._price_ttl = 300          # 5 minutes

    def get_market_cap(self, symbol: str) -> Optional[float]:
        """
        Get market cap in millions.

        Replaces: yf.Ticker(symbol).info.get('marketCap')
        Used in: scan.py get_market_cap_millions()
        """
        cache_key = f"yf_mcp:marketcap:{symbol}"

        cached = self._cache.get(cache_key)
        if cached:
            return cached

        try:
            # Call mcp__yahoo-finance__getStockHistory
            # Extract market cap from response
            result = self._call_mcp_stock_info(symbol)
            market_cap = result.get('marketCap')

            if market_cap:
                market_cap_millions = market_cap / 1_000_000
                self._cache.set(cache_key, market_cap_millions, ttl=self._market_cap_ttl)
                return market_cap_millions

            return None
        except Exception:
            return None

    def get_stock_history(
        self,
        symbol: str,
        period: str = "3y",
        interval: str = "1d"
    ) -> list:
        """
        Get historical OHLCV data.

        Replaces: yf.Ticker(symbol).history(period=period)
        Used in: backfill_yfinance.py
        """
        cache_key = f"yf_mcp:history:{symbol}:{period}:{interval}"

        cached = self._cache.get(cache_key)
        if cached:
            return cached

        try:
            # Call mcp__yahoo-finance__getStockHistory
            result = self._call_mcp_history(symbol, period, interval)

            self._cache.set(cache_key, result, ttl=self._price_ttl)

            return result
        except Exception:
            return []

    def _call_mcp_stock_info(self, symbol):
        """Internal: Call MCP for stock info."""
        pass

    def _call_mcp_history(self, symbol, period, interval):
        """Internal: Call MCP for historical data."""
        pass
```

### Sequential Thinking MCP Adapter

**File:** `3.0/src/infrastructure/api/mcp_adapters/sequential_thinking_mcp.py`

```python
"""
Sequential Thinking MCP Adapter - Multi-step reasoning for trade decisions
"""
from typing import Dict, Any
from dataclasses import dataclass

@dataclass
class TradeDecision:
    recommendation: str          # 'STRONG_BUY', 'BUY', 'HOLD', 'AVOID'
    confidence: float            # 0.0 to 1.0
    reasoning_steps: list[str]   # Step-by-step logic
    risk_factors: list[str]      # Identified risks

@dataclass
class StrategyRecommendation:
    strategy: str                # 'IRON_CONDOR', 'BULL_PUT_SPREAD', etc.
    reasoning: str               # Why this strategy
    alternatives: list[str]      # Other viable options

@dataclass
class PositionSize:
    contracts: int
    percentage_of_portfolio: float
    kelly_fraction: float
    reasoning: str

class SequentialThinkingProvider:
    """
    Multi-step reasoning for complex trade decisions.
    Uses mcp__sequential-thinking__sequentialthinking tool.
    """

    async def analyze_trade_opportunity(
        self,
        ticker_data: Dict[str, Any]
    ) -> TradeDecision:
        """
        5-step trade analysis:
        1. Evaluate VRP ratio significance
        2. Assess historical consistency
        3. Check liquidity requirements
        4. Consider earnings timing (BMO/AMC)
        5. Generate confidence score

        Args:
            ticker_data: {
                'symbol': 'NVDA',
                'vrp_ratio': 1.7,
                'implied_move_pct': 8.5,
                'historical_moves': [7.2, 6.8, 9.1, ...],
                'iv_percentile': 85,
                'open_interest': 15000,
                'earnings_timing': 'AMC'
            }

        Returns:
            TradeDecision with recommendation and reasoning
        """
        prompt = f"""
        Analyze this earnings trade opportunity step by step:

        Symbol: {ticker_data['symbol']}
        VRP Ratio: {ticker_data['vrp_ratio']}
        Implied Move: {ticker_data['implied_move_pct']}%
        Historical Moves: {ticker_data['historical_moves']}
        IV Percentile: {ticker_data['iv_percentile']}
        Open Interest: {ticker_data['open_interest']}
        Earnings Timing: {ticker_data['earnings_timing']}

        Step 1: Is the VRP ratio significant? (>1.5 is good, >1.7 is excellent)
        Step 2: How consistent are historical moves? Calculate std dev.
        Step 3: Is liquidity sufficient? (OI > 500)
        Step 4: Does earnings timing affect the trade? (AMC has overnight risk)
        Step 5: Generate overall confidence score (0-100)

        Provide a final recommendation: STRONG_BUY, BUY, HOLD, or AVOID
        """

        # Call mcp__sequential-thinking__sequentialthinking
        result = await self._call_sequential_thinking(prompt, total_thoughts=5)

        return self._parse_trade_decision(result)

    async def select_strategy(
        self,
        analysis: Dict[str, Any]
    ) -> StrategyRecommendation:
        """
        Choose optimal strategy based on market conditions.

        Strategies considered:
        - IRON_CONDOR: Neutral IV skew, high VRP
        - BULL_PUT_SPREAD: Bullish skew, support levels
        - BEAR_CALL_SPREAD: Bearish skew, resistance levels
        - STRANGLE: Very high IV, direction unknown

        Args:
            analysis: Full ticker analysis including skew data

        Returns:
            StrategyRecommendation with reasoning
        """
        prompt = f"""
        Select the optimal options strategy for this setup:

        VRP Ratio: {analysis['vrp_ratio']}
        IV Skew (call vs put): {analysis.get('iv_skew', 'neutral')}
        Historical Direction Bias: {analysis.get('direction_bias', 'none')}
        Risk Tolerance: {analysis.get('risk_tolerance', 'moderate')}

        Consider these strategies:
        1. IRON_CONDOR - Best for neutral skew, defined risk
        2. BULL_PUT_SPREAD - Best for bullish bias, capital efficient
        3. BEAR_CALL_SPREAD - Best for bearish bias
        4. STRANGLE - Best for very high IV, undefined risk

        Select one and explain why it's optimal for these conditions.
        List 1-2 alternatives if conditions change slightly.
        """

        result = await self._call_sequential_thinking(prompt, total_thoughts=4)

        return self._parse_strategy_recommendation(result)

    async def calculate_position_size(
        self,
        account: Dict[str, Any],
        trade: Dict[str, Any]
    ) -> PositionSize:
        """
        Kelly-based position sizing with portfolio considerations.

        Steps:
        1. Calculate base Kelly fraction from win rate and avg win/loss
        2. Adjust for correlation with existing positions
        3. Apply maximum position limits (e.g., 5% of portfolio)
        4. Consider current portfolio heat
        5. Final recommendation

        Args:
            account: {'balance': 50000, 'positions': [...], 'heat': 0.15}
            trade: {'win_rate': 0.65, 'avg_win': 200, 'avg_loss': 150, 'max_loss': 500}

        Returns:
            PositionSize with contracts and reasoning
        """
        prompt = f"""
        Calculate optimal position size for this trade:

        Account Balance: ${account['balance']}
        Current Portfolio Heat: {account.get('heat', 0) * 100}%
        Existing Positions: {len(account.get('positions', []))}

        Trade Parameters:
        Win Rate: {trade['win_rate'] * 100}%
        Average Win: ${trade['avg_win']}
        Average Loss: ${trade['avg_loss']}
        Max Loss Per Contract: ${trade['max_loss']}

        Step 1: Calculate Kelly fraction = (win_rate * avg_win - (1-win_rate) * avg_loss) / avg_win
        Step 2: Apply half-Kelly for safety
        Step 3: Check correlation with existing positions
        Step 4: Apply 5% max position rule
        Step 5: Calculate final contract count

        Provide final recommendation with reasoning.
        """

        result = await self._call_sequential_thinking(prompt, total_thoughts=5)

        return self._parse_position_size(result)

    async def _call_sequential_thinking(self, prompt: str, total_thoughts: int):
        """
        Internal: Call the Sequential Thinking MCP tool.

        Uses mcp__sequential-thinking__sequentialthinking with:
        - thought: The current thinking step
        - thoughtNumber: Current step (1 to total_thoughts)
        - totalThoughts: Expected number of steps
        - nextThoughtNeeded: True until final step
        """
        pass

    def _parse_trade_decision(self, result) -> TradeDecision:
        """Parse MCP response into TradeDecision."""
        pass

    def _parse_strategy_recommendation(self, result) -> StrategyRecommendation:
        """Parse MCP response into StrategyRecommendation."""
        pass

    def _parse_position_size(self, result) -> PositionSize:
        """Parse MCP response into PositionSize."""
        pass
```

### Octagon Research MCP Adapter

**File:** `3.0/src/infrastructure/api/mcp_adapters/octagon_mcp.py`

```python
"""
Octagon Research MCP Adapter - Earnings research and fundamentals
"""
from typing import Optional
from src.infrastructure.cache.unified_cache import UnifiedCache

class OctagonResearchProvider:
    """
    Earnings transcript analysis, SEC filings, institutional holdings.
    Uses octagon-agent and octagon-deep-research-agent tools.
    """

    def __init__(self, cache: UnifiedCache):
        self._cache = cache
        self._transcript_ttl = 604800  # 7 days
        self._holdings_ttl = 86400     # 24 hours

    async def get_earnings_transcript(
        self,
        symbol: str,
        quarter: str  # e.g., "2024Q3"
    ) -> str:
        """
        Get earnings call transcript summary.

        Returns key points:
        - Revenue guidance
        - Margin outlook
        - Key risks mentioned
        - Analyst Q&A highlights
        """
        cache_key = f"octagon:transcript:{symbol}:{quarter}"

        cached = self._cache.get(cache_key)
        if cached:
            return cached

        prompt = f"""
        Analyze the earnings call transcript for {symbol} {quarter}.

        Extract:
        1. Revenue guidance (beat/miss/inline, forward guidance)
        2. Margin commentary (expanding/contracting)
        3. Key risks or concerns mentioned
        4. Notable analyst questions and management responses
        5. Overall sentiment (bullish/neutral/bearish)
        """

        # Call mcp__octagon__octagon-agent
        result = await self._call_octagon_agent(prompt)

        self._cache.set(cache_key, result, ttl=self._transcript_ttl)

        return result

    async def get_institutional_holdings(self, symbol: str) -> dict:
        """
        Get 13F institutional holdings data.

        Returns:
        - Top 10 institutional holders
        - Recent changes (increased/decreased)
        - Percentage of float held by institutions
        """
        cache_key = f"octagon:holdings:{symbol}"

        cached = self._cache.get(cache_key)
        if cached:
            return cached

        prompt = f"""
        Get institutional holdings data for {symbol} from 13F filings.

        Include:
        1. Top 10 institutional holders with share counts
        2. Quarter-over-quarter changes
        3. Percentage of float held by institutions
        4. Notable new positions or exits
        """

        result = await self._call_octagon_agent(prompt)

        self._cache.set(cache_key, result, ttl=self._holdings_ttl)

        return result

    async def deep_research(self, query: str) -> str:
        """
        Comprehensive research using multiple data sources.

        Good for complex questions like:
        - "What factors drove NVDA's last earnings beat?"
        - "Compare AAPL and MSFT earnings quality"
        """
        # Call mcp__octagon__octagon-deep-research-agent
        return await self._call_deep_research(query)

    async def _call_octagon_agent(self, prompt: str) -> str:
        """Internal: Call octagon-agent MCP tool."""
        pass

    async def _call_deep_research(self, query: str) -> str:
        """Internal: Call octagon-deep-research-agent MCP tool."""
        pass
```

### Composer Backtest MCP Adapter

**File:** `3.0/src/infrastructure/api/mcp_adapters/composer_mcp.py`

```python
"""
Composer Trade MCP Adapter - Strategy backtesting and optimization
"""
from dataclasses import dataclass
from typing import Dict, Any, Tuple

@dataclass
class BacktestResult:
    win_rate: float
    total_trades: int
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    avg_win: float
    avg_loss: float

@dataclass
class OptimalParams:
    vrp_threshold: float
    min_iv_percentile: float
    dte_range: Tuple[int, int]
    performance: BacktestResult

class ComposerBacktestProvider:
    """
    Strategy backtesting and parameter optimization.
    Uses Composer Trade MCP for historical testing.
    """

    async def validate_strategy(
        self,
        strategy_config: Dict[str, Any]
    ) -> BacktestResult:
        """
        Backtest an IV crush strategy configuration.

        Args:
            strategy_config: {
                'entry_signal': 'vrp_ratio > 1.5 AND iv_percentile > 70',
                'exit_signal': 'earnings_passed OR profit_pct > 50',
                'position_size': 'kelly_half',
                'universe': 'sp500_earnings',
                'start_date': '2022-01-01',
                'end_date': '2024-01-01'
            }

        Returns:
            BacktestResult with performance metrics
        """
        # Build Composer strategy description
        strategy_desc = f"""
        Entry: Sell premium when {strategy_config['entry_signal']}
        Exit: Close when {strategy_config['exit_signal']}
        Position sizing: {strategy_config['position_size']}
        Universe: {strategy_config['universe']}
        Period: {strategy_config['start_date']} to {strategy_config['end_date']}
        """

        # Call Composer MCP for backtesting
        # Note: Composer free tier allows backtesting but not saving
        result = await self._call_composer_backtest(strategy_desc)

        return self._parse_backtest_result(result)

    async def optimize_parameters(
        self,
        param_ranges: Dict[str, Any]
    ) -> OptimalParams:
        """
        Find optimal strategy parameters via grid search.

        Args:
            param_ranges: {
                'vrp_threshold': (1.2, 1.8, 0.1),  # min, max, step
                'min_iv_percentile': (50, 80, 10),
                'dte_range': [(7, 14), (14, 21), (21, 30)]
            }

        Returns:
            OptimalParams with best configuration and performance
        """
        best_result = None
        best_params = None

        # Grid search over parameter space
        for vrp in self._range(*param_ranges['vrp_threshold']):
            for iv_pct in self._range(*param_ranges['min_iv_percentile']):
                for dte in param_ranges['dte_range']:
                    config = {
                        'entry_signal': f'vrp_ratio > {vrp} AND iv_percentile > {iv_pct}',
                        'exit_signal': 'earnings_passed',
                        'dte_min': dte[0],
                        'dte_max': dte[1]
                    }

                    result = await self.validate_strategy(config)

                    # Optimize for Sharpe ratio
                    if best_result is None or result.sharpe_ratio > best_result.sharpe_ratio:
                        best_result = result
                        best_params = (vrp, iv_pct, dte)

        return OptimalParams(
            vrp_threshold=best_params[0],
            min_iv_percentile=best_params[1],
            dte_range=best_params[2],
            performance=best_result
        )

    def _range(self, start, end, step):
        """Generate range with float step."""
        current = start
        while current <= end:
            yield current
            current += step

    async def _call_composer_backtest(self, strategy_desc: str):
        """Internal: Call Composer MCP for backtesting."""
        pass

    def _parse_backtest_result(self, result) -> BacktestResult:
        """Parse Composer response into BacktestResult."""
        pass
```

### Alpaca Paper Trading MCP Adapter

**File:** `3.0/src/infrastructure/api/mcp_adapters/alpaca_mcp.py`

```python
"""
Alpaca Paper Trading MCP Adapter - Paper trade execution
"""
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class Account:
    balance: float
    buying_power: float
    equity: float
    positions_value: float

@dataclass
class Position:
    symbol: str
    quantity: int
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float

@dataclass
class Order:
    symbol: str
    side: str        # 'buy' or 'sell'
    quantity: int
    order_type: str  # 'market', 'limit'
    limit_price: Optional[float] = None
    time_in_force: str = 'day'

@dataclass
class OrderResult:
    order_id: str
    status: str
    filled_qty: int
    filled_avg_price: float

class AlpacaPaperTradingProvider:
    """
    Paper trading via Alpaca MCP.
    Test strategies without risking real money.
    """

    async def get_account(self) -> Account:
        """
        Get paper trading account status.

        Returns balance, buying power, equity.
        """
        # Call mcp__alpaca__alpaca_get_account
        result = await self._call_alpaca('alpaca_get_account')

        return Account(
            balance=float(result.get('cash', 0)),
            buying_power=float(result.get('buying_power', 0)),
            equity=float(result.get('equity', 0)),
            positions_value=float(result.get('long_market_value', 0))
        )

    async def get_positions(self) -> List[Position]:
        """
        Get current paper positions.
        """
        # Call mcp__alpaca__alpaca_list_positions
        result = await self._call_alpaca('alpaca_list_positions')

        positions = []
        for pos in result:
            positions.append(Position(
                symbol=pos['symbol'],
                quantity=int(pos['qty']),
                avg_entry_price=float(pos['avg_entry_price']),
                current_price=float(pos['current_price']),
                unrealized_pnl=float(pos['unrealized_pl']),
                unrealized_pnl_pct=float(pos['unrealized_plpc']) * 100
            ))

        return positions

    async def create_order(self, order: Order) -> OrderResult:
        """
        Place a paper trade order.

        For options, use OCC symbology:
        AAPL240119C00150000 = AAPL Jan 19 2024 $150 Call
        """
        # Call mcp__alpaca__alpaca_create_order
        params = {
            'symbol': order.symbol,
            'side': order.side,
            'qty': str(order.quantity),
            'type': order.order_type,
            'time_in_force': order.time_in_force
        }

        if order.limit_price:
            params['limit_price'] = str(order.limit_price)

        result = await self._call_alpaca('alpaca_create_order', params)

        return OrderResult(
            order_id=result['id'],
            status=result['status'],
            filled_qty=int(result.get('filled_qty', 0)),
            filled_avg_price=float(result.get('filled_avg_price', 0))
        )

    async def get_open_orders(self) -> list:
        """Get all open orders."""
        # Call mcp__alpaca__alpaca_list_orders with status='open'
        return await self._call_alpaca('alpaca_list_orders', {'status': 'open'})

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        # Call mcp__alpaca__alpaca_cancel_order
        result = await self._call_alpaca('alpaca_cancel_order', {'id': order_id})
        return result is not None

    async def _call_alpaca(self, tool: str, params: dict = None):
        """Internal: Call Alpaca MCP tool."""
        pass
```

---

## News Sentiment & Technical Indicators

### News Sentiment Provider

**File:** `3.0/src/infrastructure/api/mcp_adapters/sentiment_indicators_mcp.py`

```python
"""
News Sentiment & Technical Indicators - Pre-earnings analysis enhancement
"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from src.infrastructure.cache.unified_cache import UnifiedCache

@dataclass
class SentimentAnalysis:
    overall_sentiment: str       # 'bullish', 'neutral', 'bearish'
    sentiment_score: float       # -1.0 to 1.0
    article_count: int
    key_topics: List[str]
    recent_headlines: List[str]

@dataclass
class TechnicalSnapshot:
    rsi: float                   # 0-100
    rsi_signal: str              # 'oversold', 'neutral', 'overbought'
    bbands_position: str         # 'below_lower', 'middle', 'above_upper'
    atr: float                   # Average True Range
    atr_percentile: float        # Relative volatility
    macd_signal: str             # 'bullish', 'neutral', 'bearish'
    trend_direction: str         # 'up', 'sideways', 'down'

class NewsSentimentProvider:
    """
    Pre-earnings sentiment analysis using Alpha Vantage NEWS_SENTIMENT.
    Helps gauge market expectations before earnings announcements.
    """

    def __init__(self, cache: UnifiedCache):
        self._cache = cache
        self._sentiment_ttl = 3600  # 1 hour for news

    async def get_pre_earnings_sentiment(
        self,
        ticker: str,
        days_before: int = 7
    ) -> SentimentAnalysis:
        """
        Analyze news sentiment in the days leading up to earnings.

        Args:
            ticker: Stock symbol
            days_before: Number of days to analyze

        Returns:
            SentimentAnalysis with overall sentiment and key topics
        """
        cache_key = f"sentiment:{ticker}:{days_before}d"

        cached = self._cache.get(cache_key)
        if cached:
            return cached

        try:
            # Call mcp__alphavantage__NEWS_SENTIMENT
            # Parameters: tickers, time_from, time_to, sort, limit

            time_to = datetime.now()
            time_from = time_to - timedelta(days=days_before)

            result = await self._call_news_sentiment(
                tickers=ticker,
                time_from=time_from.strftime('%Y%m%dT0000'),
                time_to=time_to.strftime('%Y%m%dT2359'),
                sort='RELEVANCE',
                limit=50
            )

            # Parse sentiment data
            analysis = self._parse_sentiment(result)

            self._cache.set(cache_key, analysis, ttl=self._sentiment_ttl)

            return analysis

        except Exception as e:
            # Return neutral sentiment on error
            return SentimentAnalysis(
                overall_sentiment='neutral',
                sentiment_score=0.0,
                article_count=0,
                key_topics=[],
                recent_headlines=[]
            )

    async def get_sector_sentiment(
        self,
        topics: List[str],
        days: int = 3
    ) -> Dict[str, float]:
        """
        Get sentiment for broader topics/sectors.

        Args:
            topics: List of topics like 'technology', 'earnings', 'ipo'
            days: Number of days to analyze

        Returns:
            Dict mapping topics to sentiment scores
        """
        results = {}

        for topic in topics:
            try:
                result = await self._call_news_sentiment(
                    topics=topic,
                    time_from=(datetime.now() - timedelta(days=days)).strftime('%Y%m%dT0000'),
                    limit=20
                )

                # Calculate average sentiment
                if result and 'feed' in result:
                    scores = [
                        float(article.get('overall_sentiment_score', 0))
                        for article in result['feed']
                    ]
                    results[topic] = sum(scores) / len(scores) if scores else 0.0
                else:
                    results[topic] = 0.0

            except Exception:
                results[topic] = 0.0

        return results

    def _parse_sentiment(self, result: dict) -> SentimentAnalysis:
        """Parse Alpha Vantage NEWS_SENTIMENT response."""
        if not result or 'feed' not in result:
            return SentimentAnalysis(
                overall_sentiment='neutral',
                sentiment_score=0.0,
                article_count=0,
                key_topics=[],
                recent_headlines=[]
            )

        articles = result['feed']

        # Calculate average sentiment
        scores = []
        topics = {}
        headlines = []

        for article in articles[:50]:
            # Get ticker-specific sentiment if available
            ticker_sentiment = article.get('ticker_sentiment', [])
            if ticker_sentiment:
                score = float(ticker_sentiment[0].get('ticker_sentiment_score', 0))
            else:
                score = float(article.get('overall_sentiment_score', 0))
            scores.append(score)

            # Collect topics
            for topic in article.get('topics', []):
                topic_name = topic.get('topic', '')
                if topic_name:
                    topics[topic_name] = topics.get(topic_name, 0) + 1

            # Collect headlines
            headlines.append(article.get('title', ''))

        avg_score = sum(scores) / len(scores) if scores else 0.0

        # Determine overall sentiment
        if avg_score > 0.15:
            overall = 'bullish'
        elif avg_score < -0.15:
            overall = 'bearish'
        else:
            overall = 'neutral'

        # Top topics
        top_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:5]

        return SentimentAnalysis(
            overall_sentiment=overall,
            sentiment_score=avg_score,
            article_count=len(articles),
            key_topics=[t[0] for t in top_topics],
            recent_headlines=headlines[:5]
        )

    async def _call_news_sentiment(self, **kwargs):
        """Internal: Call Alpha Vantage NEWS_SENTIMENT MCP tool."""
        # MCP tool: mcp__alphavantage__NEWS_SENTIMENT
        pass


class TechnicalIndicatorsProvider:
    """
    Technical analysis using Alpha Vantage indicators.
    Used for entry timing and position sizing.
    """

    def __init__(self, cache: UnifiedCache):
        self._cache = cache
        self._indicator_ttl = 3600  # 1 hour for daily indicators

    async def get_technical_snapshot(
        self,
        ticker: str,
        interval: str = 'daily'
    ) -> TechnicalSnapshot:
        """
        Get comprehensive technical snapshot for a ticker.

        Args:
            ticker: Stock symbol
            interval: Time interval (daily, 60min, etc.)

        Returns:
            TechnicalSnapshot with all indicator values
        """
        cache_key = f"technicals:{ticker}:{interval}"

        cached = self._cache.get(cache_key)
        if cached:
            return cached

        # Fetch all indicators in parallel
        rsi_task = self._get_rsi(ticker, interval)
        bbands_task = self._get_bbands(ticker, interval)
        atr_task = self._get_atr(ticker, interval)
        macd_task = self._get_macd(ticker, interval)

        rsi_data, bbands_data, atr_data, macd_data = await asyncio.gather(
            rsi_task, bbands_task, atr_task, macd_task
        )

        snapshot = self._build_snapshot(rsi_data, bbands_data, atr_data, macd_data)

        self._cache.set(cache_key, snapshot, ttl=self._indicator_ttl)

        return snapshot

    async def _get_rsi(
        self,
        ticker: str,
        interval: str,
        time_period: int = 14
    ) -> dict:
        """
        Get RSI (Relative Strength Index).

        RSI < 30: Oversold (potential bounce)
        RSI > 70: Overbought (potential pullback)
        """
        try:
            # Call mcp__alphavantage__RSI
            result = await self._call_indicator('RSI', {
                'symbol': ticker,
                'interval': interval,
                'time_period': time_period,
                'series_type': 'close'
            })

            # Get latest value
            if result and 'Technical Analysis: RSI' in result:
                data = result['Technical Analysis: RSI']
                latest = list(data.values())[0]
                return {'value': float(latest['RSI'])}

            return {'value': 50.0}  # Neutral default

        except Exception:
            return {'value': 50.0}

    async def _get_bbands(
        self,
        ticker: str,
        interval: str,
        time_period: int = 20
    ) -> dict:
        """
        Get Bollinger Bands.

        Price below lower band: Oversold / high volatility
        Price above upper band: Overbought / momentum
        """
        try:
            # Call mcp__alphavantage__BBANDS
            result = await self._call_indicator('BBANDS', {
                'symbol': ticker,
                'interval': interval,
                'time_period': time_period,
                'series_type': 'close',
                'nbdevup': 2,
                'nbdevdn': 2
            })

            if result and 'Technical Analysis: BBANDS' in result:
                data = result['Technical Analysis: BBANDS']
                latest = list(data.values())[0]
                return {
                    'upper': float(latest['Real Upper Band']),
                    'middle': float(latest['Real Middle Band']),
                    'lower': float(latest['Real Lower Band'])
                }

            return {'upper': 0, 'middle': 0, 'lower': 0}

        except Exception:
            return {'upper': 0, 'middle': 0, 'lower': 0}

    async def _get_atr(
        self,
        ticker: str,
        interval: str,
        time_period: int = 14
    ) -> dict:
        """
        Get ATR (Average True Range).

        Higher ATR = More volatility = Wider spreads for iron condors
        Used for position sizing and strike selection.
        """
        try:
            # Call mcp__alphavantage__ATR
            result = await self._call_indicator('ATR', {
                'symbol': ticker,
                'interval': interval,
                'time_period': time_period
            })

            if result and 'Technical Analysis: ATR' in result:
                data = result['Technical Analysis: ATR']
                # Get last 20 ATR values for percentile
                values = [float(v['ATR']) for v in list(data.values())[:20]]
                latest = values[0]

                # Calculate percentile
                sorted_values = sorted(values)
                percentile = sorted_values.index(min(sorted_values, key=lambda x: abs(x - latest))) / len(sorted_values) * 100

                return {
                    'value': latest,
                    'percentile': percentile
                }

            return {'value': 0, 'percentile': 50}

        except Exception:
            return {'value': 0, 'percentile': 50}

    async def _get_macd(
        self,
        ticker: str,
        interval: str
    ) -> dict:
        """
        Get MACD (Moving Average Convergence Divergence).

        MACD > Signal: Bullish momentum
        MACD < Signal: Bearish momentum
        Histogram shows momentum strength
        """
        try:
            # Call mcp__alphavantage__MACD
            result = await self._call_indicator('MACD', {
                'symbol': ticker,
                'interval': interval,
                'series_type': 'close',
                'fastperiod': 12,
                'slowperiod': 26,
                'signalperiod': 9
            })

            if result and 'Technical Analysis: MACD' in result:
                data = result['Technical Analysis: MACD']
                latest = list(data.values())[0]
                return {
                    'macd': float(latest['MACD']),
                    'signal': float(latest['MACD_Signal']),
                    'histogram': float(latest['MACD_Hist'])
                }

            return {'macd': 0, 'signal': 0, 'histogram': 0}

        except Exception:
            return {'macd': 0, 'signal': 0, 'histogram': 0}

    def _build_snapshot(
        self,
        rsi_data: dict,
        bbands_data: dict,
        atr_data: dict,
        macd_data: dict
    ) -> TechnicalSnapshot:
        """Build technical snapshot from indicator data."""

        # RSI signal
        rsi = rsi_data['value']
        if rsi < 30:
            rsi_signal = 'oversold'
        elif rsi > 70:
            rsi_signal = 'overbought'
        else:
            rsi_signal = 'neutral'

        # MACD signal
        if macd_data['histogram'] > 0 and macd_data['macd'] > macd_data['signal']:
            macd_signal = 'bullish'
        elif macd_data['histogram'] < 0 and macd_data['macd'] < macd_data['signal']:
            macd_signal = 'bearish'
        else:
            macd_signal = 'neutral'

        # Trend direction (simplified)
        if macd_data['macd'] > 0 and rsi > 50:
            trend = 'up'
        elif macd_data['macd'] < 0 and rsi < 50:
            trend = 'down'
        else:
            trend = 'sideways'

        return TechnicalSnapshot(
            rsi=rsi,
            rsi_signal=rsi_signal,
            bbands_position='middle',  # Would need price to determine
            atr=atr_data['value'],
            atr_percentile=atr_data['percentile'],
            macd_signal=macd_signal,
            trend_direction=trend
        )

    async def _call_indicator(self, indicator: str, params: dict):
        """Internal: Call Alpha Vantage indicator MCP tool."""
        # MCP tools: mcp__alphavantage__RSI, BBANDS, ATR, MACD
        pass


# Import for async gather
import asyncio
```

### Integration into Analysis Pipeline

**Update `analyze.py` to use sentiment and technicals:**

```python
class EnhancedTickerAnalyzer:
    """Enhanced analyzer with sentiment and technical indicators."""

    async def analyze(self, ticker: str) -> dict:
        """Full analysis including sentiment and technicals."""

        # Existing analysis
        base_analysis = await self._base_analysis(ticker)

        # Add sentiment (if earnings within 7 days)
        if self._is_near_earnings(ticker, days=7):
            sentiment = await self.container.sentiment.get_pre_earnings_sentiment(
                ticker, days_before=7
            )
            base_analysis['pre_earnings_sentiment'] = {
                'overall': sentiment.overall_sentiment,
                'score': sentiment.sentiment_score,
                'article_count': sentiment.article_count,
                'key_topics': sentiment.key_topics
            }

        # Add technical snapshot
        technicals = await self.container.technicals.get_technical_snapshot(ticker)
        base_analysis['technicals'] = {
            'rsi': technicals.rsi,
            'rsi_signal': technicals.rsi_signal,
            'atr': technicals.atr,
            'atr_percentile': technicals.atr_percentile,
            'macd_signal': technicals.macd_signal,
            'trend': technicals.trend_direction
        }

        # Adjust edge score based on technicals
        base_analysis['adjusted_edge_score'] = self._adjust_edge_score(
            base_analysis['edge_score'],
            technicals,
            sentiment if 'pre_earnings_sentiment' in base_analysis else None
        )

        return base_analysis

    def _adjust_edge_score(
        self,
        base_score: float,
        technicals: TechnicalSnapshot,
        sentiment: Optional[SentimentAnalysis]
    ) -> float:
        """Adjust edge score based on technical and sentiment factors."""

        adjustment = 0

        # RSI adjustment (-5 to +5)
        if technicals.rsi_signal == 'oversold':
            adjustment += 3  # Good for put selling
        elif technicals.rsi_signal == 'overbought':
            adjustment -= 2  # Risk of pullback

        # ATR adjustment (high volatility = wider spreads possible)
        if technicals.atr_percentile > 70:
            adjustment += 3  # High premium environment
        elif technicals.atr_percentile < 30:
            adjustment -= 2  # Low premium

        # Sentiment adjustment (-5 to +5)
        if sentiment:
            if sentiment.sentiment_score > 0.2:
                adjustment += 2  # Positive sentiment
            elif sentiment.sentiment_score < -0.2:
                adjustment -= 3  # Negative sentiment = caution

        return min(100, max(0, base_score + adjustment))
```

### Use Cases

1. **Pre-Earnings Sentiment Check:**
   - Before taking a position, check if sentiment is extremely negative
   - Helps avoid "bad surprise" earnings

2. **Entry Timing:**
   - RSI oversold + earnings = better entry for put spreads
   - RSI overbought + earnings = better entry for call spreads

3. **Position Sizing:**
   - High ATR = wider strikes possible
   - Low ATR = tighter spreads, lower premium

4. **Strategy Selection:**
   - Bearish sentiment + neutral technicals = prefer put spreads
   - Bullish sentiment + high ATR = iron condors work well

---

## Memory MCP Integration

### Overview

The Memory MCP provides persistent context across Claude Code sessions. This enables:
- Remembering user trading preferences
- Tracking ongoing analysis across sessions
- Storing frequently used configurations
- Building a knowledge base of past decisions

### Installation

Memory MCP is installed via:
```bash
claude mcp add memory -- npx -y @modelcontextprotocol/server-memory
```

### Memory MCP Adapter

**File:** `3.0/src/infrastructure/api/mcp_adapters/memory_mcp.py`

```python
"""
Memory MCP Adapter - Persistent context for trading preferences and history
"""
from typing import Optional, List, Dict, Any

class TradingMemoryProvider:
    """
    Persistent memory for trading context and preferences.
    Uses Memory MCP for cross-session persistence.
    """

    # Memory entity types
    ENTITY_TYPES = {
        'preference': 'User trading preferences',
        'ticker_note': 'Notes about specific tickers',
        'strategy_config': 'Strategy configurations',
        'session_context': 'Current session state'
    }

    async def store_preference(
        self,
        key: str,
        value: Any,
        description: str = ""
    ):
        """
        Store a user preference.

        Examples:
        - store_preference('max_position_size', 0.05, 'Max 5% of portfolio per trade')
        - store_preference('preferred_strategies', ['iron_condor', 'bull_put_spread'])
        - store_preference('avoid_sectors', ['biotech'])
        """
        await self._create_entity(
            name=f"preference:{key}",
            entity_type='preference',
            observations=[
                f"Value: {value}",
                f"Description: {description}",
                f"Set by user"
            ]
        )

    async def get_preference(self, key: str) -> Optional[Any]:
        """Retrieve a stored preference."""
        entity = await self._get_entity(f"preference:{key}")
        if entity:
            # Parse value from observations
            for obs in entity.get('observations', []):
                if obs.startswith('Value: '):
                    return self._parse_value(obs[7:])
        return None

    async def store_ticker_note(
        self,
        ticker: str,
        note: str,
        note_type: str = 'general'
    ):
        """
        Store notes about a ticker for future reference.

        Examples:
        - store_ticker_note('TSLA', 'Often gaps more than implied', 'earnings')
        - store_ticker_note('NVDA', 'IV crush very reliable', 'strategy')
        - store_ticker_note('GME', 'Avoid - meme stock volatility', 'warning')
        """
        entity_name = f"ticker:{ticker}"

        # Add to existing notes or create new
        await self._add_observation(
            entity_name,
            f"[{note_type}] {note}"
        )

    async def get_ticker_notes(self, ticker: str) -> List[str]:
        """Get all notes for a ticker."""
        entity = await self._get_entity(f"ticker:{ticker}")
        return entity.get('observations', []) if entity else []

    async def store_strategy_config(
        self,
        strategy_name: str,
        config: Dict[str, Any]
    ):
        """
        Store a strategy configuration for reuse.

        Example:
        store_strategy_config('conservative_iron_condor', {
            'min_vrp': 1.7,
            'min_iv_percentile': 75,
            'max_position_size': 0.03,
            'dte_range': (7, 14)
        })
        """
        await self._create_entity(
            name=f"strategy:{strategy_name}",
            entity_type='strategy_config',
            observations=[
                f"Config: {config}",
                f"Strategy type: {strategy_name}"
            ]
        )

    async def get_strategy_config(self, strategy_name: str) -> Optional[Dict]:
        """Retrieve a stored strategy configuration."""
        entity = await self._get_entity(f"strategy:{strategy_name}")
        if entity:
            for obs in entity.get('observations', []):
                if obs.startswith('Config: '):
                    return eval(obs[8:])  # Safe since we control the data
        return None

    async def store_session_context(
        self,
        context_key: str,
        data: Any
    ):
        """
        Store current session context for continuity.

        Examples:
        - store_session_context('current_analysis', {'ticker': 'NVDA', 'step': 3})
        - store_session_context('watchlist_progress', {'completed': 15, 'total': 50})
        """
        await self._create_entity(
            name=f"session:{context_key}",
            entity_type='session_context',
            observations=[f"Data: {data}"]
        )

    async def get_session_context(self, context_key: str) -> Optional[Any]:
        """Retrieve session context."""
        entity = await self._get_entity(f"session:{context_key}")
        if entity:
            for obs in entity.get('observations', []):
                if obs.startswith('Data: '):
                    return self._parse_value(obs[6:])
        return None

    async def create_relation(
        self,
        from_entity: str,
        to_entity: str,
        relation_type: str
    ):
        """
        Create a relationship between entities.

        Examples:
        - create_relation('ticker:NVDA', 'strategy:aggressive_put_spread', 'uses')
        - create_relation('ticker:AAPL', 'ticker:MSFT', 'correlated_with')
        """
        await self._add_relation(from_entity, to_entity, relation_type)

    async def search_memory(self, query: str) -> List[Dict]:
        """
        Search memory for relevant entities.

        Uses Memory MCP's search capability.
        """
        # Call memory MCP search tool
        pass

    def _parse_value(self, value_str: str) -> Any:
        """Parse stored value back to Python object safely."""
        import ast
        try:
            return ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            return value_str

    async def _create_entity(
        self,
        name: str,
        entity_type: str,
        observations: List[str]
    ):
        """Internal: Create or update entity in memory."""
        # Memory MCP tool calls would go here
        pass

    async def _get_entity(self, name: str) -> Optional[Dict]:
        """Internal: Retrieve entity from memory."""
        pass

    async def _add_observation(self, entity_name: str, observation: str):
        """Internal: Add observation to existing entity."""
        pass

    async def _add_relation(
        self,
        from_entity: str,
        to_entity: str,
        relation_type: str
    ):
        """Internal: Create relation between entities."""
        pass
```

### Use Cases

#### 1. Remember User Preferences

```python
# User says: "I never want to risk more than 3% per trade"
await memory.store_preference('max_position_size', 0.03, 'User specified max risk')

# Later, in position sizing:
max_size = await memory.get_preference('max_position_size') or 0.05
```

#### 2. Ticker-Specific Notes

```python
# After a bad trade on TSLA
await memory.store_ticker_note(
    'TSLA',
    'Gaps unpredictably, use wider strikes',
    'lesson_learned'
)

# Before trading TSLA again
notes = await memory.get_ticker_notes('TSLA')
# Returns: ["[lesson_learned] Gaps unpredictably, use wider strikes"]
```

#### 3. Strategy Configurations

```python
# Save a working configuration
await memory.store_strategy_config('earnings_iron_condor_v2', {
    'min_vrp': 1.6,
    'min_edge_score': 65,
    'wing_width': 5,
    'dte_range': (7, 14)
})

# Load it for backtesting or live trading
config = await memory.get_strategy_config('earnings_iron_condor_v2')
```

#### 4. Session Continuity

```python
# If analysis is interrupted
await memory.store_session_context('bulk_research', {
    'watchlist': tickers,
    'completed': 25,
    'current_ticker': 'MSFT'
})

# On resume
context = await memory.get_session_context('bulk_research')
# Continue from where left off
```

### Integration with Sequential Thinking

Memory can provide context to Sequential Thinking for better decisions:

```python
async def analyze_with_context(self, ticker: str):
    # Get historical notes
    notes = await self.memory.get_ticker_notes(ticker)
    preferences = await self.memory.get_preference('risk_tolerance')

    # Include in Sequential Thinking prompt
    context = f"""
    Historical notes for {ticker}: {notes}
    User risk tolerance: {preferences}
    """

    # Better informed decision
    decision = await self.thinking.analyze_trade_opportunity(
        ticker_data,
        additional_context=context
    )
```

---

## Custom MCP Servers

### Trade History MCP Server

**File:** `mcp-servers/trades-history/server.py`

```python
"""
Trade History MCP Server - Query your trading database
"""
import os
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database path from environment
DB_PATH = os.getenv(
    'TRADES_DB_PATH',
    '$PROJECT_ROOT/2.0/data/ivcrush.db'
)

# Initialize server
server = Server("trades-history")

def get_db_connection():
    """Get SQLite connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@server.tool()
async def query_trades(
    symbol: Optional[str] = None,
    strategy_type: Optional[str] = None,
    status: Optional[str] = None,
    year: Optional[int] = None,
    min_pnl: Optional[float] = None
) -> Dict[str, Any]:
    """
    Query trades with filters.

    Args:
        symbol: Filter by ticker (e.g., 'AAPL')
        strategy_type: Filter by strategy (IRON_CONDOR, BULL_PUT_SPREAD, etc.)
        status: Filter by status (OPEN, CLOSED, STOPPED, EXPIRED)
        year: Filter by year
        min_pnl: Minimum P/L filter

    Returns:
        List of matching trades with all fields
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        query = """
            SELECT p.*,
                   GROUP_CONCAT(
                       pl.leg_type || ':' || pl.strike || '@' || pl.entry_price
                   ) as legs
            FROM positions p
            LEFT JOIN position_legs pl ON p.id = pl.position_id
            WHERE 1=1
        """
        params = []

        if symbol:
            query += " AND p.ticker = ?"
            params.append(symbol.upper())

        if strategy_type:
            query += " AND p.strategy_type = ?"
            params.append(strategy_type.upper())

        if status:
            query += " AND p.status = ?"
            params.append(status.upper())

        if year:
            query += " AND strftime('%Y', p.entry_date) = ?"
            params.append(str(year))

        if min_pnl is not None:
            query += " AND p.final_pnl >= ?"
            params.append(min_pnl)

        query += " GROUP BY p.id ORDER BY p.entry_date DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        trades = [dict(row) for row in rows]

        conn.close()

        return {
            "count": len(trades),
            "trades": trades
        }

    except Exception as e:
        logger.error(f"Error in query_trades: {e}")
        return {"error": str(e)}

@server.tool()
async def get_strategy_performance(
    strategy_type: Optional[str] = None,
    year: Optional[int] = None
) -> Dict[str, Any]:
    """
    Get performance metrics for strategies.

    Args:
        strategy_type: Specific strategy or None for all
        year: Specific year or None for all time

    Returns:
        Win rate, avg P/L, total trades, Sharpe ratio
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # First try performance_metrics table
        if strategy_type:
            cursor.execute("""
                SELECT * FROM performance_metrics
                WHERE metric_type = 'strategy' AND metric_key = ?
            """, (strategy_type.upper(),))
        else:
            cursor.execute("""
                SELECT * FROM performance_metrics
                WHERE metric_type = 'overall'
            """)

        row = cursor.fetchone()

        if row:
            result = dict(row)
        else:
            # Calculate from positions table
            query = """
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN win_loss = 'WIN' THEN 1 ELSE 0 END) as wins,
                    AVG(CASE WHEN win_loss = 'WIN' THEN final_pnl END) as avg_win,
                    AVG(CASE WHEN win_loss = 'LOSS' THEN final_pnl END) as avg_loss,
                    SUM(final_pnl) as total_pnl,
                    MAX(final_pnl) as largest_win,
                    MIN(final_pnl) as largest_loss
                FROM positions
                WHERE status = 'CLOSED'
            """
            params = []

            if strategy_type:
                query += " AND strategy_type = ?"
                params.append(strategy_type.upper())

            if year:
                query += " AND strftime('%Y', entry_date) = ?"
                params.append(str(year))

            cursor.execute(query, params)
            row = cursor.fetchone()

            total = row['total_trades'] or 0
            wins = row['wins'] or 0

            result = {
                "total_trades": total,
                "winning_trades": wins,
                "losing_trades": total - wins,
                "win_rate": (wins / total * 100) if total > 0 else 0,
                "avg_win": row['avg_win'] or 0,
                "avg_loss": row['avg_loss'] or 0,
                "total_pnl": row['total_pnl'] or 0,
                "largest_win": row['largest_win'] or 0,
                "largest_loss": row['largest_loss'] or 0
            }

        conn.close()
        return result

    except Exception as e:
        logger.error(f"Error in get_strategy_performance: {e}")
        return {"error": str(e)}

@server.tool()
async def get_open_positions() -> Dict[str, Any]:
    """
    Get all currently open positions.

    Returns:
        List of open positions with current P/L
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.*,
                   GROUP_CONCAT(
                       pl.leg_type || ':' || pl.strike || '@' || pl.entry_price
                   ) as legs
            FROM positions p
            LEFT JOIN position_legs pl ON p.id = pl.position_id
            WHERE p.status = 'OPEN'
            GROUP BY p.id
            ORDER BY p.expiration_date ASC
        """)

        rows = cursor.fetchall()
        positions = [dict(row) for row in rows]

        # Calculate total exposure
        total_risk = sum(p.get('max_loss', 0) for p in positions)
        total_credit = sum(p.get('credit_received', 0) for p in positions)

        conn.close()

        return {
            "count": len(positions),
            "total_risk": total_risk,
            "total_credit": total_credit,
            "positions": positions
        }

    except Exception as e:
        logger.error(f"Error in get_open_positions: {e}")
        return {"error": str(e)}

@server.tool()
async def get_symbol_history(symbol: str) -> Dict[str, Any]:
    """
    Get complete trading history for a symbol.

    Args:
        symbol: Ticker symbol

    Returns:
        All trades with summary statistics
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get all trades
        cursor.execute("""
            SELECT * FROM positions
            WHERE ticker = ?
            ORDER BY entry_date DESC
        """, (symbol.upper(),))

        trades = [dict(row) for row in cursor.fetchall()]

        # Calculate summary
        closed = [t for t in trades if t['status'] == 'CLOSED']
        wins = [t for t in closed if t.get('win_loss') == 'WIN']

        summary = {
            "symbol": symbol.upper(),
            "total_trades": len(trades),
            "closed_trades": len(closed),
            "open_trades": len([t for t in trades if t['status'] == 'OPEN']),
            "win_rate": (len(wins) / len(closed) * 100) if closed else 0,
            "total_pnl": sum(t.get('final_pnl', 0) or 0 for t in closed),
            "avg_pnl": (sum(t.get('final_pnl', 0) or 0 for t in closed) / len(closed)) if closed else 0
        }

        conn.close()

        return {
            "summary": summary,
            "trades": trades
        }

    except Exception as e:
        logger.error(f"Error in get_symbol_history: {e}")
        return {"error": str(e)}

@server.tool()
async def calculate_metrics(
    metric_type: str,
    strategy_type: Optional[str] = None,
    year: Optional[int] = None
) -> Dict[str, Any]:
    """
    Calculate specific performance metrics.

    Args:
        metric_type: 'sharpe', 'max_drawdown', 'win_rate', 'avg_profit'
        strategy_type: Filter by strategy
        year: Filter by year

    Returns:
        Calculated metric value
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get P/L series
        query = """
            SELECT final_pnl, entry_date
            FROM positions
            WHERE status = 'CLOSED' AND final_pnl IS NOT NULL
        """
        params = []

        if strategy_type:
            query += " AND strategy_type = ?"
            params.append(strategy_type.upper())

        if year:
            query += " AND strftime('%Y', entry_date) = ?"
            params.append(str(year))

        query += " ORDER BY entry_date"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        pnls = [row['final_pnl'] for row in rows]

        if not pnls:
            return {"error": "No data available"}

        result = {}

        if metric_type == 'sharpe':
            import statistics
            mean = statistics.mean(pnls)
            std = statistics.stdev(pnls) if len(pnls) > 1 else 1
            result['sharpe_ratio'] = (mean / std) * (252 ** 0.5) if std > 0 else 0

        elif metric_type == 'max_drawdown':
            cumsum = 0
            peak = 0
            max_dd = 0
            for pnl in pnls:
                cumsum += pnl
                if cumsum > peak:
                    peak = cumsum
                dd = peak - cumsum
                if dd > max_dd:
                    max_dd = dd
            result['max_drawdown'] = max_dd

        elif metric_type == 'win_rate':
            wins = len([p for p in pnls if p > 0])
            result['win_rate'] = (wins / len(pnls)) * 100

        elif metric_type == 'avg_profit':
            result['avg_profit'] = sum(pnls) / len(pnls)

        conn.close()
        return result

    except Exception as e:
        logger.error(f"Error in calculate_metrics: {e}")
        return {"error": str(e)}

# Run server
if __name__ == "__main__":
    import asyncio
    asyncio.run(stdio_server(server))
```

**File:** `mcp-servers/trades-history/requirements.txt`

```
mcp>=1.0.0
```

### Screening Results MCP Server

**File:** `mcp-servers/screening-results/server.py`

```python
"""
Screening Results MCP Server - Query your analysis and screening data
"""
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database path
DB_PATH = os.getenv(
    'SCREENING_DB_PATH',
    '$PROJECT_ROOT/2.0/data/ivcrush.db'
)

server = Server("screening-results")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@server.tool()
async def get_todays_candidates(
    min_edge_score: float = 50.0,
    limit: int = 20
) -> Dict[str, Any]:
    """
    Get candidates from the latest screening run.

    Args:
        min_edge_score: Minimum edge score filter (0-100)
        limit: Maximum number of results

    Returns:
        List of candidates ranked by edge score
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get latest analysis date
        cursor.execute("SELECT MAX(DATE(analyzed_at)) FROM analysis_log")
        latest_date = cursor.fetchone()[0]

        if not latest_date:
            return {"error": "No screening results found"}

        cursor.execute("""
            SELECT
                ticker,
                earnings_date,
                expiration,
                implied_move_pct,
                historical_mean_pct,
                vrp_ratio,
                edge_score,
                recommendation,
                confidence,
                analyzed_at
            FROM analysis_log
            WHERE DATE(analyzed_at) = ?
              AND edge_score >= ?
            ORDER BY edge_score DESC
            LIMIT ?
        """, (latest_date, min_edge_score, limit))

        candidates = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return {
            "screening_date": latest_date,
            "count": len(candidates),
            "min_edge_score": min_edge_score,
            "candidates": candidates
        }

    except Exception as e:
        logger.error(f"Error in get_todays_candidates: {e}")
        return {"error": str(e)}

@server.tool()
async def get_ticker_analysis(symbol: str) -> Dict[str, Any]:
    """
    Get the latest analysis for a specific ticker.

    Args:
        symbol: Ticker symbol

    Returns:
        Full analysis data including VRP, implied move, recommendation
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM analysis_log
            WHERE ticker = ?
            ORDER BY analyzed_at DESC
            LIMIT 1
        """, (symbol.upper(),))

        row = cursor.fetchone()

        if not row:
            return {"error": f"No analysis found for {symbol}"}

        analysis = dict(row)

        # Also get historical moves for context
        cursor.execute("""
            SELECT earnings_date, close_move_pct
            FROM historical_moves
            WHERE ticker = ?
            ORDER BY earnings_date DESC
            LIMIT 8
        """, (symbol.upper(),))

        moves = [dict(r) for r in cursor.fetchall()]
        analysis['historical_moves'] = moves

        conn.close()

        return analysis

    except Exception as e:
        logger.error(f"Error in get_ticker_analysis: {e}")
        return {"error": str(e)}

@server.tool()
async def get_upcoming_earnings(
    days_ahead: int = 14,
    min_edge_score: float = 50.0
) -> Dict[str, Any]:
    """
    Get candidates with earnings in the next X days.

    Args:
        days_ahead: Number of days to look ahead
        min_edge_score: Minimum edge score filter

    Returns:
        Candidates sorted by earnings date
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        today = datetime.now().date()
        end_date = today + timedelta(days=days_ahead)

        cursor.execute("""
            SELECT
                a.ticker,
                a.earnings_date,
                a.expiration,
                a.vrp_ratio,
                a.edge_score,
                a.recommendation,
                e.timing
            FROM analysis_log a
            LEFT JOIN earnings_calendar e
                ON a.ticker = e.ticker AND a.earnings_date = e.earnings_date
            WHERE a.earnings_date BETWEEN ? AND ?
              AND a.edge_score >= ?
            ORDER BY a.earnings_date ASC, a.edge_score DESC
        """, (today.isoformat(), end_date.isoformat(), min_edge_score))

        candidates = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return {
            "date_range": f"{today} to {end_date}",
            "count": len(candidates),
            "candidates": candidates
        }

    except Exception as e:
        logger.error(f"Error in get_upcoming_earnings: {e}")
        return {"error": str(e)}

@server.tool()
async def search_by_criteria(
    min_vrp_ratio: Optional[float] = None,
    max_vrp_ratio: Optional[float] = None,
    min_edge_score: Optional[float] = None,
    recommendation: Optional[str] = None,
    min_iv_percentile: Optional[float] = None
) -> Dict[str, Any]:
    """
    Search screening results by multiple criteria.

    Args:
        min_vrp_ratio: Minimum VRP ratio
        max_vrp_ratio: Maximum VRP ratio
        min_edge_score: Minimum edge score
        recommendation: Filter by recommendation type
        min_iv_percentile: Minimum IV percentile

    Returns:
        Matching candidates
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get latest screening date
        cursor.execute("SELECT MAX(DATE(analyzed_at)) FROM analysis_log")
        latest_date = cursor.fetchone()[0]

        query = """
            SELECT *
            FROM analysis_log
            WHERE DATE(analyzed_at) = ?
        """
        params = [latest_date]

        if min_vrp_ratio:
            query += " AND vrp_ratio >= ?"
            params.append(min_vrp_ratio)

        if max_vrp_ratio:
            query += " AND vrp_ratio <= ?"
            params.append(max_vrp_ratio)

        if min_edge_score:
            query += " AND edge_score >= ?"
            params.append(min_edge_score)

        if recommendation:
            query += " AND recommendation = ?"
            params.append(recommendation.upper())

        query += " ORDER BY edge_score DESC"

        cursor.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return {
            "screening_date": latest_date,
            "criteria": {
                "min_vrp_ratio": min_vrp_ratio,
                "max_vrp_ratio": max_vrp_ratio,
                "min_edge_score": min_edge_score,
                "recommendation": recommendation
            },
            "count": len(results),
            "results": results
        }

    except Exception as e:
        logger.error(f"Error in search_by_criteria: {e}")
        return {"error": str(e)}

@server.tool()
async def get_historical_screening(
    symbol: str,
    days_back: int = 90
) -> Dict[str, Any]:
    """
    Get historical screening results for a ticker.

    Args:
        symbol: Ticker symbol
        days_back: Number of days to look back

    Returns:
        Past screening results showing how scores evolved
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        start_date = datetime.now() - timedelta(days=days_back)

        cursor.execute("""
            SELECT
                earnings_date,
                expiration,
                vrp_ratio,
                edge_score,
                recommendation,
                implied_move_pct,
                historical_mean_pct,
                analyzed_at
            FROM analysis_log
            WHERE ticker = ?
              AND analyzed_at >= ?
            ORDER BY analyzed_at DESC
        """, (symbol.upper(), start_date.isoformat()))

        results = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return {
            "symbol": symbol.upper(),
            "period": f"Last {days_back} days",
            "count": len(results),
            "history": results
        }

    except Exception as e:
        logger.error(f"Error in get_historical_screening: {e}")
        return {"error": str(e)}

@server.tool()
async def get_historical_moves(
    symbol: str,
    limit: int = 12
) -> Dict[str, Any]:
    """
    Get actual historical earnings moves for a ticker.

    Args:
        symbol: Ticker symbol
        limit: Number of quarters to return

    Returns:
        Past earnings moves (gap, intraday, close)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                earnings_date,
                prev_close,
                earnings_open,
                earnings_close,
                gap_move_pct,
                intraday_move_pct,
                close_move_pct
            FROM historical_moves
            WHERE ticker = ?
            ORDER BY earnings_date DESC
            LIMIT ?
        """, (symbol.upper(), limit))

        moves = [dict(row) for row in cursor.fetchall()]

        # Calculate statistics
        if moves:
            close_moves = [abs(m['close_move_pct']) for m in moves]
            import statistics
            stats = {
                "avg_move": statistics.mean(close_moves),
                "std_dev": statistics.stdev(close_moves) if len(close_moves) > 1 else 0,
                "max_move": max(close_moves),
                "min_move": min(close_moves)
            }
        else:
            stats = {}

        conn.close()

        return {
            "symbol": symbol.upper(),
            "quarters": len(moves),
            "statistics": stats,
            "moves": moves
        }

    except Exception as e:
        logger.error(f"Error in get_historical_moves: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    import asyncio
    asyncio.run(stdio_server(server))
```

**File:** `mcp-servers/screening-results/requirements.txt`

```
mcp>=1.0.0
```

---

## Container Modifications

**File:** `3.0/src/container.py` (additions)

```python
# Add to imports
from src.infrastructure.api.mcp_adapters.alpha_vantage_mcp import AlphaVantageMCPAdapter
from src.infrastructure.api.mcp_adapters.yahoo_finance_mcp import YahooFinanceMCPAdapter
from src.infrastructure.api.mcp_adapters.sequential_thinking_mcp import SequentialThinkingProvider
from src.infrastructure.api.mcp_adapters.octagon_mcp import OctagonResearchProvider
from src.infrastructure.api.mcp_adapters.composer_mcp import ComposerBacktestProvider
from src.infrastructure.api.mcp_adapters.alpaca_mcp import AlpacaPaperTradingProvider
from src.infrastructure.api.mcp_adapters.sentiment_indicators_mcp import (
    NewsSentimentProvider,
    TechnicalIndicatorsProvider
)
from src.infrastructure.api.mcp_adapters.memory_mcp import TradingMemoryProvider
from src.infrastructure.cache.unified_cache import UnifiedCache

class Container:
    def __init__(self, config: Config):
        self._config = config
        self._use_mcp = config.use_mcp

        # Cached provider instances (lazy initialization)
        self._unified_cache = None
        self._alphavantage = None
        self._yahoo_finance = None
        self._thinking = None
        self._research = None
        self._backtester = None
        self._paper_trading = None
        self._sentiment = None
        self._technicals = None
        self._memory = None
        # ... existing init

    @property
    def unified_cache(self) -> UnifiedCache:
        if self._unified_cache is None:
            self._unified_cache = UnifiedCache(
                db_path=self._config.mcp_cache_path,
                config=self._config
            )
        return self._unified_cache

    @property
    def alphavantage(self):
        """Alpha Vantage API - MCP or direct."""
        if self._alphavantage is None:
            if self._use_mcp:
                self._alphavantage = AlphaVantageMCPAdapter(cache=self.unified_cache)
            else:
                # Original implementation (fallback)
                self._alphavantage = self._create_alphavantage_api()
        return self._alphavantage

    @property
    def yahoo_finance(self) -> YahooFinanceMCPAdapter:
        """Yahoo Finance MCP adapter."""
        if self._yahoo_finance is None:
            self._yahoo_finance = YahooFinanceMCPAdapter(cache=self.unified_cache)
        return self._yahoo_finance

    @property
    def thinking(self) -> SequentialThinkingProvider:
        """Sequential Thinking for complex decisions."""
        if self._thinking is None:
            self._thinking = SequentialThinkingProvider()
        return self._thinking

    @property
    def research(self) -> OctagonResearchProvider:
        """Octagon research provider."""
        if self._research is None:
            self._research = OctagonResearchProvider(cache=self.unified_cache)
        return self._research

    @property
    def backtester(self) -> ComposerBacktestProvider:
        """Composer backtesting provider."""
        if self._backtester is None:
            self._backtester = ComposerBacktestProvider()
        return self._backtester

    @property
    def paper_trading(self) -> AlpacaPaperTradingProvider:
        """Alpaca paper trading."""
        if self._paper_trading is None:
            self._paper_trading = AlpacaPaperTradingProvider()
        return self._paper_trading

    @property
    def sentiment(self) -> NewsSentimentProvider:
        """News sentiment analysis."""
        if self._sentiment is None:
            self._sentiment = NewsSentimentProvider(cache=self.unified_cache)
        return self._sentiment

    @property
    def technicals(self) -> TechnicalIndicatorsProvider:
        """Technical indicators (RSI, BBANDS, ATR, MACD)."""
        if self._technicals is None:
            self._technicals = TechnicalIndicatorsProvider(cache=self.unified_cache)
        return self._technicals

    @property
    def memory(self) -> TradingMemoryProvider:
        """Persistent memory for preferences and notes."""
        if self._memory is None:
            self._memory = TradingMemoryProvider()
        return self._memory
```

---

## Cache Strategy

### Unified Cache Implementation

**File:** `3.0/src/infrastructure/cache/unified_cache.py`

```python
"""
Unified Cache - Single cache for all data sources with type-based TTLs
"""
import sqlite3
import pickle
import time
from typing import Any, Optional
from threading import Lock

class UnifiedCache:
    """
    Two-tier cache with data-type specific TTLs.
    L1: In-memory (fast, limited size)
    L2: SQLite (persistent, unlimited)
    """

    DEFAULT_TTLS = {
        'earnings': 21600,      # 6 hours
        'prices': 300,          # 5 minutes
        'market_cap': 86400,    # 24 hours
        'transcript': 604800,   # 7 days
        'holdings': 86400,      # 24 hours
        'default': 3600         # 1 hour
    }

    def __init__(self, db_path: str, config=None):
        self._db_path = db_path
        self._l1_cache = {}
        self._l1_timestamps = {}
        self._l1_max_size = 1000
        self._l1_ttl = 30  # 30 seconds for L1
        self._lock = Lock()

        # Override TTLs from config if provided
        if config:
            self.DEFAULT_TTLS.update({
                'earnings': config.mcp_cache_ttl_earnings,
                'transcript': config.mcp_cache_ttl_transcript,
                'market_cap': config.mcp_cache_ttl_marketcap
            })

        self._init_db()

    def _init_db(self):
        """Initialize SQLite cache table with optimized settings."""
        conn = sqlite3.connect(self._db_path)

        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        # Set busy timeout to avoid SQLITE_BUSY errors
        conn.execute("PRAGMA busy_timeout=5000")
        # Optimize for performance
        conn.execute("PRAGMA synchronous=NORMAL")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS mcp_cache (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                data_type TEXT,
                timestamp REAL,
                ttl INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_cache_ts ON mcp_cache(timestamp)")
        conn.commit()
        conn.close()

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache (L1 then L2)."""
        # Check L1
        with self._lock:
            if key in self._l1_cache:
                if time.time() - self._l1_timestamps[key] < self._l1_ttl:
                    return self._l1_cache[key]
                else:
                    del self._l1_cache[key]
                    del self._l1_timestamps[key]

        # Check L2
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value, timestamp, ttl FROM mcp_cache WHERE key = ?",
            (key,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            value, timestamp, ttl = row
            if time.time() - timestamp < ttl:
                result = pickle.loads(value)
                # Promote to L1
                self._set_l1(key, result)
                return result

        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None, data_type: str = 'default'):
        """Set value in cache with TTL."""
        if ttl is None:
            ttl = self.DEFAULT_TTLS.get(data_type, self.DEFAULT_TTLS['default'])

        # Set in L1
        self._set_l1(key, value)

        # Set in L2
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            INSERT OR REPLACE INTO mcp_cache (key, value, data_type, timestamp, ttl)
            VALUES (?, ?, ?, ?, ?)
        """, (key, pickle.dumps(value), data_type, time.time(), ttl))
        conn.commit()
        conn.close()

    def _set_l1(self, key: str, value: Any):
        """Set value in L1 cache with eviction."""
        with self._lock:
            if len(self._l1_cache) >= self._l1_max_size:
                # Evict oldest
                oldest_key = min(self._l1_timestamps, key=self._l1_timestamps.get)
                del self._l1_cache[oldest_key]
                del self._l1_timestamps[oldest_key]

            self._l1_cache[key] = value
            self._l1_timestamps[key] = time.time()

    def clear_expired(self):
        """Remove expired entries from L2."""
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "DELETE FROM mcp_cache WHERE timestamp + ttl < ?",
            (time.time(),)
        )
        conn.commit()
        conn.close()
```

---

## Configuration

### Environment Variables

**File:** `3.0/.env` (additions)

```bash
# MCP Integration
USE_MCP=true
USE_SEQUENTIAL_THINKING=true
USE_COMPOSER_BACKTEST=true

# MCP Cache Configuration
MCP_CACHE_PATH=data/mcp_cache.db
MCP_CACHE_TTL_EARNINGS=21600      # 6 hours
MCP_CACHE_TTL_MARKETCAP=86400     # 24 hours
MCP_CACHE_TTL_TRANSCRIPT=604800   # 7 days

# Database paths for custom MCPs
TRADES_DB_PATH=$PROJECT_ROOT/3.0/data/ivcrush.db
SCREENING_DB_PATH=$PROJECT_ROOT/3.0/data/ivcrush.db
```

### Config Class Updates

**File:** `3.0/src/config/config.py` (additions)

```python
@dataclass
class MCPConfig:
    """MCP integration configuration."""
    use_mcp: bool = True
    use_sequential_thinking: bool = True
    use_composer_backtest: bool = True
    cache_path: str = "data/mcp_cache.db"
    cache_ttl_earnings: int = 21600
    cache_ttl_marketcap: int = 86400
    cache_ttl_transcript: int = 604800

    @classmethod
    def from_env(cls) -> 'MCPConfig':
        return cls(
            use_mcp=os.getenv('USE_MCP', 'true').lower() == 'true',
            use_sequential_thinking=os.getenv('USE_SEQUENTIAL_THINKING', 'true').lower() == 'true',
            use_composer_backtest=os.getenv('USE_COMPOSER_BACKTEST', 'true').lower() == 'true',
            cache_path=os.getenv('MCP_CACHE_PATH', 'data/mcp_cache.db'),
            cache_ttl_earnings=int(os.getenv('MCP_CACHE_TTL_EARNINGS', 21600)),
            cache_ttl_marketcap=int(os.getenv('MCP_CACHE_TTL_MARKETCAP', 86400)),
            cache_ttl_transcript=int(os.getenv('MCP_CACHE_TTL_TRANSCRIPT', 604800))
        )

# Add to main Config class
@dataclass
class Config:
    # ... existing fields
    mcp: MCPConfig = field(default_factory=MCPConfig)
```

### Claude Code MCP Configuration

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "trades-history": {
      "type": "stdio",
      "command": "python",
      "args": ["$PROJECT_ROOT/mcp-servers/trades-history/server.py"],
      "env": {
        "TRADES_DB_PATH": "$PROJECT_ROOT/3.0/data/ivcrush.db"
      }
    },
    "screening-results": {
      "type": "stdio",
      "command": "python",
      "args": ["$PROJECT_ROOT/mcp-servers/screening-results/server.py"],
      "env": {
        "SCREENING_DB_PATH": "$PROJECT_ROOT/3.0/data/ivcrush.db"
      }
    }
  }
}
```

---

## Script Modifications

### scan.py Changes

**File:** `3.0/scripts/scan.py`

Replace yfinance calls with MCP adapter:

```python
# Line 47-52: Replace yfinance import
# OLD:
# import yfinance as yf

# NEW:
# yfinance replaced by Yahoo Finance MCP adapter in container

# Lines 61-91: Replace get_market_cap_millions function
def get_market_cap_millions(ticker: str, container) -> float | None:
    """Get market cap using Yahoo Finance MCP adapter."""
    try:
        return container.yahoo_finance.get_market_cap(ticker)
    except Exception as e:
        logger.warning(f"Failed to get market cap for {ticker}: {e}")
        return None
```

### trade.sh Additions

**File:** `3.0/trade.sh`

Add new commands:

```bash
# Add to case statement (around line 563)

backtest)
    shift
    python scripts/backtest_mcp.py "$@"
    ;;

optimize)
    shift
    python scripts/backtest_mcp.py optimize "$@"
    ;;

paper)
    shift
    python scripts/paper_trade.py "$@"
    ;;
```

---

## Octagon Trial Maximization

### Strategy: Aggressive Data Collection During 2-Week Trial

During the Octagon Pro trial period, aggressively analyze all 50+ watchlist tickers and persist everything to the database. This creates a permanent research archive that remains valuable after the trial ends.

### Database Schema Additions

**Add to `ivcrush.db`:**

```sql
-- Earnings transcript summaries
CREATE TABLE octagon_transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    quarter TEXT NOT NULL,  -- e.g., '2024Q3'

    -- Transcript analysis
    revenue_guidance TEXT,      -- 'beat', 'miss', 'inline', 'raised', 'lowered'
    margin_outlook TEXT,        -- 'expanding', 'stable', 'contracting'
    key_risks TEXT,             -- JSON array of risk factors
    analyst_highlights TEXT,    -- Key Q&A points
    management_tone TEXT,       -- 'bullish', 'neutral', 'cautious', 'bearish'

    -- Raw content
    full_summary TEXT,          -- Complete Octagon analysis

    -- Metadata
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(ticker, quarter)
);

CREATE INDEX idx_transcript_ticker ON octagon_transcripts(ticker);
CREATE INDEX idx_transcript_quarter ON octagon_transcripts(quarter);

-- Institutional holdings (13F data)
CREATE TABLE octagon_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    report_date DATE NOT NULL,  -- 13F filing date

    -- Holdings data
    top_holders TEXT,           -- JSON array of top 10 institutions
    total_institutional_pct REAL,
    qoq_change_pct REAL,        -- Quarter-over-quarter change
    notable_changes TEXT,       -- JSON: new positions, exits, increases

    -- Raw content
    full_analysis TEXT,

    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(ticker, report_date)
);

CREATE INDEX idx_holdings_ticker ON octagon_holdings(ticker);

-- Deep research cache
CREATE TABLE octagon_research (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    research_type TEXT NOT NULL,  -- 'comprehensive', 'competitor', 'sector'

    -- Research content
    query TEXT,                 -- Original query
    analysis TEXT,              -- Full Octagon response
    key_insights TEXT,          -- JSON array of main points

    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_research_ticker ON octagon_research(ticker);
CREATE INDEX idx_research_type ON octagon_research(research_type);
```

### Bulk Research Script

**File:** `3.0/scripts/octagon_bulk_research.py`

```python
"""
Octagon Bulk Research - Maximize trial by analyzing all watchlist tickers

Usage:
    python scripts/octagon_bulk_research.py --watchlist data/watchlist.txt
    python scripts/octagon_bulk_research.py --watchlist data/watchlist.txt --transcripts-only
    python scripts/octagon_bulk_research.py --ticker NVDA --full
"""

import asyncio
import argparse
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

from src.container import Container

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Quarters to fetch (last 8 quarters = 2 years)
QUARTERS_TO_FETCH = [
    '2024Q3', '2024Q2', '2024Q1', '2024Q4',
    '2023Q3', '2023Q2', '2023Q1', '2023Q4'
]

class OctagonBulkResearcher:
    """Bulk research using Octagon MCP during trial period."""

    def __init__(self, container: Container, db_path: str):
        self.container = container
        self.db_path = db_path
        self.research = container.research

    async def analyze_watchlist(
        self,
        tickers: list[str],
        include_transcripts: bool = True,
        include_holdings: bool = True,
        include_deep_research: bool = False
    ):
        """
        Analyze all tickers in watchlist.

        Args:
            tickers: List of ticker symbols
            include_transcripts: Fetch earnings transcripts
            include_holdings: Fetch institutional holdings
            include_deep_research: Run comprehensive analysis
        """
        total = len(tickers)
        logger.info(f"Starting bulk analysis of {total} tickers")

        for i, ticker in enumerate(tickers, 1):
            logger.info(f"[{i}/{total}] Analyzing {ticker}")

            try:
                if include_transcripts:
                    await self._fetch_transcripts(ticker)

                if include_holdings:
                    await self._fetch_holdings(ticker)

                if include_deep_research:
                    await self._fetch_deep_research(ticker)

                # Rate limiting - be respectful during trial
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error analyzing {ticker}: {e}")
                continue

        logger.info(f"Completed bulk analysis of {total} tickers")

    async def _fetch_transcripts(self, ticker: str):
        """Fetch and store earnings transcripts for multiple quarters."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        for quarter in QUARTERS_TO_FETCH:
            # Check if already fetched
            cursor.execute(
                "SELECT 1 FROM octagon_transcripts WHERE ticker = ? AND quarter = ?",
                (ticker, quarter)
            )
            if cursor.fetchone():
                logger.debug(f"{ticker} {quarter} already in database")
                continue

            try:
                # Fetch from Octagon
                summary = await self.research.get_earnings_transcript(ticker, quarter)

                if not summary or 'error' in summary.lower():
                    continue

                # Parse key fields (Octagon returns structured text)
                parsed = self._parse_transcript(summary)

                # Store in database
                cursor.execute("""
                    INSERT INTO octagon_transcripts
                    (ticker, quarter, revenue_guidance, margin_outlook,
                     key_risks, analyst_highlights, management_tone, full_summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ticker, quarter,
                    parsed.get('revenue_guidance'),
                    parsed.get('margin_outlook'),
                    json.dumps(parsed.get('key_risks', [])),
                    parsed.get('analyst_highlights'),
                    parsed.get('management_tone'),
                    summary
                ))

                logger.info(f"  Stored transcript: {ticker} {quarter}")

                # Small delay between quarters
                await asyncio.sleep(1)

            except Exception as e:
                logger.warning(f"  Failed {ticker} {quarter}: {e}")

        conn.commit()
        conn.close()

    async def _fetch_holdings(self, ticker: str):
        """Fetch and store institutional holdings."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            holdings = await self.research.get_institutional_holdings(ticker)

            if not holdings or isinstance(holdings, str) and 'error' in holdings.lower():
                return

            # Store in database
            cursor.execute("""
                INSERT OR REPLACE INTO octagon_holdings
                (ticker, report_date, top_holders, total_institutional_pct,
                 qoq_change_pct, notable_changes, full_analysis)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                datetime.now().date().isoformat(),
                json.dumps(holdings.get('top_holders', [])),
                holdings.get('institutional_pct'),
                holdings.get('qoq_change'),
                json.dumps(holdings.get('notable_changes', [])),
                json.dumps(holdings) if isinstance(holdings, dict) else str(holdings)
            ))

            conn.commit()
            logger.info(f"  Stored holdings: {ticker}")

        except Exception as e:
            logger.warning(f"  Failed holdings {ticker}: {e}")
        finally:
            conn.close()

    async def _fetch_deep_research(self, ticker: str):
        """Run comprehensive deep research analysis."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        queries = [
            (
                'comprehensive',
                f"Provide a comprehensive analysis of {ticker} including: "
                f"business model, competitive advantages, key risks, "
                f"recent earnings trends, and outlook for the next quarter."
            ),
            (
                'earnings_history',
                f"Analyze {ticker}'s earnings history over the last 2 years. "
                f"What is their beat/miss rate? How does the stock typically "
                f"react to earnings? Any patterns in guidance?"
            )
        ]

        for research_type, query in queries:
            try:
                analysis = await self.research.deep_research(query)

                if not analysis:
                    continue

                cursor.execute("""
                    INSERT INTO octagon_research
                    (ticker, research_type, query, analysis, key_insights)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    ticker, research_type, query, analysis,
                    json.dumps(self._extract_insights(analysis))
                ))

                logger.info(f"  Stored research: {ticker} ({research_type})")

                await asyncio.sleep(2)

            except Exception as e:
                logger.warning(f"  Failed research {ticker}: {e}")

        conn.commit()
        conn.close()

    def _parse_transcript(self, summary: str) -> dict:
        """Parse Octagon transcript summary into structured fields."""
        # Simple keyword-based parsing
        result = {}

        summary_lower = summary.lower()

        # Revenue guidance
        if 'beat' in summary_lower and 'revenue' in summary_lower:
            result['revenue_guidance'] = 'beat'
        elif 'miss' in summary_lower and 'revenue' in summary_lower:
            result['revenue_guidance'] = 'miss'
        elif 'raised' in summary_lower and 'guidance' in summary_lower:
            result['revenue_guidance'] = 'raised'
        elif 'lowered' in summary_lower and 'guidance' in summary_lower:
            result['revenue_guidance'] = 'lowered'

        # Management tone
        if any(word in summary_lower for word in ['optimistic', 'confident', 'strong']):
            result['management_tone'] = 'bullish'
        elif any(word in summary_lower for word in ['cautious', 'uncertain', 'challenging']):
            result['management_tone'] = 'cautious'
        elif any(word in summary_lower for word in ['concern', 'weak', 'decline']):
            result['management_tone'] = 'bearish'
        else:
            result['management_tone'] = 'neutral'

        return result

    def _extract_insights(self, analysis: str) -> list:
        """Extract key bullet points from analysis."""
        # Simple extraction - look for numbered points or bullet patterns
        insights = []
        for line in analysis.split('\n'):
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith('-') or line.startswith('•')):
                insights.append(line.lstrip('0123456789.-•) '))
        return insights[:10]  # Top 10 insights

def load_watchlist(filepath: str) -> list[str]:
    """Load tickers from watchlist file."""
    with open(filepath, 'r') as f:
        tickers = [line.strip().upper() for line in f if line.strip()]
    return tickers

async def main():
    parser = argparse.ArgumentParser(description='Bulk Octagon research')
    parser.add_argument('--watchlist', type=str, help='Path to watchlist file')
    parser.add_argument('--ticker', type=str, help='Single ticker to analyze')
    parser.add_argument('--transcripts-only', action='store_true')
    parser.add_argument('--holdings-only', action='store_true')
    parser.add_argument('--full', action='store_true', help='Include deep research')
    parser.add_argument('--db', type=str, default='data/ivcrush.db')

    args = parser.parse_args()

    # Initialize
    container = Container.from_env()
    researcher = OctagonBulkResearcher(container, args.db)

    # Get tickers
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.watchlist:
        tickers = load_watchlist(args.watchlist)
    else:
        print("Provide --watchlist or --ticker")
        return

    # Run analysis
    await researcher.analyze_watchlist(
        tickers,
        include_transcripts=not args.holdings_only,
        include_holdings=not args.transcripts_only,
        include_deep_research=args.full
    )

if __name__ == "__main__":
    asyncio.run(main())
```

### Watchlist File Format

**File:** `3.0/data/watchlist.txt`

```
AAPL
MSFT
NVDA
TSLA
AMZN
GOOGL
META
NFLX
AMD
CRM
... (50+ tickers)
```

### Trial Execution Strategy

#### Week 1: Core Data Collection

```bash
# Day 1-2: Transcripts for all tickers (8 quarters each)
python scripts/octagon_bulk_research.py --watchlist data/watchlist.txt --transcripts-only

# Day 3-4: Institutional holdings
python scripts/octagon_bulk_research.py --watchlist data/watchlist.txt --holdings-only

# Day 5-7: Deep research for top 20 tickers
python scripts/octagon_bulk_research.py --watchlist data/top_20.txt --full
```

#### Week 2: Targeted Research

```bash
# Analyze any new tickers added to watchlist
python scripts/octagon_bulk_research.py --ticker NEW_TICKER --full

# Re-analyze tickers with upcoming earnings
python scripts/octagon_bulk_research.py --watchlist data/upcoming_earnings.txt --full

# Fill in gaps
python scripts/octagon_bulk_research.py --watchlist data/watchlist.txt --holdings-only
```

### Data Retention After Trial

After the trial ends:

1. **All data persists** in SQLite database
2. **Cache layer** returns stored data for known tickers
3. **New tickers** will fail gracefully (no Octagon access)
4. **Consider upgrading** if value is proven

### Integration with Existing System

**Modify `analyze.py` to use cached Octagon data:**

```python
# In TickerAnalyzer.analyze()
def get_octagon_context(self, ticker: str) -> dict:
    """Get cached Octagon research if available."""
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()

    # Get latest transcript
    cursor.execute("""
        SELECT quarter, revenue_guidance, management_tone, full_summary
        FROM octagon_transcripts
        WHERE ticker = ?
        ORDER BY quarter DESC
        LIMIT 1
    """, (ticker,))
    transcript = cursor.fetchone()

    # Get holdings
    cursor.execute("""
        SELECT total_institutional_pct, qoq_change_pct, notable_changes
        FROM octagon_holdings
        WHERE ticker = ?
        ORDER BY report_date DESC
        LIMIT 1
    """, (ticker,))
    holdings = cursor.fetchone()

    conn.close()

    return {
        'transcript': dict(transcript) if transcript else None,
        'holdings': dict(holdings) if holdings else None
    }
```

### Expected Data Volume

For 50 tickers:
- **Transcripts:** 50 tickers × 8 quarters = 400 records
- **Holdings:** 50 tickers × 1 snapshot = 50 records
- **Deep research:** 20 tickers × 2 types = 40 records

**Total:** ~490 records of permanent research data

### trade.sh Command

```bash
# Add to trade.sh
octagon)
    shift
    python scripts/octagon_bulk_research.py "$@"
    ;;
```

Usage:
```bash
./trade.sh octagon --watchlist data/watchlist.txt
./trade.sh octagon --ticker NVDA --full
```

---

## Testing Strategy

### Unit Tests

```bash
# Test MCP adapters
pytest tests/unit/test_mcp_adapters.py

# Test cache
pytest tests/unit/test_unified_cache.py

# Test custom MCP servers
pytest tests/unit/test_mcp_servers.py
```

### Integration Tests

```bash
# Test with USE_MCP=false (original behavior)
USE_MCP=false ./trade.sh whisper
USE_MCP=false ./trade.sh list AAPL,NVDA

# Test with USE_MCP=true (MCP behavior)
USE_MCP=true ./trade.sh whisper
USE_MCP=true ./trade.sh list AAPL,NVDA

# Compare outputs - should be identical
```

### Manual Testing Checklist

- [ ] Whisper mode returns same candidates
- [ ] Ticker mode analyzes correctly
- [ ] Scanning mode filters properly
- [ ] Single ticker analysis works
- [ ] Cache TTLs are respected
- [ ] Fallback to direct API works when MCP fails
- [ ] Custom MCP servers respond correctly
- [ ] trade.sh grep patterns still work

---

## Phased Rollout

### Phase 1: Foundation (Week 1)
- Copy 2.0 to 3.0
- Create unified cache
- Create MCP adapter interfaces
- Test cache independently

### Phase 2: Data Adapters (Week 2)
- Implement Alpha Vantage MCP adapter
- Implement Yahoo Finance MCP adapter
- Update Container
- Test data flow

### Phase 3: Intelligence (Week 3)
- Implement Sequential Thinking adapter
- Implement Octagon adapter
- Integrate into analyze.py
- Test reasoning quality

### Phase 4: Validation (Week 4)
- Implement Composer adapter
- Create backtest_mcp.py script
- Test parameter optimization
- Validate against existing backtests

### Phase 5: Execution (Week 5)
- Implement Alpaca adapter
- Create paper_trade.py script
- Test order flow
- Integrate with positions table

### Phase 6: Custom MCPs (Week 6)
- Build trades-history server
- Build screening-results server
- Configure in Claude Code
- Test conversational queries

### Phase 7: Polish (Week 7)
- Documentation
- Error handling
- Performance optimization
- Final testing

---

## Rollback Procedures

### Quick Rollback
```bash
# Set environment variable
export USE_MCP=false

# All modes revert to 2.0 behavior
./trade.sh whisper  # Uses direct APIs
```

### Full Rollback
```bash
# Switch back to 2.0
cd "$PROJECT_ROOT/2.0"
./trade.sh whisper
```

### Partial Rollback
```bash
# Disable specific MCPs
export USE_SEQUENTIAL_THINKING=false
export USE_COMPOSER_BACKTEST=false
export USE_MCP=true  # Keep data MCPs

./trade.sh whisper  # Uses MCP for data, simple logic for decisions
```

---

## Success Criteria

### Functional
- [ ] All trade.sh modes produce identical results
- [ ] Custom MCPs answer queries correctly
- [ ] Sequential Thinking improves decision quality
- [ ] Backtesting validates strategy

### Performance
- [ ] No increase in analysis time
- [ ] Cache hit rate > 80%
- [ ] Free tier limits never exceeded

### Quality
- [ ] Win rate improves with better reasoning
- [ ] Parameters optimized via backtesting
- [ ] Trade decisions are explainable
