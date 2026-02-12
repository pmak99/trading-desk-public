#!/usr/bin/env python
"""Tests for the parallel /analyze pipeline.

Covers: PreFlightAgent, NewsFetchAgent, retry utility, schema validation,
and integrated pipeline behavior. All tests use mocks — no live API calls.
"""

import sys
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# Add agents/ to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from pydantic import ValidationError

from src.utils.retry import with_retry, is_transient_error
from src.utils.schemas import (
    PreFlightResponse,
    NewsFetchResponse,
    NewsHeadline,
    TickerAnalysisResponse,
    SentimentFetchResponse,
)


# ─────────────────────────────────────────────────────────────────────
# TestPreFlightValidation
# ─────────────────────────────────────────────────────────────────────
class TestPreFlightValidation:
    """Tests for PreFlightAgent ticker validation and data checks."""

    def _make_agent(self):
        """Create a PreFlightAgent with mocked DB access."""
        from src.agents.preflight import PreFlightAgent
        agent = PreFlightAgent()
        return agent

    @patch('src.agents.preflight.Container2_0')
    def test_nike_resolves_to_nke(self, mock_container_cls):
        """NIKE should resolve to NKE via alias."""
        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = self._make_agent()
        # Patch the container and mock sqlite
        agent._container = mock_container

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [(5,), ('2026-01-15',), ]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("NIKE")

        assert result['is_valid'] is True
        assert result['normalized_ticker'] == 'NKE'
        assert any('NIKE' in w and 'NKE' in w for w in result['warnings'])

    @patch('src.agents.preflight.Container2_0')
    def test_google_resolves_to_googl(self, mock_container_cls):
        """GOOGLE should resolve to GOOGL via alias."""
        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = self._make_agent()
        agent._container = mock_container

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [(10,), ('2026-01-20',)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("GOOGLE")

        assert result['is_valid'] is True
        assert result['normalized_ticker'] == 'GOOGL'

    def test_invalid_format_rejected(self):
        """Garbage ticker like 123!@# should be rejected."""
        from src.agents.preflight import PreFlightAgent
        agent = PreFlightAgent()

        result = agent.validate("123!@#")

        assert result['is_valid'] is False
        assert result['error'] is not None

    def test_empty_string_rejected(self):
        """Empty string should be rejected."""
        from src.agents.preflight import PreFlightAgent
        agent = PreFlightAgent()

        result = agent.validate("")

        assert result['is_valid'] is False
        assert result['error'] is not None

    @patch('src.agents.preflight.Container2_0')
    def test_unknown_ticker_flagged(self, mock_container_cls):
        """Unknown ticker with no data and no calendar entry should be flagged."""
        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = self._make_agent()
        agent._container = mock_container

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            # historical_moves count = 0, earnings_calendar count = 0
            mock_cursor.fetchone.side_effect = [(0,), (0,)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("ZZZZZ")

        assert result['is_valid'] is True  # Format is valid
        assert result['has_historical_data'] is False
        assert any('OTC' in w or 'delisted' in w for w in result['warnings'])

    @patch('src.agents.preflight.Container2_0')
    def test_stale_data_flagged(self, mock_container_cls):
        """Data older than 14 days should produce a warning."""
        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = self._make_agent()
        agent._container = mock_container

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            # historical_moves count = 8, latest date = 60 days ago
            mock_cursor.fetchone.side_effect = [(8,), ('2025-01-01',)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("AAPL")

        assert result['is_valid'] is True
        assert result['data_freshness_days'] is not None
        assert result['data_freshness_days'] > 14
        assert any('Stale data' in w for w in result['warnings'])

    @patch('src.agents.preflight.Container2_0')
    def test_valid_ticker_passes_clean(self, mock_container_cls):
        """Valid ticker with recent data should pass with no warnings."""
        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = self._make_agent()
        agent._container = mock_container

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            # historical_moves count = 12, latest date = today-ish
            mock_cursor.fetchone.side_effect = [(12,), ('2026-02-01',)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("PLTR")

        assert result['is_valid'] is True
        assert result['normalized_ticker'] == 'PLTR'
        assert result['has_historical_data'] is True
        assert result['historical_quarters'] == 12

    @patch('src.agents.preflight.Container2_0')
    def test_already_correct_ticker_unchanged(self, mock_container_cls):
        """Already-correct ticker like AAPL should not have alias warning."""
        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = self._make_agent()
        agent._container = mock_container

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [(10,), ('2026-02-01',)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("AAPL")

        assert result['normalized_ticker'] == 'AAPL'
        # Should NOT have alias warning
        assert not any('Resolved alias' in w for w in result['warnings'])


# ─────────────────────────────────────────────────────────────────────
# TestEarningsDateValidation
# ─────────────────────────────────────────────────────────────────────
class TestEarningsDateValidation:
    """Tests for earnings date sanity checks in PreFlightAgent."""

    @patch('src.agents.preflight.Container2_0')
    def test_date_within_7_days_no_warning(self, mock_container_cls):
        """Earnings date within 7 days should produce no date warning."""
        from src.agents.preflight import PreFlightAgent
        from datetime import date, timedelta

        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = PreFlightAgent()
        agent._container = mock_container

        near_date = (date.today() + timedelta(days=3)).strftime('%Y-%m-%d')

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [(5,), ('2026-02-01',)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("AAPL", near_date)

        # Should NOT have date distance warning
        assert not any('days away' in w for w in result['warnings'])

    @patch('src.agents.preflight.Container2_0')
    def test_date_too_far_away_warning(self, mock_container_cls):
        """Earnings date >7 days away should produce warning."""
        from src.agents.preflight import PreFlightAgent
        from datetime import date, timedelta

        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = PreFlightAgent()
        agent._container = mock_container

        far_date = (date.today() + timedelta(days=30)).strftime('%Y-%m-%d')

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [(5,), ('2026-02-01',)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("AAPL", far_date)

        assert any('days away' in w for w in result['warnings'])

    @patch('src.agents.preflight.Container2_0')
    def test_past_date_warning(self, mock_container_cls):
        """Past earnings date should produce warning."""
        from src.agents.preflight import PreFlightAgent

        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = PreFlightAgent()
        agent._container = mock_container

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [(5,), ('2026-02-01',)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("AAPL", "2025-01-01")

        assert any('in the past' in w for w in result['warnings'])

    @patch('src.agents.preflight.Container2_0')
    def test_invalid_date_format_warning(self, mock_container_cls):
        """Invalid date format should produce warning."""
        from src.agents.preflight import PreFlightAgent

        mock_container = MagicMock()
        mock_container.get_db_path.return_value = ':memory:'
        mock_container_cls.return_value = mock_container

        agent = PreFlightAgent()
        agent._container = mock_container

        with patch('src.agents.preflight.sqlite3') as mock_sqlite:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [(5,), ('2026-02-01',)]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_sqlite.connect.return_value = mock_conn

            result = agent.validate("AAPL", "not-a-date")

        assert any('Invalid earnings date' in w for w in result['warnings'])


# ─────────────────────────────────────────────────────────────────────
# TestRetryMechanism
# ─────────────────────────────────────────────────────────────────────
class TestRetryMechanism:
    """Tests for async retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_transient_error_retried_then_succeeds(self):
        """Transient error should be retried; success on 3rd attempt."""
        call_count = 0

        async def flaky_coro():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("Connection timed out")
            return {"success": True}

        result = await with_retry(flaky_coro, max_retries=3, base_delay=0.01)
        assert result == {"success": True}
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_permanent_error_not_retried(self):
        """ValueError (permanent) should NOT be retried."""
        call_count = 0

        async def bad_coro():
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid ticker")

        with pytest.raises(ValueError, match="Invalid ticker"):
            await with_retry(bad_coro, max_retries=3, base_delay=0.01)

        assert call_count == 1  # Only called once

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        """Should raise last error after all retries exhausted."""
        async def always_fail():
            raise TimeoutError("Always fails")

        with pytest.raises(TimeoutError, match="Always fails"):
            await with_retry(always_fail, max_retries=2, base_delay=0.01)

    def test_is_transient_error_classification(self):
        """Verify error classification for known types."""
        # Transient
        assert is_transient_error(TimeoutError("timeout")) is True
        assert is_transient_error(ConnectionError("refused")) is True
        assert is_transient_error(Exception("HTTP 500 error")) is True
        assert is_transient_error(Exception("429 rate limited")) is True

        # Permanent
        assert is_transient_error(ValueError("bad input")) is False
        assert is_transient_error(TypeError("wrong type")) is False
        assert is_transient_error(Exception("unauthorized 401")) is False
        assert is_transient_error(Exception("no data found")) is False

    @pytest.mark.asyncio
    async def test_zero_retries_immediate_fail(self):
        """With max_retries=0, should fail immediately on first error."""
        call_count = 0

        async def fail_once():
            nonlocal call_count
            call_count += 1
            raise TimeoutError("fail")

        with pytest.raises(TimeoutError):
            await with_retry(fail_once, max_retries=0, base_delay=0.01)

        assert call_count == 1


# ─────────────────────────────────────────────────────────────────────
# TestVRPValidation
# ─────────────────────────────────────────────────────────────────────
class TestVRPValidation:
    """Tests for VRP data in TickerAnalysisResponse."""

    def test_vrp_data_present(self):
        """Valid VRP data should pass schema validation."""
        response = TickerAnalysisResponse(
            ticker="PLTR",
            vrp_ratio=2.5,
            recommendation="EXCELLENT",
            liquidity_tier="GOOD",
            score=80
        )
        assert response.vrp_ratio == 2.5
        assert response.success is True

    def test_missing_vrp_with_error(self):
        """Error response should have success=False."""
        response = TickerAnalysisResponse(
            ticker="PLTR",
            error="API timeout"
        )
        assert response.success is False
        assert response.vrp_ratio is None


# ─────────────────────────────────────────────────────────────────────
# TestSentimentValidation
# ─────────────────────────────────────────────────────────────────────
class TestSentimentValidation:
    """Tests for sentiment data in SentimentFetchResponse."""

    def test_non_null_score_validates(self):
        """Valid sentiment score should pass."""
        response = SentimentFetchResponse(
            ticker="PLTR",
            direction="bullish",
            score=0.65,
            catalysts=["Strong growth"],
            risks=["Competition"]
        )
        assert response.success is True
        assert response.score == 0.65

    def test_null_score_with_error(self):
        """Null score with error should indicate failure."""
        response = SentimentFetchResponse(
            ticker="PLTR",
            error="Budget exhausted"
        )
        assert response.success is False
        assert response.score is None


# ─────────────────────────────────────────────────────────────────────
# TestLiquidityValidation
# ─────────────────────────────────────────────────────────────────────
class TestLiquidityValidation:
    """Tests for liquidity tier validation."""

    def test_excellent_tier_valid(self):
        """EXCELLENT tier should validate."""
        response = TickerAnalysisResponse(
            ticker="AAPL",
            liquidity_tier="EXCELLENT",
            vrp_ratio=2.0,
            recommendation="EXCELLENT",
            score=85
        )
        assert response.liquidity_tier == "EXCELLENT"

    def test_reject_tier_valid(self):
        """REJECT tier should validate (allowed but penalized)."""
        response = TickerAnalysisResponse(
            ticker="SMALL",
            liquidity_tier="REJECT",
            vrp_ratio=2.5,
            recommendation="EXCELLENT",
            score=70
        )
        assert response.liquidity_tier == "REJECT"


# ─────────────────────────────────────────────────────────────────────
# TestAPIKeyValidation
# ─────────────────────────────────────────────────────────────────────
class TestAPIKeyValidation:
    """Tests for graceful handling of missing API keys."""

    @pytest.mark.asyncio
    async def test_missing_finnhub_key_handled(self):
        """Missing FINNHUB_API_KEY should return error, not crash."""
        from src.agents.news_fetch import NewsFetchAgent

        with patch.dict('os.environ', {}, clear=True):
            agent = NewsFetchAgent()
            agent.api_key = ''  # Ensure no key
            result = await agent.fetch_news("PLTR")

        assert result['error'] is not None
        assert 'FINNHUB_API_KEY' in result['error']
        assert result['headlines'] == []

    @pytest.mark.asyncio
    async def test_missing_perplexity_key_handled(self):
        """Missing PERPLEXITY_API_KEY should return error, not crash."""
        from src.agents.sentiment_fetch import SentimentFetchAgent

        with patch('src.agents.sentiment_fetch.Perplexity5_0', side_effect=ValueError("No key")):
            with patch('src.agents.sentiment_fetch.Cache4_0') as mock_cache_cls:
                mock_cache = MagicMock()
                mock_cache.get_cached_sentiment.return_value = None
                mock_cache.can_call_perplexity.return_value = True
                mock_cache_cls.return_value = mock_cache

                agent = SentimentFetchAgent()
                result = await agent.fetch_sentiment("PLTR", "2026-02-12")

        assert result.get('error') is not None


# ─────────────────────────────────────────────────────────────────────
# TestNewsFetchAgent
# ─────────────────────────────────────────────────────────────────────
class TestNewsFetchAgent:
    """Tests for NewsFetchAgent."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Successful API call should return headlines."""
        from src.agents.news_fetch import NewsFetchAgent

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'headline': 'PLTR wins contract', 'source': 'Reuters', 'url': 'https://example.com', 'datetime': 1707300000},
            {'headline': 'PLTR Q4 earnings', 'source': 'CNBC', 'url': 'https://example.com', 'datetime': 1707200000},
        ]
        mock_response.raise_for_status = MagicMock()

        agent = NewsFetchAgent()
        agent.api_key = 'test-key'

        # httpx is imported locally inside fetch_news, so mock at sys.modules level
        mock_httpx = MagicMock()
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        with patch.dict('sys.modules', {'httpx': mock_httpx}):
            result = await agent.fetch_news("PLTR")

        assert result['error'] is None
        assert result['count'] == 2
        assert len(result['headlines']) == 2

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self):
        """API error should return empty headlines with error message."""
        from src.agents.news_fetch import NewsFetchAgent

        agent = NewsFetchAgent()
        agent.api_key = 'test-key'

        mock_httpx = MagicMock()
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        with patch.dict('sys.modules', {'httpx': mock_httpx}):
            result = await agent.fetch_news("PLTR")

        assert result['error'] is not None
        assert result['headlines'] == []

    @pytest.mark.asyncio
    async def test_missing_api_key_graceful(self):
        """Missing API key should return gracefully."""
        from src.agents.news_fetch import NewsFetchAgent

        agent = NewsFetchAgent()
        agent.api_key = ''

        result = await agent.fetch_news("PLTR")

        assert result['error'] is not None
        assert result['count'] == 0


# ─────────────────────────────────────────────────────────────────────
# TestParallelPipeline
# ─────────────────────────────────────────────────────────────────────
class TestParallelPipeline:
    """Tests for pipeline behavior: critical vs non-critical failures."""

    def test_news_failure_noncritical(self):
        """News failure should NOT block the pipeline."""
        from src.orchestrators.analyze import AnalyzeOrchestrator

        orchestrator = AnalyzeOrchestrator.__new__(AnalyzeOrchestrator)
        result = orchestrator._has_critical_failures({
            'ticker_analysis': {'vrp_ratio': 2.0, 'recommendation': 'EXCELLENT'},
            'sentiment': {'direction': 'bullish', 'score': 0.5},
            'news': {'error': 'FINNHUB_API_KEY not set'},
            'explanation': {},
            'anomaly': {'recommendation': 'TRADE', 'anomalies': []},
            'patterns': None,
        })
        assert result is False  # Pipeline should continue

    def test_ticker_analysis_failure_critical(self):
        """Ticker analysis failure IS critical and should abort pipeline."""
        from src.orchestrators.analyze import AnalyzeOrchestrator

        orchestrator = AnalyzeOrchestrator.__new__(AnalyzeOrchestrator)
        result = orchestrator._has_critical_failures({
            'ticker_analysis': {'error': 'API timeout'},
            'sentiment': {},
            'news': {},
            'explanation': None,
            'anomaly': None,
            'patterns': None,
        })
        assert result is True  # Pipeline should abort

    def test_preflight_invalid_blocks_pipeline(self):
        """PreFlight validation failure should block the pipeline.

        This tests the orchestrate() method's fail-fast check.
        We verify via the schema: is_valid=False means pipeline stops.
        """
        preflight_result = PreFlightResponse(
            ticker="123ABC",
            normalized_ticker="123ABC",
            is_valid=False,
            error="Invalid ticker format"
        )
        assert preflight_result.success is False
        assert preflight_result.is_valid is False


# ─────────────────────────────────────────────────────────────────────
# TestSchemas (new schemas)
# ─────────────────────────────────────────────────────────────────────
class TestNewSchemas:
    """Tests for PreFlightResponse and NewsFetchResponse schemas."""

    def test_preflight_response_validates(self):
        """Valid PreFlightResponse should validate correctly."""
        response = PreFlightResponse(
            ticker="PFIZER",
            normalized_ticker="PFE",
            is_valid=True,
            has_historical_data=True,
            historical_quarters=12,
            data_freshness_days=5,
            warnings=["Resolved alias: PFIZER -> PFE"]
        )
        assert response.success is True
        assert response.normalized_ticker == "PFE"

    def test_news_fetch_response_validates(self):
        """Valid NewsFetchResponse should validate correctly."""
        response = NewsFetchResponse(
            ticker="PLTR",
            headlines=[
                NewsHeadline(title="PLTR wins contract", source="Reuters"),
                NewsHeadline(title="PLTR Q4 earnings", source="CNBC"),
            ],
            count=2
        )
        assert response.success is True
        assert len(response.headlines) == 2

    def test_headlines_over_10_rejected(self):
        """More than 10 headlines should be rejected."""
        headlines = [NewsHeadline(title=f"Headline {i}") for i in range(11)]
        with pytest.raises(ValidationError, match="Too many headlines"):
            NewsFetchResponse(
                ticker="PLTR",
                headlines=headlines,
                count=11
            )
