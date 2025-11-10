"""
Type definitions for trading desk data structures.

Provides TypedDict definitions for all major data structures used throughout
the application, enabling better type safety and IDE support.
"""

from typing import TypedDict, Optional, List, Dict, Literal


class OptionsData(TypedDict, total=False):
    """
    Options data for a ticker.

    Contains IV metrics, liquidity data, and earnings-related statistics.
    Used throughout the analysis pipeline.
    """
    # IV Metrics
    current_iv: float  # Current implied volatility (percentage)
    iv_rank: float  # IV Rank from ORATS (0-100 percentile)
    iv_percentile: float  # IV Percentile

    # Expected Move
    expected_move_pct: float  # Expected move % based on ATM straddle
    avg_actual_move_pct: Optional[float]  # Historical actual move average

    # Liquidity Metrics
    options_volume: int  # Total daily options volume
    open_interest: int  # Total open interest
    bid_ask_spread_pct: Optional[float]  # Bid-ask spread as percentage

    # IV Crush Metrics
    iv_crush_ratio: Optional[float]  # Ratio of implied to actual move (>1.0 = edge)

    # Data Source
    data_source: str  # 'tradier', 'yfinance', or 'manual'

    # Additional Metadata
    expiration: Optional[str]  # Expiration date used for calculations
    last_updated: Optional[str]  # Timestamp of data fetch


class OptionContract(TypedDict, total=False):
    """
    Individual option contract data.

    Represents a single call or put option with pricing and Greeks.
    """
    # Basic Info
    symbol: str  # Option symbol (e.g., "AAPL250117C00150000")
    underlying: str  # Underlying ticker
    strike: float  # Strike price
    expiration: str  # Expiration date (YYYY-MM-DD)
    option_type: Literal['call', 'put']  # Option type

    # Pricing
    bid: float  # Bid price
    ask: float  # Ask price
    last: float  # Last traded price
    mark: Optional[float]  # Mid-point price

    # Volume & Interest
    volume: int  # Daily volume
    open_interest: int  # Open interest

    # Greeks
    implied_volatility: float  # IV for this specific contract
    delta: Optional[float]  # Delta
    gamma: Optional[float]  # Gamma
    theta: Optional[float]  # Theta
    vega: Optional[float]  # Vega
    rho: Optional[float]  # Rho

    # Calculated Metrics
    intrinsic_value: Optional[float]  # Intrinsic value
    extrinsic_value: Optional[float]  # Time value
    bid_ask_spread: Optional[float]  # Spread in dollars
    bid_ask_spread_pct: Optional[float]  # Spread as percentage of mark


class TickerData(TypedDict, total=False):
    """
    Complete ticker data including stock info, options data, and score.

    Main data structure passed through the analysis pipeline.
    """
    # Basic Stock Info
    ticker: str  # Ticker symbol
    price: float  # Current stock price
    market_cap: float  # Market capitalization
    volume: int  # Stock volume (not options volume)

    # Options Data
    options_data: OptionsData  # Options metrics
    iv: Optional[float]  # yfinance IV fallback (decimal format, not percentage)

    # Analysis Results
    score: float  # Composite score (0-100)

    # Additional Info
    sector: Optional[str]  # Company sector
    industry: Optional[str]  # Company industry
    earnings_date: Optional[str]  # Earnings date (YYYY-MM-DD)

    # ATM Options (for strategy generation)
    atm_call: Optional[OptionContract]  # At-the-money call
    atm_put: Optional[OptionContract]  # At-the-money put


class SentimentData(TypedDict, total=False):
    """
    Sentiment analysis results for a ticker.

    Aggregates retail, institutional, and hedge fund sentiment.
    """
    overall_sentiment: str  # 'bullish', 'bearish', 'neutral', 'unavailable'
    confidence: Optional[float]  # Confidence score (0-100)

    # Detailed Sentiment
    retail_sentiment: Optional[str]  # Sentiment from retail traders
    institutional_sentiment: Optional[str]  # Institutional positioning
    hedge_fund_sentiment: Optional[str]  # Hedge fund positioning

    # Sources
    sources: Optional[List[str]]  # Data sources used

    # Metadata
    summary: Optional[str]  # Natural language summary
    error: Optional[str]  # Error message if analysis failed
    note: Optional[str]  # Additional notes


class StrategyData(TypedDict, total=False):
    """
    Trading strategy details.

    Represents a specific options strategy with entry/exit criteria.
    """
    name: str  # Strategy name (e.g., "Iron Condor", "Straddle")
    type: str  # Strategy type (e.g., "neutral", "directional")

    # Position Details
    strikes: List[float]  # Strike prices involved
    legs: List[Dict]  # Individual option legs

    # Risk/Reward
    max_profit: Optional[float]  # Maximum profit potential
    max_loss: Optional[float]  # Maximum loss potential
    breakeven: Optional[List[float]]  # Breakeven points

    # Position Sizing
    contracts: Optional[int]  # Number of contracts
    capital_required: Optional[float]  # Capital required

    # Trade Management
    entry_criteria: Optional[str]  # Entry conditions
    exit_criteria: Optional[str]  # Exit conditions
    risk_management: Optional[str]  # Risk management notes

    # Rationale
    rationale: Optional[str]  # Why this strategy was recommended


class AnalysisResult(TypedDict, total=False):
    """
    Complete analysis result for a ticker.

    Final output structure containing all analysis components.
    """
    # Basic Info
    ticker: str  # Ticker symbol
    earnings_date: str  # Earnings date (YYYY-MM-DD)
    price: float  # Current stock price

    # Scoring
    score: float  # Composite score (0-100)

    # Options Data
    options_data: OptionsData  # Options metrics

    # Analysis Components
    sentiment: SentimentData  # Sentiment analysis
    strategies: List[StrategyData]  # Recommended strategies

    # Metadata
    analyzed_at: Optional[str]  # Timestamp of analysis
    analysis_version: Optional[str]  # Version of analysis logic used

    # Error Handling
    errors: Optional[List[str]]  # Any errors encountered during analysis
    warnings: Optional[List[str]]  # Warnings or notes


class EarningsCalendarEntry(TypedDict, total=False):
    """
    Earnings calendar entry for a single ticker.

    Represents upcoming or past earnings announcements.
    """
    ticker: str  # Ticker symbol
    earnings_date: str  # Date of earnings (YYYY-MM-DD)
    earnings_time: Optional[str]  # 'bmo' (before market open) or 'amc' (after market close)
    fiscal_period: Optional[str]  # Fiscal quarter/year (e.g., "Q4 2024")
    estimate: Optional[float]  # EPS estimate
    actual: Optional[float]  # Actual EPS (after announcement)
    surprise_pct: Optional[float]  # Earnings surprise percentage


class UsageRecord(TypedDict, total=False):
    """
    API usage tracking record.

    Tracks costs and usage for budget management.
    """
    provider: str  # API provider name
    operation: str  # Operation type (e.g., "sentiment_analysis", "options_fetch")
    cost: float  # Cost in dollars
    timestamp: str  # Timestamp of usage
    ticker: Optional[str]  # Associated ticker (if applicable)
    success: bool  # Whether operation succeeded
    tokens_used: Optional[int]  # Tokens used (for AI APIs)


class ConfigData(TypedDict, total=False):
    """
    Configuration data structure.

    Represents the YAML configuration structure.
    """
    trading_criteria: Dict  # Trading criteria and thresholds
    api_providers: Dict  # API provider configurations
    budget: Dict  # Budget limits and settings
    analysis: Dict  # Analysis settings
