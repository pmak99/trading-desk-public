"""Pydantic schemas for agent responses.

Validates JSON responses from agents to ensure type safety and consistency.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, validator


class AgentResponse(BaseModel):
    """Base schema for all agent responses."""
    ticker: str
    success: bool
    error: Optional[str] = None


class TickerAnalysisResponse(BaseModel):
    """Response from TickerAnalysisAgent."""
    ticker: str
    earnings_date: Optional[str] = None  # Earnings date (YYYY-MM-DD)
    vrp_ratio: Optional[float] = None
    recommendation: Optional[str] = None
    liquidity_tier: Optional[str] = None
    score: Optional[int] = None
    strategies: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None

    @validator('recommendation')
    def validate_recommendation(cls, v):
        if v is not None and v not in ['EXCELLENT', 'GOOD', 'MARGINAL', 'SKIP']:
            raise ValueError(f'Invalid recommendation: {v}')
        return v

    @validator('liquidity_tier')
    def validate_liquidity_tier(cls, v):
        if v is not None and v not in ['EXCELLENT', 'GOOD', 'WARNING', 'REJECT']:
            raise ValueError(f'Invalid liquidity tier: {v}')
        return v

    @property
    def success(self) -> bool:
        """Analysis succeeded if no error."""
        return self.error is None


class ExplanationResponse(BaseModel):
    """Response from ExplanationAgent."""
    ticker: str
    explanation: str
    key_factors: List[str] = Field(default_factory=list)
    historical_context: str = ""

    @validator('key_factors')
    def validate_key_factors(cls, v):
        if len(v) > 5:
            raise ValueError('Too many key factors (max 5)')
        return v


class AnomalyDetail(BaseModel):
    """Individual anomaly detail."""
    type: str
    severity: str
    message: str

    @validator('severity')
    def validate_severity(cls, v):
        if v not in ['warning', 'critical']:
            raise ValueError(f'Invalid severity: {v}')
        return v

    @validator('type')
    def validate_type(cls, v):
        valid_types = [
            'stale_data', 'missing_data', 'extreme_outlier',
            'conflicting_signals', 'reject_liquidity'
        ]
        if v not in valid_types:
            raise ValueError(f'Invalid anomaly type: {v}')
        return v


class AnomalyDetectionResponse(BaseModel):
    """Response from AnomalyDetectionAgent."""
    ticker: str
    anomalies: List[AnomalyDetail] = Field(default_factory=list)
    recommendation: str

    @validator('recommendation')
    def validate_recommendation(cls, v):
        if v not in ['TRADE', 'DO_NOT_TRADE', 'REDUCE_SIZE']:
            raise ValueError(f'Invalid recommendation: {v}')
        return v

    @property
    def has_critical_anomalies(self) -> bool:
        """Check if any critical anomalies exist."""
        return any(a.severity == 'critical' for a in self.anomalies)

    @property
    def has_warnings(self) -> bool:
        """Check if any warnings exist."""
        return any(a.severity == 'warning' for a in self.anomalies)


class APIHealthStatus(BaseModel):
    """Health status for a single API."""
    status: str
    latency_ms: Optional[int] = None
    remaining_calls: Optional[int] = None
    error: Optional[str] = None

    @validator('status')
    def validate_status(cls, v):
        if v not in ['ok', 'error']:
            raise ValueError(f'Invalid status: {v}')
        return v


class DatabaseHealthStatus(BaseModel):
    """Health status for database."""
    status: str
    size_mb: Optional[float] = None
    historical_moves: Optional[int] = None
    earnings_calendar: Optional[int] = None
    error: Optional[str] = None

    @validator('status')
    def validate_status(cls, v):
        if v not in ['ok', 'error']:
            raise ValueError(f'Invalid status: {v}')
        return v


class BudgetStatus(BaseModel):
    """Budget tracking status."""
    daily_calls: int = 0
    daily_limit: int = 40
    monthly_cost: float = 0.0
    monthly_budget: float = 5.0

    @property
    def daily_remaining(self) -> int:
        """Remaining daily API calls."""
        return max(0, self.daily_limit - self.daily_calls)

    @property
    def monthly_remaining(self) -> float:
        """Remaining monthly budget."""
        return max(0.0, self.monthly_budget - self.monthly_cost)


class SentimentFetchResponse(BaseModel):
    """Response from SentimentFetchAgent."""
    ticker: str
    direction: Optional[str] = None
    score: Optional[float] = None
    catalysts: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    error: Optional[str] = None

    @validator('direction')
    def validate_direction(cls, v):
        if v is not None and v not in ['bullish', 'bearish', 'neutral']:
            raise ValueError(f'Invalid direction: {v}')
        return v

    @validator('score')
    def validate_score(cls, v):
        if v is not None and not (-1.0 <= v <= 1.0):
            raise ValueError(f'Score must be between -1.0 and 1.0: {v}')
        return v

    @validator('catalysts')
    def validate_catalysts(cls, v):
        if len(v) > 5:
            raise ValueError('Too many catalysts (max 5)')
        return v

    @validator('risks')
    def validate_risks(cls, v):
        if len(v) > 3:
            raise ValueError('Too many risks (max 3)')
        return v

    @property
    def success(self) -> bool:
        """Fetch succeeded if no error."""
        return self.error is None


class HealthCheckResponse(BaseModel):
    """Response from HealthCheckAgent."""
    status: str
    apis: Dict[str, APIHealthStatus]
    database: DatabaseHealthStatus
    budget: BudgetStatus

    @validator('status')
    def validate_status(cls, v):
        if v not in ['healthy', 'degraded', 'unhealthy']:
            raise ValueError(f'Invalid status: {v}')
        return v

    @property
    def is_healthy(self) -> bool:
        """Check if system is healthy overall."""
        return self.status == 'healthy'
